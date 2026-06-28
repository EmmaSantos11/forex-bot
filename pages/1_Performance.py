"""
Performance analytics — equity curve, win rate, P&L heatmap, drawdown,
session breakdown, AI confidence analysis, streak stats, full trade history.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

from dashboard_utils import (
    inject_css, render_sidebar, page_header, metric_card, section,
    get_account_data, get_deal_history, get_colors, pnl_color, hex_to_rgba,
)

st.set_page_config(page_title="Performance · Mr Bera's Bot", page_icon="📊", layout="wide")
render_sidebar()
inject_css()
c = get_colors()

st_autorefresh(interval=30_000, key="perf_refresh")

page_header("📊 PERFORMANCE", "Strategy analytics · win rate · drawdown · pair breakdown")

# ── Helpers ───────────────────────────────────────────────────────────────────

_TL_COLS = ["timestamp", "symbol", "type", "lots", "ai_confidence",
            "ict_score", "entry", "sl", "tp", "rrr", "ticket"]


@st.cache_data(ttl=60)
def _load_trade_log() -> pd.DataFrame:
    try:
        df = pd.read_csv("logs/trade_log.csv", header=None, names=_TL_COLS)
        if str(df.iloc[0]["timestamp"]).lower() in ("timestamp", "time"):
            df = df.iloc[1:].reset_index(drop=True)
        df["timestamp"]     = pd.to_datetime(df["timestamp"], errors="coerce")
        df["ai_confidence"] = pd.to_numeric(df["ai_confidence"], errors="coerce")
        df["ict_score"]     = pd.to_numeric(df["ict_score"],     errors="coerce").fillna(0).astype(int)
        df["rrr"]           = pd.to_numeric(df["rrr"],           errors="coerce").fillna(3)
        df["ticket"]        = pd.to_numeric(df["ticket"],        errors="coerce").fillna(0).astype(int)
        return df.dropna(subset=["timestamp"])
    except Exception:
        return pd.DataFrame(columns=_TL_COLS)


def _enrich(df: pd.DataFrame, tl: pd.DataFrame) -> pd.DataFrame:
    if tl.empty or df.empty:
        return df
    slim = tl[["ticket", "ai_confidence", "ict_score", "entry", "sl", "tp", "rrr"]].copy()
    return df.merge(slim, on="ticket", how="left")


def _session_label(hour: int) -> str:
    if 1  <= hour < 3:  return "Asian"
    if 7  <= hour < 12: return "London"
    if 13 <= hour < 18: return "New York"
    return "Off-Session"


def _sharpe(df: pd.DataFrame, balance: float) -> float:
    if len(df) < 2 or balance <= 0:
        return 0.0
    daily = df.groupby("date")["profit"].sum()
    if len(daily) < 2:
        return 0.0
    r = daily / balance
    return float(r.mean() / r.std() * (252 ** 0.5)) if r.std() > 0 else 0.0


def _max_streak(win_series: list) -> tuple:
    max_w = max_l = cur_w = cur_l = 0
    for w in win_series:
        if w:
            cur_w += 1; cur_l = 0
            max_w = max(max_w, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_l = max(max_l, cur_l)
    return max_w, max_l


# ── P&L Snapshot ──────────────────────────────────────────────────────────────
_h1  = get_deal_history(days=1)
_h3  = get_deal_history(days=3)
_h7  = get_deal_history(days=7)
_h30 = get_deal_history(days=30)
_dpnl  = round(_h1["profit"].sum(),  2) if not _h1.empty  else 0.0
_3pnl  = round(_h3["profit"].sum(),  2) if not _h3.empty  else 0.0
_wpnl  = round(_h7["profit"].sum(),  2) if not _h7.empty  else 0.0
_mpnl  = round(_h30["profit"].sum(), 2) if not _h30.empty else 0.0

_s0, _s1, _s2, _s3 = st.columns(4, gap="medium")
for _sc, _label, _val, _sub in [
    (_s0, "TODAY'S P&L",  _dpnl, f"{len(_h1)} trade(s) closed today"),
    (_s1, "3-DAY P&L",    _3pnl, f"{len(_h3)} trade(s) · last 3 days"),
    (_s2, "WEEKLY P&L",   _wpnl, f"{len(_h7)} trade(s) · 7 days"),
    (_s3, "MONTHLY P&L",  _mpnl, f"{len(_h30)} trade(s) · 30 days"),
]:
    _sign  = "+" if _val >= 0 else ""
    _vcol  = c["success"] if _val > 0 else (c["danger"] if _val < 0 else c["text_dim"])
    _arrow = "▲" if _val > 0 else ("▼" if _val < 0 else "—")
    with _sc:
        st.markdown(
            f'<div class="bot-card" style="text-align:center; padding:20px 12px;">'
            f'<div style="font-family:Orbitron,monospace; font-size:11px;'
            f' color:{c["text_dim"]}; letter-spacing:2px; margin-bottom:6px;">{_label}</div>'
            f'<div style="font-size:24px; font-weight:700; color:{_vcol};'
            f' font-family:Orbitron,monospace; line-height:1.1;">'
            f'{_arrow} {_sign}${abs(_val):,.2f}</div>'
            f'<div style="font-size:11px; color:{c["text_dim"]}; margin-top:4px;">{_sub}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)

# ── Period selector ───────────────────────────────────────────────────────────
col_r1, _ = st.columns([2, 8])
with col_r1:
    days = st.selectbox(
        "Period", [1, 3, 7, 14, 30, 60, 90, 180], index=2,
        format_func=lambda d: f"Last {d} day{'s' if d > 1 else ''}",
    )

df   = get_deal_history(days=days)
acct = get_account_data()
tl   = _load_trade_log()
df   = _enrich(df, tl)

if df.empty:
    st.markdown(f"""
    <div class="bot-card" style="text-align:center; padding:60px; color:{c['text_dim']};">
        <div style="font-size:48px; margin-bottom:16px;">📭</div>
        <div style="font-size:18px;">No trade history found for this period.</div>
        <div style="font-size:13px; margin-top:8px;">The bot needs to close some trades first.</div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ── Core calculations ─────────────────────────────────────────────────────────
wins       = df[df["win"] == True]
losses     = df[df["win"] == False]
win_rate   = len(wins) / len(df) * 100 if len(df) > 0 else 0
net_pnl    = df["profit"].sum()
gross_win  = wins["profit"].sum()
gross_loss = abs(losses["profit"].sum())
pf         = gross_win / gross_loss if gross_loss > 0 else float("inf")
avg_win    = wins["profit"].mean()  if not wins.empty   else 0.0
avg_loss   = losses["profit"].mean() if not losses.empty else 0.0
start_bal  = (acct["balance"] if acct else 5000) - net_pnl
sharpe     = _sharpe(df, start_bal)
avg_rr     = float(df["rrr"].mean()) if "rrr" in df.columns and df["rrr"].notna().any() else 0.0
edge       = net_pnl / len(df) if len(df) > 0 else 0.0
max_w, max_l = _max_streak(df.sort_values("time")["win"].tolist())

# ── Metrics row 1 ─────────────────────────────────────────────────────────────
mc1, mc2, mc3, mc4 = st.columns(4)
with mc1: metric_card("Total Trades",   str(len(df)),               f"{len(wins)}W / {len(losses)}L")
with mc2: metric_card("Win Rate",       f"{win_rate:.1f}%",         "of closed trades",
                      c["success"] if win_rate >= 40 else c["warning"])
with mc3: metric_card("Profit Factor",  f"{pf:.2f}" if pf != float("inf") else "∞",
                      "gross win / gross loss",
                      c["success"] if pf >= 1.2 else (c["warning"] if pf >= 1.0 else c["danger"]))
with mc4:
    sign = "+" if net_pnl >= 0 else ""
    metric_card("Net P&L", f"{sign}${net_pnl:,.2f}", f"last {days} days", pnl_color(net_pnl))

st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

# ── Metrics row 2 ─────────────────────────────────────────────────────────────
mc5, mc6, mc7, mc8 = st.columns(4)
with mc5: metric_card("Avg Win",      f"+${avg_win:.2f}",  "per winning trade",   c["success"])
with mc6: metric_card("Avg Loss",     f"${avg_loss:.2f}",  "per losing trade",    c["danger"])
with mc7:
    sharpe_col = c["success"] if sharpe >= 1.0 else (c["warning"] if sharpe >= 0 else c["danger"])
    metric_card("Sharpe Ratio", f"{sharpe:.2f}", "annualised (252-day)", sharpe_col)
with mc8:
    rr_col = c["success"] if avg_rr >= 2.0 else (c["warning"] if avg_rr >= 1.0 else c["danger"])
    metric_card("Avg RR", f"1 : {avg_rr:.1f}" if avg_rr > 0 else "—",
                "risk / reward at entry", rr_col)

st.markdown("<br>", unsafe_allow_html=True)

# ── Equity curve + drawdown ───────────────────────────────────────────────────
section("📈 Equity Curve & Drawdown")
df_sorted = df.sort_values("time").copy()
df_sorted["equity"]   = start_bal + df_sorted["profit"].cumsum()
df_sorted["peak"]     = df_sorted["equity"].cummax()
df_sorted["drawdown"] = (df_sorted["peak"] - df_sorted["equity"]) / df_sorted["peak"] * 100
max_dd = df_sorted["drawdown"].max()

fig_eq = go.Figure()
fig_eq.add_trace(go.Scatter(
    x=df_sorted["time"], y=df_sorted["equity"],
    name="Equity", mode="lines",
    line=dict(color=c["accent"], width=2.5),
    fill="tozeroy", fillcolor=hex_to_rgba(c["accent"], 0.07),
    hovertemplate="<b>$%{y:,.2f}</b><extra>Equity</extra>",
))
fig_eq.add_trace(go.Scatter(
    x=df_sorted["time"], y=-df_sorted["drawdown"],
    name="Drawdown %", mode="lines",
    line=dict(color=c["danger"], width=1.5, dash="dot"),
    yaxis="y2",
    hovertemplate="<b>%{y:.2f}%</b><extra>Drawdown</extra>",
))
fig_eq.update_layout(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    height=300, margin=dict(l=0, r=60, t=10, b=0),
    xaxis=dict(showgrid=False, color=c["text_dim"]),
    yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
               tickformat="$,.0f", title="Equity"),
    yaxis2=dict(overlaying="y", side="right", showgrid=False,
                color=c["danger"], title="DD %", ticksuffix="%"),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=c["text_dim"])),
    hovermode="x unified",
    annotations=[dict(
        x=0.01, y=0.97, xref="paper", yref="paper",
        text=f"Max DD: {max_dd:.2f}%",
        font=dict(color=c["danger"], size=12, family="Orbitron"),
        showarrow=False,
    )],
)
st.plotly_chart(fig_eq, use_container_width=True, config={"displayModeBar": False})

# ── Per-pair breakdown ────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
left, right = st.columns(2, gap="medium")

with left:
    section("💱 Win Rate by Pair")
    pair_stats = df.groupby("symbol").agg(
        trades=("profit", "count"),
        wins=("win", "sum"),
    ).reset_index()
    pair_stats["win_rate"] = pair_stats["wins"] / pair_stats["trades"] * 100
    pair_stats = pair_stats.sort_values("win_rate", ascending=True)
    fig_wr = go.Figure(go.Bar(
        x=pair_stats["win_rate"], y=pair_stats["symbol"],
        orientation="h",
        marker_color=[c["success"] if v >= 40 else (c["warning"] if v >= 25 else c["danger"])
                      for v in pair_stats["win_rate"]],
        text=[f"{v:.1f}%" for v in pair_stats["win_rate"]],
        textposition="outside", textfont=dict(color=c["text"], size=11),
    ))
    fig_wr.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=40, t=0, b=0), height=280,
        xaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                   range=[0, 115], ticksuffix="%"),
        yaxis=dict(showgrid=False, color=c["text"]),
        showlegend=False,
    )
    st.plotly_chart(fig_wr, use_container_width=True, config={"displayModeBar": False})

with right:
    section("💰 Net P&L by Pair")
    pair_pnl = df.groupby("symbol")["profit"].sum().reset_index().sort_values("profit", ascending=True)
    fig_pp = go.Figure(go.Bar(
        x=pair_pnl["profit"], y=pair_pnl["symbol"],
        orientation="h",
        marker_color=[c["success"] if v >= 0 else c["danger"] for v in pair_pnl["profit"]],
        text=[f"${v:+.2f}" for v in pair_pnl["profit"]],
        textposition="outside", textfont=dict(color=c["text"], size=11),
    ))
    fig_pp.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=50, t=0, b=0), height=280,
        xaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                   zeroline=True, zerolinecolor=c["card_border"], tickprefix="$"),
        yaxis=dict(showgrid=False, color=c["text"]),
        showlegend=False,
    )
    st.plotly_chart(fig_pp, use_container_width=True, config={"displayModeBar": False})

# ── Daily trade activity ──────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
section("📅 Daily Trade Activity")

_filter_opts = {"1D": 1, "3D": 3, "7D": 7, "14D": 14, "30D": 30}
_fkey    = st.radio("View", list(_filter_opts.keys()), index=2,
                    horizontal=True, label_visibility="collapsed", key="daily_act_filter")
df_act   = get_deal_history(days=_filter_opts[_fkey])

if not df_act.empty:
    df_act = df_act.copy()
    df_act["date_only"] = pd.to_datetime(df_act["time"]).dt.date
    daily = df_act.groupby("date_only").agg(
        wins   = ("win", "sum"),
        losses = ("win", lambda x: (~x).sum()),
        total  = ("win", "count"),
        pnl    = ("profit", "sum"),
    ).reset_index().sort_values("date_only")
    daily["date_str"] = daily["date_only"].astype(str)

    fig_da = go.Figure()
    fig_da.add_trace(go.Bar(
        name="Wins", x=daily["date_str"], y=daily["wins"],
        marker_color=c["success"],
        text=daily["wins"], textposition="inside",
        textfont=dict(color="#000", size=11, family="Orbitron"),
    ))
    fig_da.add_trace(go.Bar(
        name="Losses", x=daily["date_str"], y=daily["losses"],
        marker_color=c["danger"],
        text=daily["losses"], textposition="inside",
        textfont=dict(color="#fff", size=11, family="Orbitron"),
    ))
    fig_da.update_layout(
        barmode="stack",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=230, margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(showgrid=False, color=c["text_dim"]),
        yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                   title="Trades", dtick=1),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=c["text_dim"]),
                    orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
    )
    st.plotly_chart(fig_da, use_container_width=True, config={"displayModeBar": False})

    daily_disp = daily[["date_str", "total", "wins", "losses", "pnl"]].copy()
    daily_disp.columns = ["Date", "Trades", "Wins", "Losses", "Net P&L"]
    daily_disp["Net P&L"]  = daily_disp["Net P&L"].map(lambda v: f"${v:+.2f}")
    daily_disp["Win Rate"] = (daily["wins"] / daily["total"] * 100).map(lambda v: f"{v:.0f}%")
    daily_disp = daily_disp[["Date", "Trades", "Wins", "Losses", "Win Rate", "Net P&L"]]
    st.dataframe(daily_disp.sort_values("Date", ascending=False),
                 use_container_width=True, hide_index=True)
else:
    st.markdown(
        f'<div style="color:{c["text_dim"]};font-size:13px;padding:12px 0;">'
        f'No trades in this window yet.</div>',
        unsafe_allow_html=True,
    )

# ── P&L Heatmap ───────────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
section("🗓️ P&L Heatmap — Hour vs Day of Week")
if len(df) >= 10:
    hm = df.pivot_table(values="profit", index="weekday", columns="hour",
                        aggfunc="sum", fill_value=0)
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hm = hm.reindex([d for d in day_order if d in hm.index])
    fig_hm = go.Figure(go.Heatmap(
        z=hm.values,
        x=[f"{h:02d}:00" for h in hm.columns],
        y=hm.index,
        colorscale=[
            [0.0, hex_to_rgba(c["danger"],  1.0)],
            [0.5, c["bg3"]],
            [1.0, hex_to_rgba(c["success"], 1.0)],
        ],
        hovertemplate="<b>%{y} %{x}</b><br>P&L: $%{z:.2f}<extra></extra>",
        showscale=True,
        colorbar=dict(tickfont=dict(color=c["text_dim"]), outlinecolor="rgba(0,0,0,0)"),
    ))
    fig_hm.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0), height=220,
        xaxis=dict(color=c["text_dim"]),
        yaxis=dict(color=c["text_dim"]),
    )
    st.plotly_chart(fig_hm, use_container_width=True, config={"displayModeBar": False})
else:
    st.markdown(
        f'<div style="color:{c["text_dim"]};font-size:13px;">'
        f'Need at least 10 trades to generate the heatmap.</div>',
        unsafe_allow_html=True,
    )

# ── Session performance ───────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
section("🕐 Performance by Session")

df["session"] = df["hour"].apply(_session_label)
sess_stats = df.groupby("session").agg(
    trades=("profit", "count"),
    wins=("win",    "sum"),
    pnl=("profit", "sum"),
).reset_index()
sess_stats["win_rate"] = sess_stats["wins"] / sess_stats["trades"] * 100
_sess_order = ["Asian", "London", "New York", "Off-Session"]
sess_stats["_ord"] = sess_stats["session"].map({s: i for i, s in enumerate(_sess_order)})
sess_stats = sess_stats.sort_values("_ord").drop(columns="_ord")

sl, sr = st.columns(2, gap="medium")
with sl:
    fig_sw = go.Figure(go.Bar(
        x=sess_stats["session"], y=sess_stats["win_rate"],
        marker_color=[c["success"] if v >= 40 else (c["warning"] if v >= 25 else c["danger"])
                      for v in sess_stats["win_rate"]],
        text=[f"{v:.0f}%\n({int(t)} trades)" for v, t in
              zip(sess_stats["win_rate"], sess_stats["trades"])],
        textposition="outside", textfont=dict(color=c["text"], size=11),
    ))
    fig_sw.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=240, margin=dict(l=0, r=0, t=30, b=0),
        title=dict(text="Win Rate by Session", font=dict(color=c["text_dim"], size=12,
                                                         family="Orbitron")),
        xaxis=dict(showgrid=False, color=c["text_dim"]),
        yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                   range=[0, 115], ticksuffix="%"),
        showlegend=False,
    )
    st.plotly_chart(fig_sw, use_container_width=True, config={"displayModeBar": False})

with sr:
    fig_sp = go.Figure(go.Bar(
        x=sess_stats["session"], y=sess_stats["pnl"],
        marker_color=[c["success"] if v >= 0 else c["danger"] for v in sess_stats["pnl"]],
        text=[f"${v:+.2f}" for v in sess_stats["pnl"]],
        textposition="outside", textfont=dict(color=c["text"], size=11),
    ))
    fig_sp.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=240, margin=dict(l=0, r=0, t=30, b=0),
        title=dict(text="Net P&L by Session", font=dict(color=c["text_dim"], size=12,
                                                        family="Orbitron")),
        xaxis=dict(showgrid=False, color=c["text_dim"]),
        yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                   tickprefix="$", zeroline=True, zerolinecolor=c["card_border"]),
        showlegend=False,
    )
    st.plotly_chart(fig_sp, use_container_width=True, config={"displayModeBar": False})

# ── AI Confidence vs Win Rate ─────────────────────────────────────────────────
if "ai_confidence" in df.columns and df["ai_confidence"].notna().sum() >= 3:
    st.markdown("<br>", unsafe_allow_html=True)
    section("🎯 AI Confidence vs Win Rate")
    df_conf = df.dropna(subset=["ai_confidence"]).copy()
    bins    = [0.74, 0.79, 0.84, 0.89, 0.94, 1.01]
    labels  = ["75–79%", "80–84%", "85–89%", "90–94%", "95–100%"]
    df_conf["conf_bucket"] = pd.cut(df_conf["ai_confidence"], bins=bins,
                                    labels=labels, right=False)
    bkt = df_conf.groupby("conf_bucket", observed=True).agg(
        trades=("win", "count"),
        wins=("win",   "sum"),
    ).reset_index()
    bkt["win_rate"] = bkt["wins"] / bkt["trades"] * 100

    fig_cb = go.Figure(go.Bar(
        x=bkt["conf_bucket"].astype(str), y=bkt["win_rate"],
        marker_color=[c["success"] if v >= 40 else (c["warning"] if v >= 25 else c["danger"])
                      for v in bkt["win_rate"]],
        text=[f"{v:.0f}%  ({int(t)} trades)" for v, t in
              zip(bkt["win_rate"], bkt["trades"])],
        textposition="outside", textfont=dict(color=c["text"], size=10),
    ))
    fig_cb.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=250, margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(showgrid=False, color=c["text_dim"], title="AI Confidence at Entry"),
        yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                   range=[0, 115], ticksuffix="%"),
        showlegend=False,
    )
    st.plotly_chart(fig_cb, use_container_width=True, config={"displayModeBar": False})

# ── Streak & consistency ──────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
section("🔥 Streak & Consistency")
sc1, sc2, sc3, sc4 = st.columns(4)
with sc1: metric_card("Max Win Streak",  str(max_w), "consecutive wins",   c["success"])
with sc2: metric_card("Max Loss Streak", str(max_l), "consecutive losses", c["danger"])
with sc3:
    edge_col = c["success"] if edge > 0 else c["danger"]
    metric_card("Edge per Trade", f"${edge:+.2f}", "avg profit per closed trade", edge_col)
with sc4:
    exp_wins = round(win_rate / 100 * len(df))
    metric_card("Expected Wins", str(exp_wins), f"at {win_rate:.0f}% win rate", c["text_dim"])

# ── Full trade history ────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
section("📋 Full Trade History")

df_hist = df.sort_values("time", ascending=False).copy()

_col_map = [
    ("time",           "Closed"),
    ("symbol",         "Pair"),
    ("type",           "Dir"),
    ("volume",         "Lots"),
    ("profit",         "P&L ($)"),
    ("win",            "Result"),
    ("ai_confidence",  "AI Conf"),
    ("ict_score",      "ICT"),
    ("rrr",            "RR"),
]
avail_keys   = [k for k, _ in _col_map if k in df_hist.columns]
avail_labels = [v for k, v in _col_map if k in df_hist.columns]

df_disp = df_hist[avail_keys].copy()
df_disp.columns = avail_labels

df_disp["Closed"]   = pd.to_datetime(df_disp["Closed"]).dt.strftime("%d %b %H:%M")
df_disp["P&L ($)"]  = df_disp["P&L ($)"].map(lambda v: f"${v:+.2f}")
df_disp["Result"]   = df_disp["Result"].map(lambda v: "✅ WIN" if v else "❌ LOSS")
if "AI Conf" in df_disp.columns:
    df_disp["AI Conf"] = df_disp["AI Conf"].map(
        lambda v: f"{v:.0%}" if pd.notna(v) else "—")
if "ICT" in df_disp.columns:
    df_disp["ICT"] = df_disp["ICT"].map(
        lambda v: f"{int(v)}/20" if pd.notna(v) else "—")
if "RR" in df_disp.columns:
    df_disp["RR"] = df_disp["RR"].map(
        lambda v: f"1:{int(v)}" if pd.notna(v) else "—")

st.dataframe(df_disp, use_container_width=True, hide_index=True)
