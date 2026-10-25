"""
Trade Hygiene (Profitability Program, Phase 2) — Regression Suite
=================================================================
1. SEC acceptance times: the submissions JSON value converts to the EDGAR header's Eastern time.
2. Reaction sessions: pre-market and intraday -> same session; after the close, weekends and
   holidays -> next session.
3. Earnings events from 8-K item 2.02 (amendments folded, one per reaction session).
4. Event lock: a DEFM14A locks, a later 8-K item 2.01 or Form 25 unlocks, 180 quiet days expire,
   rights-offering hits lock; PBT-like 425 filings lock; the price-pin backstop.
5. Royalty trusts (PBT) are not eligible common equities; banks and REITs named "Trust" still are.
6. Earnings inside the hold (TEVA-like): confirmed and estimated dates, the +/-5-session band.
7. Stop cap and R:R floor (SNOW-like); assign_tier stays score-based.
8. Strategy evidence: expected R and exit mix are display-only.
9. The backtest harness and the live scan apply the same rules (parity): same SEC events known at
   the decision, no look-ahead, unknown earnings dates rejected.
"""

import os
import sys
import unittest
from datetime import date, datetime
from unittest import mock

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src import quant_config as qc  # noqa: E402
from src.providers.context.sec_filings import (  # noqa: E402
    Filing, acceptance_et, reaction_session, parse_filings, earnings_events, event_lock, price_pin,
)
from src.trade_hygiene import (  # noqa: E402
    SessionCalendar, full_hold_window, earnings_hold_rejection, rr_stop_rejection, estimate_next_earnings,
)


def F(form, ts, items=""):
    return Filing(form, datetime.fromisoformat(ts), tuple(x for x in items.split(",") if x))


class TestSecTimes(unittest.TestCase):
    def test_acceptance_conversion_matches_edgar_headers(self):
        # Pairs checked against the EDGAR -index-headers ACCEPTANCE-DATETIME on 2026-10-11
        cases = {
            "2026-07-31T00:30:28.000Z": datetime(2026, 7, 30, 16, 30, 28),   # AAPL, daylight time
            "2026-01-30T02:30:33.000Z": datetime(2026, 1, 29, 16, 30, 33),   # AAPL, standard time
            "2026-07-14T14:30:38.000Z": datetime(2026, 7, 14, 6, 30, 38),    # JPM, pre-market
            "2026-01-28T17:20:32.000Z": datetime(2026, 1, 28, 7, 20, 32),    # TEVA, pre-market, winter
            "2015-01-28T02:31:00.000Z": datetime(2015, 1, 27, 16, 31, 0),    # older submissions page
        }
        for raw, expected in cases.items():
            self.assertEqual(acceptance_et(raw, "shifted"), expected, raw)
        self.assertIsNone(acceptance_et("not a date", "shifted"))

    def test_true_utc_convention_matches_edgar_headers(self):
        # EDGAR returned true UTC by 2026-10-25; pairs checked against the -index-headers that day
        cases = {
            "2026-07-30T20:30:28.000Z": datetime(2026, 7, 30, 16, 30, 28),   # AAPL 0000320193-26-000018
            "2025-10-30T21:53:18.000Z": datetime(2025, 10, 30, 17, 53, 18),  # GTE 0001273441-25-000035
            "2026-08-05T01:05:15.000Z": datetime(2026, 8, 4, 21, 5, 15),     # GTE 0001273441-26-000029, after 20:00 ET
            "2026-01-28T12:20:32.000Z": datetime(2026, 1, 28, 7, 20, 32),    # TEVA, standard time
        }
        for raw, expected in cases.items():
            self.assertEqual(acceptance_et(raw, "utc"), expected, raw)

    def test_the_canary_tells_the_convention(self):
        from src.providers.context.sec_filings import detect_convention
        def block(raw):
            return {"form": ["4", "8-K"], "filingDate": ["2026-07-30", "2026-07-30"],
                    "acceptanceDateTime": ["2026-07-30T22:00:00.000Z", raw], "items": ["", "2.02,9.01"]}
        self.assertEqual(detect_convention(block("2026-07-30T20:30:28.000Z")), "utc")
        self.assertEqual(detect_convention(block("2026-07-31T00:30:28.000Z")), "shifted")
        self.assertIsNone(detect_convention(block("2026-07-30T16:30:28.000Z")))     # neither: fetch nothing
        self.assertIsNone(detect_convention({}))

    def test_a_response_in_the_other_convention_is_not_used(self):
        from src.providers.context.sec_filings import contradicts
        pre_market_utc = ["2026-07-1%dT11:00:00.000Z" % d for d in range(3, 8)]   # 07:00 ET, Mon-Fri
        self.assertTrue(contradicts(pre_market_utc, "shifted"))                  # would read 03:00 ET
        self.assertFalse(contradicts(pre_market_utc, "utc"))
        self.assertFalse(contradicts(["2026-07-14T19:00:00.000Z"], "shifted"))   # fits both: no verdict

    def test_earlier_cache_files_are_refetched(self):
        import json, tempfile
        from unittest import mock
        from src.providers.context import sec_filings as sf
        sec = mock.MagicMock(available=True)
        sec.cik_for.return_value = 1
        sec._get_json.return_value = None                                       # any fetch fails
        row = [["8-K", "2026-07-30T16:30:28", "2.02"]]
        with tempfile.TemporaryDirectory() as d:
            prov = sf.SecFilingsProvider(cache_dir=d, ttl_hours=None, sec=sec)
            for fmt, expect in (("v2-6k", None), (sf.CACHE_FORMAT, 1)):    # v2: refetch, which fails here
                json.dump({"fetched_at": 1792731600, "since": "2021-01-01", "format": fmt,
                           "recent_rows": row, "pages": {}}, open(prov._path(1), "w"))
                got = prov.filings("X")
                self.assertEqual(None if got is None else len(got), expect, fmt)

    def test_reaction_session(self):
        self.assertEqual(reaction_session(datetime(2026, 7, 30, 16, 30)), date(2026, 7, 31))   # Thu after close
        self.assertEqual(reaction_session(datetime(2026, 7, 14, 6, 30)), date(2026, 7, 14))    # pre-market
        self.assertEqual(reaction_session(datetime(2026, 7, 14, 11, 0)), date(2026, 7, 14))    # intraday
        self.assertEqual(reaction_session(datetime(2026, 7, 31, 16, 5)), date(2026, 8, 3))     # Fri after close
        self.assertEqual(reaction_session(datetime(2026, 7, 3, 7, 0)), date(2026, 7, 6))       # observed holiday
        self.assertEqual(reaction_session(datetime(2026, 8, 1, 9, 0)), date(2026, 8, 3))       # Saturday


class TestEarningsEvents(unittest.TestCase):
    def test_parse_and_events(self):
        block = {
            "form": ["8-K", "8-K/A", "4", "8-K", "DEFM14A", "8-K"],
            "acceptanceDateTime": ["2026-07-31T00:30:28.000Z", "2026-07-31T01:00:00.000Z", "2026-07-20T20:00:00.000Z",
                                   "2026-05-01T00:30:41.000Z", "2026-06-01T20:00:00.000Z", "2026-06-10T20:00:00.000Z"],
            "items": ["2.02,9.01", "2.02", "", "2.02,9.01", "", "8.01"],
        }
        filings = parse_filings(block, "shifted", since=date(2026, 1, 1))
        self.assertEqual({f.form for f in filings}, {"8-K", "DEFM14A"})       # Form 4 dropped, /A folded
        ev = earnings_events(filings)
        self.assertEqual([e.reaction for e in ev], [date(2026, 5, 1), date(2026, 7, 31)])  # amendment deduplicated
        self.assertEqual(ev[-1].announced, date(2026, 7, 30))


class TestEventLock(unittest.TestCase):
    def test_defm14a_locks_until_completion(self):
        filings = [F("8-K", "2026-01-29T16:30:00", "2.02,9.01"), F("DEFM14A", "2026-03-02T17:00:00")]
        self.assertIsNone(event_lock(filings, date(2026, 2, 27)))
        lock = event_lock(filings, date(2026, 4, 1))
        self.assertEqual(lock["form"], "DEFM14A")
        self.assertEqual(lock["since"], "2026-03-02")
        done = filings + [F("8-K", "2026-05-01T08:00:00", "2.01,3.01,5.01")]
        self.assertIsNone(event_lock(done, date(2026, 5, 2)))
        self.assertIsNotNone(event_lock(done, date(2026, 4, 30)))            # point-in-time: not yet completed
        delisted = filings + [F("25-NSE", "2026-05-03T09:00:00")]
        self.assertIsNone(event_lock(delisted, date(2026, 5, 4)))
        self.assertIsNone(event_lock(filings, date(2026, 9, 1)))             # 183 quiet days: expired

    def test_pbt_like_425_filings_lock(self):
        pbt = [F("8-K", "2026-09-18T07:48:00", "2.02,9.01"), F("425", "2026-05-19T08:00:00"),
               F("425", "2026-07-29T08:00:00"), F("425", "2026-07-30T08:00:00"), F("8-K", "2026-07-29T08:00:00", "7.01,9.01")]
        lock = event_lock(pbt, date(2026, 10, 9))
        self.assertEqual(lock["since"], "2026-05-19")
        self.assertEqual(lock["last_deal_filing"], "2026-07-30")

    def test_rights_offering_locks(self):
        self.assertIsNotNone(event_lock([], date(2026, 9, 30), [date(2026, 9, 24)]))
        self.assertIsNone(event_lock([], date(2026, 9, 20), [date(2026, 9, 24)]))  # not yet filed

    def test_price_pin_backstop(self):
        rng = np.random.default_rng(0)
        r = rng.normal(0, 0.02, 400)
        r[340] = np.log(1.30)                     # +30% deal jump
        r[341:] = rng.normal(0, 0.0007, 59)       # then pinned near the deal price
        close = pd.Series(50 * np.exp(np.cumsum(r)))
        pin = price_pin(close)
        self.assertTrue(pin.iloc[350:371].all())     # sessions 10-30 after the move
        self.assertFalse(pin.iloc[:350].any())
        self.assertFalse(pin.iloc[-1])           # more than 30 sessions after the jump
        normal = price_pin(pd.Series(50 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))))
        self.assertFalse(normal.any())


class TestRoyaltyTrusts(unittest.TestCase):
    def test_pbt_is_not_eligible(self):
        from src.universe.filters import is_eligible_equity
        from src.universe.models import SecurityRecord
        ok, reason = is_eligible_equity(SecurityRecord(ticker="PBT", company_name="Permian Basin Royalty Trust Common Stock",
                                                       exchange="NYSE"))
        self.assertFalse(ok)
        self.assertIn("royalty trust", reason.lower())
        for name in ("VOC Energy Trust Units of Beneficial Interest", "Mesabi Trust Common Stock"):
            self.assertFalse(is_eligible_equity(SecurityRecord(ticker="X", company_name=name, exchange="NYSE"))[0])
        for name in ("Northern Trust Corporation - Common Stock", "Arbor Realty Trust Common Stock",
                     "Washington Trust Bancorp, Inc. - Common Stock"):
            self.assertTrue(is_eligible_equity(SecurityRecord(ticker="X", company_name=name, exchange="NYSE"))[0], name)


class TestEarningsInsideHold(unittest.TestCase):
    cal = SessionCalendar()

    def test_teva_like_idea_is_blocked(self):
        # TEVA reported 2026-01-28, 04-29, 07-29 (pre-market). An idea on 2026-10-09 with a 25-session
        # hold runs to mid-November; the next report (est. 2026-10-28) falls inside.
        past = [date(2025, 10, 29), date(2026, 1, 28), date(2026, 4, 29), date(2026, 7, 29)]
        est = estimate_next_earnings(past, date(2026, 10, 9))
        self.assertEqual(est, date(2026, 10, 28))
        window = full_hold_window(self.cal.shift(date(2026, 10, 9), 1), 25, self.cal)
        self.assertEqual(window, (date(2026, 10, 12), date(2026, 11, 17)))
        self.assertIsNotNone(earnings_hold_rejection(window, [], [est], self.cal))
        self.assertIsNotNone(earnings_hold_rejection(window, [date(2026, 10, 28)], [], self.cal))

    def test_window_edges_and_band(self):
        window = full_hold_window(date(2026, 10, 12), 10, self.cal)       # 10 sessions + 2 buffer
        self.assertEqual(window[1], self.cal.shift(date(2026, 10, 12), 11))
        after = self.cal.shift(window[1], 1)
        self.assertIsNone(earnings_hold_rejection(window, [after], [], self.cal))
        self.assertIsNotNone(earnings_hold_rejection(window, [window[1]], [], self.cal))
        self.assertIsNotNone(earnings_hold_rejection(window, [], [self.cal.shift(window[1], 4)], self.cal))   # band
        self.assertIsNone(earnings_hold_rejection(window, [], [self.cal.shift(window[1], 7)], self.cal))
        self.assertIsNone(earnings_hold_rejection(window, [], [], self.cal))                                # unknown

    def test_estimate_needs_recent_history(self):
        self.assertIsNone(estimate_next_earnings([date(2025, 1, 2)], date(2026, 10, 9)))
        self.assertIsNone(estimate_next_earnings([], date(2026, 10, 9)))
        self.assertEqual(estimate_next_earnings([date(2026, 8, 3)], date(2026, 10, 9)), date(2026, 11, 2))

    def test_session_calendar_matches_price_index(self):
        idx = [d.date() for d in pd.bdate_range("2026-09-01", "2026-12-31") if d.date() not in (date(2026, 11, 26), date(2026, 9, 7))]
        priced = SessionCalendar(idx)
        for d0 in (date(2026, 9, 4), date(2026, 10, 10), date(2026, 11, 25)):
            for n in (-3, 0, 1, 5):
                self.assertEqual(priced.shift(d0, n), self.cal.shift(d0, n), (d0, n))


class TestStopCapAndRrFloor(unittest.TestCase):
    def test_snow_like_idea_is_rejected(self):
        # SNOW idea: stop -14.2% below entry, T1 +9.7% above (R:R 0.68)
        reason = rr_stop_rejection(100.0, 85.8, 109.7, 4.0)
        self.assertIsNotNone(reason)
        self.assertIn("cap", reason)
        # Stop inside the cap: with RR_FLOOR_ENABLED off (user decision), passes; with it on, rejects
        self.assertIsNone(rr_stop_rejection(100.0, 95.0, 104.0, 3.0))
        with mock.patch.object(qc, "RR_FLOOR_ENABLED", True):
            reason = rr_stop_rejection(100.0, 95.0, 104.0, 3.0)
            self.assertIn("reward/risk", reason)
        self.assertIsNone(rr_stop_rejection(100.0, 95.0, 108.0, 2.5))   # 5% stop < min(7.5%, 10%); 1.6 R
        self.assertIsNotNone(rr_stop_rejection(100.0, 92.0, 120.0, 2.0))  # 8% stop > 3 x ATR = 6%

    def test_tier_stays_score_based(self):
        from src.ranker import assign_tier
        for score in (60.0, 70.0, 85.0):
            self.assertEqual(assign_tier(score, 0.2), assign_tier(score, 5.0))


class TestExpectedR(unittest.TestCase):
    def test_expected_r_and_exit_mix(self):
        from src.strategy_evidence import aggregate_trades, evidence_from_aggregate, apply_evidence_to_candidate
        trades = [
            {"strategy": "Trend Following", "net_return_pct": 4.0, "stop_pct": 8.0, "exit_reason": "expiry",
             "outcome": "hit_t1", "has_t2": True, "has_t3": False},
            {"strategy": "Trend Following", "net_return_pct": -8.2, "stop_pct": 8.0, "exit_reason": "stop",
             "outcome": "stopped", "has_t2": True, "has_t3": False},
            {"strategy": "Trend Following", "net_return_pct": 1.0, "stop_pct": 5.0, "exit_reason": "expiry",
             "outcome": "expired", "has_t2": True, "has_t3": False},
        ]
        ev = evidence_from_aggregate("trend_following", aggregate_trades(trades)["trend_following"])
        self.assertAlmostEqual(ev.expected_r, round((0.5 - 1.025 + 0.2) / 3, 4))
        self.assertEqual(ev.exit_mix, {"stop": 33.33, "target": 0.0, "expiry": 66.67})
        sig = {}
        apply_evidence_to_candidate(sig, ev)
        self.assertEqual(sig["strategy_expected_r"], ev.expected_r)
        self.assertEqual(sig["strategy_t2_hit_pct"], 0.0)
        self.assertIsNone(sig["strategy_t3_hit_pct"])


class TestHarnessAndLiveHooks(unittest.TestCase):
    """The backtest harness and the live scan apply the same stop-cap, earnings and event-lock rules
    (parity, 2026-10-20): same functions, same order, the same SEC events known at the decision."""

    @staticmethod
    def _ev(*days, hour=7, minute=0):
        from src.providers.context.sec_filings import EarningsEvent
        return [EarningsEvent(d, d if hour < 16 else reaction_session(datetime(d.year, d.month, d.day, hour, minute)),
                              datetime(d.year, d.month, d.day, hour, minute)) for d in days]

    def _teva(self):   # reports late Jan / Apr / Jul / Oct: the next one (about 2026-10-28) is inside the hold
        return self._ev(date(2025, 10, 29), date(2026, 1, 28), date(2026, 4, 29), date(2026, 7, 29))

    def _far(self):    # reported 2026-10-05: the next report (about 2027-01-05) is far outside the hold
        return self._ev(date(2025, 10, 6), date(2026, 1, 6), date(2026, 4, 6), date(2026, 7, 6), date(2026, 10, 5))

    def _events(self):
        return {
            "events": {"TEVA": self._teva(), "FAR": self._far(), "SNOW": self._far(), "ACQ": self._far(), "NOREP": []},
            "filings": {"TEVA": [], "FAR": [], "SNOW": [], "ACQ": [F("DEFM14A", "2026-09-01T17:00:00")], "NOSEC": None,
                        "NOREP": []},
            "rights": {},
            "unavailable": {"NOSEC": "fetch_failed"},
        }

    def test_harness_rules(self):
        from scripts.validate_backtest_pipeline import hygiene_rejection, production_hygiene_rules
        cal = SessionCalendar()
        d = pd.Timestamp("2026-10-09")
        sig = {"strategy": "Trend Following", "entry_price": 100.0, "stop_loss": 95.0, "target_1": 108.0, "atr_14": 2.5}
        ev = self._events()
        self.assertEqual(production_hygiene_rules(), ("rr_floor_stop_cap", "earnings", "event_lock"))  # the live order
        rule, reason, _ = hygiene_rejection(("earnings",), sig, "TEVA", d, False, ev, cal, 25, None)
        self.assertEqual(rule, "earnings_full_hold")
        self.assertIn("Estimated earnings 2026-10-28", reason)
        self.assertIsNone(hygiene_rejection(("earnings",), sig, "TEVA", d, True, ev, cal, 25, None)[0])   # ETFs exempt
        self.assertIsNone(hygiene_rejection(("earnings",), sig, "FAR", d, False, ev, cal, 25, None)[0])
        rule, reason, flags = hygiene_rejection(("earnings",), sig, "NOSEC", d, False, ev, cal, 25, None)
        self.assertEqual(rule, "earnings_sec_unavailable")                        # filings not read: not issued
        self.assertIn("SEC fetch failed", reason)
        self.assertTrue(flags["earnings_unknown"] and flags["earnings_sec_unavailable"])
        rule, reason, flags = hygiene_rejection(("earnings",), sig, "NOREP", d, False, ev, cal, 25, None)
        self.assertEqual(rule, "earnings_unknown")                                # read, no report: not issued
        self.assertIn("no SEC 8-K item 2.02 report", reason)
        self.assertTrue(flags["earnings_unknown"])
        self.assertFalse(flags["earnings_sec_unavailable"])
        # allow_unknown_earnings variant: undated ideas pass on to the next rule, dated ones still gate
        for t in ("NOREP", "NOSEC"):
            rule, _, flags = hygiene_rejection(("earnings", "event_lock"), sig, t, d, False, ev, cal, 25, False,
                                               reject_unknown=False)
            self.assertIsNone(rule, t)
            self.assertTrue(flags["earnings_unknown"])
        self.assertEqual(hygiene_rejection(("earnings",), sig, "TEVA", d, False, ev, cal, 25, None,
                                           reject_unknown=False)[0], "earnings_full_hold")
        rule, reason, _ = hygiene_rejection(("event_lock",), sig, "ACQ", d, False, ev, cal, 25, False)
        self.assertEqual(rule, "event_lock")
        self.assertIn("DEFM14A", reason)
        rule, _, flags = hygiene_rejection(("event_lock",), sig, "NOSEC", d, False, ev, cal, 25, False)
        self.assertIsNone(rule)
        self.assertTrue(flags["event_lock_unknown"])
        self.assertEqual(hygiene_rejection(("event_lock",), sig, "TEVA", d, False, ev, cal, 25, True)[0], "event_lock")
        snow = dict(sig, entry_price=100.0, stop_loss=85.8, target_1=109.7, atr_14=4.0)
        self.assertEqual(hygiene_rejection(("rr_floor_stop_cap",), snow, "SNOW", d, False, ev, cal, 25, None)[0],
                         "rr_floor_stop_cap")
        self.assertIsNone(hygiene_rejection(("rr_floor_stop_cap",), sig, "SNOW", d, False, ev, cal, 25, None)[0])

    def test_no_lookahead_and_cutoff(self):
        """A filing after the decision cannot change it; one filed after the close but by 20:00 ET can."""
        from scripts.validate_backtest_pipeline import hygiene_rejection
        cal = SessionCalendar()
        d = pd.Timestamp("2026-10-09")
        sig = {"strategy": "Trend Following", "entry_price": 100.0, "stop_loss": 95.0, "target_1": 108.0, "atr_14": 2.5}
        base = self._events()
        decision = hygiene_rejection(("earnings",), sig, "FAR", d, False, base, cal, 25, None)
        self.assertIsNone(decision[0])
        # The actual next report (filed 2026-10-12, inside the hold) is unknown at the 2026-10-09 close
        later = self._events()
        later["events"]["FAR"] = self._far() + self._ev(date(2026, 10, 12))
        self.assertEqual(hygiene_rejection(("earnings",), sig, "FAR", d, False, later, cal, 25, None), decision)
        # Accepted 2026-10-09 21:00 (after the 20:00 cutoff): belongs to the next session, ignored
        late = self._events()
        late["events"]["FAR"] = self._far() + self._ev(date(2026, 10, 9), hour=21)
        self.assertEqual(hygiene_rejection(("earnings",), sig, "FAR", d, False, late, cal, 25, None), decision)
        # Accepted 2026-10-09 18:00 (after the close, by the cutoff): known; it reacts on the fill session
        known = self._events()
        known["events"]["FAR"] = self._far() + self._ev(date(2026, 10, 9), hour=18)
        rule, reason, _ = hygiene_rejection(("earnings",), sig, "FAR", d, False, known, cal, 25, None)
        self.assertEqual(rule, "earnings_blackout")
        self.assertIn("reported 2026-10-12", reason)

    def test_issued_ideas_only_carry_an_estimate(self):
        """The card labels the gate's date "estimated": a report filed by the cutoff whose reaction is still
        ahead rejects every strategy, so no issued idea carries a reported date."""
        from src.pipeline_steps import holding_days_for
        from src.trade_hygiene import earnings_gate, gate_earnings_date
        d = date(2026, 10, 5)                                         # reported after Monday's close
        ev = self._far()[:-1] + self._ev(date(2026, 10, 5), hour=17, minute=30)
        self.assertEqual(gate_earnings_date(ev, d), date(2026, 10, 6))
        for name in ("Trend Following", "Pullback Recovery", "52-Week High Breakout", "Post-Earnings Drift",
                     "Cross-Sectional Momentum", "Mean Reversion"):
            rule, _ = earnings_gate(name, False, d, ev, SessionCalendar(), holding_days_for(name))
            self.assertIn(rule, ("earnings_blackout", "earnings_full_hold"), name)
        teva = self._teva()                                           # estimate: the date the gate tests
        nxt = gate_earnings_date(teva, date(2026, 10, 9))
        self.assertIn(nxt.isoformat(), earnings_gate("Trend Following", False, date(2026, 10, 9), teva,
                                                     SessionCalendar(), 20)[1])
        self.assertIsNone(gate_earnings_date([], date(2026, 10, 9)))

    def test_live_and_backtest_make_the_same_decisions(self):
        import jobs.generate_signals as gs
        from scripts.validate_backtest_pipeline import hygiene_rejection, production_hygiene_rules
        ev = self._events()
        cases = [("TEVA", "Trend Following", 20.0, 19.0), ("FAR", "Trend Following", 20.0, 19.0),
                 ("NOSEC", "Trend Following", 20.0, 19.0), ("NOREP", "Trend Following", 20.0, 19.0),
                 ("FAR", "Cross-Sectional Momentum", 20.0, 19.0),
                 ("SNOW", "Trend Following", 100.0, 85.8), ("TEVA", "Sector Rotation", 20.0, 19.0)]
        lookup = lambda t, recent_only=False: ev["events"].get(t)
        for d in (date(2026, 10, 9), date(2026, 10, 21), date(2026, 12, 1)):
            for ticker, strategy, entry, stop in cases:
                sig = {"ticker": ticker, "strategy": strategy, "entry_price": entry, "stop_loss": stop,
                       "target_1": entry * 1.08, "atr_14": entry * 0.02}
                with mock.patch.object(qc, "EVENT_LOCK_ENABLED", False), \
                        mock.patch.object(gs, "_ticker_earnings_events", side_effect=lookup), \
                        mock.patch.dict(gs._SEC_UNAVAILABLE, ev["unavailable"], clear=True):
                    live = gs._trade_hygiene_rejection(dict(sig), None, d, {})
                    rule, reason, _ = hygiene_rejection(production_hygiene_rules(), dict(sig), ticker, pd.Timestamp(d),
                                                        strategy == "Sector Rotation", ev, SessionCalendar(),
                                                        gs_hold(strategy), None)
                bt = None if rule is None else (f"Stop cap / R:R floor: {reason}" if rule == "rr_floor_stop_cap" else reason)
                self.assertEqual(live, bt, (d, ticker, strategy))

    def test_backtest_and_live_share_stop_cap_check(self):
        """Every flag combination: the backtest and the live scan both call trade_hygiene.rr_stop_rejection
        with the same plan, or neither does."""
        import jobs.generate_signals as gs
        import src.trade_hygiene as th
        from scripts.validate_backtest_pipeline import hygiene_rejection, production_hygiene_rules
        sig = {"ticker": "SNOW", "strategy": "Trend Following", "entry_price": 100.0, "stop_loss": 85.8,
               "target_1": 109.7, "atr_14": 4.0}
        ev = self._events()
        for cap in (False, True):
            for floor in (False, True):
                with mock.patch.object(qc, "STOP_CAP_ENABLED", cap), mock.patch.object(qc, "RR_FLOOR_ENABLED", floor), \
                        mock.patch.object(qc, "EVENT_LOCK_ENABLED", False), \
                        mock.patch.object(gs, "_ticker_earnings_events", side_effect=lambda t, recent_only=False: ev["events"].get(t)), \
                        mock.patch.object(th, "rr_stop_rejection", wraps=th.rr_stop_rejection) as spy:
                    live = gs._trade_hygiene_rejection(dict(sig), None, date(2026, 10, 9), {})
                    live_calls = list(spy.call_args_list)
                    spy.reset_mock()
                    rule, _, _ = hygiene_rejection(production_hygiene_rules(), dict(sig), "SNOW", pd.Timestamp("2026-10-09"),
                                                   False, ev, SessionCalendar(), 25, None)
                    self.assertEqual(live_calls, list(spy.call_args_list), (cap, floor))
                    self.assertEqual(bool(live_calls), cap or floor)
                    self.assertEqual(live is not None, rule is not None, (cap, floor))

    def test_live_hook(self):
        import jobs.generate_signals as gs
        ev = self._events()
        lookup = lambda t, recent_only=False: ev["events"].get(t)
        teva = {"ticker": "TEVA", "strategy": "Trend Following", "entry_price": 20.0, "stop_loss": 19.0,
                "target_1": 22.0, "atr_14": 0.5}
        with mock.patch.object(gs, "_ticker_earnings_events", side_effect=lookup), \
                mock.patch.object(qc, "EVENT_LOCK_ENABLED", False):
            # SEC events decide; a provider calendar date (far away here) no longer gates
            calendar = {"TEVA": {"next_earnings_date": "2027-02-01"}}
            self.assertIn("Earnings inside the hold", gs._trade_hygiene_rejection(dict(teva), None, date(2026, 10, 9), calendar))
            # SEC filings not read: rejected for SEC, flagged unknown, counted by cause
            gs._SEC_REJECTIONS.clear()
            s = dict(teva, ticker="NODATA")
            with mock.patch.dict(gs._SEC_UNAVAILABLE, {"NODATA": "no_cik"}, clear=True):
                reason = gs._trade_hygiene_rejection(s, None, date(2026, 10, 9), {})
            self.assertEqual(reason, "Earnings date unavailable: SEC filings could not be read "
                                     "(no SEC CIK mapping for the ticker)")
            self.assertTrue(s["earnings_unknown"])
            self.assertEqual(gs._SEC_REJECTIONS, {"no_cik": 1})
            gs._SEC_REJECTIONS.clear()
            # Filings read, no report in 200 days: the unknown-date rule
            s = dict(teva, ticker="NOREP")
            self.assertIn("Earnings date unknown", gs._trade_hygiene_rejection(s, None, date(2026, 10, 9), {}))
            self.assertTrue(s["earnings_unknown"])
            self.assertIsNone(gs._trade_hygiene_rejection(dict(teva, ticker="FAR"), None, date(2026, 10, 9), {}))
        # Event lock from a DEFM14A filing (earnings clear)
        fake = mock.Mock()
        fake.filings.return_value = [F("DEFM14A", "2026-09-01T17:00:00")]
        fake.rights_offering_days.return_value = {}
        fake.sec.cik_for.return_value = 123
        with mock.patch.object(qc, "EVENT_LOCK_ENABLED", True), mock.patch.object(gs, "_sec_filings", return_value=fake), \
                mock.patch.object(gs, "_ticker_earnings_events", side_effect=lookup):
            self.assertIn("Event lock", gs._trade_hygiene_rejection(dict(teva, ticker="FAR"), None, date(2026, 10, 9), {}))


class TestLiveSecEvents(unittest.TestCase):
    def test_recent_only_skips_companies_without_a_recent_8k(self):
        import jobs.generate_signals as gs
        fake = mock.Mock()
        fake.available = True
        fake.sec.cik_for.side_effect = lambda t: {"NEW": 1, "OLD": 2}.get(t)
        fake.filings.return_value = [F("8-K", "2026-10-08T07:00:00", "2.02")]
        with mock.patch.object(gs, "_sec_filings", return_value=fake), mock.patch.object(gs, "_RECENT_8K_CIKS", {1}):
            gs._EVENTS_CACHE.clear()
            self.assertEqual(gs._ticker_earnings_events("OLD", recent_only=True), [])     # no request
            fake.filings.assert_not_called()
            self.assertEqual([e.announced for e in gs._ticker_earnings_events("NEW", recent_only=True)], [date(2026, 10, 8)])
            self.assertEqual(len(gs._ticker_earnings_events("OLD")), 1)                   # full read when asked
        gs._EVENTS_CACHE.clear()

    def test_unavailable_causes_and_run_status(self):
        import jobs.generate_signals as gs
        fake = mock.Mock()
        fake.available = True
        fake.sec.cik_for.side_effect = lambda t: {"OK": 1, "DOWN": 2}.get(t)
        fake.filings.side_effect = lambda t: [F("8-K", "2026-10-08T07:00:00", "2.02")] if t == "OK" else None
        try:
            with mock.patch.object(gs, "_sec_filings", return_value=fake):
                gs._EVENTS_CACHE.clear(); gs._SEC_UNAVAILABLE.clear(); gs._SEC_REJECTIONS.clear()
                self.assertEqual(gs._sec_run_status(), (None, False))               # nothing read yet
                self.assertIsNone(gs._ticker_earnings_events("NOCIK"))
                self.assertIsNone(gs._ticker_earnings_events("DOWN"))
                self.assertEqual(gs._SEC_UNAVAILABLE, {"NOCIK": "no_cik", "DOWN": "fetch_failed"})
                note, degraded = gs._sec_run_status()
                self.assertTrue(degraded)                                           # no read was usable
                self.assertIn("0 of 2 SEC reads usable", note)
                self.assertEqual(len(gs._ticker_earnings_events("OK")), 1)
                gs._SEC_REJECTIONS.update({"fetch_failed": 1})
                note, degraded = gs._sec_run_status()
                self.assertTrue(degraded)                                           # 1 of 2 fetches failed: 50%
                self.assertEqual(note, "SEC earnings unavailable: 1 new ideas rejected (fetch_failed 1); "
                                       "1 of 3 SEC reads usable; fetch failures 50.0%")
                # A partial outage: degraded only above 5% of the reads (a missing CIK never counts)
                for usable, expect in ((19, False), (18, True)):        # 1 fetch failure: 5.0% vs 5.3%
                    gs._EVENTS_CACHE.clear()
                    gs._EVENTS_CACHE.update({f"U{i}": [] for i in range(usable)})
                    gs._SEC_UNAVAILABLE.clear()
                    gs._SEC_UNAVAILABLE.update({"DOWN": "fetch_failed", "NOCIK": "no_cik"})
                    self.assertEqual(gs._sec_run_status()[1], expect, usable)
                fake.available = False                                              # no SEC_USER_AGENT
                gs._EVENTS_CACHE.clear(); gs._SEC_UNAVAILABLE.clear(); gs._SEC_REJECTIONS.clear()
                self.assertIsNone(gs._ticker_earnings_events("OK"))
                self.assertEqual(gs._SEC_UNAVAILABLE, {"OK": "no_user_agent"})
                self.assertTrue(gs._sec_run_status()[1])
        finally:
            gs._EVENTS_CACHE.clear(); gs._SEC_UNAVAILABLE.clear(); gs._SEC_REJECTIONS.clear()


def gs_hold(strategy):
    from src.pipeline_steps import holding_days_for
    return holding_days_for(strategy) or 20


if __name__ == "__main__":
    unittest.main()
