"""
Cache Manager V2 - Date-partitioned Parquet storage
====================================================
Upgrades from per-ticker files to per-day files for better I/O performance.

Old: data/cache/{ticker}.parquet (500+ files)
New: data/cache/by_date/{YYYY-MM-DD}.parquet (1 file per day)
"""

import os
import glob
import logging
import time
import datetime
from typing import Optional, List, Dict
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_VERSION = "2"
DEFAULT_BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))

class CacheManager:
    """Manages date-partitioned price data cache with batch downloads and in-memory preloading."""
    
    def __init__(self, cache_dir: str = "data/cache/by_date"):
        self.cache_dir = cache_dir
        self.version_file = os.path.join(cache_dir, ".cache_version")
        os.makedirs(cache_dir, exist_ok=True)
        self._history_cache: Dict[str, pd.DataFrame] = {}
        self._check_version()
        
    def _check_version(self) -> None:
        """Validate cache version. Clear directory if version mismatches."""
        is_valid = False
        if os.path.exists(self.version_file):
            try:
                with open(self.version_file, "r") as f:
                    ver = f.read().strip()
                if ver == CACHE_VERSION:
                    is_valid = True
            except Exception as e:
                logger.warning(f"Failed to read cache version file: {e}")
                
        if not is_valid:
            logger.info(f"Cache version mismatch or missing (expected version {CACHE_VERSION}). Clearing cache directory...")
            # Remove all files in cache_dir
            for fpath in glob.glob(os.path.join(self.cache_dir, "*")):
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                    except Exception as e:
                        logger.warning(f"Failed to delete {fpath}: {e}")
            # Write new version
            try:
                with open(self.version_file, "w") as f:
                    f.write(CACHE_VERSION)
            except Exception as e:
                logger.error(f"Failed to write cache version file: {e}")

    def _get_cache_path(self, date_str: str) -> str:
        """Get cache file path for a specific date."""
        return os.path.join(self.cache_dir, f"{date_str}.parquet")

    def clear_all(self) -> None:
        """Delete all cached parquet files."""
        logger.info("Clearing all date-partitioned cache files...")
        parquet_files = glob.glob(os.path.join(self.cache_dir, "*.parquet"))
        for f in parquet_files:
            try:
                os.remove(f)
            except Exception as e:
                logger.warning(f"Failed to delete {f}: {e}")
        self._history_cache.clear()

    def get_data_for_date(self, date_str: str) -> Optional[pd.DataFrame]:
        """Load MultiIndex DataFrame for a specific date from cache."""
        cache_path = self._get_cache_path(date_str)
        if os.path.exists(cache_path):
            try:
                df = pd.read_parquet(cache_path, engine="pyarrow")
                return df
            except Exception as e:
                logger.warning(f"Failed to load cache for {date_str}: {e}")
        return None

    def save_data_for_date(self, date_str: str, df: pd.DataFrame) -> None:
        """Save MultiIndex DataFrame for a specific date."""
        cache_path = self._get_cache_path(date_str)
        try:
            df.to_parquet(cache_path, engine="pyarrow")
        except Exception as e:
            logger.error(f"Failed to save cache for {date_str}: {e}")

    def download_batch_with_retry(self, tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        Download OHLCV data for multiple tickers in batch with retry/exponential backoff.
        Falls back to smaller batches if a 429 rate limit is encountered.
        """
        max_retries = 3
        backoff_sec = 2
        batch_size = DEFAULT_BATCH_SIZE
        
        # Helper to run yf.download
        def _fetch(t_list: List[str]) -> pd.DataFrame:
            for attempt in range(max_retries):
                try:
                    df = yf.download(
                        t_list,
                        start=start_date,
                        end=end_date,
                        progress=False,
                        group_by='ticker',
                        auto_adjust=True,
                        timeout=30
                    )
                    if not df.empty:
                        return df
                except Exception as ex:
                    # Check for rate limiting indications
                    if "429" in str(ex) or "Too Many Requests" in str(ex):
                        sleep_time = backoff_sec * (2 ** attempt)
                        logger.warning(f"Rate limited (429) during fetch. Retrying in {sleep_time}s...")
                        time.sleep(sleep_time)
                    else:
                        logger.warning(f"Attempt {attempt + 1} failed for batch: {ex}")
            return pd.DataFrame()

        logger.info(f"Downloading data for {len(tickers)} tickers from {start_date} to {end_date}...")
        
        # Primary attempt: Download all tickers at once
        full_df = _fetch(tickers)
        if not full_df.empty:
            return full_df
            
        logger.warning(f"Full batch download failed. Falling back to smaller batches (size={batch_size})...")
        
        # Fallback: Download in chunks
        chunked_dfs = []
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i:i+batch_size]
            logger.info(f"Fetching batch chunk [{i//batch_size + 1}/{(len(tickers)-1)//batch_size + 1}] (size={len(chunk)})...")
            chunk_df = _fetch(chunk)
            if not chunk_df.empty:
                chunked_dfs.append(chunk_df)
            time.sleep(1) # Polite delay between chunk downloads
            
        if not chunked_dfs:
            return pd.DataFrame()
            
        # Merge all chunked DataFrames
        try:
            merged_df = pd.concat(chunked_dfs, axis=1)
            return merged_df
        except Exception as e:
            logger.error(f"Failed to merge batch downloads: {e}")
            return pd.DataFrame()

    def refresh_cache(self, tickers: List[str], start_date: str, end_date: str) -> None:
        """
        Fetch data for multiple tickers, transform to MultiIndex, and cache by date.
        """
        df = self.download_batch_with_retry(tickers, start_date, end_date)
        if df.empty:
            logger.error("Download returned no data. Cache refresh failed.")
            return

        # Stack yfinance group_by='ticker' columns: (Ticker, Metric) -> MultiIndex index: [Ticker, Date]
        try:
            # Drop any columns that are all NaN (e.g. invalid tickers)
            df = df.dropna(how='all', axis=1)
            
            # Stack the ticker level (level 0) to index
            stacked = df.stack(level=0)
            stacked.index.names = ["Date", "Ticker"]
            stacked = stacked.reorder_levels(["Ticker", "Date"]).sort_index()
            stacked.columns = stacked.columns.str.upper()
            
            # Filter to required columns
            valid_cols = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
            stacked = stacked[[c for c in valid_cols if c in stacked.columns]]
            
            # Cache each day separately
            grouped = stacked.groupby(level="Date")
            for date_val, group in grouped:
                # date_val can be Timestamp
                date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
                self.save_data_for_date(date_str, group)
                
            logger.info(f"Successfully cached {len(grouped)} daily files.")
        except Exception as e:
            logger.error(f"Failed to transform and cache downloaded data: {e}", exc_info=True)

    def preload_history(self, start_date: str, end_date: str) -> int:
        """
        Preload historical data in the date range into memory.
        Returns the number of preloaded daily files.
        """
        logger.info(f"Preloading date-partitioned cache into memory from {start_date} to {end_date}...")
        self._history_cache.clear()
        
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        # Find all parquet files in cache_dir
        parquet_files = glob.glob(os.path.join(self.cache_dir, "*.parquet"))
        loaded_dfs = []
        
        for fpath in parquet_files:
            bname = os.path.basename(fpath).replace(".parquet", "")
            try:
                file_dt = pd.to_datetime(bname)
                if start_dt <= file_dt <= end_dt:
                    df = pd.read_parquet(fpath)
                    loaded_dfs.append(df)
            except Exception as e:
                logger.warning(f"Skipping invalid/corrupt daily file {fpath}: {e}")
                
        if not loaded_dfs:
            logger.info("No daily cache files found in preloading range.")
            return 0
            
        try:
            # Concatenate all daily data
            combined = pd.concat(loaded_dfs).sort_index()
            
            # Group by ticker and store in dictionary
            # Index is MultiIndex [Ticker, Date]
            grouped = combined.groupby(level="Ticker")
            for ticker, group in grouped:
                # droplevel to get just Date index
                self._history_cache[ticker] = group.droplevel("Ticker")
                
            logger.info(f"Preloaded historical data for {len(self._history_cache)} tickers into memory.")
            return len(loaded_dfs)
        except Exception as e:
            logger.error(f"Failed to compile preloaded history: {e}")
            return 0

    def get_ticker_history(self, ticker: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """
        Fetch historical price data for a single ticker.
        Uses the preloaded in-memory cache if available; otherwise falls back to reading files from disk.
        """
        ticker = ticker.upper()
        
        # 1. Check in-memory preloaded cache
        if ticker in self._history_cache:
            df = self._history_cache[ticker]
            # Filter by date range
            mask = (df.index >= start_date) & (df.index <= end_date)
            return df.loc[mask]
            
        # 2. Disk fallback (reading matching daily files)
        logger.debug(f"Cache miss for {ticker} in memory, reading daily files from disk...")
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        parquet_files = glob.glob(os.path.join(self.cache_dir, "*.parquet"))
        ticker_rows = []
        
        for fpath in parquet_files:
            bname = os.path.basename(fpath).replace(".parquet", "")
            try:
                file_dt = pd.to_datetime(bname)
                if start_dt <= file_dt <= end_dt:
                    df = pd.read_parquet(fpath)
                    if ticker in df.index.levels[0]:
                        row_df = df.xs(ticker, level="Ticker")
                        # Keep track of the date from index or index value
                        ticker_rows.append(row_df)
            except Exception:
                pass
                
        if not ticker_rows:
            return None
            
        try:
            combined = pd.concat(ticker_rows).sort_index()
            return combined
        except Exception as e:
            logger.error(f"Failed to assemble ticker history from disk for {ticker}: {e}")
            return None

# Singleton instance
_cache_manager = None

def get_cache_manager() -> CacheManager:
    """Get singleton cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager
