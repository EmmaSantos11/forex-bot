"""
retrain_model.py — Full per-pair AI retraining.

Usage:
    python retrain_model.py              # retrains all active pairs
    python retrain_model.py EURUSD XAUUSD  # retrains specific pairs only

For each pair:
  1. Fetches 7 months of 5M data from MT5 (~60K bars per pair)
  2. Resamples to 1H and 4H for multi-timeframe ICT analysis
  3. Labels each bar using ATR-based dynamic TP/SL (matches live execution)
  4. Computes all 19 features (7 indicators + 12 ICT patterns) — same scale
     as live trading so training and inference are timeframe-consistent
  5. Trains pair-specific XGBoost + Random Forest models
  6. Also trains/refreshes the global fallback model

Runs automatically every Sunday from main.py.
"""

import gc
import os
import sys
import warnings
from datetime import datetime, timezone, timedelta

# Windows terminals default to cp1252 which can't print emoji — force UTF-8
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

from mt5_connector import connect_mt5, shutdown_mt5
from strategy import add_indicators, add_features, generate_signals_ict, add_ict_features, SMT_PAIRS
from ml_model import (
    prepare_ml_data, train_ensemble, train_pair_ensemble,
    add_session_features, FEATURE_COLS,
)
from core.multi_timeframe import resample_timeframes
from core.notifications import send_telegram_message

load_dotenv()
warnings.filterwarnings("ignore")

LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

LOOKBACK_DAYS = 120   # 4 months of 5M bars — enough signal, lower RAM footprint
M5_COUNT      = 25000 # hard cap — ~3 months of 5M; halves peak memory vs 65K

# Active pairs — must match main.py PREFERRED_PAIRS
ACTIVE_PAIRS = [
    "EURUSD", "USDCHF",
    "USDCAD", "NZDUSD", "EURJPY",
    "XAUUSD", "BTCUSD",
    # AUDUSD removed — backtest PF 1.05 too thin; matches main.py
]

os.makedirs("models", exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _fetch_m5(symbol: str) -> pd.DataFrame | None:
    """Fetches 5M bars for the past LOOKBACK_DAYS days.

    Using 5M (not H1) ensures training and live inference operate at the same
    timeframe — ICT lookback windows, BOS detection, and ATR values are all
    consistent between training and live signal generation.
    """
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, since, now)
    if rates is None or len(rates) < 2000:   # < ~1 week of 5M data — not worth training
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = df[["open", "high", "low", "close", "tick_volume"]]
    df.columns = ["Open", "High", "Low", "Close", "Volume"]
    df = df.iloc[-M5_COUNT:].astype("float32")   # cap + halve memory footprint
    return df


def _build_training_data(df_m5: pd.DataFrame,
                         df_corr_m5: pd.DataFrame | None = None) -> tuple:
    """
    Runs the full strategy pipeline on 5M data then creates ML labels.
    df_corr_m5 — optional 5M data for the correlated pair (SMT divergence).
    Returns (X, y) or (None, None) if not enough labelled data.
    """
    tfs = resample_timeframes(df_m5)
    for tf in tfs:
        tfs[tf] = add_indicators(tfs[tf])
        tfs[tf] = add_features(tfs[tf])
    df_final = generate_signals_ict(tfs)

    # Align correlated pair to the same 5M index
    df_corr_aligned = None
    if df_corr_m5 is not None and not df_corr_m5.empty:
        df_corr_proc = add_features(add_indicators(df_corr_m5.copy()))
        df_corr_aligned = df_corr_proc.reindex(df_final.index, method="ffill")

    # Enrich 5M bars with ICT binary features — same call as in get_signal()
    # so training features and live features are computed identically.
    df_final = add_ict_features(
        df_final, tfs.get("1h"), tfs.get("4h"), df_corr=df_corr_aligned
    )

    # Session/timing features — must match what get_signal() adds before inference
    df_final = add_session_features(df_final)

    available = [c for c in FEATURE_COLS if c in df_final.columns]
    if not available:
        return None, None

    return prepare_ml_data(df_final)


# ── Main ──────────────────────────────────────────────────────────────────────

def _count_live_trades() -> int:
    """Returns how many real closed trades are in the trade log."""
    try:
        path = "logs/trade_log.csv"
        if not os.path.exists(path):
            return 0
        df = pd.read_csv(path)
        return len(df)
    except Exception:
        return 0


def run_retrain(pairs: list[str] | None = None):
    live_trade_count = _count_live_trades()

    print("=" * 60)
    print("  🧠  Forex AI Bot — Full Retraining")
    if live_trade_count > 0:
        print(f"  Source: backtest data + {live_trade_count} live trades (3× weighted)")
    else:
        print(f"  Source: backtest data ({LOOKBACK_DAYS} days of 5M bars per pair)")
    print("=" * 60)

    print("\n🔗 Connecting to MT5...")
    if not connect_mt5(LOGIN, PASSWORD, SERVER):
        msg = "❌ Retraining failed — could not connect to MT5."
        print(msg)
        send_telegram_message(msg)
        return

    targets = pairs if pairs else ACTIVE_PAIRS

    # Resolve broker symbols
    resolved = []
    for base in targets:
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

    # ── Load manual live trades (blended in at 3× weight) ────────────────────
    MANUAL_PATH = "data/manual_trade_features.csv"
    X_manual, y_manual = None, None
    if os.path.exists(MANUAL_PATH):
        try:
            manual_df = pd.read_csv(MANUAL_PATH)
            available = [c for c in FEATURE_COLS if c in manual_df.columns]
            manual_df = manual_df[available + ["Target"]].dropna()
            manual_df = manual_df[manual_df["Target"].isin([1, -1])]
            if not manual_df.empty:
                from ml_model import _LABEL_MAP
                X_manual = pd.concat([manual_df[available]] * 3, ignore_index=True)
                y_manual = pd.concat([manual_df["Target"].map(_LABEL_MAP)] * 3, ignore_index=True)
                print(f"  📂 Manual trades loaded: {len(manual_df)} records × 3 weight = {len(X_manual)} samples")
        except Exception as e:
            print(f"  ⚠️  Could not load manual trades: {e}")

    all_X, all_y = [], []
    results      = []
    imp_rows     = []   # accumulates feature importances across all pairs

    for base, sym in resolved:
        print(f"\n{'─'*50}")
        print(f"  [{base}] Fetching 5M data ({LOOKBACK_DAYS} days)...")

        df_m5 = _fetch_m5(sym)
        if df_m5 is None:
            print(f"  ⚠️  Not enough 5M history for {sym} — skipping.")
            results.append({"pair": base, "status": "skipped (no data)"})
            continue

        # Fetch correlated pair for SMT divergence feature
        df_corr_m5 = None
        corr_base  = SMT_PAIRS.get(base)
        if corr_base:
            corr_sym = _resolve_symbol(corr_base)
            if corr_sym:
                df_corr_m5 = _fetch_m5(corr_sym)
                if df_corr_m5 is not None:
                    print(f"  [{base}] SMT pair: {corr_base} ({len(df_corr_m5):,} bars)")
                else:
                    print(f"  [{base}] SMT pair {corr_base} not available — skipping SMT feature")

        print(f"  [{base}] {len(df_m5):,} bars | Building features...")
        X, y = _build_training_data(df_m5, df_corr_m5=df_corr_m5)

        if X is None or len(X) < 50:
            print(f"  ⚠️  [{base}] Not enough labelled samples — skipping.")
            results.append({"pair": base, "status": "skipped (insufficient labels)"})
            del df_m5, df_corr_m5
            gc.collect()
            continue

        n_wins   = int((y == 1).sum())
        n_losses = int((y == 0).sum())

        # Blend in live trade data (3× weight so real outcomes outweigh simulated labels)
        if X_manual is not None:
            X_train = pd.concat([X, X_manual], ignore_index=True)
            y_train = pd.concat([y, y_manual], ignore_index=True)
            print(f"  [{base}] Samples: {len(X)} historical + {len(X_manual)} live = {len(X_train)} total ({n_wins} hist wins, {n_losses} hist losses)")
        else:
            X_train, y_train = X, y
            print(f"  [{base}] Samples: {len(X)} ({n_wins} wins, {n_losses} losses)")

        # Train pair-specific XGBoost + RF
        xgb, rf, xgb_recall, rf_recall = train_pair_ensemble(base, X_train, y_train)

        # Collect feature importances for CSV export
        for model_obj, model_name in [(xgb, "xgb"), (rf, "rf")]:
            if model_obj and hasattr(model_obj, "feature_importances_"):
                for feat, imp in zip(X_train.columns, model_obj.feature_importances_):
                    imp_rows.append({
                        "pair": base, "model": model_name,
                        "feature": feat, "importance": round(float(imp), 5),
                    })

        status = []
        if xgb: status.append("XGB ✅")
        if rf:  status.append("RF ✅")
        if not status: status.append("⚠️ no model saved")

        results.append({
            "pair":        base,
            "samples":     len(X_train),
            "wins":        n_wins,
            "losses":      n_losses,
            "xgb_recall":  xgb_recall,
            "rf_recall":   rf_recall,
            "status":      " | ".join(status),
        })

        # Accumulate for global fallback model
        all_X.append(X_train)
        all_y.append(y_train)

        # Release intermediate DataFrames before next pair
        del df_m5, df_corr_m5, X, y
        gc.collect()

    # ── Global fallback model ─────────────────────────────────────────────────
    if all_X:
        print(f"\n{'─'*50}")
        print("  🌐 Training global fallback model (all pairs combined)...")
        X_global = pd.concat(all_X, ignore_index=True)
        y_global = pd.concat(all_y, ignore_index=True)
        print(f"  Combined samples: {len(X_global):,}")
        train_ensemble(X_global, y_global)

    shutdown_mt5()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  RETRAINING COMPLETE")
    print(f"{'═'*60}")
    trained = [r for r in results if "XGB" in r.get("status", "") or "RF" in r.get("status", "")]
    skipped = [r for r in results if r not in trained]

    for r in results:
        samp = f"  {r['samples']} samples" if "samples" in r else ""
        xgb_r = f"  XGB recall: {r['xgb_recall']:.1%}" if r.get("xgb_recall") else ""
        rf_r  = f"  RF recall: {r['rf_recall']:.1%}"   if r.get("rf_recall")  else ""
        print(f"  {r['pair']:<10} {r['status']}{samp}{xgb_r}{rf_r}")

    # ── Write recall scores — latest snapshot + cumulative history ───────────
    os.makedirs("logs", exist_ok=True)
    trained_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    recall_rows = [
        {
            "pair":        r["pair"],
            "xgb_recall":  round(r.get("xgb_recall", 0.0), 4),
            "rf_recall":   round(r.get("rf_recall",  0.0), 4),
            "samples":     r.get("samples", 0),
            "trained_at":  trained_at,
        }
        for r in trained
    ]
    if recall_rows:
        df_recall = pd.DataFrame(recall_rows)

        # Overwrite latest snapshot (used by dashboard model status table)
        df_recall.to_csv("logs/model_recall.csv", index=False)

        # Append to cumulative history (used by learning-trend chart)
        hist_path  = "logs/model_recall_history.csv"
        hist_exists = os.path.exists(hist_path)
        retrain_n  = 1
        if hist_exists:
            try:
                df_hist   = pd.read_csv(hist_path)
                retrain_n = int(df_hist["retrain_n"].max()) + 1 if not df_hist.empty else 1
            except Exception:
                retrain_n = 1
        df_recall["retrain_n"] = retrain_n
        df_recall.to_csv(hist_path, mode="a", header=not hist_exists, index=False)
        print(f"\n  📊 Retrain #{retrain_n} saved → logs/model_recall_history.csv")

    # ── Feature importance export ─────────────────────────────────────────────
    if imp_rows:
        trained_at_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        df_imp = pd.DataFrame(imp_rows)
        df_imp["trained_at"] = trained_at_str
        df_imp.to_csv("logs/feature_importance.csv", index=False)
        print(f"  📊 Feature importances saved → logs/feature_importance.csv")

    # ── Telegram notification ─────────────────────────────────────────────────
    src_label = (
        f"backtest data + {live_trade_count} live trades"
        if live_trade_count > 0
        else "backtest data only"
    )
    lines = [f"<b>🧠 AI Retraining Complete</b>\n<i>Trained on: {src_label}</i>\n"]
    for r in trained:
        xgb_r = f"{r['xgb_recall']:.0%}" if r.get("xgb_recall") else "—"
        rf_r  = f"{r['rf_recall']:.0%}"  if r.get("rf_recall")  else "—"
        lines.append(
            f"  ✅ {r['pair']}: {r['samples']} samples "
            f"({r['wins']}W/{r['losses']}L) | XGB: {xgb_r} | RF: {rf_r}"
        )
    if skipped:
        lines.append("")
        for r in skipped:
            lines.append(f"  ⏭️  {r['pair']}: {r['status']}")
    if all_X:
        total = sum(r.get("samples", 0) for r in trained)
        lines.append(f"\n🌐 Global fallback: {total:,} combined samples")
    lines.append("\nBot models updated — ready for next market open. 🚀")
    send_telegram_message("\n".join(lines))
    print()


if __name__ == "__main__":
    pairs_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    run_retrain(pairs_arg)
