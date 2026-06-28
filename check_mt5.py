"""
MT5 Connection Diagnostic
Run:  python check_mt5.py
"""
import os
import glob
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()
LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER   = os.getenv("MT5_SERVER", "")

print(f"Credentials loaded:")
print(f"  Login  : {LOGIN}")
print(f"  Server : {SERVER}")
print(f"  Pass   : {'*' * len(PASSWORD)}")
print()

# ── Find every terminal64.exe on this machine ──────────────────────────────
search_paths = [
    r"C:\Program Files\**\terminal64.exe",
    r"C:\Program Files (x86)\**\terminal64.exe",
    rf"C:\Users\{os.getenv('USERNAME','')}\AppData\Roaming\MetaQuotes\Terminal\**\terminal64.exe",
]

found_terminals = []
for pattern in search_paths:
    found_terminals += glob.glob(pattern, recursive=True)

print(f"Found {len(found_terminals)} MT5 terminal(s) on your PC:")
for t in found_terminals:
    print(f"  {t}")
print()

# ── Try connecting: no path first, then each found terminal ───────────────
attempts = [None] + found_terminals   # None = let library find running terminal

for path in attempts:
    label = path if path else "no path (auto-detect)"
    print(f"Trying: {label}")

    kwargs = {"login": LOGIN, "password": PASSWORD, "server": SERVER}
    if path:
        kwargs["path"] = path

    if mt5.initialize(**kwargs):
        info    = mt5.terminal_info()
        account = mt5.account_info()

        print(f"\n✅ Connected!")
        print(f"   Terminal : {info.path}")
        print(f"   Connected: {info.connected}")

        if account:
            print(f"\n   Account  : {account.login}")
            print(f"   Server   : {account.server}")
            print(f"   Balance  : ${account.balance:.2f}")
            print(f"   Currency : {account.currency}")
            print(f"\n✅ Everything looks good. Run: python main.py")
        else:
            print(f"\n⚠️  Terminal found but not logged in.")
            print(f"   Error: {mt5.last_error()}")
            print(f"   → Log into account {LOGIN} inside MT5 and try again.")

        mt5.shutdown()
        break

    else:
        print(f"   Failed: {mt5.last_error()}")
        mt5.shutdown()

else:
    print("\n❌ All connection attempts failed.")
    print("\nNext steps:")
    print("  1. Open the Exness MT5 terminal on your PC")
    print("  2. Log into your account (Login: 435464769, Server: Exness-MT5Trial9)")
    print("  3. Enable AutoTrading (green button in MT5 toolbar)")
    print("  4. Run this script again")
