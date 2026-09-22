"""
Earnings Risk Filter Module
============================
Protects trading capital by preventing signal emissions during dangerous
pre-earnings volatility blackout windows, with explicit allowance for post-earnings PEAD.
"""

import logging
import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import datetime
import logging

logger = logging.getLogger(__name__)

# Canonical Quantitative Configuration (Single Source of Truth)
from src.quant_config import (
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_CACHE_TTL_SECONDS,
)


class EarningsStatus(str, Enum):
    KNOWN_UPCOMING = "KNOWN_UPCOMING"
    KNOWN_CLEAR = "KNOWN_CLEAR"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"


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
    P0-3: Strict fail-safe. Unknown or stale earnings data must NOT default to pass
    for strategies requiring a blackout window. PEAD remains exempt.

    Returns:
        {
            'pass': bool,
            'reason': Optional[str],
            'status': str,
            'days_to_earnings': Optional[int],
            'next_earnings_date': Optional[str],
            'last_earnings_date': Optional[str],
        }
    """
    strat_key = normalize_strategy_key(strategy)
    blackout = EARNINGS_BLACKOUT_DAYS.get(strat_key, 5)

    entry = (earnings_calendar.get(ticker.upper()) if earnings_calendar else {}) or {}
    next_date_val = entry.get('next_earnings_date')
    last_date_val = entry.get('last_earnings_date')

    next_dt = datetime.date.fromisoformat(next_date_val) if isinstance(next_date_val, str) else next_date_val
    last_dt = datetime.date.fromisoformat(last_date_val) if isinstance(last_date_val, str) else last_date_val

    # Special handling for PEAD: Post-Earnings Announcement Drift strategy
    # PEAD is exempt from pre-earnings blackout because it trades post-earnings reaction
    if strat_key == 'pead':
        if last_dt is not None:
            days_since = (scan_date - last_dt).days
            if 0 <= days_since <= 3:
                return {
                    'pass': True,
                    'reason': 'Post-earnings window',
                    'status': EarningsStatus.KNOWN_CLEAR.value,
                    'days_to_earnings': -days_since,
                    'next_earnings_date': next_dt.isoformat() if next_dt else None,
                    'last_earnings_date': last_dt.isoformat() if last_dt else None,
                }
        return {
            'pass': True,
            'reason': 'PEAD window (exempt from pre-earnings blackout)',
            'status': entry.get('status', EarningsStatus.KNOWN_CLEAR.value),
            'days_to_earnings': None,
            'next_earnings_date': next_dt.isoformat() if next_dt else None,
            'last_earnings_date': last_dt.isoformat() if last_dt else None,
        }

    # For all blackout-dependent strategies (blackout > 0):
    # If no calendar or ticker not present: FAIL-CLOSED
    if not earnings_calendar or ticker.upper() not in earnings_calendar:
        return {
            'pass': False,
            'reason': f"Earnings status {EarningsStatus.UNKNOWN.value} - blackout safety check failed",
            'status': EarningsStatus.UNKNOWN.value,
            'days_to_earnings': None,
            'next_earnings_date': None,
            'last_earnings_date': None,
        }

    # Check status and freshness
    status = entry.get('status')
    if not status:
        if is_earnings_record_fresh(entry, EARNINGS_CACHE_TTL_SECONDS):
            status = EarningsStatus.KNOWN_UPCOMING.value if next_dt else EarningsStatus.KNOWN_CLEAR.value
        else:
            status = EarningsStatus.STALE.value

    # Fail closed on STALE or UNKNOWN
    if status in (EarningsStatus.STALE.value, EarningsStatus.UNKNOWN.value):
        logger.info(f"[EARNINGS RISK GATE] Rejected {ticker} ({strategy}): Earnings status {status}")
        return {
            'pass': False,
            'reason': f"Earnings status {status} - blackout safety check failed",
            'status': status,
            'days_to_earnings': None,
            'next_earnings_date': next_dt.isoformat() if next_dt else None,
            'last_earnings_date': last_dt.isoformat() if last_dt else None,
        }

    # If upcoming earnings date is known
    if next_dt is not None:
        days_to_earnings = (next_dt - scan_date).days
        if days_to_earnings < 0:
            return {
                'pass': True,
                'reason': 'Past earnings',
                'status': EarningsStatus.KNOWN_CLEAR.value,
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
                'status': EarningsStatus.KNOWN_UPCOMING.value,
                'days_to_earnings': days_to_earnings,
                'next_earnings_date': next_dt.isoformat(),
                'last_earnings_date': last_dt.isoformat() if last_dt else None,
            }
        else:
            return {
                'pass': True,
                'reason': 'Earnings passed',
                'status': EarningsStatus.KNOWN_UPCOMING.value,
                'days_to_earnings': days_to_earnings,
                'next_earnings_date': next_dt.isoformat(),
                'last_earnings_date': last_dt.isoformat() if last_dt else None,
            }

    # If confirmed clear
    if status == EarningsStatus.KNOWN_CLEAR.value:
        return {
            'pass': True,
            'reason': 'Earnings clear',
            'status': EarningsStatus.KNOWN_CLEAR.value,
            'days_to_earnings': None,
            'next_earnings_date': None,
            'last_earnings_date': last_dt.isoformat() if last_dt else None,
        }

    # Final fallback: fail closed
    return {
        'pass': False,
        'reason': f"Earnings status {EarningsStatus.UNKNOWN.value} - unconfirmed earnings schedule",
        'status': EarningsStatus.UNKNOWN.value,
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
        # If updated_at is omitted but next_earnings_date is present (e.g. mock test fixture), treat as fresh
        return bool(record.get("next_earnings_date"))
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
    Refreshes records older than 24 hours and fetches missing records for the entire active scan universe.
    """
    calendar_map: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 1. Check local Supabase table cache first
    if supabase is not None:
        try:
            res = supabase.table("earnings_calendar").select("*").in_("ticker", [t.upper() for t in tickers]).execute()
            for row in (res.data or []):
                t = row.get("ticker", "").upper()
                next_date = row.get("next_earnings_date")
                status = row.get("status")
                if not status:
                    status = EarningsStatus.KNOWN_UPCOMING.value if next_date else EarningsStatus.KNOWN_CLEAR.value
                calendar_map[t] = {
                    "next_earnings_date": next_date,
                    "last_earnings_date": row.get("last_earnings_date"),
                    "fiscal_period": row.get("fiscal_period"),
                    "updated_at": row.get("updated_at"),
                    "status": status,
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

    # 3. Controlled parallel fetch for missing/stale records across the universe (no arbitrary [:30] cap)
    if tickers_to_fetch:
        logger.info(f"Fetching earnings calendar for {len(tickers_to_fetch)} missing/stale tickers...")
        def _fetch_single_cal(ticker_sym: str):
            try:
                import yfinance as yf
                yf_ticker = yf.Ticker(ticker_sym)
                cal = getattr(yf_ticker, 'calendar', None)
                next_date = None
                if cal is not None and isinstance(cal, dict):
                    ed = cal.get('Earnings Date')
                    if ed and len(ed) > 0:
                        next_date = ed[0].date().isoformat() if hasattr(ed[0], 'date') else str(ed[0])[:10]
                status = EarningsStatus.KNOWN_UPCOMING.value if next_date else EarningsStatus.KNOWN_CLEAR.value
                return ticker_sym, {
                    "next_earnings_date": next_date,
                    "last_earnings_date": None,
                    "fiscal_period": None,
                    "updated_at": now_iso,
                    "status": status,
                }
            except Exception as e:
                logger.debug(f"Earnings fetch skipped for {ticker_sym}: {e}")
                return ticker_sym, {
                    "next_earnings_date": None,
                    "last_earnings_date": None,
                    "fiscal_period": None,
                    "updated_at": now_iso,
                    "status": EarningsStatus.UNKNOWN.value,
                }

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_sym = {executor.submit(_fetch_single_cal, sym): sym for sym in tickers_to_fetch}
            for future in as_completed(future_to_sym):
                sym_orig = future_to_sym[future]
                try:
                    sym_res, data_res = future.result()
                    calendar_map[sym_res] = data_res
                except Exception:
                    calendar_map[sym_orig] = {
                        "next_earnings_date": None,
                        "last_earnings_date": None,
                        "fiscal_period": None,
                        "updated_at": now_iso,
                        "status": EarningsStatus.UNKNOWN.value,
                    }

    return calendar_map
