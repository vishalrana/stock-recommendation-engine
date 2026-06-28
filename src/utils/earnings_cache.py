import os
import json
import time
import logging
import datetime
from typing import Optional, Tuple
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_FILE = "data/cache/earnings_dates_cache.json"
TTL_SECONDS = 86400  # 24 hours

def _load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load earnings cache file: {e}")
    return {}

def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save earnings cache file: {e}")

def get_ticker_earnings(ticker: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Get (last_earnings_date, next_earnings_date) for a ticker.
    Reads from local cache if valid; otherwise fetches from yfinance.
    Dates are returned as ISO string formats (YYYY-MM-DD) or None.
    """
    ticker = ticker.upper()
    cache = _load_cache()
    now = time.time()
    
    if ticker in cache:
        entry = cache[ticker]
        if now - entry.get("updated_at", 0) < TTL_SECONDS:
            logger.debug(f"Earnings cache HIT for {ticker}")
            return entry.get("last_earnings"), entry.get("next_earnings")
            
    # Cache miss - fetch from yfinance
    logger.info(f"Earnings cache MISS for {ticker}, fetching from yfinance...")
    last_earnings = None
    next_earnings = None
    
    try:
        t = yf.Ticker(ticker)
        
        # 1. Fetch last earnings date from earnings_dates index
        dates = t.earnings_dates
        if dates is not None and not dates.empty:
            current_date = datetime.date.today()
            past_dates = [d for d in dates.index if d.date() <= current_date]
            if past_dates:
                last_earnings = max(past_dates).strftime("%Y-%m-%d")
                
        # 2. Fetch next earnings date from calendar
        calendar = t.calendar
        if calendar is not None:
            if isinstance(calendar, dict):
                e_dates = calendar.get("Earnings Date")
                if e_dates and isinstance(e_dates, list) and len(e_dates) > 0:
                    next_earnings = datetime.datetime.strptime(str(e_dates[0]), "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
            elif hasattr(calendar, "empty") and not calendar.empty:
                if hasattr(calendar, "index") and len(calendar.index) > 0:
                    next_earnings = datetime.datetime.strptime(str(calendar.index[0]), "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
                    
    except Exception as e:
        logger.debug(f"Failed to fetch yfinance earnings for {ticker}: {e}")
        
    # Save to cache even if None (prevents endless retries on failed tickers)
    cache[ticker] = {
        "last_earnings": last_earnings,
        "next_earnings": next_earnings,
        "updated_at": now
    }
    _save_cache(cache)
    
    return last_earnings, next_earnings
