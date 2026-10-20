"""
Point-in-time earnings dates from SEC 8-K item 2.02 filings
===========================================================
One source for every earnings date the Ideas engine uses, shared by the live scan
(jobs/generate_signals.py) and the backtest harness (scripts/validate_backtest_pipeline.py) so both
apply the same dates, cutoff and rules (parity, 2026-10-20):
  * PEAD's last report (jobs/strategies/pead.py);
  * Pullback's next-report buffer (jobs/strategies/pullback.py);
  * the issuance gate: unknown date, fixed-days blackout, full hold (src/trade_hygiene.earnings_gate).

A report is known at the close of session `as_of` when its 8-K was accepted by 20:00 ET that day
(sec_filings.DECISION_CUTOFF_HOUR); later filings belong to the next session. The next report date
is always a point-in-time estimate (trade_hygiene.estimate_next_earnings), never the actual later
filing, so the backtest cannot see the future.

The caller registers how to get a ticker's events (set_events_lookup): the live scan reads the SEC
provider, the harness its preloaded events.
"""

from datetime import date, datetime
from typing import Callable, List, Optional, Sequence

from src.providers.context.sec_filings import DECISION_CUTOFF_HOUR, EarningsEvent, _next_session_after

EventsLookup = Callable[..., Optional[Sequence[EarningsEvent]]]
_LOOKUP: Optional[EventsLookup] = None


def set_events_lookup(fn: Optional[EventsLookup]) -> None:
    """fn(ticker, recent_only=False) -> that ticker's events, or None when SEC data is unavailable.
    recent_only=True lets a caller skip tickers without a recent 8-K (PEAD needs only those)."""
    global _LOOKUP
    _LOOKUP = fn


def events_for(ticker: str, recent_only: bool = False) -> Optional[Sequence[EarningsEvent]]:
    if _LOOKUP is None:
        return None
    return _LOOKUP(ticker.strip().upper(), recent_only=recent_only)


def _effective_day(accepted: datetime) -> date:
    """Session date a filing belongs to for decisions (sec_filings.effective_day for an event)."""
    if (accepted.hour, accepted.minute, accepted.second) > (DECISION_CUTOFF_HOUR, 0, 0):
        return _next_session_after(accepted.date())
    return accepted.date()


def known_events(events: Optional[Sequence[EarningsEvent]], as_of: date) -> List[EarningsEvent]:
    """Reports a decision at the close of `as_of` may use."""
    return [e for e in (events or []) if _effective_day(e.accepted) <= as_of]


def last_report(events: Optional[Sequence[EarningsEvent]], as_of: date) -> Optional[date]:
    """Announcement date (Eastern calendar date of acceptance) of the latest known report."""
    known = known_events(events, as_of)
    return max(e.announced for e in known) if known else None


def upcoming_known_reactions(events: Optional[Sequence[EarningsEvent]], as_of: date) -> List[date]:
    """Reaction sessions after `as_of` of reports already known (filed after the close, by 20:00 ET)."""
    return sorted(e.reaction for e in known_events(events, as_of) if e.reaction > as_of)


def next_report_estimate(events: Optional[Sequence[EarningsEvent]], as_of: date) -> Optional[date]:
    """Point-in-time estimate of the next report's reaction session (None without recent history)."""
    from src.trade_hygiene import estimate_next_earnings
    return estimate_next_earnings([e.reaction for e in known_events(events, as_of)], as_of)
