"""
About — Overview of Mr Bera's_Bot, the dashboard, strategy, and schedule.
"""

import os
import streamlit as st
from dashboard_utils import inject_css, render_sidebar, get_colors, PAIRS

st.set_page_config(page_title="About · Mr Bera's_Bot", page_icon="🐻", layout="wide")
render_sidebar()
inject_css()
c = get_colors()

_logo = os.path.join(os.path.dirname(__file__), "..", "assets", "bot_logo.png")

# ── Hero ───────────────────────────────────────────────────────────────────────

_, mid, _ = st.columns([1, 4, 1])
with mid:
    if os.path.exists(_logo):
        _lc1, _lc2, _lc3 = st.columns([1, 2, 1])
        with _lc2:
            st.image(_logo, use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)

    st.markdown(f"""
    <div style="text-align:center; margin-bottom:40px;">
        <div style="font-family:'Orbitron',monospace; font-size:10px; letter-spacing:5px;
                    color:{c['text_dim']}; margin-bottom:10px;">AUTOMATED FOREX INTELLIGENCE</div>
        <div style="font-family:'Orbitron',monospace; font-size:36px; font-weight:900;
                    color:{c['accent']}; letter-spacing:4px; line-height:1.2;">
            🐻 Mr Bera's_Bot
        </div>
        <div style="font-family:'Orbitron',monospace; font-size:11px; letter-spacing:6px;
                    color:{c['text_dim']}; margin-top:8px;">
            ICT · SMC · AI-POWERED TRADING SYSTEM
        </div>
    </div>
    """, unsafe_allow_html=True)

# ── What is Mr Bera's_Bot ──────────────────────────────────────────────────────

st.markdown(f"""
<div class="bot-card" style="margin-bottom:24px; padding:32px;">
    <div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
                color:{c['accent']}; letter-spacing:2px; margin-bottom:20px;">
        🤖 WHAT IS MR BERA'S_BOT?
    </div>
    <div style="font-size:15px; color:{c['text']}; line-height:1.9; max-width:820px;">
        Mr Bera's_Bot is a fully automated forex trading system that combines
        <strong style="color:{c['accent']};">Institutional Trading Concepts (ICT)</strong>,
        <strong style="color:{c['accent']};">Smart Money Concepts (SMC)</strong>, and a
        <strong style="color:{c['accent']};">machine learning ensemble</strong> (XGBoost + Random Forest)
        to identify and execute high-probability trade setups across 8 major currency pairs.
        <br><br>
        Unlike simple indicator-based bots, this system thinks in terms of institutional order flow —
        watching where liquidity pools sit, where large players leave footprints, and where price is
        likely to deliver next. The AI layer adds a second opinion before any trade fires, ensuring
        both the rule-based strategy and the machine learning model agree on the setup.
        <br><br>
        Every aspect — entry, stop loss, take profit, position sizing, news filtering, and risk
        management — runs without human intervention once the bot is started.
        <br><br>
        Every trade is also accompanied by a
        <strong style="color:{c['accent']};">live candlestick chart</strong> — automatically
        generated and posted to the Telegram channel at entry and again at exit — so followers
        can visually review the exact setup the bot acted on, see where entry, SL, and TP sat
        on the chart, and judge for themselves whether the trade made sense.
    </div>
</div>
""", unsafe_allow_html=True)

# ── Dashboard Pages ────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
            color:{c['accent']}; letter-spacing:2px; margin:32px 0 20px 0;">
    📊 DASHBOARD PAGES
</div>
""", unsafe_allow_html=True)

pages = [
    ("🏠", "Home",
     "The mission control centre. Real-time account balance, equity, open P&L, and margin. "
     "Live signal scanner showing what the bot is looking at right now — confidence scores, "
     "ICT confluence factors, and whether the AI agrees. Bot mood indicator shows the system's "
     "current state (scanning, watching a setup, in a trade, resting on weekends). "
     "Active positions displayed with live P&L updates."),

    ("📈", "Performance",
     "Full trade history and analytics. Cumulative equity curve, win rate, average R:R, "
     "drawdown metrics, and per-pair breakdowns. Compare performance across time periods and "
     "see exactly how each strategy component has contributed to results. All data pulled "
     "live from MetaTrader 5 deal history."),

    ("🧠", "AI Training",
     "Manage and monitor the machine learning models. View the last retrain date, accuracy "
     "metrics, and feature importances for each of the 8 pair models. Manually trigger a "
     "retrain with pair selection and live log output — models re-learn from the latest "
     "trade data every Sunday automatically, or any time you kick one off here."),

    ("📰", "Blog",
     "Auto-generated posts built from real trade events. Win/loss recaps with market context, "
     "news impact summaries, AI confidence updates, and retrain milestones. Each post has its "
     "own personality so the feed stays interesting. Think of it as the bot's trading journal — "
     "written by the bot, for you."),

    ("🤝", "Trade With Us",
     "Connect via Telegram to follow along with every trade in real time. Get alerts on every "
     "entry, exit, and P&L update as it happens. This page also previews upcoming managed "
     "account services and custom strategy configuration — coming soon."),

    ("🐻", "About",
     "You are here. Full system overview, strategy explanation, schedule, and tech stack."),
]

cols_a, cols_b = st.columns(2)
for i, (icon, name, desc) in enumerate(pages):
    col = cols_a if i % 2 == 0 else cols_b
    with col:
        st.markdown(f"""
        <div class="bot-card" style="padding:24px; margin-bottom:16px; min-height:160px;">
            <div style="font-family:'Orbitron',monospace; font-size:12px; font-weight:700;
                        color:{c['accent']}; letter-spacing:1px; margin-bottom:12px;">
                {icon} &nbsp; {name.upper()}
            </div>
            <div style="font-size:13px; color:{c['text_dim']}; line-height:1.7;">{desc}</div>
        </div>
        """, unsafe_allow_html=True)

# ── Trading Strategy ───────────────────────────────────────────────────────────

st.markdown(f"""
<div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
            color:{c['accent']}; letter-spacing:2px; margin:32px 0 20px 0;">
    📐 TRADING STRATEGY
</div>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="bot-card" style="padding:32px; margin-bottom:16px;">
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:32px;">
        <div>
            <div style="font-family:'Orbitron',monospace; font-size:11px; font-weight:700;
                        color:{c['accent']}; letter-spacing:2px; margin-bottom:14px;">
                MULTI-TIMEFRAME STRUCTURE
            </div>
            <div style="font-size:13px; color:{c['text_dim']}; line-height:1.8;">
                <strong style="color:{c['text']};">4H chart</strong> — defines the macro market structure.
                The bot identifies the prevailing trend, swing highs/lows, and key liquidity zones
                at this level first. Nothing trades against the 4H bias.<br><br>
                <strong style="color:{c['text']};">1H chart</strong> — finds the precise entry zone.
                Order blocks (institutional candles before a large move), fair value gaps
                (imbalances price will return to fill), and breaker blocks are identified here.
                The Optimal Trade Entry (OTE) sits in the 0.618–0.786 Fibonacci retracement of the
                most recent impulse leg.<br><br>
                <strong style="color:{c['text']};">5M chart</strong> — entry trigger. A Break of
                Structure (BOS) on the 5M in the direction of the 4H bias, combined with
                displacement candles confirming institutional delivery, triggers the entry signal.
            </div>
        </div>
        <div>
            <div style="font-family:'Orbitron',monospace; font-size:11px; font-weight:700;
                        color:{c['accent']}; letter-spacing:2px; margin-bottom:14px;">
                20-POINT CONFLUENCE SCORING
            </div>
            <div style="font-size:13px; color:{c['text_dim']}; line-height:1.8;">
                Every potential setup is scored across 16 factors before the AI gets involved:
                <br><br>
                Structure alignment · Higher-timeframe OTE · Order block presence ·
                Fair value gap · Breaker block · Session timing (London/NY overlap) ·
                Spread filter · ATR momentum · Engulfing candle · Wick rejection ·
                Higher-timeframe FVG · Kill zone (08:00–11:00 / 13:00–16:00 UTC) ·
                CISD (Change in State of Delivery) · Displacement candles ·
                Golden Zone (0.618 ± 5% on 4H) · Asian range breakout
                <br><br>
                A setup must reach a minimum score threshold <em>and</em> have both the
                XGBoost and Random Forest models predict a win before a trade is placed.
            </div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Risk Management ────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
            color:{c['accent']}; letter-spacing:2px; margin:32px 0 20px 0;">
    🛡️ RISK MANAGEMENT
</div>
""", unsafe_allow_html=True)

risk_items = [
    ("Position Sizing",     "Each trade risks a fixed percentage of account equity. "
                            "Position size is calculated from the ATR-based stop loss distance so "
                            "every trade risks the same dollar amount regardless of pair or volatility."),
    ("ATR Stop Loss",       "Stop loss is placed beyond the recent swing structure, sized using the "
                            "Average True Range. This keeps stops at technically meaningful levels "
                            "rather than arbitrary pip distances."),
    ("Daily Loss Cap",      "If the bot loses more than the configured daily limit, it stops trading "
                            "for the rest of that day. Prevents a bad session from becoming a "
                            "catastrophic drawdown."),
    ("3-Hour Cooldown",     "After a losing trade, the bot waits 3 hours before taking the next "
                            "position. Forces a pause and prevents revenge-trading sequences."),
    ("Breakeven Automation","Once a trade reaches a target profit level (e.g. 1R), the stop loss "
                            "automatically moves to breakeven. The trade can no longer be a loss."),
    ("News Blackout",       "30 minutes before any HIGH-impact economic event (NFP, FOMC, CPI etc.), "
                            "the bot stops opening new trades. It reads the economic calendar live "
                            "to know when events are scheduled."),
    ("Scaling With Balance","Position sizing thresholds scale up automatically as the account "
                            "reaches equity milestones — controlled growth rather than flat lot sizes."),
    ("Max Spread Filter",   "Trades are skipped when the spread is abnormally wide (e.g. during "
                            "illiquid Asian session spikes). Protects against slippage on entries."),
]

r1, r2 = st.columns(2)
for i, (title, body) in enumerate(risk_items):
    col = r1 if i % 2 == 0 else r2
    with col:
        st.markdown(f"""
        <div class="bot-card" style="padding:20px; margin-bottom:14px;">
            <div style="font-family:'Orbitron',monospace; font-size:11px; font-weight:700;
                        color:{c['success']}; letter-spacing:1px; margin-bottom:8px;">
                ✅ &nbsp; {title.upper()}
            </div>
            <div style="font-size:13px; color:{c['text_dim']}; line-height:1.7;">{body}</div>
        </div>
        """, unsafe_allow_html=True)

# ── Pairs & Schedule ───────────────────────────────────────────────────────────

sched_col, pairs_col = st.columns([3, 2])

with sched_col:
    st.markdown(f"""
    <div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
                color:{c['accent']}; letter-spacing:2px; margin:32px 0 20px 0;">
        🗓️ BOT SCHEDULE
    </div>
    <div class="bot-card" style="padding:28px;">
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <tr style="border-bottom:1px solid {c['card_border']};">
                <td style="padding:10px 0; color:{c['accent']}; font-family:'Orbitron',monospace;
                            font-size:11px; width:160px;">MON – FRI</td>
                <td style="padding:10px 0; color:{c['text']};">
                    Active scanning during London (07:00–12:00 UTC) and New York
                    (13:00–18:00 UTC) sessions. Overnight positions may be held.
                </td>
            </tr>
            <tr style="border-bottom:1px solid {c['card_border']};">
                <td style="padding:10px 0; color:{c['accent']}; font-family:'Orbitron',monospace;
                            font-size:11px;">FRIDAY 22:00</td>
                <td style="padding:10px 0; color:{c['text']};">
                    Market closes. Bot stops opening new positions.
                    All positions managed until closed.
                </td>
            </tr>
            <tr style="border-bottom:1px solid {c['card_border']};">
                <td style="padding:10px 0; color:{c['accent']}; font-family:'Orbitron',monospace;
                            font-size:11px;">SATURDAY</td>
                <td style="padding:10px 0; color:{c['text']};">
                    Rest day. Bot is offline. Dashboard shows weekend mode.
                </td>
            </tr>
            <tr style="border-bottom:1px solid {c['card_border']};">
                <td style="padding:10px 0; color:{c['accent']}; font-family:'Orbitron',monospace;
                            font-size:11px;">SUNDAY 18:00</td>
                <td style="padding:10px 0; color:{c['text']};">
                    Weekly model retrain begins. AI models are updated with the
                    previous week's trade data and new market conditions.
                </td>
            </tr>
            <tr>
                <td style="padding:10px 0; color:{c['accent']}; font-family:'Orbitron',monospace;
                            font-size:11px;">SUNDAY 21:00</td>
                <td style="padding:10px 0; color:{c['text']};">
                    Market reopens (Sydney session). Bot resumes full operation
                    with freshly trained models.
                </td>
            </tr>
        </table>
    </div>
    """, unsafe_allow_html=True)

with pairs_col:
    st.markdown(
        f'<div style="font-family:Orbitron,monospace; font-size:13px; font-weight:700;'
        f' color:{c["accent"]}; letter-spacing:2px; margin:32px 0 20px 0;">💱 TRADED PAIRS</div>',
        unsafe_allow_html=True,
    )

    _PAIR_DESCRIPTIONS = {
        "EURUSD": "Euro / US Dollar",
        "USDCHF": "US Dollar / Swiss Franc",
        "USDCAD": "US Dollar / Canadian",
        "NZDUSD": "Kiwi / US Dollar",
        "AUDUSD": "Aussie / US Dollar",
        "EURJPY": "Euro / Yen",
        "GBPUSD": "Pound / US Dollar",
        "USDJPY": "US Dollar / Yen",
        "XAUUSD": "Gold / US Dollar",
        "BTCUSD": "Bitcoin / US Dollar",
    }

    with st.container():
        for sym in PAIRS:
            desc = _PAIR_DESCRIPTIONS.get(sym, sym)
            st.markdown(
                f'<div style="display:flex; align-items:center; justify-content:space-between;'
                f' padding:10px 0; border-bottom:1px solid {c["card_border"]};">'
                f'<div style="display:flex; align-items:center; gap:10px;">'
                f'<div style="width:7px;height:7px;border-radius:50%;background:{c["accent"]};flex-shrink:0;"></div>'
                f'<span style="font-family:Orbitron,monospace;font-size:12px;'
                f'color:{c["text"]};letter-spacing:1px;">{sym}</span>'
                f'</div>'
                f'<span style="font-size:11px;color:{c["text_dim"]};">{desc}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

# ── Tech Stack ────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
            color:{c['accent']}; letter-spacing:2px; margin:32px 0 20px 0;">
    ⚙️ TECHNOLOGY STACK
</div>
""", unsafe_allow_html=True)

tech_cols = st.columns(4)
tech = [
    ("🐍", "Python 3.11",        "Core runtime — strategy logic, ML pipeline, trade execution"),
    ("📊", "MetaTrader 5",        "Live market data feed and order execution via MT5 Python API"),
    ("🤖", "XGBoost + RF",        "Gradient boosted trees + random forest ensemble for signal filtering"),
    ("📐", "ICT / SMC",           "Institutional trading concepts: order blocks, FVGs, BOS, OTE, displacement"),
    ("📱", "Telegram Bot API",    "Real-time trade alerts, P&L notifications, and candlestick setup charts sent to channel"),
    ("🌐", "Streamlit",           "Live dashboard UI — this interface you're looking at right now"),
    ("📈", "pandas / NumPy",      "Data wrangling, OHLCV processing, and technical calculations"),
    ("💾", "joblib / JSON",       "Model persistence and lightweight state management between cycles"),
    ("📉", "matplotlib",          "TradingView-style candlestick charts auto-generated on every trade entry and exit"),
]
for i, (icon, name, desc) in enumerate(tech):
    with tech_cols[i % 4]:
        st.markdown(f"""
        <div class="bot-card" style="padding:20px; margin-bottom:14px; text-align:center;">
            <div style="font-size:28px; margin-bottom:10px;">{icon}</div>
            <div style="font-family:'Orbitron',monospace; font-size:10px; font-weight:700;
                        color:{c['accent']}; letter-spacing:1px; margin-bottom:8px;">{name}</div>
            <div style="font-size:11px; color:{c['text_dim']}; line-height:1.6;">{desc}</div>
        </div>
        """, unsafe_allow_html=True)

# ── Go Live Timeline ──────────────────────────────────────────────────────────

st.markdown(f"""
<div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
            color:{c['accent']}; letter-spacing:2px; margin:32px 0 20px 0;">
    🚀 GO-LIVE ROADMAP
</div>
""", unsafe_allow_html=True)

phases = [
    ("✅", c['success'],  "PHASE 1 · 2",
     "Foundation & Strategy",
     "Multi-timeframe ICT/SMC engine (4H/1H/5M), 16-point confluence scoring, order blocks, "
     "FVGs, BOS, OTE zones, liquidity sweeps. Full risk suite: ATR stops, partial close at 1R, "
     "daily loss cap, correlation filter, news blackout, spread filter. Complete."),
    ("✅", c['success'],  "PHASE 3 · 4",
     "AI Engine · Live Demo",
     "XGBoost + Random Forest ensemble, per-pair models (8 pairs), Sunday auto-retrain blending "
     "live trade data at 3× weight. Running live on $5,000 Exness demo. Models improve "
     "every Sunday. Telegram alerts with visual setup charts, weekly reports, crash recovery. Complete."),
    ("🔲", c['warning'],  "PHASE 5",
     "Real Account",
     "After 6-month demo validation (target: 50+ trades, positive profit factor, controlled "
     "drawdown under 10%). Migrate to real capital with prop firm discipline. "
     "Stricter daily limits, verified live-spread lot sizing."),
    ("⏳", c['text_dim'], "PHASE 6",
     "Managed Service",
     "Telegram channel for live signal subscribers, custom strategy configuration, "
     "performance-based managed account service. Fixed ngrok domain for permanent dashboard URL. "
     "Accepting early followers now — link in sidebar."),
]

_pc1, _pc2, _pc3, _pc4 = st.columns(4, gap="small")
_phase_cols = [_pc1, _pc2, _pc3, _pc4]
for i, (check, color, phase_label, phase_title, phase_body) in enumerate(phases):
    with _phase_cols[i]:
        st.markdown(f"""
        <div style="border:1px solid {color}44; border-radius:12px; padding:20px;
                    background:{c['bg2']}; height:100%; box-sizing:border-box;">
            <div style="font-size:24px; margin-bottom:10px;">{check}</div>
            <div style="font-family:'Orbitron',monospace; font-size:9px; letter-spacing:3px;
                        color:{color}; margin-bottom:6px;">{phase_label}</div>
            <div style="font-family:'Orbitron',monospace; font-size:11px; font-weight:700;
                        color:{c['text']}; margin-bottom:10px;">{phase_title}</div>
            <div style="font-size:11px; color:{c['text_dim']}; line-height:1.7;">{phase_body}</div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Disclaimer ────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="margin-top:16px; padding:24px; background:{c['bg2']};
            border:1px solid {c['warning']}33; border-radius:16px;">
    <div style="font-family:'Orbitron',monospace; font-size:10px; letter-spacing:3px;
                color:{c['warning']}; margin-bottom:12px;">⚠️ DISCLAIMER</div>
    <div style="font-size:12px; color:{c['text_dim']}; line-height:1.8;">
        <strong style="color:{c['text']};">Trading forex involves significant risk and is not
        suitable for all investors.</strong> Past performance of this bot — whether on demo or live
        accounts — does not guarantee future results. Automated systems can and do experience
        losses, including drawdown periods where multiple consecutive trades lose. You should never
        trade with money you cannot afford to lose. This dashboard is for informational and
        educational purposes. Always conduct your own due diligence before committing real capital
        to any trading system.
        <br><br>
        Mr Bera's_Bot is a personal project under active development. All results shown reflect
        demo account performance unless explicitly stated otherwise.
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
