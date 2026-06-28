"""
main.py — Single entry point for the Forex AI Trading Bot.

Run with:   python main.py

What this does automatically:
  • Connects to MT5 and detects which pairs are available on your account
  • Starts the Telegram control bot in a background thread
  • Trades live during market hours (Mon–Fri)
  • Every Sunday before market open: imports your manual demo trades
    then retrains the AI — no manual step needed
  • Users can add/remove pairs live via Telegram (/addpair, /removepair)
"""

import os
import sys
import time
import threading
import warnings
import traceback
from datetime import datetime, date, timezone, timedelta

import json
import requests
import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

from mt5_connector import connect_mt5, shutdown_mt5, is_terminal_connected
from strategy import add_indicators, add_features, generate_signals_ict
from ml_model import (
    load_model, load_rf_model, load_pair_models, save_model,
    generate_ai_signals, generate_ensemble_signals,
    prepare_ml_data, train_model, train_ensemble, train_pair_ensemble,
    RF_MODEL_PATH,
)
from portfolio_manager import PortfolioManager
from live_trader import place_trade
from core.notifications import send_telegram_message, send_telegram_to, get_telegram_updates
from core.multi_timeframe import resample_timeframes
from core.execution_logger import log_execution_note
from core.trade_logger import log_trade
from cloud_sync import upload_to_hub, download_from_hub

load_dotenv()
warnings.filterwarnings("ignore")

# ── Credentials ──────────────────────────────────────────────────────────────
LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

# ── Preferred pairs — base names only (broker suffix handled automatically)
PREFERRED_PAIRS = [
    "EURUSD", "USDCHF",
    "AUDUSD", "USDCAD", "NZDUSD", "EURJPY",
    "XAUUSD",   # Gold
    "BTCUSD",   # Bitcoin
    # GBPUSD removed — backtest PF 0.84 (net loser), 37% max drawdown
    # USDJPY removed — backtest PF 1.0 (breakeven), 28% max drawdown; re-add after per-pair ML trains
]

# ── File paths ────────────────────────────────────────────────────────────────
USER_PAIRS_FILE    = "data/user_pairs.txt"
MANUAL_TRADES_FILE = "data/manual_trade_features.csv"
USERS_FILE         = "data/users.json"

# ── Model feature columns (must match ml_model.py) ───────────────────────────
FEATURE_COLS = ["trend_strength", "momentum", "volatility", "RSI", "MACD_hist", "BB_width", "BB_position"]

# ── Runtime settings ─────────────────────────────────────────────────────────
# Per-instrument spread limits (% of price).  Each is calibrated independently
# so tightening BTC's threshold doesn't loosen the forex guard.
#
#   Forex majors: 0.03% ≈ 3 pips on EURUSD  (same protection as the old flat filter)
#   Gold:         0.04% ≈ $0.94 on $2350 gold  (normal spread $0.30–$0.60)
#   BTC:          0.20% ≈ $130 on $65k BTC      (normal spread $20–$50 on Exness)
_SPREAD_LIMITS = {
    "BTCUSD": 0.0020,   # crypto — inherently wider spread
    "XAUUSD": 0.0004,   # gold
}
_SPREAD_DEFAULT = 0.0003   # forex — ~3 pips on most majors

POLL_SECONDS = 30    # how often the main loop checks for new signals
MAGIC        = 123456

# ── Confidence gate ───────────────────────────────────────────────────────────
# Bot will NOT enter any trade below this confidence level.
# A+ setups only: raised thresholds cut low-conviction trades significantly.
MIN_CONFIDENCE        = 0.80   # 80% minimum at all times
EVENING_MIN_CONFIDENCE = 0.85  # 85% required during low-momentum evening hours

# ── Evening caution window (UTC) ──────────────────────────────────────────────
# Post-London-close through NY close: momentum fades, spreads widen, fake-outs rise.
# Bot still runs but demands higher conviction before entering.
EVENING_CAUTION_START = 18   # 18:00 UTC — after London close
EVENING_CAUTION_END   = 22   # 22:00 UTC — NY close / dead zone begins

# ── Trading Mode ─────────────────────────────────────────────────────────────
# Auto-detected from MT5 account type at startup.
# Override: set PROP_FIRM_MODE=true in .env to force prop firm rules on any account.
#
#  DEMO     — Exness demo / paper trading  (MT5 trade_mode = 0)
#  PERSONAL — Real account, your own money (MT5 trade_mode = 2, no override)
#  PROP     — Prop firm challenge          (PROP_FIRM_MODE=true OR auto-detect)

_PROP_FIRM_OVERRIDE = os.getenv("PROP_FIRM_MODE", "false").lower() == "true"

# Per-mode configs — all three kept close to prop firm discipline,
# personal just has more room to grow.
MODE_CONFIGS = {
    "DEMO": {
        "emoji":               "🧪",
        "label":               "Demo",
        "max_daily_loss_pct":  0.05,    # 5%  daily
        "max_total_dd_pct":    0.12,    # 12% total (reduced from 15%)
        "risk_per_trade_pct":  None,    # use tier default
        "profit_milestones":   [0.10, 0.15, 0.25, 0.50, 1.00],  # celebrate, keep going
        "profit_hard_stop":    False,   # never stop — just grow
        "close_before_weekend":False,
        "monday_gap_guard":    True,    # always on for all modes
        "news_buffer_mins":    30,
    },
    "PERSONAL": {
        "emoji":               "💼",
        "label":               "Personal/Real",
        "max_daily_loss_pct":  0.05,    # 5%  daily
        "max_total_dd_pct":    0.10,    # 10% total — tighter, closer to prop
        "risk_per_trade_pct":  None,    # use tier (scales with balance automatically)
        "profit_milestones":   [0.10, 0.15, 0.25, 0.50, 1.00],
        "profit_hard_stop":    False,
        "close_before_weekend":False,
        "monday_gap_guard":    True,
        "news_buffer_mins":    30,
    },
    "PROP": {
        "emoji":               "🏆",
        "label":               "Prop Firm",
        "max_daily_loss_pct":  0.04,    # 4% — buffer before firm's 5%
        "max_total_dd_pct":    0.07,    # 7% — buffer before firm's 8%
        "risk_per_trade_pct":  0.005,   # 0.5% fixed — no tier variance
        "profit_milestones":   [0.08],  # one milestone = hard stop at 8%
        "profit_hard_stop":    True,    # stop trading once target hit
        "close_before_weekend":True,
        "monday_gap_guard":    True,
        "news_buffer_mins":    60,
    },
}


def detect_trading_mode() -> str:
    """
    Auto-detects DEMO / PERSONAL / PROP from MT5 account info.
    PROP_FIRM_MODE=true in .env always overrides to PROP.
    """
    if _PROP_FIRM_OVERRIDE:
        return "PROP"
    acct = mt5.account_info()
    if acct is None:
        return "DEMO"
    # MT5 trade_mode: 0=demo, 1=contest, 2=real
    if acct.trade_mode == 2:
        return "PERSONAL"
    return "DEMO"


# Resolved at startup — set once in main()
TRADING_MODE: str = "DEMO"    # overwritten in main()
MODE_CFG:     dict = MODE_CONFIGS["DEMO"]  # overwritten in main()

# ── Global daily trade cap ────────────────────────────────────────────────────
# A+ only: hard ceiling across ALL pairs. Resets at midnight UTC.
GLOBAL_DAILY_TRADE_CAP  = 6      # max 6 trades across all pairs per day
MAX_DAILY_LOSSES        = 3      # stop trading after 3 SL hits in one day
GLOBAL_TRADE_COOLDOWN_SECS = 7200  # 2-hour minimum gap between any two trades
_global_trade_count: int  = 0    # incremented on each filled trade, reset each day
_daily_losses:       int  = 0    # SL hits today — resets at midnight
_last_trade_time:    float = 0.0  # unix timestamp of last filled trade

# ── Shared state (main thread + Telegram thread both access this) ─────────────
active_pairs: list = []   # populated on startup by detect_pairs()
pair_models: dict  = {}   # {symbol: (xgb_model, rf_model)} — per-pair models
_pair_trade_count: dict = {}   # {symbol: count} — resets every day, max 2 per pair

# Pending signal confirmation — signal must appear on 2 consecutive cycles before firing.
# Prevents one-bar false breakouts from triggering trades.
_pending_signals: dict = {}   # {symbol: direction}  (+1 or -1)

# After a stop-loss, the pair is locked out for 3 hours before re-entry.
# {symbol: unix_timestamp_of_sl_close}
_pair_loss_cooldown: dict = {}
LOSS_COOLDOWN_SECS = 3 * 3600   # 3 hours

# Tracks every open position so we can detect exits and notify users.
# {ticket: {symbol, direction, entry, sl, tp, lots}}
_position_cache: dict = {}

# Tracks news events already notified — prevents spamming the same event every cycle.
# Keys: "{title}|{date}|upcoming" or "{title}|{date}|released"
_news_notified: set = set()

# Tracks pairs currently in a news blackout so we only print the skip once,
# not every 30-second cycle. Cleared when the pair exits the window.
_news_skip_active: set = set()

# ── Bot mood ──────────────────────────────────────────────────────────────────
_MOOD_FILE = os.path.join("logs", "bot_mood.json")

def _set_mood(state: str, message: str = "", detail: str = "",
              pair: str = "", confidence: float = 0.0, pnl: float | None = None):
    """Write the bot's current mood to logs/bot_mood.json for the dashboard."""
    os.makedirs("logs", exist_ok=True)
    data: dict = {
        "state":      state,
        "message":    message,
        "detail":     detail,
        "pair":       pair,
        "confidence": round(confidence, 4),
        "updated":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    if pnl is not None:
        data["pnl"] = round(pnl, 2)
    try:
        with open(_MOOD_FILE, "w") as _f:
            json.dump(data, _f)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# PAIR MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _load_user_pairs() -> list:
    """Reads extra pairs the user added via Telegram."""
    if not os.path.exists(USER_PAIRS_FILE):
        return []
    with open(USER_PAIRS_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def _save_user_pairs(pairs: list):
    os.makedirs("data", exist_ok=True)
    with open(USER_PAIRS_FILE, "w") as f:
        f.write("\n".join(pairs))


def detect_pairs() -> list:
    """
    Finds the broker-specific symbol name for each preferred pair.
    Brokers add suffixes like 'm', '.m', 'c', 'pro', etc.
    This matches by base name so it works on any broker automatically.
    """
    wanted = list(dict.fromkeys(PREFERRED_PAIRS + _load_user_pairs()))

    # Build a lookup: base-name → actual broker symbol name
    all_symbols = mt5.symbols_get()
    if all_symbols is None:
        return []

    # Strip common broker suffixes to get base name for matching
    def base(name):
        for suffix in [".m", "m", ".c", "c", ".pro", "pro", ".ecn", "ecn"]:
            if name.lower().endswith(suffix):
                return name[: len(name) - len(suffix)].upper()
        return name.upper()

    broker_map = {}   # base_name → actual symbol name on broker
    for s in all_symbols:
        b = base(s.name)
        if b not in broker_map:
            broker_map[b] = s.name

    found = []
    for pair in wanted:
        actual = broker_map.get(pair.upper())
        if actual:
            info = mt5.symbol_info(actual)
            if info and not info.visible:
                mt5.symbol_select(actual, True)
            found.append(actual)
            print(f"  ✅ {pair} → {actual}")
        else:
            print(f"  ⏭️  {pair} — not found on this broker (skipped)")

    return found


# ══════════════════════════════════════════════════════════════════════════════
# TRADING SESSIONS
# All times UTC.  Overlaps are computed on the fly from the base windows.
# ══════════════════════════════════════════════════════════════════════════════

SESSIONS = {
    "Sydney": {
        "emoji": "🇦🇺",
        "start": 22, "end": 7,   # wraps midnight
        "pairs": ["AUDUSD", "NZDUSD", "USDJPY", "BTCUSD"],
        "note": "Low volatility — AUD/NZD/JPY + BTC active",
    },
    "Tokyo": {
        "emoji": "🇯🇵",
        "start": 0, "end": 9,
        "pairs": ["USDJPY", "EURJPY", "AUDUSD", "NZDUSD", "BTCUSD"],
        "note": "Asian session — JPY and Pacific pairs most active",
    },
    "London": {
        "emoji": "🇬🇧",
        "start": 7, "end": 16,
        "pairs": ["EURUSD", "GBPUSD", "USDCHF", "EURJPY", "USDJPY", "XAUUSD", "BTCUSD"],
        "note": "Highest EUR/GBP volume — Gold and BTC also very active",
    },
    "New York": {
        "emoji": "🗽",
        "start": 13, "end": 22,
        "pairs": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "XAUUSD", "BTCUSD"],
        "note": "USD dominance — Gold and BTC see biggest moves at NY open",
    },
}

# Dead zone: post-NY close, pre-Sydney ramp-up — low liquidity, wide spreads
DEAD_ZONE_START = 21   # UTC hour
DEAD_ZONE_END   = 23   # UTC hour  (22:00 Sydney opens but is still thin)


def get_active_sessions() -> list:
    """Returns list of session names currently active (may be 2 during overlaps)."""
    hour   = datetime.now(timezone.utc).hour
    active = []
    for name, cfg in SESSIONS.items():
        s, e = cfg["start"], cfg["end"]
        if s < e:                          # normal window (doesn't wrap midnight)
            if s <= hour < e:
                active.append(name)
        else:                              # wraps midnight (Sydney 22 → 07)
            if hour >= s or hour < e:
                active.append(name)
    return active if active else ["Off-Hours"]


def is_dead_zone() -> bool:
    """True during the post-NY / pre-Sydney gap where liquidity dries up."""
    hour = datetime.now(timezone.utc).hour
    return DEAD_ZONE_START <= hour < DEAD_ZONE_END


def is_evening_caution() -> bool:
    """
    True during the low-momentum evening window (18:00–22:00 UTC).
    London has closed, NY volume is fading — bot still runs but requires
    higher confidence (80%) before entering any trade.
    """
    hour = datetime.now(timezone.utc).hour
    return EVENING_CAUTION_START <= hour < EVENING_CAUTION_END


def session_suits_pair(symbol: str, sessions: list) -> bool:
    """
    Returns True if the base pair appears in at least one active session's list.
    During Off-Hours we still allow any pair so the bot isn't completely frozen.
    """
    if "Off-Hours" in sessions:
        return True   # let other filters (spread, news) decide
    base = _strip_suffix(symbol)
    for name in sessions:
        cfg = SESSIONS.get(name, {})
        if base in cfg.get("pairs", []):
            return True
    return False


def _sessions_text() -> str:
    """One-line summary of which sessions are active right now + overlap flag."""
    sessions = get_active_sessions()
    if "Off-Hours" in sessions:
        return "💤 Off-Hours (low liquidity)"
    parts = [f"{SESSIONS[s]['emoji']} {s}" for s in sessions if s in SESSIONS]
    label = " / ".join(parts)
    if len(sessions) >= 2:
        label += " 🔥 OVERLAP"
    return label


def _write_signal_scan(state: dict):
    """Dumps per-pair signal scanner state to logs/signal_scan.json for the dashboard.
    Keys are normalised to base pair names (no broker suffix) so the dashboard can
    look them up using plain names like EURUSD regardless of broker naming.
    """
    os.makedirs("logs", exist_ok=True)
    try:
        clean = {_strip_suffix(sym): data for sym, data in state.items()}
        with open("logs/signal_scan.json", "w") as _f:
            json.dump({
                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "session": _sessions_text(),
                "pairs":   clean,
            }, _f)
    except Exception:
        pass


def _write_news_events():
    """Writes upcoming HIGH-impact events to logs/news_events.json for the dashboard countdown."""
    os.makedirs("logs", exist_ok=True)
    events = _fetch_news_calendar()
    now    = datetime.now(timezone.utc)
    upcoming = []
    for ev in events:
        if ev.get("impact", "").lower() != "high":
            continue
        try:
            dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        mins = (dt - now).total_seconds() / 60
        if -120 <= mins <= 480:
            upcoming.append({
                "title":    ev.get("title", ""),
                "country":  ev.get("country", ""),
                "time_utc": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "mins_away": round(mins, 1),
                "forecast": ev.get("forecast", ""),
                "previous": ev.get("previous", ""),
                "actual":   ev.get("actual", ""),
            })
    upcoming.sort(key=lambda x: x["mins_away"])
    try:
        with open("logs/news_events.json", "w") as _f:
            json.dump({"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "events": upcoming}, _f)
    except Exception:
        pass


def _check_all_time_high(current_balance: float, all_users: set):
    """Sends a Telegram ATH alert and updates logs/ath.json when a new peak is hit."""
    import random as _rand
    global _all_time_high
    ath_file = "logs/ath.json"
    if _all_time_high == 0.0 and os.path.exists(ath_file):
        try:
            with open(ath_file) as _f:
                _all_time_high = json.load(_f).get("ath", 0.0)
        except Exception:
            pass

    if current_balance > _all_time_high + 0.01:
        prev           = _all_time_high
        _all_time_high = current_balance
        os.makedirs("logs", exist_ok=True)
        try:
            with open(ath_file, "w") as _f:
                json.dump({"ath": _all_time_high,
                           "at": datetime.now(timezone.utc).isoformat()}, _f)
        except Exception:
            pass
        if prev > 0:   # skip the first time (startup initialisation)
            gain = current_balance - prev
            msg  = _rand.choice([
                f"🏆 <b>New All-Time High!</b>\n\n💰 Balance: <b>${current_balance:,.2f}</b>\nUp +${gain:.2f} from last peak\nThe grind is paying off 🐻🚀",
                f"🚀 <b>ATH Alert!</b>\n\n<b>${current_balance:,.2f}</b> — new high water mark!\nPrevious peak: ${prev:,.2f}\nBot is on a mission 🤖💪",
                f"🎉 <b>New Peak Unlocked!</b>\n\n${current_balance:,.2f} · up +${gain:.2f}\nLet's keep stacking 📈🐻",
            ])
            for uid in all_users:
                if uid:
                    send_telegram_to(uid, msg)


# ══════════════════════════════════════════════════════════════════════════════
# MARKET HOURS
# ══════════════════════════════════════════════════════════════════════════════

def market_is_open() -> bool:
    """Returns True during standard forex trading hours (Mon–Fri UTC)."""
    now     = datetime.now(timezone.utc)
    weekday = now.weekday()   # Mon=0 … Sun=6
    hour    = now.hour
    if weekday == 4 and hour >= 21:   # Friday close ~21:00 UTC
        return False
    if weekday == 5:                  # All Saturday
        return False
    if weekday == 6 and hour < 21:    # Sunday before open ~21:00 UTC
        return False
    return True


# ── Prop firm helpers ─────────────────────────────────────────────────────────

_milestones_hit: set = set()   # e.g. {0.10, 0.15} — prevents repeat alerts
_all_time_high:  float = 0.0   # highest balance ever seen — loaded from logs/ath.json
_scan_state:     dict  = {}    # per-pair signal state written to logs/signal_scan.json


def is_friday_close_time() -> bool:
    """True from Friday 20:30 UTC — only active when mode has close_before_weekend."""
    if not MODE_CFG.get("close_before_weekend"):
        return False
    now = datetime.now(timezone.utc)
    return now.weekday() == 4 and (now.hour > 20 or (now.hour == 20 and now.minute >= 30))


def is_monday_gap_time() -> bool:
    """True Sun 21:00–22:00 UTC (= market open hour) — universal gap guard."""
    if not MODE_CFG.get("monday_gap_guard"):
        return False
    now = datetime.now(timezone.utc)
    return now.weekday() == 6 and now.hour == 21


def active_news_buffer_mins() -> int:
    """News blackout window in minutes — wider for prop firm mode."""
    return MODE_CFG.get("news_buffer_mins", 30)


def check_profit_milestones(current_balance: float, start_balance: float, all_users: set):
    """
    Checks if the account has crossed a profit milestone.
    Demo/Personal: celebrate and keep trading (lot sizes grow with tier).
    Prop firm: celebrate + hard stop at the target.
    Returns True if trading should stop (prop firm target hit).
    """
    if start_balance == 0:
        return False

    growth = (current_balance - start_balance) / start_balance

    for milestone in MODE_CFG.get("profit_milestones", []):
        if growth >= milestone and milestone not in _milestones_hit:
            _milestones_hit.add(milestone)
            is_hard_stop = MODE_CFG["profit_hard_stop"] and milestone == MODE_CFG["profit_milestones"][-1]

            if is_hard_stop:
                msg = (
                    f"🎯 {MODE_CFG['emoji']} <b>Prop Firm Target Reached!</b>\n\n"
                    f"Started:  ${start_balance:,.2f}\n"
                    f"Now:      ${current_balance:,.2f}\n"
                    f"Growth:   +{growth:.1%}\n\n"
                    f"✅ Target of {milestone:.0%} hit — bot has stopped new trades.\n"
                    f"Submit your account for evaluation!"
                )
            else:
                msg = (
                    f"🎉 {MODE_CFG['emoji']} <b>{milestone:.0%} Profit Milestone!</b>\n\n"
                    f"Started:  ${start_balance:,.2f}\n"
                    f"Now:      ${current_balance:,.2f}\n"
                    f"Growth:   +{growth:.1%}\n\n"
                    f"Bot keeps trading — lot sizes scale up as the account grows. 🚀"
                )
            for uid in all_users:
                if uid:
                    send_telegram_to(uid, msg)
            print(f"🎉 {milestone:.0%} milestone reached (growth {growth:.1%})")

            if is_hard_stop:
                return True   # caller should stop opening new trades

    return False


def today_is_sunday() -> bool:
    return datetime.now(timezone.utc).weekday() == 6


def next_market_open() -> datetime | None:
    """Next Sunday 21:00 UTC open. Returns None if market is already open."""
    if market_is_open():
        return None
    now = datetime.now(timezone.utc)
    days_to_sunday = (6 - now.weekday()) % 7
    candidate = (now + timedelta(days=days_to_sunday)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def next_market_close() -> datetime | None:
    """Next Friday 21:00 UTC close. Returns None if market is already closed."""
    if not market_is_open():
        return None
    now = datetime.now(timezone.utc)
    days_to_friday = (4 - now.weekday()) % 7
    candidate = (now + timedelta(days=days_to_friday)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _fmt_td(td: timedelta) -> str:
    """Formats a timedelta into '2d 4h 30m' style."""
    total = int(td.total_seconds())
    if total <= 0:
        return "any moment now"
    h, rem = divmod(total, 3600)
    m = rem // 60
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m"


# ── Daily pair schedule — which pairs suit each day of the week and why ───────
# Keys: 0=Mon … 4=Fri (weekday() integers)
DAILY_PAIR_SCHEDULE = {
    0: {
        "label":  "Monday",
        "pairs":  ["EURUSD", "USDCHF", "EURJPY"],
        "reason": (
            "London opens fresh and institutions set their weekly bias. "
            "EUR leads early direction and CHF confirms safe-haven flow. "
            "EUR/JPY captures momentum from the Asian-to-London handoff."
        ),
    },
    1: {
        "label":  "Tuesday",
        "pairs":  ["EURUSD", "USDCHF", "AUDUSD", "XAUUSD"],
        "reason": (
            "Highest liquidity day of the week. "
            "London/NY overlap is at full strength across EUR, CHF, and AUD. "
            "Gold (XAUUSD) sees strong directional moves on Tuesday economic data."
        ),
    },
    2: {
        "label":  "Wednesday",
        "pairs":  ["EURUSD", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "XAUUSD", "BTCUSD"],
        "reason": (
            "Peak volume mid-week — best day for trend-following on all instruments. "
            "Gold reacts to mid-week USD/inflation data. "
            "BTC often sees its largest weekly moves Wednesday into Thursday."
        ),
    },
    3: {
        "label":  "Thursday",
        "pairs":  ["EURJPY", "AUDUSD", "NZDUSD", "USDCAD", "BTCUSD"],
        "reason": (
            "Asian session drives JPY carry-trade flows into EUR/JPY. "
            "Pacific currencies (AUD, NZD) stay active on Asian commodity demand. "
            "BTC continues mid-week momentum — often the strongest crypto day."
        ),
    },
    4: {
        "label":  "Friday",
        "pairs":  ["EURUSD", "USDCHF", "XAUUSD"],
        "reason": (
            "London morning session only — tight, controlled exposure. "
            "Gold included as institutions rebalance positions into the weekend. "
            "BTC, AUD, NZD, CAD avoided Friday: spreads widen as liquidity drains."
        ),
    },
}


def _today_schedule_text() -> str:
    """Builds the /today Telegram reply showing today's pairs and reasoning."""
    now     = datetime.now(timezone.utc)
    weekday = now.weekday()

    if weekday in (5, 6):  # weekend
        open_dt  = next_market_open()
        open_str = open_dt.strftime("%A %d %b at %H:%M UTC") if open_dt else "Sunday 21:00 UTC"
        eta      = _fmt_td(open_dt - now) if open_dt else "—"
        return (
            f"📅 <b>Market closed — weekend</b>\n\n"
            f"Next open: <b>{open_str}</b> (in {eta})\n\n"
            f"Bot is standing by and will resume trading automatically at open.\n"
            f"Monday's focus: {', '.join(DAILY_PAIR_SCHEDULE[0]['pairs'])}"
        )

    sched = DAILY_PAIR_SCHEDULE.get(weekday)
    if not sched:
        return "No schedule found for today."

    # Map base names to actual broker symbols from active_pairs
    base_to_broker = {_strip_suffix(p): p for p in active_pairs}
    matched = [base_to_broker[b] for b in sched["pairs"] if b in base_to_broker]
    pair_str = "\n".join(f"• {p}" for p in matched) if matched else "• (no matching pairs active)"

    close_dt  = next_market_close()
    close_str = close_dt.strftime("%A %d %b at %H:%M UTC") if close_dt else "Friday 21:00 UTC"
    eta_close = _fmt_td(close_dt - now) if close_dt else "—"

    return (
        f"📅 <b>{sched['label']} — Today's Pairs</b>\n\n"
        f"{pair_str}\n\n"
        f"<b>Why today?</b>\n{sched['reason']}\n\n"
        f"🕐 Market closes: <b>{close_str}</b> (in {eta_close})"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DATA HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_bars(symbol: str, timeframe: int, count: int = 500):
    """Pulls OHLCV from the active MT5 session (no init/shutdown)."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = df[["open", "high", "low", "close", "tick_volume"]]
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    return df


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def get_signal(symbol: str, model, rf_model=None) -> tuple:
    """
    Returns (signal, confidence, ict_score).  Priority:
      1. Ensemble (XGBoost + RF both loaded and agree)  — highest quality
      2. XGBoost alone (RF not yet trained)
      3. Rule-based ICT (no model at all)

    ict_score is always included so the confidence gate can allow 75%+
    when the ICT confluence is strong (≥6 factors confirmed).
    """
    df = _fetch_bars(symbol, mt5.TIMEFRAME_M5, 500)
    if df is None:
        return 0, 0.0, 0

    tfs = resample_timeframes(df)
    for tf in tfs:
        tfs[tf] = add_indicators(tfs[tf])
        tfs[tf] = add_features(tfs[tf])

    if model and rf_model:
        result_df  = generate_ensemble_signals(tfs["5min"], model, rf_model)
        signal     = int(result_df["Signal"].iloc[-1])
        confidence = float(result_df["AI_Confidence"].iloc[-1])
    elif model:
        result_df  = generate_ai_signals(tfs["5min"], model)
        signal     = int(result_df["Signal"].iloc[-1])
        confidence = float(result_df["AI_Confidence"].iloc[-1])
    else:
        result_df  = generate_signals_ict(tfs)
        signal     = int(result_df["Signal"].iloc[-1])
        raw_score  = float(result_df["ICT_Score"].iloc[-1])
        confidence = min(raw_score / 20.0, 1.0)   # max 20 factors (0–19)
        return signal, confidence, int(raw_score)

    # AI mode: run ICT pipeline to get the raw confluence score.
    # Used by the 75% confidence tier — AI says maybe, ICT confirms why.
    ict_score = 0
    if signal != 0:
        try:
            ict_df    = generate_signals_ict(tfs)
            ict_score = int(ict_df["ICT_Score"].iloc[-1])
        except Exception:
            pass

    return signal, confidence, ict_score


# ══════════════════════════════════════════════════════════════════════════════
# PHASE A — NEWS FILTER
# Pauses trading 30 min before and after any HIGH-impact ForexFactory event
# for the currencies inside the symbol being evaluated.
# ══════════════════════════════════════════════════════════════════════════════

_news_cache: list = []
_news_cache_time: datetime | None = None
_NEWS_CACHE_TTL = 3600  # seconds — refresh calendar once per hour


def _fetch_news_calendar() -> list:
    global _news_cache, _news_cache_time
    now = datetime.now(timezone.utc)
    if _news_cache_time and (now - _news_cache_time).total_seconds() < _NEWS_CACHE_TTL:
        return _news_cache
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10,
            headers={"User-Agent": "ForexAIBot/1.0"},
        )
        if resp.status_code == 200:
            _news_cache = resp.json()
            _news_cache_time = now
    except Exception as e:
        print(f"⚠️  News calendar fetch failed: {e}")
    return _news_cache


def _strip_suffix(symbol: str) -> str:
    s = symbol.upper()
    for sfx in [".M", "M", ".C", "C", ".PRO", "PRO", ".ECN", "ECN", ".R", "R"]:
        if s.endswith(sfx) and len(s) > len(sfx):
            return s[: -len(sfx)]
    return s


def is_news_time(symbol: str) -> bool:
    """True if a HIGH-impact news event is within 30 min for either currency in symbol."""
    base = _strip_suffix(symbol)
    if len(base) < 6:
        return False
    currencies = {base[:3], base[3:6]}
    now = datetime.now(timezone.utc)

    for event in _fetch_news_calendar():
        if event.get("impact", "").lower() != "high":
            continue
        if event.get("country", "").upper() not in currencies:
            continue
        try:
            raw = event.get("date", "")
            event_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        buffer_secs = active_news_buffer_mins() * 60
        if abs((now - event_dt).total_seconds()) <= buffer_secs:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# NEWS INTELLIGENCE — direction inference + user notifications
# ══════════════════════════════════════════════════════════════════════════════

def _parse_news_value(s: str):
    """Parse news value strings like '180K', '2.3%', '-0.1M' into a float."""
    if not s or not s.strip():
        return None
    s = s.strip().upper().replace(",", "").replace(" ", "")
    mult = 1.0
    if   s.endswith("K"): mult = 1e3; s = s[:-1]
    elif s.endswith("M"): mult = 1e6; s = s[:-1]
    elif s.endswith("B"): mult = 1e9; s = s[:-1]
    s = s.rstrip("%")
    try:
        return float(s) * mult
    except (ValueError, TypeError):
        return None


def _infer_news_direction(event: dict) -> int:
    """
    Returns +1 (bullish for that currency), -1 (bearish), or 0 (unknown).
    Pre-event:  uses forecast vs previous to guess expected direction.
    Post-event: uses actual vs forecast — more reliable.
    Lower-is-better events (unemployment, claims, CPI) get sign flipped.
    """
    title = event.get("title", "").lower()
    lower_is_better = any(kw in title for kw in [
        "unemployment", "claims", "jobless", "deficit", "cpi", "ppi", "inflation",
    ])

    actual   = _parse_news_value(event.get("actual",   ""))
    forecast = _parse_news_value(event.get("forecast", ""))
    previous = _parse_news_value(event.get("previous", ""))

    if actual is not None:
        ref = forecast if forecast is not None else previous
        compare_val, compare_to = actual, ref
    elif forecast is not None and previous is not None:
        compare_val, compare_to = forecast, previous
    else:
        return 0

    if compare_val is None or compare_to is None:
        return 0

    raw = 1 if compare_val > compare_to else (-1 if compare_val < compare_to else 0)
    return -raw if lower_is_better else raw


def _news_pair_signal(symbol: str, currency: str, direction: int) -> int:
    """
    Maps a currency's news direction onto a directional signal for a symbol.
    +1 = buy the pair,  -1 = sell the pair,  0 = not affected.
    """
    base = _strip_suffix(symbol)
    if len(base) < 6:
        return 0
    if currency == base[:3].upper():
        return direction        # bullish base → pair goes UP
    if currency == base[3:6].upper():
        return -direction       # bullish quote → pair goes DOWN
    return 0


def _check_and_notify_news():
    """
    Called once per main loop cycle.
    Sends one-time alerts (per event) to all users:

    Upcoming (≤ 30 min before):
      - Event name, currency, time, forecast vs previous, expected direction,
        which active pairs are affected and in which direction.

    Released (actual value just appeared):
      - Same info + confirmed direction + list of setups to watch post-buffer.
    """
    global _news_notified

    now = datetime.now(timezone.utc)

    # Build the set of currencies traded right now
    currencies: set = set()
    for pair in active_pairs:
        base = _strip_suffix(pair)
        if len(base) >= 6:
            currencies.update([base[:3].upper(), base[3:6].upper()])

    all_users = _load_registered_users() | {os.getenv("TELEGRAM_CHAT_ID", "")}

    for event in _fetch_news_calendar():
        if event.get("impact", "").lower() != "high":
            continue
        country = event.get("country", "").upper()
        if country not in currencies:
            continue

        try:
            event_dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        mins_away  = (event_dt - now).total_seconds() / 60
        title      = event.get("title", "Unknown Event")
        event_id   = f"{title}|{event.get('date', '')}"
        actual_raw = event.get("actual", "").strip()
        direction  = _infer_news_direction(event)

        dir_label = (
            "🟢 BULLISH" if direction == 1 else
            "🔴 BEARISH" if direction == -1 else
            "⚪ NEUTRAL / unclear"
        )

        # Build the "affected pairs" line
        def _pair_lines(dir_override=None):
            d = dir_override if dir_override is not None else direction
            lines = []
            for pair in active_pairs:
                sig = _news_pair_signal(pair, country, d)
                if sig != 0:
                    lines.append(f"  {'🟢 BUY' if sig == 1 else '🔴 SELL'} {pair}")
            return lines

        # ── Post-release notification ──────────────────────────────────────
        released_key = event_id + "|released"
        if actual_raw and released_key not in _news_notified and -120 <= mins_away <= 0:
            _news_notified.add(released_key)
            lines = _pair_lines()
            pairs_block = (
                "<b>Setups to watch (~15 min after release):</b>\n" + "\n".join(lines)
                if lines else "No active pairs directly impacted."
            )
            msg = (
                f"📰 <b>News Released</b>\n\n"
                f"<b>{title}</b>\n"
                f"Currency: {country}\n"
                f"Actual:   <b>{actual_raw}</b>\n"
                f"Forecast: {event.get('forecast', 'N/A')}  |  "
                f"Previous: {event.get('previous', 'N/A')}\n"
                f"Verdict:  {dir_label} for {country}\n\n"
                f"{pairs_block}\n\n"
                f"Bot resumes full analysis after the {active_news_buffer_mins()} min buffer. "
                f"If the setup aligns with the above direction the bot will enter automatically."
            )
            for uid in all_users:
                if uid:
                    send_telegram_to(uid, msg)

        # ── Upcoming notification (≤ 30 min, fires once) ──────────────────
        upcoming_key = event_id + "|upcoming"
        if 0 < mins_away <= 30 and upcoming_key not in _news_notified:
            _news_notified.add(upcoming_key)
            lines = _pair_lines()
            pairs_block = (
                "<b>Pairs to watch after release:</b>\n" + "\n".join(lines)
                if lines else "No active pairs directly affected."
            )
            forecast_line = (
                f"Forecast: {event.get('forecast', 'N/A')}  |  "
                f"Previous: {event.get('previous', 'N/A')}"
            )
            msg = (
                f"⚠️ <b>HIGH-Impact News in {int(mins_away)} min</b>\n\n"
                f"<b>{title}</b>\n"
                f"Currency: {country}\n"
                f"Time:     {event_dt.strftime('%H:%M UTC')}\n"
                f"{forecast_line}\n"
                f"→ {dir_label} for {country}\n\n"
                f"{pairs_block}\n\n"
                f"Bot is pausing affected pairs for {active_news_buffer_mins()} min around the event.\n"
                f"After release: if actual confirms the direction above, the bot will look for an entry."
            )
            for uid in all_users:
                if uid:
                    send_telegram_to(uid, msg)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE A — CORRELATION FILTER
# Prevents opening a new trade if a strongly correlated pair is already open.
# ══════════════════════════════════════════════════════════════════════════════

CORRELATION_GROUPS = [
    {"EURUSD", "GBPUSD"},           # both positively correlated vs USD
    {"USDJPY", "USDCHF", "USDCAD"}, # USD-base pairs
    {"AUDUSD", "NZDUSD"},           # commodity / Pacific currencies
]


def has_correlated_open(symbol: str) -> bool:
    """True if a pair in the same correlation group already has an open position."""
    base = _strip_suffix(symbol)
    positions = mt5.positions_get() or []
    open_bases = {_strip_suffix(p.symbol) for p in positions}

    for group in CORRELATION_GROUPS:
        if base in group:
            if (group - {base}) & open_bases:
                return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# PHASE A — TRAILING STOPS + PARTIAL PROFIT TAKING
# At 1R profit: close 50% of the position, move SL to breakeven.
# ══════════════════════════════════════════════════════════════════════════════

# Tracks the last trailing SL step applied per ticket.
# Steps: 0=none, 1=−0.5R, 2=BE, 3=+0.5R, 4=+1R, 5=+1.5R
_trailing_sl_step: dict = {}

# Tickets where a reversal-signal partial has already been taken this session.
# Prevents double-partialing the same position.
_reversal_partialed: set = set()


def _modify_sl(ticket: int, symbol: str, new_sl: float) -> bool:
    pos_list = mt5.positions_get(ticket=ticket)
    if not pos_list:
        return False
    pos = pos_list[0]
    info = mt5.symbol_info(symbol)
    digits = info.digits if info else 5
    req = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol":   symbol,
        "sl":       round(new_sl, digits),
        "tp":       pos.tp,
    }
    r = mt5.order_send(req)
    return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)


def manage_open_positions():
    """
    Gradual trailing SL — advances every R-multiple the trade moves in profit.
    Full position runs to TP; no partial closes.

    Step  Trigger    New SL
      1   +1R profit  entry − 0.5R  (cuts risk in half)
      2   +2R profit  entry          (breakeven)
      3   +3R profit  entry + 0.5R  (locks half initial risk)
      4   +4R profit  entry + 1.0R  (locks full initial risk)
      5   +5R profit  entry + 1.5R  (let it run — SL beyond full risk)
    """
    _STEP_LABELS = {1: "-0.5R", 2: "BE", 3: "+0.5R", 4: "+1R", 5: "+1.5R"}
    _STEP_OFFSETS = {1: -0.5, 2: 0.0, 3: 0.5, 4: 1.0, 5: 1.5}

    positions = mt5.positions_get() or []
    for pos in positions:
        ticket = pos.ticket
        symbol = pos.symbol

        if pos.sl == 0:
            continue
        sl_distance = abs(pos.price_open - pos.sl)
        if sl_distance == 0:
            continue

        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        entry  = pos.price_open

        profit_distance = (pos.price_current - entry) if is_buy else (entry - pos.price_current)
        profit_r = profit_distance / sl_distance

        # Which step should we be at?
        if profit_r >= 5.0:
            target_step = 5
        elif profit_r >= 4.0:
            target_step = 4
        elif profit_r >= 3.0:
            target_step = 3
        elif profit_r >= 2.0:
            target_step = 2
        elif profit_r >= 1.0:
            target_step = 1
        else:
            continue  # not at +1R yet — leave SL alone

        current_step = _trailing_sl_step.get(ticket, 0)
        if target_step <= current_step:
            continue  # already at this level or beyond

        offset = _STEP_OFFSETS[target_step]
        new_sl = (entry + offset * sl_distance) if is_buy else (entry - offset * sl_distance)

        # Never move SL backward
        if is_buy and new_sl <= pos.sl:
            continue
        if not is_buy and pos.sl != 0 and new_sl >= pos.sl:
            continue

        if _modify_sl(ticket, symbol, new_sl):
            _trailing_sl_step[ticket] = target_step
            label = _STEP_LABELS[target_step]
            print(f"📈 {symbol}: SL → {label} (profit +{profit_r:.1f}R, ticket {ticket})")
            send_telegram_message(
                f"📈 <b>Trailing SL</b> — {symbol}\n"
                f"Profit: +{profit_r:.1f}R | SL moved to <b>{label}</b>"
            )


def _check_reversal_partials():
    """
    Smart reversal partial — runs AFTER the signal scan updates _scan_state.

    Logic: for each open position, if the scan shows an A+ signal (≥85% conf)
    in the OPPOSITE direction AND the position is floating ≥ $20 profit,
    close 50% immediately to lock gains before the reversal takes the trade out.

    This handles the "$50-$80 open profit → $0.00 closed" scenario by harvesting
    half the position the moment the bot detects the market flipping against it.
    """
    positions = mt5.positions_get() or []
    if not positions:
        return

    for pos in positions:
        ticket = pos.ticket
        if ticket in _reversal_partialed:
            continue   # already took a reversal partial on this ticket

        symbol     = pos.symbol
        scan       = _scan_state.get(symbol, {})
        scan_sig   = scan.get("signal", 0)
        scan_conf  = scan.get("confidence", 0.0)
        pos_dir    = 1 if pos.type == mt5.ORDER_TYPE_BUY else -1

        # Need an opposite direction signal at ≥85% confidence
        if scan_sig == 0 or scan_sig == pos_dir or scan_conf < 0.85:
            continue

        # Only protect a position that is already in meaningful profit
        if pos.profit < 20.0:
            continue

        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if not info or not tick:
            continue

        partial_lot = round(pos.volume * 0.50, 2)
        if partial_lot < info.volume_min:
            partial_lot = info.volume_min
        if partial_lot > pos.volume:
            continue

        close_type  = mt5.ORDER_TYPE_SELL if pos_dir == 1 else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos_dir == 1 else tick.ask
        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "position":     ticket,
            "symbol":       symbol,
            "volume":       partial_lot,
            "type":         close_type,
            "price":        close_price,
            "deviation":    20,
            "magic":        MAGIC,
            "comment":      "RevPartial",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        r = mt5.order_send(req)
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            _reversal_partialed.add(ticket)
            remaining  = round(pos.volume - partial_lot, 2)
            opp_str    = "SELL" if pos_dir == 1 else "BUY"
            locked_pnl = pos.profit * 0.50
            print(
                f"⚡ {symbol}: Reversal partial — {opp_str} signal at {scan_conf:.0%} "
                f"| ~${locked_pnl:.2f} locked | {remaining} lots remain"
            )
            send_telegram_message(
                f"⚡ <b>Smart Partial Close</b> — {symbol}\n"
                f"Reason: opposite <b>{opp_str}</b> signal at <b>{scan_conf:.0%} confidence</b>\n"
                f"50% closed | Profit locked: ~<b>${locked_pnl:.2f}</b>\n"
                f"Remaining: {remaining} lots — trailing SL continues."
            )


# ══════════════════════════════════════════════════════════════════════════════
# EXIT NOTIFICATION — detects closed positions and broadcasts to all users
# ══════════════════════════════════════════════════════════════════════════════

def _check_closed_positions():
    global _daily_losses
    live_tickets = {p.ticket for p in (mt5.positions_get() or [])}
    closed = [t for t in list(_position_cache) if t not in live_tickets]

    if not closed:
        return

    now   = datetime.now(timezone.utc)
    since = now - timedelta(hours=2)   # look back 2 h to find the closing deal

    deals = mt5.history_deals_get(since, now) or []
    deal_by_pos = {}
    for d in deals:
        if d.entry == mt5.DEAL_ENTRY_OUT and d.symbol:
            deal_by_pos[d.position_id] = d

    all_users = _load_registered_users() | {os.getenv("TELEGRAM_CHAT_ID", "")}

    for ticket in closed:
        # Clean up per-ticket state
        _trailing_sl_step.pop(ticket, None)
        _reversal_partialed.discard(ticket)

        cached = _position_cache.pop(ticket, {})
        symbol    = cached.get("symbol", "?")
        direction = cached.get("direction", "?")
        lots      = cached.get("lots", 0)
        sl_price  = cached.get("sl", 0)
        tp_price  = cached.get("tp", 0)
        entry     = cached.get("entry", 0)

        deal = deal_by_pos.get(ticket)
        if deal:
            profit     = deal.profit
            exit_price = deal.price

            # Determine close reason by comparing exit price to cached SL/TP
            tol = abs(tp_price - entry) * 0.05 if tp_price and entry else 0.0001
            if tp_price and abs(exit_price - tp_price) <= tol:
                reason = "✅ Take Profit"
                emoji  = "🏆"
                _set_mood("won",
                          f"WIN! +${profit:.2f} on {_strip_suffix(symbol)}",
                          "That felt right. Stay disciplined — onto the next one! 🏆",
                          pair=_strip_suffix(symbol), pnl=profit)
            elif sl_price and abs(exit_price - sl_price) <= tol:
                reason = "❌ Stop Loss"
                emoji  = "🛑"
                _pair_loss_cooldown[symbol] = time.time()   # 3-hour re-entry lockout
                _daily_losses += 1   # track toward MAX_DAILY_LOSSES stop
                if _daily_losses >= MAX_DAILY_LOSSES:
                    _set_mood("loss_guard",
                              f"{_daily_losses} SL hits today — protecting the account.",
                              "Taking a break for the rest of the day. Capital first. 🛡️",
                              pair=_strip_suffix(symbol), pnl=profit)
                else:
                    _set_mood("lost",
                              f"Tough loss. ${profit:.2f} on {_strip_suffix(symbol)}",
                              "Shaking it off... onto the next A+ setup. Stay disciplined. 😤",
                              pair=_strip_suffix(symbol), pnl=profit)
            elif abs(exit_price - entry) <= tol:
                reason = "⚖️ Breakeven"
                emoji  = "⚖️"
                _set_mood("ready", "Breakeven — SL trailed to entry. Moving on.", pair=_strip_suffix(symbol))
            else:
                reason = "🔒 Closed" + (" (profit)" if profit > 0 else " (loss)")
                emoji  = "💰" if profit > 0 else "📉"
                if profit > 0:
                    _set_mood("won", f"+${profit:.2f} on {_strip_suffix(symbol)}", "Closed in profit. Next setup!", pair=_strip_suffix(symbol), pnl=profit)
                else:
                    _set_mood("lost", f"${profit:.2f} on {_strip_suffix(symbol)}", "Onward — next setup awaits.", pair=_strip_suffix(symbol), pnl=profit)

            sign = "+" if profit >= 0 else ""
            msg = (
                f"{emoji} <b>Trade Closed — {reason}</b>\n\n"
                f"Pair:      <b>{symbol}</b>\n"
                f"Direction: {'🟢 BUY' if direction == 'BUY' else '🔴 SELL'}\n"
                f"Lots:      {lots}\n"
                f"Entry:     {entry:.5f}\n"
                f"Exit:      {exit_price:.5f}\n"
                f"P&L:       <b>{sign}{profit:.2f}</b>\n"
                f"Reason:    {reason}"
            )
        else:
            msg = (
                f"🔒 <b>Trade Closed</b>\n\n"
                f"Pair: <b>{symbol}</b> {direction}\n"
                f"Lots: {lots}\n"
                f"(Could not fetch exit details from MT5 history)"
            )

        print(f"🔔 {symbol} position {ticket} closed — notifying users.")
        for uid in all_users:
            if uid:
                send_telegram_to(uid, msg)


# ══════════════════════════════════════════════════════════════════════════════
# MONDAY WEEKLY PERFORMANCE REPORT
# Sent once per Monday to all registered users.
# ══════════════════════════════════════════════════════════════════════════════

_last_report_date = None


def send_weekly_report(pm):
    global _last_report_date

    today = date.today()
    if _last_report_date == today:
        return
    if datetime.now(timezone.utc).weekday() != 0:   # 0 = Monday
        return

    _last_report_date = today

    # Pull last 7 days of closed deals from MT5
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    deals = mt5.history_deals_get(since, now) or []
    exits = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT and d.symbol]

    if not exits:
        send_telegram_message(
            "📊 <b>Weekly Report</b>\n\nNo closed trades in the past 7 days."
        )
        return

    total_pnl  = sum(d.profit for d in exits)
    wins       = [d for d in exits if d.profit > 0]
    losses     = [d for d in exits if d.profit <= 0]
    win_rate   = len(wins) / len(exits) * 100 if exits else 0
    avg_win    = sum(d.profit for d in wins)    / len(wins)    if wins    else 0
    avg_loss   = sum(d.profit for d in losses)  / len(losses)  if losses  else 0
    best_trade = max(exits, key=lambda d: d.profit)
    worst_trade= min(exits, key=lambda d: d.profit)

    # Per-pair breakdown
    pair_pnl: dict = {}
    for d in exits:
        pair_pnl[d.symbol] = pair_pnl.get(d.symbol, 0) + d.profit
    pair_lines = "\n".join(
        f"  {'✅' if v >= 0 else '❌'} {k}: {'+' if v >= 0 else ''}{v:.2f}"
        for k, v in sorted(pair_pnl.items(), key=lambda x: x[1], reverse=True)
    )

    sign = "+" if total_pnl >= 0 else ""
    acct = mt5.account_info()
    bal_line = f"Balance: ${acct.balance:,.2f}" if acct else ""

    msg = (
        f"📊 <b>Weekly Performance Report</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Period:    Last 7 days\n"
        f"Trades:    {len(exits)} ({len(wins)}W / {len(losses)}L)\n"
        f"Win rate:  {win_rate:.1f}%\n"
        f"Net P&L:   <b>{sign}{total_pnl:.2f}</b>\n"
        f"Avg win:   +{avg_win:.2f}   Avg loss: {avg_loss:.2f}\n"
        f"Best:      +{best_trade.profit:.2f} ({best_trade.symbol})\n"
        f"Worst:     {worst_trade.profit:.2f} ({worst_trade.symbol})\n"
        f"Max DD:    {pm.max_drawdown:.2%}\n"
        f"{bal_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>By pair:</b>\n{pair_lines}"
    )

    all_users = _load_registered_users() | {os.getenv("TELEGRAM_CHAT_ID", "")}
    for uid in all_users:
        if uid:
            send_telegram_to(uid, msg)

    print("📊 Weekly report sent to all users.")


# ══════════════════════════════════════════════════════════════════════════════
# SUNDAY AUTO-RETRAIN
# Uses the existing MT5 session — does NOT call mt5.shutdown()
# ══════════════════════════════════════════════════════════════════════════════

_last_retrain_date = None


def _import_manual_trades_inline():
    """
    Pulls MT5 deal history (using the active session) and saves feature
    rows + outcome labels to MANUAL_TRADES_FILE.
    """
    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    now   = datetime.now(timezone.utc)

    deals = mt5.history_deals_get(since, now)
    if deals is None or len(deals) == 0:
        print("  ℹ️  No deal history found.")
        return

    df_d   = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    exits  = df_d[df_d["entry"] == mt5.DEAL_ENTRY_OUT].copy()

    rows = []
    for _, ex in exits.iterrows():
        symbol = ex["symbol"]
        profit = ex["profit"]
        if not symbol or profit == 0:
            continue

        entries = df_d[
            (df_d["position_id"] == ex["position_id"]) &
            (df_d["entry"] == mt5.DEAL_ENTRY_IN)
        ]
        if entries.empty:
            continue

        entry_time = datetime.fromtimestamp(entries.iloc[0]["time"], tz=timezone.utc)
        label      = 1 if profit > 0 else -1

        bars = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M5, entry_time, 100)
        if bars is None or len(bars) == 0:
            continue

        bar_df = pd.DataFrame(bars)
        bar_df["time"] = pd.to_datetime(bar_df["time"], unit="s")
        bar_df.set_index("time", inplace=True)
        bar_df = bar_df[["open", "high", "low", "close", "tick_volume"]]
        bar_df.columns = ["Open", "High", "Low", "Close", "Volume"]
        bar_df = add_indicators(bar_df)
        bar_df = add_features(bar_df)

        latest = bar_df.iloc[-1]
        rows.append({
            "trend_strength": latest.get("trend_strength"),
            "momentum":       latest.get("momentum"),
            "volatility":     latest.get("volatility"),
            "RSI":            latest.get("RSI"),
            "MACD_hist":      latest.get("MACD_hist"),
            "BB_width":       latest.get("BB_width"),
            "BB_position":    latest.get("BB_position"),
            "Target":         label,
            "symbol":         symbol,
            "entry_time":     entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "source":         "manual",
        })

    if not rows:
        print("  ℹ️  No usable manual trades found.")
        return

    df_new = pd.DataFrame(rows)
    if os.path.exists(MANUAL_TRADES_FILE):
        df_old = pd.read_csv(MANUAL_TRADES_FILE)
        df_all = pd.concat([df_old, df_new]).drop_duplicates(
            subset=["entry_time", "symbol"]
        )
    else:
        os.makedirs("data", exist_ok=True)
        df_all = df_new

    df_all.to_csv(MANUAL_TRADES_FILE, index=False)
    wins   = (df_all["Target"] == 1).sum()
    losses = (df_all["Target"] == -1).sum()
    print(f"  📂 Manual trades: {len(df_all)} total | wins: {wins} | losses: {losses}")


def run_auto_retrain():
    """
    Full Sunday retrain pipeline — runs entirely within the active MT5 session:
      1. Import latest manual demo trades
      2. Fetch 5000 H1 bars of primary pair (broker-resolved EURUSD) for training history
      3. Generate signal labels from multi-TF strategy
      4. Blend in manual trade data at 3× weight
      5. Train global + per-pair XGBoost and Random Forest models
    Runs at most once per Sunday.
    """
    global _last_retrain_date

    today = date.today()
    if _last_retrain_date == today:
        return   # already ran today

    print("\n📅 Sunday auto-retrain starting...")
    _set_mood("retrain_prep", "Sunday retrain starting!", "Importing live trades before training the AI...")
    send_telegram_message(
        "📅 <b>Sunday Auto-Retrain Starting</b>\n"
        "Importing your manual trades + retraining AI...\n"
        "This takes a few minutes."
    )

    # Step 1 — import manual trades
    try:
        _import_manual_trades_inline()
    except Exception as e:
        print(f"  ⚠️  Manual trade import error: {e}")

    # Step 2 — fetch training history (3 attempts, then auto-stop)
    # Resolve broker-specific EURUSD symbol (e.g. EURUSDm on Exness) from active_pairs.
    # Fallback: scan MT5 symbols directly so retrain works even before detect_pairs() runs.
    eurusd_sym = next((p for p in active_pairs if _strip_suffix(p) == "EURUSD"), None)
    if eurusd_sym is None:
        all_syms = mt5.symbols_get()
        if all_syms:
            for s in all_syms:
                if _strip_suffix(s.name) == "EURUSD":
                    eurusd_sym = s.name
                    break
    if eurusd_sym is None:
        eurusd_sym = active_pairs[0] if active_pairs else "EURUSD"
    print(f"  📡 Training data source: {eurusd_sym}")

    rates = None
    for attempt in range(1, 4):
        rates = mt5.copy_rates_from_pos(eurusd_sym, mt5.TIMEFRAME_H1, 0, 5000)
        if rates is not None and len(rates) > 0:
            break
        print(f"  ⚠️  Data fetch attempt {attempt}/3 failed — {mt5.last_error()}")
        if attempt < 3:
            time.sleep(60)

    if rates is None or len(rates) == 0:
        _last_retrain_date = today  # prevent infinite retry loop today
        open_dt  = next_market_open()
        open_str = open_dt.strftime("%A %d %b at %H:%M UTC") if open_dt else "soon"
        send_telegram_message(
            "⚠️ <b>Sunday Retrain Skipped</b>\n"
            "Could not fetch training data after 3 attempts.\n"
            "The bot is still running with the existing model.\n\n"
            f"📅 Market opens: <b>{open_str}</b>\n"
            "Use /status or /pairs anytime — the bot is standing by."
        )
        print("⚠️ No training data after 3 attempts. Continuing with existing model.")
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = df[["open", "high", "low", "close", "tick_volume"]]
    df.columns = ["Open", "High", "Low", "Close", "Volume"]

    _set_mood("retraining", "Crunching through months of market data...", "Training XGBoost + Random Forest for all pairs 🧮")
    # Step 3 — build labeled dataset from multi-TF strategy
    tfs = resample_timeframes(df)
    for tf in tfs:
        tfs[tf] = add_indicators(tfs[tf])
        tfs[tf] = add_features(tfs[tf])

    df_labeled   = generate_signals_ict(tfs)
    X_hist, y_hist = prepare_ml_data(df_labeled)

    if X_hist is None:
        send_telegram_message("⚠️  Retrain skipped — not enough signal data in history.")
        return

    # Step 4 — blend manual trades (3× weight = human judgment matters more)
    if os.path.exists(MANUAL_TRADES_FILE):
        manual = pd.read_csv(MANUAL_TRADES_FILE)
        manual = manual[FEATURE_COLS + ["Target"]].dropna()
        manual = manual[manual["Target"].isin([1, -1])]
        if not manual.empty:
            X_m    = pd.concat([manual[FEATURE_COLS]] * 3, ignore_index=True)
            y_m    = pd.concat([manual["Target"]]    * 3, ignore_index=True)
            X_all  = pd.concat([X_hist, X_m], ignore_index=True)
            y_all  = pd.concat([y_hist, y_m], ignore_index=True)
            print(f"  ✅ Blended {len(X_hist)} historical + {len(X_m)} manual samples")
        else:
            X_all, y_all = X_hist, y_hist
    else:
        X_all, y_all = X_hist, y_hist
        print("  ℹ️  No manual trades file yet — training on historical data only.")

    # Step 5 — train global ensemble (fallback for all pairs)
    print("  🤖 Training global ensemble (XGBoost + Random Forest)...")
    xgb, rf = train_ensemble(X_all, y_all)

    # Step 6 — train per-pair models (each pair on its own history)
    print("\n  🎯 Training per-pair models...")
    pair_results  = []
    recall_records = []   # collected for history CSV
    trained_at    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for pair in active_pairs:
        base = _strip_suffix(pair)
        try:
            pair_rates = None
            for attempt in range(1, 3):
                pair_rates = mt5.copy_rates_from_pos(pair, mt5.TIMEFRAME_H1, 0, 3000)  # type: ignore[attr-defined]
                if pair_rates is not None and len(pair_rates) > 0:
                    break
                time.sleep(10)

            if pair_rates is None or len(pair_rates) < 200:
                print(f"    ⏭️  {base}: not enough history — using global model")
                pair_results.append(f"  ⏭️ {base}: skipped (no data)")
                continue

            df_p = pd.DataFrame(pair_rates)
            df_p["time"] = pd.to_datetime(df_p["time"], unit="s")
            df_p.set_index("time", inplace=True)
            df_p = df_p[["open", "high", "low", "close", "tick_volume"]]
            df_p.columns = ["Open", "High", "Low", "Close", "Volume"]

            tfs_p = resample_timeframes(df_p)
            for tf in tfs_p:
                tfs_p[tf] = add_indicators(tfs_p[tf])
                tfs_p[tf] = add_features(tfs_p[tf])

            df_sig_p = generate_signals_ict(tfs_p)
            X_p, y_p = prepare_ml_data(df_sig_p)

            if X_p is None or len(X_p) < 30:
                print(f"    ⏭️  {base}: too few signals — using global model")
                pair_results.append(f"  ⏭️ {base}: skipped (insufficient signals)")
                continue

            xgb_p, rf_p, xgb_r, rf_r = train_pair_ensemble(base, X_p, y_p)

            # Hot-swap into the live pair_models dict immediately
            if xgb_p or rf_p:
                pair_models[pair] = (xgb_p, rf_p)
                pair_results.append(
                    f"  ✅ {base}: trained ({len(X_p)} samples) "
                    f"| XGB: {xgb_r:.0%} | RF: {rf_r:.0%}"
                )
                recall_records.append({
                    "pair":       base,
                    "xgb_recall": round(xgb_r, 4),
                    "rf_recall":  round(rf_r,  4),
                    "samples":    len(X_p),
                    "trained_at": trained_at,
                })
            else:
                pair_results.append(f"  ⚠️ {base}: training failed")

        except Exception as e:
            print(f"    ⚠️  {base} training error: {e}")
            pair_results.append(f"  ⚠️ {base}: error — {str(e)[:60]}")

    _last_retrain_date = today
    _set_mood("week_prep", "Models updated! Gearing up for the week ahead.", f"All {len(active_pairs)} pairs retrained — ready to trade 🚀")

    # ── Save recall scores to CSV so the AI Training dashboard can track progress ──
    if recall_records:
        os.makedirs("logs", exist_ok=True)
        df_rec = pd.DataFrame(recall_records)

        # Overwrite latest snapshot
        df_rec.to_csv("logs/model_recall.csv", index=False)

        # Append to cumulative history for the learning-trend chart
        hist_path   = "logs/model_recall_history.csv"
        hist_exists = os.path.exists(hist_path)
        retrain_n   = 1
        if hist_exists:
            try:
                df_h      = pd.read_csv(hist_path)
                retrain_n = int(df_h["retrain_n"].max()) + 1 if not df_h.empty else 1
            except Exception:
                retrain_n = 1
        df_rec["retrain_n"] = retrain_n
        df_rec.to_csv(hist_path, mode="a", header=not hist_exists, index=False)
        print(f"  📊 Retrain #{retrain_n} logged → logs/model_recall_history.csv")

    pair_summary = "\n".join(pair_results) if pair_results else "  (none)"
    status = "✅" if (xgb and rf) else "⚠️"
    send_telegram_message(
        f"{status} <b>Weekly Retrain Complete</b>\n\n"
        f"Global ensemble: {'✅ XGBoost + RF' if xgb and rf else '⚠️ partial'}\n"
        f"Training samples: {len(X_all)}\n\n"
        f"<b>Per-pair models:</b>\n{pair_summary}\n\n"
        f"Bot is ready for the market open. 🚀"
    )
    print("  ✅ Retrain complete.")

    # ── Push freshly trained models to Hugging Face cloud storage ─────────────
    print("\n☁️  Uploading models to cloud...")
    try:
        upload_to_hub()
        send_telegram_message(
            "☁️ <b>Models saved to cloud</b>\n"
            "All trained models backed up to Hugging Face.\n"
            "Safe to restore on any machine with /download."
        )
    except Exception as _e:
        print(f"  ⚠️  Cloud upload failed: {_e}")


# ══════════════════════════════════════════════════════════════════════════════
# USER REGISTRY  — any Telegram user who /start's the bot gets registered
# Admin (TELEGRAM_CHAT_ID) always has full access.
# Registered users get: /start  /status  /pairs
# Admin only:           /addpair  /removepair  /close
# ══════════════════════════════════════════════════════════════════════════════

def _load_registered_users() -> set:
    if not os.path.exists(USERS_FILE):
        return set()
    try:
        with open(USERS_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_registered_users(users: set):
    os.makedirs("data", exist_ok=True)
    with open(USERS_FILE, "w") as f:
        json.dump(list(users), f)


def _register_user(chat_id: str) -> bool:
    """Adds chat_id to registry. Returns True if it was a new user."""
    users = _load_registered_users()
    if chat_id in users:
        return False
    users.add(chat_id)
    _save_registered_users(users)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM LISTENER  (runs in a background daemon thread)
# ══════════════════════════════════════════════════════════════════════════════

def _telegram_loop(pm: PortfolioManager):
    admin_id    = os.getenv("TELEGRAM_CHAT_ID", "")
    last_update = 0

    print("📱 Telegram listener active.")

    while True:
        try:
            updates = get_telegram_updates(offset=last_update + 1)
            for upd in updates:
                last_update = upd.get("update_id", last_update)
                msg     = upd.get("message", {})
                text    = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if not chat_id or not text:
                    continue

                is_admin = (chat_id == admin_id)
                cmd      = text.lower()

                # Auto-register anyone who contacts the bot
                is_new = _register_user(chat_id)

                # ── /start ────────────────────────────────────────────────
                if cmd in ["/start", "start", "hey", "hello"]:
                    if is_admin:
                        send_telegram_to(chat_id,
                            "👋 <b>Forex AI Bot — Admin Panel</b>\n\n"
                            "<b>Commands:</b>\n"
                            "• /status — balance, equity & drawdown\n"
                            "• /pairs — active trading pairs\n"
                            "• /trades — open trades with live P&L\n"
                            "• /history — today's closed trades, wins & losses\n"
                            "• /market — market open/close countdown\n"
                            "• /today — today's pairs & why they're suited\n"
                            "• /addpair SYMBOL — add a pair\n"
                            "• /removepair SYMBOL — remove a pair\n"
                            "• /close — emergency close all positions"
                        )
                    else:
                        greeting = "👋 Welcome! You're now connected to the bot.\n\n" if is_new else "👋 Welcome back!\n\n"
                        send_telegram_to(chat_id,
                            greeting +
                            "<b>Available commands:</b>\n"
                            "• /status — live account balance & equity\n"
                            "• /pairs — currency pairs being traded\n"
                            "• /trades — open trades with live P&L\n"
                            "• /history — today's closed trades, wins & losses\n"
                            "• /market — market open/close countdown\n"
                            "• /today — today's pairs & why they're suited"
                        )

                # ── /status (all users) ───────────────────────────────────
                elif cmd in ["/status", "status"]:
                    acct = mt5.account_info()
                    if acct:
                        send_telegram_to(chat_id,
                            f"📊 <b>Account Status</b>\n"
                            f"Balance:  ${acct.balance:,.2f}\n"
                            f"Equity:   ${acct.equity:,.2f}\n"
                            f"Profit:   ${acct.profit:+,.2f}\n"
                            f"Drawdown: {pm.max_drawdown:.2%}"
                        )
                    else:
                        send_telegram_to(chat_id, "⚠️ Could not fetch account info right now.")

                # ── /pairs (all users) ────────────────────────────────────
                elif cmd in ["/pairs", "pairs"]:
                    pair_list = "\n".join(f"• {p}" for p in active_pairs) or "None active"
                    send_telegram_to(chat_id, f"📋 <b>Active Pairs</b>\n{pair_list}")

                # ── /market (all users) ──────────────────────────────────
                elif cmd in ["/market", "market"]:
                    if market_is_open():
                        close_dt  = next_market_close()
                        close_str = close_dt.strftime("%A %d %b at %H:%M UTC") if close_dt else "Friday 21:00 UTC"
                        eta       = _fmt_td(close_dt - datetime.now(timezone.utc)) if close_dt else "—"
                        sess_line = _sessions_text()
                        dead      = is_dead_zone()
                        dead_warn = "\n⚠️ <i>Dead zone — bot pausing trades (low liquidity)</i>" if dead else ""

                        # Per-session pair list
                        active_sess = get_active_sessions()
                        sess_detail = []
                        for sname in active_sess:
                            cfg = SESSIONS.get(sname)
                            if not cfg:
                                continue
                            base_to_broker = {_strip_suffix(p): p for p in active_pairs}
                            suited = [base_to_broker[b] for b in cfg["pairs"] if b in base_to_broker]
                            if suited:
                                sess_detail.append(
                                    f"{cfg['emoji']} <b>{sname}</b>: {', '.join(suited)}\n"
                                    f"   <i>{cfg['note']}</i>"
                                )
                        sess_pairs_str = "\n".join(sess_detail) if sess_detail else "—"

                        send_telegram_to(chat_id,
                            f"🟢 <b>Market is OPEN</b>\n\n"
                            f"📡 Session: {sess_line}{dead_warn}\n\n"
                            f"{sess_pairs_str}\n\n"
                            f"🕐 Closes: <b>{close_str}</b> (in {eta})"
                        )
                    else:
                        open_dt  = next_market_open()
                        open_str = open_dt.strftime("%A %d %b at %H:%M UTC") if open_dt else "Sunday 21:00 UTC"
                        eta      = _fmt_td(open_dt - datetime.now(timezone.utc)) if open_dt else "—"
                        send_telegram_to(chat_id,
                            f"🔴 <b>Market is CLOSED</b>\n\n"
                            f"Opens: <b>{open_str}</b> (in {eta})\n\n"
                            f"📡 Session schedule (UTC):\n"
                            f"🇦🇺 Sydney   — 22:00 → 07:00\n"
                            f"🇯🇵 Tokyo    — 00:00 → 09:00\n"
                            f"🇬🇧 London   — 07:00 → 16:00\n"
                            f"🗽 New York — 13:00 → 22:00\n\n"
                            f"Bot is standing by. All commands still work."
                        )

                # ── /today (all users) ────────────────────────────────────
                elif cmd in ["/today", "today"]:
                    send_telegram_to(chat_id, _today_schedule_text())

                # ── /trades (all users) ───────────────────────────────────
                elif cmd in ["/trades", "trades"]:
                    positions = mt5.positions_get() or []
                    if not positions:
                        send_telegram_to(chat_id, "📭 <b>No open trades right now.</b>")
                    else:
                        lines = []
                        total_profit = 0.0
                        for pos in positions:
                            direction = "BUY 🟢" if pos.type == mt5.ORDER_TYPE_BUY else "SELL 🔴"
                            profit    = pos.profit
                            total_profit += profit
                            sign      = "+" if profit >= 0 else ""
                            lines.append(
                                f"• <b>{pos.symbol}</b> {direction}\n"
                                f"  Lots: {pos.volume} | P&L: {sign}{profit:.2f}"
                            )
                        sign_total = "+" if total_profit >= 0 else ""
                        summary = "\n".join(lines)
                        send_telegram_to(chat_id,
                            f"📈 <b>Open Trades ({len(positions)})</b>\n\n"
                            f"{summary}\n\n"
                            f"Total P&L: <b>{sign_total}{total_profit:.2f}</b>"
                        )

                # ── /history (all users) ─────────────────────────────────
                elif cmd in ["/history", "history"]:
                    since = datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    now   = datetime.now(timezone.utc)
                    deals = mt5.history_deals_get(since, now)

                    exits = [
                        d for d in (deals or [])
                        if d.entry == mt5.DEAL_ENTRY_OUT and d.symbol
                    ]

                    if not exits:
                        send_telegram_to(chat_id, "📭 <b>No closed trades today yet.</b>")
                    else:
                        wins   = [d for d in exits if d.profit > 0]
                        losses = [d for d in exits if d.profit < 0]
                        total  = sum(d.profit for d in exits)
                        lines  = []

                        for d in exits:
                            emoji = "✅" if d.profit > 0 else "❌"
                            sign  = "+" if d.profit >= 0 else ""
                            lines.append(
                                f"{emoji} <b>{d.symbol}</b> — {sign}{d.profit:.2f}"
                            )

                        sign_total = "+" if total >= 0 else ""
                        send_telegram_to(chat_id,
                            f"📋 <b>Today's Closed Trades ({len(exits)})</b>\n\n"
                            f"{chr(10).join(lines)}\n\n"
                            f"✅ Wins: {len(wins)}  ❌ Losses: {len(losses)}\n"
                            f"Net P&L: <b>{sign_total}{total:.2f}</b>"
                        )

                # ── /addpair SYMBOL (admin only) ──────────────────────────
                elif cmd.startswith("/addpair "):
                    if not is_admin:
                        send_telegram_to(chat_id, "🔒 This command is restricted to the bot admin.")
                        continue
                    sym  = text.split(" ", 1)[1].strip().upper()
                    info = mt5.symbol_info(sym)
                    if info is None:
                        send_telegram_to(chat_id,
                            f"❌ <b>{sym}</b> not found on this broker.\n"
                            f"Check the exact symbol name in MT5 Market Watch."
                        )
                    elif sym in active_pairs:
                        send_telegram_to(chat_id, f"ℹ️ <b>{sym}</b> is already being traded.")
                    else:
                        if not info.visible:
                            mt5.symbol_select(sym, True)
                        active_pairs.append(sym)
                        user_ps = _load_user_pairs()
                        if sym not in user_ps:
                            user_ps.append(sym)
                            _save_user_pairs(user_ps)
                        send_telegram_to(chat_id,
                            f"✅ <b>{sym}</b> added.\n"
                            f"Bot will trade it on the next cycle."
                        )

                # ── /removepair SYMBOL (admin only) ───────────────────────
                elif cmd.startswith("/removepair "):
                    if not is_admin:
                        send_telegram_to(chat_id, "🔒 This command is restricted to the bot admin.")
                        continue
                    sym = text.split(" ", 1)[1].strip().upper()
                    if sym in active_pairs:
                        active_pairs.remove(sym)
                        user_ps = _load_user_pairs()
                        if sym in user_ps:
                            user_ps.remove(sym)
                            _save_user_pairs(user_ps)
                        send_telegram_to(chat_id, f"✅ <b>{sym}</b> removed from active pairs.")
                    else:
                        send_telegram_to(chat_id, f"ℹ️ <b>{sym}</b> was not in the active list.")

                # ── /close (admin only) ───────────────────────────────────
                elif cmd in ["/close", "close"]:
                    if not is_admin:
                        send_telegram_to(chat_id, "🔒 This command is restricted to the bot admin.")
                        continue
                    send_telegram_to(chat_id, "⚠️ <b>Emergency close triggered!</b>\nClosing all positions...")
                    positions = mt5.positions_get() or []
                    closed = 0
                    for pos in positions:
                        tick  = mt5.symbol_info_tick(pos.symbol)
                        otype = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
                        req = {
                            "action":       mt5.TRADE_ACTION_DEAL,
                            "position":     pos.ticket,
                            "symbol":       pos.symbol,
                            "volume":       pos.volume,
                            "type":         otype,
                            "price":        price,
                            "deviation":    20,
                            "magic":        MAGIC,
                            "comment":      "Emergency Close",
                            "type_time":    mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_FOK,
                        }
                        r = mt5.order_send(req)
                        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
                            closed += 1
                    send_telegram_to(chat_id, f"🏁 Closed {closed} of {len(positions)} positions.")

                else:
                    send_telegram_to(chat_id,
                        f"❓ Unknown command: <b>{text}</b>\n"
                        f"Type /start to see available commands."
                    )

        except Exception as e:
            print(f"⚠️  Telegram thread error: {e}")

        time.sleep(5)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND MENU SETUP
# Called once at startup.  Sets different visible command lists for admin vs
# regular users using Telegram's setMyCommands scoped API.
# ══════════════════════════════════════════════════════════════════════════════

def _setup_telegram_commands():
    token    = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token:
        return

    base = f"https://api.telegram.org/bot{token}"

    # Commands every user can see when they tap "/"
    user_commands = [
        {"command": "start",   "description": "Connect to the bot"},
        {"command": "status",  "description": "Live account balance & equity"},
        {"command": "pairs",   "description": "Currency pairs being traded"},
        {"command": "trades",  "description": "Open trades with live P&L"},
        {"command": "history", "description": "Today's closed trades & results"},
        {"command": "market",  "description": "Market open/close + active session"},
        {"command": "today",   "description": "Today's pairs & why they're chosen"},
    ]

    # Admin sees everything above PLUS these
    admin_commands = user_commands + [
        {"command": "addpair",    "description": "➕ Add a trading pair"},
        {"command": "removepair", "description": "➖ Remove a trading pair"},
        {"command": "close",      "description": "🚨 Emergency close all positions"},
    ]

    try:
        # Default scope — all users see only the user commands
        requests.post(f"{base}/setMyCommands", json={
            "commands": user_commands,
            "scope": {"type": "default"},
        }, timeout=10)

        # Admin-specific scope — admin sees full command list
        if admin_id:
            requests.post(f"{base}/setMyCommands", json={
                "commands": admin_commands,
                "scope": {"type": "chat", "chat_id": int(admin_id)},
            }, timeout=10)

        print("✅ Telegram command menus configured (admin/user scopes set).")
    except Exception as e:
        print(f"⚠️  Could not set Telegram command menus: {e}")


# ══════════════════════════════════════════════════════════════════════════════

def _sync_position_cache():
    """
    Populates _position_cache from existing MT5 positions on startup or restart.
    Prevents missed exit notifications after a power cut or crash.
    """
    positions = mt5.positions_get() or []
    for pos in positions:
        if pos.ticket not in _position_cache:
            _position_cache[pos.ticket] = {
                "symbol":    pos.symbol,
                "direction": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "entry":     pos.price_open,
                "sl":        pos.sl,
                "tp":        pos.tp,
                "lots":      pos.volume,
            }
            # Reconstruct the trailing SL step from the current SL position so
            # we don't re-apply a step that was already set before a restart.
            if pos.sl != 0:
                sl_dist = abs(pos.price_open - pos.sl)
                is_buy  = pos.type == mt5.ORDER_TYPE_BUY
                sl_offset = ((pos.sl - pos.price_open) if is_buy
                             else (pos.price_open - pos.sl))
                if sl_dist > 0:
                    ratio = sl_offset / sl_dist
                    if ratio >= 1.5:
                        _trailing_sl_step[pos.ticket] = 5
                    elif ratio >= 1.0:
                        _trailing_sl_step[pos.ticket] = 4
                    elif ratio >= 0.5:
                        _trailing_sl_step[pos.ticket] = 3
                    elif ratio >= 0.0:
                        _trailing_sl_step[pos.ticket] = 2
                    elif ratio >= -0.5:
                        _trailing_sl_step[pos.ticket] = 1
    if positions:
        print(f"  🔄 Synced {len(positions)} existing position(s) into cache after restart.")


def main():
    global active_pairs, _global_trade_count, _daily_losses, _last_trade_time

    print("=" * 55)
    print("  🤖  Forex AI Trading Bot")
    print("=" * 55)

    # ── Connect to MT5 ───────────────────────────────────────────────────────
    print("\n🔗 Connecting to MT5...")
    if not connect_mt5(LOGIN, PASSWORD, SERVER):
        print("❌ MT5 connection failed. Make sure the terminal is open.")
        sys.exit(1)

    # ── Auto-detect available pairs ──────────────────────────────────────────
    print("\n🔍 Detecting available pairs on your account:")
    active_pairs = detect_pairs()

    if not active_pairs:
        print("⚠️  No preferred pairs found — falling back to EURUSD")
        active_pairs = ["EURUSD"]

    print(f"\n📋 Trading {len(active_pairs)} pair(s): {', '.join(active_pairs)}")

    # ── Detect trading mode ───────────────────────────────────────────────────
    global TRADING_MODE, MODE_CFG
    acct        = mt5.account_info()
    initial_bal = acct.balance if acct else 1000
    TRADING_MODE = detect_trading_mode()
    MODE_CFG     = MODE_CONFIGS[TRADING_MODE]
    _start_balance = initial_bal   # reference point for milestone % calculations

    cfg = MODE_CFG
    pm = PortfolioManager(
        initial_balance        = initial_bal,
        risk_per_trade         = cfg["risk_per_trade_pct"],        # None = use tier
        max_total_drawdown_pct = cfg["max_total_dd_pct"],
        max_risk_per_trade_usd = 20,   # hard cap: never risk more than $20 per trade
    )

    milestones_str = " → ".join(f"{m:.0%}" for m in cfg["profit_milestones"])
    risk_str = f"{cfg['risk_per_trade_pct']:.1%}" if cfg["risk_per_trade_pct"] else "tier-based (auto-scales)"
    print(
        f"\n{cfg['emoji']} MODE: {cfg['label']}\n"
        f"   Daily loss limit : {cfg['max_daily_loss_pct']:.0%}\n"
        f"   Total DD limit   : {cfg['max_total_dd_pct']:.0%}\n"
        f"   Risk/trade       : {risk_str}\n"
        f"   Profit milestones: {milestones_str} "
        f"{'(hard stop at last)' if cfg['profit_hard_stop'] else '(keep growing)'}\n"
        f"   Monday gap guard : {'Yes' if cfg['monday_gap_guard'] else 'No'}\n"
        f"   Friday auto-close: {'Yes' if cfg['close_before_weekend'] else 'No'}\n"
        f"   News buffer      : {cfg['news_buffer_mins']} min"
    )

    send_telegram_message(
        f"{cfg['emoji']} <b>Bot Started — {cfg['label']} Mode</b>\n\n"
        f"Balance:       ${initial_bal:,.2f}\n"
        f"Daily limit:   {cfg['max_daily_loss_pct']:.0%}\n"
        f"Total DD cap:  {cfg['max_total_dd_pct']:.0%}\n"
        f"Milestones:    {milestones_str}\n"
        f"Friday close:  {'Yes' if cfg['close_before_weekend'] else 'No'}\n"
        f"News buffer:   {cfg['news_buffer_mins']} min"
    )

    # ── Cloud restore — download missing models from Hugging Face on startup ──
    print("\n☁️  Checking cloud model storage...")
    download_from_hub()

    # ── Load ML models ────────────────────────────────────────────────────────
    model    = load_model()
    rf_model = load_rf_model()

    if model and rf_model:
        print("✅ Global ensemble loaded (XGBoost + Random Forest).")
    elif model:
        print("✅ Global XGBoost loaded (RF not yet trained — will train Sunday).")
    else:
        print("⚠️  No models found. Using rule-based ICT signals until Sunday.")

    # ── Load per-pair models (override global where available) ───────────────
    print("\n🧠 Loading per-pair models...")
    has_pair_models = False
    for pair in active_pairs:
        base = _strip_suffix(pair)
        xgb_p, rf_p = load_pair_models(base)
        pair_models[pair] = (xgb_p, rf_p)
        if xgb_p or rf_p:
            has_pair_models = True
    if not has_pair_models:
        print("  ⚠️  No per-pair models yet — will train per-pair on Sunday.")

    # ── Sync existing positions after restart / power cut ────────────────────
    _sync_position_cache()

    # ── Configure Telegram command menus (admin sees more than users) ────────
    _setup_telegram_commands()

    # ── Start Telegram listener in background ────────────────────────────────
    tg = threading.Thread(target=_telegram_loop, args=(pm,), daemon=True)
    tg.start()

    # ── Notify Telegram ──────────────────────────────────────────────────────
    send_telegram_message(
        f"🟢 <b>Bot Started</b>\n"
        f"Pairs ({len(active_pairs)}): {', '.join(active_pairs)}\n"
        f"Balance: ${initial_bal:,.2f}\n"
        f"Retrains: every Sunday automatically\n\n"
        f"Add pairs: /addpair SYMBOL\n"
        f"Remove: /removepair SYMBOL"
    )

    current_day           = date.today()
    market_closed_alerted = False

    print("\n🚀 Bot running. Press Ctrl+C to stop.\n")
    _set_mood("ready", "Scanning markets... watching for A+ setups.", f"{len(active_pairs)} pairs active")

    # Notify all users the bot is live
    _acct = mt5.account_info()
    _bal  = _acct.balance if _acct else 0
    _startup_msg = (
        f"🟢 <b>Bot is Online</b>\n\n"
        f"Mode:     {MODE_CFG['emoji']} {MODE_CFG['label']}\n"
        f"Balance:  ${_bal:,.2f}\n"
        f"Pairs:    {len(active_pairs)} active\n"
        f"Session:  {_sessions_text()}\n"
        f"Strategy: ICT + SMC + Market Mechanics\n\n"
        f"Trading resumes on next signal. 🤖"
    )
    for _uid in (_load_registered_users() | {os.getenv("TELEGRAM_CHAT_ID", "")}):
        if _uid:
            send_telegram_to(_uid, _startup_msg)

    try:
        while True:

            # ── Reconnect watchdog ────────────────────────────────────────
            if not is_terminal_connected():
                print("⚠️  MT5 disconnected. Reconnecting...")
                if not connect_mt5(LOGIN, PASSWORD, SERVER):
                    time.sleep(10)
                    continue
                send_telegram_message("🔄 MT5 reconnected.")

            # ── Daily balance reset ───────────────────────────────────────
            today = date.today()
            if today != current_day:
                acct = mt5.account_info()
                if acct:
                    pm.new_day_reset(acct.balance)
                _pair_trade_count.clear()   # reset per-pair daily trade counter
                _pending_signals.clear()    # stale pending signals don't carry over
                _global_trade_count = 0     # reset global daily cap
                _daily_losses       = 0     # reset consecutive-loss guard
                _last_trade_time    = 0.0   # reset inter-trade cooldown
                current_day = today

            # ── Monday weekly report ─────────────────────────────────────
            send_weekly_report(pm)

            # ── Sunday auto-retrain ───────────────────────────────────────
            if today_is_sunday():
                run_auto_retrain()
                model    = load_model()       # pick up freshly trained XGBoost
                rf_model = load_rf_model()    # pick up freshly trained RF

            # ── Market hours gate ─────────────────────────────────────────
            if not market_is_open():
                if not market_closed_alerted:
                    open_dt  = next_market_open()
                    open_str = open_dt.strftime("%A %d %b at %H:%M UTC") if open_dt else "Sunday 21:00 UTC"
                    eta      = _fmt_td(open_dt - datetime.now(timezone.utc)) if open_dt else "—"
                    print("😴 Market closed. Bot standing by...")
                    send_telegram_message(
                        f"😴 <b>Market Closed — Standing By</b>\n\n"
                        f"Next open: <b>{open_str}</b> (in {eta})\n"
                        f"Bot will resume trading automatically.\n\n"
                        f"Use /market for live countdown · /today for tomorrow's pairs"
                    )
                    market_closed_alerted = True
                time.sleep(300)
                continue

            if market_closed_alerted:
                print("🥳 Market open — resuming trading.")
                send_telegram_message(
                    f"🥳 <b>Market Open — Trading Resumed</b>\n\n"
                    f"{_today_schedule_text()}"
                )
                market_closed_alerted = False

            # ── Friday auto-close (prop firm mode) ───────────────────────
            if is_friday_close_time():
                positions = mt5.positions_get() or []
                if positions:
                    print("🏆 Friday close — closing all positions before weekend...")
                    for pos in positions:
                        tick  = mt5.symbol_info_tick(pos.symbol)
                        otype = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
                        mt5.order_send({
                            "action": mt5.TRADE_ACTION_DEAL, "position": pos.ticket,
                            "symbol": pos.symbol, "volume": pos.volume,
                            "type": otype, "price": price, "deviation": 20,
                            "magic": MAGIC, "comment": "Friday Close",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_FOK,
                        })
                    send_telegram_message(
                        f"{MODE_CFG['emoji']} <b>Friday Close Complete</b>\n"
                        "All positions closed before weekend to protect drawdown limit."
                    )
                time.sleep(POLL_SECONDS)
                continue

            # ── Monday gap guard (all modes) ──────────────────────────────
            if is_monday_gap_time():
                time.sleep(POLL_SECONDS)
                continue

            # ── Sync balance + circuit breakers ───────────────────────────
            acct = mt5.account_info()
            if acct:
                pm.update_balance(acct.balance)
                all_users = _load_registered_users() | {os.getenv("TELEGRAM_CHAT_ID", "")}
                _check_all_time_high(acct.balance, all_users)

                # Total drawdown hard stop
                if pm.check_total_drawdown(acct.balance):
                    print("🛑 Total drawdown limit hit — bot halted.")
                    time.sleep(300)
                    continue

                # Daily loss limit (mode-specific tightness)
                if pm.check_circuit_breaker(acct.balance, MODE_CFG["max_daily_loss_pct"]):
                    print("🚨 Daily loss limit hit — pausing until tomorrow.")
                    time.sleep(300)
                    continue

                # Profit milestones — celebrate or hard-stop
                if check_profit_milestones(acct.balance, _start_balance, all_users):
                    time.sleep(POLL_SECONDS)
                    continue

            # ── Sync our open-trade list with live MT5 positions ──────────
            live_pos     = mt5.positions_get() or []
            live_tickets = {p.ticket for p in live_pos}
            for t in list(pm.open_trades):
                tid = t.get("ticket") if isinstance(t, dict) else t
                if tid not in live_tickets:
                    pm.close_trade_by_ticket(tid)

            # ── Detect closed positions + notify users ────────────────────
            _check_closed_positions()

            # ── Phase A: trailing stops + partial close at 1R ─────────────
            manage_open_positions()

            # ── News intelligence — notify users of upcoming/released events ─
            _check_and_notify_news()
            _write_news_events()

            # ── Session gate — skip dead zone entirely ────────────────────
            if is_dead_zone():
                time.sleep(POLL_SECONDS)
                continue

            current_sessions = get_active_sessions()

            # ── Apply daily pair schedule ─────────────────────────────────
            # Only evaluate the pairs that suit today's session character.
            # Non-scheduled pairs are marked OFF_SESSION in the scanner so the
            # dashboard reflects the correct state.
            _wd_now      = datetime.now(timezone.utc).weekday()
            _sched_today = DAILY_PAIR_SCHEDULE.get(_wd_now)
            if _sched_today:
                _sched_bases  = set(_sched_today["pairs"])
                _pairs_today  = [p for p in active_pairs if _strip_suffix(p) in _sched_bases]
                _off_schedule = [p for p in active_pairs if _strip_suffix(p) not in _sched_bases]
                if not _pairs_today:          # safety: if schedule wipes all pairs, trade all
                    _pairs_today  = list(active_pairs)
                    _off_schedule = []
            else:
                _pairs_today  = list(active_pairs)
                _off_schedule = []

            # ── Evaluate each active pair ─────────────────────────────────
            _scan_state.clear()

            # Mark off-schedule pairs so dashboard shows NOT ACTIVE TODAY
            for _op in _off_schedule:
                _scan_state[_op] = {"status": "NOT_TODAY", "signal": 0, "confidence": 0.0}

            for pair in _pairs_today:

                # Skip if we already hold a position on this pair
                existing = mt5.positions_get(symbol=pair)
                if existing and len(existing) > 0:
                    _scan_state[pair] = {"status": "HOLDING", "signal": 0, "confidence": 0.0}
                    continue

                if not pm.can_open_trade():
                    _scan_state[pair] = {"status": "MAX_RISK", "signal": 0, "confidence": 0.0}
                    print("⛔ Max portfolio risk reached — no new trades this cycle.")
                    break

                # Global daily cap — hard ceiling across all pairs
                if _global_trade_count >= GLOBAL_DAILY_TRADE_CAP:
                    _scan_state[pair] = {"status": "DAILY_LIMIT", "signal": 0, "confidence": 0.0}
                    continue

                # Consecutive-loss guard — stop after MAX_DAILY_LOSSES SL hits today
                if _daily_losses >= MAX_DAILY_LOSSES:
                    _scan_state[pair] = {"status": "LOSS_GUARD", "signal": 0, "confidence": 0.0}
                    continue

                # Global inter-trade cooldown — 60 min between any two trades
                secs_since_last = time.time() - _last_trade_time
                if _last_trade_time > 0 and secs_since_last < GLOBAL_TRADE_COOLDOWN_SECS:
                    mins_left = int((GLOBAL_TRADE_COOLDOWN_SECS - secs_since_last) / 60)
                    _scan_state[pair] = {"status": "COOLDOWN", "signal": 0, "confidence": 0.0}
                    if pair == list(active_pairs)[0]:   # log once per cycle, not per pair
                        print(f"⏸️  Global cooldown: {mins_left}m until next trade window.")
                    continue

                # Max 2 trades per pair per day — second entry only if first was BE or better
                if _pair_trade_count.get(pair, 0) >= 3:
                    _scan_state[pair] = {"status": "DAILY_LIMIT", "signal": 0, "confidence": 0.0}
                    continue

                # Loss cooldown — 3-hour lockout after a stop-loss on this pair
                last_loss = _pair_loss_cooldown.get(pair, 0)
                if time.time() - last_loss < LOSS_COOLDOWN_SECS:
                    remaining = int((LOSS_COOLDOWN_SECS - (time.time() - last_loss)) / 60)
                    if pair not in _news_skip_active:
                        _news_skip_active.add(pair)
                        print(f"⏸️  {pair}: SL cooldown — {remaining}m remaining.")
                    _scan_state[pair] = {"status": "COOLDOWN", "signal": 0, "confidence": 0.0}
                    continue

                # Session filter — only trade pairs suited to the active session
                if not session_suits_pair(pair, current_sessions):
                    _scan_state[pair] = {"status": "OFF_SESSION", "signal": 0, "confidence": 0.0}
                    continue

                # Phase A: news filter — skip if within 30 min of HIGH news
                if is_news_time(pair):
                    if pair not in _news_skip_active:
                        _news_skip_active.add(pair)
                        print(f"📰 {pair}: HIGH-impact news — pausing until window clears.")
                    _scan_state[pair] = {"status": "NEWS", "signal": 0, "confidence": 0.0}
                    continue
                else:
                    _news_skip_active.discard(pair)   # window cleared — reset

                # Phase A: correlation filter — skip if correlated pair is open
                if has_correlated_open(pair):
                    if pair not in _news_skip_active:
                        _news_skip_active.add(pair)
                        print(f"🔗 {pair}: Correlated pair already open — skipping.")
                    _scan_state[pair] = {"status": "CORRELATED", "signal": 0, "confidence": 0.0}
                    continue

                # Use pair-specific model if available, fall back to global
                _pair_xgb, _pair_rf = pair_models.get(pair, (model, rf_model))
                signal, confidence, ict_score = get_signal(pair, _pair_xgb, _pair_rf)
                if signal == 0:
                    _pending_signals.pop(pair, None)   # signal gone — reset confirmation
                    _scan_state[pair] = {"status": "WATCHING", "signal": 0, "confidence": round(confidence, 3), "ict_score": ict_score}
                    continue

                # ── Confidence gate ───────────────────────────────────────
                # Evening (18–22 UTC): require 85% — strict, no exceptions.
                # Standard hours: require 80%, OR 75%+ with ICT score ≥ 6
                #   (strong confluence justifies slightly lower AI confidence).
                _min_conf  = EVENING_MIN_CONFIDENCE if is_evening_caution() else MIN_CONFIDENCE
                _is_evening = is_evening_caution()
                _passes = confidence >= _min_conf
                if not _passes and not _is_evening:
                    # 78% tier: AI uncertain but ICT confluence very strong — raised bar
                    _passes = confidence >= 0.78 and ict_score >= 8
                    if _passes:
                        print(f"  ✅ {pair}: 78% tier — conf {confidence:.0%} + ICT score {ict_score}/20 (≥8 required).")
                if not _passes:
                    _pending_signals.pop(pair, None)
                    period = "evening" if _is_evening else "standard"
                    print(f"  ⏸️  {pair}: conf {confidence:.0%} below gate ({period}) | ICT {ict_score}/20 — skip.")
                    _scan_state[pair] = {"status": "LOW_CONF", "signal": signal, "confidence": round(confidence, 3), "ict_score": ict_score}
                    _set_mood("unsure",
                              f"Signal on {_strip_suffix(pair)} but not convinced yet...",
                              f"{confidence:.0%} conf | ICT {ict_score}/20 | needs {_min_conf:.0%} to fire",
                              pair=_strip_suffix(pair), confidence=confidence)
                    continue

                # ── Pending signal double-confirmation ────────────────────
                # Signal must appear on 2 consecutive 30-second cycles before
                # we act — filters one-bar false breakouts and fake-outs.
                prev_signal = _pending_signals.get(pair)
                if prev_signal != signal:
                    # First sighting of this signal direction — save and wait
                    _pending_signals[pair] = signal
                    print(f"  ⏳ {pair}: signal {'+1' if signal==1 else '-1'} pending confirmation (conf {confidence:.0%} | ICT {ict_score}/20).")
                    _scan_state[pair] = {"status": "PENDING", "signal": signal, "confidence": round(confidence, 3), "ict_score": ict_score}
                    _dir_lbl = "BUY" if signal == 1 else "SELL"
                    _set_mood("watching",
                              f"Setup detected on {_strip_suffix(pair)}! Waiting for confirmation...",
                              f"{_dir_lbl} signal @ {confidence:.0%} conf | ICT {ict_score}/20 — need 2 cycles",
                              pair=_strip_suffix(pair), confidence=confidence)
                    continue
                # Same direction confirmed on second cycle — proceed to trade
                _pending_signals.pop(pair, None)

                # Spread filter — per-instrument threshold keeps forex at ~3 pips
                tick = mt5.symbol_info_tick(pair)
                info = mt5.symbol_info(pair)
                if not tick or not info:
                    _scan_state[pair] = {"status": "WATCHING", "signal": signal, "confidence": round(confidence, 3)}
                    continue
                base_name  = _strip_suffix(pair)
                max_spread = _SPREAD_LIMITS.get(base_name, _SPREAD_DEFAULT)
                spread_pct = (tick.ask - tick.bid) / tick.bid if tick.bid > 0 else 1.0
                if spread_pct > max_spread:
                    if pair not in _news_skip_active:
                        _news_skip_active.add(pair)
                        print(f"⏳ {pair}: spread too wide ({spread_pct*100:.3f}%) — skipping.")
                    _scan_state[pair] = {"status": "SPREAD", "signal": signal, "confidence": round(confidence, 3)}
                    continue

                # Position size using ATR-based stop distance
                df_5m = _fetch_bars(pair, mt5.TIMEFRAME_M5, count=50)
                if df_5m is None:
                    continue
                df_5m  = add_indicators(df_5m)
                atr    = df_5m["ATR"].iloc[-1]
                sl_pts = int((atr * 1.5) / info.point)   # 1.5×ATR matches ML training SL
                if confidence >= 0.92 and ict_score >= 10:
                    tp_pts = sl_pts * 10  # 1:10 — elite setup, very high confidence
                elif confidence >= 0.87 and ict_score >= 8:
                    tp_pts = sl_pts * 5   # 1:5 — strong confluence, elevated confidence
                else:
                    tp_pts = sl_pts * 3   # 1:3 default — profitable at ≥35% win rate
                lots   = pm.calculate_position_size(pair, sl_pts)

                if lots <= 0:
                    continue

                result = place_trade(pair, signal=signal, lot=lots, sl=sl_pts, tp=tp_pts)

                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    _scan_state[pair] = {"status": "TRADED", "signal": signal, "confidence": round(confidence, 3)}
                    direction = "BUY" if signal == 1 else "SELL"
                    _set_mood("high_conf",
                              f"High conviction trade placed — {_strip_suffix(pair)} {direction}!",
                              f"{confidence:.0%} confidence | ICT {ict_score}/20 | Trailing SL active 🔥",
                              pair=_strip_suffix(pair), confidence=confidence)
                    _pair_trade_count[pair] = _pair_trade_count.get(pair, 0) + 1
                    _global_trade_count += 1
                    _last_trade_time = time.time()
                    _pending_signals.pop(pair, None)  # clear confirmation state
                    _news_skip_active.discard(pair)   # reset so future blocks log once again

                    pm.open_trade({
                        "ticket":     result.order,
                        "symbol":     pair,
                        "type":       direction,
                        "lots":       lots,
                        "confidence": confidence,
                    })

                    log_trade({
                        "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "symbol":        pair,
                        "type":          direction,
                        "lots":          lots,
                        "ai_confidence": round(confidence, 4),
                        "ticket":        result.order,
                    })

                    log_execution_note(
                        result.order, pair, signal, lots,
                        f"AI Confidence: {confidence:.2%} | Spread: {spread_pct*100:.4f}%"
                    )

                    print(
                        f"✅ [{datetime.now().strftime('%H:%M:%S')}] "
                        f"{pair} {direction} {lots} lots | conf={confidence:.2f}"
                    )

                    # ── Cache position for exit detection ─────────────────
                    entry_price = tick.ask if signal == 1 else tick.bid
                    pt          = info.point
                    sl_price    = entry_price - sl_pts * pt if signal == 1 else entry_price + sl_pts * pt
                    tp_price    = entry_price + tp_pts * pt if signal == 1 else entry_price - tp_pts * pt
                    _position_cache[result.order] = {
                        "symbol":    pair,
                        "direction": direction,
                        "entry":     entry_price,
                        "sl":        sl_price,
                        "tp":        tp_price,
                        "lots":      lots,
                    }

                    # ── Broadcast trade entry to all registered users ──────
                    rrr         = tp_pts // sl_pts
                    conf_bar    = "🟢🟢🟢" if confidence >= 0.80 else ("🟡🟡" if confidence >= 0.60 else "🟠")
                    sess_line   = _sessions_text()
                    direction_emoji = "🟢 BUY" if signal == 1 else "🔴 SELL"
                    trade_msg = (
                        f"📣 <b>Trade Entered</b>\n\n"
                        f"Pair:       <b>{pair}</b>\n"
                        f"Direction:  {direction_emoji}\n"
                        f"Lots:       {lots}\n"
                        f"Entry:      {entry_price:.5f}\n"
                        f"Stop Loss:  {sl_price:.5f}\n"
                        f"Take Profit:{tp_price:.5f}\n"
                        f"RRR:        1:{rrr}\n"
                        f"Confidence: {conf_bar} {confidence:.0%}\n"
                        f"Session:    {sess_line}\n"
                        f"Ticket:     #{result.order}"
                    )
                    all_users = _load_registered_users() | {os.getenv("TELEGRAM_CHAT_ID", "")}
                    for uid in all_users:
                        if uid:
                            send_telegram_to(uid, trade_msg)

                else:
                    _scan_state[pair] = {"status": "FAILED", "signal": signal, "confidence": round(confidence, 3)}
                    err = result.comment if result else "no response from MT5"
                    print(f"❌ Trade failed for {pair}: {err}")
                    send_telegram_message(f"⚠️ Trade failed for <b>{pair}</b>: {err}")

            # ── Reversal partials — protect profits when AI flips direction ──
            _check_reversal_partials()
            _write_signal_scan(_scan_state)

            # If no mood-changing event fired this cycle, keep bot "ready"
            _current_mood_state = ""
            try:
                import json as _j
                with open(_MOOD_FILE) as _mf:
                    _current_mood_state = _j.load(_mf).get("state", "")
            except Exception:
                pass
            if _current_mood_state not in ("high_conf", "won", "lost", "loss_guard", "watching", "retraining", "week_prep"):
                _set_mood("ready", "Scanning markets... watching for A+ setups.", f"{len(active_pairs)} pairs active")

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user.")
        send_telegram_message("🔴 <b>Bot Stopped</b>")
    except Exception as e:
        tb = traceback.format_exc()
        print(f"\n💥 Unexpected crash:\n{tb}")
        os.makedirs("logs", exist_ok=True)
        with open("logs/crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"CRASH AT {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n{tb}\n")
        send_telegram_message(
            f"💥 <b>Bot Crashed</b>\n\n"
            f"Error: {str(e)[:200]}\n\n"
            f"Your open trades in MT5 are safe.\n"
            f"Check logs/crash.log for details.\n"
            f"Restarting automatically..."
        )
        raise   # re-raise so run_bot.py can catch and restart
    finally:
        shutdown_mt5()
        print("✅ MT5 connection closed.")


if __name__ == "__main__":
    main()
