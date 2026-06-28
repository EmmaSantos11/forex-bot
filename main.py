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
from strategy import add_indicators, add_features, generate_signals_ict, add_ict_features, SMT_PAIRS, ADX_TRANSITIONING
from ml_model import (
    load_model, load_rf_model, load_pair_models, save_model,
    generate_ai_signals, generate_ensemble_signals,
    prepare_ml_data, train_model, train_ensemble, train_pair_ensemble,
    add_session_features, RF_MODEL_PATH,
)
from portfolio_manager import PortfolioManager
from live_trader import place_trade
from core.notifications import send_telegram_message, send_telegram_to, send_to_channel, send_photo_to_channel, get_telegram_updates, clear_telegram_webhook
from core.chart_generator import generate_trade_chart
from core.multi_timeframe import resample_timeframes
from core.execution_logger import log_execution_note
from core.trade_logger import log_trade
from cloud_sync import upload_to_hub, download_from_hub

load_dotenv()
warnings.filterwarnings("ignore")

# Force UTF-8 console output on Windows — prevents UnicodeEncodeError on emoji
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ── Credentials ──────────────────────────────────────────────────────────────
LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

# ── Preferred pairs — base names only (broker suffix handled automatically)
PREFERRED_PAIRS = [
    "EURUSD", "USDCHF",
    "USDCAD", "NZDUSD", "EURJPY",
    "XAUUSD",   # Gold
    "BTCUSD",   # Bitcoin
    # GBPUSD removed — backtest PF 0.84 (net loser), 37% max drawdown
    # USDJPY removed — backtest PF 1.0 (breakeven), 28% max drawdown
    # AUDUSD removed — backtest PF 1.05 too thin; live slippage puts it below 1.0
]

# ── File paths ────────────────────────────────────────────────────────────────
USER_PAIRS_FILE    = "data/user_pairs.txt"
MANUAL_TRADES_FILE = "data/manual_trade_features.csv"
USERS_FILE         = "data/users.json"

# ── Model feature columns (must match ml_model.py) ───────────────────────────
FEATURE_COLS = [
    "trend_strength", "momentum", "volatility", "RSI", "MACD_hist", "BB_width", "BB_position",
    "ict_structure_bull", "ict_bos", "ict_liq_sweep", "ict_ob", "ict_fvg",
    "ict_choch", "ict_displacement", "ict_cisd", "ict_kill_zone",
    "ict_ote", "ict_golden_zone", "ict_score_norm", "ict_smt",
]

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
MIN_CONFIDENCE         = 0.75  # 75% minimum AI confidence — both XGB and RF must agree
EVENING_MIN_CONFIDENCE = 0.80  # 80% required during low-momentum evening hours (18-22 UTC)
STALE_MIN_CONFIDENCE   = 0.85  # 85% required when models are >8 days old — near-certainty only

# ── ICT hard gate ─────────────────────────────────────────────────────────────
# At least this many ICT/SMC factors must be confirmed before the AI confidence
# gate is even checked.  The ict_score is computed as round(n/11 * 20) where n
# is the number of binary ICT factors present:
#   score 5  ≈ 3/11 factors — was the old threshold, too permissive
#   score 7  ≈ 4/11 factors — current floor (requires meaningful confluence)
#   score 9  ≈ 5/11 factors — ideal for A+ setups
# BOS is also enforced as a standalone gate below this (see ICT_REQUIRE_BOS).
ICT_MIN_SCORE    = 9   # minimum out of 20 (≈ 5/11 binary ICT factors)
ICT_REQUIRE_BOS  = True  # Break of Structure must be confirmed — non-negotiable

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
GLOBAL_TRADE_COOLDOWN_SECS = 5400  # 90-min minimum gap between any two trades
_global_trade_count: int  = 0    # incremented on each filled trade, reset each day
_daily_losses:       int  = 0    # SL hits today — resets at midnight
_last_trade_time:    float = 0.0  # unix timestamp of last filled trade

# ── Weekly drawdown protection ────────────────────────────────────────────────
# A bad multi-day streak can pile up before the per-day 5% limit is hit each day.
# Example: lose 4% Monday + 4% Tuesday = 8% in 2 days without triggering 5% daily cap.
# This adds a mid-layer guard that pauses trading for the rest of the week.
WEEKLY_DD_LIMIT         = 0.08   # 8% loss from Monday's opening balance → pause until Mon
_week_start_balance: float    = 0.0   # balance at Monday open (or bot startup mid-week)
_week_start_date:    date | None = None  # which Monday this week's balance was snapped
_weekly_dd_alerted:  date | None = None  # prevents Telegram spam — alert sent once per day
_models_stale:       bool        = False  # raised when models are >8 days old — tightens confidence floor

# ── Session-aware trade budget ────────────────────────────────────────────────
# Spreads the daily 6-trade cap across both London and NY kill zones so the bot
# never burns all its slots before noon. Budget resets with the global count at
# midnight UTC. The overlap window (13:00-16:00) counts toward whichever session
# bucket the trade fires in — first come, first served.
#
#   London kill zone  07:00–12:00 UTC → max 3 trades
#   NY kill zone      13:00–18:00 UTC → max 3 trades
#   Outside KZ        any other hour  → max 1 trade (still gated by spread/conf)
#
ASIAN_KZ_START,  ASIAN_KZ_END  = 1,  3    # UTC hours — Tokyo/Asian kill zone
LONDON_KZ_START, LONDON_KZ_END = 7,  12   # UTC hours (inclusive start, exclusive end)
NY_KZ_START,     NY_KZ_END     = 13, 18
ASIAN_KZ_MAX    = 1   # one setup during the Asian kill zone (NZD, JPY, BTC)
LONDON_KZ_MAX   = 3
NY_KZ_MAX       = 3
OTHER_KZ_MAX    = 0   # zero trades in grey periods — must be inside a defined kill zone

_session_trade_counts: dict = {"asian": 0, "london": 0, "ny": 0, "other": 0}

# ── Drought / FOMO guard ──────────────────────────────────────────────────────
# Tracks the unix timestamp of the last successfully filled trade, persisted
# across days (unlike _last_trade_time which resets at midnight).
# Used to prevent the bot from jumping at the first marginal setup after a long
# quiet period — a professional trader is MORE selective after a break, not less.
#
# Rule: if no trade in DROUGHT_HOURS, block Asian KZ entirely and wait for London.
# Asian liquidity is thin; a proper A+ setup after a drought should be taken in
# the highest-liquidity window (London/NY), not the weakest one (Asian 01-03 UTC).
DROUGHT_HOURS          = 24    # hours since last trade = "drought" state
DROUGHT_BLOCK_ASIAN    = True  # in drought: skip Asian KZ, wait for London open
ASIAN_KZ_MIN_ICT_SCORE = 10   # stricter ICT bar for Asian KZ (thinner liquidity)
                               # standard ICT_MIN_SCORE=7 applies to London/NY
_last_successful_trade_ts: float = 0.0  # persists across days — never reset at midnight

# ── Heartbeat ─────────────────────────────────────────────────────────────────
# Sends a "bot is alive" DM to the admin every 6 hours so silence on Telegram
# doesn't mean the process died — it just means no setups fired.
HEARTBEAT_INTERVAL_SECS      = 6 * 3600   # 6 hours between each heartbeat
_last_heartbeat_ts: float    = 0.0        # unix timestamp of last heartbeat sent
_mt5_reconnect_fails: int    = 0          # consecutive MT5 reconnect failures
MT5_RECONNECT_ALERT_AFTER    = 3          # send Telegram alert after this many fails

# ── Daily open post + ICT setup alerts ───────────────────────────────────────
_daily_open_posted:     date | None = None   # prevents double-posting on same day
_sunday_prep_posted:    date | None = None   # prevents double Sunday prep post
_market_bias_posted:    date | None = None   # prevents double-posting market bias
_friday_summary_posted: date | None = None   # prevents double-posting Friday summary
_edu_post_week:        int         = -1    # ISO week number of last educational post
_ict_alert_sent:       dict        = {}    # {pair: unix_ts} — 4-hour cooldown per pair
_market_closed_alerted: bool       = False  # True while market-closed message is live
_STATE_FILE = "logs/bot_state.json"         # persists notification state across restarts
ICT_ALERT_THRESHOLD = 10                 # /20 factors needed to post a setup alert
ICT_ALERT_COOLDOWN  = 4 * 3600          # seconds between ICT alerts for the same pair


def _current_kz_bucket() -> str:
    """Returns which kill-zone bucket the current UTC hour falls into."""
    h = datetime.now(timezone.utc).hour
    if ASIAN_KZ_START  <= h < ASIAN_KZ_END:
        return "asian"
    if LONDON_KZ_START <= h < LONDON_KZ_END:
        return "london"
    if NY_KZ_START     <= h < NY_KZ_END:
        return "ny"
    return "other"


def _kz_budget_remaining() -> int:
    """How many trades are still allowed in the current kill-zone bucket."""
    bucket = _current_kz_bucket()
    caps = {"asian": ASIAN_KZ_MAX, "london": LONDON_KZ_MAX, "ny": NY_KZ_MAX, "other": OTHER_KZ_MAX}
    return caps[bucket] - _session_trade_counts.get(bucket, 0)

# ── Shared state (main thread + Telegram thread both access this) ─────────────
active_pairs: list = []   # populated on startup by detect_pairs()
pair_models: dict  = {}   # {symbol: (xgb_model, rf_model)} — per-pair models
_pair_trade_count: dict = {}   # {symbol: count} — resets every day, max 2 per pair
# Per-session pair limit: a pair may only trade ONCE per kill-zone session.
# This prevents 2 trades in the same London window (1 London + 1 NY = fine; 2 London = not).
# Tracked separately so _pair_trade_count keeps serving the daily 2-trade hard cap.
_pair_session_trade_counts: dict = {}   # {symbol: {"asian":n, "london":n, "ny":n}}

# Pending signal confirmation — signal must appear on 2 consecutive cycles before firing.
# Prevents one-bar false breakouts from triggering trades.
_pending_signals: dict = {}   # {symbol: direction}  (+1 or -1)

# After a stop-loss, the pair is locked out for 3 hours before re-entry.
# {symbol: unix_timestamp_of_sl_close}
_pair_loss_cooldown: dict = {}
LOSS_COOLDOWN_SECS = 3 * 3600   # 3 hours

# Tracks every open position so we can detect exits and notify users.
# {ticket: {symbol, direction, entry, sl, tp, lots, ict_score, confidence}}
_position_cache: dict = {}
_POSITION_CACHE_FILE  = "logs/position_cache.json"


def _save_position_cache():
    """Writes _position_cache to disk so ict_score/confidence survive a restart."""
    try:
        os.makedirs("logs", exist_ok=True)
        with open(_POSITION_CACHE_FILE, "w") as _pcf:
            json.dump({str(k): v for k, v in _position_cache.items()}, _pcf)
    except Exception:
        pass


def _load_position_cache() -> dict:
    """Loads a previously saved position cache (int keys restored from string)."""
    if not os.path.exists(_POSITION_CACHE_FILE):
        return {}
    try:
        with open(_POSITION_CACHE_FILE) as _pcf:
            raw = json.load(_pcf)
            return {int(k): v for k, v in raw.items()}
    except Exception:
        return {}


def _load_bot_state():
    """Restores notification + signal state from disk so restarts don't resend messages
    or lose pending signal confirmations mid-analysis."""
    global _daily_open_posted, _sunday_prep_posted, _edu_post_week
    global _news_notified, _market_closed_alerted, _last_report_date, _pending_signals
    global _market_bias_posted, _friday_summary_posted
    global _daily_losses, _global_trade_count, _last_trade_time
    global _last_successful_trade_ts
    global _week_start_balance, _week_start_date
    global _last_retrain_date
    if not os.path.exists(_STATE_FILE):
        return
    try:
        with open(_STATE_FILE) as _sf:
            _st = json.load(_sf)
        _dp = _st.get("daily_open_posted")
        _daily_open_posted     = date.fromisoformat(_dp) if _dp else None
        _sp = _st.get("sunday_prep_posted")
        _sunday_prep_posted    = date.fromisoformat(_sp) if _sp else None
        _edu_post_week         = int(_st.get("edu_post_week", -1))
        _news_notified         = set(_st.get("news_notified", []))
        _market_closed_alerted = bool(_st.get("market_closed", False))
        _lr = _st.get("last_report_date")
        _last_report_date      = date.fromisoformat(_lr) if _lr else None
        _pending_signals       = {k: int(v) for k, v in _st.get("pending_signals", {}).items()}
        _mb = _st.get("market_bias_posted")
        _market_bias_posted    = date.fromisoformat(_mb) if _mb else None
        _fs = _st.get("friday_summary_posted")
        _friday_summary_posted = date.fromisoformat(_fs) if _fs else None
        _lr2 = _st.get("last_retrain_date")
        if _lr2:
            _last_retrain_date = date.fromisoformat(_lr2)
        # Drought guard — always restore regardless of date so drought detection
        # works across day boundaries (this is intentionally NOT gated by trade_guard_date)
        _last_successful_trade_ts = float(_st.get("last_successful_trade_ts", 0.0))
        # Weekly drawdown — restore if saved this week so a restart mid-week
        # doesn't reset the reference balance and bypass the weekly guard
        _wsb = _st.get("week_start_balance", 0.0)
        _wsd = _st.get("week_start_date")
        if _wsd:
            _wsd_parsed = date.fromisoformat(_wsd)
            # Only use saved reference if it's from the current ISO week
            if _wsd_parsed.isocalendar()[1] == date.today().isocalendar()[1]:
                _week_start_balance = float(_wsb)
                _week_start_date    = _wsd_parsed
        if _last_successful_trade_ts > 0:
            _hours = (time.time() - _last_successful_trade_ts) / 3600
            print(f"  ⏱️  Last successful trade: {_hours:.1f}h ago"
                  + (" — DROUGHT mode active" if _hours >= DROUGHT_HOURS else ""))
        # Restore trade guard counters only if saved today — prevents stale data from
        # yesterday bypassing a fresh day's limits after a midnight restart
        _tg_date = _st.get("trade_guard_date")
        if _tg_date and date.fromisoformat(_tg_date) == date.today():
            _daily_losses       = int(_st.get("daily_losses", 0))
            _global_trade_count = int(_st.get("global_trade_count", 0))
            _last_trade_time    = float(_st.get("last_trade_time", 0.0))
            _saved_ptc          = _st.get("pair_trade_count", {})
            _pair_trade_count.update({k: int(v) for k, v in _saved_ptc.items()})
            _saved_plc          = _st.get("pair_loss_cooldown", {})
            _pair_loss_cooldown.update({k: float(v) for k, v in _saved_plc.items()})
            print(f"  ↩️  Restored trade guards: {_daily_losses} losses, "
                  f"{_global_trade_count} trades today, pairs={dict(_pair_trade_count)}")
    except Exception as _e:
        print(f"⚠️  Could not load bot state: {_e}")


def _save_bot_state():
    """Writes notification + signal state to disk so restarts stay in sync."""
    os.makedirs("logs", exist_ok=True)
    try:
        with open(_STATE_FILE, "w") as _sf:
            json.dump({
                "daily_open_posted":  _daily_open_posted.isoformat()  if _daily_open_posted  else None,
                "sunday_prep_posted": _sunday_prep_posted.isoformat() if _sunday_prep_posted else None,
                "edu_post_week":      _edu_post_week,
                "news_notified":      list(_news_notified),
                "market_closed":      _market_closed_alerted,
                "last_report_date":      _last_report_date.isoformat()      if _last_report_date      else None,
                "pending_signals":       _pending_signals,
                "market_bias_posted":    _market_bias_posted.isoformat()    if _market_bias_posted    else None,
                "friday_summary_posted": _friday_summary_posted.isoformat() if _friday_summary_posted else None,
                # Cross-day drought guard — NOT gated by trade_guard_date (must survive midnight reset)
                "last_successful_trade_ts": _last_successful_trade_ts,
                # Weekly drawdown reference — persists across day boundaries within the same week
                "week_start_balance":       _week_start_balance,
                "week_start_date":          _week_start_date.isoformat() if _week_start_date else None,
                # Model retrain date — persists so staleness check survives restarts
                "last_retrain_date":        _last_retrain_date.isoformat() if _last_retrain_date else None,
                # Trade guard counters — restored on restart so crashes don't bypass daily limits
                "trade_guard_date":       date.today().isoformat(),
                "daily_losses":           _daily_losses,
                "global_trade_count":     _global_trade_count,
                "last_trade_time":        _last_trade_time,
                "pair_trade_count":       _pair_trade_count,
                "pair_loss_cooldown":     _pair_loss_cooldown,
            }, _sf)
    except Exception:
        pass


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
        "pairs": ["NZDUSD", "USDJPY", "BTCUSD"],
        "note": "Low volatility — NZD/JPY + BTC active",
    },
    "Tokyo": {
        "emoji": "🇯🇵",
        "start": 0, "end": 9,
        "pairs": ["USDJPY", "EURJPY", "NZDUSD", "BTCUSD"],
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

# Dead zone: post-NY close through thin early Sydney open — low liquidity, wide spreads.
# Extended to 01:00 UTC (was 23:00) to block the 23:00-01:00 window where Sydney
# opens but has minimal volume before Tokyo ramps up.  First valid entry is the
# Asian kill zone (01:00 UTC).
DEAD_ZONE_START = 21   # UTC hour — after NY close
DEAD_ZONE_END   =  1   # UTC hour — wraps midnight; first valid session is Asian KZ at 01:00


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
    """True during the post-NY / thin-Sydney window (21:00–01:00 UTC, wraps midnight)."""
    hour = datetime.now(timezone.utc).hour
    # DEAD_ZONE_END < DEAD_ZONE_START means the window wraps midnight.
    if DEAD_ZONE_END < DEAD_ZONE_START:
        return hour >= DEAD_ZONE_START or hour < DEAD_ZONE_END
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
            send_to_channel(msg)


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
            send_to_channel(msg)
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
        "pairs":  ["EURUSD", "USDCHF", "EURJPY", "BTCUSD"],
        "reason": (
            "London opens fresh and institutions set their weekly bias. "
            "EUR leads early direction and CHF confirms safe-haven flow. "
            "EUR/JPY captures momentum from the Asian-to-London handoff. "
            "BTC often reacts strongly to the weekly opening bias — Monday sets the crypto direction."
        ),
    },
    1: {
        "label":  "Tuesday",
        "pairs":  ["EURUSD", "USDCHF", "NZDUSD", "XAUUSD", "BTCUSD"],
        "reason": (
            "Highest liquidity day of the week. "
            "London/NY overlap is at full strength across EUR and CHF. "
            "Gold (XAUUSD) sees strong directional moves on Tuesday economic data. "
            "BTC Tuesday volume is consistently among the highest of the week."
        ),
    },
    2: {
        "label":  "Wednesday",
        "pairs":  ["EURUSD", "USDCHF", "USDCAD", "NZDUSD", "XAUUSD", "BTCUSD"],
        "reason": (
            "Peak volume mid-week — best day for trend-following on all instruments. "
            "Gold reacts to mid-week USD/inflation data. "
            "BTC often sees its largest weekly moves Wednesday into Thursday."
        ),
    },
    3: {
        "label":  "Thursday",
        "pairs":  ["EURJPY", "NZDUSD", "USDCAD", "BTCUSD"],
        "reason": (
            "Asian session drives JPY carry-trade flows into EUR/JPY. "
            "NZD and CAD stay active on Asian commodity demand. "
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


def _post_daily_open(now: datetime):
    """Posts the London-open daily briefing to the Telegram channel once per day."""
    weekday  = now.weekday()
    sched    = DAILY_PAIR_SCHEDULE.get(weekday)
    if not sched:
        return
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    day       = day_names[weekday]

    base_to_broker = {_strip_suffix(p): p for p in active_pairs}
    matched   = [base_to_broker[b] for b in sched["pairs"] if b in base_to_broker]
    pair_str  = "\n".join(f"  • {p}" for p in matched) or "  • (resolving active pairs)"

    send_to_channel(
        f"🌅 <b>London Open — {day} Briefing</b>\n\n"
        f"Markets are live. Bot is scanning.\n\n"
        f"<b>Today's Focus Pairs:</b>\n{pair_str}\n\n"
        f"<b>Why these pairs?</b>\n{sched['reason']}\n\n"
        f"📡 Sessions: London 07:00–16:00 · NY 13:00–22:00 UTC\n"
        f"⚡ Every trade alert posts here in real time.\n\n"
        f"Follow along — wins and losses, no filter. 🐻"
    )
    print(f"📢 Daily open post sent to channel ({day}).")


def _post_sunday_prep():
    """Posts a Sunday evening briefing to the channel ~2h before Sydney open."""
    sched_mon = DAILY_PAIR_SCHEDULE.get(0)   # Monday's planned pairs
    open_dt   = next_market_open()
    eta       = _fmt_td(open_dt - datetime.now(timezone.utc)) if open_dt else "~2 hours"

    retrain_line = (
        "✅ Weekly retrain complete — models are fresh and up to date."
        if _last_retrain_date == date.today()
        else "⚠️ No retrain logged this week — bot running on existing models."
    )

    base_to_broker = {_strip_suffix(p): p for p in active_pairs}
    matched  = [base_to_broker[b] for b in sched_mon["pairs"] if b in base_to_broker] if sched_mon else []
    pair_str = "\n".join(f"  • {p}" for p in matched) or "  • (resolving active pairs)"

    send_to_channel(
        f"🌙 <b>Sunday Prep — Markets Open in {eta}</b>\n\n"
        f"A new week starts soon. Bot is warmed up and ready to scan.\n\n"
        f"<b>Monday's Focus Pairs:</b>\n{pair_str}\n\n"
        f"<b>AI Status:</b>\n{retrain_line}\n\n"
        f"📡 First scan begins at Sydney open (22:00 UTC).\n"
        f"London kill zone kicks off 07:00 UTC — that's where the real setups form.\n\n"
        f"Follow along for live trade alerts all week. 🐻"
    )
    print("📢 Sunday prep post sent to channel.")


def _post_market_bias(now: datetime):
    """Posts a pre-London 4H bias summary for today's pairs at 06:30 UTC Mon-Fri."""
    weekday = now.weekday()
    sched   = DAILY_PAIR_SCHEDULE.get(weekday)
    if not sched:
        return

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    day = day_names[weekday]

    base_to_broker = {_strip_suffix(p): p for p in active_pairs}
    pairs_today = [base_to_broker[b] for b in sched["pairs"] if b in base_to_broker]
    if not pairs_today:
        return

    pair_lines = []
    for pair in pairs_today:
        try:
            df4h = _fetch_bars(pair, mt5.TIMEFRAME_H4, 60)
            if df4h is None or len(df4h) < 52:
                pair_lines.append(f"  • <b>{_strip_suffix(pair)}</b>: — (data unavailable)")
                continue
            close   = df4h["Close"].iloc[-1]
            ma50    = df4h["Close"].rolling(50).mean().iloc[-1]
            high20  = df4h["High"].iloc[-20:].max()
            low20   = df4h["Low"].iloc[-20:].min()
            bias    = "🟢 Bullish" if close > ma50 else "🔴 Bearish"
            # Format price with appropriate decimal places
            decimals = 2 if "JPY" in pair or "XAU" in pair or "BTC" in pair else 5
            h_str = f"{high20:.{decimals}f}"
            l_str = f"{low20:.{decimals}f}"
            pair_lines.append(
                f"  • <b>{_strip_suffix(pair)}</b>: {bias}\n"
                f"    Key zone: {l_str} — {h_str}"
            )
        except Exception:
            pair_lines.append(f"  • <b>{_strip_suffix(pair)}</b>: — (fetch error)")

    pairs_block = "\n".join(pair_lines)
    send_to_channel(
        f"🌍 <b>Market Bias — {day}</b>\n"
        f"Pre-London · 06:30 UTC\n\n"
        f"<b>4H structure for today's pairs:</b>\n{pairs_block}\n\n"
        f"📌 Bias = price vs 4H MA50  |  Key zone = 20-bar high/low\n"
        f"⚡ London kill zone opens in 30 min (07:00 UTC)\n"
        f"🐻 Bot enters only when AI + ICT confirm the bias direction."
    )
    print(f"📢 Market bias post sent ({day}).")


def _post_friday_summary():
    """Posts a public weekly performance summary to the channel at Friday 17:00 UTC."""
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=5)
    deals = mt5.history_deals_get(since, now) or []
    exits = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT and d.symbol]

    week_num = now.isocalendar()[1]
    acct     = mt5.account_info()
    bal_line = f"\n💰 Balance: <b>${acct.balance:,.2f}</b>" if acct else ""

    if not exits:
        send_to_channel(
            f"📊 <b>Week {week_num} Summary</b>\n\n"
            f"No closed trades this week.\n\n"
            f"Markets didn't offer high-probability setups that met the bot's standards. "
            f"Quality over quantity — sitting on hands is a valid trade. 🐻"
            f"{bal_line}"
        )
        print("📢 Friday summary (no trades) sent to channel.")
        return

    total_pnl   = sum(d.profit for d in exits)
    wins        = [d for d in exits if d.profit > 0]
    losses      = [d for d in exits if d.profit <= 0]
    win_rate    = len(wins) / len(exits) * 100
    best_trade  = max(exits, key=lambda d: d.profit)
    worst_trade = min(exits, key=lambda d: d.profit)

    pair_pnl: dict = {}
    for d in exits:
        base = _strip_suffix(d.symbol)
        pair_pnl[base] = pair_pnl.get(base, 0) + d.profit
    pair_lines = "\n".join(
        f"  {'✅' if v >= 0 else '❌'} {k}: {'+'if v>=0 else ''}{v:.2f}"
        for k, v in sorted(pair_pnl.items(), key=lambda x: x[1], reverse=True)
    )

    sign         = "+" if total_pnl >= 0 else ""
    result_emoji = "🟢" if total_pnl >= 0 else "🔴"

    send_to_channel(
        f"📊 <b>Week {week_num} Summary</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{result_emoji} Net P&L:   <b>{sign}${abs(total_pnl):.2f}</b>\n"
        f"🎯 Trades:   <b>{len(exits)}</b>  ({len(wins)}W / {len(losses)}L)\n"
        f"📈 Win Rate: <b>{win_rate:.1f}%</b>\n"
        f"🏆 Best:  +${best_trade.profit:.2f} ({_strip_suffix(best_trade.symbol)})\n"
        f"📉 Worst:  ${worst_trade.profit:.2f} ({_strip_suffix(worst_trade.symbol)})"
        f"{bal_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>By pair:</b>\n{pair_lines}\n\n"
        f"Live record — no filters, no cherry-picking.\n"
        f"Follow @bera_bot_channel for real-time trade alerts all week. 🐻"
    )
    print("📢 Friday performance summary sent to channel.")


_EDU_TOPICS = [
    {
        "title": "Order Blocks — Where Institutions Leave Their Mark",
        "body": (
            "An <b>Order Block (OB)</b> is the last opposing candle before a strong directional move — "
            "it marks the zone where banks and institutions placed their orders.\n\n"
            "When price returns to that zone, smart money defends it. That's our entry.\n\n"
            "<b>How the bot uses it:</b>\n"
            "• Identifies the last bearish candle before a bullish surge (bullish OB)\n"
            "• Waits for price to retrace into the OB zone on a lower timeframe\n"
            "• Enters only if other ICT factors confirm (BOS, liquidity sweep, kill zone)\n\n"
            "<i>Patience is the edge — most retail traders chase the move, we wait for the return.</i>"
        ),
    },
    {
        "title": "Fair Value Gaps — Price Doesn't Like Imbalance",
        "body": (
            "A <b>Fair Value Gap (FVG)</b> is a 3-candle pattern where the middle candle moves so fast "
            "it leaves a gap between the wicks of candles 1 and 3.\n\n"
            "That gap is an <i>inefficiency</i> — price almost always returns to fill it.\n\n"
            "<b>How the bot uses it:</b>\n"
            "• Detects FVGs on the 15M and 1H charts\n"
            "• Treats them as high-probability entry zones on the first retest\n"
            "• Combines with structure direction — only trades FVGs in the trend's direction\n\n"
            "<i>The gap is the breadcrumb. Structure is the map. Both must agree.</i>"
        ),
    },
    {
        "title": "Break of Structure — Confirming the Trend",
        "body": (
            "A <b>Break of Structure (BOS)</b> happens when price takes out a prior swing high (bullish) "
            "or swing low (bearish), confirming the trend is still intact.\n\n"
            "Without BOS, we don't know if a move is a trend or a fakeout.\n\n"
            "<b>How the bot uses it:</b>\n"
            "• Checks 4H structure first — only trades in the direction of a confirmed BOS\n"
            "• Change of Character (CHOCH) on 15M gives early warning of reversals\n"
            "• BOS + OB/FVG confluence = A+ setup\n\n"
            "<i>The market tells you its direction. BOS is the announcement.</i>"
        ),
    },
    {
        "title": "Liquidity Sweeps — The Stop Hunt Before the Real Move",
        "body": (
            "Institutions need <i>liquidity</i> to fill large orders. Where does it sit? "
            "Above swing highs and below swing lows — right where retail traders put their stop losses.\n\n"
            "A <b>Liquidity Sweep</b> is when price briefly spikes through those levels to grab the stops, "
            "then reverses hard. That reversal is the actual trade.\n\n"
            "<b>How the bot uses it:</b>\n"
            "• Identifies equal highs/lows where retail stops cluster\n"
            "• Detects the sweep spike on the 15M chart\n"
            "• Enters on the reversal candle after the sweep, in the BOS direction\n\n"
            "<i>Retail gets stopped out. We enter where they just exited.</i>"
        ),
    },
    {
        "title": "Optimal Trade Entry (OTE) — Fibonacci Is Not Magic",
        "body": (
            "The <b>OTE zone</b> is the 0.50–0.786 Fibonacci retracement of a swing move. "
            "It's not random — institutions routinely fill positions in this zone after an impulsive move.\n\n"
            "Price returns to the OTE, builds a base, then continues in the original direction.\n\n"
            "<b>How the bot uses it:</b>\n"
            "• Draws the OTE on every confirmed 4H swing\n"
            "• Enters only when price retraces into the 0.618–0.786 zone (Golden Zone)\n"
            "• Uses ATR-based SL below the swing low — not an arbitrary number\n\n"
            "<i>0.618 is where value and momentum meet. That's our preferred entry.</i>"
        ),
    },
    {
        "title": "Kill Zones — Not All Hours Are Created Equal",
        "body": (
            "Forex doesn't move evenly around the clock. The biggest, cleanest moves happen during "
            "two windows when the largest market participants are active:\n\n"
            "🇬🇧 <b>London Kill Zone:</b> 07:00–12:00 UTC\n"
            "🇺🇸 <b>New York Kill Zone:</b> 13:00–18:00 UTC\n\n"
            "Outside these windows, spreads widen and moves lose conviction. "
            "Setups that look great at 02:00 UTC often fail — not because the analysis was wrong, "
            "but because the engine wasn't running.\n\n"
            "<b>How the bot uses it:</b>\n"
            "• Only enters trades during London and NY kill zones\n"
            "• Spreads the daily 6-trade cap: max 3 trades per session\n"
            "• Dead zone (22:00–05:00 UTC outside Sydney pairs) is skipped entirely\n\n"
            "<i>Time your entries with the big money. Timing is the strategy.</i>"
        ),
    },
]


def _post_edu_content():
    """Posts this week's rotating ICT/SMC lesson to the Telegram channel."""
    week_num = datetime.now(timezone.utc).isocalendar()[1]
    topic    = _EDU_TOPICS[week_num % len(_EDU_TOPICS)]
    send_to_channel(
        f"📚 <b>ICT Weekly Lesson</b>\n\n"
        f"<b>{topic['title']}</b>\n\n"
        f"{topic['body']}\n\n"
        f"——\n"
        f"🤖 <i>This is the same strategy powering Mr Bera's Bot — "
        f"automating what institutions do manually every day.</i>"
    )
    print(f"📢 Educational post sent (week {week_num}: {topic['title']}).")


def _post_trade_autopsy(symbol: str, direction: str, entry: float, exit_price: float,
                        profit: float, ict_score: int, confidence: float):
    """Posts an honest trade autopsy to the channel after a stop-loss hit."""
    base      = _strip_suffix(symbol)
    sign      = "+" if profit >= 0 else ""
    dir_emoji = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    send_to_channel(
        f"🔍 <b>Trade Autopsy — {base}</b>\n\n"
        f"Direction:  {dir_emoji}\n"
        f"Entry:      {entry:.5f}\n"
        f"Stopped at: {exit_price:.5f}\n"
        f"P&L:        <b>{sign}{profit:.2f}</b>\n\n"
        f"<b>What the bot saw at entry:</b>\n"
        f"• ICT Confluence: <b>{ict_score}/20</b> factors confirmed\n"
        f"• AI Confidence:  <b>{confidence:.0%}</b>\n\n"
        f"<b>What happened:</b>\n"
        f"Price did not respect the setup — SL was hit before TP.\n"
        f"This outcome feeds into the next weekly retrain so the AI learns from it.\n\n"
        f"<i>Losses are data. Every SL makes the model sharper. 🧠</i>"
    )
    print(f"📢 Trade autopsy posted for {base}.")


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

    # Replace resampled 4H/1H with directly-fetched bars so SMA50/MA70 have
    # proper history. Resampling 500 5M bars gives only ~10 4H bars — nowhere
    # near enough for a 50- or 70-period MA. 500 H4 bars ≈ 83 days, matching
    # the retrain's ~85-day history so training and live features are consistent.
    _df4h = _fetch_bars(symbol, mt5.TIMEFRAME_H4, 500)
    if _df4h is not None:
        tfs["4h"] = add_features(add_indicators(_df4h))
    _df1h = _fetch_bars(symbol, mt5.TIMEFRAME_H1, 400)
    if _df1h is not None:
        tfs["1h"] = add_features(add_indicators(_df1h))

    # ADX regime gate — mirrors generate_signals_ict's RANGING block.
    # If 4H ADX < 20, the market is choppy: BOS breaks are noise, ML predictions
    # trained on trending markets are unreliable. Block before any inference runs.
    if "4h" in tfs and "ADX" in tfs["4h"].columns:
        _adx_val = float(tfs["4h"]["ADX"].iloc[-1])
        if _adx_val < ADX_TRANSITIONING:
            print(f"  📊 {_strip_suffix(symbol)}: 4H ADX {_adx_val:.1f} — ranging market, skip.")
            return 0, 0.0, 0, False

    # Fetch correlated pair for SMT divergence feature
    _corr_df = None
    _corr_base = SMT_PAIRS.get(_strip_suffix(symbol))
    if _corr_base:
        _corr_sym = next((p for p in active_pairs if _strip_suffix(p) == _corr_base), None)
        if _corr_sym:
            _corr_raw = _fetch_bars(_corr_sym, mt5.TIMEFRAME_M5, 500)
            if _corr_raw is not None:
                _corr_raw = add_features(add_indicators(_corr_raw))
                _corr_df  = _corr_raw.reindex(tfs["5min"].index, method="ffill")

    # Add ICT binary features so live prediction uses the same 20-feature set
    # that the models were trained on.
    tfs["5min"] = add_ict_features(tfs["5min"], tfs.get("1h"), tfs.get("4h"), df_corr=_corr_df)

    # Session/timing features — must match what _build_training_data adds during retrain
    tfs["5min"] = add_session_features(tfs["5min"])

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
        confidence = min(raw_score / 20.0, 1.0)
        # In the rule-based path generate_signals_ict() enforces BOS as a gate,
        # so a non-zero signal already implies BOS was confirmed.
        ict_bos = signal != 0
        return signal, confidence, int(raw_score), ict_bos

    # ICT score: read from ict_score_norm (computed by add_ict_features above).
    # ict_score_norm = (sum of 11 binary ICT factors) / 11, scaled to /20 for display.
    # Previously used generate_signals_ict() here, which only writes non-zero scores
    # on bars with a live BOS — so the last bar was almost always 0. Fixed.
    try:
        last_bar  = tfs["5min"].iloc[-1]
        ict_score = round(float(last_bar.get("ict_score_norm", 0.0)) * 20)
        # ict_bos: True only when the current 5M bar's close breaks above/below
        # the 10-bar swing high/low — structural confirmation in trade direction.
        ict_bos   = bool(last_bar.get("ict_bos", 0) > 0)
    except Exception:
        ict_score = 0
        ict_bos   = False

    # Diagnostic: log model outputs whenever confidence is notable (≥ 45%).
    base = _strip_suffix(symbol)
    if confidence >= 0.45 or ict_score >= 5:
        direction = "BUY" if signal == 1 else ("SELL" if signal == -1 else "—")
        bos_str = "✅ BOS" if ict_bos else "❌ no-BOS"
        print(f"  📡 {base}: AI {confidence:.0%} conf | signal {direction} | ICT {ict_score}/20 | {bos_str}")

    return signal, confidence, ict_score, ict_bos


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
            _save_bot_state()
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
            send_to_channel(msg)

        # ── Upcoming notification (≤ 30 min, fires once) ──────────────────
        upcoming_key = event_id + "|upcoming"
        if 0 < mins_away <= 30 and upcoming_key not in _news_notified:
            _news_notified.add(upcoming_key)
            _save_bot_state()
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
            send_to_channel(msg)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE A — CORRELATION FILTER
# Prevents opening a new trade if a strongly correlated pair is already open.
# ══════════════════════════════════════════════════════════════════════════════

CORRELATION_GROUPS = [
    {"EURUSD", "GBPUSD"},           # both positively correlated vs USD
    {"USDJPY", "USDCHF", "USDCAD"}, # USD-base pairs
    {"NZDUSD", "USDCAD"},           # commodity / resource currencies
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
        _save_position_cache()
        symbol    = cached.get("symbol", "?")
        direction = cached.get("direction", "?")
        lots      = cached.get("lots", 0)
        sl_price  = cached.get("sl", 0)
        tp_price  = cached.get("tp", 0)
        entry     = cached.get("entry", 0)

        _outcome_chart = None
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
                _save_bot_state()  # persist immediately so a crash can't reset the loss guard
                _post_trade_autopsy(
                    symbol, direction, entry, exit_price, profit,
                    int(cached.get("ict_score", 0)),
                    float(cached.get("confidence", 0.0)),
                )
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

            # ── Outcome chart ─────────────────────────────────────────────
            _oc = ("TP" if "Take Profit" in reason else
                   "SL" if "Stop Loss"   in reason else
                   "BE" if "Breakeven"   in reason else None)
            if _oc:
                try:
                    _df_oc = _fetch_bars(symbol, mt5.TIMEFRAME_M5, count=90)
                    if _df_oc is not None:
                        _df_oc = add_indicators(_df_oc)
                        _outcome_chart = generate_trade_chart(
                            symbol=symbol, direction=direction,
                            entry_price=entry, sl_price=sl_price, tp_price=tp_price,
                            confidence=float(cached.get("confidence", 0.0)),
                            ict_score=int(cached.get("ict_score", 0)),
                            df_5m=_df_oc, outcome=_oc, exit_price=exit_price,
                        )
                except Exception as _ce:
                    print(f"⚠️ Outcome chart error: {_ce}")
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
        send_to_channel(msg)
        if _outcome_chart:
            send_photo_to_channel(_outcome_chart)


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
    _save_bot_state()   # persist immediately so a restart won't resend

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
    send_to_channel(msg)

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
        bar_df = add_ict_features(bar_df)   # compute ICT binary features at 5M scale

        latest = bar_df.iloc[-1]
        rows.append({
            "trend_strength":     latest.get("trend_strength"),
            "momentum":           latest.get("momentum"),
            "volatility":         latest.get("volatility"),
            "RSI":                latest.get("RSI"),
            "MACD_hist":          latest.get("MACD_hist"),
            "BB_width":           latest.get("BB_width"),
            "BB_position":        latest.get("BB_position"),
            "ict_structure_bull": latest.get("ict_structure_bull", 0),
            "ict_bos":            latest.get("ict_bos", 0),
            "ict_liq_sweep":      latest.get("ict_liq_sweep", 0),
            "ict_ob":             latest.get("ict_ob", 0),
            "ict_fvg":            latest.get("ict_fvg", 0),
            "ict_choch":          latest.get("ict_choch", 0),
            "ict_displacement":   latest.get("ict_displacement", 0),
            "ict_cisd":           latest.get("ict_cisd", 0),
            "ict_kill_zone":      latest.get("ict_kill_zone", 0),
            "ict_ote":            latest.get("ict_ote", 0),
            "ict_golden_zone":    latest.get("ict_golden_zone", 0),
            "ict_score_norm":     latest.get("ict_score_norm", 0),
            "ict_smt":            latest.get("ict_smt", 0),
            "Target":             label,
            "symbol":             symbol,
            "entry_time":         entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "source":             "manual",
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
    global _last_retrain_date, _models_stale

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
        rates = mt5.copy_rates_from_pos(eurusd_sym, mt5.TIMEFRAME_M5, 0, 25000)
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

    df_labeled = generate_signals_ict(tfs)
    df_labeled = add_ict_features(df_labeled, tfs.get("1h"), tfs.get("4h"))
    df_labeled = add_session_features(df_labeled)   # must match live get_signal() pipeline
    X_hist, y_hist = prepare_ml_data(df_labeled)

    if X_hist is None:
        send_telegram_message("⚠️  Retrain skipped — not enough signal data in history.")
        return

    # Step 4 — blend manual trades (3× weight = human judgment matters more)
    if os.path.exists(MANUAL_TRADES_FILE):
        manual = pd.read_csv(MANUAL_TRADES_FILE)
        if "Target" in manual.columns and not manual.empty:
            # Backfill ICT columns absent from older manual trade exports with 0
            for col in FEATURE_COLS:
                if col not in manual.columns:
                    manual[col] = 0
            manual = manual[FEATURE_COLS + ["Target"]].dropna()
            manual = manual[manual["Target"].isin([1, -1])]
            if not manual.empty:
                X_m   = pd.concat([manual[FEATURE_COLS]] * 3, ignore_index=True)
                y_m   = pd.concat([manual["Target"]]    * 3, ignore_index=True)
                X_all = pd.concat([X_hist, X_m], ignore_index=True)
                y_all = pd.concat([y_hist, y_m], ignore_index=True)
                print(f"  ✅ Blended {len(X_hist)} historical + {len(X_m)} manual samples")
            else:
                X_all, y_all = X_hist, y_hist
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
                pair_rates = mt5.copy_rates_from_pos(pair, mt5.TIMEFRAME_M5, 0, 15000)
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

            # Fetch correlated pair for SMT divergence feature — same as retrain_model.py
            _corr_base_p = SMT_PAIRS.get(base)
            _corr_df_p   = None
            if _corr_base_p:
                _corr_sym_p = next(
                    (ap for ap in active_pairs if _strip_suffix(ap) == _corr_base_p), None
                )
                if _corr_sym_p:
                    _corr_rates_p = mt5.copy_rates_from_pos(_corr_sym_p, mt5.TIMEFRAME_M5, 0, 15000)
                    if _corr_rates_p is not None and len(_corr_rates_p) > 0:
                        _corr_df_p = pd.DataFrame(_corr_rates_p)
                        _corr_df_p["time"] = pd.to_datetime(_corr_df_p["time"], unit="s")
                        _corr_df_p.set_index("time", inplace=True)
                        _corr_df_p = _corr_df_p[["open", "high", "low", "close", "tick_volume"]]
                        _corr_df_p.columns = ["Open", "High", "Low", "Close", "Volume"]
                        _corr_df_p = add_features(add_indicators(_corr_df_p))
                        _corr_df_p = _corr_df_p.reindex(tfs_p["5min"].index, method="ffill")

            df_sig_p = generate_signals_ict(tfs_p)
            df_sig_p = add_ict_features(
                df_sig_p, tfs_p.get("1h"), tfs_p.get("4h"), df_corr=_corr_df_p
            )
            df_sig_p = add_session_features(df_sig_p)   # must match live pipeline
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
    _models_stale      = False   # fresh models — restore normal confidence floor
    _save_bot_state()  # persist retrain date so staleness check survives restarts
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


MAX_BOT_USERS = 5   # max non-admin users that can receive bot DM alerts


def _register_user(chat_id: str) -> str:
    """
    Adds chat_id to registry.
    Returns: "new" if just registered, "existing" if already in, "full" if limit reached.
    """
    admin_id = os.getenv("TELEGRAM_CHAT_ID", "")
    users    = _load_registered_users()
    if chat_id in users:
        return "existing"
    # Admin is always allowed; non-admin users capped at MAX_BOT_USERS
    if chat_id != admin_id:
        non_admin = {u for u in users if u != admin_id}
        if len(non_admin) >= MAX_BOT_USERS:
            return "full"
    users.add(chat_id)
    _save_registered_users(users)
    return "new"


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM LISTENER  (runs in a background daemon thread)
# ══════════════════════════════════════════════════════════════════════════════

def _telegram_loop(pm: PortfolioManager):
    admin_id    = os.getenv("TELEGRAM_CHAT_ID", "")
    last_update = 0
    _fail_streak   = 0       # consecutive fetch failures
    _last_err_msg  = ""      # last printed error — suppress identical repeats

    # Clear any stale webhook so getUpdates works without 409 Conflict errors.
    # A webhook and long-polling cannot coexist on the same bot token.
    clear_telegram_webhook()
    print("📱 Telegram listener active.")

    while True:
        try:
            updates = get_telegram_updates(offset=last_update + 1)
            _fail_streak  = 0          # successful fetch — reset backoff
            _last_err_msg = ""
            for upd in updates:
                last_update = upd.get("update_id", last_update)
                msg     = upd.get("message", {})
                text    = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if not chat_id or not text:
                    continue

                is_admin = (chat_id == admin_id)
                cmd      = text.lower()

                # Auto-register anyone who contacts the bot (capped at MAX_BOT_USERS)
                reg_result = _register_user(chat_id)
                is_new     = (reg_result == "new")

                # Reject non-admin users when slots are full
                if reg_result == "full" and not is_admin:
                    send_telegram_to(chat_id,
                        "👋 <b>Mr Bera's Bot</b>\n\n"
                        "The bot's direct-user slots are currently full (5/5).\n\n"
                        "You can still follow every trade live on the channel:\n"
                        "📡 https://t.me/+m-pvIcsZ6OYxOTNk\n\n"
                        "To enquire about a managed account, message the admin directly."
                    )
                    continue

                # ── /start ────────────────────────────────────────────────
                if cmd in ["/start", "start", "hey", "hello"]:
                    if is_admin:
                        send_telegram_to(chat_id,
                            "👋 <b>Forex AI Bot — Admin Panel</b>\n\n"
                            "<b>Commands:</b>\n"
                            "• /health — full bot health check (on-demand)\n"
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

                # ── /health (admin only) — on-demand status report ────────
                elif cmd in ["/health", "health"]:
                    if not is_admin:
                        send_telegram_to(chat_id, "🔒 This command is restricted to the bot admin.")
                        continue
                    send_telegram_to(chat_id, _build_heartbeat_msg(pm))

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
            _fail_streak += 1
            err_msg = str(e)
            # Only print if the error is new or every 5th repeat — avoids console spam
            if err_msg != _last_err_msg or _fail_streak % 5 == 1:
                print(f"⚠️  Telegram error (attempt {_fail_streak}): {err_msg}")
                _last_err_msg = err_msg

        # Exponential backoff: 5 → 10 → 20 → 40 → 60s max on repeated failures.
        # On success (_fail_streak == 0) the Telegram long-poll naturally delays
        # ~10 seconds per empty cycle, so no extra sleep is needed.
        _sleep = min(5 * (2 ** min(_fail_streak, 4)), 60) if _fail_streak > 0 else 5
        time.sleep(_sleep)


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
        {"command": "health",     "description": "💓 Bot health & live stats"},
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
    Merges with the persisted cache file so ict_score/confidence are recovered.
    Prevents missed exit notifications after a power cut or crash.
    """
    _saved = _load_position_cache()
    positions = mt5.positions_get() or []
    for pos in positions:
        if pos.ticket not in _position_cache:
            _prev = _saved.get(pos.ticket, {})
            _position_cache[pos.ticket] = {
                "symbol":     pos.symbol,
                "direction":  "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "entry":      pos.price_open,
                "sl":         pos.sl,
                "tp":         pos.tp,
                "lots":       pos.volume,
                "ict_score":  _prev.get("ict_score", 0),
                "confidence": _prev.get("confidence", 0.0),
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


def _build_heartbeat_msg(pm: "PortfolioManager") -> str:
    """
    Builds the periodic health-check message sent to the admin every 6 hours
    and returned by the /health Telegram command.  Never sent to the channel.
    """
    acct = mt5.account_info()
    now  = datetime.now(timezone.utc)

    bal_str = f"${acct.balance:,.2f}" if acct else "—"
    eq_str  = f"${acct.equity:,.2f}"  if acct else "—"

    # Daily P&L from MT5 history
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(today_start, now) or []
    exits = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
    daily_pnl = sum(d.profit for d in exits)
    pnl_sign  = "+" if daily_pnl >= 0 else ""

    # Last successful trade
    if _last_successful_trade_ts > 0:
        h_since = (time.time() - _last_successful_trade_ts) / 3600
        last_str = f"{h_since:.1f}h ago"
        if h_since >= DROUGHT_HOURS:
            last_str += " ⚠️ DROUGHT"
    else:
        last_str = "none this session"

    # Model freshness
    if _last_retrain_date:
        model_age = (date.today() - _last_retrain_date).days
        model_str = f"{model_age}d ago"
        if model_age >= 8:
            model_str += " ⚠️ stale"
    else:
        model_str = "unknown"

    kz        = _current_kz_bucket()
    kz_left   = _kz_budget_remaining()
    mt5_ok    = is_terminal_connected()
    open_pos  = len(mt5.positions_get() or [])

    return (
        f"💓 <b>Heartbeat — Bot is Running</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Balance:      {bal_str}\n"
        f"Equity:       {eq_str}\n"
        f"Daily P&L:    {pnl_sign}{daily_pnl:.2f}\n"
        f"Max DD:       {pm.max_drawdown:.2%}\n"
        f"Open trades:  {open_pos}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Session:      {_sessions_text()}\n"
        f"KZ slot:      {kz} ({kz_left} remaining)\n"
        f"Trades today: {_global_trade_count} / {GLOBAL_DAILY_TRADE_CAP}\n"
        f"Losses today: {_daily_losses} / {MAX_DAILY_LOSSES}\n"
        f"Last trade:   {last_str}\n"
        f"Model age:    {model_str}\n"
        f"MT5:          {'🟢 Connected' if mt5_ok else '🔴 Disconnected'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>"
    )


def main():
    global active_pairs, _global_trade_count, _daily_losses, _last_trade_time, _session_trade_counts
    global _pair_session_trade_counts
    global _daily_open_posted, _sunday_prep_posted, _edu_post_week, _ict_alert_sent
    global _news_notified, _market_closed_alerted, _last_report_date, _pending_signals
    global _market_bias_posted, _friday_summary_posted, _last_successful_trade_ts
    global _week_start_balance, _week_start_date, _weekly_dd_alerted
    global _models_stale, _last_heartbeat_ts, _mt5_reconnect_fails

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

    # ── Model staleness check ─────────────────────────────────────────────────
    # Alert on startup if models haven't been retrained in 8+ days.
    # The Sunday auto-retrain should keep models fresh; this catches silent failures.
    MODEL_STALE_DAYS = 8
    if _last_retrain_date is not None:
        _model_age = (date.today() - _last_retrain_date).days
        if _model_age >= MODEL_STALE_DAYS:
            _models_stale = True
            _stale_msg = (
                f"⚠️ <b>Stale Models — {_model_age} Days Since Last Retrain</b>\n\n"
                f"Last retrain: {_last_retrain_date.strftime('%A %d %b %Y')}\n"
                f"The Sunday auto-retrain may have failed silently.\n\n"
                f"⛔ Confidence floor raised to {STALE_MIN_CONFIDENCE:.0%} until models are refreshed.\n"
                f"Only near-certain setups will fire. Run retrain_model.py to restore normal thresholds."
            )
            send_telegram_message(_stale_msg)
            print(f"⚠️  Models are {_model_age} days old — confidence floor raised to {STALE_MIN_CONFIDENCE:.0%}.")
    else:
        print("  ℹ️  No retrain date on record (first run or state was cleared).")

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

    # ── Weekly drawdown reference — set if missing (first run or new week) ───
    if _week_start_balance == 0.0 or _week_start_date is None:
        _wk_init = mt5.account_info()
        if _wk_init:
            _week_start_balance = _wk_init.balance
            _week_start_date    = date.today()
            print(f"📅 Weekly DD reference initialised: ${_week_start_balance:,.2f} "
                  f"(will reset properly next Monday)")
            _save_bot_state()

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

    current_day = date.today()
    _load_bot_state()
    market_closed_alerted = _market_closed_alerted

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
                _mt5_reconnect_fails += 1
                print(f"⚠️  MT5 disconnected. Reconnecting (attempt {_mt5_reconnect_fails})...")
                if not connect_mt5(LOGIN, PASSWORD, SERVER):
                    if _mt5_reconnect_fails == MT5_RECONNECT_ALERT_AFTER:
                        _admin = os.getenv("TELEGRAM_CHAT_ID", "")
                        if _admin:
                            send_telegram_to(_admin,
                                f"🔴 <b>MT5 Connection Lost</b>\n\n"
                                f"Failed to reconnect after {_mt5_reconnect_fails} attempts.\n"
                                f"Check that the MT5 terminal is open on the VPS.\n\n"
                                f"Bot will keep retrying every 10 seconds automatically."
                            )
                    time.sleep(10)
                    continue
                _mt5_reconnect_fails = 0   # reset counter on successful reconnect
                send_telegram_message("🔄 MT5 reconnected.")

            # ── Daily balance reset ───────────────────────────────────────
            today = date.today()
            if today != current_day:
                acct = mt5.account_info()
                if acct:
                    pm.new_day_reset(acct.balance)
                _pair_trade_count.clear()          # reset per-pair daily trade counter
                _pair_session_trade_counts.clear() # reset per-pair per-session counters
                _pending_signals.clear()           # stale pending signals don't carry over
                _global_trade_count = 0            # reset global daily cap
                _daily_losses       = 0            # reset consecutive-loss guard
                _last_trade_time    = 0.0          # reset inter-trade cooldown
                _session_trade_counts["asian"]  = 0  # reset session kill-zone budgets
                _session_trade_counts["london"] = 0
                _session_trade_counts["ny"]     = 0
                _session_trade_counts["other"]  = 0
                _daily_open_posted   = None     # allow today's London open post
                _sunday_prep_posted  = None     # allow this Sunday's prep post
                _market_bias_posted  = None     # allow today's market bias post
                _ict_alert_sent.clear()        # reset ICT alert cooldowns
                # Weekly drawdown — snapshot balance every Monday so the 8% guard
                # is relative to the week's opening equity, not an old reference
                if today.weekday() == 0:   # Monday
                    _wk_acct = mt5.account_info()
                    if _wk_acct:
                        _week_start_balance = _wk_acct.balance
                        _week_start_date    = today
                        print(f"📅 New week — weekly DD reference set: ${_week_start_balance:,.2f}")
                _save_bot_state()              # persist the cleared dates to disk
                current_day = today

            # ── Monday weekly report ─────────────────────────────────────
            send_weekly_report(pm)

            # ── Sunday auto-retrain ───────────────────────────────────────
            if today_is_sunday():
                run_auto_retrain()
                model    = load_model()       # pick up freshly trained XGBoost
                rf_model = load_rf_model()    # pick up freshly trained RF

            # ── Sunday prep post (20:00 UTC — 2h before Sydney open) ─────
            _now_utc_pre = datetime.now(timezone.utc)
            if (_now_utc_pre.weekday() == 6
                    and _now_utc_pre.hour >= 20
                    and _sunday_prep_posted != today):
                _sunday_prep_posted = today
                _save_bot_state()
                _post_sunday_prep()

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
                    _market_closed_alerted = True
                    _save_bot_state()
                time.sleep(300)
                continue

            if market_closed_alerted:
                print("🥳 Market open — resuming trading.")
                send_telegram_message(
                    f"🥳 <b>Market Open — Trading Resumed</b>\n\n"
                    f"{_today_schedule_text()}"
                )
                market_closed_alerted = False
                _market_closed_alerted = False
                _save_bot_state()

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

                # Weekly drawdown guard — 8% from Monday open pauses the bot
                # until the next Monday reset. Stops a multi-day losing streak
                # from piling up below the per-day radar.
                if _week_start_balance > 0:
                    _week_dd = (_week_start_balance - acct.balance) / _week_start_balance
                    if _week_dd >= WEEKLY_DD_LIMIT:
                        _set_mood("loss_guard",
                                  f"Weekly drawdown {_week_dd:.1%} — protecting the account.",
                                  "No new trades until Monday. Capital protection is the priority. 🛡️")
                        if _weekly_dd_alerted != today:
                            _weekly_dd_alerted = today
                            _week_dd_msg = (
                                f"🛡️ <b>Weekly Drawdown Limit Hit</b>\n\n"
                                f"Lost {_week_dd:.1%} from Monday's ${_week_start_balance:,.2f} opening.\n"
                                f"Weekly limit: {WEEKLY_DD_LIMIT:.0%}\n\n"
                                f"Bot is pausing all new trades until Monday.\n"
                                f"Open positions continue to be managed (trailing SL active)."
                            )
                            for _wu in all_users:
                                if _wu:
                                    send_telegram_to(_wu, _week_dd_msg)
                            send_to_channel(_week_dd_msg)
                            print(f"🛡️ Weekly DD {_week_dd:.1%} ≥ {WEEKLY_DD_LIMIT:.0%} — pausing until Monday.")
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

            # ── Daily market bias post (Mon-Fri at 06:30 UTC, pre-London) ──
            _now_utc = datetime.now(timezone.utc)
            if (_now_utc.weekday() < 5
                    and (_now_utc.hour > 6 or (_now_utc.hour == 6 and _now_utc.minute >= 30))
                    and _now_utc.hour < 8
                    and _market_bias_posted != today):
                _market_bias_posted = today
                _save_bot_state()
                _post_market_bias(_now_utc)

            # ── Daily London open post (once per weekday at 07:00 UTC) ───
            if (_now_utc.weekday() < 5
                    and _now_utc.hour >= LONDON_KZ_START
                    and _daily_open_posted != today):
                _daily_open_posted = today
                _save_bot_state()
                _post_daily_open(_now_utc)

            # ── Friday performance summary (Friday 17:00 UTC) ─────────────
            if (_now_utc.weekday() == 4
                    and _now_utc.hour >= 17
                    and _friday_summary_posted != today):
                _friday_summary_posted = today
                _save_bot_state()
                _post_friday_summary()

            # ── Weekly educational post (Wednesday 10:00 UTC) ─────────────
            _edu_week_now = _now_utc.isocalendar()[1]
            if (_now_utc.weekday() == 2
                    and _now_utc.hour >= 10
                    and _edu_post_week != _edu_week_now):
                _edu_post_week = _edu_week_now
                _save_bot_state()
                _post_edu_content()

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

                # Drought guard — if no trade in DROUGHT_HOURS, block Asian KZ and
                # wait for London.  A professional trader doesn't lower standards
                # after a quiet period; if anything they're more selective.
                # Asian 01-03 UTC has the thinnest liquidity — not where you want
                # to re-enter after a long break.  London (07:00 UTC) is the floor.
                if DROUGHT_BLOCK_ASIAN and _last_successful_trade_ts > 0:
                    _drought_hours = (time.time() - _last_successful_trade_ts) / 3600
                    if _drought_hours >= DROUGHT_HOURS and _current_kz_bucket() == "asian":
                        if pair == _pairs_today[0]:  # log once per cycle
                            print(f"💤 Drought ({_drought_hours:.0f}h since last trade) — "
                                  f"skipping Asian KZ, waiting for London open (07:00 UTC).")
                        _scan_state[pair] = {"status": "DROUGHT_WAIT", "signal": 0, "confidence": 0.0}
                        continue

                # Session kill-zone budget — ensures trades are spread across London
                # and NY sessions rather than all firing before noon.
                # "other" bucket has max=0 by design (grey periods, not kill zones).
                # Show OFF_SESSION for grey periods so the user doesn't see DAILY_LIMIT
                # when the daily cap hasn't actually been used up.
                if _kz_budget_remaining() <= 0:
                    _kz = _current_kz_bucket()
                    if _kz == "other":
                        _scan_state[pair] = {"status": "OFF_SESSION", "signal": 0, "confidence": 0.0}
                    else:
                        if pair == list(active_pairs)[0]:
                            print(f"⏸️  {_kz.title()} kill-zone budget exhausted — waiting for next session.")
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

                # Max 2 trades per pair per day (hard daily cap)
                if _pair_trade_count.get(pair, 0) >= 2:
                    _scan_state[pair] = {"status": "DAILY_LIMIT", "signal": 0, "confidence": 0.0}
                    continue

                # Per-session pair limit — max 1 trade per pair per kill-zone session.
                # Prevents 2 trades in the same London window; 1 London + 1 NY is fine.
                _kz_now = _current_kz_bucket()
                if _kz_now != "other":
                    _pair_sess_done = _pair_session_trade_counts.get(pair, {}).get(_kz_now, 0)
                    if _pair_sess_done >= 1:
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
                signal, confidence, ict_score, ict_bos = get_signal(pair, _pair_xgb, _pair_rf)
                if signal == 0:
                    if pair in _pending_signals:
                        _pending_signals.pop(pair, None)
                        _save_bot_state()   # signal gone — clear persisted pending state
                    else:
                        _pending_signals.pop(pair, None)
                    if (ict_score >= ICT_ALERT_THRESHOLD
                            and time.time() - _ict_alert_sent.get(pair, 0) > ICT_ALERT_COOLDOWN):
                        _ict_alert_sent[pair] = time.time()
                        _pair_base = _strip_suffix(pair)
                        send_to_channel(
                            f"👀 <b>Setup Forming — {_pair_base}</b>\n\n"
                            f"ICT confluence: <b>{ict_score}/20</b> factors confirmed\n"
                            f"AI watching but not yet confident enough to enter.\n\n"
                            f"Session: {_sessions_text()}\n\n"
                            f"Bot will enter automatically if AI confidence clears the threshold. 🎯"
                        )
                    _scan_state[pair] = {"status": "WATCHING", "signal": 0, "confidence": round(confidence, 3), "ict_score": ict_score}
                    continue

                # ── ICT hard gate — must pass before AI confidence is checked ──
                # Asian KZ uses a higher bar: thinner liquidity means a marginal
                # setup that would work in London can easily fail at 01-03 UTC.
                _eff_ict_min = (ASIAN_KZ_MIN_ICT_SCORE
                                if _current_kz_bucket() == "asian"
                                else ICT_MIN_SCORE)
                if ict_score < _eff_ict_min:
                    _pending_signals.pop(pair, None)
                    _ict_label = f"Asian KZ min {_eff_ict_min}" if _eff_ict_min > ICT_MIN_SCORE else f"minimum {_eff_ict_min}"
                    print(f"  ⛔ {pair}: ICT {ict_score}/20 below {_ict_label} — skip.")
                    _scan_state[pair] = {"status": "WEAK_ICT", "signal": signal, "confidence": round(confidence, 3), "ict_score": ict_score}
                    continue

                # ── BOS gate — Break of Structure is a non-negotiable confirmation ──
                # Without BOS the market hasn't structurally committed to the direction.
                # This is the core ICT requirement: sweep → BOS → entry.
                if ICT_REQUIRE_BOS and not ict_bos:
                    _pending_signals.pop(pair, None)
                    print(f"  ⛔ {pair}: No BOS on 5M — price hasn't broken structure yet, skip.")
                    _scan_state[pair] = {"status": "NO_BOS", "signal": signal, "confidence": round(confidence, 3), "ict_score": ict_score}
                    continue

                # ── Confidence gate ───────────────────────────────────────
                if _models_stale:
                    _min_conf = STALE_MIN_CONFIDENCE
                    period    = "stale-models"
                elif is_evening_caution():
                    _min_conf = EVENING_MIN_CONFIDENCE
                    period    = "evening"
                else:
                    _min_conf = MIN_CONFIDENCE
                    period    = "standard"
                _passes = confidence >= _min_conf
                if not _passes:
                    _pending_signals.pop(pair, None)
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
                    _save_bot_state()   # persist so a restart picks up mid-analysis
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
                else:
                    tp_pts = sl_pts * 5   # 1:5 default — profitable at ≥17% win rate
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
                    _last_successful_trade_ts = time.time()  # cross-day drought tracker
                    _kz_bucket = _current_kz_bucket()
                    _session_trade_counts[_kz_bucket] = _session_trade_counts.get(_kz_bucket, 0) + 1
                    # Per-session pair counter — prevents a second trade on the same pair
                    # within the same kill-zone window (e.g. two London trades on EURUSD)
                    if _kz_bucket != "other":
                        _pair_session_trade_counts.setdefault(pair, {})[_kz_bucket] = \
                            _pair_session_trade_counts.get(pair, {}).get(_kz_bucket, 0) + 1
                    _pending_signals.pop(pair, None)  # clear confirmation state
                    _news_skip_active.discard(pair)   # reset so future blocks log once again
                    _save_bot_state()  # persist counters immediately so a crash can't bypass daily limits

                    pm.open_trade({
                        "ticket":     result.order,
                        "symbol":     pair,
                        "type":       direction,
                        "lots":       lots,
                        "confidence": confidence,
                    })

                    # ── Compute actual prices for log and position cache ──────────────
                    entry_price = tick.ask if signal == 1 else tick.bid
                    pt          = info.point
                    sl_price    = entry_price - sl_pts * pt if signal == 1 else entry_price + sl_pts * pt
                    tp_price    = entry_price + tp_pts * pt if signal == 1 else entry_price - tp_pts * pt
                    rrr_log     = tp_pts // sl_pts if sl_pts > 0 else 3

                    log_trade({
                        "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "symbol":        pair,
                        "type":          direction,
                        "lots":          lots,
                        "ai_confidence": round(confidence, 4),
                        "ict_score":     ict_score,
                        "entry":         round(entry_price, 5),
                        "sl":            round(sl_price, 5),
                        "tp":            round(tp_price, 5),
                        "rrr":           rrr_log,
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
                    _position_cache[result.order] = {
                        "symbol":     pair,
                        "direction":  direction,
                        "entry":      entry_price,
                        "sl":         sl_price,
                        "tp":         tp_price,
                        "lots":       lots,
                        "ict_score":  ict_score,
                        "confidence": confidence,
                    }
                    _save_position_cache()

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
                    send_to_channel(trade_msg)

                    # ── Entry setup chart → channel ────────────────────────
                    try:
                        _df_chart = _fetch_bars(pair, mt5.TIMEFRAME_M5, count=90)
                        if _df_chart is not None:
                            _df_chart = add_indicators(_df_chart)
                            _entry_chart = generate_trade_chart(
                                symbol=pair, direction=direction,
                                entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
                                confidence=confidence, ict_score=ict_score,
                                df_5m=_df_chart, rr_ratio=rrr,
                            )
                            if _entry_chart:
                                send_photo_to_channel(_entry_chart)
                    except Exception as _ce:
                        print(f"⚠️ Entry chart error: {_ce}")

                else:
                    _scan_state[pair] = {"status": "FAILED", "signal": signal, "confidence": round(confidence, 3)}
                    err = result.comment if result else "no response from MT5"
                    print(f"❌ Trade failed for {pair}: {err}")
                    send_telegram_message(f"⚠️ Trade failed for <b>{pair}</b>: {err}")

            # ── Reversal partials — protect profits when AI flips direction ──
            _check_reversal_partials()
            _write_signal_scan(_scan_state)

            # ── Heartbeat — "I'm alive" DM to admin every 6 hours ───────────
            # Fires even during dead zones / weekends so silence on Telegram
            # never leaves you guessing whether the process is still running.
            _now_ts = time.time()
            if _now_ts - _last_heartbeat_ts >= HEARTBEAT_INTERVAL_SECS:
                _last_heartbeat_ts = _now_ts
                _hb_admin = os.getenv("TELEGRAM_CHAT_ID", "")
                if _hb_admin:
                    send_telegram_to(_hb_admin, _build_heartbeat_msg(pm))
                print(f"💓 Heartbeat sent to admin.")

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
