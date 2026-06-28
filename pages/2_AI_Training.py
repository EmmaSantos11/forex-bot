"""
AI Training dashboard — model status, win recall, retrain schedule.
"""

import os
import sys
import subprocess
import joblib
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timezone, timedelta

from dashboard_utils import (
    inject_css, render_sidebar, page_header, metric_card, section,
    get_colors, get_model_info, PAIRS, MODEL_DIR, hex_to_rgba,
)

st.set_page_config(page_title="AI Training · Mr Bera's_Bot", page_icon="🧠", layout="wide")
render_sidebar()
inject_css()
c = get_colors()

page_header("🧠 AI TRAINING", "Per-pair model status · win recall · Sunday retrain schedule")

# ── Model status cards ────────────────────────────────────────────────────────
models = get_model_info()
trained = [m for m in models if m["xgb"] and m["rf"]]
last_train_times = [m["last_train"] for m in models if m["last_train"]]
last_train = max(last_train_times) if last_train_times else None

now = datetime.now(timezone.utc)
days_to_sunday = (6 - now.weekday()) % 7 or 7
next_sunday = (now + timedelta(days=days_to_sunday)).replace(
    hour=21, minute=0, second=0, microsecond=0
)
hours_to_retrain = (next_sunday - now).total_seconds() / 3600

# Live trade data count
LIVE_TRADE_PATH = os.path.join("data", "manual_trade_features.csv")
live_sample_count = 0
if os.path.exists(LIVE_TRADE_PATH):
    try:
        live_sample_count = len(pd.read_csv(LIVE_TRADE_PATH))
    except Exception:
        pass

# Total retrains from history
HIST_PATH = os.path.join("logs", "model_recall_history.csv")
total_retrains = 0
if os.path.exists(HIST_PATH):
    try:
        df_hist_meta = pd.read_csv(HIST_PATH)
        total_retrains = 0 if df_hist_meta.empty else int(df_hist_meta["retrain_n"].max())
    except Exception:
        pass

col1, col2, col3, col4, col5, col6 = st.columns(6)
with col1:
    metric_card("Models Ready", f"{len(trained)}/{len(PAIRS)}",
                "XGBoost + RF per pair",
                c["success"] if len(trained) == len(PAIRS) else c["warning"])
with col2:
    metric_card("Last Trained",
                last_train.strftime("%d %b") if last_train else "Never",
                last_train.strftime("%H:%M") if last_train else "Run retrain_model.py")
with col3:
    metric_card("Next Retrain",
                f"{int(hours_to_retrain)}h {int((hours_to_retrain % 1) * 60)}m",
                next_sunday.strftime("Sunday %d %b %H:%M UTC"),
                c["accent2"])
with col4:
    global_xgb = os.path.exists(os.path.join(MODEL_DIR, "xgb_trading_model.pkl"))
    global_rf  = os.path.exists(os.path.join(MODEL_DIR, "rf_trading_model.pkl"))
    status_txt = "XGB + RF" if (global_xgb and global_rf) else ("XGB only" if global_xgb else "Missing")
    metric_card("Global Fallback", status_txt, "used when pair model fails",
                c["success"] if (global_xgb and global_rf) else c["warning"])
with col5:
    metric_card("Total Retrains", str(total_retrains),
                "cumulative training rounds",
                c["accent"] if total_retrains > 0 else c["text_dim"])
with col6:
    metric_card("Live Trade Samples", f"{live_sample_count:,}",
                "real outcomes in training",
                c["success"] if live_sample_count >= 10 else c["warning"])

st.markdown("<br>", unsafe_allow_html=True)

# ── Per-pair model table ──────────────────────────────────────────────────────
section("📋 Per-Pair Model Status")

# Try to load actual win recall from saved metadata if available
RECALL_FILE = os.path.join("logs", "model_recall.csv")
recall_map = {}
if os.path.exists(RECALL_FILE):
    try:
        recall_df = pd.read_csv(RECALL_FILE)
        for _, row in recall_df.iterrows():
            recall_map[row["pair"]] = {
                "xgb_recall": row.get("xgb_recall", None),
                "rf_recall":  row.get("rf_recall", None),
                "samples":    row.get("samples", None),
            }
    except Exception:
        pass

rows = []
for m in models:
    pair = m["pair"]
    meta = recall_map.get(pair, {})
    rows.append({
        "Pair":       pair,
        "XGBoost":    "✅" if m["xgb"] else "❌",
        "RF":         "✅" if m["rf"]  else "❌",
        "Samples":    int(meta.get("samples", 0)) if meta.get("samples") else "—",
        "XGB Recall": f"{meta.get('xgb_recall', 0)*100:.1f}%" if meta.get("xgb_recall") else "—",
        "RF Recall":  f"{meta.get('rf_recall',  0)*100:.1f}%" if meta.get("rf_recall")  else "—",
        "Last Train": m["last_train"].strftime("%d %b %H:%M") if m["last_train"] else "—",
    })

df_models = pd.DataFrame(rows)
st.dataframe(df_models, use_container_width=True, hide_index=True)

# ── Win recall chart ──────────────────────────────────────────────────────────
if recall_map:
    st.markdown("<br>", unsafe_allow_html=True)
    section("🎯 Win Recall by Pair (RF Model)")
    st.markdown(f"""
    <div style="color:{c['text_dim']}; font-size:12px; margin-bottom:12px;">
        Win recall = % of winning bars the model correctly identified as wins.
        Higher = better at spotting real opportunities. Target: &gt;30%.
    </div>
    """, unsafe_allow_html=True)

    recall_pairs   = []
    recall_rf_vals = []
    recall_xgb_vals = []
    for pair in PAIRS:
        meta = recall_map.get(pair, {})
        rf_r  = meta.get("rf_recall",  0) or 0
        xgb_r = meta.get("xgb_recall", 0) or 0
        recall_pairs.append(pair)
        recall_rf_vals.append(rf_r * 100)
        recall_xgb_vals.append(xgb_r * 100)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="RF Win Recall", x=recall_pairs, y=recall_rf_vals,
        marker_color=[c["success"] if v >= 30 else (c["warning"] if v >= 15 else c["danger"])
                      for v in recall_rf_vals],
        text=[f"{v:.1f}%" for v in recall_rf_vals],
        textposition="outside", textfont=dict(color=c["text"], size=11),
    ))
    fig.add_trace(go.Bar(
        name="XGB Win Recall", x=recall_pairs, y=recall_xgb_vals,
        marker_color=hex_to_rgba(c["accent2"], 0.6),
        text=[f"{v:.1f}%" for v in recall_xgb_vals],
        textposition="outside", textfont=dict(color=c["text_dim"], size=10),
    ))
    fig.add_hline(y=30, line_dash="dot", line_color=c["success"],
                  annotation_text="Target 30%", annotation_font_color=c["success"])
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=10, b=0), height=300,
        barmode="group",
        xaxis=dict(showgrid=False, color=c["text_dim"]),
        yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                   ticksuffix="%", range=[0, 110]),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=c["text_dim"])),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
else:
    st.markdown(f"""
    <div class="bot-card" style="color:{c['text_dim']}; text-align:center; padding:30px;">
        <div style="font-size:32px; margin-bottom:10px;">📊</div>
        Win recall data will appear here after the next retrain.<br>
        Run <code style="color:{c['accent']};">python retrain_model.py</code> to populate this.
    </div>
    """, unsafe_allow_html=True)

# ── Learning trend chart — recall improvement over time ───────────────────────
st.markdown("<br>", unsafe_allow_html=True)
section("📈 Learning Progress — Recall Over Time")
st.markdown(f"""
<div style="color:{c['text_dim']}; font-size:12px; margin-bottom:12px;">
    Each point is one Sunday retrain. As live trade data accumulates the models
    learn real broker-specific patterns and recall should trend upward over 6 months.
    <b style="color:{c['accent']};">{live_sample_count:,} live trade samples</b> in training data so far
    · <b style="color:{c['accent2']};">{total_retrains} retrains completed</b>.
</div>
""", unsafe_allow_html=True)

if os.path.exists(HIST_PATH):
    try:
        df_hist = pd.read_csv(HIST_PATH)
        if not df_hist.empty and "retrain_n" in df_hist.columns:
            fig_trend = go.Figure()
            colors_palette = [
                c["accent"], c["accent2"], c["success"], c["warning"],
                c["danger"], "#9D4EDD", "#00B4D8", "#F77F00",
            ]
            for i, pair in enumerate(PAIRS):
                pair_hist = df_hist[df_hist["pair"] == pair].sort_values("retrain_n")
                if pair_hist.empty:
                    continue
                col_line = colors_palette[i % len(colors_palette)]
                fig_trend.add_trace(go.Scatter(
                    x=pair_hist["retrain_n"],
                    y=(pair_hist["rf_recall"] * 100).round(1),
                    mode="lines+markers",
                    name=pair,
                    line=dict(color=col_line, width=2),
                    marker=dict(size=6, color=col_line),
                    hovertemplate=f"<b>{pair}</b><br>Retrain #%{{x}}<br>RF Recall: %{{y:.1f}}%<extra></extra>",
                ))
            fig_trend.add_hline(y=30, line_dash="dot", line_color=c["success"],
                                annotation_text="Target 30%",
                                annotation_font_color=c["success"])
            fig_trend.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                height=280, margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(showgrid=False, color=c["text_dim"], title="Retrain #",
                           tickmode="linear", dtick=1),
                yaxis=dict(showgrid=True, gridcolor=c["chart_grid"], color=c["text_dim"],
                           ticksuffix="%", title="RF Win Recall"),
                legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=c["text_dim"]),
                            orientation="h", yanchor="bottom", y=1.02),
                hovermode="x unified",
            )
            st.plotly_chart(fig_trend, use_container_width=True, config={"displayModeBar": False})
        else:
            st.markdown(f'<div style="color:{c["text_dim"]}; font-size:13px;">History will populate after the next retrain.</div>', unsafe_allow_html=True)
    except Exception as _e:
        st.markdown(f'<div style="color:{c["text_dim"]}; font-size:13px;">Could not load recall history: {_e}</div>', unsafe_allow_html=True)
else:
    st.markdown(f"""
    <div class="bot-card" style="text-align:center; padding:30px; color:{c['text_dim']};">
        <div style="font-size:32px; margin-bottom:10px;">📉→📈</div>
        Learning trend chart appears here after 2+ retrains.<br>
        Run <code style="color:{c['accent']};">python retrain_model.py</code> tonight, then again next Sunday,
        and the improvement line starts drawing itself.
    </div>
    """, unsafe_allow_html=True)

# ── Live data accumulation tracker ───────────────────────────────────────────
if live_sample_count > 0:
    st.markdown("<br>", unsafe_allow_html=True)
    section("🎯 Live Trade Data Accumulation")
    target_6m = 500   # rough target for 6 months of live trading
    pct = min(live_sample_count / target_6m, 1.0)
    bar_col = c["success"] if pct >= 0.5 else (c["warning"] if pct >= 0.2 else c["accent"])
    st.markdown(f"""
    <div style="margin:8px 0 4px 0; display:flex; justify-content:space-between;
                font-size:12px; color:{c['text_dim']};">
        <span>Live trade samples blended into models at 3× weight</span>
        <span style="color:{bar_col}; font-weight:700;">{live_sample_count} / {target_6m} target</span>
    </div>
    <div class="recall-bar-wrap" style="height:12px;">
        <div class="recall-bar" style="width:{int(pct*100)}%; background:{bar_col}; height:12px;"></div>
    </div>
    <div style="font-size:11px; color:{c['text_dim']}; margin-top:6px;">
        Every closed trade is imported on Sundays and blended into retraining.
        The more live trades the bot makes, the more it learns from real outcomes on your broker.
    </div>
    """, unsafe_allow_html=True)

# ── Manual retrain ────────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
section("🔄 Manual Retrain")

_ctrl_col, _info_col = st.columns([3, 2], gap="medium")

with _ctrl_col:
    st.warning(
        "⚠️ Stop the bot (`start_bot.bat` window) before retraining to avoid memory errors.",
        icon=None,
    )
    _selected_pairs = st.multiselect(
        "Pairs to retrain (leave empty = all 8)",
        options=PAIRS,
        default=[],
        key="retrain_pair_select",
        placeholder="All pairs",
    )
    _import_first = st.checkbox(
        "Import live trades first (recommended)",
        value=True,
        key="retrain_import_first",
    )
    _run = st.button("🚀  Start Retraining", type="primary", use_container_width=True)

with _info_col:
    st.markdown(f"""
    <div class="bot-card" style="padding:16px 18px;">
        <div style="font-family:'Orbitron',monospace; font-size:11px;
                    color:{c['accent2']}; letter-spacing:1.5px; margin-bottom:10px;">
            AUTO-RETRAIN SCHEDULE
        </div>
        <div style="color:{c['text_dim']}; font-size:12px; line-height:1.8;">
            <b style="color:{c['text']};">Every Sunday 21:00 UTC</b><br>
            Runs automatically while waiting for market open.<br><br>
            Next auto-retrain in
            <b style="color:{c['success']};">{int(hours_to_retrain)}h {int((hours_to_retrain % 1)*60)}m</b>
        </div>
    </div>
    """, unsafe_allow_html=True)

if _run:
    _log_box = st.empty()
    _lines: list[str] = []

    def _stream(cmd: list[str], label: str) -> bool:
        _lines.append(f"{'─'*52}")
        _lines.append(f"  {label}")
        _lines.append(f"{'─'*52}")
        _log_box.code("\n".join(_lines), language="")
        _env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
        _proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8",
            cwd=os.path.abspath("."), env=_env,
        )
        for _line in _proc.stdout:
            _lines.append(_line.rstrip())
            _log_box.code("\n".join(_lines[-80:]), language="")
        _proc.wait()
        return _proc.returncode == 0

    _ok = True

    if _import_first:
        _ok = _stream([sys.executable, "import_manual_trades.py"], "STEP 1 — Importing live trades...")

    if _ok:
        _args = [sys.executable, "retrain_model.py"] + _selected_pairs
        _ok = _stream(_args, "STEP 2 — Retraining models...")

    st.markdown("<br>", unsafe_allow_html=True)
    if _ok:
        st.success("Retraining complete — bot will use updated models on the next signal cycle. Restart start_bot.bat to reload immediately.")
    else:
        st.error("Retraining failed — check the log above for details.")
