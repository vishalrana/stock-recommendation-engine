"""
Cache Manager V2 - Date-partitioned Parquet storage
====================================================
Upgrades from per-ticker files to per-day files for better I/O performance.

Old: data/cache/{ticker}.parquet (500+ files)
New: data/cache/by_date/{YYYY-MM-DD}.parquet (1 file per day)
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class CacheManagerV2:
    """Manages date-partitioned price data cache."""
    
    def __init__(self, cache_dir: str = "data/cache/by_date"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def _get_cache_path(self, date_str: str) -> str:
        """Get cache file path for a specific date."""
        return os.path.join(self.cache_dir, f"{date_str}.parquet")
    
    def get_data_for_date(self, date_str: str) -> Optional[pd.DataFrame]:
        """
        Load all ticker data for a specific date from cache.
        Returns MultiIndex DataFrame (tickers x OHLCV) or None if not cached.
        """
        cache_path = self._get_cache_path(date_str)
        if os.path.exists(cache_path):
            try:
                df = pd.read_parquet(cache_path, engine="pyarrow")
                logger.info(f"Loaded cached data for {date_str}: {len(df.columns)} tickers")
                return df
            except Exception as e:
                logger.warning(f"Failed to load cache for {date_str}: {e}")
        return None
    
    def save_data_for_date(self, date_str: str, df: pd.DataFrame) -> None:
        """Save MultiIndex DataFrame for a specific date."""
        cache_path = self._get_cache_path(date_str)
        try:
            df.to_parquet(cache_path, engine="pyarrow")
            logger.info(f"Saved cached data for {date_str}: {len(df.columns)} tickers")
        except Exception as e:
            logger.error(f"Failed to save cache for {date_str}: {e}")
    
    def fetch_and_cache(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch data for multiple tickers and cache by date.
        Returns MultiIndex DataFrame.
        """
        logger.info(f"Fetching data for {len(tickers)} tickers from {start_date} to {end_date}")
        
        try:
            # Download all tickers at once (efficient)
            df = yf.download(
                tickers,
                start=start_date,
                end=end_date,
                progress=False,
                auto_adjust=True
            )
            
            if df.empty:
                logger.warning("No data returned from yfinance")
                return pd.DataFrame()
            
            # Cache each day separately
            if isinstance(df.index, pd.DatetimeIndex):
                for date in df.index.unique():
                    date_str = date.strftime("%Y-%m-%d")
                    daily_data = df.loc[[date]]
                    self.save_data_for_date(date_str, daily_data)
            
            return df
            
        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return pd.DataFrame()
    
    def get_ticker_data(self, ticker: str, date_str: str) -> Optional[pd.DataFrame]:
        """
        Get data for a single ticker on a specific date.
        Falls back to downloading if not cached.
        """
        # Try to load from daily cache
        daily_data = self.get_data_for_date(date_str)
        if daily_data is not None and ticker in daily_data.columns:
            return daily_data[[ticker]]
        
        # Fallback: download just this ticker
        logger.info(f"Cache miss for {ticker} on {date_str}, downloading...")
        try:
            end_date = datetime.strptime(date_str, "%Y-%m-%d")
            start_date = end_date - timedelta(days=365)  # Get 1 year of data
            
            df = yf.download(
                ticker,
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=True
            )
            
            if not df.empty:
                # Cache this day's data
                daily_slice = df.loc[[date_str]]
                if not daily_slice.empty:
                    self.save_data_for_date(date_str, daily_slice[[ticker]])
            
            return df
            
        except Exception as e:
            logger.error(f"Failed to download {ticker}: {e}")
            return None


# Singleton instance
_cache_manager = None

def get_cache_manager() -> CacheManagerV2:
    """Get singleton cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManagerV2()
    return _cache_manager
