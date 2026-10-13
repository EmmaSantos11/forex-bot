"""
train_macro_models.py — Trains 1H Macro Swing AI models (XGBoost + Random Forest)
for all active pairs using symmetric 3-class 1:3.5 RRR labeling.
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
import MetaTrader5 as mt5
from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier
import joblib

from mt5_connector import connect_mt5, shutdown_mt5
from ml_model import extract_macro_features, MACRO_FEATURE_COLS, MODEL_DIR

load_dotenv()

LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

ACTIVE_PAIRS = [
    "EURUSD", "USDCHF", "USDCAD", "NZDUSD", "EURJPY", "XAUUSD", "BTCUSD"
]

def _strip_suffix(s: str) -> str:
    for sfx in [".m", "m", ".c", "c", ".pro", "pro", ".ecn", "ecn"]:
        if s.lower().endswith(sfx):
            return s[: len(s) - len(sfx)].upper()
    return s.upper()

def resolve_broker_symbol(base: str) -> str:
    syms = mt5.symbols_get() or []
    for s in syms:
        if _strip_suffix(s.name) == base:
            return s.name
    return base

def label_symmetric_swings(df1: pd.DataFrame, sl_mult=1.5, tp_mult=5.25, horizon=40) -> list:
    """Labels 1H bars: 2=BUY (3.5R), 0=SELL (3.5R), 1=CHOP"""
    labels = []
    n = len(df1)
    for i in range(n - horizon):
        entry = df1["Close"].iloc[i]
        c_atr = df1["ATR"].iloc[i] if "ATR" in df1.columns else 0.0
        if pd.isna(c_atr) or c_atr <= 0:
            labels.append(1)
            continue
        sl = c_atr * sl_mult
        tp = c_atr * tp_mult
        fut = df1.iloc[i+1 : i+horizon+1]
        
        b_tp = fut[fut["High"] >= entry + tp]
        b_sl = fut[fut["Low"] <= entry - sl]
        b_won = not b_tp.empty and (b_sl.empty or b_tp.index[0] < b_sl.index[0])
        
        s_tp = fut[fut["Low"] <= entry - tp]
        s_sl = fut[fut["High"] >= entry + sl]
        s_won = not s_tp.empty and (s_sl.empty or s_tp.index[0] < s_sl.index[0])
        
        if b_won and not s_won:
            labels.append(2)  # BUY
        elif s_won and not b_won:
            labels.append(0)  # SELL
        else:
            labels.append(1)  # CHOP
            
    labels.extend([1] * horizon)
    return labels

def train_pair(base: str, broker_sym: str):
    print(f"\n🧠 [{base}] Training 1H Macro AI Models ({broker_sym})...")
    r1 = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_H1, 0, 3500)
    r4 = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_H4, 0, 1000)
    if r1 is None or r4 is None or len(r1) < 500 or len(r4) < 100:
        print(f"  ⚠️ Not enough data for {base}, skipping.")
        return

    df1 = pd.DataFrame(r1)
    df1["time"] = pd.to_datetime(df1["time"], unit="s")
    df1.set_index("time", inplace=True)
    df1.columns = ["Open", "High", "Low", "Close", "Volume"] + list(df1.columns[5:])

    df4 = pd.DataFrame(r4)
    df4["time"] = pd.to_datetime(df4["time"], unit="s")
    df4.set_index("time", inplace=True)
    df4.columns = ["Open", "High", "Low", "Close", "Volume"] + list(df4.columns[5:])

    # Add ATR for labeling
    tr1 = df1["High"] - df1["Low"]
    tr2 = (df1["High"] - df1["Close"].shift()).abs()
    tr3 = (df1["Low"] - df1["Close"].shift()).abs()
    df1["ATR"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()

    feats = extract_macro_features(df1, df4)
    labels = label_symmetric_swings(df1)

    X = feats.iloc[100:-40][MACRO_FEATURE_COLS]
    y = pd.Series(labels, index=feats.index).iloc[100:-40]

    if len(X) < 200 or len(np.unique(y)) < 2:
        print(f"  ⚠️ Insufficient feature variance for {base}.")
        return

    # Train XGBoost
    xgb = XGBClassifier(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.03,
        random_state=42,
        eval_metric="mlogloss",
        verbosity=0,
    )
    xgb.fit(X, y)

    # Train Random Forest
    rf = RandomForestClassifier(
        n_estimators=100,
        max_depth=4,
        random_state=42,
        n_jobs=1,
    )
    rf.fit(X, y)

    os.makedirs(MODEL_DIR, exist_ok=True)
    xgb_path = os.path.join(MODEL_DIR, f"macro_xgb_{base}.pkl")
    rf_path  = os.path.join(MODEL_DIR, f"macro_rf_{base}.pkl")
    joblib.dump(xgb, xgb_path)
    joblib.dump(rf, rf_path)

    n_buy = (y == 2).sum()
    n_sell = (y == 0).sum()
    n_chop = (y == 1).sum()
    print(f"  ✅ Saved: {xgb_path} & {rf_path}")
    print(f"     Trained on {len(X)} bars | Wins: {n_buy} BUYs, {n_sell} SELLs, {n_chop} CHOP")

def main():
    if not connect_mt5(LOGIN, PASSWORD, SERVER):
        print("❌ MT5 connection failed.")
        sys.exit(1)
    try:
        for base in ACTIVE_PAIRS:
            broker_sym = resolve_broker_symbol(base)
            train_pair(base, broker_sym)
    finally:
        shutdown_mt5()
    print("\n🎉 All 1H Macro AI models trained successfully!")

if __name__ == "__main__":
    main()
