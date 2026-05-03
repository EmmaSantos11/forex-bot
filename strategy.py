"""
Trading strategies for Mr Bera's_Bot.
Each strategy receives an OHLCV DataFrame and returns a Series of signals:
  +1 = BUY, -1 = SELL, 0 = HOLD
"""
import pandas as pd


def moving_average_crossover(df: pd.DataFrame, fast: int = 10, slow: int = 30) -> pd.Series:
    """
    Classic dual moving-average crossover.
    BUY  when fast MA crosses above slow MA.
    SELL when fast MA crosses below slow MA.
    """
    fast_ma = df["close"].rolling(fast).mean()
    slow_ma = df["close"].rolling(slow).mean()

    signal = pd.Series(0, index=df.index)
    signal[fast_ma > slow_ma] = 1
    signal[fast_ma < slow_ma] = -1

    # Only emit a signal on crossover (change point)
    signal = signal.diff().fillna(0)
    signal[signal > 0] = 1
    signal[signal < 0] = -1
    return signal


def rsi_strategy(df: pd.DataFrame, period: int = 14, oversold: int = 30, overbought: int = 70) -> pd.Series:
    """
    RSI mean-reversion strategy.
    BUY  when RSI crosses below oversold threshold.
    SELL when RSI crosses above overbought threshold.
    """
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))

    signal = pd.Series(0, index=df.index)
    signal[rsi < oversold] = 1
    signal[rsi > overbought] = -1

    signal = signal.diff().fillna(0)
    signal[signal > 0] = 1
    signal[signal < 0] = -1
    return signal


def bollinger_band_strategy(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.Series:
    """
    Bollinger Band breakout / mean-reversion strategy.
    BUY  when price crosses below lower band (mean reversion entry).
    SELL when price crosses above upper band.
    """
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std

    signal = pd.Series(0, index=df.index)
    signal[df["close"] < lower] = 1
    signal[df["close"] > upper] = -1

    signal = signal.diff().fillna(0)
    signal[signal > 0] = 1
    signal[signal < 0] = -1
    return signal
