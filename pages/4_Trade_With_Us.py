"""
Trade With Us — Channel CTA, live managed accounts, and inline admin panel.
Admin is accessible ONLY through the hidden 🔐 button at the bottom.
There is no separate admin page — the panel renders here after authentication.
"""

import os
from datetime import date, datetime, timezone
import streamlit as st
from dashboard_utils import (
    inject_css, render_sidebar, get_colors,
    load_managed_accounts, save_managed_accounts, fetch_mt5_account_stats,
    check_admin_password, load_bot_users, remove_bot_user,
    MAX_MANAGED_ACCOUNTS, MAX_BOT_USERS,
)

st.set_page_config(page_title="Trade With Us · Mr Bera's_Bot", page_icon="🤝", layout="wide")
render_sidebar()
inject_css()
c = get_colors()

CHANNEL_LINK  = "https://t.me/+m-pvIcsZ6OYxOTNk"
WEBSITE_LINK  = "https://emmasantos11.github.io/My-Site/"
ADMIN_TG_LINK = "https://t.me/Bera_sa"
ADMIN_TG      = os.getenv("ADMIN_TELEGRAM_USERNAME", "@Bera_sa")
_logo = os.path.join(os.path.dirname(__file__), "..", "assets", "bot_logo.png")

# ── Session state ─────────────────────────────────────────────────────────────
for _k, _v in [
    ("_gate_step", "idle"), ("_gate_attempts", 0), ("_gate_from_no", False),
    ("admin_auth", False),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL — inline, only shown when authenticated
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state["admin_auth"]:
    st.markdown(f"""
    <div style="padding:16px 0 4px 0;">
        <div style="font-family:'Orbitron',monospace; font-size:22px; font-weight:900;
                    color:{c['accent']}; letter-spacing:3px;">🔐 Admin Panel</div>
        <div style="color:{c['text_dim']}; font-size:12px; margin-top:4px;">
            Logged in as admin · Bot owner controls
        </div>
    </div>
    """, unsafe_allow_html=True)

    _lo, _ = st.columns([1, 5])
    with _lo:
        if st.button("🚪 Logout"):
            st.session_state["admin_auth"] = False
            st.session_state["_gate_step"] = "idle"
            st.rerun()

    st.markdown(f'<hr style="border:none;border-top:1px solid {c["card_border"]};margin:12px 0 24px 0;">', unsafe_allow_html=True)

    tab_accounts, tab_users = st.tabs(["📊 Managed Accounts", "👥 Bot Users"])

    # ── Tab 1: Managed Accounts ────────────────────────────────────────────────
    with tab_accounts:
        accounts     = load_managed_accounts()
        active_count = sum(1 for a in accounts if a.get("active", False))

        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:16px; margin-bottom:20px;">
            <div style="font-family:'Orbitron',monospace; font-size:14px; font-weight:700;
                        color:{c['text']}; letter-spacing:1px;">CLIENT ACCOUNTS</div>
            <div style="background:{c['accent']}22; color:{c['accent']}; font-size:11px;
                        font-weight:700; padding:3px 10px; border-radius:20px;
                        border:1px solid {c['accent']}44;">
                {active_count} / {MAX_MANAGED_ACCOUNTS} slots used
            </div>
        </div>
        """, unsafe_allow_html=True)

        if accounts:
            for i, acct in enumerate(accounts):
                slot      = acct.get("slot", i + 1)
                nick      = acct.get("nickname", f"Slot {slot}")
                is_active = acct.get("active", False)
                with st.expander(f"{'🟢' if is_active else '🔴'} Slot {slot} — {nick}", expanded=False):
                    col1, col2 = st.columns(2)
                    with col1:
                        new_nick   = st.text_input("Client Nickname",    value=acct.get("nickname", ""),       key=f"nick_{slot}")
                        new_login  = st.text_input("MT5 Login",          value=str(acct.get("mt5_login", "")), key=f"login_{slot}")
                        new_server = st.text_input("MT5 Server",         value=acct.get("mt5_server", ""),     key=f"server_{slot}")
                    with col2:
                        new_pwd    = st.text_input("MT5 Password",       value=acct.get("mt5_password", ""),   key=f"pwd_{slot}",  type="password")
                        new_tg     = st.text_input("Client Telegram ID", value=acct.get("telegram_id", ""),    key=f"tg_{slot}",   help="Optional — for sending alerts to client")
                        new_plan   = st.selectbox("Plan", ["Standard", "Premium", "VIP"], key=f"plan_{slot}",
                                                  index=["Standard","Premium","VIP"].index(acct.get("plan","Standard")))
                    col3, col4, col5 = st.columns(3)
                    with col3:
                        new_init   = st.number_input("Initial Balance", value=float(acct.get("initial_balance", 1000)), step=100.0, key=f"init_{slot}")
                    with col4:
                        new_date   = st.date_input("Start Date", value=date.fromisoformat(acct.get("start_date", str(date.today()))), key=f"date_{slot}")
                    with col5:
                        new_active = st.checkbox("Active", value=is_active, key=f"active_{slot}")

                    bc1, bc2 = st.columns([3, 1])
                    with bc1:
                        if st.button(f"💾 Save Changes — Slot {slot}", key=f"save_{slot}", use_container_width=True):
                            accounts[i] = {
                                "slot": slot, "active": new_active, "nickname": new_nick,
                                "mt5_login": new_login, "mt5_password": new_pwd,
                                "mt5_server": new_server, "telegram_id": new_tg,
                                "plan": new_plan, "initial_balance": new_init,
                                "start_date": str(new_date),
                            }
                            save_managed_accounts(accounts)
                            st.success(f"✅ Slot {slot} updated.")
                            st.rerun()
                    with bc2:
                        if st.button("🗑️ Remove", key=f"del_{slot}", use_container_width=True):
                            accounts.pop(i)
                            save_managed_accounts(accounts)
                            st.success(f"Slot {slot} removed.")
                            st.rerun()
        else:
            st.info("No managed accounts configured yet. Add one below.")

        st.markdown("<br>", unsafe_allow_html=True)
        if active_count >= MAX_MANAGED_ACCOUNTS:
            st.warning(f"⛔ Maximum of {MAX_MANAGED_ACCOUNTS} active accounts reached. Deactivate or remove one to add another.")
        else:
            with st.expander("➕ Add New Client Account", expanded=False):
                next_slot = max((a.get("slot", 0) for a in accounts), default=0) + 1
                col1, col2 = st.columns(2)
                with col1:
                    a_nick   = st.text_input("Client Nickname",    key="a_nick",   placeholder="e.g. Client Alpha")
                    a_login  = st.text_input("MT5 Login",          key="a_login",  placeholder="e.g. 12345678")
                    a_server = st.text_input("MT5 Server",         key="a_server", placeholder="e.g. Exness-MT5Real")
                with col2:
                    a_pwd    = st.text_input("MT5 Password",       key="a_pwd",    type="password")
                    a_tg     = st.text_input("Client Telegram ID", key="a_tg",     placeholder="Optional — numeric chat ID")
                    a_plan   = st.selectbox("Plan", ["Standard", "Premium", "VIP"], key="a_plan")
                col3, col4 = st.columns(2)
                with col3:
                    a_init = st.number_input("Initial Balance", value=1000.0, step=100.0, key="a_init")
                with col4:
                    a_date = st.date_input("Start Date", value=date.today(), key="a_date")
                if st.button("➕ Add Account", use_container_width=True):
                    if not a_nick or not a_login or not a_pwd or not a_server:
                        st.error("Nickname, MT5 Login, Password and Server are required.")
                    else:
                        accounts.append({
                            "slot": next_slot, "active": True, "nickname": a_nick,
                            "mt5_login": a_login, "mt5_password": a_pwd,
                            "mt5_server": a_server, "telegram_id": a_tg,
                            "plan": a_plan, "initial_balance": a_init, "start_date": str(a_date),
                        })
                        save_managed_accounts(accounts)
                        st.success(f"✅ Account '{a_nick}' added as Slot {next_slot}.")
                        st.rerun()

    # ── Tab 2: Bot Users ───────────────────────────────────────────────────────
    with tab_users:
        admin_id  = os.getenv("TELEGRAM_CHAT_ID", "")
        all_users = load_bot_users()
        non_admin = [u for u in all_users if str(u) != admin_id]

        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:16px; margin-bottom:20px;">
            <div style="font-family:'Orbitron',monospace; font-size:14px; font-weight:700;
                        color:{c['text']}; letter-spacing:1px;">REGISTERED BOT USERS</div>
            <div style="background:{c['accent']}22; color:{c['accent']}; font-size:11px;
                        font-weight:700; padding:3px 10px; border-radius:20px;
                        border:1px solid {c['accent']}44;">
                {len(non_admin)} / {MAX_BOT_USERS} slots used
            </div>
        </div>
        <div style="color:{c['text_dim']}; font-size:12px; margin-bottom:20px; line-height:1.7;">
            These users receive live trade alerts directly from the bot via Telegram DM.<br>
            Max {MAX_BOT_USERS} users. The admin account is not counted toward this limit.
        </div>
        """, unsafe_allow_html=True)

        if non_admin:
            for uid in non_admin:
                uc1, uc2, uc3 = st.columns([3, 2, 1])
                with uc1:
                    st.markdown(f'<div style="padding:8px 0;color:{c["text"]};font-size:13px;">👤 <code>{uid}</code></div>', unsafe_allow_html=True)
                with uc2:
                    st.markdown(f'<div style="padding:8px 0;color:{c["text_dim"]};font-size:11px;">Telegram User ID</div>', unsafe_allow_html=True)
                with uc3:
                    if st.button("Remove", key=f"rm_{uid}"):
                        remove_bot_user(str(uid))
                        st.success(f"User {uid} removed.")
                        st.rerun()
        else:
            st.info("No non-admin users registered yet.")

        st.markdown(f'<hr style="border:none;border-top:1px solid {c["card_border"]};margin:24px 0 16px 0;">', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="color:{c['text_dim']}; font-size:12px;">
            🔑 <b>Admin (you):</b> <code>{admin_id or 'not set in .env'}</code>
            — always has access, not counted toward the {MAX_BOT_USERS}-user limit.
        </div>
        """, unsafe_allow_html=True)
        if len(non_admin) >= MAX_BOT_USERS:
            st.warning(f"⛔ Bot user slots full ({MAX_BOT_USERS}/{MAX_BOT_USERS}). Remove a user before new registrations are accepted.")

    st.stop()   # nothing below renders while admin panel is open


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC PAGE
# ══════════════════════════════════════════════════════════════════════════════

# ── Hero ──────────────────────────────────────────────────────────────────────
_, mid, _ = st.columns([1, 4, 1])
with mid:
    if os.path.exists(_logo):
        _lc1, _lc2, _lc3 = st.columns([1, 2, 1])
        with _lc2:
            st.image(_logo, use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)

    st.markdown(f"""
    <div class="hero-section">
        <div style="font-family:'Orbitron',monospace; font-size:11px; letter-spacing:5px;
                    color:{c['text_dim']}; margin-bottom:16px;">POWERED BY AI</div>
        <div style="font-family:'Orbitron',monospace; font-size:38px; font-weight:900;
                    color:{c['accent']}; letter-spacing:4px; line-height:1.2; margin-bottom:6px;">
            🐻 Mr Bera's_Bot
        </div>
        <div style="font-family:'Orbitron',monospace; font-size:12px; letter-spacing:6px;
                    color:{c['text_dim']}; margin-bottom:32px;">
            SMARTER TRADES. BETTER RESULTS.
        </div>
        <div style="font-size:16px; color:{c['text']}; max-width:520px; margin:0 auto 32px auto;
                    line-height:1.7;">
            Follow every trade in real time — entries, exits, P&amp;L, and honest post-mortems.
            Join the channel first. When you're ready to let the bot trade your account,
            message the admin directly.
        </div>
        <div style="display:flex; gap:16px; justify-content:center; flex-wrap:wrap; margin-bottom:16px;">
            <a href="{CHANNEL_LINK}" target="_blank" class="cta-link">
                📡 &nbsp; JOIN THE CHANNEL
            </a>
            <a href="{WEBSITE_LINK}" target="_blank" class="cta-link"
               style="background:transparent; border:2px solid {c['accent']}; color:{c['accent']};">
                🌐 &nbsp; VISIT WEBSITE
            </a>
        </div>
        <div style="color:{c['text_dim']}; font-size:12px; margin-top:8px; line-height:1.8;">
            Free to follow · No sign-up · Live alerts, daily briefings &amp; educational posts
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    features = [
        ("⚡", "ICT + SMC Strategy",   "Institutional-grade setups — liquidity sweeps, order blocks, BOS."),
        ("🧠", "AI-Powered Signals",   "XGBoost + Random Forest ensemble. Both must agree before any trade fires."),
        ("🛡️", "Risk First",           "ATR-based SL, 3h loss cooldown, daily loss cap, breakeven automation."),
        ("📰", "News Intelligence",    "30-min blackout before HIGH-impact events. Bot reads the calendar."),
        ("💰", "Scales With Balance",  "Position sizing grows automatically as the account hits milestones."),
        ("📱", "Live Telegram Alerts", "Every trade entry, exit, and P&L update sent directly to you."),
    ]
    for i, (emoji, title, body) in enumerate(features):
        with [col1, col2, col3][i % 3]:
            st.markdown(f"""
            <div class="bot-card" style="text-align:center; padding:24px 16px; margin-bottom:12px;">
                <div style="font-size:32px; margin-bottom:12px;">{emoji}</div>
                <div style="font-family:'Orbitron',monospace; font-size:12px; font-weight:700;
                            color:{c['accent']}; letter-spacing:1px; margin-bottom:10px;">{title}</div>
                <div style="font-size:12px; color:{c['text_dim']}; line-height:1.6;">{body}</div>
            </div>
            """, unsafe_allow_html=True)

# ── Managed Accounts live stats ───────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
st.markdown(f"""
<div style="text-align:center; margin-bottom:24px;">
    <div style="font-family:'Orbitron',monospace; font-size:11px; letter-spacing:5px;
                color:{c['text_dim']}; margin-bottom:10px;">LIVE PORTFOLIO</div>
    <div style="font-family:'Orbitron',monospace; font-size:24px; font-weight:900;
                color:{c['accent']}; letter-spacing:3px; margin-bottom:8px;">
        📊 Managed Accounts
    </div>
    <div style="color:{c['text_dim']}; font-size:13px; max-width:500px; margin:0 auto;">
        Real accounts. Real trades. No filter.<br>
        Every position managed by Mr Bera's Bot using ICT + SMC + AI strategy.
    </div>
</div>
""", unsafe_allow_html=True)

accounts = load_managed_accounts()
active   = [a for a in accounts if a.get("active", False)]

if not active:
    st.markdown(f"""
    <div class="bot-card" style="text-align:center; padding:36px 24px; max-width:560px; margin:0 auto;">
        <div style="font-size:40px; margin-bottom:14px;">🚧</div>
        <div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
                    color:{c['accent']}; letter-spacing:1px; margin-bottom:10px;">
            NO ACCOUNTS ONBOARDED YET
        </div>
        <div style="color:{c['text_dim']}; font-size:13px; line-height:1.8;">
            Up to {MAX_MANAGED_ACCOUNTS} client accounts can be managed simultaneously.<br>
            Interested? Message the admin on Telegram to discuss terms.
        </div>
        <div style="margin-top:20px;">
            <a href="{ADMIN_TG_LINK}" target="_blank" class="cta-link"
               style="font-size:13px; padding:10px 26px;">
                💬 Message Admin
            </a>
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    _hdr, _btn = st.columns([5, 1])
    with _hdr:
        st.markdown(f"""
        <div style="color:{c['text_dim']}; font-size:12px; padding-top:8px;">
            <b style="color:{c['text']}">{len(active)}</b> active ·
            <b style="color:{c['text']}">{MAX_MANAGED_ACCOUNTS - len(active)}</b> slots available
        </div>
        """, unsafe_allow_html=True)
    with _btn:
        if st.button("🔄 Refresh", use_container_width=True):
            st.session_state["_mgmt_stats"] = {}
            for acct in active:
                with st.spinner(f"Fetching {acct['nickname']}…"):
                    st.session_state["_mgmt_stats"][acct["slot"]] = fetch_mt5_account_stats(
                        login    = int(acct["mt5_login"]),
                        password = acct["mt5_password"],
                        server   = acct["mt5_server"],
                    )
            st.session_state["_mgmt_ts"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    if st.session_state.get("_mgmt_ts"):
        st.caption(f"Last refreshed: {st.session_state['_mgmt_ts']}")

    stats_cache = st.session_state.get("_mgmt_stats", {})
    _acct_cols  = st.columns(min(len(active), 3))

    for idx, acct in enumerate(active):
        slot     = acct.get("slot", idx + 1)
        nick     = acct.get("nickname", f"Client {slot}")
        plan     = acct.get("plan", "Standard")
        since    = acct.get("start_date", "—")
        init_bal = float(acct.get("initial_balance", 0))
        stats    = stats_cache.get(slot)

        with _acct_cols[idx % len(_acct_cols)]:
            if stats:
                balance  = stats["balance"]
                equity   = stats["equity"]
                open_pnl = stats["open_pnl"]
                n_pos    = stats["open_positions"]
                currency = stats["currency"]
                gain_pct = ((balance - init_bal) / init_bal * 100) if init_bal else 0
                gain_col = c["success"] if gain_pct >= 0 else c["danger"]
                opnl_col = c["success"] if open_pnl >= 0 else c["danger"]
                st.markdown(f"""
                <div class="bot-card" style="padding:22px; margin-bottom:14px; position:relative;">
                    <span style="position:absolute;top:12px;right:12px;background:{c['success']}22;
                                 color:{c['success']};font-size:9px;font-weight:700;letter-spacing:2px;
                                 padding:3px 8px;border-radius:20px;border:1px solid {c['success']}44;">
                        ● LIVE
                    </span>
                    <div style="font-family:'Orbitron',monospace;font-size:13px;font-weight:700;
                                color:{c['accent']};letter-spacing:1px;margin-bottom:3px;">{nick}</div>
                    <div style="font-size:11px;color:{c['text_dim']};margin-bottom:18px;">
                        {plan} · Since {since}
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
                        <div>
                            <div style="font-size:9px;color:{c['text_dim']};letter-spacing:1px;">BALANCE</div>
                            <div style="font-size:18px;font-weight:700;color:{c['text']};">{currency} {balance:,.2f}</div>
                        </div>
                        <div>
                            <div style="font-size:9px;color:{c['text_dim']};letter-spacing:1px;">EQUITY</div>
                            <div style="font-size:18px;font-weight:700;color:{c['text']};">{currency} {equity:,.2f}</div>
                        </div>
                        <div>
                            <div style="font-size:9px;color:{c['text_dim']};letter-spacing:1px;">TOTAL GAIN</div>
                            <div style="font-size:15px;font-weight:700;color:{gain_col};">{gain_pct:+.2f}%</div>
                        </div>
                        <div>
                            <div style="font-size:9px;color:{c['text_dim']};letter-spacing:1px;">OPEN P&L</div>
                            <div style="font-size:15px;font-weight:700;color:{opnl_col};">{'+' if open_pnl >= 0 else ''}{open_pnl:.2f}</div>
                        </div>
                    </div>
                    <div style="font-size:11px;color:{c['text_dim']};">
                        {'🟢 ' + str(n_pos) + ' position(s) open' if n_pos > 0 else '⚪ No open positions'}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="bot-card" style="padding:22px;margin-bottom:14px;opacity:0.65;text-align:center;">
                    <div style="font-family:'Orbitron',monospace;font-size:13px;font-weight:700;
                                color:{c['accent']};margin-bottom:4px;">{nick}</div>
                    <div style="font-size:11px;color:{c['text_dim']};margin-bottom:16px;">
                        {plan} · Since {since}
                    </div>
                    <div style="color:{c['text_dim']};font-size:12px;">Hit <b>Refresh</b> to load live stats</div>
                </div>
                """, unsafe_allow_html=True)

    remaining = MAX_MANAGED_ACCOUNTS - len(active)
    if remaining > 0:
        st.markdown("<br>", unsafe_allow_html=True)

# ── Managed Trading CTA ───────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
remaining = MAX_MANAGED_ACCOUNTS - len(active)
st.markdown(f"""
<div style="text-align:center; padding:28px 24px; background:{c['bg2']};
            border:1px solid {c['card_border']}; border-radius:16px; max-width:640px; margin:0 auto;">
    <div style="font-family:'Orbitron',monospace; font-size:11px;
                color:{c['text_dim']}; letter-spacing:3px; margin-bottom:12px;">
        INTERESTED IN MANAGED TRADING?
    </div>
    <div style="color:{c['text']}; font-size:14px; line-height:1.8; margin-bottom:20px;">
        The bot manages up to <b>{MAX_MANAGED_ACCOUNTS} client accounts</b> simultaneously.<br>
        ICT + SMC + AI strategy · ATR-based risk management · Full transparency.<br>
        <span style="color:{c['success'] if remaining > 0 else c['danger']}; font-weight:600;">
            {'✅ ' + str(remaining) + ' slot' + ('s' if remaining != 1 else '') + ' currently available'
             if remaining > 0 else '⛔ All slots currently filled — enquire to join the waitlist'}
        </span>
    </div>
    <a href="{ADMIN_TG_LINK}" target="_blank"
       style="display:inline-block; border:1px solid {c['accent']}; color:{c['accent']};
              font-family:'Orbitron',monospace; font-size:11px; font-weight:700;
              letter-spacing:2px; padding:11px 28px; border-radius:8px; text-decoration:none;
              background:{c['accent']}11;">
        💬 MESSAGE THE ADMIN
    </a>
    <div style="color:{c['text_dim']}; font-size:11px; margin-top:12px;">
        Watch the accounts above first · then reach out when you're ready
    </div>
</div>
""", unsafe_allow_html=True)

# ── Risk disclaimer ───────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
st.markdown(f"""
<div style="text-align:center; font-size:11px; color:{c['text_dim']}; max-width:600px; margin:0 auto;
            padding:12px 0; border-top:1px solid {c['card_border']};">
    ⚠️ Forex trading involves significant risk of loss. Past performance is not indicative of
    future results. Capital is not guaranteed.
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN GATE — hidden 🔐 icon at far bottom-right
# Cycles: idle → confirm → password → (auth OR goto_tg)
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("<br>", unsafe_allow_html=True)
st.markdown(
    f'<hr style="border:none;border-top:1px solid {c["card_border"]};margin:0 0 6px 0;">',
    unsafe_allow_html=True,
)

_step = st.session_state["_gate_step"]

if _step == "idle":
    _, _lock_col = st.columns([15, 1])
    with _lock_col:
        if st.button("🔐", help="Admin", key="gate_open"):
            st.session_state["_gate_step"] = "confirm"
            st.rerun()

elif _step == "confirm":
    _, _mid, _ = st.columns([2, 2, 2])
    with _mid:
        st.markdown(f"""
        <div style="text-align:center; padding:24px 20px; background:{c['bg2']};
                    border:1px solid {c['card_border']}; border-radius:12px; margin-bottom:12px;">
            <div style="font-family:'Orbitron',monospace; font-size:10px; letter-spacing:3px;
                        color:{c['text_dim']}; margin-bottom:14px;">ACCESS VERIFICATION</div>
            <div style="color:{c['text']}; font-size:15px; margin-bottom:8px;">Are you the admin?</div>
        </div>
        """, unsafe_allow_html=True)
        _y, _n = st.columns(2)
        with _y:
            if st.button("✅  Yes, I am", use_container_width=True, key="gate_yes"):
                st.session_state["_gate_step"]    = "password"
                st.session_state["_gate_from_no"] = False
                st.rerun()
        with _n:
            if st.button("❌  No, I'm not", use_container_width=True, key="gate_no"):
                st.session_state["_gate_step"]    = "goto_tg"
                st.session_state["_gate_from_no"] = True
                st.rerun()

elif _step == "password":
    _attempts_left = 3 - st.session_state["_gate_attempts"]
    _, _mid, _ = st.columns([2, 2, 2])
    with _mid:
        st.markdown(f"""
        <div style="text-align:center; padding:22px 20px; background:{c['bg2']};
                    border:1px solid {c['card_border']}; border-radius:12px; margin-bottom:10px;">
            <div style="font-family:'Orbitron',monospace; font-size:10px; letter-spacing:3px;
                        color:{c['text_dim']}; margin-bottom:10px;">ADMIN LOGIN</div>
            <div style="color:{c['text_dim']}; font-size:11px;">
                {_attempts_left} attempt{'s' if _attempts_left != 1 else ''} remaining
            </div>
        </div>
        """, unsafe_allow_html=True)
        with st.form("admin_gate_form"):
            _pwd = st.text_input(
                "Password", type="password",
                label_visibility="collapsed",
                placeholder="Enter admin password",
            )
            if st.form_submit_button("🔓  Login", use_container_width=True):
                if check_admin_password(_pwd):
                    st.session_state["admin_auth"]     = True
                    st.session_state["_gate_step"]     = "idle"
                    st.session_state["_gate_attempts"] = 0
                    st.rerun()
                else:
                    st.session_state["_gate_attempts"] += 1
                    if st.session_state["_gate_attempts"] >= 3:
                        st.session_state["_gate_step"]    = "goto_tg"
                        st.session_state["_gate_from_no"] = False
                        st.rerun()
                    else:
                        st.error(f"Wrong password — {3 - st.session_state['_gate_attempts']} attempt(s) left.")

elif _step == "goto_tg":
    _from_no = st.session_state.get("_gate_from_no", True)
    _, _mid, _ = st.columns([2, 2, 2])
    with _mid:
        st.markdown(f"""
        <div style="text-align:center; padding:28px 20px; background:{c['bg2']};
                    border:1px solid {c['card_border']}; border-radius:12px;">
            <div style="font-size:28px; margin-bottom:10px;">{'👋' if _from_no else '🔒'}</div>
            <div style="font-family:'Orbitron',monospace; font-size:13px; font-weight:700;
                        color:{c['accent']}; letter-spacing:1px; margin-bottom:8px;">
                {'No problem!' if _from_no else 'Too many failed attempts.'}
            </div>
            <div style="color:{c['text_dim']}; font-size:13px; margin-bottom:20px;">
                {'Reach out to the admin directly:' if _from_no else 'Message the admin on Telegram:'}
            </div>
            <a href="{ADMIN_TG_LINK}" target="_blank" class="cta-link"
               style="font-size:13px; padding:10px 28px;">
                💬 &nbsp; Message Admin on Telegram
            </a>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("← Back", key="gate_back"):
            st.session_state["_gate_step"]     = "idle"
            st.session_state["_gate_attempts"] = 0
            st.session_state["_gate_from_no"]  = False
            st.rerun()
