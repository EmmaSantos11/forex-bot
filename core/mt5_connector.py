import MetaTrader5 as mt5

def connect_mt5(login, password, server):
    path = r"C:\Program Files\MetaTrader 5\terminal64.exe"

    if not mt5.initialize(path, login=login, password=password, server=server):
        print("❌ MT5 initialization failed")
        print("Error:", mt5.last_error())
        return False

    terminal_info = mt5.terminal_info()
    if not terminal_info or not terminal_info.connected:
        print("❌ Login failed or terminal not connected to broker.")
        print("Error:", mt5.last_error())
        mt5.shutdown()
        return False

    print("✅ Connected to MT5")
    return True


def is_terminal_connected():
    """Checks if the MT5 terminal is connected to the broker server."""
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        return False
    return terminal_info.connected


def shutdown_mt5():
    mt5.shutdown()
