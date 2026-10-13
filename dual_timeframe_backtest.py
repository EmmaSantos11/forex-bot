"""
dual_timeframe_backtest.py — Dual-Horizon (HTF Swing + LTF Scalp) Institutional Backtest Engine.

Compares 4 modes across historical MT5 data:
  1. Baseline: Current standalone 5M ICT Strategy
  2. Swing Only: 1H/4H Macro Swing Trades (1:3.5 RRR, 1.5x 1H ATR SL, 0.5% risk)
  3. Scalp Only: 5M Trend-Aligned Scalps (1:1.5 RRR, 1.2x 5M ATR SL, 0.25% risk, HTF Directional Lock)
  4. Dual-Horizon: Simultaneous Swing + Scalp running on a single portfolio

Usage:
    python dual_timeframe_backtest.py
    python dual_timeframe_backtest.py EURUSD GBPUSD
    python dual_timeframe_backtest.py EURUSD USDCHF XAUUSD
"""

import os
import sys
import warnings
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore")

# ── Backtest Settings ──────────────────────────────────────────────────────────
INITIAL_BALANCE   = 5000.0
SWING_RISK_PCT    = 0.005   # 0.5% risk per swing trade
SCALP_RISK_PCT    = 0.0025  # 0.25% risk per scalp trade
BASELINE_RISK_PCT = 0.005   # 0.5% risk for baseline 5M

MAX_SWING_TRADES  = 1
MAX_SCALP_TRADES  = 2
MAX_TOTAL_TRADES  = 3

SWING_RRR         = 3.5
SCALP_RRR         = 1.5

DEFAULT_PAIRS     = ["EURUSD", "GBPUSD", "USDCHF"]
LOOKBACK_BARS_5M  = 15000   # ~100 trading days of 5M bars

# Spread assumptions in points (1 point = 0.00001 for 5-digit forex, 0.01 for gold)
SPREAD_POINTS = {
    "EURUSD": 12,   # 1.2 pips
    "GBPUSD": 18,   # 1.8 pips
    "USDCHF": 15,   # 1.5 pips
    "USDCAD": 16,   # 1.6 pips
    "EURJPY": 18,   # 1.8 pips
    "NZDUSD": 18,   # 1.8 pips
    "XAUUSD": 35,   # $0.35 on Gold
    "BTCUSD": 3000, # $30 on BTC
}

os.makedirs("logs", exist_ok=True)


# ── Suffix Helpers ────────────────────────────────────────────────────────────

def _strip_suffix(symbol: str) -> str:
    s = symbol.upper()
    for sfx in [".M", "M", ".C", "C", ".PRO", "PRO", ".ECN", "ECN", ".R", "R"]:
        if s.endswith(sfx) and len(s) > len(sfx):
            return s[: -len(sfx)]
    return s


def _resolve_symbol(base: str) -> Optional[str]:
    all_syms = mt5.symbols_get()
    if not all_syms:
        return None
    for s in all_syms:
        if _strip_suffix(s.name) == base.upper():
            return s.name
    return None


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_data(symbol: str, count_5m: int = LOOKBACK_BARS_5M) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Fetches synchronized 5M, 1H, and 4H bars from MT5."""
    resolved = _resolve_symbol(symbol) or symbol
    
    rates_5m = mt5.copy_rates_from_pos(resolved, mt5.TIMEFRAME_M5, 0, count_5m)
    if rates_5m is None or len(rates_5m) < 500:
        return None, None, None
    
    df_5m = pd.DataFrame(rates_5m)
    df_5m["time"] = pd.to_datetime(df_5m["time"], unit="s")
    df_5m.set_index("time", inplace=True)
    df_5m = df_5m[["open", "high", "low", "close", "tick_volume"]]
    df_5m.columns = ["Open", "High", "Low", "Close", "Volume"]

    start_time = df_5m.index[0]
    end_time   = df_5m.index[-1]

    # Fetch 1H and 4H covering the same window plus warm-up period
    warmup_start = start_time - timedelta(days=60)
    rates_1h = mt5.copy_rates_range(resolved, mt5.TIMEFRAME_H1, warmup_start, end_time)
    rates_4h = mt5.copy_rates_range(resolved, mt5.TIMEFRAME_H4, warmup_start, end_time)

    if rates_1h is None or rates_4h is None:
        return None, None, None

    df_1h = pd.DataFrame(rates_1h)
    df_1h["time"] = pd.to_datetime(df_1h["time"], unit="s")
    df_1h.set_index("time", inplace=True)
    df_1h = df_1h[["open", "high", "low", "close", "tick_volume"]]
    df_1h.columns = ["Open", "High", "Low", "Close", "Volume"]

    df_4h = pd.DataFrame(rates_4h)
    df_4h["time"] = pd.to_datetime(df_4h["time"], unit="s")
    df_4h.set_index("time", inplace=True)
    df_4h = df_4h[["open", "high", "low", "close", "tick_volume"]]
    df_4h.columns = ["Open", "High", "Low", "Close", "Volume"]

    return df_5m, df_1h, df_4h


# ── Feature & Indicator Calculations ──────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr = tr.rolling(period, min_periods=1).mean()

    h_diff = df["High"].diff()
    l_diff = df["Low"].diff()
    plus_dm  = h_diff.where((h_diff > 0) & (h_diff > -l_diff), 0.0)
    minus_dm = (-l_diff).where((-l_diff > 0) & (-l_diff > h_diff), 0.0)

    p_di = 100 * plus_dm.rolling(period, min_periods=1).mean() / atr.replace(0, 1e-10)
    m_di = 100 * minus_dm.rolling(period, min_periods=1).mean() / atr.replace(0, 1e-10)
    di_sum = (p_di + m_di).replace(0, 1e-10)
    dx = 100 * (p_di - m_di).abs() / di_sum
    return dx.rolling(period, min_periods=1).mean()


def build_htf_signals(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> pd.DataFrame:
    """
    Computes HTF bias and Swing trade signals on 1H bars with 4H regime confluence.
    Outputs:
      • htf_bias: +1 (Bullish), -1 (Bearish), 0 (Neutral/Ranging)
      • swing_signal: +1 (Buy), -1 (Sell), 0 (None)
      • htf_atr: 1H ATR for swing stop loss sizing
    """
    df = df_1h.copy()
    df["atr"] = calculate_atr(df, 14)
    df["ema_20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["Close"].ewm(span=200, adjust=False).mean()

    # 4H ADX for regime
    df_4h_calc = df_4h.copy()
    df_4h_calc["adx_4h"] = calculate_adx(df_4h_calc, 14)
    df_4h_calc["trend_4h"] = np.where(df_4h_calc["Close"] > df_4h_calc["Close"].rolling(50).mean(), 1, -1)

    # Reindex 4H metrics strictly without lookahead
    adx_4h_reindexed = df_4h_calc["adx_4h"].reindex(df.index, method="ffill").fillna(0)
    trend_4h_reindexed = df_4h_calc["trend_4h"].reindex(df.index, method="ffill").fillna(0)

    df["adx_4h"] = adx_4h_reindexed
    df["trend_4h"] = trend_4h_reindexed

    # Macro Directional Bias
    bull_bias = (df["Close"] > df["ema_50"]) & (df["ema_50"] > df["ema_200"]) & (df["adx_4h"] >= 20) & (df["trend_4h"] == 1)
    bear_bias = (df["Close"] < df["ema_50"]) & (df["ema_50"] < df["ema_200"]) & (df["adx_4h"] >= 20) & (df["trend_4h"] == -1)

    df["htf_bias"] = 0
    df.loc[bull_bias, "htf_bias"] = 1
    df.loc[bear_bias, "htf_bias"] = -1

    # 1H Swing Signal: Pullback into 20/50 EMA band followed by structural rejection
    df["swing_signal"] = 0
    for i in range(50, len(df)):
        bias = df["htf_bias"].iloc[i]
        if bias == 1:
            # Bullish pullback test: low touched near 20/50 EMA, closed above 20 EMA
            prev_low = df["Low"].iloc[i-1]
            prev_ema50 = df["ema_50"].iloc[i-1]
            curr_close = df["Close"].iloc[i]
            curr_ema20 = df["ema_20"].iloc[i]
            if prev_low <= curr_ema20 * 1.002 and curr_close > curr_ema20:
                # 1H Bullish Break of last 5 bars high
                if curr_close > df["High"].iloc[i-5:i].max():
                    df.iloc[i, df.columns.get_loc("swing_signal")] = 1
        elif bias == -1:
            # Bearish pullback test
            prev_high = df["High"].iloc[i-1]
            curr_close = df["Close"].iloc[i]
            curr_ema20 = df["ema_20"].iloc[i]
            if prev_high >= curr_ema20 * 0.998 and curr_close < curr_ema20:
                if curr_close < df["Low"].iloc[i-5:i].min():
                    df.iloc[i, df.columns.get_loc("swing_signal")] = -1

    return df


def build_ltf_signals(df_5m: pd.DataFrame, df_1h_signals: pd.DataFrame) -> pd.DataFrame:
    """
    Computes 5M Scalp signals gated strictly by the HTF directional bias.
    """
    df = df_5m.copy()
    df["atr_5m"] = calculate_atr(df, 14)
    df["ema_9"]  = df["Close"].ewm(span=9, adjust=False).mean()
    df["ema_21"] = df["Close"].ewm(span=21, adjust=False).mean()

    # Align HTF signals to 5M index strictly backward (forward-fill closed 1H bars)
    htf_aligned = df_1h_signals[["htf_bias", "swing_signal", "atr"]].reindex(df.index, method="ffill").fillna(0)
    df["htf_bias"]     = htf_aligned["htf_bias"].astype(int)
    df["swing_signal"] = htf_aligned["swing_signal"].astype(int)
    df["htf_atr"]      = htf_aligned["atr"]

    # Session Killzone Filter: 07-10 UTC (London), 13-16 UTC (New York)
    hours = df.index.hour
    df["in_killzone"] = ((hours >= 7) & (hours < 10)) | ((hours >= 13) & (hours < 16))

    # Scalp Signal Generation:
    # 1. HTF Directional Lock: Buy ONLY when htf_bias == 1, Sell ONLY when htf_bias == -1
    # 2. In session killzone
    # 3. 5M FVG or Liquidity Sweep + micro BOS
    df["scalp_signal"] = 0
    df["baseline_signal"] = 0  # Unconstrained baseline for comparison

    for i in range(25, len(df)):
        htf_bias = df["htf_bias"].iloc[i]
        in_kz    = df["in_killzone"].iloc[i]
        close    = df["Close"].iloc[i]
        high     = df["High"].iloc[i]
        low      = df["Low"].iloc[i]

        # 5M Bullish Fair Value Gap (FVG): Low of bar i > High of bar i-2
        bull_fvg = df["Low"].iloc[i-1] > df["High"].iloc[i-3]
        # 5M Bearish Fair Value Gap (FVG): High of bar i-1 < Low of bar i-3
        bear_fvg = df["High"].iloc[i-1] < df["Low"].iloc[i-3]

        # 5M Micro Break of Structure (BOS)
        bull_bos = close > df["High"].iloc[i-6:i].max()
        bear_bos = close < df["Low"].iloc[i-6:i].min()

        # Baseline Strategy: enters whenever 5M BOS + FVG occurs (ignores HTF lock)
        if bull_bos and in_kz:
            df.iloc[i, df.columns.get_loc("baseline_signal")] = 1
        elif bear_bos and in_kz:
            df.iloc[i, df.columns.get_loc("baseline_signal")] = -1

        # Gated Scalp Strategy: Requires strict alignment with HTF bias
        if in_kz and htf_bias == 1:
            if (bull_bos or bull_fvg) and close > df["ema_9"].iloc[i]:
                df.iloc[i, df.columns.get_loc("scalp_signal")] = 1
        elif in_kz and htf_bias == -1:
            if (bear_bos or bear_fvg) and close < df["ema_9"].iloc[i]:
                df.iloc[i, df.columns.get_loc("scalp_signal")] = -1

    return df


# ── Simulation Engine ─────────────────────────────────────────────────────────

def run_simulation(df: pd.DataFrame, symbol: str, mode: str) -> Dict:
    """
    Simulates portfolio execution bar-by-bar across 5M data.
    Modes:
      'baseline'     : Current 5M standalone setup
      'swing_only'   : 1H Swing setups only
      'scalp_only'   : 5M HTF-aligned Scalps only
      'dual_horizon' : Both Swing + Scalp running concurrently
    """
    balance  = INITIAL_BALANCE
    peak_bal = INITIAL_BALANCE
    max_dd   = 0.0

    open_trades: List[Dict] = []
    closed_trades: List[Dict] = []

    spread_pts = SPREAD_POINTS.get(_strip_suffix(symbol), 15)
    # Estimate point value
    base_sym = _strip_suffix(symbol)
    point_val = 0.01 if "XAU" in base_sym or "JPY" in base_sym else 0.00001
    spread_usd = spread_pts * point_val

    for i in range(50, len(df)):
        bar = df.iloc[i]
        curr_high = bar["High"]
        curr_low  = bar["Low"]
        curr_close = bar["Close"]

        # 1. Evaluate open trades
        surviving_trades = []
        for t in open_trades:
            hit_tp = (t["dir"] ==  1 and curr_high >= t["tp"]) or \
                     (t["dir"] == -1 and curr_low  <= t["tp"])
            hit_sl = (t["dir"] ==  1 and curr_low  <= t["sl"]) or \
                     (t["dir"] == -1 and curr_high >= t["sl"])

            if hit_tp or hit_sl:
                if hit_tp and hit_sl:
                    # Ambiguous single-bar resolution: whichever was closer to entry wins
                    hit_tp = abs(t["tp"] - t["entry"]) <= abs(t["sl"] - t["entry"])
                    hit_sl = not hit_tp

                pnl = (t["risk_usd"] * t["rrr"]) if hit_tp else -t["risk_usd"]
                # Deduct spread cost
                pnl -= (t["lots"] * spread_usd * 100000) if "XAU" not in base_sym else (t["lots"] * spread_usd * 100)

                balance += pnl
                peak_bal = max(peak_bal, balance)
                dd = (peak_bal - balance) / peak_bal if peak_bal > 0 else 0
                max_dd = max(max_dd, dd)

                closed_trades.append({
                    "type":     t["type"],
                    "dir":      "BUY" if t["dir"] == 1 else "SELL",
                    "entry":    t["entry"],
                    "exit":     t["tp"] if hit_tp else t["sl"],
                    "pnl":      round(pnl, 2),
                    "result":   "WIN" if hit_tp else "LOSS",
                    "rrr":      t["rrr"],
                    "bars":     i - t["entry_bar"],
                })
            else:
                surviving_trades.append(t)
        open_trades = surviving_trades

        # 2. Check for New Entries according to mode
        num_swings = sum(1 for t in open_trades if t["type"] == "SWING")
        num_scalps = sum(1 for t in open_trades if t["type"] == "SCALP")
        total_open = len(open_trades)

        # ── Mode: Baseline
        if mode == "baseline":
            sig = bar["baseline_signal"]
            if sig != 0 and total_open < 1:
                atr = bar["atr_5m"]
                if atr > 0:
                    sl_dist = atr * 1.5
                    tp_dist = sl_dist * 2.0
                    risk_usd = balance * BASELINE_RISK_PCT
                    lots = max(0.01, round(risk_usd / (sl_dist * 100000), 2)) if "XAU" not in base_sym else 0.01

                    open_trades.append({
                        "type":      "BASELINE",
                        "dir":       sig,
                        "entry":     curr_close,
                        "sl":        curr_close - sl_dist if sig == 1 else curr_close + sl_dist,
                        "tp":        curr_close + tp_dist if sig == 1 else curr_close - tp_dist,
                        "risk_usd":  risk_usd,
                        "lots":      lots,
                        "rrr":       2.0,
                        "entry_bar": i,
                    })

        # ── Mode: Swing Only
        elif mode == "swing_only":
            sig = bar["swing_signal"]
            if sig != 0 and num_swings < MAX_SWING_TRADES:
                atr_htf = bar["htf_atr"]
                if atr_htf > 0:
                    sl_dist = atr_htf * 1.5
                    tp_dist = sl_dist * SWING_RRR
                    risk_usd = balance * SWING_RISK_PCT
                    lots = max(0.01, round(risk_usd / (sl_dist * 100000), 2)) if "XAU" not in base_sym else 0.01

                    open_trades.append({
                        "type":      "SWING",
                        "dir":       sig,
                        "entry":     curr_close,
                        "sl":        curr_close - sl_dist if sig == 1 else curr_close + sl_dist,
                        "tp":        curr_close + tp_dist if sig == 1 else curr_close - tp_dist,
                        "risk_usd":  risk_usd,
                        "lots":      lots,
                        "rrr":       SWING_RRR,
                        "entry_bar": i,
                    })

        # ── Mode: Scalp Only
        elif mode == "scalp_only":
            sig = bar["scalp_signal"]
            if sig != 0 and num_scalps < MAX_SCALP_TRADES:
                atr = bar["atr_5m"]
                if atr > 0:
                    sl_dist = atr * 1.2
                    tp_dist = sl_dist * SCALP_RRR
                    risk_usd = balance * SCALP_RISK_PCT
                    lots = max(0.01, round(risk_usd / (sl_dist * 100000), 2)) if "XAU" not in base_sym else 0.01

                    open_trades.append({
                        "type":      "SCALP",
                        "dir":       sig,
                        "entry":     curr_close,
                        "sl":        curr_close - sl_dist if sig == 1 else curr_close + sl_dist,
                        "tp":        curr_close + tp_dist if sig == 1 else curr_close - tp_dist,
                        "risk_usd":  risk_usd,
                        "lots":      lots,
                        "rrr":       SCALP_RRR,
                        "entry_bar": i,
                    })

        # ── Mode: Dual-Horizon (Both concurrently)
        elif mode == "dual_horizon":
            # Check Swing Entry
            swing_sig = bar["swing_signal"]
            if swing_sig != 0 and num_swings < MAX_SWING_TRADES and total_open < MAX_TOTAL_TRADES:
                atr_htf = bar["htf_atr"]
                if atr_htf > 0:
                    sl_dist = atr_htf * 1.5
                    tp_dist = sl_dist * SWING_RRR
                    risk_usd = balance * SWING_RISK_PCT
                    lots = max(0.01, round(risk_usd / (sl_dist * 100000), 2)) if "XAU" not in base_sym else 0.01

                    open_trades.append({
                        "type":      "SWING",
                        "dir":       swing_sig,
                        "entry":     curr_close,
                        "sl":        curr_close - sl_dist if swing_sig == 1 else curr_close + sl_dist,
                        "tp":        curr_close + tp_dist if swing_sig == 1 else curr_close - tp_dist,
                        "risk_usd":  risk_usd,
                        "lots":      lots,
                        "rrr":       SWING_RRR,
                        "entry_bar": i,
                    })
                    total_open += 1

            # Check Scalp Entry (Never against active swing position)
            scalp_sig = bar["scalp_signal"]
            active_swing_dirs = [t["dir"] for t in open_trades if t["type"] == "SWING"]

            # Filter: If a swing is open, only allow scalps in the EXACT SAME direction
            can_scalp = True
            if active_swing_dirs and scalp_sig not in active_swing_dirs:
                can_scalp = False

            if can_scalp and scalp_sig != 0 and num_scalps < MAX_SCALP_TRADES and total_open < MAX_TOTAL_TRADES:
                atr = bar["atr_5m"]
                if atr > 0:
                    sl_dist = atr * 1.2
                    tp_dist = sl_dist * SCALP_RRR
                    risk_usd = balance * SCALP_RISK_PCT
                    lots = max(0.01, round(risk_usd / (sl_dist * 100000), 2)) if "XAU" not in base_sym else 0.01

                    open_trades.append({
                        "type":      "SCALP",
                        "dir":       scalp_sig,
                        "entry":     curr_close,
                        "sl":        curr_close - sl_dist if scalp_sig == 1 else curr_close + sl_dist,
                        "tp":        curr_close + tp_dist if scalp_sig == 1 else curr_close - tp_dist,
                        "risk_usd":  risk_usd,
                        "lots":      lots,
                        "rrr":       SCALP_RRR,
                        "entry_bar": i,
                    })

    # ── Summary Calculations ──────────────────────────────────────────────────
    if not closed_trades:
        return {
            "mode": mode, "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "net_pnl_pct": 0.0, "profit_factor": 0.0, "max_dd_pct": 0.0,
            "final_balance": balance, "swing_pnl": 0.0, "scalp_pnl": 0.0,
        }

    wins       = [t for t in closed_trades if t["result"] == "WIN"]
    losses     = [t for t in closed_trades if t["result"] == "LOSS"]
    net_pnl    = sum(t["pnl"] for t in closed_trades)
    gross_win  = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf         = gross_win / gross_loss if gross_loss > 0 else float("inf")

    swing_pnl  = sum(t["pnl"] for t in closed_trades if t["type"] == "SWING")
    scalp_pnl  = sum(t["pnl"] for t in closed_trades if t["type"] == "SCALP")

    return {
        "mode":          mode,
        "trades":        len(closed_trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(closed_trades) * 100, 1),
        "net_pnl":       round(net_pnl, 2),
        "net_pnl_pct":   round((balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100, 2),
        "profit_factor": round(pf, 2),
        "max_dd_pct":    round(max_dd * 100, 2),
        "final_balance": round(balance, 2),
        "swing_pnl":     round(swing_pnl, 2),
        "scalp_pnl":     round(scalp_pnl, 2),
    }


# ── Main Runner ───────────────────────────────────────────────────────────────

def main():
    pairs = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_PAIRS
    print("=" * 70)
    print(" 🏛️  DUAL-HORIZON (HTF SWING + LTF SCALP) PROTOTYPE BACKTEST")
    print("=" * 70)
    print(f" Initial Balance : ${INITIAL_BALANCE:,.2f}")
    print(f" Pairs Tested    : {', '.join(pairs)}")
    print(f" Lookback Depth  : {LOOKBACK_BARS_5M} 5M bars (~100 trading days)")
    print(f" Swing Setup     : 1H Timeframe | 1.5x ATR SL | 1:{SWING_RRR} RRR | 0.5% Risk")
    print(f" Scalp Setup     : 5M Timeframe | 1.2x ATR SL | 1:{SCALP_RRR} RRR | 0.25% Risk (HTF Direction Locked)")
    print("=" * 70)

    if not mt5.initialize():
        print("❌ Failed to initialize MT5")
        sys.exit(1)

    all_results = []

    for pair in pairs:
        print(f"\n⏳ Fetching data & processing signals for {pair}...")
        df_5m, df_1h, df_4h = fetch_data(pair)
        if df_5m is None or df_1h is None or df_4h is None:
            print(f"  ❌ Insufficient data for {pair}")
            continue

        print(f"  ✓ Fetched: {len(df_5m)} 5M bars, {len(df_1h)} 1H bars, {len(df_4h)} 4H bars")
        df_1h_sig = build_htf_signals(df_1h, df_4h)
        df_merged = build_ltf_signals(df_5m, df_1h_sig)

        print(f"  ✓ Simulating 4 strategies on {pair}...")
        res_base  = run_simulation(df_merged, pair, "baseline")
        res_swing = run_simulation(df_merged, pair, "swing_only")
        res_scalp = run_simulation(df_merged, pair, "scalp_only")
        res_dual  = run_simulation(df_merged, pair, "dual_horizon")

        for r in [res_base, res_swing, res_scalp, res_dual]:
            r["pair"] = pair
            all_results.append(r)

    mt5.shutdown()

    if not all_results:
        print("\n❌ No backtest results generated.")
        return

    # Print Summary Table
    df_res = pd.DataFrame(all_results)
    csv_path = "logs/dual_timeframe_backtest_results.csv"
    df_res.to_csv(csv_path, index=False)

    print("\n" + "=" * 90)
    print(f"{'PAIR':<8} {'STRATEGY':<15} {'TRADES':<8} {'WIN%':<8} {'PROFIT FACTOR':<14} {'NET P&L':<12} {'MAX DD':<10}")
    print("-" * 90)

    for _, r in df_res.iterrows():
        pnl_str = f"${r['net_pnl']:+,.2f} ({r['net_pnl_pct']:+.1f}%)"
        print(f"{r['pair']:<8} {r['mode']:<15} {r['trades']:<8} {r['win_rate']:<8.1f} {r['profit_factor']:<14.2f} {pnl_str:<12} {r['max_dd_pct']:<10.1f}%")

    print("=" * 90)
    print(f"\n📊 Detailed results saved to: {csv_path}\n")


if __name__ == "__main__":
    main()
