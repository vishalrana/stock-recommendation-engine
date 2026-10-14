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
9. The backtest harness and the live scan apply the same rules; unknown data never rejects.
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
            self.assertEqual(acceptance_et(raw), expected, raw)
        self.assertIsNone(acceptance_et("not a date"))

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
        filings = parse_filings(block, since=date(2026, 1, 1))
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
    def _events(self):
        from src.providers.context.sec_filings import EarningsEvent
        teva = [EarningsEvent(d, d, datetime(d.year, d.month, d.day, 7, 0))
                for d in (date(2025, 10, 29), date(2026, 1, 28), date(2026, 4, 29), date(2026, 7, 29))]
        return {
            "events": {"TEVA": teva, "ACQ": []},
            "filings": {"TEVA": [], "ACQ": [F("DEFM14A", "2026-09-01T17:00:00")], "NOSEC": None},
            "rights": {},
        }

    def test_backtest_and_live_share_stop_cap_check(self):
        """Every flag combination: the backtest and the live scan both call trade_hygiene.rr_stop_rejection
        with the same plan, or neither does."""
        import jobs.generate_signals as gs
        import src.trade_hygiene as th
        from scripts.validate_backtest_pipeline import hygiene_rejection, production_hygiene_rules
        sig = {"ticker": "SNOW", "strategy": "Trend Following", "entry_price": 100.0, "stop_loss": 85.8,
               "target_1": 109.7, "atr_14": 4.0}
        for cap in (False, True):
            for floor in (False, True):
                with mock.patch.object(qc, "STOP_CAP_ENABLED", cap), mock.patch.object(qc, "RR_FLOOR_ENABLED", floor), \
                        mock.patch.object(qc, "EARNINGS_BLACKOUT_MODE", "fixed_days"), \
                        mock.patch.object(qc, "EVENT_LOCK_ENABLED", False), \
                        mock.patch.object(th, "rr_stop_rejection", wraps=th.rr_stop_rejection) as spy:
                    live = gs._trade_hygiene_rejection(dict(sig), None, date(2026, 10, 9), {})
                    live_calls = list(spy.call_args_list)
                    spy.reset_mock()
                    rules = production_hygiene_rules()
                    rule, _, _ = hygiene_rejection(rules, dict(sig), "SNOW", pd.Timestamp("2026-10-09"), False,
                                                   self._events(), SessionCalendar(), 25, None)
                    self.assertEqual(live_calls, list(spy.call_args_list), (cap, floor))
                    self.assertEqual(bool(live_calls), cap or floor)
                    self.assertEqual(live is not None, rule is not None, (cap, floor))

    def test_harness_rules(self):
        from scripts.validate_backtest_pipeline import hygiene_rejection, fixed_days_blackout
        cal = SessionCalendar()
        d = pd.Timestamp("2026-10-09")
        sig = {"entry_price": 100.0, "stop_loss": 95.0, "target_1": 108.0, "atr_14": 2.5}
        ev = self._events()
        rule, _, _ = hygiene_rejection(("earnings_full_hold",), sig, "TEVA", d, False, ev, cal, 25, None)
        self.assertEqual(rule, "earnings_full_hold")
        rule, _, _ = hygiene_rejection(("earnings_full_hold",), sig, "TEVA", d, True, ev, cal, 25, None)
        self.assertIsNone(rule)                                                   # sector ETFs exempt
        rule, _, flags = hygiene_rejection(("earnings_full_hold",), sig, "NOSEC", d, False, ev, cal, 25, None)
        self.assertIsNone(rule)
        self.assertTrue(flags["earnings_unknown"])                                # unknown: issued, flagged
        rule, reason, _ = hygiene_rejection(("event_lock",), sig, "ACQ", d, False, ev, cal, 25, False)
        self.assertEqual(rule, "event_lock")
        self.assertIn("DEFM14A", reason)
        rule, _, flags = hygiene_rejection(("event_lock",), sig, "NOSEC", d, False, ev, cal, 25, False)
        self.assertIsNone(rule)
        self.assertTrue(flags["event_lock_unknown"])
        rule, _, _ = hygiene_rejection(("event_lock",), sig, "TEVA", d, False, ev, cal, 25, True)
        self.assertEqual(rule, "event_lock")                                      # price-pin backstop
        snow = {"entry_price": 100.0, "stop_loss": 85.8, "target_1": 109.7, "atr_14": 4.0}
        rule, _, _ = hygiene_rejection(("rr_floor_stop_cap",), snow, "SNOW", d, False, ev, cal, 25, None)
        self.assertEqual(rule, "rr_floor_stop_cap")
        self.assertEqual(hygiene_rejection(("rr_floor_stop_cap",), sig, "SNOW", d, False, ev, cal, 25, None)[0], None)
        # Live fixed-days blackout on SEC dates: TEVA's next report 2026-10-28 is 13 sessions away
        spy_idx = pd.DatetimeIndex(pd.bdate_range("2026-09-01", "2026-12-31"))
        ev["events"]["TEVA"].append(ev["events"]["TEVA"][0].__class__(date(2026, 10, 14), date(2026, 10, 14),
                                                                     datetime(2026, 10, 14, 7, 0)))
        self.assertIsNotNone(fixed_days_blackout("TEVA", "Trend Following", d, ev, spy_idx))   # 3 sessions <= 5
        self.assertIsNone(fixed_days_blackout("TEVA", "Sector Rotation", d, ev, spy_idx))

    def test_live_hook_switches(self):
        import jobs.generate_signals as gs
        from src import quant_config as qc
        sig = {"ticker": "SNOW", "strategy": "Trend Following", "entry_price": 100.0, "stop_loss": 85.8,
               "target_1": 109.7, "atr_14": 4.0}
        # All switches off: nothing is rejected
        with mock.patch.object(qc, "STOP_CAP_ENABLED", False), \
                mock.patch.object(qc, "RR_FLOOR_ENABLED", False), \
                mock.patch.object(qc, "EARNINGS_BLACKOUT_MODE", "fixed_days"), \
                mock.patch.object(qc, "EVENT_LOCK_ENABLED", False):
            self.assertIsNone(gs._trade_hygiene_rejection(dict(sig), None, date(2026, 10, 9), {}))
        with mock.patch.object(qc, "STOP_CAP_ENABLED", True):
            self.assertIn("Stop cap", gs._trade_hygiene_rejection(dict(sig), None, date(2026, 10, 9), {}))
        # Full hold with an estimated provider date inside the hold (TEVA-like)
        teva = {"ticker": "TEVA", "strategy": "Trend Following", "entry_price": 20.0, "stop_loss": 19.0,
                "target_1": 22.0, "atr_14": 0.5}
        calendar = {"TEVA": {"next_earnings_date": "2026-10-28", "last_earnings_date": "2026-07-29"}}
        with mock.patch.object(qc, "EARNINGS_BLACKOUT_MODE", "full_hold"):
            self.assertIn("Earnings inside the hold", gs._trade_hygiene_rejection(dict(teva), None, date(2026, 10, 9), calendar))
            # Unknown date and no SEC history: issued and flagged
            fake = mock.Mock()
            fake.filings.return_value = None
            with mock.patch.object(gs, "_sec_filings", return_value=fake):
                s = dict(teva, ticker="NODATA")
                self.assertIsNone(gs._trade_hygiene_rejection(s, None, date(2026, 10, 9), {}))
                self.assertTrue(s["earnings_unknown"])
        # Event lock from a DEFM14A filing
        fake = mock.Mock()
        fake.filings.return_value = [F("DEFM14A", "2026-09-01T17:00:00")]
        fake.rights_offering_days.return_value = {}
        fake.sec.cik_for.return_value = 123
        with mock.patch.object(qc, "EVENT_LOCK_ENABLED", True), mock.patch.object(gs, "_sec_filings", return_value=fake):
            self.assertIn("Event lock", gs._trade_hygiene_rejection(dict(teva), None, date(2026, 10, 9), {}))


if __name__ == "__main__":
    unittest.main()
