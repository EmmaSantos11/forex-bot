"""
ml_model.py — Ensemble ML: XGBoost + Random Forest, with per-pair models.

Model hierarchy (best → fallback):
  1. Pair-specific ensemble  (models/xgb_EURUSD.pkl + models/rf_EURUSD.pkl)
  2. Pair-specific XGBoost   (one model missing)
  3. Global ensemble         (models/xgb_trading_model.pkl + rf_trading_model.pkl)
  4. Rule-based ICT          (no models at all — caller handles)

A signal only fires when both XGBoost AND RF agree AND both exceed MIN_CONFIDENCE.
"""

import os
import numpy as np
import joblib
from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

MODEL_DIR = "models"

# ── Global (fallback) model paths ─────────────────────────────────────────────
XGB_MODEL_PATH = os.path.join(MODEL_DIR, "xgb_trading_model.pkl")
RF_MODEL_PATH  = os.path.join(MODEL_DIR, "rf_trading_model.pkl")

FEATURE_COLS = [
    # ── Standard indicators ───────────────────────────────────────────────────
    "trend_strength",
    "momentum",
    "volatility",
    "RSI",
    "MACD_hist",
    "BB_width",
    "BB_position",
    # ── ICT/SMC pattern features ──────────────────────────────────────────────
    # The AI can now learn WHICH ICT combinations predict wins, not just
    # whether the total score passes a threshold.
    "ict_structure_bull",   # 4H bullish structure confirmed
    "ict_bos",              # Break of Structure in structure direction
    "ict_liq_sweep",        # Liquidity sweep (stop hunt before BOS)
    "ict_ob",               # Order Block / POI retest
    "ict_fvg",              # Fair Value Gap retest
    "ict_choch",            # Change of Character (early reversal)
    "ict_displacement",     # 3+ institutional delivery candles
    "ict_cisd",             # Change in State of Delivery (dominant candle)
    "ict_kill_zone",        # London / NY / Asian kill zone timing
    "ict_ote",              # Fibonacci OTE 0.50–0.786 on 4H swing
    "ict_golden_zone",      # Golden Zone 0.618 ± 5% precision entry
    "ict_score_norm",       # Normalized confluence score 0–1
    "ict_smt",              # SMT divergence vs correlated pair
    # ── Session / timing features ──────────────────────────────────────────────
    "hour_sin",             # cyclic hour encoding — smoothly captures time-of-day
    "hour_cos",             # paired with hour_sin for complete cycle representation
    "is_london_kz",         # 1 inside London kill zone  (07:00–12:00 UTC)
    "is_ny_kz",             # 1 inside New York kill zone (13:00–18:00 UTC)
    "is_asian_kz",          # 1 inside Asian kill zone   (01:00–03:00 UTC)
    "day_of_week",          # 0=Mon … 4=Fri — Friday behavior differs from Mon
]

_LABEL_MAP   = {-1: 0, 1: 1}
_LABEL_UNMAP = {0: -1, 1: 1}


def add_session_features(df):
    """Adds 6 session/timing columns derived from the DataFrame's datetime index.
    Must be called before prepare_ml_data (training) AND before ensemble
    inference (live) so training and inference features are always identical."""
    df = df.copy()
    hours              = df.index.hour
    df["hour_sin"]     = np.sin(2 * np.pi * hours / 24)
    df["hour_cos"]     = np.cos(2 * np.pi * hours / 24)
    df["is_london_kz"] = ((hours >= 7)  & (hours < 12)).astype("int8")
    df["is_ny_kz"]     = ((hours >= 13) & (hours < 18)).astype("int8")
    df["is_asian_kz"]  = ((hours >= 1)  & (hours < 3)).astype("int8")
    df["day_of_week"]  = df.index.dayofweek.astype("int8")
    return df

MIN_CONFIDENCE = 0.60  # minimum per-model confidence to even register a signal


# ══════════════════════════════════════════════════════════════════════════════
# PER-PAIR MODEL PATHS
# ══════════════════════════════════════════════════════════════════════════════

def _pair_model_paths(base_name: str) -> tuple:
    """Returns (xgb_path, rf_path) for a given base pair name (e.g. 'EURUSD')."""
    clean = base_name.upper().replace("/", "")
    return (
        os.path.join(MODEL_DIR, f"xgb_{clean}.pkl"),
        os.path.join(MODEL_DIR, f"rf_{clean}.pkl"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# SAVE / LOAD
# ══════════════════════════════════════════════════════════════════════════════

def save_model(model, path=XGB_MODEL_PATH):
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(model, path)
    print(f"  ✅ Saved → {path}")


def load_model(path=XGB_MODEL_PATH):
    if os.path.exists(path):
        m = joblib.load(path)
        print(f"  ✅ Loaded ← {path}")
        return m
    print(f"  ⚠️  No model at {path}")
    return None


def load_rf_model():
    return load_model(RF_MODEL_PATH)


def load_pair_models(base_name: str) -> tuple:
    """
    Loads the best available models for this pair.
    Priority: pair-specific → global → None.
    Returns (xgb_model, rf_model). Either may be None.
    """
    xgb_path, rf_path = _pair_model_paths(base_name)

    xgb = (
        load_model(xgb_path) if os.path.exists(xgb_path)
        else load_model(XGB_MODEL_PATH)
    )
    rf = (
        load_model(rf_path) if os.path.exists(rf_path)
        else load_model(RF_MODEL_PATH)
    )
    return xgb, rf


# ══════════════════════════════════════════════════════════════════════════════
# LABEL GENERATION — ATR-based dynamic TP/SL (matches live trading exactly)
# ══════════════════════════════════════════════════════════════════════════════

def create_trade_labels(df, sl_atr_mult=1.5, rrr=3, lookforward=100):
    """
    Labels each bar: +1 if TP hit first, -1 if SL hit first, 0 if neither.

    Uses ATR-based SL/TP (same formula as live trading) so the ML model
    learns from outcomes that match real execution, not fixed-pip theory.

    lookforward: how many 5M bars ahead to check (~5 hours at 60 bars).
    """
    df = df.copy()
    df["Target"] = 0

    for i in range(len(df) - lookforward):
        entry = df["Close"].iloc[i]
        atr   = df["ATR"].iloc[i] if "ATR" in df.columns else 0

        if atr == 0 or entry == 0:
            continue

        sl_dist = atr * sl_atr_mult
        tp_dist = sl_dist * rrr
        future  = df.iloc[i + 1: i + lookforward]

        tp_bars = future[future["High"] >= entry + tp_dist]
        sl_bars = future[future["Low"]  <= entry - sl_dist]

        tp_hit = not tp_bars.empty
        sl_hit = not sl_bars.empty

        if tp_hit and sl_hit:
            # Both levels were reached — whichever came first determines the outcome
            label = 1 if tp_bars.index[0] <= sl_bars.index[0] else -1
        elif tp_hit:
            label = 1
        elif sl_hit:
            label = -1
        else:
            label = 0

        df.at[df.index[i], "Target"] = label

    return df


# ══════════════════════════════════════════════════════════════════════════════
# DATA PREPARATION
# ══════════════════════════════════════════════════════════════════════════════

def prepare_ml_data(df, sl_atr_mult=1.5, rrr=3):
    """
    Runs label generation then returns (X, y) ready for training.
    Drops bars with no clear outcome (label == 0).
    """
    df = create_trade_labels(df, sl_atr_mult=sl_atr_mult, rrr=rrr)

    available = [c for c in FEATURE_COLS if c in df.columns]
    df = df[available + ["Target"]].dropna()
    df = df[df["Target"] != 0]

    if len(df) < 50:
        print("  ⚠️  Not enough labelled data for ML training.")
        return None, None

    X = df[available]
    y = df["Target"].map(_LABEL_MAP)
    return X, y


# ══════════════════════════════════════════════════════════════════════════════
# TRAINING
# ══════════════════════════════════════════════════════════════════════════════

def train_model(X, y, n_estimators: int = 400) -> tuple:
    """
    Trains XGBoost. Returns (model, xgb_recall_wins).
    recall_wins = fraction of actual winning bars the model correctly called as wins.
    """
    if X is None or len(X) == 0:
        print("  ❌ No data to train on.")
        return None, 0.0

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, shuffle=False)
    if len(X_tr) == 0 or len(X_te) == 0:
        return None, 0.0
    if y_tr.nunique() < 2:
        print("  ⏭️  XGBoost skipped — training split has only one class (need more signal variety)")
        return None, 0.0

    n_neg = int((y_tr == 0).sum())
    n_pos = int((y_tr == 1).sum())
    # 1.5× boost over the raw ratio — pushes recall of winning setups higher
    # without completely ignoring losses. Tuned for 35–55% baseline win recall.
    spw   = (n_neg / n_pos * 1.5) if n_pos > 0 else 1.0

    xgb = XGBClassifier(
        n_estimators=n_estimators, # configurable trees (default 400)
        max_depth=4,             # shallower → less overfitting on noisy financial data
        learning_rate=0.04,      # slower learning → generalises better
        subsample=0.75,          # row sampling per tree
        colsample_bytree=0.75,   # feature sampling per tree
        min_child_weight=3,      # prevents splits on tiny minority samples
        gamma=0.1,               # minimum loss reduction to split — filters noise
        reg_alpha=0.05,          # L1 regularisation — forces sparse feature use
        reg_lambda=1.5,          # L2 regularisation — shrinks large weights
        scale_pos_weight=spw,
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )
    xgb.fit(X_tr, y_tr)
    acc          = xgb.score(X_te, y_te)
    n_pos_te     = int((y_te == 1).sum())
    recall_wins  = float((xgb.predict(X_te)[y_te == 1] == 1).mean()) if n_pos_te > 0 else 0.0
    print(f"    📊 XGBoost acc: {acc:.2%}  |  win recall: {recall_wins:.2%}  |  spw: {spw:.1f}x")

    # Feature importance — log top and weakest contributors
    if hasattr(xgb, "feature_importances_") and hasattr(X_tr, "columns"):
        imps = sorted(zip(X_tr.columns, xgb.feature_importances_), key=lambda x: x[1], reverse=True)
        top  = ", ".join(f"{k}({v:.2f})" for k, v in imps[:3])
        weak = ", ".join(f"{k}({v:.2f})" for k, v in imps[-3:])
        print(f"    ⭐ Top:    {top}")
        print(f"    💤 Weakest: {weak}")

    return xgb, recall_wins


def train_rf_model(X, y, n_estimators: int = 300) -> tuple:
    """
    Trains Random Forest. Returns (model, rf_recall_wins).
    """
    if X is None or len(X) == 0:
        return None, 0.0

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, shuffle=False)
    if len(X_tr) == 0 or len(X_te) == 0:
        return None, 0.0
    if y_tr.nunique() < 2:
        print("  ⏭️  Random Forest skipped — training split has only one class (need more signal variety)")
        return None, 0.0

    rf = RandomForestClassifier(
        n_estimators=n_estimators, # configurable trees (default 300)
        max_depth=5,             # reduced from 6 — less overfitting
        min_samples_leaf=4,      # slightly relaxed to catch more win patterns
        max_features="sqrt",     # standard — prevents feature dominance
        class_weight="balanced",
        random_state=42,
        n_jobs=1,                # single-threaded — Python 3.14 threadpool crash fix
    )
    rf.fit(X_tr, y_tr)
    acc         = rf.score(X_te, y_te)
    n_pos       = int((y_te == 1).sum())
    recall_wins = float((rf.predict(X_te)[y_te == 1] == 1).mean()) if n_pos > 0 else 0.0
    print(f"    📊 Random Forest acc: {acc:.2%}  |  win recall: {recall_wins:.2%}")

    if hasattr(rf, "feature_importances_") and hasattr(X_tr, "columns"):
        imps = sorted(zip(X_tr.columns, rf.feature_importances_), key=lambda x: x[1], reverse=True)
        top  = ", ".join(f"{k}({v:.2f})" for k, v in imps[:3])
        weak = ", ".join(f"{k}({v:.2f})" for k, v in imps[-3:])
        print(f"    ⭐ Top:    {top}")
        print(f"    💤 Weakest: {weak}")

    return rf, recall_wins


def train_ensemble(X, y) -> tuple:
    """Trains both models and saves to global paths. Returns (xgb, rf)."""
    print("  🤖 Training XGBoost (global)...")
    xgb, _ = train_model(X, y)
    if xgb:
        save_model(xgb, XGB_MODEL_PATH)

    print("  🌲 Training Random Forest (global)...")
    rf, _ = train_rf_model(X, y)
    if rf:
        save_model(rf, RF_MODEL_PATH)

    return xgb, rf


def train_pair_ensemble(base_name: str, X, y) -> tuple:
    """
    Trains XGBoost + RF for one pair, saves to pair-specific files.
    Returns (xgb, rf, xgb_recall, rf_recall).
    """
    xgb_path, rf_path = _pair_model_paths(base_name)

    print(f"  🤖 [{base_name}] Training XGBoost...")
    xgb, xgb_recall = train_model(X, y)
    if xgb:
        save_model(xgb, xgb_path)

    print(f"  🌲 [{base_name}] Training Random Forest...")
    rf, rf_recall = train_rf_model(X, y)
    if rf:
        save_model(rf, rf_path)

    return xgb, rf, xgb_recall, rf_recall


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def _predict_one(model, X_valid) -> tuple:
    """Returns (signals list, confidences list) for a single model."""
    # Align X_valid to the exact feature set the model was trained on.
    # Handles version skew between old (7-feature) and new (20-feature) models:
    # missing columns are filled with 0, extra columns are dropped.
    if hasattr(model, "feature_names_in_"):
        expected = list(model.feature_names_in_)
        X_valid = X_valid.copy()
        for col in expected:
            if col not in X_valid.columns:
                X_valid[col] = 0.0
        X_valid = X_valid[expected]

    # Force single-threaded prediction — Python 3.14 threadpool bug causes
    # RuntimeError / AttributeError when joblib tries to spin up a ThreadPool.
    if hasattr(model, "n_jobs"):
        model.n_jobs = 1
    probs = model.predict_proba(X_valid)
    sigs, confs = [], []
    for p in probs:
        conf      = float(max(p))
        class_idx = int(p.argmax())
        confs.append(conf)
        sigs.append(_LABEL_UNMAP[class_idx] if conf >= MIN_CONFIDENCE else 0)
    return sigs, confs


def generate_ai_signals(df, model):
    """Single-model XGBoost signal generation. Used when RF is not available."""
    if model is None:
        return df
    df = df.copy()
    df["Signal"]        = 0
    df["AI_Confidence"] = 0.0

    X_valid = df[[c for c in FEATURE_COLS if c in df.columns]].dropna()
    if X_valid.empty:
        return df

    sigs, confs = _predict_one(model, X_valid)
    df.loc[X_valid.index, "Signal"]        = sigs
    df.loc[X_valid.index, "AI_Confidence"] = confs
    return df


def generate_ensemble_signals(df, xgb_model, rf_model):
    """
    Ensemble: signal only fires when XGBoost AND RF agree on the same direction
    AND both exceed MIN_CONFIDENCE. Confidence = average of the two.
    """
    df = df.copy()
    df["Signal"]        = 0
    df["AI_Confidence"] = 0.0

    X_valid = df[[c for c in FEATURE_COLS if c in df.columns]].dropna()
    if X_valid.empty:
        return df

    xgb_sigs, xgb_confs = _predict_one(xgb_model, X_valid)
    rf_sigs,  rf_confs  = _predict_one(rf_model,   X_valid)

    final_sigs, final_confs = [], []
    for xs, xc, rs, rc in zip(xgb_sigs, xgb_confs, rf_sigs, rf_confs):
        if xs != 0 and xs == rs:
            final_sigs.append(xs)
            final_confs.append((xc + rc) / 2.0)
        else:
            final_sigs.append(0)
            final_confs.append(0.0)

    df.loc[X_valid.index, "Signal"]        = final_sigs
    df.loc[X_valid.index, "AI_Confidence"] = final_confs
    return df
