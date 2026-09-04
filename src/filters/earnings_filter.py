"""
Earnings Risk Filter Module
============================
Protects trading capital by preventing signal emissions during dangerous
pre-earnings volatility blackout windows, with explicit allowance for post-earnings PEAD.
"""

import logging
import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Canonical Quantitative Configuration (Single Source of Truth)
from src.quant_config import (
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_CACHE_TTL_SECONDS,
)


def normalize_strategy_key(strategy: str) -> str:
    """Normalize any strategy string variant."""
    if not strategy:
        return 'trend_following'
    s = str(strategy).strip().lower().replace('-', '_').replace(' ', '_')
    if '52' in s or 'breakout' in s or 'high' in s:
        return '52w_high_breakout'
    if 'trend' in s:
        return 'trend_following'
    if 'pullback' in s:
        return 'pullback_recovery'
    if 'cross' in s or 'momentum' in s:
        return 'cross_sectional_momentum'
    if 'pead' in s or 'earnings' in s:
        return 'pead'
    if 'sector' in s or 'rotation' in s:
        return 'sector_rotation'
    if 'mean' in s or 'reversion' in s:
        return 'mean_reversion'
    return s


def earnings_risk_filter(
    ticker: str,
    scan_date: datetime.date,
    strategy: str,
    earnings_calendar: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluates whether a candidate ticker falls within a strategy blackout window.
    
    # ponytail: Keep filter rule clean and simple.
    Returns:
        {
            'pass': bool,
            'reason': Optional[str],
            'days_to_earnings': Optional[int],
            'next_earnings_date': Optional[str],
            'last_earnings_date': Optional[str],
        }
    """
    strat_key = normalize_strategy_key(strategy)
    blackout = EARNINGS_BLACKOUT_DAYS.get(strat_key, 5)

    if not earnings_calendar:
        return {
            'pass': True,
            'reason': None,
            'days_to_earnings': None,
            'next_earnings_date': None,
            'last_earnings_date': None,
        }

    entry = earnings_calendar.get(ticker.upper()) or {}
    next_date_val = entry.get('next_earnings_date')
    last_date_val = entry.get('last_earnings_date')

    # Convert dates if strings
    next_dt = datetime.date.fromisoformat(next_date_val) if isinstance(next_date_val, str) else next_date_val
    last_dt = datetime.date.fromisoformat(last_date_val) if isinstance(last_date_val, str) else last_date_val

    # Special handling for PEAD: Earnings-driven drift strategy
    if strat_key == 'pead':
        if last_dt is not None:
            days_since = (scan_date - last_dt).days
            if 0 <= days_since <= 3:
                return {
                    'pass': True,
                    'reason': 'Post-earnings window',
                    'days_to_earnings': -days_since,
                    'next_earnings_date': next_dt.isoformat() if next_dt else None,
                    'last_earnings_date': last_dt.isoformat() if last_dt else None,
                }
        return {
            'pass': True,
            'reason': 'PEAD window',
            'days_to_earnings': None,
            'next_earnings_date': next_dt.isoformat() if next_dt else None,
            'last_earnings_date': last_dt.isoformat() if last_dt else None,
        }

    # Standard pre-earnings blackout check
    if next_dt is not None:
        days_to_earnings = (next_dt - scan_date).days

        if days_to_earnings < 0:
            # Next earnings date is in the past; safe to pass unless last earnings requires PEAD
            return {
                'pass': True,
                'reason': 'Past earnings',
                'days_to_earnings': None,
                'next_earnings_date': next_dt.isoformat(),
                'last_earnings_date': last_dt.isoformat() if last_dt else None,
            }

        if days_to_earnings <= blackout:
            reason_msg = f"Earnings in {days_to_earnings}d (blackout: {blackout}d)"
            logger.info(f"[EARNINGS RISK GATE] Rejected {ticker} ({strategy}): {reason_msg}")
            return {
                'pass': False,
                'reason': reason_msg,
                'days_to_earnings': days_to_earnings,
                'next_earnings_date': next_dt.isoformat(),
                'last_earnings_date': last_dt.isoformat() if last_dt else None,
            }
        else:
            return {
                'pass': True,
                'reason': 'Earnings passed',
                'days_to_earnings': days_to_earnings,
                'next_earnings_date': next_dt.isoformat(),
                'last_earnings_date': last_dt.isoformat() if last_dt else None,
            }

    return {
        'pass': True,
        'reason': None,
        'days_to_earnings': None,
        'next_earnings_date': None,
        'last_earnings_date': None,
    }


def is_earnings_record_fresh(record: Optional[Dict[str, Any]], max_age_seconds: int = EARNINGS_CACHE_TTL_SECONDS) -> bool:
    """
    Check if an earnings cache record is fresh (<= 24 hours old).
    Returns False for missing or stale records.
    """
    if not record or not isinstance(record, dict):
        return False
    updated_at_val = record.get("updated_at") or record.get("cached_at")
    if not updated_at_val:
        return False
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        if isinstance(updated_at_val, (int, float)):
            dt = datetime.datetime.fromtimestamp(updated_at_val, tz=datetime.timezone.utc)
        elif isinstance(updated_at_val, str):
            s = updated_at_val.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
        elif isinstance(updated_at_val, datetime.datetime):
            dt = updated_at_val
        elif isinstance(updated_at_val, datetime.date):
            dt = datetime.datetime.combine(updated_at_val, datetime.time.min, tzinfo=datetime.timezone.utc)
        else:
            return False

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        age = (now - dt).total_seconds()
        return 0 <= age <= max_age_seconds
    except Exception:
        return False


def fetch_earnings_calendar(tickers: List[str], supabase=None) -> Dict[str, Dict[str, Any]]:
    """
    Populates and retrieves upcoming earnings dates for universe tickers.
    Checks Supabase DB cache first to avoid hitting external API rate limits.
    Refreshes records older than 24 hours and fetches missing records.
    Falls back gracefully to Yahoo Finance if Finnhub is rate-limited or unavailable.
    """
    calendar_map: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 1. Check local Supabase table cache first
    if supabase is not None:
        try:
            res = supabase.table("earnings_calendar").select("*").in_("ticker", [t.upper() for t in tickers]).execute()
            for row in (res.data or []):
                t = row.get("ticker", "").upper()
                calendar_map[t] = {
                    "next_earnings_date": row.get("next_earnings_date"),
                    "last_earnings_date": row.get("last_earnings_date"),
                    "fiscal_period": row.get("fiscal_period"),
                    "updated_at": row.get("updated_at"),
                }
        except Exception as e:
            logger.warning(f"Could not load earnings_calendar from DB: {e}")

    # 2. Identify tickers needing fetch (missing) or refresh (stale > 24 hours)
    tickers_to_fetch = []
    for t in tickers:
        t_up = t.upper()
        if t_up not in calendar_map:
            tickers_to_fetch.append(t_up)
        elif not is_earnings_record_fresh(calendar_map[t_up], EARNINGS_CACHE_TTL_SECONDS):
            tickers_to_fetch.append(t_up)

    # 3. Fetch missing/stale records using fallback
    for t in tickers_to_fetch[:30]:  # Cap batch to avoid long waits
        try:
            import yfinance as yf
            yf_ticker = yf.Ticker(t)
            cal = getattr(yf_ticker, 'calendar', None)
            next_date = None
            if cal is not None and isinstance(cal, dict):
                ed = cal.get('Earnings Date')
                if ed and len(ed) > 0:
                    next_date = ed[0].date().isoformat() if hasattr(ed[0], 'date') else str(ed[0])[:10]
            calendar_map[t] = {
                "next_earnings_date": next_date,
                "last_earnings_date": None,
                "fiscal_period": None,
                "updated_at": now_iso,
            }
        except Exception as e:
            logger.debug(f"Earnings fetch skipped for {t}: {e}")
            calendar_map[t] = {
                "next_earnings_date": None,
                "last_earnings_date": None,
                "fiscal_period": None,
                "updated_at": now_iso,
            }

    return calendar_map
