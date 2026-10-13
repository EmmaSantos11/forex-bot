"""
streamlit_app.py — Apex Forex AI Bot Dashboard (Home)
Run with: streamlit run streamlit_app.py
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timezone
from streamlit_autorefresh import st_autorefresh

from dashboard_utils import (
    inject_css, render_sidebar, page_header, metric_card, section,
    get_account_data, get_open_positions, get_deal_history,
    get_colors, pnl_color, hex_to_rgba,
    get_signal_scan, get_news_events,
    get_consecutive_winning_days, get_all_time_high,
    get_bot_mood, PAIRS,
)

st.set_page_config(
    page_title="Mr Bera's_Bot",
    page_icon="🐻",
    layout="wide",
    initial_sidebar_state="expanded",
)

def _market_is_open() -> bool:
    now = datetime.now(timezone.utc)
    dow, h = now.weekday(), now.hour
    if dow == 5: return False           # Saturday — always closed
    if dow == 6 and h < 21: return False  # Sunday before 21:00 UTC
    if dow == 4 and h >= 22: return False  # Friday after 22:00 UTC
    return True

render_sidebar()
inject_css()
c = get_colors()

st_autorefresh(interval=15_000, key="home_refresh")

# ── Header ────────────────────────────────────────────────────────────────────
import os as _os
_logo = _os.path.join(_os.path.dirname(__file__), "assets", "bot_logo.png")
if _os.path.exists(_logo):
    _hcol1, _hcol2, _hcol3 = st.columns([1, 2, 1])
    with _hcol2:
        st.image(_logo, use_container_width=True)
    st.markdown("<br>", unsafe_allow_html=True)

page_header("DASHBOARD", "Live account overview · updates every 15 seconds")

# ── Top metrics ───────────────────────────────────────────────────────────────
acct       = get_account_data()
positions  = get_open_positions()
hist_today = get_deal_history(days=1)
hist_7     = get_deal_history(days=7)
hist_30    = get_deal_history(days=30)

daily_pnl   = round(hist_today["profit"].sum(), 2) if not hist_today.empty else 0.0
weekly_pnl  = round(hist_7["profit"].sum(),     2) if not hist_7.empty    else 0.0
monthly_pnl = round(hist_30["profit"].sum(),    2) if not hist_30.empty   else 0.0

win_streak = get_consecutive_winning_days()
ath        = get_all_time_high()

col1, col2, col3, col4, col5, col6 = st.columns(6)
if acct:
    with col1:
        metric_card("Balance", f"${acct['balance']:,.2f}", acct["currency"])
    with col2:
        eq_diff = acct["equity"] - acct["balance"]
        eq_col  = c["success"] if eq_diff >= 0 else c["danger"]
        metric_card("Equity", f"${acct['equity']:,.2f}",
                    f"{'▲' if eq_diff >= 0 else '▼'} ${abs(eq_diff):.2f} floating", eq_col)
    with col3:
        metric_card("Open Trades", str(len(positions)),
                    f"{'Max 2/pair/day' if positions else 'No open positions'}")
    with col4:
        today_w = len(hist_today[hist_today["profit"] > 0]) if not hist_today.empty else 0
        today_l = len(hist_today[hist_today["profit"] <= 0]) if not hist_today.empty else 0
        metric_card("Today's Trades", f"{today_w}W / {today_l}L",
                    f"{len(hist_today)} closed today" if not hist_today.empty else "None today")
    with col5:
        streak_col = c["success"] if win_streak >= 3 else (c["warning"] if win_streak >= 1 else c["text_dim"])
        streak_lbl = f"{win_streak}d streak" if win_streak >= 2 else ("Today ✅" if win_streak == 1 else "No streak")
        metric_card("Win Streak", streak_lbl, f"ATH ${ath:,.0f}" if ath > 0 else "No ATH yet", streak_col)
    with col6:
        pf_trades = len(hist_7)
        pf_wins   = len(hist_7[hist_7["profit"] > 0]) if not hist_7.empty else 0
        wr7       = round(pf_wins / pf_trades * 100, 1) if pf_trades > 0 else 0
        metric_card("7-Day Win Rate", f"{wr7}%",
                    f"{pf_wins}W / {pf_trades - pf_wins}L this week",
                    c["success"] if wr7 >= 50 else (c["warning"] if wr7 >= 35 else c["danger"]))
else:
    for col in [col1, col2, col3, col4, col5, col6]:
        with col:
            metric_card("—", "—", "MT5 offline")

# ── P&L Summary row ───────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
_pd, _pw, _pm = st.columns(3, gap="medium")

def _pnl_card(col, label, value, subtitle):
    sign   = "+" if value >= 0 else ""
    colour = c["success"] if value > 0 else (c["danger"] if value < 0 else c["text_dim"])
    arrow  = "▲" if value > 0 else ("▼" if value < 0 else "—")
    with col:
        st.markdown(f"""
        <div class="bot-card" style="text-align:center; padding:20px 12px;">
            <div style="font-family:'Orbitron',monospace; font-size:11px;
                        color:{c['text_dim']}; letter-spacing:2px; margin-bottom:6px;">
                {label}
            </div>
            <div style="font-size:28px; font-weight:700; color:{colour};
                        font-family:'Orbitron',monospace; line-height:1.1;">
                {arrow} {sign}${abs(value):,.2f}
            </div>
            <div style="font-size:11px; color:{c['text_dim']}; margin-top:4px;">{subtitle}</div>
        </div>
        """, unsafe_allow_html=True)

_pnl_card(_pd, "DAILY P&L",   daily_pnl,   "closed trades today")
_pnl_card(_pw, "WEEKLY P&L",  weekly_pnl,  "last 7 days")
_pnl_card(_pm, "MONTHLY P&L", monthly_pnl, "last 30 days")

st.markdown("<br>", unsafe_allow_html=True)

# ── Bot mood widget ───────────────────────────────────────────────────────────
_MOODS = {
    "weekend_rest": ("😴", "💤", "💤", "WEEKEND REST",        "#7986CB", "bot-float 4s ease-in-out infinite"),
    "retrain_prep": ("🤔", "📚", "📖", "SUNDAY PREP",          "#FF9800", "bot-pulse-scale 2s ease-in-out infinite"),
    "retraining":   ("🧮", "📚", "🔢", "RETRAINING AI",        "#9C27B0", "bot-pulse-scale 1.2s ease-in-out infinite"),
    "week_prep":    ("💪", "🎯", "🚀", "READY FOR THE WEEK",   "#4CAF50", "bot-bounce 1.5s ease-in-out infinite"),
    "ready":        ("👁️", "⚡", "📡", "SCANNING MARKETS",    "#00BCD4", "bot-breathe 3s ease-in-out infinite"),
    "high_conf":    ("😤", "🔥", "🔥", "HIGH CONFIDENCE",     "#FF5722", "bot-fire 0.8s ease-in-out infinite"),
    "unsure":       ("🤔", "❓", "💭", "UNCERTAIN",            "#FFC107", "bot-shake 3s ease-in-out infinite"),
    "watching":     ("👀", "⚡", "🎯", "SETUP DETECTED",       "#2196F3", "bot-pulse-scale 1s ease-in-out infinite"),
    "won":          ("🎉", "😄", "🏆", "WINNER!",              "#4CAF50", "bot-celebrate 0.8s ease-in-out infinite"),
    "lost":         ("😔", "💪", "😤", "TOUGH LOSS",           "#F44336", "bot-droop 2s ease-in-out infinite"),
    "loss_guard":   ("🛡️", "⛔", "🔒", "PROTECTING ACCOUNT",  "#9E9E9E", "bot-breathe 2s ease-in-out infinite"),
    "idle":         ("🧘", "✨", "✨", "PATIENT",              "#607D8B", "bot-breathe 4s ease-in-out infinite"),
}

_mood      = get_bot_mood()
_mstate    = _mood.get("state", "idle")
_mmsg      = _mood.get("message", "")
_mdetail   = _mood.get("detail", "")
_mpair     = _mood.get("pair", "")
_mconf     = _mood.get("confidence", 0)
_mpnl      = _mood.get("pnl")
_me, _ml, _mr, _mlabel, _mcol, _manim = _MOODS.get(_mstate, _MOODS["idle"])
_mh        = _mcol.lstrip("#")
_mrgb      = f"{int(_mh[0:2],16)},{int(_mh[2:4],16)},{int(_mh[4:6],16)}"

_right_html = ""
if _mpair:
    _right_html += f'<div style="font-family:Orbitron,monospace;font-size:15px;color:{_mcol};font-weight:700;margin-bottom:4px;">{_mpair}</div>'
if _mconf > 0:
    _right_html += (
        f'<div style="font-size:22px;font-weight:800;color:{_mcol};'
        f'font-family:Orbitron,monospace;">{_mconf:.0%}</div>'
        f'<div style="font-size:10px;color:#888;letter-spacing:1px;">CONFIDENCE</div>'
    )
if _mpnl is not None:
    _pc = c["success"] if _mpnl >= 0 else c["danger"]
    _sign = "+" if _mpnl >= 0 else ""
    _right_html += f'<div style="font-size:18px;font-weight:700;color:{_pc};margin-top:6px;font-family:Orbitron,monospace;">{_sign}${_mpnl:.2f}</div>'

_right_block  = f'<div style="text-align:center;min-width:100px;padding-left:12px;">{_right_html}</div>' if _right_html else ""
_divider_side = f'<div style="width:1px;height:64px;background:rgba({_mrgb},0.25);border-radius:2px;margin:0 4px;"></div>' if _right_html else ""

st.markdown(f"""
<div class="mood-widget" style="
    background:linear-gradient(135deg,rgba({_mrgb},0.07) 0%,{c['bg2']} 70%);
    border:1px solid rgba({_mrgb},0.4);
    border-radius:16px; padding:18px 26px; margin:0 0 16px 0;
    box-shadow:0 0 40px rgba({_mrgb},0.12);
    display:flex; align-items:center; gap:22px;
">
    <div style="text-align:center;min-width:88px;">
        <div style="font-size:18px;margin-bottom:4px;opacity:0.85;">{_ml}&nbsp;&nbsp;{_mr}</div>
        <div style="font-size:52px;line-height:1;display:inline-block;
                    animation:{_manim};
                    filter:drop-shadow(0 0 14px rgba({_mrgb},0.55));">{_me}</div>
    </div>
    <div style="width:1px;height:68px;background:rgba({_mrgb},0.3);border-radius:2px;"></div>
    <div style="flex:1;min-width:0;">
        <div style="font-family:'Orbitron',monospace;font-size:10px;letter-spacing:3px;
                    color:{_mcol};font-weight:700;margin-bottom:6px;">{_mlabel}</div>
        <div style="font-size:14px;color:{c['text']};font-weight:500;margin-bottom:3px;line-height:1.4;">{_mmsg}</div>
        <div style="font-size:12px;color:{c['text_dim']};">{_mdetail}</div>
    </div>
    {_divider_side}{_right_block}
</div>
""", unsafe_allow_html=True)

# ── Main layout ───────────────────────────────────────────────────────────────
left, right = st.columns([6, 4], gap="medium")

# ── LEFT: Positions + Recent trades ──────────────────────────────────────────
with left:
    section("⚡ Open Positions")
    if positions:
        df_pos = pd.DataFrame(positions)
        def _style_pnl(val):
            if isinstance(val, float):
                clr = c["success"] if val > 0 else (c["danger"] if val < 0 else c["text_dim"])
                return f"color: {clr}; font-weight: 600"
            return ""
        styled = df_pos.drop(columns=["Ticket"]).style.map(_style_pnl, subset=["P&L ($)"])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        _closed_msg = "Market closed — see you at Sunday 21:00 UTC 😴" if not _market_is_open() else "No open positions — bot is scanning for setups"
        _closed_icon = "🌙" if not _market_is_open() else "📭"
        st.markdown(f"""
        <div class="bot-card" style="text-align:center; padding:36px 20px; color:{c['text_dim']};">
            <div style="font-size:36px; margin-bottom:10px;">{_closed_icon}</div>
            {_closed_msg}
        </div>
        """, unsafe_allow_html=True)

    # ── Signal Scanner ────────────────────────────────────────────────────────
    section("🎯 Live Signal Scanner")

    _HEAT_MAP = {
        "TRADED":      ("🔥", c["success"],  "TRADED"),
        "TRIGGER":     ("🎯", c["success"],  "TRIGGER READY"),
        "VALUE_TEST":  ("💎", c["accent"],   "VALUE RETEST"),
        "TRENDING":    ("📡", c["accent2"],  "TREND ACTIVE"),
        "WEAK_ICT":    ("🧱", c["warning"],  "WEAK ICT"),
        "NO_BOS":      ("⛔", c["warning"],  "NO BOS"),
        "LOW_CONF":    ("⚡", c["warning"],  "LOW CONF"),
        "WATCHING":    ("⚡", c["accent"],   "WATCHING"),
        "HOLDING":     ("🤝", c["accent"],   "HOLDING"),
        "NEWS":        ("📰", c["warning"],  "NEWS BLOCK"),
        "COOLDOWN":    ("🛑", c["danger"],   "COOLDOWN"),
        "CORRELATED":  ("🔗", c["text_dim"], "CORRELATED"),
        "OFF_SESSION":  ("😴", c["text_dim"], "OFF SESSION"),
        "DROUGHT_WAIT": ("🏜️", c["text_dim"], "DROUGHT — WAITING FOR LONDON"),
        "NOT_TODAY":    ("🚫", c["text_dim"], "NOT ACTIVE TODAY"),
        "RANGING":     ("📊", c["text_dim"], "RANGING CHOP"),
        "SPREAD":      ("⏳", c["text_dim"], "WIDE SPREAD"),
        "MAX_RISK":    ("⛔", c["danger"],   "MAX RISK"),
        "DAILY_LIMIT": ("⛔", c["danger"],   "DAILY LIMIT"),
        "LOSS_GUARD":  ("🛡️", c["danger"],   "LOSS GUARD"),
        "PENDING":     ("⏳", c["accent2"],  "PENDING"),
        "FAILED":      ("❌", c["danger"],   "FAILED"),
        "WEEKEND":     ("🌙", c["text_dim"], "WEEKEND"),
    }
    # Statuses that mean this pair is actively in scope for today's session
    _ACTIVE_STATUSES = {"WATCHING", "PENDING", "LOW_CONF", "WEAK_ICT", "NO_BOS", "HOLDING",
                        "TRIGGER", "VALUE_TEST", "TRENDING",
                        "TRADED", "COOLDOWN", "NEWS", "CORRELATED",
                        "SPREAD", "MAX_RISK", "DAILY_LIMIT", "LOSS_GUARD", "FAILED",
                        "DROUGHT_WAIT"}
    _PAIRS_ORDER = PAIRS  # single source of truth — edit dashboard_utils.PAIRS to add/remove

    if not _market_is_open():
        now_utc = datetime.now(timezone.utc)
        dow_now = now_utc.weekday()
        reopen_msg = "Opens tonight at 21:00 UTC" if dow_now == 6 else "Opens Sunday at 21:00 UTC"
        st.markdown(
            f'<div class="bot-card" style="text-align:center; padding:28px 20px; color:{c["text_dim"]};">'
            f'<div style="font-size:32px; margin-bottom:10px;">🌙</div>'
            f'<div style="font-family:Orbitron,monospace; font-size:11px; letter-spacing:3px;'
            f' color:{c["accent"]}; margin-bottom:8px;">SCANNER OFFLINE</div>'
            f'<div style="font-size:13px;">Market is closed. Scanner resumes at open.</div>'
            f'<div style="font-size:12px; margin-top:6px; color:{c["accent2"]};">{reopen_msg}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        scan = get_signal_scan()
        scan_pairs   = scan.get("pairs", {})
        scan_updated = scan.get("updated", "")

        if scan_pairs:
            sc1, sc2, sc3, sc4 = st.columns(4)
            _scan_cols = [sc1, sc2, sc3, sc4, sc1, sc2, sc3, sc4]
            for i, pair in enumerate(_PAIRS_ORDER):
                info    = scan_pairs.get(pair, {})
                status  = info.get("status", "WATCHING")
                signal  = info.get("signal", 0)
                conf    = info.get("confidence", 0.0)
                adx_val = info.get("adx_4h", 0.0)
                bias    = info.get("macro_bias", "")
                heat, heat_col, heat_lbl = _HEAT_MAP.get(status, ("⚡", c["text_dim"], status))

                is_not_today = status == "NOT_TODAY"
                is_active    = status in _ACTIVE_STATUSES

                # Confidence / signal display differs by active state
                if is_not_today:
                    sig_lbl  = "—"
                    conf_pct = "NOT ACTIVE TODAY"
                    bar_w    = 0
                    bar_col  = c["text_dim"]
                elif signal == 1:
                    sig_lbl  = "🟢 BUY (1:3.5 RRR)"
                    conf_pct = f"{conf:.0%}"
                    bar_w    = int(conf * 100)
                    bar_col  = c["success"]
                elif signal == -1:
                    sig_lbl  = "🔴 SELL (1:3.5 RRR)"
                    conf_pct = f"{conf:.0%}"
                    bar_w    = int(conf * 100)
                    bar_col  = c["success"]
                elif status == "VALUE_TEST":
                    sig_lbl  = f"💎 {bias} RETEST"
                    conf_pct = f"{conf:.0%}" if conf > 0 else "—"
                    bar_w    = int(conf * 100)
                    bar_col  = c["accent"]
                elif status == "TRENDING":
                    sig_lbl  = f"📡 {bias} TREND"
                    conf_pct = f"{conf:.0%}" if conf > 0 else "—"
                    bar_w    = int(conf * 100)
                    bar_col  = c["accent2"]
                elif status == "RANGING":
                    sig_lbl  = "📊 RANGING CHOP"
                    conf_pct = "—"
                    bar_w    = 0
                    bar_col  = c["text_dim"]
                else:
                    sig_lbl  = "—"
                    conf_pct = f"{conf:.0%}" if conf > 0 else "—"
                    bar_w    = int(conf * 100)
                    bar_col  = c["success"] if conf >= 0.70 else (c["warning"] if conf >= 0.50 else c["danger"])

                # Macro context tag
                macro_sub = f'<div style="font-size:10px; color:{c["text_dim"]}; margin-top:4px;">4H ADX: {adx_val:.1f} · {bias}</div>' if adx_val > 0 else ""

                # ACTIVE badge shown on pairs that are in scope for today
                active_badge = (
                    f'<span style="font-size:9px; font-weight:700; color:{c["success"]};'
                    f' background:rgba(63,185,80,0.12); border-radius:4px;'
                    f' padding:2px 5px; margin-left:4px; letter-spacing:1px;">● ACTIVE</span>'
                    if is_active else ""
                )

                with _scan_cols[i]:
                    st.markdown(
                        f'<div class="bot-card" style="padding:14px 16px; margin-bottom:8px;'
                        f' opacity:{"0.45" if is_not_today else "1"};">'
                        f'<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">'
                        f'<span style="font-family:Orbitron,monospace; font-weight:700;'
                        f' font-size:13px; color:{c["text"]};">{pair}{active_badge}</span>'
                        f'<span style="font-size:11px; font-weight:700; color:{heat_col};">{heat} {heat_lbl}</span>'
                        f'</div>'
                        f'<div style="display:flex; justify-content:space-between; font-size:11px;'
                        f' color:{c["text_dim"]}; margin-bottom:8px;">'
                        f'<span>{sig_lbl}</span>'
                        f'<span style="color:{bar_col}; font-weight:600;">{conf_pct}</span>'
                        f'</div>'
                        f'<div class="recall-bar-wrap">'
                        f'<div class="recall-bar" style="width:{bar_w}%; background:{bar_col};"></div>'
                        f'</div>'
                        f'{macro_sub}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            if scan_updated:
                st.markdown(
                    f'<div style="font-size:10px; color:{c["text_dim"]}; text-align:right; margin-top:-4px;">'
                    f'Scanner updated: {scan_updated} UTC</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f'<div style="color:{c["text_dim"]}; font-size:13px; padding:10px 0;">'
                f'Signal scanner activates when the bot is running (main.py).</div>',
                unsafe_allow_html=True,
            )

    section("📋 Recent Closed Trades (7 days)")
    if not hist_7.empty:
        recent = hist_7.sort_values("time", ascending=False).head(12)
        for _, row in recent.iterrows():
            pnl   = row["profit"]
            col   = pnl_color(pnl)
            sign  = "+" if pnl >= 0 else ""
            emoji = "✅" if pnl > 0 else ("⚖️" if pnl == 0 else "❌")
            st.markdown(f"""
            <div style="display:flex; align-items:center; justify-content:space-between;
                        padding:10px 16px; background:{c['bg2']}; border-radius:10px;
                        margin:4px 0; border:1px solid {c['card_border']};">
                <span style="font-weight:600; min-width:120px;">{emoji} {row['symbol']}</span>
                <span style="color:{c['text_dim']}; font-size:13px; min-width:60px;">{row['type']}</span>
                <span style="color:{c['text_dim']}; font-size:12px; min-width:100px;">
                    {row['time'].strftime('%d %b %H:%M')}
                </span>
                <span style="color:{col}; font-weight:700; font-family:'Orbitron',monospace; font-size:14px;">
                    {sign}${abs(pnl):.2f}
                </span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="color:{c["text_dim"]}; padding:20px 0;">No closed trades in the last 7 days.</div>', unsafe_allow_html=True)

# ── RIGHT: Equity chart + sessions + pair breakdown ───────────────────────────
with right:
    section("📈 Equity Curve (30 days)")
    hist_30 = get_deal_history(days=30)
    if not hist_30.empty and acct:
        df_eq = hist_30.sort_values("time").copy()
        start = acct["balance"] - df_eq["profit"].sum()
        df_eq["cumulative"] = start + df_eq["profit"].cumsum()
        fill_color = hex_to_rgba(c["accent"], 0.08)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_eq["time"], y=df_eq["cumulative"],
            mode="lines", line=dict(color=c["accent"], width=2.5),
            fill="tozeroy", fillcolor=fill_color,
            hovertemplate="<b>$%{y:,.2f}</b><br>%{x|%d %b %H:%M}<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=4, b=0), height=190,
            xaxis=dict(showgrid=False, color=c["text_dim"], tickfont=dict(size=10)),
            yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                       tickformat="$,.0f", tickfont=dict(size=10)),
            showlegend=False, hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown(f'<div style="color:{c["text_dim"]}; padding:20px 0; font-size:13px;">No history yet — equity curve will appear after first trades close.</div>', unsafe_allow_html=True)

    section("💱 Today by Pair")
    if not hist_today.empty:
        by_pair = hist_today.groupby("symbol")["profit"].sum().reset_index().sort_values("profit")
        bar_colors = [c["success"] if v >= 0 else c["danger"] for v in by_pair["profit"]]
        fig2 = go.Figure(go.Bar(
            x=by_pair["profit"], y=by_pair["symbol"],
            orientation="h", marker_color=bar_colors,
            text=[f"${v:+.2f}" for v in by_pair["profit"]],
            textposition="outside", textfont=dict(color=c["text"], size=11),
        ))
        fig2.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=40, t=4, b=0), height=180,
            xaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                       zeroline=True, zerolinecolor=c["card_border"]),
            yaxis=dict(showgrid=False, color=c["text"]),
            showlegend=False,
        )
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown(f'<div style="color:{c["text_dim"]}; font-size:13px; padding:10px 0;">No trades closed today yet.</div>', unsafe_allow_html=True)

    section("🌏 Trading Sessions (UTC)")
    now_utc      = datetime.now(timezone.utc)
    hour         = now_utc.hour
    _mkt_open    = _market_is_open()
    SESSIONS = {
        "Sydney 🇦🇺":  (22, 7),
        "Tokyo 🇯🇵":   (0, 9),
        "London 🇬🇧":  (7, 16),
        "New York 🗽": (13, 22),
    }
    for name, (s, e) in SESSIONS.items():
        _time_active = (hour >= s or hour < e) if s > e else (s <= hour < e)
        active = _time_active and _mkt_open   # never show ACTIVE when market is closed
        dot    = c["success"] if active else c["bg3"]
        label  = "ACTIVE 🔥" if active else ("CLOSED 🌙" if not _mkt_open else f"{s:02d}:00 – {e:02d}:00")
        txt    = c["text"] if active else c["text_dim"]
        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:10px; padding:7px 0;
                    border-bottom:1px solid {c['card_border']};">
            <div style="width:8px;height:8px;border-radius:50%;background:{dot};flex-shrink:0;
                        {'box-shadow:0 0 8px ' + c['success'] if active else ''}"></div>
            <span style="color:{txt}; font-size:13px; flex:1;">{name}</span>
            <span style="color:{c['text_dim']}; font-size:11px; font-family:'Orbitron',monospace;">{label}</span>
        </div>
        """, unsafe_allow_html=True)
    if not _mkt_open:
        st.markdown(f'<div style="font-size:11px;color:{c["text_dim"]};margin-top:6px;text-align:center;">All sessions resume Sunday 21:00 UTC</div>', unsafe_allow_html=True)

    # ── News Countdown (Feature 2) ────────────────────────────────────────────
    section("📅 Economic Calendar")
    news_ev = get_news_events()
    if news_ev:
        for ev in news_ev[:8]:
            mins  = ev.get("mins_away", 0)
            title = ev.get("title", "")
            country = ev.get("country", "")
            forecast = ev.get("forecast", "—")
            previous = ev.get("previous", "—")
            actual   = ev.get("actual", "")

            if mins < 0:
                timing_lbl  = f"Released {int(abs(mins))}m ago"
                border_col  = c["text_dim"]
                dot_col     = c["text_dim"]
            elif mins <= 30:
                timing_lbl  = f"⚠️ in {int(mins)}m — BOT PAUSED"
                border_col  = c["danger"]
                dot_col     = c["danger"]
            elif mins <= 120:
                timing_lbl  = f"in {int(mins)}m"
                border_col  = c["warning"]
                dot_col     = c["warning"]
            else:
                h = int(mins // 60)
                timing_lbl  = f"in {h}h {int(mins % 60)}m"
                border_col  = c["card_border"]
                dot_col     = c["accent"]

            actual_html = f" · <b>Actual: {actual}</b>" if actual else ""
            st.markdown(f"""
            <div style="padding:8px 12px; margin:4px 0; border-radius:8px;
                        border:1px solid {border_col}; background:{c['bg2']};">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-weight:600; font-size:12px; color:{c['text']};">
                        <span style="color:{dot_col}; font-size:10px;">●</span>
                        &nbsp;{country} — {title[:30]}{'…' if len(title)>30 else ''}
                    </span>
                    <span style="font-size:10px; color:{dot_col};
                                 font-family:'Orbitron',monospace;">{timing_lbl}</span>
                </div>
                <div style="font-size:10px; color:{c['text_dim']}; margin-top:3px;">
                    Forecast: {forecast} · Prev: {previous}{actual_html}
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="color:{c["text_dim"]}; font-size:12px; padding:8px 0;">No HIGH-impact events in the next 8 hours · calendar updates when bot runs.</div>', unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
st.markdown(f"""
<div style="text-align:right; font-size:11px; color:{c['text_dim']};">
    🐻 Mr Bera's_Bot &nbsp;·&nbsp; Last updated: {datetime.now().strftime('%H:%M:%S')}
</div>
""", unsafe_allow_html=True)
