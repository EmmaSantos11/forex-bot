
# multi_timeframe.py
import pandas as pd

def resample_timeframes(df):
    df = df.copy()
    df.index = pd.to_datetime(df.index)  # ensure datetime index

    timeframes = {
        "5min": df,
        "15min": df.resample('15min').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).ffill(),
        "30min": df.resample('30min').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).ffill(),
        "1h": df.resample('1h').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).ffill(),
        "4h": df.resample('4h').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).ffill(),
        "1d": df.resample('1D').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).ffill(),
    }

    return timeframes