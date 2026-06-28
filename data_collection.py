import os
import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

# Load MT5 credentials from .env
load_dotenv()
LOGIN = os.getenv("MT5_LOGIN")
PASSWORD = os.getenv("MT5_PASSWORD")
SERVER = os.getenv("MT5_SERVER")

def fetch_forex_data(symbol="EURUSD", timeframe=mt5.TIMEFRAME_H1, count=2000):
    print(f"Connecting to MT5 to fetch {symbol} data...")
    
    if not mt5.initialize():
        print("❌ MT5 initialize failed")
        return None

    if LOGIN:
        mt5.login(int(LOGIN), password=PASSWORD, server=SERVER)

    # Fetch rates
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print("❌ No data received from MT5")
        return None

    # Convert to DataFrame
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    
    # Keep and rename core columns
    df = df[['open', 'high', 'low', 'close', 'tick_volume']]
    df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    df.dropna(inplace=True)

    return df


def save_data(df):
    if not os.path.exists("data"):
        os.makedirs("data")

    df.to_csv("data/forex_data.csv")
    print("Data saved to data/forex_data.csv")


if __name__ == "__main__":
    df = fetch_forex_data()
    print(df.head())
    save_data(df)