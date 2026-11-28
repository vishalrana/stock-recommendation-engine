"""Forward-test review (scripts/forward_test_review.py): due dates, decision rules, COMBINED, outcomes, dry run."""

import os
import sys
import unittest
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.forward_test_review as ftr  # noqa: E402
from src.forward_test_stats import m1  # noqa: E402


def frame(closes, start="2026-11-02"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"OPEN": c, "HIGH": c * 1.01, "LOW": c * 0.99, "CLOSE": c, "ATR_14": c * 0.02}, index=idx)


def idea(ticker, sd, entry, **kw):
    return dict({"ticker": ticker, "strategy": "Trend Following", "signal_date": sd, "entry": entry,
                 "stop": entry * 0.95, "t1": entry * 1.05, "t2": None, "t3": None}, **kw)


class TestSchedule(unittest.TestCase):
    def test_review_dates_come_from_the_doc(self):
        self.assertEqual(ftr.reviews(), [("R1", date(2027, 1, 16)), ("R2", date(2027, 4, 17)),
                                         ("R3", date(2027, 10, 16)), ("R4", date(2028, 10, 14))])

    def test_due_is_the_oldest_unwritten_review(self):
        self.assertIsNone(ftr.due(date(2027, 1, 15), []))
        self.assertEqual(ftr.due(date(2027, 1, 16), []), ("R1", date(2027, 1, 16)))
        self.assertEqual(ftr.due(date(2027, 5, 1), ["2027-01-16"]), ("R2", date(2027, 4, 17)))
        self.assertIsNone(ftr.due(date(2027, 5, 1), ["2027-01-16", "2027-04-17"]))


class TestDecision(unittest.TestCase):
    def combined(self, m2, ci):
        return {"M2": m2, "M1": {"closed": 40, "mean_pct": 0.0, "ci95": ci}}

    def test_stop_at_any_review(self):
        d = ftr.decide("R1", self.combined(-300.0, [-1.0, 1.0]), -272.7)
        self.assertEqual(d["state"], "STOP")
        s = ftr.status_doc("R1", date(2027, 1, 16), d, "123")
        self.assertEqual(s["message"], "Forward test stopped by its pre-registered rule on 2027-01-16")

    def test_end_only_at_r3_and_r4(self):
        below = self.combined(-10.0, [-2.0, -0.5])
        self.assertEqual(ftr.decide("R2", below, -272.7)["state"], "CONTINUE")
        self.assertEqual(ftr.decide("R3", below, -272.7)["state"], "END")
        self.assertIsNotNone(ftr.status_doc("R3", date(2027, 10, 16), ftr.decide("R3", below, -272.7), "1"))

    def test_ci_including_zero_and_above_zero(self):
        across = self.combined(5.0, [-1.0, 1.0])
        self.assertEqual(ftr.decide("R3", across, -272.7)["state"], "CONTINUE")
        self.assertEqual(ftr.decide("R4", across, -272.7)["state"], "USER_DECIDES")
        above = ftr.decide("R3", self.combined(50.0, [0.2, 1.0]), -272.7)
        self.assertEqual(above["state"], "CONTINUE")
        self.assertIn("live better than backtest", above["text"].lower())
        self.assertIsNone(ftr.status_doc("R3", date(2027, 10, 16), above, "1"))   # no banner without STOP or END

    def test_counts_only_never_ends(self):
        self.assertEqual(ftr.decide("R4", {"M2": 0.0, "M1": {"closed": 5, "note": "x"}}, -10.0)["state"], "CONTINUE")


class TestCombined(unittest.TestCase):
    def test_later_idea_dropped_while_the_other_set_holds_the_ticker(self):
        live = [dict(idea("AAA", "2026-11-02", 10), out={"status": "open"})]
        replay = [dict(idea("AAA", "2026-11-04", 10), out={"status": "closed", "exit_date": "2026-11-10"}),
                  dict(idea("BBB", "2026-11-04", 10), out={"status": "closed", "exit_date": "2026-11-10"})]
        kept, dropped = ftr.combine(live, replay)
        self.assertEqual(dropped, 1)
        self.assertEqual(sorted((k["ticker"], k["src"]) for k in kept), [("AAA", "LIVE"), ("BBB", "REPLAY")])

    def test_exit_day_still_blocks_like_the_harness(self):
        live = [dict(idea("AAA", "2026-11-02", 10), out={"status": "closed", "exit_date": "2026-11-04"})]
        self.assertEqual(ftr.combine(live, [dict(idea("AAA", "2026-11-04", 10), out={"status": "open"})])[1], 1)
        self.assertEqual(ftr.combine(live, [dict(idea("AAA", "2026-11-05", 10), out={"status": "open"})])[1], 0)


class TestOutcome(unittest.TestCase):
    def setUp(self):
        self.spy = frame([100.0] * 30)

    def test_target_and_costs(self):
        f = frame([10.0, 10.0, 10.0, 10.6] + [10.6] * 26)
        o = ftr.outcome(idea("AAA", "2026-11-02", 10.0), f, self.spy, 1.0, date(2026, 12, 31))
        self.assertEqual(o["status"], "closed")
        # the bar gaps above the 10.5 target: filled at its 10.6 open, less both sides' cost; SPY flat
        self.assertAlmostEqual(o["beta_adjusted_pct"], 6.0 - 0.2, places=4)

    def test_levels_rebased_after_a_split(self):
        # stored at the pre-split price 20; the bars were re-adjusted to 10: no false stop on the first bar
        f = frame([10.0] * 30)
        o = ftr.outcome(idea("AAA", "2026-11-02", 20.0), f, self.spy, 1.0, date(2026, 11, 20))
        self.assertEqual(o["status"], "open")
        self.assertAlmostEqual(o["rebased_stop"], 9.5)

    def test_unfilled_without_a_next_bar(self):
        f = frame([10.0] * 3)
        self.assertEqual(ftr.outcome(idea("AAA", "2026-11-04", 10.0), f, self.spy, 1.0, date(2026, 12, 31))["status"],
                         "unfilled")


class TestEvidenceAndDryRun(unittest.TestCase):
    def test_m1_reproduces_run_40205392302(self):
        t = pd.read_csv(ftr.BACKTEST).dropna(subset=["beta_adjusted_pct"])
        out = m1(t.beta_adjusted_pct, t.signal_date.str[:7])
        self.assertEqual(out["closed"], 1631)
        self.assertEqual(round(out["mean_pct"], 2), -0.79)                      # CLAUDE.md: -0.79%/idea
        self.assertEqual([round(x, 2) for x in out["ci95"]], [-1.39, -0.22])    # CI -1.39 to -0.22

    def test_dry_run_uses_fixtures_only(self):
        text = ftr.dry_run(date(2027, 1, 16))
        self.assertIn("DRY RUN on fixtures", text)
        self.assertIn("| Ideas | 45 |", text)
        self.assertIn("## Swing sleeves (report only)", text)


if __name__ == "__main__":
    unittest.main()
