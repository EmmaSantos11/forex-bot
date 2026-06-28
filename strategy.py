import numpy as np
import pandas as pd
from datetime import datetime, timezone


def add_indicators(df):
    df = df.copy()

    # ── Trend: SMAs + MA70 ────────────────────────────────────────────────────
    df["SMA_7"]  = df["Close"].rolling(7,  min_periods=1).mean()
    df["SMA_50"] = df["Close"].rolling(50, min_periods=1).mean()
    df["MA_70"]  = df["Close"].rolling(70, min_periods=1).mean()   # medium-term trend

    # ── Momentum: RSI ────────────────────────────────────────────────────────
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss  = -delta.clip(upper=0).rolling(14, min_periods=1).mean()
    df["RSI"] = 100 - (100 / (1 + gain / loss.replace(0, 1e-10)))

    # ── Volatility: ATR ──────────────────────────────────────────────────────
    high_low   = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close  = (df["Low"]  - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14, min_periods=1).mean()

    # ── MACD ─────────────────────────────────────────────────────────────────
    ema12             = df["Close"].ewm(span=12, adjust=False).mean()
    ema26             = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"]        = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"]   = df["MACD"] - df["MACD_signal"]

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_mid   = df["Close"].rolling(20, min_periods=1).mean()
    bb_std   = df["Close"].rolling(20, min_periods=1).std().fillna(0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_range = (bb_upper - bb_lower).replace(0, 1e-10)
    df["BB_width"]    = bb_range / bb_mid.replace(0, 1e-10)
    df["BB_position"] = (df["Close"] - bb_lower) / bb_range  # 0=lower, 1=upper

    # ── ADX — Average Directional Index (trend strength, not direction) ───────
    # ADX < 20  → ranging / choppy  → avoid BOS signals
    # ADX 20-25 → transitioning     → require extra confluence
    # ADX ≥ 25  → trending          → full signal quality
    high_diff = df["High"].diff()
    low_diff  = df["Low"].diff()

    plus_dm  = high_diff.where((high_diff > 0) & (high_diff > -low_diff), 0.0)
    minus_dm = (-low_diff).where((-low_diff > 0) & (-low_diff > high_diff), 0.0)

    atr14     = tr.rolling(14, min_periods=1).mean()
    plus_di   = 100 * plus_dm.rolling(14, min_periods=1).mean() / atr14.replace(0, 1e-10)
    minus_di  = 100 * minus_dm.rolling(14, min_periods=1).mean() / atr14.replace(0, 1e-10)
    di_sum    = (plus_di + minus_di).replace(0, 1e-10)
    dx        = 100 * (plus_di - minus_di).abs() / di_sum
    df["ADX"]      = dx.rolling(14, min_periods=1).mean()
    df["DI_plus"]  = plus_di
    df["DI_minus"] = minus_di

    return df


def add_features(df):
    df = df.copy()

    df["returns"]        = df["Close"].pct_change()
    df["volatility"]     = df["returns"].rolling(10, min_periods=1).std()
    df["trend_strength"] = df["SMA_7"] - df["SMA_50"]
    df["momentum"]       = df["Close"] - df["Close"].shift(5)

    # MACD_hist, BB_width, BB_position already computed in add_indicators
    return df


# ══════════════════════════════════════════════════════════════════════════════
# FIBONACCI RETRACEMENT HELPER
# Finds the most recent 4H swing range and checks whether the current 1H price
# is sitting inside the Optimal Trade Entry (OTE) zone: 0.50 – 0.786 retrace.
# ══════════════════════════════════════════════════════════════════════════════

FIB_LEVELS = {
    "0.236": 0.236,
    "0.382": 0.382,
    "0.500": 0.500,
    "0.618": 0.618,
    "0.705": 0.705,   # ICT "golden pocket" midpoint
    "0.786": 0.786,
}
OTE_LOW  = 0.500
OTE_HIGH = 0.786


def _compute_fib_ote(df_4h_aligned, i, lookback=30):
    """
    Returns (in_ote_bull, in_ote_bear):
      in_ote_bull — price retraced 50–78.6% DOWN from swing high (discount zone)
      in_ote_bear — price retraced 50–78.6% UP from swing low  (premium zone)
    """
    start  = max(0, i - lookback)
    window = df_4h_aligned.iloc[start : i + 1]

    swing_high  = window["High"].max()
    swing_low   = window["Low"].min()
    swing_range = swing_high - swing_low

    if swing_range < 1e-10:
        return False, False

    close = df_4h_aligned["Close"].iloc[i]
    retrace_from_high = (swing_high - close) / swing_range
    retrace_from_low  = (close - swing_low)  / swing_range

    in_ote_bull = OTE_LOW <= retrace_from_high <= OTE_HIGH
    in_ote_bear = OTE_LOW <= retrace_from_low  <= OTE_HIGH

    return in_ote_bull, in_ote_bear


def compute_fib_levels(swing_high: float, swing_low: float) -> dict:
    rng = swing_high - swing_low
    return {
        name: {
            "bull": round(swing_high - ratio * rng, 5),
            "bear": round(swing_low  + ratio * rng, 5),
        }
        for name, ratio in FIB_LEVELS.items()
    }


# ══════════════════════════════════════════════════════════════════════════════
# SMART MONEY / MARKET MECHANICS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _detect_liquidity_sweep(df, i, direction=1, lookback=15):
    """
    Detects a stop hunt (liquidity sweep) on the current bar.

    Bullish setup (direction=1):
      Price wick dips BELOW a recent swing low (grabs sell-stop liquidity)
      but the candle CLOSES back above that low → buyers absorbed the sweep.

    Bearish setup (direction=-1):
      Price wick spikes ABOVE a recent swing high (grabs buy-stop liquidity)
      but the candle CLOSES back below that high → sellers absorbed the sweep.
    """
    if i < lookback + 1:
        return False

    window = df.iloc[i - lookback: i]
    bar    = df.iloc[i]

    if direction == 1:
        swing_low = window["Low"].min()
        return bar["Low"] < swing_low and bar["Close"] > swing_low
    else:
        swing_high = window["High"].max()
        return bar["High"] > swing_high and bar["Close"] < swing_high


def _detect_order_block(df, i, direction=1, lookback=20):
    """
    Detects if the current price is retesting an Order Block (POI).

    Bullish OB: the last bearish (red) candle before the rally that created
                the current bullish structure. Price returning to its range
                signals institutional demand waiting there.

    Bearish OB: the last bullish (green) candle before the sell-off.
    """
    if i < lookback + 1:
        return False

    close = df["Close"].iloc[i]

    if direction == 1:
        for j in range(i - 1, max(0, i - lookback), -1):
            if df["Close"].iloc[j] < df["Open"].iloc[j]:   # red candle
                ob_low  = df["Close"].iloc[j]
                ob_high = df["Open"].iloc[j]
                return ob_low <= close <= ob_high
    else:
        for j in range(i - 1, max(0, i - lookback), -1):
            if df["Close"].iloc[j] > df["Open"].iloc[j]:   # green candle
                ob_low  = df["Open"].iloc[j]
                ob_high = df["Close"].iloc[j]
                return ob_low <= close <= ob_high

    return False


def _detect_fvg(df, i, direction=1, lookback=10):
    """
    Detects a Fair Value Gap (imbalance) in the recent lookback bars and
    checks whether current price is retesting (trading inside) that gap.

    Bullish FVG: candle[j-2].high < candle[j].low  — gap left on the way up.
    Bearish FVG: candle[j-2].low  > candle[j].high — gap left on the way down.
    """
    if i < 3:
        return False

    close = df["Close"].iloc[i]
    start = max(2, i - lookback)

    for j in range(start, i):
        if direction == 1:
            gap_lo = df["High"].iloc[j - 2]
            gap_hi = df["Low"].iloc[j]
            if gap_hi > gap_lo and gap_lo <= close <= gap_hi:
                return True
        else:
            gap_hi = df["Low"].iloc[j - 2]
            gap_lo = df["High"].iloc[j]
            if gap_hi > gap_lo and gap_lo <= close <= gap_hi:
                return True

    return False


def _near_key_level(df, i, atr_mult=0.3):
    """
    Returns True if the current price is within ATR * atr_mult of a key level:
      • Psychological round numbers (last 2 digits ≈ 00 or 50)
      • Previous-day high or low (derived from the last 288 5M bars ≈ 24 hours)
    """
    if i < 10:
        return False

    close = df["Close"].iloc[i]
    atr   = df["ATR"].iloc[i]
    tol   = atr * atr_mult

    # ── Round number check ────────────────────────────────────────────────────
    # Works for any instrument: scale to 2 significant digits after the first
    # non-zero digit, then check if the last two "pips" are near 00 or 50.
    magnitude = 1.0
    temp = close
    while temp < 100:
        temp      *= 10
        magnitude *= 10
    # temp is now a 3-digit-ish number — check if last two digits are 00 or 50
    last_two = temp % 100
    if abs(last_two) < 5 or abs(last_two - 50) < 5 or abs(last_two - 100) < 5:
        return True

    # ── Previous-day high / low ───────────────────────────────────────────────
    day_bars = 288   # 24 h × 12 five-minute bars
    if i >= day_bars:
        prev_day = df.iloc[i - day_bars: i - day_bars // 2]
        pd_high  = prev_day["High"].max()
        pd_low   = prev_day["Low"].min()
        if abs(close - pd_high) <= tol or abs(close - pd_low) <= tol:
            return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# CHANGE OF CHARACTER (CHoCH) — SMC early reversal signal
#
# BOS  = trend continuation (price breaks WITH the trend)
# CHoCH = trend shift warning (price breaks AGAINST the prior micro-trend)
#
# Bullish CHoCH (direction=1):
#   Prior bars showed Lower Lows and Lower Highs (mini downtrend).
#   Price then forms a Higher Low AND breaks above the last Lower High.
#   → First "higher high" in a downtrend = character has changed = smart
#     money is starting to accumulate. Good early entry signal.
#
# Bearish CHoCH (direction=-1):
#   Prior bars showed Higher Highs and Higher Lows (mini uptrend).
#   Price then forms a Lower High AND breaks below the last Higher Low.
#   → First "lower low" in an uptrend = distribution starting.
# ══════════════════════════════════════════════════════════════════════════════

def _detect_choch(df, i, direction=1, lookback=30):
    """
    Detects a Change of Character at bar i.
    Splits the lookback window in half:
      first half  = establish the prior micro-trend (the "old character")
      second half = look for the structural break that signals the shift
    """
    if i < lookback + 2:
        return False

    mid    = lookback // 2
    window = df.iloc[i - lookback: i + 1]
    prior  = window.iloc[:mid]
    recent = window.iloc[mid:]
    close  = df["Close"].iloc[i]

    if prior.empty or recent.empty:
        return False

    if direction == 1:   # looking for bullish CHoCH
        prior_low_swing   = prior["Low"].min()
        recent_low_swing  = recent["Low"].min()
        last_lower_high   = prior["High"].max()

        # Recent low is higher than prior low (Higher Low formed)
        # AND current price has broken above the last Lower High
        return recent_low_swing > prior_low_swing and close > last_lower_high

    else:   # direction == -1, looking for bearish CHoCH
        prior_high_swing   = prior["High"].max()
        recent_high_swing  = recent["High"].max()
        last_higher_low    = prior["Low"].min()

        # Recent high is lower than prior high (Lower High formed)
        # AND current price has broken below the last Higher Low
        return recent_high_swing < prior_high_swing and close < last_higher_low


# ══════════════════════════════════════════════════════════════════════════════
# TURTLE BODY SOUP (TBS) — ICT body-level liquidity sweep
#
# Stronger than a regular wick sweep. The candle BODY (not just wick) closes
# through the swing level and then closes back inside the range.
# This means more traders were trapped at the wrong side of the move.
# ══════════════════════════════════════════════════════════════════════════════

def _detect_tbs(df, i, direction=1, lookback=15):
    """
    Bullish TBS: the candle body dips below the recent swing low, then closes back above.
    Bearish TBS: the candle body spikes above the recent swing high, then closes back below.
    """
    if i < lookback + 1:
        return False

    window  = df.iloc[i - lookback: i]
    bar     = df.iloc[i]
    body_lo = min(bar["Open"], bar["Close"])
    body_hi = max(bar["Open"], bar["Close"])

    if direction == 1:
        swing_low = window["Low"].min()
        return body_lo < swing_low and bar["Close"] > swing_low
    else:
        swing_high = window["High"].max()
        return body_hi > swing_high and bar["Close"] < swing_high


# ══════════════════════════════════════════════════════════════════════════════
# INVERSION FAIR VALUE GAP (IFVG) — ICT violated gap that flips POI role
#
# A regular FVG (3-candle imbalance) that is fully violated by subsequent price.
# Once violated, that same gap zone becomes the OPPOSITE type of POI:
#   Former bearish FVG (supply/resistance) → price rallies above it → IFVG demand
#   Former bullish FVG (demand/support)    → price falls below it  → IFVG supply
#
# Price retesting an IFVG from the new side is a high-probability entry.
# ══════════════════════════════════════════════════════════════════════════════

def _detect_ifvg(df, i, direction=1, lookback=20):
    """
    Bullish IFVG: a bearish FVG (downward imbalance) was broken above — price
    now retesting the former gap zone from above = demand.

    Bearish IFVG: a bullish FVG (upward imbalance) was broken below — price
    retesting from below = supply.
    """
    if i < 5:
        return False

    close = df["Close"].iloc[i]
    start = max(3, i - lookback)

    for j in range(start, i - 2):
        if direction == 1:
            # Bearish FVG: bar[j-2].Low > bar[j].High  →  downward gap
            gap_hi = df["Low"].iloc[j - 2]
            gap_lo = df["High"].iloc[j]
            if gap_hi <= gap_lo:
                continue
            # Price must have since violated it by trading ABOVE gap_hi
            if df["High"].iloc[j + 1: i].max() <= gap_hi:
                continue
            # Current price retesting inside the IFVG zone = demand
            if gap_lo <= close <= gap_hi:
                return True
        else:
            # Bullish FVG: bar[j-2].High < bar[j].Low  →  upward gap
            gap_lo = df["High"].iloc[j - 2]
            gap_hi = df["Low"].iloc[j]
            if gap_hi <= gap_lo:
                continue
            # Price must have since violated it by trading BELOW gap_lo
            if df["Low"].iloc[j + 1: i].min() >= gap_lo:
                continue
            # Current price retesting inside the IFVG zone = supply
            if gap_lo <= close <= gap_hi:
                return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# BREAKER BLOCK — ICT/SMC failed order block that flips supply↔demand
#
# A Breaker Block forms when an Order Block is broken through (structural break).
# The OB that was just violated flips roles:
#   Bearish OB broken above → becomes bullish Breaker Block (demand)
#   Bullish OB broken below → becomes bearish Breaker Block (supply)
#
# Price retesting a Breaker Block from the new side = institutional flip zone.
# ══════════════════════════════════════════════════════════════════════════════

def _detect_breaker_block(df, i, direction=1, lookback=30):
    """
    Bullish Breaker: find a bearish OB that was broken above, now price retests
    the former OB zone from above → demand / breaker block.

    Bearish Breaker: find a bullish OB that was broken below, now price retests
    the former OB zone from below → supply / breaker block.
    """
    if i < lookback + 3:
        return False

    close = df["Close"].iloc[i]

    if direction == 1:
        for j in range(max(0, i - lookback), i - 2):
            if df["Close"].iloc[j] < df["Open"].iloc[j]:   # bearish (red) candle = OB candidate
                ob_lo = df["Close"].iloc[j]
                ob_hi = df["Open"].iloc[j]
                # OB must have been broken above by subsequent bars
                if df["High"].iloc[j + 1: i].max() <= ob_hi:
                    continue
                # Price now retesting the former OB from above = bullish breaker
                if ob_lo <= close <= ob_hi:
                    return True
    else:
        for j in range(max(0, i - lookback), i - 2):
            if df["Close"].iloc[j] > df["Open"].iloc[j]:   # bullish (green) candle = OB candidate
                ob_lo = df["Open"].iloc[j]
                ob_hi = df["Close"].iloc[j]
                # OB must have been broken below by subsequent bars
                if df["Low"].iloc[j + 1: i].min() >= ob_lo:
                    continue
                # Price now retesting the former OB from below = bearish breaker
                if ob_lo <= close <= ob_hi:
                    return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# DISPLACEMENT — The Trading Geek / Brad Goh "Market Mechanics" core filter
#
# After a liquidity sweep, institutional delivery should show as 3+ consecutive
# large-bodied candles in the trade direction. Random/retail moves don't sustain
# this pattern. If displacement isn't present, the BOS is suspect.
#
# Large body = body covers ≥55% of candle range AND body ≥ 70% of average body
# ══════════════════════════════════════════════════════════════════════════════

def _detect_displacement(df, i, direction=1, lookback=12, min_consecutive=3):
    if i < lookback:
        return False
    window = df.iloc[max(0, i - lookback): i + 1]
    avg_body = (window["High"] - window["Low"]).mean()
    if avg_body == 0:
        return False

    consecutive = 0
    for j in range(len(window) - 1, -1, -1):
        row = window.iloc[j]
        full_range = row["High"] - row["Low"]
        if full_range == 0:
            break
        body = abs(row["Close"] - row["Open"])
        if body / full_range < 0.55 or body < avg_body * 0.70:
            break
        if direction == 1 and row["Close"] > row["Open"]:
            consecutive += 1
        elif direction == -1 and row["Close"] < row["Open"]:
            consecutive += 1
        else:
            break
        if consecutive >= min_consecutive:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# CISD — Change in State of Delivery (The Trading Geek / Brad Goh)
#
# A single dominant candle (body ≥ 70% of range) that closes powerfully beyond
# a recent swing extreme, declaring a decisive shift in who controls price.
# Brad's version: one candle that says "the old regime is over."
# ══════════════════════════════════════════════════════════════════════════════

def _detect_cisd(df, i, direction=1, lookback=6):
    if i < lookback + 1:
        return False
    bar = df.iloc[i]
    full_range = bar["High"] - bar["Low"]
    if full_range == 0:
        return False
    body = abs(bar["Close"] - bar["Open"])
    if body / full_range < 0.70:
        return False
    # Must close in trade direction
    if direction == 1 and bar["Close"] <= bar["Open"]:
        return False
    if direction == -1 and bar["Close"] >= bar["Open"]:
        return False
    # Close must break beyond the recent swing extreme
    recent = df.iloc[max(0, i - lookback): i]
    if direction == 1:
        return bar["Close"] > recent["High"].max()
    return bar["Close"] < recent["Low"].min()


# ══════════════════════════════════════════════════════════════════════════════
# GOLDEN ZONE — The Trading Geek 0.618 Fibonacci entry bonus
#
# Brad specifically targets the 0.618 retracement level for entries.
# Checks whether current price sits within ±0.05 of the 0.618 level on the
# 4H swing range (tighter than the broad OTE 0.50–0.786 window).
# ══════════════════════════════════════════════════════════════════════════════

GOLDEN_LOW  = 0.568   # 0.618 − 0.05
GOLDEN_HIGH = 0.668   # 0.618 + 0.05

def _in_golden_zone(df_4h, i, lookback=30):
    window = df_4h.iloc[max(0, i - lookback): i + 1]
    if len(window) < 5:
        return False, False
    swing_high = window["High"].max()
    swing_low  = window["Low"].min()
    rng        = swing_high - swing_low
    if rng == 0:
        return False, False
    close = df_4h["Close"].iloc[i]
    retrace_from_high = (swing_high - close) / rng
    retrace_from_low  = (close - swing_low)  / rng
    in_golden_bull = GOLDEN_LOW <= retrace_from_high <= GOLDEN_HIGH
    in_golden_bear = GOLDEN_LOW <= retrace_from_low  <= GOLDEN_HIGH
    return in_golden_bull, in_golden_bear


# ══════════════════════════════════════════════════════════════════════════════
# MARKET REGIME DETECTION
# Uses 4H ADX to classify the current environment before allowing any entry.
#
#  RANGING      ADX < 20   — choppy, mean-reverting, BOS signals are fake-outs
#  TRANSITIONING 20 ≤ ADX < 25 — market picking direction, be selective
#  TRENDING     ADX ≥ 25   — institutional trend in progress, best for ICT/SMC
# ══════════════════════════════════════════════════════════════════════════════

ADX_TRENDING      = 25.0
ADX_TRANSITIONING = 20.0


def market_regime(df_4h_aligned, i) -> str:
    """
    Returns the market regime at bar i based on 4H ADX.
    Call with the 4H dataframe reindexed to the 5M index.
    """
    if "ADX" not in df_4h_aligned.columns or i < 1:
        return "TRENDING"   # fallback: don't block if ADX not available
    adx = df_4h_aligned["ADX"].iloc[i]
    if adx >= ADX_TRENDING:
        return "TRENDING"
    if adx >= ADX_TRANSITIONING:
        return "TRANSITIONING"
    return "RANGING"


# ══════════════════════════════════════════════════════════════════════════════
# ICT KILL ZONES — London (07–09 UTC), New York (13–15 UTC), Asian (01–03 UTC)
#
# "Liquidity + Timing = Profit" — The Trading Geek
# Sweeps and BOS during session opens carry 2-3× more conviction because
# institutional algorithms are actively delivering orders at these windows.
# ══════════════════════════════════════════════════════════════════════════════

def _in_kill_zone_at(bar_time) -> bool:
    """Returns True if bar_time falls within a major ICT session kill zone."""
    try:
        if hasattr(bar_time, "hour"):
            hour = bar_time.hour
        else:
            hour = pd.Timestamp(bar_time).hour
    except Exception:
        return False
    return (1 <= hour < 3) or (7 <= hour < 9) or (13 <= hour < 15)


# ══════════════════════════════════════════════════════════════════════════════
# MASTER CANDLE RANGE THEORY — The Trading Geek
#
# The widest-range candle in the recent lookback window is the "master candle".
# It represents peak institutional delivery. When price later closes BEYOND its
# high (for longs) or low (for shorts), that's a range expansion breakout:
# institutions have committed to the new direction and are delivering further.
# ══════════════════════════════════════════════════════════════════════════════

def _detect_master_candle_break(df, i, direction=1, lookback=20):
    """Returns True if current bar closes beyond the master candle's range extreme."""
    if i < lookback + 1:
        return False
    window = df.iloc[max(0, i - lookback): i]
    ranges = window["High"] - window["Low"]
    if ranges.empty or ranges.max() == 0:
        return False
    master_bar = df.loc[ranges.idxmax()]
    close = df["Close"].iloc[i]
    return close > master_bar["High"] if direction == 1 else close < master_bar["Low"]


# ══════════════════════════════════════════════════════════════════════════════
# REJECTION CANDLE — Pin bar or engulfing at entry level
#
# A pin bar (long rejection wick ≥ 55% of total range) or a full engulfing
# candle at the POI confirms that participants tried to push price the other
# way and failed. The rejection IS the institutional footprint.
# ══════════════════════════════════════════════════════════════════════════════

def _detect_rejection_candle(df, i, direction=1):
    """Returns True if bar i shows a pin bar or engulfing candle in trade direction."""
    if i < 2:
        return False
    bar  = df.iloc[i]
    prev = df.iloc[i - 1]
    full_range = bar["High"] - bar["Low"]
    if full_range == 0:
        return False
    body_lo = min(bar["Close"], bar["Open"])
    body_hi = max(bar["Close"], bar["Open"])

    if direction == 1:
        lower_wick = body_lo - bar["Low"]
        # Pin bar: lower wick covers ≥55% of the full candle range
        if lower_wick >= full_range * 0.55:
            return True
        # Engulfing: bullish body fully covers previous bearish body
        prev_lo = min(prev["Close"], prev["Open"])
        prev_hi = max(prev["Close"], prev["Open"])
        if bar["Close"] > bar["Open"] and body_lo <= prev_lo and body_hi >= prev_hi:
            return True
    else:
        upper_wick = bar["High"] - body_hi
        # Pin bar: upper wick covers ≥55% of the full candle range
        if upper_wick >= full_range * 0.55:
            return True
        # Engulfing: bearish body fully covers previous bullish body
        prev_lo = min(prev["Close"], prev["Open"])
        prev_hi = max(prev["Close"], prev["Open"])
        if bar["Close"] < bar["Open"] and body_hi >= prev_hi and body_lo <= prev_lo:
            return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# ICT / SMC / MARKET MECHANICS MULTI-TIMEFRAME STRATEGY
#
# REQUIRED (gate conditions — must both be true):
#   4H  — Market Structure: SMA50 + MA70 directional bias
#   5M  — Break of Structure (BOS): price closes beyond recent swing
#
# CONFLUENCE SCORING (each factor +1, max 20):
#   0.  ADX trending bonus                  13. Daily Bias (PO3)
#   1.  Fibonacci OTE 0.50–0.786            14. Displacement (3+ large-body candles)
#   2.  RSI dual-timeframe confirm           15. CISD — dominant delivery candle
#   3.  MACD histogram aligned              16. Golden Zone 0.618±5%
#   4.  Liquidity sweep (stop hunt)         17. ICT Kill Zone (session open)
#   5.  Order Block / POI retest            18. Master Candle Range Break
#   6.  Fair Value Gap retest               19. Rejection Candle (pin bar/engulf)
#   7.  MA70 aligned on 1H
#   8.  Key level proximity (round/PDH/PDL)
#   9.  CHoCH — early structural reversal
#   10. Turtle Body Soup (body-level sweep)
#   11. Inversion FVG (flipped imbalance)
#   12. Breaker Block (flipped OB)
#
# Trade fires when score >= BASE_MIN_SCORE (kill zones allow -1 reduction).
# ICT_Score column feeds rule-based confidence (score / 20) back to main.py.
# ══════════════════════════════════════════════════════════════════════════════

def generate_signals_ict(tfs):
    df_5m = tfs["5min"].copy()
    df_1h = tfs["1h"].copy()
    df_4h = tfs["4h"].copy()
    df_1d = tfs.get("1d")   # daily — used for PO3 / daily bias confluence

    df_1h = df_1h.reindex(df_5m.index, method="ffill")
    df_4h = df_4h.reindex(df_5m.index, method="ffill")
    if df_1d is not None:
        df_1d = df_1d.reindex(df_5m.index, method="ffill")

    df_5m["Signal"]    = 0
    df_5m["ICT_Score"] = 0   # confluence count — used as rule-based confidence
    df_5m["Regime"]    = "UNKNOWN"

    BOS_LOOKBACK      = 10
    FIB_LOOKBACK      = 30
    BASE_MIN_SCORE    = 5   # A+ setups only: need ≥5/13 confluence factors in trending markets
    TRANSIT_MIN_SCORE = 6   # even tighter during transitioning regime

    for i in range(70, len(df_5m)):   # start at 70 so MA70 and ADX are warm

        # ── GATE 0: Market Regime — ADX filter ───────────────────────────────
        # This is the single biggest fix for choppy-market false signals.
        # In a ranging market, BOS breaks happen constantly and mean nothing.
        regime   = market_regime(df_4h, i)
        min_score = TRANSIT_MIN_SCORE if regime == "TRANSITIONING" else BASE_MIN_SCORE
        df_5m.at[df_5m.index[i], "Regime"] = regime

        if regime == "RANGING":
            continue   # flat market — no ICT/BOS entries

        # ── GATE 1: 4H Market Structure ───────────────────────────────────────
        close_4h = df_4h["Close"].iloc[i]
        sma7_4h  = df_4h["SMA_7"].iloc[i]
        sma50_4h = df_4h["SMA_50"].iloc[i]
        ma70_4h  = df_4h["MA_70"].iloc[i]

        # Triple-confirmation: price above SMA50, SMA7 above SMA50, price above MA70
        structure_bull = close_4h > sma50_4h and sma7_4h > sma50_4h and close_4h > ma70_4h
        structure_bear = close_4h < sma50_4h and sma7_4h < sma50_4h and close_4h < ma70_4h

        if not structure_bull and not structure_bear:
            continue

        direction = 1 if structure_bull else -1

        # ── GATE 2: 5M Break of Structure ─────────────────────────────────────
        close_5m  = df_5m["Close"].iloc[i]
        macd_hist = df_5m["MACD_hist"].iloc[i]
        rsi_5m    = df_5m["RSI"].iloc[i]

        start        = max(0, i - BOS_LOOKBACK)
        recent_highs = df_5m["High"].iloc[start:i]
        recent_lows  = df_5m["Low"].iloc[start:i]

        if recent_highs.empty or recent_lows.empty:
            continue

        swing_high_5m = recent_highs.max()
        swing_low_5m  = recent_lows.min()

        bos_bull = direction ==  1 and close_5m > swing_high_5m
        bos_bear = direction == -1 and close_5m < swing_low_5m

        if not bos_bull and not bos_bear:
            continue

        # ── CONFLUENCE SCORING ────────────────────────────────────────────────
        score = 0

        # 0. ADX trending bonus — strongly trending market gets a free point
        if regime == "TRENDING":
            score += 1

        # 1. Fibonacci OTE (institutional retracement zone)
        in_ote_bull, in_ote_bear = _compute_fib_ote(df_4h, i, FIB_LOOKBACK)
        if (direction == 1 and in_ote_bull) or (direction == -1 and in_ote_bear):
            score += 1

        # 2. RSI confirms on both timeframes
        rsi_1h = df_1h["RSI"].iloc[i]
        rsi_ok_bull = rsi_5m > 50 and rsi_1h < 60   # momentum up but not overbought
        rsi_ok_bear = rsi_5m < 50 and rsi_1h > 40
        if (direction == 1 and rsi_ok_bull) or (direction == -1 and rsi_ok_bear):
            score += 1

        # 3. MACD histogram aligned
        if (direction == 1 and macd_hist > 0) or (direction == -1 and macd_hist < 0):
            score += 1

        # 4. Liquidity sweep (stop hunt before BOS — highest quality signal)
        if _detect_liquidity_sweep(df_5m, i, direction=direction):
            score += 1

        # 5. Order Block / Point of Interest
        if _detect_order_block(df_5m, i, direction=direction):
            score += 1

        # 6. Fair Value Gap retest
        if _detect_fvg(df_5m, i, direction=direction):
            score += 1

        # 7. MA70 aligned on 1H (medium-term trend confirmation)
        close_1h = df_1h["Close"].iloc[i]
        ma70_1h  = df_1h["MA_70"].iloc[i]
        if (direction == 1 and close_1h > ma70_1h) or (direction == -1 and close_1h < ma70_1h):
            score += 1

        # 8. Key level proximity (round numbers or previous-day high/low)
        if _near_key_level(df_5m, i):
            score += 1

        # 9. CHoCH — early structural reversal signal on 5M
        if _detect_choch(df_5m, i, direction=direction):
            score += 1

        # 10. Turtle Body Soup (TBS) — candle BODY sweeps through swing level
        #     Stronger than a wick-only sweep: more traders trapped.
        if _detect_tbs(df_5m, i, direction=direction):
            score += 1

        # 11. Inversion FVG (IFVG) — violated imbalance zone retested from new side
        if _detect_ifvg(df_5m, i, direction=direction):
            score += 1

        # 12. Breaker Block — failed OB that has flipped to opposite POI role
        if _detect_breaker_block(df_5m, i, direction=direction):
            score += 1

        # 13. Daily Bias (PO3) — entry in discount for buys, premium for sells
        if df_1d is not None:
            daily_open   = df_1d["Open"].iloc[i]
            close_5m_now = df_5m["Close"].iloc[i]
            if direction == 1 and close_5m_now < daily_open:
                score += 1
            elif direction == -1 and close_5m_now > daily_open:
                score += 1

        # 14. Displacement — 3+ consecutive large-bodied institutional delivery candles
        #     (The Trading Geek "Market Mechanics" core: sweep → displacement → entry)
        if _detect_displacement(df_5m, i, direction=direction):
            score += 1

        # 15. CISD — Change in State of Delivery: single dominant candle that closes
        #     powerfully beyond a recent swing extreme (Brad Goh's delivery-shift signal)
        if _detect_cisd(df_5m, i, direction=direction):
            score += 1

        # 16. Golden Zone — price inside the 0.618 ± 5% Fibonacci band on the 4H swing
        #     (Brad's specific precision entry vs the broader 0.50–0.786 OTE window)
        gz_bull, gz_bear = _in_golden_zone(df_4h, i, FIB_LOOKBACK)
        if (direction == 1 and gz_bull) or (direction == -1 and gz_bear):
            score += 1

        # 17. ICT Kill Zone — London (07–09 UTC), NY (13–15 UTC), Asian (01–03 UTC)
        #     Institutional algorithms are most active at session opens. A sweep + BOS
        #     during a kill zone is 2-3× more reliable than the same pattern off-hours.
        in_kill_zone = _in_kill_zone_at(df_5m.index[i])
        if in_kill_zone:
            score += 1

        # 18. Master Candle Range Break — The Trading Geek's range expansion signal.
        #     When price closes beyond the widest candle in the lookback, institutions
        #     have committed to a full directional delivery. Strong continuation signal.
        if _detect_master_candle_break(df_5m, i, direction=direction):
            score += 1

        # 19. Rejection Candle — pin bar (wick ≥ 55% of range) or engulfing at POI.
        #     Physical evidence that the opposing side tried to push price the wrong way
        #     and failed completely. Highest-quality single-candle confirmation.
        if _detect_rejection_candle(df_5m, i, direction=direction):
            score += 1

        # Kill zone timing bonus: require 1 fewer factor during prime session windows.
        # Outside kill zones the full score threshold applies (more conviction needed).
        effective_min = (min_score - 1) if in_kill_zone else min_score

        if score < effective_min:
            continue   # not enough confluence — skip this BOS

        df_5m.at[df_5m.index[i], "Signal"]    = direction
        df_5m.at[df_5m.index[i], "ICT_Score"] = score

    return df_5m


# Alias so any external script importing the old name still works
generate_signals_multi_tf = generate_signals_ict


# ══════════════════════════════════════════════════════════════════════════════
# ICT FEATURE ENGINEERING — feeds ML models with pattern-level inputs
#
# Converts the 20-factor ICT rule engine into 12 binary/float columns that
# XGBoost and Random Forest can learn from. The AI can now discover WHICH
# ICT combinations actually predict wins, not just whether the total score
# passes a threshold.
#
# Called once per retrain (on historical bars) and once per live signal check
# (on the 500-bar 5M window). Works on any base timeframe.
# ══════════════════════════════════════════════════════════════════════════════

# ── SMT correlation map (base names only, no broker suffix) ──────────────────
# Positively correlated pairs that should move together. When they diverge
# at a swing extreme it signals a liquidity grab / manipulation rather than
# a genuine impulse — giving the bot a higher-quality entry in the opposite
# direction of the fakeout.
#
# Only positive correlations are listed. Inverse pairs (USDCHF, USDCAD) are
# excluded to keep the divergence logic unambiguous.
SMT_PAIRS = {
    # EURUSD / NZDUSD — both are risk currencies vs USD (~0.65 positive correlation).
    # Divergence at swing extremes reveals liquidity grabs not confirmed by the broader USD basket.
    # Note: GBPUSD was the original EURUSD partner but is not in the active pair list.
    # Switching to NZDUSD. Takes full effect after next Sunday retrain.
    "EURUSD": "NZDUSD",
    "NZDUSD": "EURUSD",

    # USDCAD / EURJPY — both react to risk sentiment and commodity flows.
    # USDCAD CAD oil correlation + EURJPY risk-on/off gives divergence signal quality.
    # EURJPY's former partner GBPUSD is not traded; USDCAD is the closest active proxy.
    "USDCAD": "EURJPY",
    "EURJPY": "USDCAD",

    # XAUUSD / BTCUSD — hard-asset positive correlation is the strongest in the pair list.
    # Divergence at swing extremes is the highest-reliability SMT signal the bot has.
    "XAUUSD": "BTCUSD",
    "BTCUSD": "XAUUSD",
}

ICT_FEATURE_COLS = [
    "ict_structure_bull",  # 1 = 4H bullish structure, 0 = bearish
    "ict_bos",             # Break of Structure confirmed in structure direction
    "ict_liq_sweep",       # Liquidity sweep (stop hunt before BOS)
    "ict_ob",              # Order Block / Point of Interest retest
    "ict_fvg",             # Fair Value Gap retest
    "ict_choch",           # Change of Character (early reversal signal)
    "ict_displacement",    # 3+ consecutive institutional delivery candles
    "ict_cisd",            # Change in State of Delivery (dominant candle)
    "ict_kill_zone",       # London / NY / Asian kill zone timing
    "ict_ote",             # Fibonacci OTE 0.50–0.786 on 4H swing
    "ict_golden_zone",     # Golden Zone 0.618 ± 5% (tighter precision entry)
    "ict_score_norm",      # Normalized confluence score 0–1 (out of 10 factors)
    "ict_smt",             # SMT divergence between correlated pairs
]


def _detect_smt_divergence(df_main: pd.DataFrame,
                           df_corr: pd.DataFrame,
                           i: int,
                           direction: int,
                           lookback: int = 20) -> bool:
    """
    Detects SMT (Smart Money Technique) divergence between two positively
    correlated instruments at bar i.

    Bullish SMT (direction=1):
        Main pair makes a LOWER LOW while the correlated pair makes a HIGHER
        LOW over the same window. The main pair's selloff is a liquidity grab
        not matched by the correlated pair — smart money is absorbing, not
        distributing. Look for longs on the main pair.

    Bearish SMT (direction=-1):
        Main pair makes a HIGHER HIGH while the correlated pair makes a LOWER
        HIGH. The rally is a stop hunt not matched by the correlated pair.
        Look for shorts on the main pair.
    """
    if i < lookback + 2:
        return False

    start = max(0, i - lookback)

    main_hi_prev = df_main["High"].iloc[start:i].max()
    main_lo_prev = df_main["Low"].iloc[start:i].min()
    main_hi_cur  = df_main["High"].iloc[i]
    main_lo_cur  = df_main["Low"].iloc[i]

    try:
        corr_hi_prev = df_corr["High"].iloc[start:i].max()
        corr_lo_prev = df_corr["Low"].iloc[start:i].min()
        corr_hi_cur  = df_corr["High"].iloc[i]
        corr_lo_cur  = df_corr["Low"].iloc[i]
    except (IndexError, KeyError):
        return False

    if direction == 1:
        # Bullish: main lower low, corr higher low
        return main_lo_cur < main_lo_prev and corr_lo_cur > corr_lo_prev
    else:
        # Bearish: main higher high, corr lower high
        return main_hi_cur > main_hi_prev and corr_hi_cur < corr_hi_prev


def add_ict_features(df_base: pd.DataFrame,
                     df_1h:   pd.DataFrame | None = None,
                     df_4h:   pd.DataFrame | None = None,
                     df_corr: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Adds ICT binary feature columns to df_base (any base timeframe).

    Direction per bar is determined from the 4H structure (same triple-
    confirmation used in generate_signals_ict): close > SMA50 > MA70 with
    SMA7 > SMA50 = bullish. Falls back to base-df SMA7/SMA50 if 4H not given.

    Returns an enriched copy of df_base with ICT_FEATURE_COLS added.
    """
    df = df_base.copy()
    n  = len(df)

    # Align higher-TF data to base index (forward-fill, same as generate_signals_ict)
    df4 = df_4h.reindex(df.index, method="ffill") if df_4h is not None else None
    _has_4h_struct = (
        df4 is not None
        and all(c in df4.columns for c in ("SMA_7", "SMA_50", "MA_70", "Close"))
    )

    # Align correlated pair data (SMT divergence). Uses its own index so we
    # reindex to match df's timestamps — missing bars become NaN then ffilled.
    _corr = (
        df_corr.reindex(df.index, method="ffill")
        if df_corr is not None and not df_corr.empty
        else None
    )

    FIB_LOOKBACK = 30
    BOS_LOOKBACK = 10

    # Pre-allocate output arrays (faster than df.at in a loop)
    a_struct = np.zeros(n)
    a_bos    = np.zeros(n)
    a_liq    = np.zeros(n)
    a_ob     = np.zeros(n)
    a_fvg    = np.zeros(n)
    a_choch  = np.zeros(n)
    a_disp   = np.zeros(n)
    a_cisd   = np.zeros(n)
    a_kz     = np.zeros(n)
    a_ote    = np.zeros(n)
    a_gz     = np.zeros(n)
    a_score  = np.zeros(n)
    a_smt    = np.zeros(n)

    for i in range(70, n):

        # ── Direction from 4H structure ───────────────────────────────────────
        if _has_4h_struct:
            c4   = df4["Close"].iloc[i]
            s7   = df4["SMA_7"].iloc[i]
            s50  = df4["SMA_50"].iloc[i]
            m70  = df4["MA_70"].iloc[i]
            s_bull = c4 > s50 and s7 > s50 and c4 > m70
            s_bear = c4 < s50 and s7 < s50 and c4 < m70
        elif "SMA_7" in df.columns and "SMA_50" in df.columns:
            s_bull = df["SMA_7"].iloc[i] > df["SMA_50"].iloc[i]
            s_bear = not s_bull
        else:
            continue

        if not s_bull and not s_bear:
            continue

        direction = 1 if s_bull else -1
        a_struct[i] = 1 if s_bull else 0

        # ── BOS in structure direction ────────────────────────────────────────
        start = max(0, i - BOS_LOOKBACK)
        r_hi  = df["High"].iloc[start:i]
        r_lo  = df["Low"].iloc[start:i]
        if r_hi.empty:
            continue
        close = df["Close"].iloc[i]
        bos   = (direction == 1 and close > r_hi.max()) or \
                (direction == -1 and close < r_lo.min())
        a_bos[i] = int(bos)

        # ── Individual ICT factors ────────────────────────────────────────────
        liq  = _detect_liquidity_sweep(df, i, direction=direction)
        ob   = _detect_order_block(df, i, direction=direction)
        fvg  = _detect_fvg(df, i, direction=direction)
        chch = _detect_choch(df, i, direction=direction)
        disp = _detect_displacement(df, i, direction=direction)
        csd  = _detect_cisd(df, i, direction=direction)
        kz   = _in_kill_zone_at(df.index[i])

        a_liq[i]   = int(liq)
        a_ob[i]    = int(ob)
        a_fvg[i]   = int(fvg)
        a_choch[i] = int(chch)
        a_disp[i]  = int(disp)
        a_cisd[i]  = int(csd)
        a_kz[i]    = int(kz)

        # ── 4H-dependent factors ──────────────────────────────────────────────
        ote = golden = False
        if df4 is not None:
            ote_bull, ote_bear = _compute_fib_ote(df4, i, FIB_LOOKBACK)
            ote = (direction == 1 and ote_bull) or (direction == -1 and ote_bear)
            gz_bull, gz_bear = _in_golden_zone(df4, i, FIB_LOOKBACK)
            golden = (direction == 1 and gz_bull) or (direction == -1 and gz_bear)
        a_ote[i] = int(ote)
        a_gz[i]  = int(golden)

        # ── SMT divergence against correlated pair ────────────────────────────
        smt = False
        if _corr is not None:
            smt = _detect_smt_divergence(df, _corr, i, direction)
        a_smt[i] = int(smt)

        # ── Normalized score (out of 11 binary factors above) ─────────────────
        a_score[i] = (int(bos) + int(liq) + int(ob) + int(fvg) + int(chch) +
                      int(disp) + int(csd) + int(kz) + int(ote) + int(golden) +
                      int(smt)) / 11.0

    df["ict_structure_bull"] = a_struct
    df["ict_bos"]            = a_bos
    df["ict_liq_sweep"]      = a_liq
    df["ict_ob"]             = a_ob
    df["ict_fvg"]            = a_fvg
    df["ict_choch"]          = a_choch
    df["ict_displacement"]   = a_disp
    df["ict_cisd"]           = a_cisd
    df["ict_kill_zone"]      = a_kz
    df["ict_ote"]            = a_ote
    df["ict_golden_zone"]    = a_gz
    df["ict_score_norm"]     = a_score
    df["ict_smt"]            = a_smt

    return df
