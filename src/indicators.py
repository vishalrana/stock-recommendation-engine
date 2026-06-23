"""
Indicators Module
=================
Purpose: Calculate technical indicators: DMA, RSI, ADX, MACD, EMA, and volume statistics.

Single Responsibility: Technical indicator calculations only.
"""

import logging
from typing import Tuple, Optional
import pandas as pd
import numpy as np

from config import (
    SHORT_MA_PERIOD,
    LONG_MA_PERIOD,
    RSI_PERIOD,
    VOLUME_MA_PERIOD,
)

logger = logging.getLogger(__name__)


def calculate_dma(data: pd.Series, period: int) -> pd.Series:
    """
    Calculate Simple Moving Average (DMA).
    
    Args:
        data: Price series (e.g., Close prices)
        period: Moving average period (e.g., 50, 200)
        
    Returns:
        Series with DMA values (aligned with input data)
        
    Note:
        First (period-1) values will be NaN
    """
    return data.rolling(window=period, min_periods=1).mean()


def calculate_rsi(data: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI).
    
    Formula:
        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        
    Args:
        data: Price series (e.g., Close prices)
        period: RSI period (default 14)
        
    Returns:
        Series with RSI values (0-100 range)
        
    Note:
        First (period) values will be NaN due to averaging initialization
    """
    delta = data.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    rsi = pd.Series(np.nan, index=data.index, dtype=float)
    if len(data) <= period:
        return rsi

    avg_gain = gains.iloc[1 : period + 1].mean()
    avg_loss = losses.iloc[1 : period + 1].mean()

    def rsi_value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        if gain == 0:
            return 0.0
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    rsi.iloc[period] = rsi_value(avg_gain, avg_loss)

    for index in range(period + 1, len(data)):
        avg_gain = ((avg_gain * (period - 1)) + gains.iloc[index]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses.iloc[index]) / period
        rsi.iloc[index] = rsi_value(avg_gain, avg_loss)

    return rsi


def calculate_volume_ma(volume: pd.Series, period: int = VOLUME_MA_PERIOD) -> pd.Series:
    """
    Calculate volume moving average.
    
    Args:
        volume: Volume series
        period: Moving average period (default 20)
        
    Returns:
        Series with volume MA values
    """
    return volume.rolling(window=period, min_periods=1).mean().shift(1)


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's ADX. Returns a Series of ADX values (0–100).
    Values >= 20 indicate a trending market; >= 25 is a strong trend.
    """
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    dm_plus = high.diff()
    dm_minus = -low.diff()
    dm_plus = dm_plus.where((dm_plus > dm_minus) & (dm_plus > 0), 0.0)
    dm_minus = dm_minus.where((dm_minus > dm_plus) & (dm_minus > 0), 0.0)

    def wilder_smooth(series, n):
        result = series.copy().astype(float)
        result.iloc[:n] = series.iloc[:n].sum()
        for i in range(n, len(series)):
            result.iloc[i] = result.iloc[i - 1] - (result.iloc[i - 1] / n) + series.iloc[i]
        return result

    tr_smooth = wilder_smooth(tr, period)
    dm_plus_smooth = wilder_smooth(dm_plus, period)
    dm_minus_smooth = wilder_smooth(dm_minus, period)

    di_plus = 100 * dm_plus_smooth / tr_smooth
    di_minus = 100 * dm_minus_smooth / tr_smooth
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    adx = wilder_smooth(dx, period) / period
    return adx


def compute_macd(close: pd.Series,
                 fast: int = 12, slow: int = 26, signal: int = 9
                 ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (macd_line, signal_line, histogram).
    macd_line > signal_line => bullish momentum.
    histogram > 0 AND rising => strengthening bullish momentum.
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def check_rsi_pullback_recovery(rsi_series: pd.Series,
                                 lookback: int = 10,
                                 dip_threshold: float = 45.0,
                                 recovery_min: float = 45.0,
                                 recovery_max: float = 62.0) -> dict:
    """
    Validates the dip-and-recover RSI pattern:
      1. RSI must have dipped below dip_threshold within the last `lookback` bars.
      2. Current RSI must be in [recovery_min, recovery_max].

    Returns dict with keys: 'passed' (bool), 'rsi_min_10d' (float), 'current_rsi' (float).
    """
    if len(rsi_series) < lookback:
        return {"passed": False, "rsi_min_10d": None, "current_rsi": None}

    current_rsi = rsi_series.iloc[-1]
    rsi_min_10d = rsi_series.iloc[-lookback:].min()

    passed = (
        rsi_min_10d < dip_threshold
        and recovery_min <= current_rsi <= recovery_max
    )
    return {
        "passed": passed,
        "rsi_min_10d": round(float(rsi_min_10d), 2),
        "current_rsi": round(float(current_rsi), 2)
    }


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate all required indicators for a stock.
    
    Input DataFrame expected to have: Open, High, Low, Close, Volume
    (handles both uppercase and lowercase column names)
    
    Output DataFrame includes:
        Original columns + DMA_50 + DMA_200 + RSI_14 + VOLUME_MA_20 + ADX_14 + MACD_LINE + MACD_SIGNAL + MACD_HIST + EMA_20
        
    Args:
        df: OHLCV DataFrame from yfinance
        
    Returns:
        DataFrame with all indicators calculated
    """
    df = df.copy()
    
    # Normalize column names to uppercase for consistency
    df.columns = df.columns.str.upper()
    
    # Validate required columns exist
    required_cols = ["CLOSE", "VOLUME", "HIGH", "LOW"]
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f"Missing required columns. Need: {required_cols}")
    
    # Calculate indicators using uppercase columns
    df["DMA_50"] = calculate_dma(df["CLOSE"], SHORT_MA_PERIOD)
    df["DMA_200"] = calculate_dma(df["CLOSE"], LONG_MA_PERIOD)
    df["RSI_14"] = calculate_rsi(df["CLOSE"], RSI_PERIOD)
    df["VOLUME_MA_20"] = calculate_volume_ma(df["VOLUME"], VOLUME_MA_PERIOD)
    
    df["ADX_14"] = compute_adx(df["HIGH"], df["LOW"], df["CLOSE"], 14)
    macd_line, signal_line, histogram = compute_macd(df["CLOSE"], 12, 26, 9)
    df["MACD_LINE"] = macd_line
    df["MACD_SIGNAL"] = signal_line
    df["MACD_HIST"] = histogram
    df["EMA_20"] = compute_ema(df["CLOSE"], 20)
    
    return df


def get_indicator_values(
    df: pd.DataFrame, ticker: str
) -> Optional[Tuple[float, float, float, float, float, float, float]]:
    """
    Extract current and historical indicator values from DataFrame.
    
    Returns tuple of (price, dma_50, dma_200, rsi_current, min_rsi_10d, vol_20d_avg, vol_current)
    or None if data is insufficient.
    
    Args:
        df: DataFrame with calculated indicators
        ticker: Ticker symbol (for logging)
        
    Returns:
        Tuple of (price, dma_50, dma_200, rsi_current, min_rsi_10d, vol_20d_avg, vol_current)
        Returns None if any required value is NaN or missing
    """
    try:
        # Get latest row
        latest = df.iloc[-1]
        
        # Extract values - column names are now uppercase
        price = float(latest["CLOSE"])
        dma_50 = float(latest["DMA_50"])
        dma_200 = float(latest["DMA_200"])
        rsi_current = float(latest["RSI_14"])
        vol_20d_avg = float(latest["VOLUME_MA_20"])
        vol_current = float(latest["VOLUME"])
        
        # Get minimum RSI from last 10 days
        last_10_rsi = df["RSI_14"].iloc[-10:].values
        min_rsi_10d = np.nanmin(last_10_rsi)
        
        # Validate all values are valid (not NaN, inf, etc.)
        values = [price, dma_50, dma_200, rsi_current, min_rsi_10d, vol_20d_avg, vol_current]
        
        if any(np.isnan(v) or np.isinf(v) for v in values):
            logger.warning(f"{ticker}: Contains NaN or inf values")
            return None
        
        return (price, dma_50, dma_200, rsi_current, min_rsi_10d, vol_20d_avg, vol_current)
        
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as e:
        logger.warning(f"{ticker}: Failed to extract indicator values - {str(e)}")
        return None
