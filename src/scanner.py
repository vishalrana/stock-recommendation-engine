"""
Scanner Module
==============
Purpose: Apply Layer 1 filters and detect price-action patterns for qualifying stocks.

Single Responsibility: Filter logic, pattern classification, and signal creation only.
"""

import logging
from typing import Dict, List, Tuple, Optional
import pandas as pd

from config import (
    RSI_MIN_THRESHOLD,
    RSI_CURRENT_MIN,
    RSI_CURRENT_MAX,
    VOLUME_MULTIPLIER,
)
from patterns import detect_pattern

logger = logging.getLogger(__name__)


class SignalQualifier:
    """
    Evaluates whether a stock qualifies for Layer 1 filter.
    
    Filter Logic (ALL conditions must be met):
    1. Price > 50 DMA > 200 DMA
    2. Minimum RSI in last 10 days < 45
    3. Current RSI between 45 and 65
    4. Today's volume > 1.5 × 20-day average volume
    """
    
    @staticmethod
    def passes_price_filter(price: float, dma_50: float, dma_200: float) -> bool:
        """Check if Price > 50 DMA > 200 DMA."""
        return price > dma_50 > dma_200
    
    @staticmethod
    def passes_rsi_min_filter(min_rsi_10d: float, threshold: float = RSI_MIN_THRESHOLD) -> bool:
        """Check if minimum RSI in last 10 days < threshold (45)."""
        return min_rsi_10d < threshold
    
    @staticmethod
    def passes_rsi_current_filter(
        rsi_current: float,
        min_threshold: float = RSI_CURRENT_MIN,
        max_threshold: float = RSI_CURRENT_MAX,
    ) -> bool:
        """Check if current RSI is between min and max thresholds (45-65)."""
        return min_threshold <= rsi_current <= max_threshold
    
    @staticmethod
    def passes_volume_filter(vol_current: float, vol_20d_avg: float, multiplier: float = VOLUME_MULTIPLIER) -> bool:
        """Check if today's volume > multiplier × 20-day average volume."""
        return vol_current > (vol_20d_avg * multiplier)
    
    @staticmethod
    def check_all_filters(
        price: float,
        dma_50: float,
        dma_200: float,
        rsi_current: float,
        min_rsi_10d: float,
        vol_20d_avg: float,
        vol_current: float,
    ) -> Tuple[bool, Dict[str, bool]]:
        """
        Check all Layer 1 filters.
        
        Args:
            price: Current closing price
            dma_50: 50-day moving average
            dma_200: 200-day moving average
            rsi_current: Current RSI(14)
            min_rsi_10d: Minimum RSI from last 10 days
            vol_20d_avg: 20-day average volume
            vol_current: Current day volume
            
        Returns:
            Tuple of (passes_all_filters: bool, filter_results: dict)
            
            filter_results dict contains:
            {
                "price_filter": True/False,
                "rsi_min_filter": True/False,
                "rsi_current_filter": True/False,
                "volume_filter": True/False,
            }
        """
        filters = {
            "price_filter": SignalQualifier.passes_price_filter(price, dma_50, dma_200),
            "rsi_min_filter": SignalQualifier.passes_rsi_min_filter(min_rsi_10d),
            "rsi_current_filter": SignalQualifier.passes_rsi_current_filter(rsi_current),
            "volume_filter": SignalQualifier.passes_volume_filter(vol_current, vol_20d_avg),
        }
        
        passes_all = all(filters.values())
        
        return passes_all, filters


def scan_signals(
    indicators_dict: Dict[str, Optional[pd.DataFrame]],
    ticker_list: List[str],
) -> Tuple[List[Dict], int, int]:
    """
    Scan all tickers and identify qualified signals.
    
    Args:
        indicators_dict: Dictionary mapping ticker -> DataFrame with indicators
        ticker_list: List of all processed tickers
        
    Returns:
        Tuple of (qualifying_signals: list, processed_count: int, qualified_count: int)
        
        Each signal in the list is a dict:
        {
            "ticker": "AAPL",
            "date": "2024-01-15",
            "price": 180.25,
            "dma_50": 175.30,
            "dma_200": 170.15,
            "rsi_14": 55.2,
            "current_rsi": 55.2,
            "min_rsi_10d": 42.1,
            "volume_20d_avg": 52000000,
            "volume_current": 80000000,
            "volume_ratio": 1.54,
            "pattern": "Bullish Engulfing",
            "entry_price": 181.47,
            "filters_passed": {...},
        }
    """
    signals = []
    processed = 0
    qualified = 0
    
    logger.info("=" * 60)
    logger.info("LAYER 1 SIGNAL SCAN")
    logger.info("=" * 60)
    
    for ticker in ticker_list:
        df = indicators_dict.get(ticker)
        
        if df is None or df.empty:
            logger.debug(f"{ticker}: No data available, skipping")
            continue
        
        processed += 1
        
        # Import here to avoid circular imports
        from indicators import get_indicator_values
        
        indicator_values = get_indicator_values(df, ticker)
        
        if indicator_values is None:
            logger.debug(f"{ticker}: Insufficient indicator data")
            continue
        
        price, dma_50, dma_200, rsi_current, min_rsi_10d, vol_20d_avg, vol_current = indicator_values
        
        # Check all filters
        passes_all, filter_results = SignalQualifier.check_all_filters(
            price, dma_50, dma_200, rsi_current, min_rsi_10d, vol_20d_avg, vol_current
        )
        
        # Log filter results
        filter_status = " | ".join([f"{k}: {'PASS' if v else 'FAIL'}" for k, v in filter_results.items()])
        logger.debug(f"{ticker}: {filter_status}")
        
        if passes_all:
            pattern_name, entry_price = detect_pattern(df)
            if pattern_name is None or entry_price is None:
                logger.debug(f"{ticker}: Layer 1 passed, but no price-action pattern detected")
                continue

            qualified += 1
            volume_ratio = vol_current / vol_20d_avg if vol_20d_avg > 0 else 0
            
            latest_date = df.index[-1]
            if hasattr(latest_date, 'date'):
                signal_date = latest_date.date().isoformat()
            else:
                signal_date = str(latest_date)

            signal = {
                "ticker": ticker,
                "date": signal_date,
                "price": round(price, 2),
                "dma_50": round(dma_50, 2),
                "dma_200": round(dma_200, 2),
                "rsi_14": round(rsi_current, 2),
                "current_rsi": round(rsi_current, 2),
                "min_rsi_10d": round(min_rsi_10d, 2),
                "volume_20d_avg": int(vol_20d_avg),
                "volume_current": int(vol_current),
                "volume_ratio": round(volume_ratio, 2),
                "pattern": pattern_name,
                "entry_price": round(entry_price, 2),
            }
            
            signals.append(signal)
            
            logger.info(
                f"[QUALIFIED] {ticker} | {pattern_name} | Entry: ${entry_price:.2f} | "
                f"Price: ${price:.2f} | RSI: {rsi_current:.1f} | Vol Ratio: {volume_ratio:.2f}x"
            )
    
    logger.info(f"Scan Complete: {processed} processed, {qualified} qualified")
    logger.info("=" * 60)
    
    return signals, processed, qualified


def signals_to_dataframe(signals: List[Dict]) -> pd.DataFrame:
    """
    Convert signal list to pandas DataFrame for CSV export.
    
    Args:
        signals: List of signal dictionaries
        
    Returns:
        DataFrame with all signal data
    """
    if not signals:
        return pd.DataFrame()
    
    return pd.DataFrame(signals)
