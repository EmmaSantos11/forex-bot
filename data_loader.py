"""
Data loading utilities for Mr Bera's_Bot.
Supports CSV files and synthetic demo data for testing.
"""
import pandas as pd
import numpy as np
from pathlib import Path


REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


def load_csv(filepath: str, datetime_col: str = "time") -> pd.DataFrame:
    """Load OHLCV data from a CSV file."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {filepath}")

    df = pd.read_csv(path, parse_dates=[datetime_col], index_col=datetime_col)
    df.columns = [c.lower() for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    df.sort_index(inplace=True)
    df.dropna(subset=list(REQUIRED_COLUMNS), inplace=True)
    return df


def generate_demo_data(
    n_bars: int = 2000,
    start: str = "2023-01-01",
    freq: str = "1h",
    seed: int = 42,
    symbol: str = "EURUSD",
) -> pd.DataFrame:
    """
    Generate synthetic OHLCV data for testing without a live data feed.
    Uses geometric Brownian motion to simulate realistic price movement.
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range(start=start, periods=n_bars, freq=freq)

    # GBM parameters
    mu = 0.0001        # slight upward drift
    sigma = 0.0008     # hourly volatility
    s0 = 1.1000        # EURUSD-like starting price

    log_returns = rng.normal(mu, sigma, n_bars)
    close = s0 * np.exp(np.cumsum(log_returns))

    # Generate realistic OHLCV from close series
    hl_spread = close * sigma * rng.uniform(0.5, 2.0, n_bars)
    high  = close + hl_spread * rng.uniform(0.3, 1.0, n_bars)
    low   = close - hl_spread * rng.uniform(0.3, 1.0, n_bars)
    open_ = np.roll(close, 1)
    open_[0] = s0
    volume = rng.integers(500, 5000, n_bars).astype(float)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
    df.index.name = "time"
    print(f"Generated {n_bars} bars of synthetic {symbol} data "
          f"({index[0].date()} → {index[-1].date()})")
    return df


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample OHLCV data to a higher timeframe (e.g. '1h' → '4h')."""
    return df.resample(timeframe).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
