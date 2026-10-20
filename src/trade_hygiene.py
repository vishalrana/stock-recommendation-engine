"""
Trade hygiene filters (profitability program, Phase 2)
======================================================
Issuance filters shared by the nightly scan and the backtest. They act after an idea has
qualified on its score, so they are risk rules, not ranking inputs:

  * Earnings (earnings_gate, one function for the live scan and the backtest): every date comes
    from SEC 8-K item 2.02 filings known at the decision (src/earnings_pit.py).
      - Unknown next report (no report in the last 200 days): not issued (REJECT_UNKNOWN_EARNINGS).
      - Fixed-days blackout: the next report within EARNINGS_BLACKOUT_DAYS sessions after the scan.
      - Full hold (EARNINGS_BLACKOUT_MODE = "full_hold"): no idea when a known report's reaction
        session falls in [fill, fill + hold_days + 2 sessions], or when the estimated next report's
        +/- 5-session band overlaps that window.
  * Stop cap (STOP_CAP_ENABLED) and R:R floor (RR_FLOOR_ENABLED): reject when the stop sits further than
    min(3 x ATR14, 10% of entry) below the entry, or when T1 offers less than 1.5 R (R:R floor
    turned off 2026-10-11 by user decision).
  * Event lock (EVENT_LOCK_ENABLED): see src/providers/context/sec_filings.py.

Session arithmetic goes through a SessionCalendar so the backtest can use the price index and the
live scan the NYSE calendar.
"""

from datetime import date
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from src import quant_config as qc


class SessionCalendar:
    """Shift dates by trading sessions over a known session list, extended by NYSE rules."""

    def __init__(self, sessions: Optional[Sequence[date]] = None):
        self.sessions = np.array(sorted(set(sessions or [])), dtype="datetime64[D]")

    def shift(self, day: date, n: int) -> date:
        """The session n sessions after (n > 0) or before (n < 0) `day`; `day` rolls to a session first."""
        d64 = np.datetime64(day, "D")
        s = self.sessions
        if len(s) and s[0] <= d64 <= s[-1]:
            i = int(np.searchsorted(s, d64))                  # first session on or after day
            if n < 0 and s[i] != d64:
                i -= 1                                         # backward shifts roll back to a session first
            j = i + n
            if 0 <= j < len(s):
                return s[j].astype(object)
        from src.utils.market_date import nyse_busdaycal
        out = np.busday_offset(d64, n, roll="forward" if n >= 0 else "backward", busdaycal=nyse_busdaycal())
        return out.astype(object)


def full_hold_window(fill_session: date, hold_days: int, cal: SessionCalendar) -> Tuple[date, date]:
    """[fill, fill + hold_days + buffer sessions]: the fill is session 1 of the hold."""
    return fill_session, cal.shift(fill_session, max(1, int(hold_days)) - 1 + qc.FULL_HOLD_BUFFER_SESSIONS)


def earnings_hold_rejection(window: Tuple[date, date], confirmed: Iterable[date], estimated: Iterable[date],
                            cal: SessionCalendar) -> Optional[str]:
    """Reason to reject when earnings fall inside the hold window, else None."""
    start, end = window
    for d in sorted(confirmed):
        if start <= d <= end:
            return f"Earnings reaction {d.isoformat()} inside the hold window {start.isoformat()}..{end.isoformat()}"
    band = qc.ESTIMATED_EARNINGS_BAND_SESSIONS
    for e in sorted(estimated):
        lo, hi = cal.shift(e, -band), cal.shift(e, band)
        if lo <= end and hi >= start:
            return (f"Estimated earnings {e.isoformat()} (+/-{band} sessions) overlaps the hold window "
                    f"{start.isoformat()}..{end.isoformat()}")
    return None


def earnings_gate(strategy: str, is_etf: bool, scan_date: date, events, cal: "SessionCalendar",
                  hold_days: int) -> Tuple[Optional[str], Optional[str]]:
    """
    (rule, reason) when the earnings rules reject a new idea, else (None, None). The live scan and
    the backtest call this same function with the ticker's SEC 8-K item 2.02 events (None when no
    SEC data exists for it); only reports accepted by the 20:00 ET cutoff on scan_date are used.
    Rules: "earnings_unknown", "earnings_blackout" (fixed days), "earnings_full_hold".
    """
    from src.earnings_pit import next_report_estimate, upcoming_known_reactions
    from src.quant_config import normalize_strategy_key
    from src.utils.market_date import trading_sessions_between
    key = normalize_strategy_key(strategy)
    if is_etf or key == "sector_rotation":
        return None, None
    upcoming = upcoming_known_reactions(events, scan_date)      # reported after the close, reaction ahead
    est = next_report_estimate(events, scan_date)
    nxt = upcoming[0] if upcoming else est
    if nxt is None:
        if qc.REJECT_UNKNOWN_EARNINGS:
            return "earnings_unknown", "Earnings date unknown (no SEC 8-K item 2.02 report in the last 200 days)"
        return None, None
    blackout = qc.EARNINGS_BLACKOUT_DAYS.get(key, 5)
    sessions = trading_sessions_between(scan_date, nxt)
    if 0 < sessions <= blackout:
        kind = "reported" if upcoming else "estimated"
        return "earnings_blackout", f"Earnings in {sessions} trading days ({kind} {nxt.isoformat()}; blackout {blackout})"
    if qc.EARNINGS_BLACKOUT_MODE == "full_hold":
        window = full_hold_window(cal.shift(scan_date, 1), hold_days or 20, cal)
        reason = earnings_hold_rejection(window, upcoming, [est] if est else [], cal)
        if reason:
            return "earnings_full_hold", f"Earnings inside the hold: {reason}"
    return None, None


def rr_stop_enabled() -> bool:
    """Whether the stop cap / R:R floor check runs; the live scan and the backtest both ask here."""
    return bool(qc.STOP_CAP_ENABLED or qc.RR_FLOOR_ENABLED)


def rr_stop_rejection(entry: float, stop: float, target_1: Optional[float], atr: Optional[float]) -> Optional[str]:
    """Reason to reject for a stop beyond the cap or a T1 R:R below the floor, else None."""
    try:
        entry, stop = float(entry), float(stop)
    except (TypeError, ValueError):
        return None
    risk = entry - stop
    if not (entry > 0 and risk > 0):
        return None  # plan validity is checked by the trade-plan builder
    if qc.STOP_CAP_ENABLED:
        cap = qc.STOP_CAP_PCT * entry
        if atr is not None and np.isfinite(atr) and atr > 0:
            cap = min(cap, qc.STOP_CAP_ATR_MULT * float(atr))
        if risk > cap * (1 + 1e-9):
            return (f"Stop {risk / entry * 100:.1f}% below entry exceeds the cap of {cap / entry * 100:.1f}% "
                    f"(min of {qc.STOP_CAP_ATR_MULT:g} x ATR14 and {qc.STOP_CAP_PCT * 100:g}%)")
    if qc.RR_FLOOR_ENABLED:
        if target_1 is not None:
            rr1 = (float(target_1) - entry) / risk
            if rr1 < qc.RR_FLOOR - 1e-9:
                return f"T1 reward/risk {rr1:.2f} below the {qc.RR_FLOOR:g} floor"
    return None


def estimate_next_earnings(past_reactions: Sequence[date], as_of: date) -> Optional[date]:
    """
    Point-in-time estimate of the next earnings date from past reaction sessions: the nearest
    one-year anniversary (+364 days, same weekday) of a past report that falls after `as_of`, when
    four reports of history exist; otherwise the last report + 91 days, rolled forward by quarters.
    None without a report in the past 200 days.
    """
    past = sorted(d for d in past_reactions if d <= as_of)
    if not past or (as_of - past[-1]).days > 200:
        return None
    from datetime import timedelta
    year = timedelta(days=364)
    anniversaries = [p + year for p in past if as_of < p + year <= as_of + timedelta(days=200)]
    if len(past) >= 4 and anniversaries:
        return min(anniversaries)
    est = past[-1] + timedelta(days=91)
    while est <= as_of:
        est += timedelta(days=91)
    return est
