from __future__ import annotations

import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range.
    df must have columns: high, low, close
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """20-period simple moving average of volume."""
    return sma(df["volume"], period)


def add_indicators(df: pd.DataFrame, fast: int = 20, slow: int = 50, atr_period: int = 14) -> pd.DataFrame:
    """
    Add all standard indicators to a candle DataFrame in-place.
    Columns added:
      ema_fast, ema_slow, atr, rsi, vol_sma, vol_ratio
    """
    df = df.copy()
    df["ema_fast"]  = ema(df["close"], fast)
    df["ema_slow"]  = ema(df["close"], slow)
    df["atr"]       = atr(df, atr_period)
    df["rsi"]       = rsi(df["close"])
    df["vol_sma"]   = volume_sma(df)
    df["vol_ratio"] = df["volume"] / df["vol_sma"]   # >1.5 = high-volume bar
    return df


def is_uptrend(df: pd.DataFrame) -> bool:
    """True if latest bar has fast EMA above slow EMA."""
    return float(df["ema_fast"].iloc[-1]) > float(df["ema_slow"].iloc[-1])


def is_high_volume(df: pd.DataFrame, threshold: float = 1.5) -> bool:
    """True if latest bar volume is above threshold × 20-day average."""
    return float(df["vol_ratio"].iloc[-1]) >= threshold
