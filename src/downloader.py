"""
Downloader Module
=================
Purpose: Fetch S&P 500 tickers and OHLCV data from yfinance.

Single Responsibility: Data acquisition only. No processing or filtering.
"""

import logging
from typing import List, Optional
from datetime import datetime, date
import pandas as pd
import yfinance as yf
from requests.exceptions import RequestException

from cache import get_data
from config import (
    TEST_MODE,
    TEST_TICKERS,
    START_DATE,
    END_DATE,
    YFINANCE_TIMEOUT,
)

# Configure logging
logger = logging.getLogger(__name__)


def fetch_sp500_tickers() -> List[str]:
    """
    Fetch S&P 500 ticker symbols from Wikipedia.
    
    Returns:
        List of ticker symbols (uppercase strings)
        
    Raises:
        RequestException: If Wikipedia fetch fails
    """
    if TEST_MODE:
        logger.info(f"TEST MODE: Using {len(TEST_TICKERS)} hardcoded tickers")
        return TEST_TICKERS
    
    logger.info("Fetching S&P 500 tickers from Wikipedia...")
    
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url, timeout=10)
        sp500_table = tables[0]
        tickers = sp500_table["Symbol"].tolist()
        
        # Clean and validate
        tickers = [t.strip().upper().replace(".", "-") for t in tickers if isinstance(t, str)]
        
        logger.info(f"Successfully fetched {len(tickers)} tickers from Wikipedia")
        return tickers
        
    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 tickers: {str(e)}")
        raise RequestException(f"Wikipedia fetch failed: {str(e)}")


def fetch_ohlcv_data(
    ticker: str,
    start_date: date = START_DATE,
    end_date: date = END_DATE,
    timeout: int = YFINANCE_TIMEOUT,
) -> Optional[pd.DataFrame]:
    """
    Download OHLCV data for a single ticker using yfinance.
    
    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        start_date: Start date for historical data
        end_date: End date for historical data
        timeout: Request timeout in seconds
        
    Returns:
        DataFrame with columns [Open, High, Low, Close, Volume] or None if fetch fails
        
    Note:
        - Returns None on failure (no exception raised) to allow batch processing to continue
        - Automatically retries failed tickers
    """
    def download() -> Optional[pd.DataFrame]:
        try:
            logger.debug(f"Downloading data for {ticker}...")

            data = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                progress=False,  # Suppress progress bar for cleaner output
                timeout=timeout,
            )

            # Validate data
            if data is None or data.empty:
                logger.warning(f"{ticker}: No data returned")
                return None

            # Handle MultiIndex columns from yfinance (newer versions)
            if isinstance(data.columns, pd.MultiIndex):
                # MultiIndex structure is (OHLCV, Ticker) - get the OHLCV names
                data.columns = data.columns.get_level_values(0)

            # Normalize column names to uppercase
            data.columns = data.columns.str.upper()

            # Ensure required columns exist
            required_cols = ["CLOSE", "VOLUME"]
            if not all(col in data.columns for col in required_cols):
                logger.warning(f"{ticker}: Missing required columns. Have: {list(data.columns)}")
                return None

            logger.debug(f"{ticker}: Downloaded {len(data)} rows")
            return data

        except Exception as e:
            logger.warning(f"{ticker}: Download failed - {str(e)}")
            return None

    try:
        return get_data(
            ticker,
            download,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        logger.warning(f"{ticker}: Cache lookup failed - {str(e)}")
        return None


def fetch_batch_ohlcv(tickers: List[str]) -> dict:
    """
    Download OHLCV data for multiple tickers.
    
    Args:
        tickers: List of ticker symbols
        
    Returns:
        Dictionary mapping ticker -> DataFrame (or None if failed)
        {
            "AAPL": DataFrame(...),
            "MSFT": None,  # Failed download
            "NVDA": DataFrame(...),
        }
        
    Note:
        - Continues processing even if some tickers fail
        - Returns all results (success and failure) in single dict
    """
    logger.info(f"Downloading OHLCV data for {len(tickers)} tickers...")
    
    data_dict = {}
    successful = 0
    failed = 0
    
    for i, ticker in enumerate(tickers, 1):
        logger.info(f"[{i}/{len(tickers)}] Fetching {ticker}...")
        
        df = fetch_ohlcv_data(ticker)
        
        if df is not None:
            data_dict[ticker] = df
            successful += 1
        else:
            data_dict[ticker] = None
            failed += 1
    
    logger.info(f"Download complete: {successful} successful, {failed} failed")
    return data_dict
