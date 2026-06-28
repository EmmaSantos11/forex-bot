"""
portfolio_manager.py — Smart risk management for any account size.

Works with accounts from $5 (micro) up to $100k+.
Risk tier and lot caps are derived automatically from balance.
Scales up gradually as the account grows.
"""

import MetaTrader5 as mt5
from core.notifications import send_telegram_message


# ── Risk tiers: keyed by minimum balance ──────────────────────────────────────
# Each tier defines:
#   risk_pct   — % of balance risked per trade
#   max_lots   — hard lot cap per single trade
#   max_trades — max simultaneous open positions
#   label      — human-readable name for Telegram
_TIERS = [
    {"min":      0, "max":     50, "risk_pct": 0.010, "max_lots": 0.01, "max_trades": 1, "label": "Micro"},
    {"min":     50, "max":    200, "risk_pct": 0.010, "max_lots": 0.05, "max_trades": 2, "label": "Mini"},
    {"min":    200, "max":   1000, "risk_pct": 0.010, "max_lots": 0.50, "max_trades": 3, "label": "Small"},
    {"min":   1000, "max":   5000, "risk_pct": 0.005, "max_lots": 2.00, "max_trades": 4, "label": "Standard"},
    {"min":   5000, "max":  20000, "risk_pct": 0.005, "max_lots": 5.00, "max_trades": 5, "label": "Growth"},
    {"min":  20000, "max": 999999, "risk_pct": 0.003, "max_lots": 10.0, "max_trades": 6, "label": "Pro"},
]


def _get_tier(balance: float) -> dict:
    for t in _TIERS:
        if t["min"] <= balance < t["max"]:
            return t
    return _TIERS[-1]


class PortfolioManager:

    def __init__(
        self,
        initial_balance: float = 1000.0,
        risk_per_trade=None,
        max_total_drawdown_pct: float = 0.15,   # 15% total drawdown → hard stop
        max_risk_per_trade_usd=None,              # absolute $ cap per trade
    ):
        self.balance             = initial_balance
        self.daily_start_balance = initial_balance
        self.peak_balance        = initial_balance
        self.max_drawdown        = 0.0
        self.open_trades         = []
        self.trades_since_retrain = 0
        self.trading_halted       = False        # set True on total DD breach

        self._risk_override         = risk_per_trade
        self.max_total_drawdown_pct = max_total_drawdown_pct
        # Default absolute cap: 5% of starting balance (adjusts conservatively)
        self.max_risk_usd = max_risk_per_trade_usd or (initial_balance * 0.05)

        tier = _get_tier(initial_balance)
        print(
            f"  💼 Account tier: {tier['label']} (${initial_balance:,.2f})\n"
            f"     Risk/trade: {tier['risk_pct']:.1%} | "
            f"Max lots: {tier['max_lots']} | "
            f"Max open trades: {tier['max_trades']}"
        )

    # ── Derived properties auto-update with balance ───────────────────────────

    @property
    def _tier(self) -> dict:
        return _get_tier(self.balance)

    @property
    def risk_per_trade(self) -> float:
        return self._risk_override if self._risk_override else self._tier["risk_pct"]

    @property
    def max_lot_size(self) -> float:
        return self._tier["max_lots"]

    @property
    def max_open_trades(self) -> int:
        return self._tier["max_trades"]

    @property
    def max_portfolio_risk(self) -> float:
        return self.risk_per_trade * self.max_open_trades

    # ── Balance tracking ──────────────────────────────────────────────────────

    def update_balance(self, current_balance: float):
        prev_tier = _get_tier(self.balance)
        self.balance = current_balance

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        if self.peak_balance > 0:
            dd = (self.peak_balance - self.balance) / self.peak_balance
            self.max_drawdown = max(self.max_drawdown, dd)

        # Notify if account crossed into a new tier
        new_tier = _get_tier(self.balance)
        if new_tier["label"] != prev_tier["label"]:
            send_telegram_message(
                f"🎯 <b>Account Tier Upgraded!</b>\n\n"
                f"You've grown into the <b>{new_tier['label']}</b> tier.\n"
                f"Balance:    ${self.balance:,.2f}\n"
                f"Risk/trade: {new_tier['risk_pct']:.1%}\n"
                f"Max lots:   {new_tier['max_lots']}\n"
                f"Max trades: {new_tier['max_trades']}\n\n"
                f"Bot has automatically adjusted its risk parameters."
            )

    def new_day_reset(self, current_balance: float):
        self.daily_start_balance = current_balance
        print(f"🌅 Daily reset. Balance: ${current_balance:,.2f} | Tier: {self._tier['label']}")
        send_telegram_message(
            f"🌅 <b>New Trading Day</b>\n"
            f"Balance: ${current_balance:,.2f}\n"
            f"Tier: {self._tier['label']} | Risk: {self.risk_per_trade:.1%}/trade"
        )

    # ── Circuit breakers ──────────────────────────────────────────────────────

    def check_circuit_breaker(self, current_balance: float, max_daily_loss_pct: float = 0.05) -> bool:
        """Daily loss limit — resets at midnight."""
        if self.trading_halted:
            return True   # total DD already triggered — stay halted

        loss  = self.daily_start_balance - current_balance
        limit = self.daily_start_balance * max_daily_loss_pct
        if loss > limit:
            send_telegram_message(
                f"🚨 <b>Daily Circuit Breaker Hit!</b>\n"
                f"Daily loss: ${loss:.2f} (limit ${limit:.2f})\n"
                f"Trading paused until tomorrow to protect capital."
            )
            return True
        return False

    def check_total_drawdown(self, current_balance: float) -> bool:
        """
        Hard stop when total drawdown from the all-time peak exceeds the limit.
        Unlike the daily breaker this does NOT reset — requires manual restart.
        """
        if self.peak_balance == 0:
            return False
        total_dd = (self.peak_balance - current_balance) / self.peak_balance
        if total_dd >= self.max_total_drawdown_pct and not self.trading_halted:
            self.trading_halted = True
            send_telegram_message(
                f"🛑 <b>TOTAL DRAWDOWN LIMIT HIT — BOT HALTED</b>\n\n"
                f"Peak balance:    ${self.peak_balance:,.2f}\n"
                f"Current balance: ${current_balance:,.2f}\n"
                f"Drawdown:        {total_dd:.1%} (limit {self.max_total_drawdown_pct:.0%})\n\n"
                f"All trading has been stopped to protect your capital.\n"
                f"Restart the bot manually once you've reviewed the account.\n\n"
                f"⚠️ Open positions are still live in MT5 — review them now."
            )
        return self.trading_halted

    # ── Trade gate ────────────────────────────────────────────────────────────

    def can_open_trade(self) -> bool:
        return len(self.open_trades) < self.max_open_trades

    # ── Position sizing ───────────────────────────────────────────────────────

    def calculate_position_size(self, symbol: str, stop_loss_pips: float) -> float:
        """
        Returns lot size so that a full SL hit costs exactly risk_per_trade × balance.
        Works on any account from $5 upward.
        """
        info = mt5.symbol_info(symbol)  # type: ignore[attr-defined]
        if info is None or stop_loss_pips == 0:
            return info.volume_min if info else 0.01

        # Risk in dollars — capped by both % of balance AND the absolute dollar limit
        risk_dollars = min(
            self.balance * self.risk_per_trade,
            self.max_risk_usd,
        )
        tick_value = info.trade_tick_value   # USD value of 1 point move for 1 lot

        raw_lots = risk_dollars / (stop_loss_pips * tick_value)

        # Round to broker's lot step
        step     = info.volume_step
        lot_size = round(raw_lots / step) * step

        # Clamp: never exceed tier cap, broker max, or go below broker min
        final = min(lot_size, self.max_lot_size, info.volume_max)
        final = max(info.volume_min, final)

        # Fix floating-point drift (e.g. 0.010000000000000002 → 0.01)
        decimals = len(str(float(step)).split(".")[1]) if "." in str(float(step)) else 0
        return round(final, decimals)

    # ── Trade registry ────────────────────────────────────────────────────────

    def open_trade(self, trade: dict):
        self.open_trades.append(trade)

    def close_trade_by_ticket(self, ticket):
        self.trades_since_retrain += 1
        self.open_trades = [
            t for t in self.open_trades
            if (t.get("ticket") if isinstance(t, dict) else t) != ticket
        ]

    # ── Account summary (for Telegram /status) ───────────────────────────────

    def summary(self) -> str:
        tier = self._tier
        return (
            f"Balance:    ${self.balance:,.2f}\n"
            f"Peak:       ${self.peak_balance:,.2f}\n"
            f"Max DD:     {self.max_drawdown:.2%}\n"
            f"Tier:       {tier['label']}\n"
            f"Risk/trade: {self.risk_per_trade:.1%}\n"
            f"Max lots:   {self.max_lot_size}\n"
            f"Open trades:{len(self.open_trades)}/{self.max_open_trades}"
        )
