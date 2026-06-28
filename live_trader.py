import MetaTrader5 as mt5

MAGIC = 123456

def place_trade(symbol, signal, lot=0.01, sl=50, tp=100):

    # 🔥 ENSURE SYMBOL IS AVAILABLE
    symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        print(f"❌ Symbol {symbol} not found")
        return None

    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            print(f"❌ Failed to select symbol {symbol}")
            return None

    # 🔥 GET PRICE
    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        print("❌ Failed to get tick data")
        return None

    # Determine Filling Mode dynamically (prefer IOC/FOK if Return fails)
    # Many brokers use ORDER_FILLING_IOC or ORDER_FILLING_FOK
    # Use numeric bitmask values if constants are missing in some MT5 library versions
    # 1 = SYMBOL_FILLING_FOK, 2 = SYMBOL_FILLING_IOC
    if symbol_info.filling_mode & 2:
        filling_type = mt5.ORDER_FILLING_IOC
    else:
        filling_type = mt5.ORDER_FILLING_FOK

    price = tick.ask if signal == 1 else tick.bid
    point = symbol_info.point

    # 🔥 SL / TP CALCULATION
    if signal == 1:  # BUY
        sl_price = price - sl * point
        tp_price = price + tp * point
        order_type = mt5.ORDER_TYPE_BUY
    else:  # SELL
        sl_price = price + sl * point
        tp_price = price - tp * point
        order_type = mt5.ORDER_TYPE_SELL

    # 🔥 BUILD REQUEST
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl_price,
        "tp": tp_price,
        "deviation": 20,
        "magic": MAGIC,
        "comment": "AI BOT",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_type,
    }

    # 🔥 SEND ORDER
    result = mt5.order_send(request)

    # 🔥 FULL DEBUG
    if result is None:
        print("❌ order_send returned None")
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"❌ Trade failed: {result.retcode}")
        print("🔍 Full error:", result)
    else:
        print(f"✅ Trade executed successfully on {symbol}")

    return result