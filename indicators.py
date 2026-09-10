"""
Indicator calculations. Pure pandas/numpy, no external TA library required.
All functions take a DataFrame with columns: open, high, low, close (oldest -> newest)
and return a pandas Series aligned to that DataFrame's index.
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def cci(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period).mean()
    mean_dev = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - sma) / (0.015 * mean_dev.replace(0, np.nan))


def bollinger_bands(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def fractal_chaos_bands(df: pd.DataFrame, period: int = 2):
    """
    Williams-fractal based Fractal Chaos Bands.
    A fractal high at i is confirmed when high[i] is the max of the
    surrounding `period` bars on each side. The band value steps forward
    and holds until the next fractal is found.
    Returns (upper_band, lower_band) as pandas Series.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    last_upper = np.nan
    last_lower = np.nan

    for i in range(period, n - period):
        window_h = highs[i - period: i + period + 1]
        window_l = lows[i - period: i + period + 1]
        if highs[i] == window_h.max():
            last_upper = highs[i]
        if lows[i] == window_l.min():
            last_lower = lows[i]
        upper[i] = last_upper
        lower[i] = last_lower

    for i in range(n - period, n):
        upper[i] = last_upper
        lower[i] = last_lower

    return pd.Series(upper, index=df.index), pd.Series(lower, index=df.index)
