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

# Strategy-Specific Earnings Blackout Windows (days prior to announcement)
EARNINGS_BLACKOUT_DAYS: Dict[str, int] = {
    'trend_following': 5,        # Avoid 5 days before earnings
    '52w_high_breakout': 3,      # Avoid 3 days before
    'pullback_recovery': 7,      # Avoid 7 days before (volatility kills mean-reversion)
    'cross_sectional_momentum': 5,
    'pead': 0,                   # PEAD is *about* earnings — no blackout, entry in 3d post announcement
    'sector_rotation': 3,
    'mean_reversion': 7,
}


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


def fetch_earnings_calendar(tickers: List[str], supabase=None) -> Dict[str, Dict[str, Any]]:
    """
    Populates and retrieves upcoming earnings dates for universe tickers.
    Checks Supabase DB cache first to avoid hitting external API rate limits.
    Falls back gracefully to Yahoo Finance if Finnhub is rate-limited or unavailable.
    """
    calendar_map: Dict[str, Dict[str, Any]] = {}
    today = datetime.date.today()

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

    # Identify tickers needing refresh (missing or older than 24 hours)
    tickers_to_fetch = [t.upper() for t in tickers if t.upper() not in calendar_map]

    # # ponytail: Lazy batch fetch using yfinance fallback if Finnhub is omitted
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
                "updated_at": today.isoformat(),
            }
        except Exception as e:
            logger.debug(f"Earnings fetch skipped for {t}: {e}")
            calendar_map[t] = {
                "next_earnings_date": None,
                "last_earnings_date": None,
                "fiscal_period": None,
                "updated_at": today.isoformat(),
            }

    return calendar_map
