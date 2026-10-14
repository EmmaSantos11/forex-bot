"""
dashboard_utils.py — Shared utilities for the Streamlit dashboard.
Themes, CSS injection, MT5 data helpers, and common UI components.
"""

import os
import json
import joblib
import MetaTrader5 as mt5
import pandas as pd
import streamlit as st
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

MODEL_DIR              = "models"
LOGS_DIR               = "logs"
MANUAL_PATH            = "data/manual_trade_features.csv"
MANAGED_ACCOUNTS_FILE  = "managed_accounts.json"
MAX_MANAGED_ACCOUNTS   = 5
MAX_BOT_USERS          = 5

# ── Color Themes ──────────────────────────────────────────────────────────────

THEMES = {
    "Midnight": {
        "bg":          "#0D1117",
        "bg2":         "#161B22",
        "bg3":         "#21262D",
        "accent":      "#00D4FF",
        "accent2":     "#7B2FFF",
        "text":        "#E6EDF3",
        "text_dim":    "#8B949E",
        "success":     "#3FB950",
        "warning":     "#F0883E",
        "danger":      "#F85149",
        "card_border": "rgba(0,212,255,0.15)",
        "glow":        "rgba(0,212,255,0.25)",
        "chart_grid":  "#21262D",
        "grid_line":   "rgba(255,255,255,0.015)",
    },
    "Matrix": {
        "bg":          "#020B02",
        "bg2":         "#071007",
        "bg3":         "#0D1A0D",
        "accent":      "#00FF41",
        "accent2":     "#00CC33",
        "text":        "#C8FFC8",
        "text_dim":    "#3A7A3A",
        "success":     "#00FF41",
        "warning":     "#FFFF00",
        "danger":      "#FF4444",
        "card_border": "rgba(0,255,65,0.2)",
        "glow":        "rgba(0,255,65,0.3)",
        "chart_grid":  "#0D1A0D",
        "grid_line":   "rgba(255,255,255,0.015)",
    },
    "Ember": {
        "bg":          "#0F0A00",
        "bg2":         "#1C1300",
        "bg3":         "#2A1E00",
        "accent":      "#FF6B35",
        "accent2":     "#FFB800",
        "text":        "#FFE8D0",
        "text_dim":    "#7A5C3A",
        "success":     "#52C47A",
        "warning":     "#FFB800",
        "danger":      "#FF4560",
        "card_border": "rgba(255,107,53,0.2)",
        "glow":        "rgba(255,107,53,0.3)",
        "chart_grid":  "#2A1E00",
        "grid_line":   "rgba(255,255,255,0.015)",
    },
    "Day": {
        "bg":          "#F5F7FA",
        "bg2":         "#FFFFFF",
        "bg3":         "#EDF0F5",
        "accent":      "#0078C8",
        "accent2":     "#7B2FFF",
        "text":        "#0D1117",
        "text_dim":    "#57606A",
        "success":     "#1A7F37",
        "warning":     "#B45309",
        "danger":      "#CF222E",
        "card_border": "rgba(0,120,200,0.18)",
        "glow":        "rgba(0,120,200,0.08)",
        "chart_grid":  "#DDE3EA",
        "grid_line":   "rgba(0,0,0,0.04)",
    },
}


def get_theme() -> str:
    return st.session_state.get("theme", "Midnight")


def get_colors() -> dict:
    return THEMES[get_theme()]


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── CSS Injection ─────────────────────────────────────────────────────────────

def inject_css():
    c = get_colors()
    grid = c.get("grid_line", "rgba(255,255,255,0.015)")
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=Inter:wght@300;400;500;600&display=swap');

    :root {{
        --bg: {c['bg']}; --bg2: {c['bg2']}; --bg3: {c['bg3']};
        --accent: {c['accent']}; --accent2: {c['accent2']};
        --text: {c['text']}; --text-dim: {c['text_dim']};
        --success: {c['success']}; --warning: {c['warning']}; --danger: {c['danger']};
        --card-border: {c['card_border']}; --glow: {c['glow']};
    }}

    .stApp {{
        background: var(--bg);
        background-image:
            linear-gradient({grid} 1px, transparent 1px),
            linear-gradient(90deg, {grid} 1px, transparent 1px);
        background-size: 50px 50px;
        color: var(--text);
        font-family: 'Inter', sans-serif;
    }}

    [data-testid="stSidebar"] {{
        background: var(--bg2) !important;
        border-right: 1px solid var(--card-border);
    }}
    [data-testid="stSidebar"] * {{ color: var(--text) !important; }}
    [data-testid="stSidebar"] .stSelectbox label {{ color: var(--text-dim) !important; font-size: 11px; }}

    h1,h2,h3 {{ font-family: 'Orbitron', monospace !important; color: var(--accent) !important; letter-spacing: 1px; }}

    .bot-card {{
        background: var(--bg2); border: 1px solid var(--card-border);
        border-radius: 14px; padding: 20px; margin: 8px 0;
        box-shadow: 0 0 24px var(--glow); transition: all 0.3s ease;
    }}
    .bot-card:hover {{ border-color: var(--accent); box-shadow: 0 0 36px var(--glow); transform: translateY(-2px); }}

    .metric-card {{
        background: var(--bg2); border: 1px solid var(--card-border);
        border-radius: 14px; padding: 18px 16px; text-align: center;
        box-shadow: 0 0 18px var(--glow); height: 100%;
    }}
    .metric-label {{
        font-size: 10px; color: var(--text-dim); text-transform: uppercase;
        letter-spacing: 2.5px; margin-bottom: 10px; font-family: 'Orbitron', monospace;
    }}
    .metric-value {{
        font-size: 26px; font-weight: 700; color: var(--accent);
        font-family: 'Orbitron', monospace; line-height: 1.1;
    }}
    .metric-sub {{ font-size: 11px; color: var(--text-dim); margin-top: 6px; }}

    .status-online {{
        display: inline-flex; align-items: center; gap: 8px;
        background: rgba(63,185,80,0.1); border: 1px solid rgba(63,185,80,0.4);
        border-radius: 20px; padding: 5px 14px; font-size: 12px;
        color: var(--success); font-family: 'Orbitron', monospace; font-weight: 600;
    }}
    .status-offline {{
        display: inline-flex; align-items: center; gap: 8px;
        background: rgba(248,81,73,0.1); border: 1px solid rgba(248,81,73,0.4);
        border-radius: 20px; padding: 5px 14px; font-size: 12px;
        color: var(--danger); font-family: 'Orbitron', monospace; font-weight: 600;
    }}
    .pulse {{
        width: 8px; height: 8px; border-radius: 50%; background: var(--success);
        display: inline-block; animation: pulse-anim 2s infinite;
    }}
    .pulse-red {{ width: 8px; height: 8px; border-radius: 50%; background: var(--danger); display: inline-block; }}
    @keyframes pulse-anim {{
        0%   {{ box-shadow: 0 0 0 0 rgba(63,185,80,0.7); }}
        70%  {{ box-shadow: 0 0 0 8px rgba(63,185,80,0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(63,185,80,0); }}
    }}

    .section-header {{
        font-family: 'Orbitron', monospace; font-size: 11px; letter-spacing: 3px;
        text-transform: uppercase; color: var(--text-dim);
        margin: 24px 0 12px 0; padding-bottom: 8px; border-bottom: 1px solid var(--card-border);
    }}

    .blog-card {{
        background: var(--bg2); border: 1px solid var(--card-border);
        border-radius: 16px; padding: 24px 24px 24px 28px; margin: 12px 0;
        transition: all 0.3s ease; position: relative; overflow: hidden;
    }}
    .blog-card::before {{
        content: ''; position: absolute; top: 0; left: 0;
        width: 4px; height: 100%; background: var(--accent);
    }}
    .blog-card:hover {{ border-color: var(--accent); transform: translateX(3px); box-shadow: 0 0 24px var(--glow); }}
    .blog-card.win::before     {{ background: var(--success); }}
    .blog-card.loss::before    {{ background: var(--danger); }}
    .blog-card.news::before    {{ background: var(--accent); }}
    .blog-card.retrain::before {{ background: var(--warning); }}
    .blog-card.daily::before   {{ background: var(--accent2); }}
    .blog-card.autopsy::before {{ background: #9D4EDD; }}

    .blog-tag {{
        display: inline-block; padding: 3px 12px; border-radius: 20px;
        font-size: 10px; font-weight: 700; letter-spacing: 1.5px;
        text-transform: uppercase; font-family: 'Orbitron', monospace; margin-bottom: 10px;
    }}
    .tag-win     {{ background: rgba(63,185,80,0.12);  color: var(--success); border: 1px solid rgba(63,185,80,0.3); }}
    .tag-loss    {{ background: rgba(248,81,73,0.12);  color: var(--danger);  border: 1px solid rgba(248,81,73,0.3); }}
    .tag-news    {{ background: rgba(0,212,255,0.08);  color: var(--accent);  border: 1px solid var(--card-border); }}
    .tag-retrain {{ background: rgba(240,136,62,0.12); color: var(--warning); border: 1px solid rgba(240,136,62,0.3); }}
    .tag-daily   {{ background: rgba(123,47,255,0.12); color: var(--accent2); border: 1px solid rgba(123,47,255,0.3); }}
    .tag-autopsy {{ background: rgba(157,78,221,0.12); color: #9D4EDD;        border: 1px solid rgba(157,78,221,0.3); }}

    .hero-section {{
        text-align: center; padding: 60px 30px;
        background: linear-gradient(135deg, var(--bg2) 0%, var(--bg3) 100%);
        border: 1px solid var(--card-border); border-radius: 20px;
        margin: 10px 0; box-shadow: 0 0 50px var(--glow);
    }}
    .cta-link {{
        display: inline-block; padding: 16px 44px; border-radius: 50px;
        font-family: 'Orbitron', monospace; font-weight: 700;
        font-size: 14px; letter-spacing: 2px; text-decoration: none !important;
        background: linear-gradient(135deg, var(--accent), var(--accent2));
        color: var(--bg) !important; box-shadow: 0 0 30px var(--glow);
        transition: all 0.3s ease; margin: 16px 0; display: inline-block;
    }}
    .cta-link:hover {{ transform: scale(1.04); box-shadow: 0 0 50px var(--glow); }}

    .recall-bar-wrap {{ background: var(--bg3); border-radius: 6px; height: 8px; overflow: hidden; }}
    .recall-bar      {{ height: 8px; border-radius: 6px; transition: width 0.6s ease; }}

    .stButton > button {{
        background: transparent; border: 1px solid var(--accent);
        color: var(--accent); border-radius: 8px;
        font-family: 'Orbitron', monospace; font-size: 11px;
        letter-spacing: 1px; transition: all 0.3s ease;
    }}
    .stButton > button:hover {{
        background: var(--accent); color: var(--bg); box-shadow: 0 0 20px var(--glow);
    }}

    div[data-testid="stDataFrame"] {{ border-radius: 10px; overflow: hidden; }}
    .stSelectbox > div > div {{ background: var(--bg2) !important; border-color: var(--card-border) !important; }}
    #MainMenu {{ visibility: hidden; }} footer {{ visibility: hidden; }}
    .stDeployButton {{ display: none; }}

    /* ── Bot mood animations ── */
    @keyframes bot-float       {{ 0%,100%{{transform:translateY(0) rotate(-2deg)}} 50%{{transform:translateY(-10px) rotate(2deg)}} }}
    @keyframes bot-bounce      {{ 0%{{transform:translateY(0)}} 20%{{transform:translateY(-16px)}} 40%{{transform:translateY(0)}} 60%{{transform:translateY(-8px)}} 80%{{transform:translateY(0)}} 100%{{transform:translateY(0)}} }}
    @keyframes bot-pulse-scale {{ 0%,100%{{transform:scale(1)}} 50%{{transform:scale(1.13)}} }}
    @keyframes bot-shake       {{ 0%,100%{{transform:translateX(0) rotate(0deg)}} 25%{{transform:translateX(-7px) rotate(-4deg)}} 75%{{transform:translateX(7px) rotate(4deg)}} }}
    @keyframes bot-celebrate   {{ 0%{{transform:scale(1) rotate(0deg)}} 25%{{transform:scale(1.2) rotate(-9deg)}} 50%{{transform:scale(1.2) rotate(9deg)}} 75%{{transform:scale(1.1) rotate(-4deg)}} 100%{{transform:scale(1) rotate(0deg)}} }}
    @keyframes bot-breathe     {{ 0%,100%{{transform:scale(1);opacity:0.85}} 50%{{transform:scale(1.05);opacity:1}} }}
    @keyframes bot-droop       {{ 0%,100%{{transform:translateY(0) rotate(0deg)}} 40%,60%{{transform:translateY(9px) rotate(-5deg)}} }}
    @keyframes bot-fire        {{ 0%,100%{{transform:scale(1) translateY(0)}} 50%{{transform:scale(1.16) translateY(-7px)}} }}

    /* ── Responsive layout ──────────────────────────────────────────────────── */

    /* ─ Phone — up to 768px ─────────────────────────────────────────────── */
    @media screen and (max-width: 768px) {{

        /* Full-width, edge-to-edge — no wasted side margins */
        .block-container {{
            padding: 0.5rem 0.6rem 2rem !important;
            max-width: 100vw !important;
        }}

        /* Typography */
        h1 {{ font-size: 20px !important; letter-spacing: 0.5px !important; }}
        h2, h3 {{ font-size: 14px !important; }}
        .section-header {{
            font-size: 9px !important; letter-spacing: 2px !important;
            margin: 14px 0 8px 0 !important;
        }}

        /* Cards */
        .metric-value {{ font-size: 20px !important; }}
        .metric-label {{ font-size: 9px !important; letter-spacing: 1.5px !important; }}
        .metric-card  {{ padding: 12px 10px !important; }}
        .bot-card     {{ padding: 14px 12px !important; margin: 5px 0 !important; }}
        .blog-card    {{ padding: 14px 14px 14px 18px !important; }}
        .hero-section {{ padding: 28px 16px !important; }}
        .cta-link     {{
            padding: 13px 26px !important; font-size: 12px !important;
            letter-spacing: 1.5px !important; display: block !important;
            text-align: center !important; margin: 12px 0 !important;
        }}

        /* Touch-friendly buttons (Apple / Google minimum = 44px) */
        .stButton > button {{
            min-height: 44px !important;
            padding: 10px 18px !important;
            font-size: 12px !important;
        }}

        /* Mood widget: wrap emoji + text on very narrow screens */
        .mood-widget {{ flex-wrap: wrap !important; gap: 12px !important; }}

        /* Tables: horizontal scroll instead of overflow-clip */
        div[data-testid="stDataFrame"] {{ overflow-x: auto !important; }}

        /* ── Column layout ───────────────────────────────────────────────── */
        /* All horizontal blocks wrap */
        [data-testid="stHorizontalBlock"] {{
            flex-wrap: wrap !important;
            gap: 6px !important;
        }}

        /* Default: EVERY column goes full-width and stacks vertically.
           This handles the main left/right body split correctly. */
        [data-testid="column"] {{
            flex: 1 1 100% !important;
            min-width: 100% !important;
            max-width: 100% !important;
        }}

        /* Exception: blocks with 3+ children (metrics ×6, scanner ×4, P&L ×3)
           → 2 per row so they form a compact grid instead of a long scroll */
        [data-testid="stHorizontalBlock"]:has(> [data-testid="column"]:nth-child(3)) > [data-testid="column"] {{
            flex: 1 1 calc(50% - 4px) !important;
            min-width: calc(50% - 4px) !important;
            max-width: calc(50% - 4px) !important;
        }}
    }}

    /* ─ Tablet / iPad — 769px to 1024px ─────────────────────────────────── */
    @media screen and (min-width: 769px) and (max-width: 1024px) {{

        .metric-value {{ font-size: 22px !important; }}
        .hero-section {{ padding: 40px 22px !important; }}
        .block-container {{ padding: 1.5rem 1.5rem !important; }}

        [data-testid="stHorizontalBlock"] {{
            flex-wrap: wrap !important;
            gap: 8px !important;
        }}

        /* 2-column body (left/right): stays side by side on tablet — enough room */

        /* 4-column scanner → 2 per row */
        [data-testid="stHorizontalBlock"]:has(> [data-testid="column"]:nth-child(4)) > [data-testid="column"] {{
            flex: 1 1 calc(50% - 6px) !important;
            min-width: calc(50% - 6px) !important;
        }}

        /* 5+ column blocks (6 metrics) → 3 per row */
        [data-testid="stHorizontalBlock"]:has(> [data-testid="column"]:nth-child(5)) > [data-testid="column"] {{
            flex: 1 1 calc(33.33% - 6px) !important;
            min-width: calc(33.33% - 6px) !important;
        }}
    }}

    /* ─ Desktop / laptop (1025px+): natural layout, no column overrides ─── */
    </style>
    """, unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────

LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "bot_logo.png")


def render_sidebar():
    inject_css()
    with st.sidebar:
        c = get_colors()
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, use_container_width=True)
        st.markdown(f"""
        <div style="text-align:center; padding: 12px 0 8px 0;">
            <div style="font-family:'Orbitron',monospace; font-size:16px;
                        color:{c['accent']}; font-weight:900; letter-spacing:3px; line-height:1.3;">
                🐻 Mr Bera's_Bot
            </div>
            <div style="font-family:'Orbitron',monospace; font-size:9px;
                        color:{c['text_dim']}; letter-spacing:4px; margin-top:3px;">
                SMARTER TRADES. BETTER RESULTS.
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f'<hr style="border:none; border-top:1px solid {c["card_border"]}; margin:0 0 16px 0;">', unsafe_allow_html=True)

        theme = st.selectbox(
            "🎨 Theme",
            options=list(THEMES.keys()),
            index=list(THEMES.keys()).index(get_theme()),
        )
        st.session_state["theme"] = theme

        st.markdown(f'<hr style="border:none;border-top:1px solid {c["card_border"]};margin:12px 0;">', unsafe_allow_html=True)

        acct = _get_account_raw()
        if acct:
            st.markdown(f"""
            <div class="status-online"><span class="pulse"></span> LIVE</div>
            <div style="margin-top:10px; font-size:13px; color:{c['text_dim']};">
                💰 ${acct.balance:,.2f} {acct.currency}
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown('<div class="status-offline"><span class="pulse-red"></span> MT5 OFFLINE</div>', unsafe_allow_html=True)

        st.markdown(f'<hr style="border:none;border-top:1px solid {c["card_border"]};margin:12px 0;">', unsafe_allow_html=True)

        now = datetime.now(timezone.utc)
        st.markdown(f"""
        <div style="font-size:11px; color:{c['text_dim']}; text-align:center; line-height:1.8;">
            🕐 {now.strftime('%H:%M:%S')} UTC<br>
            📅 {now.strftime('%a %d %b %Y')}
        </div>
        """, unsafe_allow_html=True)

        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()

        st.markdown(f'<hr style="border:none;border-top:1px solid {c["card_border"]};margin:12px 0;">', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="text-align:center; padding:8px 0;">
            <div style="font-family:'Orbitron',monospace; font-size:9px; letter-spacing:3px;
                        color:{c['text_dim']}; margin-bottom:8px;">CREATED BY</div>
            <a href="https://emmasantos11.github.io/My-Site/" target="_blank"
               style="font-family:'Orbitron',monospace; font-size:12px; font-weight:700;
                      color:{c['accent']}; text-decoration:none; letter-spacing:1px;">
                🐻 Mr Bera
            </a>
            <div style="font-size:10px; color:{c['text_dim']}; margin-top:4px; font-style:italic;">
                Here to Serve You
            </div>
        </div>
        """, unsafe_allow_html=True)


# ── Managed Accounts Helpers ──────────────────────────────────────────────────

def load_managed_accounts() -> list:
    if os.path.exists(MANAGED_ACCOUNTS_FILE):
        try:
            with open(MANAGED_ACCOUNTS_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_managed_accounts(accounts: list):
    with open(MANAGED_ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)


def check_admin_password(pwd: str) -> bool:
    admin_pwd = os.getenv("ADMIN_PASSWORD", "")
    return bool(admin_pwd) and pwd == admin_pwd


def load_bot_users() -> list:
    """Returns list of registered Telegram user IDs (from data/users.json)."""
    path = os.path.join("data", "users.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def remove_bot_user(chat_id: str) -> bool:
    """Removes a user from the bot user registry."""
    path = os.path.join("data", "users.json")
    try:
        users = load_bot_users()
        if chat_id in users:
            users.remove(chat_id)
            with open(path, "w") as f:
                json.dump(users, f)
            return True
    except Exception:
        pass
    return False


def fetch_mt5_account_stats(login: int, password: str, server: str) -> dict | None:
    """
    Temporarily logs into a managed MT5 account and returns live stats.
    Always restores the admin account connection afterward.
    """
    result = None
    try:
        if not mt5.initialize():
            return None
        if not mt5.login(login=login, password=password, server=server):
            return None
        info      = mt5.account_info()
        positions = mt5.positions_get() or []
        if info:
            open_pnl = sum(p.profit for p in positions) if positions else 0.0
            result   = {
                "balance":        round(info.balance, 2),
                "equity":         round(info.equity, 2),
                "profit":         round(info.profit, 2),
                "open_pnl":       round(open_pnl, 2),
                "open_positions": len(positions),
                "currency":       info.currency,
                "margin_level":   round(getattr(info, "margin_level", 0), 1),
            }
    except Exception:
        result = None
    finally:
        if LOGIN:
            try:
                mt5.login(login=LOGIN, password=PASSWORD, server=SERVER)
            except Exception:
                pass
    return result


# ── MT5 Helpers ───────────────────────────────────────────────────────────────

_MT5_EXE_PATHS = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
]

def _ensure_mt5() -> bool:
    if mt5.account_info() is not None:
        return True
    if mt5.initialize():
        if LOGIN:
            mt5.login(LOGIN, password=PASSWORD, server=SERVER)
        if mt5.account_info() is not None:
            return True
    for p in _MT5_EXE_PATHS:
        if os.path.isfile(p):
            if mt5.initialize(p, login=LOGIN, password=PASSWORD, server=SERVER):
                if mt5.account_info() is not None:
                    return True
    return False


def _get_account_raw():
    if not _ensure_mt5():
        return None
    return mt5.account_info()


@st.cache_data(ttl=15)
def get_account_data() -> dict | None:
    if not _ensure_mt5():
        return None
    a = mt5.account_info()
    if a is None:
        return None
    return {
        "balance":      a.balance,
        "equity":       a.equity,
        "profit":       a.profit,
        "margin":       a.margin,
        "free_margin":  a.margin_free,
        "leverage":     a.leverage,
        "currency":     a.currency,
        "server":       a.server,
        "name":         a.name,
    }


@st.cache_data(ttl=10)
def get_open_positions() -> list:
    if not _ensure_mt5():
        return []
    positions = mt5.positions_get() or []
    rows = []
    for p in positions:
        rows.append({
            "Pair":      p.symbol,
            "Type":      "BUY 🟢" if p.type == 0 else "SELL 🔴",
            "Lots":      p.volume,
            "Entry":     round(p.price_open, 5),
            "Current":   round(p.price_current, 5),
            "SL":        round(p.sl, 5) if p.sl else "—",
            "TP":        round(p.tp, 5) if p.tp else "—",
            "P&L ($)":   round(p.profit, 2),
            "Open":      datetime.fromtimestamp(p.time).strftime("%d %b %H:%M"),
            "Ticket":    p.ticket,
        })
    return rows


@st.cache_data(ttl=60)
def get_deal_history(days: int = 30) -> pd.DataFrame:
    if not _ensure_mt5():
        return pd.DataFrame()
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    deals = mt5.history_deals_get(since, now) or []
    rows  = []
    for d in deals:
        if d.entry == mt5.DEAL_ENTRY_OUT and d.symbol:
            # On DEAL_ENTRY_OUT: closing a SELL deal has d.type==DEAL_TYPE_BUY, closing a BUY has d.type==DEAL_TYPE_SELL
            orig_type = "SELL" if d.type == mt5.DEAL_TYPE_BUY else "BUY"
            rows.append({
                "time":    datetime.fromtimestamp(d.time),
                "symbol":  d.symbol,
                "type":    orig_type,
                "profit":  round(d.profit, 2),
                "volume":  d.volume,
                "price":   d.price,
                "ticket":  d.position_id,
                "comment": d.comment or "",
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"]    = df["time"].dt.date
    df["hour"]    = df["time"].dt.hour
    df["weekday"] = df["time"].dt.strftime("%a")
    df["win"]     = df["profit"] > 0
    return df


_TL_COLS = [
    "timestamp", "symbol", "type", "lots", "ai_confidence",
    "ict_score", "entry", "sl", "tp", "rrr", "ticket"
]


@st.cache_data(ttl=30)
def get_trade_log() -> pd.DataFrame:
    """Reads logs/trade_log.csv written by main.py."""
    log_path = os.path.join(os.path.dirname(__file__), "logs", "trade_log.csv")
    if not os.path.exists(log_path):
        return pd.DataFrame(columns=_TL_COLS)
    try:
        df = pd.read_csv(log_path, header=None, names=_TL_COLS)
        if str(df.iloc[0]["timestamp"]).lower() in ("timestamp", "time"):
            df = df.iloc[1:].reset_index(drop=True)
        df["timestamp"]     = pd.to_datetime(df["timestamp"], errors="coerce")
        df["ai_confidence"] = pd.to_numeric(df["ai_confidence"], errors="coerce")
        df["ict_score"]     = pd.to_numeric(df["ict_score"],     errors="coerce").fillna(0).astype(int)
        df["rrr"]           = pd.to_numeric(df["rrr"],           errors="coerce").fillna(3.0)
        df["ticket"]        = pd.to_numeric(df["ticket"],        errors="coerce").fillna(0).astype(int)
        return df.dropna(subset=["timestamp"])
    except Exception:
        return pd.DataFrame(columns=_TL_COLS)




# ── Model metadata ────────────────────────────────────────────────────────────

PAIRS = ["EURUSD", "USDCHF", "USDCAD", "NZDUSD", "EURJPY", "XAUUSD", "BTCUSD"]

def get_model_info() -> list:
    rows = []
    for pair in PAIRS:
        xgb_path = os.path.join(MODEL_DIR, f"xgb_{pair}.pkl")
        rf_path  = os.path.join(MODEL_DIR, f"rf_{pair}.pkl")
        xgb_ok   = os.path.exists(xgb_path)
        rf_ok    = os.path.exists(rf_path)
        mtime    = None
        if xgb_ok:
            mtime = datetime.fromtimestamp(os.path.getmtime(xgb_path))
        rows.append({
            "pair":       pair,
            "xgb":        xgb_ok,
            "rf":         rf_ok,
            "last_train": mtime,
        })
    return rows


# ── Common UI components ──────────────────────────────────────────────────────

def page_header(title: str, subtitle: str = ""):
    c = get_colors()
    sub_html = f'<p style="color:{c["text_dim"]};margin:4px 0 0 0;font-size:13px;">{subtitle}</p>' if subtitle else ""
    st.markdown(f"""
    <div style="margin-bottom:20px;">
        <h1 style="margin:0;font-size:26px;">{title}</h1>
        {sub_html}
    </div>
    """, unsafe_allow_html=True)


def metric_card(label: str, value: str, sub: str = "", color: str = ""):
    c   = get_colors()
    col = color if color else c["accent"]
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value" style="color:{col};">{value}</div>
        {f'<div class="metric-sub">{sub}</div>' if sub else ""}
    </div>
    """, unsafe_allow_html=True)


def section(title: str):
    st.markdown(f'<div class="section-header">{title}</div>', unsafe_allow_html=True)


def pnl_color(val: float) -> str:
    c = get_colors()
    if val > 0:  return c["success"]
    if val < 0:  return c["danger"]
    return c["text_dim"]


# ── Live signal scanner ───────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def get_signal_scan() -> dict:
    """Reads per-pair signal state written by main.py every 30 s."""
    path = os.path.join("logs", "signal_scan.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


@st.cache_data(ttl=60)
def get_news_events() -> list:
    """Reads upcoming HIGH-impact news events written by main.py."""
    path = os.path.join("logs", "news_events.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f).get("events", [])
    except Exception:
        return []


def get_consecutive_winning_days() -> int:
    """Number of consecutive profitable trading days ending today."""
    df = get_deal_history(days=90)
    if df.empty:
        return 0
    day_pnl = df.groupby("date")["profit"].sum().sort_index(ascending=False)
    streak = 0
    for pnl in day_pnl:
        if pnl > 0:
            streak += 1
        else:
            break
    return streak


def get_all_time_high() -> float:
    """Returns the stored all-time high balance (written by main.py)."""
    path = os.path.join("logs", "ath.json")
    if not os.path.exists(path):
        return 0.0
    try:
        with open(path) as f:
            return json.load(f).get("ath", 0.0)
    except Exception:
        return 0.0


# ── Bot mood ──────────────────────────────────────────────────────────────────

_MOOD_FILE = os.path.join("logs", "bot_mood.json")

_MOOD_STALE_MINS = 10   # if file is older than this, bot is offline


def _read_mood_file(now: datetime) -> dict | None:
    if not os.path.exists(_MOOD_FILE):
        return None
    try:
        with open(_MOOD_FILE) as f:
            data = json.load(f)
        updated_str = data.get("updated", "")
        if updated_str:
            up_dt = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_mins = (now - up_dt).total_seconds() / 60
            if age_mins > _MOOD_STALE_MINS:
                data["state"]   = "idle"
                data["message"] = "Bot is offline or paused."
                data["detail"]  = f"Last active {int(age_mins)}m ago"
        return data
    except Exception:
        return None


def get_bot_mood() -> dict:
    """
    Returns the bot's current mood state dict.
    Weekend / Sunday timing is handled here so the widget works even when
    main.py is not running.
    """
    now  = datetime.now(timezone.utc)
    dow  = now.weekday()   # 0=Mon … 5=Sat, 6=Sun
    hour = now.hour

    if dow == 5:   # Saturday — full rest
        return {
            "state":   "weekend_rest",
            "message": "Chilling for the weekend... markets are closed.",
            "detail":  "Back online Sunday evening at 21:00 UTC",
            "pair": "", "confidence": 0,
        }

    if dow == 6 and hour < 18:   # Sunday morning — still resting
        return {
            "state":   "weekend_rest",
            "message": "Still resting... waking up soon.",
            "detail":  f"Markets open in ~{21 - hour}h at 21:00 UTC",
            "pair": "", "confidence": 0,
        }

    if dow == 6 and 18 <= hour < 21:   # Sunday afternoon — prep window
        live = _read_mood_file(now)
        if live and live.get("state") in ("retraining", "week_prep", "retrain_prep"):
            return live
        return {
            "state":   "retrain_prep",
            "message": "Getting ready for the Sunday retrain...",
            "detail":  "Auto-retrain and model refresh starts at 21:00 UTC",
            "pair": "", "confidence": 0,
        }

    live = _read_mood_file(now)
    return live or {
        "state":   "idle",
        "message": "Bot is offline — start it with start_bot.bat",
        "detail":  "",
        "pair": "", "confidence": 0,
    }
