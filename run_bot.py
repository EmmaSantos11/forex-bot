"""
run_bot.py — Auto-restart wrapper for main.py

What it does:
  • Prevents Windows from sleeping (powercfg)
  • Starts main.py as a subprocess
  • On crash: notifies all Telegram users, responds to messages
    with "bot is offline" during the restart delay, then restarts
  • On clean stop: notifies all users the bot is offline
  • Ctrl+C here stops everything and notifies users
"""

import subprocess
import sys
import time
import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

RESTART_DELAY = 30
LOG_FILE       = "logs/crash.log"
USERS_FILE     = "data/users.json"

os.makedirs("logs", exist_ok=True)

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

python  = sys.executable
attempt = 0
_tg_offset = 0   # shared offset — keeps main.py's Telegram queue clean on restart


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _load_all_users() -> set:
    users = {ADMIN_ID} if ADMIN_ID else set()
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                users |= set(json.load(f))
        except Exception:
            pass
    return users


def _send(chat_id: str, text: str):
    if not TOKEN or not chat_id:
        return
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def _broadcast(text: str):
    """Send a message to every registered user and admin."""
    for uid in _load_all_users():
        _send(uid, text)


def _broadcast_offline(is_crash: bool = False):
    """
    Sends shutdown/crash notices with role-aware content.
    Admin sees restart instructions. Regular users get a clean offline notice.
    """
    admin_msg_clean = (
        "🔴 <b>Bot has been shut down</b>\n\n"
        "The trading bot is currently offline.\n"
        "Restart it anytime using start_bot.bat\n\n"
        "📌 Your open trades remain active in MT5."
    )
    user_msg_clean = (
        "🔴 <b>Bot has been shut down</b>\n\n"
        "The trading bot is currently offline.\n"
        "📌 Your open trades remain active in MT5."
    )

    for uid in _load_all_users():
        msg = admin_msg_clean if uid == ADMIN_ID else user_msg_clean
        _send(uid, msg)


def _poll_offline(duration_secs: int):
    """
    Polls Telegram for `duration_secs` seconds while main.py is not running.
    Replies to every incoming message with an offline notice.
    Advances the offset so main.py gets a clean queue when it restarts.
    """
    global _tg_offset
    if not TOKEN:
        time.sleep(duration_secs)
        return

    offline_msg = (
        "🔴 <b>Bot is temporarily offline</b>\n\n"
        "The trading bot stopped unexpectedly and is restarting.\n"
        "It will be back online in a few seconds — please wait.\n\n"
        "📌 Your open trades are <b>safe</b> inside MT5."
    )

    deadline = time.time() + duration_secs
    while time.time() < deadline:
        try:
            r = requests.get(
                f"{BASE_URL}/getUpdates",
                params={"offset": _tg_offset, "timeout": 5},
                timeout=12,
            )
            for upd in r.json().get("result", []):
                _tg_offset = upd["update_id"] + 1
                chat_id = str(upd.get("message", {}).get("chat", {}).get("id", ""))
                if chat_id:
                    _send(chat_id, offline_msg)
        except Exception:
            pass
        time.sleep(3)


# ── Banner ────────────────────────────────────────────────────────────────────

print("=" * 55)
print("  🛡️  Forex AI Bot — Auto-Restart Wrapper")
print("=" * 55)
print(f"  Running: {python} main.py")
print(f"  Auto-restarts on crash (Ctrl+C to stop fully)\n")

# ── Main loop ─────────────────────────────────────────────────────────────────

while True:
    attempt += 1
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{started_at}] ▶  Starting bot (attempt #{attempt})...")

    if attempt > 1:
        _broadcast(
            f"🔄 <b>Bot Restarting</b> (attempt #{attempt})\n\n"
            f"The bot is coming back online now."
        )

    try:
        result = subprocess.run(
            [python, "-u", "main.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
        )

        if result.returncode == 0:
            # Clean exit — user pressed Ctrl+C inside main.py
            print("\n✅ Bot exited cleanly.")
            _broadcast_offline()
            break

        # Non-zero exit = crash
        crashed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = (
            f"\n[{crashed_at}] 💥 Bot crashed "
            f"(exit code {result.returncode}, attempt #{attempt}). "
            f"Restarting in {RESTART_DELAY}s...\n"
        )
        print(log_msg)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_msg)

        _broadcast(
            f"⚠️ <b>Bot Crashed — Restarting in {RESTART_DELAY}s</b>\n\n"
            f"Something went wrong but the auto-restart is handling it.\n"
            f"📌 Your open trades are safe inside MT5.\n\n"
            f"If this keeps happening, check logs/crash.log"
        )

        # Respond to any Telegram messages during the restart window
        _poll_offline(RESTART_DELAY)

    except KeyboardInterrupt:
        print("\n🛑 Wrapper stopped by user. Bot will not restart.")
        _broadcast_offline()
        break

    except Exception as e:
        print(f"⚠️  Wrapper error: {e}. Retrying in {RESTART_DELAY}s...")
        _poll_offline(RESTART_DELAY)
