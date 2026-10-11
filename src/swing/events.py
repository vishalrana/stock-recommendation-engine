"""
Swing Signals — Event Lookups & Point-in-Time Filters
=====================================================
- Earnings within 5 sessions
- Event lock & Price pin backstop
- NEG8K items & V1 deterministic veto
- Catalyst 8-K/6-K lookup for Sleeve N
- Insider cluster detection with routine filter for Sleeve I
- Short interest & DTC decile flag (D1)
"""

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers.context.sec_filings import (
    Filing, EarningsEvent, earnings_events, event_lock, price_pin, reaction_session
)
from src.trade_hygiene import SessionCalendar, estimate_next_earnings

NEG8K_ITEMS: frozenset[str] = frozenset({"1.03", "2.04", "2.06", "3.01", "3.02", "4.01", "4.02"})


def has_earnings_within_5_sessions(
    t_session: date,
    confirmed_reaction_sessions: Iterable[date],
    cal: SessionCalendar,
) -> bool:
    """Check if any reaction session falls in t+1..t+5."""
    if not confirmed_reaction_sessions:
        return False
    window_start = cal.shift(t_session, 1)
    window_end = cal.shift(t_session, 5)
    for r in confirmed_reaction_sessions:
        if window_start <= r <= window_end:
            return True
    return False


def is_event_locked(
    t_session: date,
    filings_up_to_t: Iterable[Filing],
    rights_offering_days: Iterable[date] = (),
    price_series: Optional[pd.Series] = None,
) -> bool:
    """
    Event-locked(t): event_lock(filings accepted <= 20:00 ET on t, as_of=t) is not None,
    OR price_pin(total-return close) is True on t.
    """
    lock = event_lock(filings_up_to_t, t_session, rights_offering_days)
    if lock is not None:
        return True

    if price_series is not None and len(price_series) >= 20:
        pinned = price_pin(price_series)
        ts = pd.Timestamp(t_session)
        if ts in pinned.index and bool(pinned.loc[ts]):
            return True

    return False


def has_recent_neg8k(
    t_session: date,
    filings: Iterable[Filing],
    cal: SessionCalendar,
    lookback_sessions: int = 10,
) -> bool:
    """
    V1 deterministic 8-K veto: True if an 8-K with any NEG8K item was accepted
    within lookback_sessions ending at t_session.
    """
    start_session = cal.shift(t_session, -(lookback_sessions - 1))
    for f in filings:
        if f.form == "8-K" and start_session <= f.day <= t_session:
            if any(item in NEG8K_ITEMS for item in f.items):
                return True
    return False


def check_catalyst_gap_8k(
    t_session: date,
    filings: Iterable[Filing],
    cal: SessionCalendar,
) -> Tuple[bool, Optional[str]]:
    """
    Sleeve N catalyst requirement:
    - At least one 8-K or 6-K accepted in (16:00 ET on t-1, 20:00 ET on t].
    - None of those 8-Ks carries a NEG8K item.
    """
    t_minus_1 = cal.shift(t_session, -1)
    cutoff_start = datetime(t_minus_1.year, t_minus_1.month, t_minus_1.day, 16, 0)
    cutoff_end = datetime(t_session.year, t_session.month, t_session.day, 20, 0)

    found_catalyst = False
    for f in filings:
        if f.form in ("8-K", "6-K"):
            if cutoff_start < f.accepted <= cutoff_end:
                if f.form == "8-K" and any(item in NEG8K_ITEMS for item in f.items):
                    return False, f"Carries NEG8K item: {f.items}"
                found_catalyst = True

    if found_catalyst:
        return True, "Valid 8-K/6-K catalyst filing"
    return False, "No 8-K or 6-K filing in catalyst window"


def is_routine_purchase(
    owner_id: str,
    filing_date: date,
    historical_purchases: Iterable[Tuple[str, date]],
) -> bool:
    """
    Routine purchase: the same reporting owner made a code-P purchase in the same calendar
    month in each of the three prior calendar years (y-1, y-2, y-3).
    """
    y, m = filing_date.year, filing_date.month
    needed_years = {y - 1, y - 2, y - 3}
    found_years = set()

    for oid, pdate in historical_purchases:
        if oid == owner_id and pdate.month == m and pdate.year in needed_years:
            found_years.add(pdate.year)

    return found_years == needed_years


def detect_insider_clusters_on_date(
    d_filing: date,
    ticker: str,
    purchases: List[Dict[str, Any]],
    last_fired_date: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """
    Cluster completed on filing date D:
    - >= 2 distinct reporting owners made qualifying purchases in the same issuer;
    - filing dates fall within [D - 30 calendar days, D];
    - total value >= $100,000;
    - at least one qualifying filing came after issuer's last fired I signal.
    """
    window_start = d_filing - timedelta(days=30)
    window_purchases = [
        p for p in purchases
        if p["ticker"] == ticker and window_start <= p["filing_date"] <= d_filing
    ]

    distinct_owners = {p["owner_id"] for p in window_purchases}
    if len(distinct_owners) < 2:
        return None

    total_value = sum(p["value"] for p in window_purchases)
    if total_value < 100_000.0:
        return None

    if last_fired_date is not None:
        has_new = any(p["filing_date"] > last_fired_date for p in window_purchases)
        if not has_new:
            return None

    return {
        "cluster_date": d_filing,
        "ticker": ticker,
        "distinct_owners": len(distinct_owners),
        "total_value": total_value,
        "purchases_count": len(window_purchases),
    }
