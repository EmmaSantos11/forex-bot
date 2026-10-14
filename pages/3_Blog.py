"""
Blog — Auto-generated posts from trade history, news events, and retrain milestones.
Each post has randomised personality messages so no two look the same.
"""

import os
import random
import requests
import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta

from dashboard_utils import (
    inject_css, render_sidebar, page_header, section,
    get_deal_history, get_colors, get_model_info, MODEL_DIR,
    get_trade_log,
)

st.set_page_config(page_title="Blog · Mr Bera's_Bot", page_icon="📰", layout="wide")
render_sidebar()
inject_css()
c = get_colors()

page_header("📰 BLOG", "Trade highlights · market intelligence · AI activity log")

# ── Personality message pools ──────────────────────────────────────────────────

_WIN_MSGS = [
    "🎉 BOOM! We just cooked! That's what ICT + AI looks like in the wild 🤖🔥",
    "💰 The market tried it... and failed spectacularly. We ate well today 😏",
    "🏆 Another one! The algorithm said it was giving — and IT GAVE 🫡",
    "🚀 Not even close. The setup was too clean, we couldn't resist 💅",
    "🎯 Precision. Patience. Profit. That's the Mr Bera's Bot way 🐻✨",
    "🤑 The chart was basically BEGGING us to take this trade. Who are we to say no? 😂",
    "💪 Structure aligned. OTE hit. BOS confirmed. We were locked in 🔒",
    "😤 Called it, placed it, closed it in profit. The market doesn't stand a chance 😎",
    "🧠 Both XGBoost AND Random Forest agreed on this one. When they agree, we WIN 🎊",
    "🐻💰 Mr Bera's Bot said 'trust the process' — and the process delivered!",
    "🌙✨ Quietly printing while everyone else is sleeping. That's the bot life.",
    "🎸 The market played a tune and we danced perfectly. Chef's kiss 💋",
]

_LOSS_MSGS = [
    "📉 Ouch... the market had other plans today. Stop loss hit, ego checked ✅",
    "😅 We took the L with full dignity. The SL did exactly what it was supposed to do!",
    "🤷 Sometimes the market is just built different. Cooldown activated, we'll be back 💪",
    "📚 This one goes straight into the training data. Bot is taking notes 🤖📝",
    "😤 The market faked us out GOOD. Liquidity sweep? More like a liquidity AMBUSH 😂",
    "🎭 Plot twist nobody asked for. 3-hour cooldown. We'll return calmer and smarter 🔄",
    "🙃 The chart lied. The indicators lied. Only the stop loss told the truth.",
    "🤬 Tough one. But 1 loss at 1:5 RRR just means we need ONE win to be back in profit 🧮",
    "😔 Not every trade is a winner — even the smartest bots have bad days. Onwards! 🚶",
    "🛑 Stop loss triggered. That's not a failure, that's risk management working correctly 🛡️",
    "💀 The market giveth and the market taketh away. Today it tooketh. Tomorrow we taketh back 😤",
    "📖 Going back to the books on this one. The bot is already studying the replay 🤓",
]

_DAILY_PROFIT_MSGS = [
    "🌟 Another green day in the books! Consistency is how accounts grow 📈",
    "🥳 The portfolio is looking happy today. Good vibes only 💚",
    "💰 Profitable day. The bot showed up and did its job. Respect. 🐻",
    "🎉 Green across the board! ICT + SMC + AI working as a team today 🤝",
    "📈 Daily P&L looking clean. One trade at a time, one day at a time 🙏",
]

_DAILY_LOSS_MSGS = [
    "😬 Rough day. The market was choppy and the bot knew when to step back 🛑",
    "📉 Not our finest hour, but the daily loss limit protected the account. Safety first 🛡️",
    "🤕 Took some hits today. Reloading for tomorrow — the edge is still there 💪",
    "😤 The market won today's battle. The bot wins the war. 6 months of training incoming 🧠",
    "🌧️ Rainy day trading. Even the best setups get faked out sometimes. That's forex 🤷",
]

_DAILY_FLAT_MSGS = [
    "⚖️ Perfectly breakeven — sometimes discipline means walking away flat 🧘",
    "😐 Neither won nor lost today. The bot was selective and it shows.",
    "🤝 Market and bot called it a draw today. Rematch tomorrow 👊",
]

_RETRAIN_MSGS = [
    "📚 Back to school! The bot just completed its weekly study session 🤓✏️",
    "🧠 Sunday retrain complete. The AI just got a little smarter. Watch out market 👀",
    "🏋️ New week, new models! The bot hit the gym and trained all pairs 💪🔥",
    "📖 The bot went back to the books and emerged stronger. Ready for the market open 🐻",
    "🎓 Graduation day! Fresh models, fresh confidence, fresh week ahead 🚀",
    "🔬 7 months of H1 data digested. XGBoost and RF both graduated with honours 🎊",
    "🤖💡 Model update complete. The bot knows things now that it didn't know last week.",
    "⚡ Retrained and recharged. This is what it means to never stop learning 🧠✨",
]

_LOW_CONF_MSGS = [
    "🤔 The bot looked at this setup and said 'nah fam, the confidence isn't there' 😌",
    "📊 XGBoost and RF disagreed. When they argue, we don't trade. Adults in the room 🧠",
    "🙅 Not every setup deserves our attention. The bot said no and we respect that 💅",
    "😶 Signals were there but the conviction wasn't. Better to wait than to gamble 🎰❌",
    "🔍 Low confluence score — the strategy needs ALL the stars to align before we move ⭐",
    "😤 The bot WANTED to trade but the models said 'below 70% confidence'. House rules 📋",
]

_NEWS_MSGS = [
    "⚠️ Big news incoming! The bot is stepping aside and letting the dust settle first 🌪️",
    "📰 HIGH-impact event detected. Trading paused — we don't fight central banks 🏦",
    "🤫 Shhhh... the market is listening to the news. Bot is in observation mode 👀",
    "⏸️ 30-minute blackout activated. This is how smart traders protect their positions 🛡️",
    "📡 Economic event on radar. The bot knows when to sit on its hands 🙌",
]

_AUTOPSY_WIN_MSGS = [
    "🔬 Post-trade analysis complete. Everything went exactly to plan — and then some 📈",
    "🧪 We dissected this trade and the anatomy is perfect. ICT + AI = deadly combo 🎯",
    "📋 Trade review: entry was clean, structure held, AI was confident. Textbook execution 🐻",
    "🏆 This one aged well. The bot trusted its models and they delivered 💪",
    "🔍 Autopsy says: patient entry, disciplined exit, maximum efficiency 🧠",
]

_AUTOPSY_LOSS_MSGS = [
    "🔬 Post-mortem on this one: the setup was valid but the market had other ideas 🤷",
    "🩺 Trade dissection complete. SL did its job — that's all we can ask 🛡️",
    "📋 Review notes: entry valid, stop hit, no rule broken. Loss within system parameters ✅",
    "🔍 The signals fired, the models agreed, but the market faked left and went right 😤",
    "📚 Important lesson captured. This goes into the training data and makes us better 🤖",
]


def _rnd(pool): return random.choice(pool)


# ── News fetch ─────────────────────────────────────────────────────────────────

def _fetch_news():
    try:
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=8, headers={"User-Agent": "MrBerasBot/1.0"},
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


# ── Build posts ────────────────────────────────────────────────────────────────

posts = []

# 1. Trade-based posts ──────────────────────────────────────────────────────────
df = get_deal_history(days=30)
tlog = get_trade_log()

if not df.empty:

    # Wins (all profitable closed trades)
    wins = df[df["profit"] > 0].sort_values("profit", ascending=False).head(10)
    for _, row in wins.iterrows():
        posts.append({
            "type":  "win",
            "tag":   "Win",
            "time":  row["time"],
            "title": f"🏆 {row['symbol']} {row['type']} — +${row['profit']:.2f}",
            "body":  (
                f"{_rnd(_WIN_MSGS)}<br><br>"
                f"The bot landed a strong trade on <b>{row['symbol']}</b> ({row['type']}) "
                f"closing at <b style='color:{c['success']};'>+${row['profit']:.2f}</b>. "
                f"ICT structure + SMC confluence + AI agreement — the holy trinity fired perfectly."
            ),
            "meta": f"Lots: {row['volume']} · {row['time'].strftime('%d %b %Y %H:%M')}",
        })

    # Losses (all closed loss trades)
    losses = df[df["profit"] < 0].sort_values("profit", ascending=True).head(10)
    for _, row in losses.iterrows():
        posts.append({
            "type":  "loss",
            "tag":   "Loss",
            "time":  row["time"],
            "title": f"📉 {row['symbol']} {row['type']} — ${row['profit']:+.2f}",
            "body":  (
                f"{_rnd(_LOSS_MSGS)}<br><br>"
                f"Stop loss triggered on <b>{row['symbol']}</b> ({row['type']}). "
                f"The bot absorbed <b style='color:{c['danger']};'>${row['profit']:+.2f}</b> "
                f"and protected capital according to systematic risk management rules. "
                f"With favorable RRR, consistent wins outpace losses over a high sample size 🧮"
            ),
            "meta": f"Lots: {row['volume']} · {row['time'].strftime('%d %b %Y %H:%M')}",
        })

    # Daily summaries
    df_7 = df[df["time"] >= datetime.now() - timedelta(days=7)]
    if not df_7.empty:
        for day, group in df_7.groupby("date"):
            net    = group["profit"].sum()
            wins_cnt   = (group["profit"] > 0).sum()
            losses_cnt = (group["profit"] <= 0).sum()
            sign   = "+" if net >= 0 else ""
            color  = c["success"] if net > 0 else (c["text_dim"] if net == 0 else c["danger"])

            if net > 0:
                mood = _rnd(_DAILY_PROFIT_MSGS)
                verdict = "profitable"
            elif net == 0:
                mood = _rnd(_DAILY_FLAT_MSGS)
                verdict = "breakeven"
            else:
                mood = _rnd(_DAILY_LOSS_MSGS)
                verdict = "drawdown"

            best_p = ""
            if net > 0 and not group.empty:
                best_row = group.loc[group["profit"].idxmax()]
                best_p = f" Best pair: <b>{best_row['symbol']}</b> (+${best_row['profit']:.2f})"

            posts.append({
                "type":  "daily",
                "tag":   "Daily Summary",
                "time":  datetime.combine(day, datetime.min.time()),
                "title": f"📅 {datetime.strptime(str(day), '%Y-%m-%d').strftime('%A %d %b')} — {sign}${abs(net):.2f}",
                "body":  (
                    f"{mood}<br><br>"
                    f"<b>{wins_cnt}</b> winning trade{'s' if wins_cnt != 1 else ''} and "
                    f"<b>{losses_cnt}</b> losing trade{'s' if losses_cnt != 1 else ''} closed. "
                    f"Net result: <b style='color:{color};'>{sign}${abs(net):.2f}</b> — a {verdict} day.{best_p}"
                ),
                "meta": f"{len(group)} closed trades · {str(day)}",
            })

# 2. Trade autopsy posts — join MT5 history with trade_log.csv for confidence & confluence ──
if not df.empty:
    try:
        tlog_df = tlog.copy()
        if not tlog_df.empty:
            tlog_df["ticket"] = pd.to_numeric(tlog_df["ticket"], errors="coerce").fillna(0).astype(int)
            closed = df.copy()
            closed["ticket"] = pd.to_numeric(closed["ticket"], errors="coerce").fillna(0).astype(int)
            merged = closed.merge(
                tlog_df[["ticket", "ai_confidence", "ict_score", "rrr", "entry", "sl", "tp", "type"]].rename(
                    columns={"type": "log_type", "entry": "log_entry", "sl": "log_sl", "tp": "log_tp"}
                ),
                on="ticket", how="left"
            ).drop_duplicates(subset=["ticket", "time"])
        else:
            merged = df.copy()

        for _, row in merged.sort_values("time", ascending=False).head(15).iterrows():
            won = row["profit"] > 0
            conf = row.get("ai_confidence") if "ai_confidence" in row else None
            conf_val = float(conf) if pd.notna(conf) else None
            ict = row.get("ict_score") if "ict_score" in row else None
            ict_val = int(ict) if pd.notna(ict) else None
            rrr = row.get("rrr") if "rrr" in row else None
            rrr_val = float(rrr) if pd.notna(rrr) else 3.0
            trade_dir = row.get("log_type") if ("log_type" in row and pd.notna(row["log_type"])) else row["type"]

            conf_bar = ("🟢🟢🟢" if conf_val >= 0.80 else ("🟡🟡" if conf_val >= 0.65 else "🟠")) if conf_val is not None else "🛡️ Systematic"
            conf_str = f"{conf_val:.0%}" if conf_val is not None else "Standard Rules"
            ict_str = f" · ICT Score: <b>{ict_val}/20</b>" if ict_val is not None else ""
            rrr_str = f"1:{rrr_val:.1f}" if rrr_val != int(rrr_val) else f"1:{int(rrr_val)}"

            comment = str(row.get("comment", "")).lower()
            if "tp" in comment:
                outcome_detail = "Take Profit achieved 🎯"
            elif "sl" in comment:
                outcome_detail = "Stop Loss triggered 🛑"
            else:
                outcome_detail = "Target reached ✅" if won else "Risk limit reached 🛡️"

            verdict = (
                f"AI Model Confidence: <b>{conf_str}</b> — {'Signal validated ✅' if won else 'Protected by stop loss ❌'}"
            )
            posts.append({
                "type":  "autopsy",
                "tag":   "Trade Autopsy",
                "time":  row["time"],
                "title": f"🔬 {'WIN' if won else 'LOSS'} — {row['symbol']} {trade_dir} ${row['profit']:+.2f}",
                "body":  (
                    f"{_rnd(_AUTOPSY_WIN_MSGS if won else _AUTOPSY_LOSS_MSGS)}<br><br>"
                    f"<b>{row['symbol']}</b> {trade_dir} — "
                    f"Closed at <b style='color:{c['success'] if won else c['danger']};'>${row['profit']:+.2f}</b> ({outcome_detail}).<br>"
                    f"{verdict}<br>"
                    f"Confidence: {conf_bar} {conf_str}{ict_str} · Target RRR: <b>{rrr_str}</b><br><br>"
                    f"The bot followed all institutional rule parameters. "
                    + ("Structure held, liquidity was swept, and the AI called it correctly. " if won
                       else "Market invalidated the setup before target was reached. Stop loss prevented deeper drawdown. ")
                    + "Systematic execution 🤖"
                ),
                "meta": f"{row['time'].strftime('%d %b %Y %H:%M')} · Lots: {row['volume']} · Ticket: #{row['ticket']}",
            })
    except Exception:
        pass

# 3. Model retrain posts ────────────────────────────────────────────────────────
model_info = get_model_info()
trained_models = [m for m in model_info if m["last_train"]]
if trained_models:
    latest = max(trained_models, key=lambda m: m["last_train"])
    pairs_trained = [m["pair"] for m in trained_models if m["xgb"] and m["rf"]]
    posts.append({
        "type":  "retrain",
        "tag":   "AI Retrain",
        "time":  latest["last_train"],
        "title": f"🧠 AI Models Retrained — {len(pairs_trained)}/8 Pairs",
        "body":  (
            f"{_rnd(_RETRAIN_MSGS)}<br><br>"
            f"All per-pair XGBoost + Random Forest models were retrained with 7 months of fresh data. "
            f"<br><b>Pairs updated:</b> {', '.join(pairs_trained)}.<br><br>"
            f"The ensemble filter (both models must agree) now reflects the latest "
            f"market conditions and live trade outcomes blended in at 3× weight. "
            f"Every Sunday it gets sharper. Every loss becomes a lesson 📖"
        ),
        "meta": f"Retrained: {latest['last_train'].strftime('%d %b %Y %H:%M')} · 7 months of H1 data per pair",
    })

# 3. News posts ─────────────────────────────────────────────────────────────────
news_events = _fetch_news()
now_utc = datetime.now(timezone.utc)
for event in news_events:
    if event.get("impact", "").lower() != "high":
        continue
    try:
        dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_local = dt.replace(tzinfo=None)
    except Exception:
        continue

    mins_away = (dt - now_utc).total_seconds() / 60
    if not (-120 <= mins_away <= 480):
        continue

    title_ev = event.get("title", "Economic Event")
    country  = event.get("country", "")
    forecast = event.get("forecast", "N/A")
    previous = event.get("previous", "N/A")
    actual   = event.get("actual", "")
    timing   = "RELEASED" if mins_away < 0 else (f"in {int(mins_away)}m" if mins_away < 60 else f"in {int(mins_away/60)}h")

    posts.append({
        "type":  "news",
        "tag":   "Market Alert",
        "time":  dt_local,
        "title": f"⚠️ {title_ev} ({country}) — {timing}",
        "body":  (
            f"{_rnd(_NEWS_MSGS)}<br><br>"
            f"HIGH-impact event for <b>{country}</b>.<br>"
            f"Forecast: <b>{forecast}</b> · Previous: <b>{previous}</b>"
            + (f" · <b style='color:{c['accent']};'>Actual: {actual}</b>" if actual else "")
            + f"<br><br>Bot is {'pausing' if abs(mins_away) <= 30 else 'monitoring'} "
            f"affected pairs during the 30-minute news buffer window."
        ),
        "meta": f"Impact: HIGH · Currency: {country} · {dt_local.strftime('%d %b %H:%M UTC')}",
    })

# ── Filter + sort ──────────────────────────────────────────────────────────────
filter_opt = st.radio(
    "Filter Blog Posts",
    ["All", "🏆 Wins", "📉 Losses", "🔬 Autopsies", "🧠 AI", "⚠️ News", "📅 Daily"],
    horizontal=True,
    label_visibility="collapsed",
    key="blog_filter_radio",
)

filter_map = {
    "All": None,
    "🏆 Wins": "win",
    "📉 Losses": "loss",
    "🔬 Autopsies": "autopsy",
    "🧠 AI": "retrain",
    "⚠️ News": "news",
    "📅 Daily": "daily",
}
filter_type = filter_map.get(filter_opt)

filtered = [p for p in posts if filter_type is None or p["type"] == filter_type]
filtered.sort(key=lambda p: p["time"], reverse=True)

st.markdown(
    f'<div style="color:{c["text_dim"]}; font-size:12px; margin:8px 0 16px 0;">'
    f'{len(filtered)} post{"s" if len(filtered) != 1 else ""}</div>',
    unsafe_allow_html=True,
)

# ── Render posts ───────────────────────────────────────────────────────────────
if not filtered:
    st.markdown(f"""
    <div class="bot-card" style="text-align:center; padding:60px; color:{c['text_dim']};">
        <div style="font-size:48px; margin-bottom:16px;">📭</div>
        No posts yet matching the selected filter.
    </div>
    """, unsafe_allow_html=True)
else:
    TAG_CLASSES = {
        "win":     ("tag-win",     "WIN"),
        "loss":    ("tag-loss",    "LOSS"),
        "news":    ("tag-news",    "MARKET ALERT"),
        "retrain": ("tag-retrain", "AI RETRAIN"),
        "daily":   ("tag-daily",   "DAILY SUMMARY"),
        "autopsy": ("tag-autopsy", "TRADE AUTOPSY"),
    }
    for post in filtered:
        tag_cls, tag_label = TAG_CLASSES.get(post["type"], ("tag-news", "UPDATE"))
        st.markdown(f"""
        <div class="blog-card {post['type']}">
            <span class="blog-tag {tag_cls}">{tag_label}</span>
            <div style="font-size:17px; font-weight:600; color:{c['text']};
                        margin-bottom:10px; line-height:1.4;">
                {post['title']}
            </div>
            <div style="color:{c['text_dim']}; font-size:13px; line-height:1.8;">
                {post['body']}
            </div>
            <div style="margin-top:14px; font-size:11px; color:{c['text_dim']};
                        font-family:'Orbitron',monospace; letter-spacing:0.5px; opacity:0.7;">
                {post['meta']}
            </div>
        </div>
        """, unsafe_allow_html=True)
