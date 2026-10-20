"""
Market Date Utility Module
==========================
Provides canonical US equity market-date and execution-timestamp helpers.

Rules:
1. MARKET DATE represents the active or most recent completed US regular trading session.
2. Regular market session is 09:30 - 16:00 US/Eastern (America/New_York).
3. If after 16:00 ET on a weekday, market date is that weekday.
4. If before 09:30 ET on a weekday, market date is the previous business day.
5. If on a weekend (Saturday or Sunday), market date rolls back to Friday.
6. EXECUTION TIMESTAMP is always ISO 8601 UTC.
"""

import datetime
from zoneinfo import ZoneInfo
from typing import Optional

EASTERN_TZ = ZoneInfo("America/New_York")
UTC_TZ = datetime.timezone.utc


def get_execution_timestamp_utc() -> str:
    """Return the current execution timestamp as an ISO 8601 UTC string."""
    return datetime.datetime.now(UTC_TZ).isoformat()


def get_market_date(as_of: Optional[datetime.datetime] = None) -> datetime.date:
    """
    Compute the canonical US regular market date for a given datetime (or now).
    Guarantees consistent Eastern session semantics regardless of runner timezone
    or post-UTC-midnight execution.
    """
    if as_of is None:
        as_of = datetime.datetime.now(EASTERN_TZ)
    elif as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=EASTERN_TZ)
    else:
        as_of = as_of.astimezone(EASTERN_TZ)

    current_date = as_of.date()
    weekday = as_of.weekday()  # Monday=0, Sunday=6

    # If weekend: Saturday (5) -> Friday, Sunday (6) -> Friday
    if weekday == 5:
        return current_date - datetime.timedelta(days=1)
    elif weekday == 6:
        return current_date - datetime.timedelta(days=2)

    # If weekday before 09:30 ET (market open), session is previous trading day
    market_open_time = datetime.time(9, 30)
    if as_of.time() < market_open_time:
        if weekday == 0:  # Monday morning -> previous Friday
            return current_date - datetime.timedelta(days=3)
        else:
            return current_date - datetime.timedelta(days=1)

    return current_date


def latest_completed_session(now: Optional[datetime.datetime] = None) -> datetime.date:
    """The last NYSE session whose close has passed (16:15 ET buffer), from the run's clock."""
    now = (now or datetime.datetime.now(EASTERN_TZ)).astimezone(EASTERN_TZ)
    d = now.date()
    if is_nyse_session(d) and (now.hour, now.minute) >= (16, 15):
        return d
    k = d - datetime.timedelta(days=1)
    while not is_nyse_session(k):
        k -= datetime.timedelta(days=1)
    return k


def get_trading_days_ago(n_days: int, as_of: Optional[datetime.date] = None) -> datetime.date:
    """
    Calculate the date approximately n trading days in the past from as_of (skipping weekends).
    """
    current = as_of or get_market_date()
    days_counted = 0
    while days_counted < n_days:
        current -= datetime.timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            days_counted += 1
    return current


# ------------------------------------------------------------------------------
# NYSE trading sessions (for windows measured in trading days)
# ------------------------------------------------------------------------------
def _nyse_holidays(start: datetime.date, end: datetime.date) -> list:
    """NYSE full-day holidays between start and end (inclusive), from the exchange's standing rules."""
    from pandas.tseries.holiday import (
        AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr,
        USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday, sunday_to_monday,
    )

    class _NYSECalendar(AbstractHolidayCalendar):
        rules = [
            Holiday("NewYearsDay", month=1, day=1, observance=sunday_to_monday),  # no Friday close for a Saturday Jan 1
            USMartinLutherKingJr,
            USPresidentsDay,
            GoodFriday,
            USMemorialDay,
            Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
            Holiday("IndependenceDay", month=7, day=4, observance=nearest_workday),
            USLaborDay,
            USThanksgivingDay,
            Holiday("Christmas", month=12, day=25, observance=nearest_workday),
        ]

    return [d.date() for d in _NYSECalendar().holidays(start=start, end=end)]


def trading_sessions_between(start: datetime.date, end: datetime.date) -> int:
    """
    NYSE trading sessions after `start` up to and including `end` (0 when end <= start).
    Example: from Friday to the following Wednesday is 3 sessions (Mon, Tue, Wed).
    """
    import numpy as np

    if end <= start:
        return 0
    begin = start + datetime.timedelta(days=1)
    stop = end + datetime.timedelta(days=1)
    return int(np.busday_count(begin, stop, holidays=_nyse_holidays(begin, end)))


_HOLIDAYS_CACHE = None


def _all_holidays():
    """NYSE standing-rule holidays 1990-2045, computed once (same rules as _nyse_holidays)."""
    global _HOLIDAYS_CACHE
    if _HOLIDAYS_CACHE is None:
        import numpy as np
        _HOLIDAYS_CACHE = np.array(_nyse_holidays(datetime.date(1990, 1, 1), datetime.date(2045, 12, 31)),
                                   dtype="datetime64[D]")
    return _HOLIDAYS_CACHE


_BUSDAYCAL = None


def nyse_busdaycal():
    """Cached numpy business-day calendar with the NYSE holidays (building one per call is slow)."""
    global _BUSDAYCAL
    if _BUSDAYCAL is None:
        import numpy as np
        _BUSDAYCAL = np.busdaycalendar(holidays=_all_holidays())
    return _BUSDAYCAL


def _session_holidays(start: datetime.date, end: datetime.date) -> list:
    return _nyse_holidays(start - datetime.timedelta(days=7), end + datetime.timedelta(days=400))


def is_nyse_session(day: datetime.date) -> bool:
    """True when `day` is a regular NYSE session (weekday, not a standing-rule holiday)."""
    import numpy as np

    return bool(np.is_busday(day, busdaycal=nyse_busdaycal()))


def session_on_or_after(day: datetime.date) -> datetime.date:
    """The first NYSE session on or after `day`."""
    import numpy as np

    out = np.busday_offset(day, 0, roll="forward", busdaycal=nyse_busdaycal())
    return datetime.date.fromisoformat(str(out))


def add_sessions(day: datetime.date, n: int) -> datetime.date:
    """The NYSE session `n` sessions after `day` (n >= 0; `day` rolls forward to a session first)."""
    import numpy as np

    out = np.busday_offset(day, n, roll="forward", busdaycal=nyse_busdaycal())
    return datetime.date.fromisoformat(str(out))
