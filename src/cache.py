"""Local Parquet cache for downloaded OHLCV data."""

import logging
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from config import DATA_DIR


logger = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "cache"
CACHE_MAX_AGE_HOURS = 24
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(ticker: str) -> Path:
    safe_ticker = ticker.strip().upper().replace(".", "-").replace("/", "-")
    return CACHE_DIR / f"{safe_ticker}.parquet"


def load_cached_data(
    ticker: str,
    max_age_hours: int = CACHE_MAX_AGE_HOURS,
) -> Optional[pd.DataFrame]:
    """Load a ticker when its Parquet cache exists and is younger than 24 hours."""
    path = _cache_path(ticker)
    if not path.exists():
        return None

    age_hours = (time.time() - path.stat().st_mtime) / 3600.0
    if age_hours >= max_age_hours:
        logger.info("%s: cache expired (%.1f hours old)", ticker, age_hours)
        return None

    try:
        data = pd.read_parquet(path, engine="pyarrow")
        if data.empty:
            logger.warning("%s: cached file is empty", ticker)
            return None
        logger.debug("%s: loaded %s cached rows", ticker, len(data))
        return data
    except Exception as exc:
        logger.warning("%s: failed to read cache - %s", ticker, exc)
        return None


def save_cached_data(
    ticker: str,
    data: pd.DataFrame,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Path:
    """Atomically save OHLCV data and its requested coverage as Parquet."""
    if data is None or data.empty:
        raise ValueError("Cannot cache empty OHLCV data")

    cached = data.copy()
    if start_date is not None:
        cached.attrs["cache_start_date"] = start_date.isoformat()
    if end_date is not None:
        cached.attrs["cache_end_date"] = end_date.isoformat()

    path = _cache_path(ticker)
    temporary_path = path.with_suffix(".parquet.tmp")
    cached.to_parquet(temporary_path, engine="pyarrow", index=True)
    temporary_path.replace(path)
    logger.debug("%s: saved %s rows to cache", ticker, len(cached))
    return path


def _covers_requested_range(
    data: pd.DataFrame,
    start_date: Optional[date],
    end_date: Optional[date],
) -> bool:
    """Check the request metadata stored with the cached frame."""
    if start_date is not None:
        cached_start = data.attrs.get("cache_start_date")
        if cached_start is None or date.fromisoformat(cached_start) > start_date:
            return False
    if end_date is not None:
        cached_end = data.attrs.get("cache_end_date")
        if cached_end is None or date.fromisoformat(cached_end) < end_date:
            return False
    return True


def _slice_requested_range(
    data: pd.DataFrame,
    start_date: Optional[date],
    end_date: Optional[date],
) -> pd.DataFrame:
    """Return the requested interval; yfinance treats end dates as exclusive."""
    result = data
    if start_date is not None:
        result = result[result.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        result = result[result.index < pd.Timestamp(end_date)]
    return result.copy()


def get_data(
    ticker: str,
    fetcher: Callable[[], Optional[pd.DataFrame]],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    max_age_hours: int = CACHE_MAX_AGE_HOURS,
) -> Optional[pd.DataFrame]:
    """Return fresh cached data, otherwise fetch, cache, and return it."""
    cached = load_cached_data(ticker, max_age_hours=max_age_hours)
    if cached is not None and _covers_requested_range(cached, start_date, end_date):
        logger.info("%s: using cached OHLCV data", ticker)
        return _slice_requested_range(cached, start_date, end_date)

    if cached is not None:
        logger.info("%s: cache does not cover requested dates; refreshing", ticker)

    data = fetcher()
    if data is None or data.empty:
        return None

    try:
        save_cached_data(ticker, data, start_date=start_date, end_date=end_date)
    except Exception as exc:
        logger.warning("%s: download succeeded but cache write failed - %s", ticker, exc)
    return data
