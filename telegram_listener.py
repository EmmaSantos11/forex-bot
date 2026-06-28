import time
import os
import MetaTrader5 as mt5
from core.notifications import get_telegram_updates, send_telegram_message
from dotenv import load_dotenv
from mt5_connector import connect_mt5
from live_trader import MAGIC

load_dotenv()

LOGIN = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER = os.getenv("MT5_SERVER", "")

def close_all_positions():
    """Closes all currently open positions in MT5."""
    positions = mt5.positions_get()
    if positions is None or len(positions) == 0:
        return "No open positions found to close."

    closed_count = 0
    for pos in positions:
        # Determine order type to close (Buy needs a Sell, Sell needs a Buy)
        tick      = mt5.symbol_info_tick(pos.symbol)
        sym_info  = mt5.symbol_info(pos.symbol)
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price      = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        # Match the same dynamic fill-mode detection used in live_trader.py
        filling_type = (
            mt5.ORDER_FILLING_IOC if sym_info and (sym_info.filling_mode & 2)
            else mt5.ORDER_FILLING_FOK
        )

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": MAGIC,
            "comment": "Telegram Emergency Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            closed_count += 1
        else:
            print(f"❌ Failed to close position {pos.ticket}: {result.comment}")

    return f"Emergency Stop Complete. Closed {closed_count} positions."

def process_commands():
    print("👂 Bot Listener Active... (Press Ctrl+C to stop)")
    last_update_id = 0
    authorized_user = os.getenv("TELEGRAM_CHAT_ID")
    
    # Retry loop to prevent crashing if the terminal is busy starting up
    connected = False
    for _ in range(5):
        if connect_mt5(LOGIN, PASSWORD, SERVER):
            connected = True
            break
        print("⏳ Listener waiting 5 seconds before retrying MT5 connection...")
        time.sleep(5)
        
    if not connected:
        print("❌ Could not connect to MT5 after multiple attempts. Exiting listener.")
        return

    while True:
        updates = get_telegram_updates(offset=last_update_id + 1)
        for update in updates:
            last_update_id = update.get("update_id")
            message = update.get("message", {})
            text = message.get("text", "").lower()
            chat_id = str(message.get("chat", {}).get("id"))

            print(f"📩 Received: '{text}' from {chat_id}")

            # Only respond to your specific Chat ID for security
            if chat_id == authorized_user and text in ["/start", "start", "hey", "hello"]:
                msg = (
                    "👋 <b>Forex AI Bot Command Center Active</b>\n\n"
                    "Available commands:\n"
                    "🔹 <code>/status</code> - Check account balance & profit\n"
                    "🔹 <code>/close</code> - Emergency close all positions"
                )
                send_telegram_message(msg)

            elif chat_id == authorized_user and text in ["/status", "status"]:
                account_info = mt5.account_info()
                if account_info:
                    msg = (
                        f"📊 <b>Current Account Status</b>\n"
                        f"Balance: ${account_info.balance:.2f}\n"
                        f"Equity: ${account_info.equity:.2f}\n"
                        f"Profit: ${account_info.profit:.2f}\n"
                    )
                    send_telegram_message(msg)

            elif chat_id == authorized_user and text in ["/close", "close"]:
                send_telegram_message("⚠️ <b>Emergency Signal Received!</b>\nAttempting to close all trades...")
                result_msg = close_all_positions()
                send_telegram_message(result_msg)
            
            elif chat_id != authorized_user:
                print(f"🚫 Unauthorized access attempt from {chat_id}")
            elif chat_id == authorized_user:
                # Reply to the user so they know the bot is alive
                send_telegram_message(f"❓ Unknown command: <b>{text}</b>\nType <code>/start</code> to see what I can do.")
        
        time.sleep(5) # Poll every 5 seconds

if __name__ == "__main__":
    try:
        process_commands()
    except KeyboardInterrupt:
        print("\n👋 Telegram Listener stopped.")