"""Parity replay comparison (scripts/parity_replay.py): stage mapping, issued sets, causes."""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.parity_replay import compare, live_stage


class TestParityReplay(unittest.TestCase):
    def test_live_reasons_map_to_harness_stages(self):
        cases = {
            None: "issued",
            "Composite score 51.3 below Buy threshold (65.0)": "score_below_65",
            "Stop cap / R:R floor: Stop 8.4% below entry exceeds the cap of 6.8%": "rr_floor_stop_cap",
            "Earnings inside the hold: Estimated earnings 2026-10-28 (+/-5 sessions) overlaps": "earnings_full_hold",
            "Earnings in 2 trading days (estimated 2026-10-21; blackout 5)": "earnings_blackout",
            "Earnings date unknown (no SEC 8-K item 2.02 report in the last 200 days)": "earnings_unknown",
            "Event lock: DEFM14A filed (since 2026-09-01)": "event_lock",
            "WAIT: Approaching resistance": "entry_location",
            "Strategy deduplication: preferred 'Trend Following' (Score 70.0 vs 66.0)": "dedupe",
        }
        for reason, stage in cases.items():
            self.assertEqual(live_stage(reason), stage, reason)

    def test_differences_have_causes(self):
        live = pd.DataFrame([
            ("AAA", "Trend Following", 70.0, "issued"),
            ("BBB", "Trend Following", 66.0, "rr_floor_stop_cap"),
            ("CCC", "Trend Following", 64.0, "score_below_65"),
            ("DDD", "Trend Following", 68.0, "earnings_full_hold"),
            ("EEE", "Pullback Recovery", 40.0, "score_below_65"),     # harness logged nothing: both reject
        ], columns=["ticker", "strategy", "composite_score", "stage"])
        harness = pd.DataFrame([
            ("AAA", "Trend Following", 70.0, "issued"),
            ("BBB", "Trend Following", 66.0, "rr_floor_stop_cap"),
            ("CCC", "Trend Following", 65.4, "rr_floor_stop_cap"),    # score crosses 65 in the harness
            ("DDD", "Trend Following", 68.0, "issued"),               # same score, a rule read other inputs
            ("OBE", "Cross-Sectional Momentum", 67.0, "already_active"),   # open live: not logged there
            ("FFF", "Trend Following", 66.0, "earnings_unknown"),     # harness-only candidate
        ], columns=["ticker", "strategy", "composite_score", "stage"])
        res = compare(live, harness, live_open=["OBE"])
        self.assertEqual(res["issued_live"], ["AAA / Trend Following"])
        self.assertEqual(res["issued_harness"], ["AAA / Trend Following", "DDD / Trend Following"])
        self.assertFalse(res["issued_sets_equal"])
        by = {d["candidate"]: d for d in res["differences"]}
        self.assertEqual(set(by), {"CCC / Trend Following", "DDD / Trend Following", "FFF / Trend Following"})
        self.assertIn("score differs by -1.40", by["CCC / Trend Following"]["cause"])
        self.assertIn("same score, different stage", by["DDD / Trend Following"]["cause"])
        self.assertIn("harness only", by["FFF / Trend Following"]["cause"])
        self.assertEqual(res["unexplained"], [])


if __name__ == "__main__":
    unittest.main()
