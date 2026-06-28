import requests
import os

def send_telegram_message(message):
    """Sends a notification message to a Telegram bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        # Silently skip if credentials aren't set
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🤖 <b>Forex AI Bot</b>\n\n{message}",
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        print(f"⚠️ Telegram Notification failed: {e}")

def send_telegram_to(chat_id: str, message: str):
    """Sends a reply to a specific chat ID (used for multi-user responses)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🤖 <b>Forex AI Bot</b>\n\n{message}",
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Telegram reply failed: {e}")


def send_to_channel(message: str):
    """Broadcasts a public-facing message to the Telegram channel.

    Uses TELEGRAM_CHANNEL_ID from .env (must include the -100 prefix,
    e.g. -1001003955989151).  Silently skips if the variable is not set.
    """
    token      = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    if not token or not channel_id:
        return
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":    channel_id,
        "text":       f"🤖 <b>Mr Bera's Bot</b>\n\n{message}",
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Channel broadcast failed: {e}")


def send_photo_to_channel(photo_bytes: bytes, caption: str = ""):
    """Sends a PNG chart image to the Telegram channel."""
    token      = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    if not token or not channel_id or not photo_bytes:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        requests.post(
            url,
            data={"chat_id": channel_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("chart.png", photo_bytes, "image/png")},
            timeout=30,
        )
    except Exception as e:
        print(f"⚠️ Chart photo send failed: {e}")


def clear_telegram_webhook():
    """Deletes any active webhook so long-polling (getUpdates) can work without 409 conflicts.
    Call once at bot startup before starting the listener thread."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
        if r.status_code == 200 and r.json().get("result"):
            print("📱 Telegram webhook cleared — long-polling active.")
    except Exception:
        pass


def get_telegram_updates(offset=None):
    """Checks for new messages sent to the bot. Returns [] on any error."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return []

    url    = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": 10, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset

    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get("result", [])
        # Surface non-200 errors (409 Conflict, 401 Unauthorized, 429 Rate Limit, etc.)
        try:
            desc = response.json().get("description", "no description")
        except Exception:
            desc = response.text[:120]
        raise RuntimeError(f"HTTP {response.status_code} — {desc}")
    except Exception as e:
        raise   # re-raise so _telegram_loop can track failures and back off