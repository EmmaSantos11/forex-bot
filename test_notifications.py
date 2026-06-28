import os
from dotenv import load_dotenv
from core.notifications import send_telegram_message

load_dotenv()

if __name__ == "__main__":
    send_telegram_message("🔔 <b>Test Successful!</b>\nYour Forex AI Bot is now connected to Telegram.")