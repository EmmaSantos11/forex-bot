"""
walk_forward_validation.py — Institutional Walk-Forward Validation (WFV) for Forex AI Bot.

Eliminates backtest overfitting by testing purely on unseen out-of-sample (OOS) future data:
  • Divides historical market data into sequential rolling train/test windows (e.g. 90-day train -> 30-day test).
  • In each fold:
      1. Generates labeled samples from in-sample data.
      2. Trains XGBoost + Random Forest ensemble models.
      3. Tests the ensemble strictly on the subsequent out-of-sample period.
      4. Simulates trades with institutional execution rules:
         - 1.5x ATR Stop Loss
         - 1:3 base RRR (scales to 1:5 / 1:10 on high confidence & ICT score)
         - 50% partial exit at 1R profit with Breakeven stop
         - 75% minimum AI confidence gate (both models must agree)
  • Aggregates all out-of-sample results into a true out-of-sample equity curve and metric report.

Usage:
    python walk_forward_validation.py              # runs on EURUSD
    python walk_forward_validation.py XAUUSD       # runs on Gold
    python walk_forward_validation.py EURUSD BTCUSD
"""

import os
import sys
import warnings
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
warnings.filterwarnings("ignore")

from mt5_connector import connect_mt5, shutdown_mt5
from strategy import add_indicators, add_features, generate_signals_ict, add_ict_features, SMT_PAIRS
from ml_model import (
    prepare_ml_data, train_model, train_rf_model,
    generate_ensemble_signals, add_session_features, FEATURE_COLS,
)
from core.multi_timeframe import resample_timeframes

LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

# ── Walk-Forward Configuration ────────────────────────────────────────────────
TOTAL_DAYS      = 180       # ~6 months total history
TRAIN_DAYS      = 90        # 3 months training window
TEST_DAYS       = 30        # 1 month out-of-sample testing window
STEP_DAYS       = 30        # 1 month forward step

INITIAL_BALANCE = 5000.0    # starting balance
RISK_PCT        = 0.005     # 0.5% risk per trade
MIN_CONFIDENCE  = 0.75      # 75% AI confidence floor
SL_ATR_MULT     = 1.5       # 1.5x ATR Stop Loss
BASE_RRR        = 3.0       # 1:3 Base RRR

os.makedirs("logs", exist_ok=True)


def _strip_suffix(symbol: str) -> str:
    s = symbol.upper()
    for sfx in [".M", "M", ".C", "C", ".PRO", "PRO", ".ECN", "ECN", ".R", "R"]:
        if s.endswith(sfx) and len(s) > len(sfx):
            return s[: -len(sfx)]
    return s


def _resolve_symbol(base: str) -> str | None:
    all_syms = mt5.symbols_get()
    if not all_syms:
        return None
    for s in all_syms:
        if _strip_suffix(s.name) == base.upper():
            return s.name
    return None


def fetch_full_history(symbol: str, days: int = TOTAL_DAYS) -> pd.DataFrame | None:
    """Fetches M5 bars from MT5 for the specified history window."""
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, since, now)
    if rates is None or len(rates) < 3000:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = df[["open", "high", "low", "close", "tick_volume"]]
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    return df


def prepare_market_dataframe(df_m5: pd.DataFrame, df_corr_m5: pd.DataFrame | None = None) -> pd.DataFrame:
    """Runs indicator, multi-timeframe resampling, ICT, and session feature pipeline."""
    tfs = resample_timeframes(df_m5)
    for tf in tfs:
        tfs[tf] = add_indicators(tfs[tf])
        tfs[tf] = add_features(tfs[tf])

    df_final = generate_signals_ict(tfs)

    df_corr_aligned = None
    if df_corr_m5 is not None and not df_corr_m5.empty:
        df_corr_proc = add_features(add_indicators(df_corr_m5.copy()))
        df_corr_aligned = df_corr_proc.reindex(df_final.index, method="ffill")

    df_final = add_ict_features(df_final, tfs.get("1h"), tfs.get("4h"), df_corr=df_corr_aligned)
    df_final = add_session_features(df_final)
    return df_final


def simulate_oos_trades(df_oos: pd.DataFrame, xgb_model, rf_model, initial_balance: float) -> tuple[list[dict], float]:
    """
    Simulates trading strictly on out-of-sample data using the models trained on in-sample data.
    Includes 50% partial exit at 1R profit with Breakeven stop.
    """
    signals_df = generate_ensemble_signals(df_oos, xgb_model, rf_model)
    balance = initial_balance
    trades = []
    open_trade = None

    n_sigs = int((signals_df["Signal"] != 0).sum()) if "Signal" in signals_df.columns else 0
    print(f"       [OOS Evaluation] {n_sigs} ensemble signals detected across {len(signals_df):,} bars")

    for i in range(len(signals_df)):
        bar = signals_df.iloc[i]

        # Manage open position
        if open_trade:
            t = open_trade
            high = bar["High"]
            low  = bar["Low"]

            # Check 1R partial take profit
            if not t["partial_taken"]:
                reached_1r = (t["dir"] == 1 and high >= t["entry"] + t["r_dist"]) or \
                             (t["dir"] == -1 and low <= t["entry"] - t["r_dist"])
                if reached_1r:
                    # Lock in 50% gain and move SL to breakeven
                    partial_pnl = 0.5 * t["risk_usd"] * 1.0
                    balance += partial_pnl
                    t["partial_taken"] = True
                    t["sl"] = t["entry"]   # Move SL to Breakeven
                    t["locked_pnl"] += partial_pnl

            # Check Full TP or SL
            hit_tp = (t["dir"] ==  1 and high >= t["tp"]) or (t["dir"] == -1 and low  <= t["tp"])
            hit_sl = (t["dir"] ==  1 and low  <= t["sl"]) or (t["dir"] == -1 and high >= t["sl"])

            if hit_tp or hit_sl:
                remaining_weight = 0.5 if t["partial_taken"] else 1.0
                if hit_tp:
                    rem_pnl = remaining_weight * t["risk_usd"] * t["rrr"]
                    result_label = "WIN"
                else:
                    # If partial was taken, SL is at BE (0 pnl for remaining half)
                    rem_pnl = 0.0 if t["partial_taken"] else -remaining_weight * t["risk_usd"]
                    result_label = "BE" if t["partial_taken"] else "LOSS"

                trade_total_pnl = t["locked_pnl"] + rem_pnl
                balance += rem_pnl

                trades.append({
                    "entry_time": t["entry_time"],
                    "exit_time":  signals_df.index[i],
                    "dir":        "BUY" if t["dir"] == 1 else "SELL",
                    "entry":      t["entry"],
                    "exit":       t["tp"] if hit_tp else t["sl"],
                    "pnl":        round(trade_total_pnl, 2),
                    "result":     result_label,
                    "confidence": t["confidence"],
                    "ict_score":  t["ict_score"],
                    "bars_held":  i - t["entry_idx"],
                })
                open_trade = None

        # Check for new entry signal
        if open_trade is None:
            sig  = int(bar.get("Signal", 0))
            conf = float(bar.get("AI_Confidence", 0.0))
            ict  = float(bar.get("ict_score_norm", 0.0)) * 20
            atr  = float(bar.get("ATR", 0.0))

            if sig != 0 and conf >= MIN_CONFIDENCE and atr > 0:
                sl_dist = atr * SL_ATR_MULT
                # Scale RRR dynamically based on confidence & ICT score
                rrr = 10.0 if (conf >= 0.92 and ict >= 10) else (5.0 if (conf >= 0.87 and ict >= 8) else BASE_RRR)
                tp_dist = sl_dist * rrr
                risk_usd = balance * RISK_PCT

                open_trade = {
                    "dir":           sig,
                    "entry":         bar["Close"],
                    "entry_time":    signals_df.index[i],
                    "entry_idx":     i,
                    "sl":            bar["Close"] - sl_dist if sig == 1 else bar["Close"] + sl_dist,
                    "tp":            bar["Close"] + tp_dist if sig == 1 else bar["Close"] - tp_dist,
                    "r_dist":        sl_dist,
                    "risk_usd":      risk_usd,
                    "rrr":           rrr,
                    "confidence":    round(conf, 4),
                    "ict_score":     round(ict, 1),
                    "partial_taken": False,
                    "locked_pnl":    0.0,
                }

    return trades, balance


def run_walk_forward(base_pair: str = "EURUSD"):
    print("\n" + "=" * 65)
    print(f"  🚶 Institutional Walk-Forward Validation: {base_pair}")
    print(f"  Train: {TRAIN_DAYS}d  |  Test: {TEST_DAYS}d  |  Step: {STEP_DAYS}d  |  Total: {TOTAL_DAYS}d")
    print("=" * 65)

    if not connect_mt5(LOGIN, PASSWORD, SERVER):
        print("❌ Could not connect to MT5.")
        return None

    sym = _resolve_symbol(base_pair)
    if not sym:
        print(f"❌ Could not resolve symbol for {base_pair}")
        shutdown_mt5()
        return None

    print(f"\n📡 Fetching {TOTAL_DAYS} days of 5M bars for {sym}...")
    df_m5 = fetch_full_history(sym, TOTAL_DAYS)
    if df_m5 is None:
        print(f"❌ Insufficient historical bars for {sym}")
        shutdown_mt5()
        return None

    # Fetch correlated pair if available for SMT divergence
    df_corr_m5 = None
    corr_base = SMT_PAIRS.get(base_pair)
    if corr_base:
        corr_sym = _resolve_symbol(corr_base)
        if corr_sym:
            df_corr_m5 = fetch_full_history(corr_sym, TOTAL_DAYS)

    shutdown_mt5()

    print(f"✅ Fetched {len(df_m5):,} 5M bars ({df_m5.index[0].date()} → {df_m5.index[-1].date()})")
    print("⚙️  Computing indicators, multi-TF structure, and ICT patterns...")
    full_df = prepare_market_dataframe(df_m5, df_corr_m5)

    # ── Rolling Walk-Forward Folds ────────────────────────────────────────────
    start_date = full_df.index[0]
    end_date   = full_df.index[-1]
    
    current_train_start = start_date
    fold = 1
    all_oos_trades = []
    fold_summaries = []
    current_balance = INITIAL_BALANCE

    print(f"\n{'─'*65}")
    print(f"  {'Fold':<6} {'Train Window':<23} {'OOS Test Window':<23} {'Trades':<8} {'OOS PnL'}")
    print(f"{'─'*65}")

    while True:
        train_end = current_train_start + timedelta(days=TRAIN_DAYS)
        test_end  = train_end + timedelta(days=TEST_DAYS)

        if test_end > end_date + timedelta(days=2):
            break

        df_train_raw = full_df.loc[current_train_start:train_end]
        df_test_raw  = full_df.loc[train_end:test_end]

        if len(df_train_raw) < 1000 or len(df_test_raw) < 300:
            current_train_start += timedelta(days=STEP_DAYS)
            continue

        # Prepare in-sample training labels
        X_train, y_train = prepare_ml_data(df_train_raw)
        if X_train is None or len(X_train) < 30 or y_train.nunique() < 2:
            current_train_start += timedelta(days=STEP_DAYS)
            continue

        # Train models strictly on In-Sample data
        xgb_m, _ = train_model(X_train, y_train, n_estimators=150)
        rf_m, _  = train_rf_model(X_train, y_train, n_estimators=100)

        if xgb_m is None or rf_m is None:
            current_train_start += timedelta(days=STEP_DAYS)
            continue

        # Test strictly on unseen Out-of-Sample data
        trades, next_bal = simulate_oos_trades(df_test_raw, xgb_m, rf_m, current_balance)
        fold_pnl = next_bal - current_balance
        current_balance = next_bal

        for t in trades:
            t["fold"] = fold
            all_oos_trades.append(t)

        train_str = f"{current_train_start.strftime('%m/%d')} → {train_end.strftime('%m/%d')}"
        test_str  = f"{train_end.strftime('%m/%d')} → {test_end.strftime('%m/%d')}"
        pnl_str   = f"+${fold_pnl:,.2f}" if fold_pnl >= 0 else f"-${abs(fold_pnl):,.2f}"
        print(f"  #{fold:<5} {train_str:<23} {test_str:<23} {len(trades):<8} {pnl_str}")

        fold_summaries.append({
            "fold": fold,
            "train_start": current_train_start.date(),
            "train_end": train_end.date(),
            "test_start": train_end.date(),
            "test_end": test_end.date(),
            "trades": len(trades),
            "fold_pnl": round(fold_pnl, 2),
        })

        fold += 1
        current_train_start += timedelta(days=STEP_DAYS)

    print(f"{'─'*65}")

    # ── Aggregate Walk-Forward Results ────────────────────────────────────────
    if not all_oos_trades:
        print("\n⚠️ No out-of-sample trades triggered during Walk-Forward Validation.")
        return None

    df_trades = pd.DataFrame(all_oos_trades)
    wins = df_trades[df_trades["pnl"] > 0]
    losses = df_trades[df_trades["pnl"] < 0]
    total_trades = len(df_trades)
    win_rate = len(wins) / total_trades if total_trades else 0.0

    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    total_pnl = df_trades["pnl"].sum()
    roi_pct = (total_pnl / INITIAL_BALANCE) * 100

    # Max Drawdown calculation
    equity_curve = [INITIAL_BALANCE]
    running = INITIAL_BALANCE
    for pnl in df_trades["pnl"]:
        running += pnl
        equity_curve.append(running)
    
    peak = INITIAL_BALANCE
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd_usd = peak - eq
        dd_pct = (dd_usd / peak) * 100 if peak > 0 else 0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct

    print(f"\n{'═'*65}")
    print(f"  🏆 OUT-OF-SAMPLE WALK-FORWARD PERFORMANCE REPORT")
    print(f"{'═'*65}")
    print(f"  Total OOS Trades   : {total_trades}")
    print(f"  Win / Loss / BE    : {len(wins)}W / {len(losses)}L / {total_trades - len(wins) - len(losses)}BE")
    print(f"  OOS Win Rate       : {win_rate:.1%}")
    print(f"  OOS Profit Factor  : {profit_factor:.2f}")
    print(f"  Initial Balance    : ${INITIAL_BALANCE:,.2f}")
    print(f"  Final Balance      : ${current_balance:,.2f}")
    print(f"  Net OOS Profit     : ${total_pnl:+,.2f} ({roi_pct:+.1f}%)")
    print(f"  Max Out-of-Sample DD: -${max_dd_usd:,.2f} (-{max_dd_pct:.1f}%)")
    print(f"{'═'*65}")

    # Export trade records
    out_csv = f"logs/walk_forward_{base_pair}.csv"
    df_trades.to_csv(out_csv, index=False)
    print(f"\n📁 Detailed OOS trade log saved to: {out_csv}\n")
    return df_trades


if __name__ == "__main__":
    target_pairs = sys.argv[1:] if len(sys.argv) > 1 else ["EURUSD"]
    for p in target_pairs:
        run_walk_forward(p)
