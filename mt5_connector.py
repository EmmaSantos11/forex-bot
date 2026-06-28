import os
import MetaTrader5 as mt5

# Only real executable paths — no directories
_MT5_PATHS = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
]

def connect_mt5(login, password, server):
    """
    Connects to MT5. Tries two strategies:
      1. No path — works when MT5 is already open (most common case)
      2. Known install paths — fallback if terminal wasn't running
    MT5 must be installed and the account must be reachable.
    """
    # Strategy 1: MT5 is already open — just authenticate
    if mt5.initialize(login=login, password=password, server=server):
        info = mt5.terminal_info()
        if info and info.connected:
            print("✅ Connected to MT5")
            return True
        mt5.shutdown()

    # Strategy 2: Try launching from a known install path
    for path in _MT5_PATHS:
        if not os.path.isfile(path):
            continue
        if mt5.initialize(path, login=login, password=password, server=server):
            info = mt5.terminal_info()
            if info and info.connected:
                print("✅ Connected to MT5")
                return True
            mt5.shutdown()

    # Nothing worked
    print("❌ MT5 connection failed.")
    print(f"   Error: {mt5.last_error()}")
    print()
    print("   Fix: Open MetaTrader 5, log into your account, then run the bot again.")
    print(f"   Server: '{server}'  |  Login: {login}")
    return False


def is_terminal_connected():
    """Checks if the MT5 terminal is connected to the broker server."""
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        return False
    return terminal_info.connected


def shutdown_mt5():
    mt5.shutdown()