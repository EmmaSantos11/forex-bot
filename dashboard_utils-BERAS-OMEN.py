"""
dashboard_utils.py — shared utilities for the Streamlit dashboard.
Handles MT5 data fetching, CSS injection, component rendering, and color palette.
"""

import os
import json
import streamlit as st
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL_DIR = "models"
LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "bot_logo.png")
PAIRS     = ["EURUSD", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "XAUUSD", "BTCUSD"]

_BROKER_SUFFIXES = ("m", ".r", "_pro", ".z", "pro", "raw")

def _strip(symbol: str) -> str:
    s = symbol.upper()
    for suf in sorted(_BROKER_SUFFIXES, key=len, reverse=True):
        if s.endswith(suf.upper()):
            return s[: -len(suf)]
    return s


# ── Color palette ─────────────────────────────────────────────────────────────

def get_colors() -> dict:
    return {
        "bg":          "#0D1117",
        "bg2":         "#161B22",
        "bg3":         "#1C2128",
        "accent":      "#58A6FF",
        "accent2":     "#9D4EDD",
        "success":     "#3FB950",
        "danger":      "#F85149",
        "warning":     "#E3B341",
        "text":        "#E6EDF3",
        "text_dim":    "#7D8590",
        "card_border": "#30363D",
        "chart_grid":  "rgba(48,54,61,0.4)",
    }


def pnl_color(value: float) -> str:
    c = get_colors()
    return c["success"] if value > 0 else (c["danger"] if value < 0 else c["text_dim"])


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── MT5 helpers ───────────────────────────────────────────────────────────────

def _mt5_ok() -> bool:
    try:
        return mt5.initialize()
    except Exception:
        return False


def get_account_data() -> dict | None:
    if not _mt5_ok():
        return None
    acct = mt5.account_info()
    if acct is None:
        return None
    return {
        "balance":  acct.balance,
        "equity":   acct.equity,
        "currency": acct.currency,
        "leverage": acct.leverage,
        "profit":   acct.profit,
    }


def get_open_positions() -> list:
    if not _mt5_ok():
        return []
    positions = mt5.positions_get() or []
    rows = []
    for p in positions:
        rows.append({
            "Ticket":      p.ticket,
            "Symbol":      _strip(p.symbol),
            "Type":        "BUY" if p.type == 0 else "SELL",
            "Lots":        p.volume,
            "Open Price":  round(p.price_open, 5),
            "Current":     round(p.price_current, 5),
            "P&L ($)":     round(p.profit, 2),
        })
    return rows


def get_deal_history(days: int = 7) -> pd.DataFrame:
    if not _mt5_ok():
        return pd.DataFrame()
    now   = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now - timedelta(days=days)
    try:
        deals = mt5.history_deals_get(start, now)
    except Exception:
        return pd.DataFrame()
    if not deals:
        return pd.DataFrame()

    _DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rows = []
    for d in deals:
        # Only closing deals (DEAL_ENTRY_OUT=1) of BUY/SELL type
        if d.entry != 1 or d.type not in (0, 1):
            continue
        t = datetime.fromtimestamp(d.time)
        rows.append({
            "ticket":  d.position_id,
            "symbol":  _strip(d.symbol),
            "type":    "BUY" if d.type == 0 else "SELL",
            "volume":  d.volume,
            "profit":  round(d.profit, 2),
            "time":    t,
            "date":    t.date(),
            "weekday": _DAY_NAMES[t.weekday()],
            "hour":    t.hour,
            "win":     d.profit > 0,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_consecutive_winning_days() -> int:
    df = get_deal_history(days=90)
    if df.empty:
        return 0
    daily = df.groupby("date")["profit"].sum().sort_index()
    streak = 0
    for day_pnl in reversed(daily.tolist()):
        if day_pnl > 0:
            streak += 1
        else:
            break
    return streak


def get_all_time_high() -> float:
    acct = get_account_data()
    if acct is None:
        return 0.0
    df = get_deal_history(days=365)
    if df.empty:
        return acct["balance"]
    df_s = df.sort_values("time")
    start = acct["balance"] - df_s["profit"].sum()
    equity_curve = start + df_s["profit"].cumsum()
    return float(equity_curve.max())


# ── Log file readers ──────────────────────────────────────────────────────────

def _read_json(filename: str) -> dict | list:
    path = os.path.join(os.path.dirname(__file__), filename)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_signal_scan() -> dict:
    data = _read_json(os.path.join("logs", "signal_scan.json"))
    return data if isinstance(data, dict) else {}


def get_news_events() -> list:
    data = _read_json(os.path.join("logs", "news_events.json"))
    if isinstance(data, dict):
        return data.get("events", [])
    return []


def get_bot_mood() -> dict:
    data = _read_json(os.path.join("logs", "bot_mood.json"))
    if isinstance(data, dict):
        return data
    return {"state": "idle", "message": "Bot offline", "detail": "", "pair": "", "confidence": 0}


# ── Model info ────────────────────────────────────────────────────────────────

def get_model_info() -> list:
    results = []
    for pair in PAIRS:
        xgb_path = os.path.join(MODEL_DIR, f"xgb_{pair}.pkl")
        rf_path  = os.path.join(MODEL_DIR, f"rf_{pair}.pkl")

        last_train = None
        for p in (xgb_path, rf_path):
            if os.path.exists(p):
                mtime = datetime.fromtimestamp(os.path.getmtime(p))
                if last_train is None or mtime > last_train:
                    last_train = mtime

        results.append({
            "pair":       pair,
            "xgb":        os.path.exists(xgb_path),
            "rf":         os.path.exists(rf_path),
            "last_train": last_train,
        })
    return results


# ── CSS injection ─────────────────────────────────────────────────────────────

def inject_css():
    c = get_colors()
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&display=swap');

.stApp {{ background-color: {c['bg']}; }}
section[data-testid="stSidebar"] {{
    background-color: {c['bg2']};
    border-right: 1px solid {c['card_border']};
}}

.bot-card {{
    background: {c['bg2']};
    border: 1px solid {c['card_border']};
    border-radius: 14px;
    padding: 18px 20px;
    margin-bottom: 12px;
}}

.recall-bar-wrap {{
    width: 100%; height: 4px;
    background: {c['bg3']};
    border-radius: 2px;
    overflow: hidden;
}}
.recall-bar {{
    height: 4px;
    border-radius: 2px;
    transition: width 0.6s ease;
}}

.blog-card {{
    background: {c['bg2']};
    border: 1px solid {c['card_border']};
    border-radius: 14px;
    padding: 22px 24px;
    margin-bottom: 16px;
}}
.blog-card.win      {{ border-left: 3px solid {c['success']}; }}
.blog-card.loss     {{ border-left: 3px solid {c['danger']}; }}
.blog-card.news     {{ border-left: 3px solid {c['warning']}; }}
.blog-card.retrain  {{ border-left: 3px solid {c['accent2']}; }}
.blog-card.daily    {{ border-left: 3px solid {c['accent']}; }}
.blog-card.autopsy  {{ border-left: 3px solid #00BCD4; }}

.blog-tag {{
    display: inline-block;
    font-family: 'Orbitron', monospace;
    font-size: 9px; font-weight: 700; letter-spacing: 2px;
    padding: 3px 8px; border-radius: 4px; margin-bottom: 10px;
}}
.tag-win     {{ background: rgba(63,185,80,0.15);  color: {c['success']}; }}
.tag-loss    {{ background: rgba(248,81,73,0.15);  color: {c['danger']}; }}
.tag-news    {{ background: rgba(227,179,65,0.15); color: {c['warning']}; }}
.tag-retrain {{ background: rgba(157,78,221,0.15); color: {c['accent2']}; }}
.tag-daily   {{ background: rgba(88,166,255,0.15); color: {c['accent']}; }}
.tag-autopsy {{ background: rgba(0,188,212,0.15);  color: #00BCD4; }}

@keyframes bot-float {{
    0%, 100% {{ transform: translateY(0px); }}
    50%       {{ transform: translateY(-8px); }}
}}
@keyframes bot-pulse-scale {{
    0%, 100% {{ transform: scale(1); }}
    50%       {{ transform: scale(1.12); }}
}}
@keyframes bot-bounce {{
    0%, 100% {{ transform: translateY(0); }}
    30%       {{ transform: translateY(-14px); }}
    60%       {{ transform: translateY(-6px); }}
}}
@keyframes bot-breathe {{
    0%, 100% {{ transform: scale(1); opacity: 1; }}
    50%       {{ transform: scale(1.06); opacity: 0.85; }}
}}
@keyframes bot-fire {{
    0%, 100% {{ transform: rotate(-3deg) scale(1); }}
    25%       {{ transform: rotate(3deg) scale(1.08); }}
    75%       {{ transform: rotate(-2deg) scale(1.05); }}
}}
@keyframes bot-shake {{
    0%, 100% {{ transform: translateX(0); }}
    20%       {{ transform: translateX(-5px); }}
    40%       {{ transform: translateX(5px); }}
    60%       {{ transform: translateX(-3px); }}
    80%       {{ transform: translateX(3px); }}
}}
@keyframes bot-celebrate {{
    0%   {{ transform: scale(1) rotate(0deg); }}
    20%  {{ transform: scale(1.2) rotate(-10deg); }}
    40%  {{ transform: scale(1.2) rotate(10deg); }}
    60%  {{ transform: scale(1.1) rotate(-5deg); }}
    80%  {{ transform: scale(1.1) rotate(5deg); }}
    100% {{ transform: scale(1) rotate(0deg); }}
}}
@keyframes bot-droop {{
    0%, 100% {{ transform: translateY(0) rotate(0deg); }}
    50%       {{ transform: translateY(6px) rotate(-5deg); }}
}}
</style>
""", unsafe_allow_html=True)


# ── UI components ─────────────────────────────────────────────────────────────

def page_header(title: str, subtitle: str = ""):
    c = get_colors()
    sub_html = (
        f'<div style="font-size:13px; color:{c["text_dim"]}; margin-top:4px; letter-spacing:0.5px;">'
        f'{subtitle}</div>'
    ) if subtitle else ""
    st.markdown(f"""
<div style="margin-bottom: 24px;">
    <div style="font-family:'Orbitron',monospace; font-size:22px; font-weight:900;
                color:{c['text']}; letter-spacing:4px; line-height:1.2;">
        {title}
    </div>
    {sub_html}
    <div style="height:2px; background:linear-gradient(90deg,{c['accent']},transparent);
                border-radius:2px; margin-top:10px;"></div>
</div>
""", unsafe_allow_html=True)


def section(title: str):
    c = get_colors()
    st.markdown(f"""
<div style="font-family:'Orbitron',monospace; font-size:11px; font-weight:700;
            letter-spacing:3px; color:{c['text_dim']}; margin:20px 0 10px 0;
            border-bottom:1px solid {c['card_border']}; padding-bottom:6px;">
    {title}
</div>
""", unsafe_allow_html=True)


def metric_card(label: str, value: str, subtitle: str = "", color: str = None):
    c = get_colors()
    val_color = color or c["text"]
    st.markdown(f"""
<div class="bot-card" style="text-align:center; padding:16px 12px;">
    <div style="font-family:'Orbitron',monospace; font-size:10px; letter-spacing:2px;
                color:{c['text_dim']}; margin-bottom:6px; text-transform:uppercase;">
        {label}
    </div>
    <div style="font-size:22px; font-weight:700; color:{val_color};
                font-family:'Orbitron',monospace; line-height:1.1; margin-bottom:4px;">
        {value}
    </div>
    <div style="font-size:11px; color:{c['text_dim']};">{subtitle}</div>
</div>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar():
    inject_css()
    with st.sidebar:
        c = get_colors()
        if os.path.exists(LOGO_PATH):
            with open(LOGO_PATH, "rb") as _f:
                st.image(_f.read(), use_container_width=True)

        st.markdown(f"""
        <div style="text-align:center; padding: 12px 0 8px 0;">
            <div style="font-family:'Orbitron',monospace; font-size:16px;
                        color:{c['accent']}; font-weight:900; letter-spacing:3px; line-height:1.3;">
                🐻 Mr Bera's_Bot
            </div>
            <div style="font-family:'Orbitron',monospace; font-size:9px;
                        color:{c['text_dim']}; letter-spacing:2px; margin-top:4px;">
                ICT · SMC · AI ENSEMBLE
            </div>
        </div>
        <hr style="border-color:{c['card_border']}; margin:8px 0 16px 0;">
        """, unsafe_allow_html=True)

        acct = get_account_data()
        if acct:
            st.markdown(f"""
            <div style="background:{c['bg3']}; border-radius:10px; padding:12px 14px; margin-bottom:10px;">
                <div style="font-size:10px; color:{c['text_dim']}; font-family:'Orbitron',monospace;
                            letter-spacing:1.5px; margin-bottom:6px;">ACCOUNT</div>
                <div style="font-size:18px; font-weight:700; color:{c['success']};
                            font-family:'Orbitron',monospace;">${acct['balance']:,.2f}</div>
                <div style="font-size:11px; color:{c['text_dim']}; margin-top:2px;">
                    {acct['currency']} · Equity ${acct['equity']:,.2f}
                </div>
            </div>
            <div style="display:flex; align-items:center; gap:8px; font-size:12px;
                        color:{c['success']}; margin-bottom:12px;">
                <span style="width:8px;height:8px;border-radius:50%;background:{c['success']};
                             box-shadow:0 0 8px {c['success']};display:inline-block;"></span>
                MT5 CONNECTED
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="display:flex; align-items:center; gap:8px; font-size:12px;'
                f' color:{c["danger"]}; margin-bottom:12px;">'
                f'<span style="width:8px;height:8px;border-radius:50%;background:{c["danger"]};'
                f' display:inline-block;"></span>'
                f' MT5 OFFLINE</div>',
                unsafe_allow_html=True,
            )

        st.markdown(f"""
        <div style="font-size:10px; color:{c['text_dim']}; margin-top:8px;
                    padding-top:12px; border-top:1px solid {c['card_border']};
                    text-align:center; letter-spacing:0.5px;">
            Mr Bera's Bot · Exness Demo<br>
            <span style="color:{c['accent']};">XGBoost + Random Forest + ICT</span>
        </div>
        """, unsafe_allow_html=True)
