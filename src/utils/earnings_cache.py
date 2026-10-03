"""
Earnings Cache Utility Module
==============================
Provides cached lookup for last and next earnings dates, integrating with
the unified multi-layer earnings cache and Supabase backend.
"""

import os
import time
import logging
import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from src.filters.earnings_filter import (
    load_local_cache,
    save_local_cache,
    normalize_date_str,
    fetch_single_ticker_provider,
    persist_supabase_earnings,
    DEFAULT_EARNINGS_CACHE_FILE,
    EARNINGS_CACHE_TTL_SECONDS,
    EarningsStatus,
)

logger = logging.getLogger(__name__)

CACHE_FILE = str(DEFAULT_EARNINGS_CACHE_FILE)
TTL_SECONDS = EARNINGS_CACHE_TTL_SECONDS


def _load_cache() -> dict:
    return load_local_cache(DEFAULT_EARNINGS_CACHE_FILE)


def _save_cache(cache: dict) -> None:
    save_local_cache(cache, DEFAULT_EARNINGS_CACHE_FILE)


def get_ticker_earnings(
    ticker: str,
    as_of_date: Optional[datetime.date] = None,
    supabase=None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Get (last_earnings_date, next_earnings_date) for a ticker.
    Reads from local cache if valid; otherwise checks Supabase, then fetches from yfinance.
    Dates are returned as ISO string formats (YYYY-MM-DD) or None.
    If as_of_date is provided, returns the most recent earnings date on or before as_of_date.
    """
    ticker = ticker.strip().upper()
    cache = _load_cache()
    now = time.time()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 1. Check local cache
    if ticker in cache:
        entry = cache[ticker]
        next_e = normalize_date_str(entry.get("next_earnings_date") or entry.get("next_earnings"))
        last_e = normalize_date_str(entry.get("last_earnings_date") or entry.get("last_earnings"))
        all_dates = [normalize_date_str(d) for d in entry.get("all_earnings", []) if normalize_date_str(d)]

        # Updated timestamp check
        updated_at = entry.get("updated_at") or entry.get("cached_at", 0)
        is_fresh = False
        if isinstance(updated_at, (int, float)):
            is_fresh = (now - updated_at) < TTL_SECONDS
        elif isinstance(updated_at, str):
            try:
                dt = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                is_fresh = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() < TTL_SECONDS
            except Exception:
                is_fresh = False

        if as_of_date is not None:
            as_of_str = as_of_date.isoformat() if hasattr(as_of_date, "isoformat") else str(as_of_date)[:10]
            if all_dates:
                past = [d for d in all_dates if d <= as_of_str]
                if past:
                    return max(past), next_e
            if last_e and last_e <= as_of_str:
                return last_e, next_e
            return None, next_e
        elif is_fresh:
            logger.debug("Earnings cache HIT for %s", ticker)
            return last_e, next_e

    # 2. Check Supabase persistent cache if client available
    if supabase is not None:
        try:
            res = supabase.table("earnings_calendar").select("*").eq("ticker", ticker).execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                sb_next = normalize_date_str(row.get("next_earnings_date"))
                sb_last = normalize_date_str(row.get("last_earnings_date"))
                cache[ticker] = {
                    "ticker": ticker,
                    "last_earnings": sb_last,
                    "next_earnings": sb_next,
                    "last_earnings_date": sb_last,
                    "next_earnings_date": sb_next,
                    "all_earnings": [sb_last] if sb_last else [],
                    "updated_at": now_iso,
                    "cached_at": now,
                    "status": row.get("status") or (EarningsStatus.KNOWN_UPCOMING.value if sb_next else EarningsStatus.KNOWN_CLEAR.value),
                }
                _save_cache(cache)
                if as_of_date is not None:
                    as_of_str = as_of_date.isoformat() if hasattr(as_of_date, "isoformat") else str(as_of_date)[:10]
                    last_valid = sb_last if (sb_last and sb_last <= as_of_str) else None
                    return last_valid, sb_next
                return sb_last, sb_next
        except Exception as e:
            logger.warning("Supabase lookup failed for %s: %s", ticker, e)

    # 3. Provider fetch with retry and backoff
    logger.info("Earnings cache MISS for %s, fetching from provider...", ticker)
    sym, next_earnings, last_earnings, fiscal_p, fetch_status = fetch_single_ticker_provider(ticker)

    # Extract all earnings dates if possible
    all_earnings: List[str] = []
    if last_earnings:
        all_earnings.append(last_earnings)

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        edates = getattr(t, "earnings_dates", None)
        if edates is not None and hasattr(edates, "index") and len(edates.index) > 0:
            all_earnings = sorted(list({normalize_date_str(d) for d in edates.index if normalize_date_str(d)}))
    except Exception:
        pass

    # Save to local cache
    cache[ticker] = {
        "ticker": ticker,
        "last_earnings": last_earnings,
        "next_earnings": next_earnings,
        "last_earnings_date": last_earnings,
        "next_earnings_date": next_earnings,
        "all_earnings": all_earnings,
        "updated_at": now_iso,
        "cached_at": now,
        "status": fetch_status,
    }
    _save_cache(cache)

    # Persist to Supabase if client available
    if supabase is not None and fetch_status in (EarningsStatus.KNOWN_UPCOMING.value, EarningsStatus.KNOWN_CLEAR.value):
        try:
            persist_supabase_earnings([
                {
                    "ticker": ticker,
                    "next_earnings_date": next_earnings,
                    "last_earnings_date": last_earnings,
                    "fiscal_period": fiscal_p,
                    "updated_at": now_iso,
                }
            ], supabase)
        except Exception as e:
            logger.warning("Supabase persist failed for %s: %s", ticker, e)

    if as_of_date is not None:
        as_of_str = as_of_date.isoformat() if hasattr(as_of_date, "isoformat") else str(as_of_date)[:10]
        if all_earnings:
            past = [d for d in all_earnings if d <= as_of_str]
            if past:
                return max(past), next_earnings
        if last_earnings and last_earnings <= as_of_str:
            return last_earnings, next_earnings
        return None, next_earnings

    return last_earnings, next_earnings
