"""
import_manual_trades.py

Pulls your closed trade history from the MT5 demo account, reconstructs
the market indicator values at each entry point, and saves them as
labeled training data for the AI model.

Your human decisions + actual profit/loss outcomes = high-quality labels.

Run this once (or any time you've taken more manual trades):
    python import_manual_trades.py
"""

import os
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timezone
from dotenv import load_dotenv

from mt5_connector import connect_mt5, shutdown_mt5
from strategy import add_indicators, add_features

load_dotenv()

LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

OUTPUT_PATH = "data/manual_trade_features.csv"


# ──────────────────────────────────────────────────────────────
# FEATURE EXTRACTION
# ──────────────────────────────────────────────────────────────

def _features_at_entry(symbol, entry_time, lookback=100):
    """
    Fetches `lookback` 5-min bars ending at entry_time, calculates
    indicators, and returns the feature values from the entry bar.
    """
    rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M5, entry_time, lookback)
    if rates is None or len(rates) == 0:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = df[["open", "high", "low", "close", "tick_volume"]]
    df.columns = ["Open", "High", "Low", "Close", "Volume"]

    df = add_indicators(df)
    df = add_features(df)

    row = df.iloc[-1]

    # All 7 features the ML model uses (must match ml_model.py FEATURE_COLS)
    return {
        "trend_strength": row.get("trend_strength"),
        "momentum":       row.get("momentum"),
        "volatility":     row.get("volatility"),
        "RSI":            row.get("RSI"),
        "MACD_hist":      row.get("MACD_hist"),
        "BB_width":       row.get("BB_width"),
        "BB_position":    row.get("BB_position"),
    }


# ──────────────────────────────────────────────────────────────
# MAIN IMPORT FUNCTION
# ──────────────────────────────────────────────────────────────

def import_trades_from_mt5(from_date=None, to_date=None):
    """
    Fetches all closed trades from MT5, extracts market features at
    each entry, labels them (win=1, loss=-1), and saves to CSV.

    Parameters
    ----------
    from_date : datetime (UTC) — start of history window (default: Jan 2024)
    to_date   : datetime (UTC) — end of history window (default: now)
    """
    if from_date is None:
        from_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    if to_date is None:
        to_date = datetime.now(timezone.utc)

    print(f"📅 Fetching trade history: {from_date.date()} → {to_date.date()}")

    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None or len(deals) == 0:
        print("⚠️  No trade history found in this date range.")
        return None

    deals_df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())

    # Exit deals (DEAL_ENTRY_OUT = 1) carry the final profit/loss
    exit_deals = deals_df[deals_df["entry"] == mt5.DEAL_ENTRY_OUT].copy()
    if exit_deals.empty:
        print("⚠️  No closed trades found.")
        return None

    print(f"📊 Found {len(exit_deals)} closed trades. Extracting features...\n")

    rows = []

    for _, exit_deal in exit_deals.iterrows():
        symbol      = exit_deal["symbol"]
        profit      = exit_deal["profit"]
        position_id = exit_deal["position_id"]

        # Skip non-trade entries (deposits, dividends, etc.)
        if not symbol:
            continue

        # Find the matching entry deal
        entry_candidates = deals_df[
            (deals_df["position_id"] == position_id) &
            (deals_df["entry"] == mt5.DEAL_ENTRY_IN)
        ]
        if entry_candidates.empty:
            continue

        entry_deal = entry_candidates.iloc[0]
        entry_time = datetime.fromtimestamp(entry_deal["time"], tz=timezone.utc)
        trade_type = "BUY" if entry_deal["type"] == mt5.DEAL_TYPE_BUY else "SELL"

        # Label: profitable = 1, losing = -1, skip breakeven
        if profit > 0:
            label = 1
        elif profit < 0:
            label = -1
        else:
            continue

        # Reconstruct market state at entry
        features = _features_at_entry(symbol, entry_time)
        if features is None:
            print(f"  ⚠️  No bar data for {symbol} at {entry_time} — skipping")
            continue

        rows.append({
            **features,
            "Target":     label,
            "symbol":     symbol,
            "trade_type": trade_type,
            "profit":     round(profit, 2),
            "entry_time": entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "source":     "manual",
        })

        result_icon = "✅" if label == 1 else "❌"
        print(f"  {result_icon} {symbol} {trade_type} | Profit: ${profit:.2f} | Label: {label}")

    if not rows:
        print("\n❌ No usable trades with market data found.")
        return None

    df_out = pd.DataFrame(rows)

    # If a previous file exists, append without duplicating
    if os.path.exists(OUTPUT_PATH):
        existing = pd.read_csv(OUTPUT_PATH)
        combined = pd.concat([existing, df_out])
        combined = combined.drop_duplicates(subset=["entry_time", "symbol", "trade_type"])
        combined.to_csv(OUTPUT_PATH, index=False)
        new_count = len(combined) - len(existing)
        print(f"\n✅ Added {new_count} new records (total: {len(combined)}) → {OUTPUT_PATH}")
        df_out = combined
    else:
        os.makedirs("data", exist_ok=True)
        df_out.to_csv(OUTPUT_PATH, index=False)
        print(f"\n✅ Saved {len(rows)} records → {OUTPUT_PATH}")

    wins      = (df_out["Target"] == 1).sum()
    losses    = (df_out["Target"] == -1).sum()
    win_rate  = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    print(f"📈 Wins: {wins} | Losses: {losses} | Win Rate: {win_rate:.1f}%")

    return df_out


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔗 Connecting to MT5...")
    if not connect_mt5(LOGIN, PASSWORD, SERVER):
        print("❌ Connection failed. Is MT5 running?")
    else:
        import_trades_from_mt5()
        shutdown_mt5()
        print("\nDone. Run `python retrain_model.py` to train the model on this data.")
