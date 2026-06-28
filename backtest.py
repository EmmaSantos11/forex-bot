"""
backtest.py — Run the full ICT + SMC + ADX strategy on 6 months of MT5 history.

Usage:
    python backtest.py                  # tests all preferred pairs
    python backtest.py EURUSD GBPUSD    # tests specific pairs only

Results are printed to console and saved to logs/backtest_results.csv
Requires MT5 to be open and logged in.
"""

import os
import sys
import warnings
from datetime import datetime, timezone, timedelta

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

from strategy import add_indicators, add_features, generate_signals_ict
from core.multi_timeframe import resample_timeframes
from mt5_connector import connect_mt5, shutdown_mt5

load_dotenv()
warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

LOOKBACK_DAYS   = 180       # 6 months of history
INITIAL_BALANCE = 5000.0    # starting balance for simulation
RISK_PCT        = 0.005     # 0.5% risk per trade (matches Growth/Standard tier)
SL_ATR_MULT     = 1.5       # SL = ATR × 1.5 (matches live trading)
RRR_LOW         = 5         # 1:5 RRR for confidence < 0.80
RRR_HIGH        = 10        # 1:10 RRR for confidence ≥ 0.80
CONF_HIGH_THRESH = 0.80     # threshold for 1:10

PREFERRED_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "USDCAD", "NZDUSD", "EURJPY",
    "XAUUSD", "BTCUSD",
]

os.makedirs("logs", exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_suffix(symbol: str) -> str:
    s = symbol.upper()
    for sfx in [".M", "M", ".C", "C", ".PRO", "PRO", ".ECN", "ECN", ".R", "R"]:
        if s.endswith(sfx) and len(s) > len(sfx):
            return s[: -len(sfx)]
    return s


def _resolve_symbol(base: str) -> str | None:
    """Find the broker's actual symbol name for a base pair like 'EURUSD'."""
    all_syms = mt5.symbols_get()
    if not all_syms:
        return None
    for s in all_syms:
        if _strip_suffix(s.name) == base.upper():
            return s.name
    return None


def _fetch_history(symbol: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame | None:
    """Fetches M5 bars going back `days` calendar days."""
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, since, now)
    if rates is None or len(rates) < 200:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = df[["open", "high", "low", "close", "tick_volume"]]
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    return df


def _run_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """Applies the full indicator + ICT signal pipeline."""
    tfs = resample_timeframes(df)
    for tf in tfs:
        tfs[tf] = add_indicators(tfs[tf])
        tfs[tf] = add_features(tfs[tf])
    result = generate_signals_ict(tfs)
    return result


def _simulate_trades(df: pd.DataFrame, symbol: str) -> dict:
    """
    Walks through the 5M signal df bar by bar.
    Entry: on signal bar at close price.
    SL:    ATR × 1.5 from entry.
    TP:    SL × RRR (5 or 10 depending on ICT_Score / confidence).
    Exit:  scan forward bars until High/Low hits TP or SL.
    """
    balance    = INITIAL_BALANCE
    peak_bal   = INITIAL_BALANCE
    max_dd     = 0.0
    trades     = []
    open_trade = None

    for i in range(len(df)):
        bar = df.iloc[i]

        # ── Manage open trade ─────────────────────────────────────────────────
        if open_trade:
            high_i = bar["High"]
            low_i  = bar["Low"]
            t      = open_trade

            hit_tp = (t["dir"] ==  1 and high_i >= t["tp"]) or \
                     (t["dir"] == -1 and low_i  <= t["tp"])
            hit_sl = (t["dir"] ==  1 and low_i  <= t["sl"]) or \
                     (t["dir"] == -1 and high_i >= t["sl"])

            if hit_tp or hit_sl:
                # Both hit same bar — whichever was closer to entry price wins
                if hit_tp and hit_sl:
                    tp_dist = abs(t["tp"] - t["entry"])
                    sl_dist = abs(t["sl"] - t["entry"])
                    hit_tp  = tp_dist <= sl_dist
                    hit_sl  = not hit_tp

                pnl = t["risk_usd"] * t["rrr"] if hit_tp else -t["risk_usd"]
                balance += pnl
                peak_bal = max(peak_bal, balance)
                dd = (peak_bal - balance) / peak_bal if peak_bal else 0
                max_dd = max(max_dd, dd)
                trades.append({
                    "symbol":  symbol,
                    "dir":     "BUY" if t["dir"] == 1 else "SELL",
                    "entry":   t["entry"],
                    "exit":    t["tp"] if hit_tp else t["sl"],
                    "pnl":     round(pnl, 2),
                    "result":  "WIN" if hit_tp else "LOSS",
                    "rrr":     t["rrr"],
                    "bars":    i - t["entry_bar"],
                    "regime":  t.get("regime", ""),
                })
                open_trade = None

        # ── Check for new entry ───────────────────────────────────────────────
        if open_trade is None:
            sig   = int(bar.get("Signal", 0))
            score = float(bar.get("ICT_Score", 0))

            if sig == 0:
                continue

            entry = bar["Close"]
            atr   = bar.get("ATR", 0)
            if atr == 0:
                continue

            sl_dist   = atr * SL_ATR_MULT
            rrr       = RRR_HIGH if (score / 10.0) >= CONF_HIGH_THRESH else RRR_LOW
            tp_dist   = sl_dist * rrr
            risk_usd  = balance * RISK_PCT

            open_trade = {
                "dir":       sig,
                "entry":     entry,
                "sl":        entry - sl_dist if sig == 1 else entry + sl_dist,
                "tp":        entry + tp_dist if sig == 1 else entry - tp_dist,
                "risk_usd":  risk_usd,
                "rrr":       rrr,
                "entry_bar": i,
                "regime":    bar.get("Regime", ""),
            }

    # ── Summary ───────────────────────────────────────────────────────────────
    if not trades:
        return {
            "symbol": symbol, "trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "profit_factor": 0, "net_pnl": 0,
            "max_dd_pct": 0, "final_balance": balance,
        }

    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    net    = sum(t["pnl"] for t in trades)
    gross_win  = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    return {
        "symbol":        symbol,
        "trades":        len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      len(wins) / len(trades) * 100,
        "profit_factor": round(pf, 2),
        "net_pnl":       round(net, 2),
        "max_dd_pct":    round(max_dd * 100, 2),
        "final_balance": round(balance, 2),
        "trade_log":     trades,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  📊  Forex AI Bot — Backtest")
    print(f"  Strategy: ICT + SMC + ADX | Last {LOOKBACK_DAYS} days")
    print(f"  Balance:  ${INITIAL_BALANCE:,.0f} | Risk: {RISK_PCT:.1%}/trade")
    print("=" * 60)

    print("\n🔗 Connecting to MT5...")
    if not connect_mt5(LOGIN, PASSWORD, SERVER):
        print("❌ Could not connect to MT5. Make sure it is open and logged in.")
        return

    # Resolve which pairs to test
    pairs_to_test = sys.argv[1:] if len(sys.argv) > 1 else PREFERRED_PAIRS
    resolved = []
    for base in pairs_to_test:
        sym = _resolve_symbol(base)
        if sym:
            resolved.append((base, sym))
            print(f"  ✅ {base} → {sym}")
        else:
            print(f"  ⏭️  {base} — not found on broker (skipped)")

    if not resolved:
        print("\n❌ No pairs resolved. Exiting.")
        shutdown_mt5()
        return

    all_results = []
    all_trades  = []

    for base, sym in resolved:
        print(f"\n{'─'*50}")
        print(f"  Testing {sym}...")

        df_raw = _fetch_history(sym)
        if df_raw is None:
            print(f"  ⚠️  Not enough history for {sym} — skipping.")
            continue

        print(f"  Bars fetched:  {len(df_raw):,}")

        df_sig = _run_strategy(df_raw)
        n_signals = (df_sig["Signal"] != 0).sum()
        print(f"  Signals found: {n_signals}")

        result = _simulate_trades(df_sig, sym)
        all_results.append(result)
        if "trade_log" in result:
            all_trades.extend(result.pop("trade_log"))

        sign = "+" if result["net_pnl"] >= 0 else ""
        print(
            f"  Trades: {result['trades']}  |  "
            f"Win rate: {result['win_rate']:.1f}%  |  "
            f"PF: {result['profit_factor']}  |  "
            f"Net P&L: {sign}${result['net_pnl']}  |  "
            f"Max DD: {result['max_dd_pct']}%"
        )

    shutdown_mt5()

    if not all_results:
        print("\n⚠️  No results to show.")
        return

    # ── Combined summary ──────────────────────────────────────────────────────
    total_trades = sum(r["trades"] for r in all_results)
    total_wins   = sum(r["wins"]   for r in all_results)
    total_net    = sum(r["net_pnl"] for r in all_results)
    gross_w = sum(t["pnl"] for t in all_trades if t["result"] == "WIN")
    gross_l = abs(sum(t["pnl"] for t in all_trades if t["result"] == "LOSS"))
    overall_pf   = round(gross_w / gross_l, 2) if gross_l > 0 else float("inf")
    overall_wr   = total_wins / total_trades * 100 if total_trades > 0 else 0

    print(f"\n{'═'*60}")
    print("  OVERALL RESULTS")
    print(f"{'═'*60}")
    print(f"  Pairs tested:    {len(all_results)}")
    print(f"  Total trades:    {total_trades}")
    print(f"  Win rate:        {overall_wr:.1f}%")
    print(f"  Profit factor:   {overall_pf}")
    sign = "+" if total_net >= 0 else ""
    print(f"  Net P&L:         {sign}${total_net:,.2f}")
    print(f"{'─'*60}")

    # Per-pair table
    print(f"\n  {'Pair':<10} {'Trades':>7} {'Win%':>7} {'PF':>6} {'Net P&L':>10} {'Max DD':>8}")
    print(f"  {'─'*10} {'─'*7} {'─'*7} {'─'*6} {'─'*10} {'─'*8}")
    for r in sorted(all_results, key=lambda x: x["net_pnl"], reverse=True):
        s = "+" if r["net_pnl"] >= 0 else ""
        print(
            f"  {r['symbol']:<10} {r['trades']:>7} "
            f"{r['win_rate']:>6.1f}% {r['profit_factor']:>6} "
            f"{s}${r['net_pnl']:>8,.2f} {r['max_dd_pct']:>7.1f}%"
        )

    # ── Save CSV ──────────────────────────────────────────────────────────────
    if all_trades:
        csv_path = "logs/backtest_results.csv"
        pd.DataFrame(all_trades).to_csv(csv_path, index=False)
        print(f"\n  💾 Full trade log saved → {csv_path}")

    summary_path = "logs/backtest_summary.csv"
    pd.DataFrame(all_results).to_csv(summary_path, index=False)
    print(f"  💾 Summary saved → {summary_path}")
    print()


if __name__ == "__main__":
    main()
