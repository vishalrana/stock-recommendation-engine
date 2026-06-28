"""
Indicators Module
=================
Purpose: Calculate technical indicators: DMA, RSI, and volume statistics.

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
    # Calculate daily price changes
    delta = data.diff()
    
    # Separate gains and losses
    gains = delta.where(delta > 0, 0)
    losses = -delta.where(delta < 0, 0)
    
    # Calculate exponential moving averages
    avg_gain = gains.ewm(span=period, adjust=False).mean()
    avg_loss = losses.ewm(span=period, adjust=False).mean()
    
    # Avoid division by zero
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    
    # Replace inf and invalid values with NaN
    rsi = rsi.replace([np.inf, -np.inf], np.nan)
    
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
    return volume.rolling(window=period, min_periods=1).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate all required indicators for a stock.
    
    Input DataFrame expected to have: Open, High, Low, Close, Volume
    (handles both uppercase and lowercase column names)
    
    Output DataFrame includes:
        Original columns + DMA_50 + DMA_200 + RSI_14 + VOLUME_MA_20
        
    Args:
        df: OHLCV DataFrame from yfinance
        
    Returns:
        DataFrame with all indicators calculated
    """
    df = df.copy()
    
    # Normalize column names to uppercase for consistency
    df.columns = df.columns.str.upper()
    
    # Validate required columns exist
    required_cols = ["CLOSE", "VOLUME"]
    if not all(col in df.columns for col in required_cols):
        raise ValueError(f"Missing required columns. Need: {required_cols}")
    
    # Calculate indicators using uppercase columns
    df["DMA_50"] = calculate_dma(df["CLOSE"], SHORT_MA_PERIOD)
    df["DMA_200"] = calculate_dma(df["CLOSE"], LONG_MA_PERIOD)
    df["RSI_14"] = calculate_rsi(df["CLOSE"], RSI_PERIOD)
    df["VOLUME_MA_20"] = calculate_volume_ma(df["VOLUME"], VOLUME_MA_PERIOD)
    
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
