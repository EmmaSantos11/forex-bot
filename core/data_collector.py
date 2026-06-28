# core/data_collector.py

import requests
import pandas as pd
import os
import time

PAIRS_MAP = {
    "EURUSD": "EURUSDT",
    "GBPUSD": "GBPUSDT",  # synthetic (not perfect but usable)
    "BTCUSD": "BTCUSDT"
}

INTERVAL = "5m"
OUTPUT_FOLDER = "data"


def fetch_binance(symbol, interval="5m", limit=1000):

    print(f"📡 Fetching {symbol} from Binance...")

    url = "https://api.binance.com/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    response = requests.get(url, params=params)
    data = response.json()

    if not isinstance(data, list):
        print(f"❌ API error: {data}")
        return None

    df = pd.DataFrame(data, columns=[
        "Timestamp", "Open", "High", "Low", "Close", "Volume",
        "CloseTime", "QuoteVolume", "Trades",
        "TakerBuyBase", "TakerBuyQuote", "Ignore"
    ])

    df = df[["Timestamp", "Open", "High", "Low", "Close", "Volume"]]

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit='ms')
    df.set_index("Timestamp", inplace=True)

    df = df.astype(float)

    return df


def save_to_csv(df, pair):

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    file_path = f"{OUTPUT_FOLDER}/{pair}.csv"

    if os.path.exists(file_path):
        old_df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        df = pd.concat([old_df, df])

    df = df[~df.index.duplicated(keep='last')]
    df.sort_index(inplace=True)

    df.to_csv(file_path)

    print(f"✅ Saved {pair} → {file_path}")


if __name__ == "__main__":

    for pair, symbol in PAIRS_MAP.items():

        df = fetch_binance(symbol)

        if df is not None:
            save_to_csv(df, pair)

        time.sleep(1)