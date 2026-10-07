"""
Indicators Module
=================
Purpose: Calculate technical indicators: DMA, RSI, and volume statistics.

Single Responsibility: Technical indicator calculations only.
"""

import logging
from typing import Tuple, Optional, Union
import pandas as pd
import numpy as np

try:
    from src.config import (
        SHORT_MA_PERIOD,
        LONG_MA_PERIOD,
        RSI_PERIOD,
        VOLUME_MA_PERIOD,
    )
except ImportError:
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
        First (period-1) values will be NaN (no partial-window contamination)
    """
    return data.rolling(window=period, min_periods=period).mean()


def calculate_rsi(data: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI) using canonical Wilder's RMA smoothing.
    
    Formula:
        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        where Average Gain and Loss use Wilder's RMA (alpha = 1 / period),
        initialized with the simple average over the first `period` price changes.
        
    Edge cases:
        - When Average Loss == 0:
            - If Average Gain == 0: RSI = 50.0 (flat market)
            - If Average Gain > 0: RSI = 100.0
        - When Average Gain == 0 (and Average Loss > 0): RSI = 0.0
        - First (period) values are NaN.
    """
    if len(data) <= period:
        return pd.Series(np.nan, index=data.index, dtype=float)

    delta = data.diff()
    gains = delta.clip(lower=0.0).to_numpy(dtype=float)
    losses = (-delta.clip(upper=0.0)).to_numpy(dtype=float)

    avg_gain = np.full(len(data), np.nan, dtype=float)
    avg_loss = np.full(len(data), np.nan, dtype=float)

    # First valid price difference starts at index 1 (index 0 diff is NaN)
    # The first `period` price changes span indices 1 .. period
    avg_gain[period] = np.mean(gains[1:period + 1])
    avg_loss[period] = np.mean(losses[1:period + 1])

    for i in range(period + 1, len(data)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

    rsi = np.full(len(data), np.nan, dtype=float)
    for i in range(period, len(data)):
        ag = avg_gain[i]
        al = avg_loss[i]
        if np.isnan(ag) or np.isnan(al):
            continue
        if al == 0.0:
            if ag == 0.0:
                rsi[i] = 50.0
            else:
                rsi[i] = 100.0
        elif ag == 0.0:
            rsi[i] = 0.0
        else:
            rs = ag / al
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))

    return pd.Series(rsi, index=data.index, dtype=float)


def calculate_volume_ma(volume: pd.Series, period: int = VOLUME_MA_PERIOD) -> pd.Series:
    """
    Calculate volume moving average.
    
    Args:
        volume: Volume series
        period: Moving average period (default 20)
        
    Returns:
        Series with volume MA values
    """
    return volume.rolling(window=period, min_periods=period).mean()


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's canonical Average Directional Index (ADX).
    
    Formula:
        TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
        +DM_t = up_move if (up_move > down_move and up_move > 0) else 0.0
        -DM_t = down_move if (down_move > up_move and down_move > 0) else 0.0
        
        Wilder's smoothing over period N:
        First smoothed TR, +DM, -DM at index N is the sum of changes over bars 1..N.
        Subsequent: Smooth_t = Smooth_{t-1} - (Smooth_{t-1} / N) + Change_t.
        
        +DI_t = 100 * (+DM_smooth / TR_smooth)
        -DI_t = 100 * (-DM_smooth / TR_smooth)
        DX_t = 100 * (|+DI - -DI| / (+DI + -DI))
        
        Initial ADX at index 2*N - 1 is the simple mean of the first N values of DX (indices N..2*N-1).
        Subsequent: ADX_t = (ADX_{t-1} * (N - 1) + DX_t) / N.
        
        Indices prior to 2*N - 1 are NaN.
    """
    n = len(close)
    if n < 2 * period:
        return pd.Series(np.nan, index=close.index, dtype=float)

    highs = high.to_numpy(dtype=float)
    lows = low.to_numpy(dtype=float)
    closes = close.to_numpy(dtype=float)

    tr = np.zeros(n, dtype=float)
    dm_plus = np.zeros(n, dtype=float)
    dm_minus = np.zeros(n, dtype=float)

    for t in range(1, n):
        tr[t] = max(
            highs[t] - lows[t],
            abs(highs[t] - closes[t - 1]),
            abs(lows[t] - closes[t - 1]),
        )
        up_move = highs[t] - highs[t - 1]
        down_move = lows[t - 1] - lows[t]

        if up_move > down_move and up_move > 0.0:
            dm_plus[t] = up_move
        else:
            dm_plus[t] = 0.0

        if down_move > up_move and down_move > 0.0:
            dm_minus[t] = down_move
        else:
            dm_minus[t] = 0.0

    tr_smooth = np.full(n, np.nan, dtype=float)
    dm_plus_smooth = np.full(n, np.nan, dtype=float)
    dm_minus_smooth = np.full(n, np.nan, dtype=float)

    # First N changes are at indices 1 .. period
    tr_smooth[period] = np.sum(tr[1:period + 1])
    dm_plus_smooth[period] = np.sum(dm_plus[1:period + 1])
    dm_minus_smooth[period] = np.sum(dm_minus[1:period + 1])

    for t in range(period + 1, n):
        tr_smooth[t] = tr_smooth[t - 1] - (tr_smooth[t - 1] / period) + tr[t]
        dm_plus_smooth[t] = dm_plus_smooth[t - 1] - (dm_plus_smooth[t - 1] / period) + dm_plus[t]
        dm_minus_smooth[t] = dm_minus_smooth[t - 1] - (dm_minus_smooth[t - 1] / period) + dm_minus[t]

    dx = np.full(n, np.nan, dtype=float)
    for t in range(period, n):
        trs = tr_smooth[t]
        if trs == 0.0 or np.isnan(trs):
            di_p = 0.0
            di_m = 0.0
        else:
            di_p = 100.0 * (dm_plus_smooth[t] / trs)
            di_m = 100.0 * (dm_minus_smooth[t] / trs)

        di_sum = di_p + di_m
        if di_sum == 0.0:
            dx[t] = 0.0
        else:
            dx[t] = 100.0 * (abs(di_p - di_m) / di_sum)

    adx = np.full(n, np.nan, dtype=float)
    seed_adx_idx = 2 * period - 1
    adx[seed_adx_idx] = np.mean(dx[period:seed_adx_idx + 1])

    for t in range(seed_adx_idx + 1, n):
        adx[t] = (adx[t - 1] * (period - 1) + dx[t]) / period

    return pd.Series(adx, index=close.index, dtype=float)



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


def calculate_atr(
    high: Union[pd.Series, pd.DataFrame],
    low: Optional[pd.Series] = None,
    close: Optional[pd.Series] = None,
    period: int = 14
) -> pd.Series:
    """
    Calculate Average True Range (ATR) using canonical Wilder's RMA smoothing.
    Accepts either separate (high, low, close) Series or a single OHLC DataFrame.
    
    Formula:
        TR_0 = High_0 - Low_0
        TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
        Initial ATR at index period-1 is the simple mean of the first `period` TRs.
        Subsequent: ATR_t = ((period - 1) * ATR_{t-1} + TR_t) / period
        First (period - 1) values are NaN.
    """
    if isinstance(high, pd.DataFrame):
        df = high
        h_col = "HIGH" if "HIGH" in df.columns else "High" if "High" in df.columns else "high"
        l_col = "LOW" if "LOW" in df.columns else "Low" if "Low" in df.columns else "low"
        c_col = "CLOSE" if "CLOSE" in df.columns else "Close" if "Close" in df.columns else "close"
        actual_period = low if isinstance(low, int) else period
        return calculate_atr(df[h_col], df[l_col], df[c_col], period=actual_period)

    if close is None or low is None or len(close) < period:
        return pd.Series(np.nan, index=close.index if close is not None else None, dtype=float)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    tr_vals = tr.to_numpy(dtype=float)
    atr = np.full(len(close), np.nan, dtype=float)

    # Initial ATR is simple average of first `period` true ranges (indices 0 .. period - 1)
    atr[period - 1] = np.mean(tr_vals[:period])
    for i in range(period, len(close)):
        atr[i] = (atr[i - 1] * (period - 1) + tr_vals[i]) / period

    return pd.Series(atr, index=close.index, dtype=float)


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate all required indicators for a stock.
    
    Input DataFrame expected to have: Open, High, Low, Close, Volume
    (handles both uppercase and lowercase column names)
    
    Output DataFrame includes:
        Original columns + DMA_50 + DMA_200 + RSI_14 + VOLUME_MA_20 + ADX_14 + MACD_LINE + MACD_SIGNAL + MACD_HIST + EMA_20 + ATR_14
        
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
    
    # Calculate ATR_14 using canonical Wilder RMA smoothing
    df["ATR_14"] = calculate_atr(df["HIGH"], df["LOW"], df["CLOSE"], 14)
    
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
