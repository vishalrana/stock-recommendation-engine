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
            "Earnings date unavailable: SEC filings could not be read (SEC fetch failed)": "earnings_sec_unavailable",
            "Event lock: DEFM14A filed (since 2026-09-01)": "event_lock",
            "WAIT: Approaching resistance": "entry_location",
            "Strategy deduplication: preferred 'Trend Following' (Score 70.0 vs 66.0)": "dedupe",
        }
        for reason, stage in cases.items():
            self.assertEqual(live_stage(reason), stage, reason)
        self.assertEqual(live_stage(float("nan")), "issued")          # pandas 3.1: a missing string is NaN
        self.assertEqual(live_stage("  "), "issued")

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


    def test_load_live_issued_whatever_the_outcome(self):
        from unittest import mock
        from scripts.parity_replay import load_live
        rows = [{"ticker": "OMC", "strategy": "Trend Following", "composite_score": 70.0, "rejection_reason": float("nan"),
                 "outcome": "stopped", "created_at": "t1"},
                {"ticker": "XYZ", "strategy": "Trend Following", "composite_score": 50.0,
                 "rejection_reason": "Composite score 50.0 below Buy threshold (65.0)", "outcome": "rejected", "created_at": "t1"}]
        client = mock.MagicMock()
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = rows
        client.table.return_value.select.return_value.in_.return_value.lt.return_value.execute.return_value.data = []
        live, _ = load_live(client, "2026-10-23")
        self.assertEqual(dict(zip(live.ticker, live.stage)), {"OMC": "issued", "XYZ": "score_below_65"})

    def test_weekly_replay_picks_the_latest_successful_session(self):
        from datetime import date
        from unittest import mock
        from scripts.parity_replay import latest_successful_session
        def client_with(rows):
            c = mock.MagicMock()
            c.table.return_value.select.return_value.gte.return_value.lte.return_value.execute.return_value.data = rows
            return c
        week = [{"scan_date": "2026-10-19", "status": "success"}, {"scan_date": "2026-10-21", "status": "stale_data"},
                {"scan_date": "2026-10-22", "status": "success"}, {"scan_date": "2026-10-23", "status": "stale_data"}]
        self.assertEqual(latest_successful_session(client_with(week), 7, today=date(2026, 10, 23)), "2026-10-22")
        self.assertIsNone(latest_successful_session(client_with(week[1::2]), 7, today=date(2026, 10, 23)))

    def test_workflow_steps_fail_on_a_crash_inside_a_pipe(self):
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        wf = yaml.safe_load(open(os.path.join(root, ".github", "workflows", "parity_replay.yml"), encoding="utf-8"))
        for step in wf["jobs"]["replay"]["steps"]:
            if "parity_replay.py" in (step.get("run") or ""):
                # GitHub's default (bash -e) has no pipefail: $(crash | tail -1) would succeed with ""
                self.assertEqual(step.get("shell"), "bash", step.get("name"))
                self.assertNotIn("if", step, step.get("name"))          # no silent skip of the replay

    def test_class_a_label_is_information_only(self):
        live = pd.DataFrame([("AAA", "Trend Following", 70.0, "issued")], columns=["ticker", "strategy", "composite_score", "stage"])
        harness = pd.DataFrame([("AAA", "Trend Following", 70.0, "issued"),
                                ("PEND", "Trend Following", 68.0, "rr_floor_stop_cap"),
                                ("OTHER", "Trend Following", 66.0, "rr_floor_stop_cap")],
                               columns=["ticker", "strategy", "composite_score", "stage"])
        ex = {"session": "2026-10-26", "pending": ["PEND"], "split_flagged": [], "stale": []}
        res = compare(live, harness, [], ex)
        by = {d["candidate"]: d for d in res["material_differences"]}
        self.assertEqual(by["PEND / Trend Following"]["class"], "(a) failed closed live: pending")
        self.assertEqual(by["OTHER / Trend Following"]["class"], "unclassified")
        self.assertFalse(res["passed"])                                  # pass/fail unchanged by the class

    def _main(self, live_rows, harness_rows):
        """Exit code of parity_replay.main() for these decisions, and the step summary it wrote."""
        import tempfile
        from unittest import mock
        import scripts.parity_replay as pr
        cols = ["ticker", "strategy", "composite_score", "stage"]
        with tempfile.TemporaryDirectory() as d:
            csv, out, summ = (os.path.join(d, x) for x in ("h.csv", "o.json", "summary.md"))
            pd.DataFrame(harness_rows, columns=cols).to_csv(csv, index=False)
            with mock.patch.object(pr, "load_live", return_value=(pd.DataFrame(live_rows, columns=cols), [])),                     mock.patch("jobs.supabase_client.get_client"),                     mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summ}):
                code = pr.main(["--date", "2026-10-20", "--harness-csv", csv, "--out", out])
            return code, open(summ, encoding="utf-8").read()

    def test_exit_code_fails_on_material_differences(self):
        same = [("AAA", "Trend Following", 70.0, "issued"), ("BBB", "Trend Following", 60.0, "score_below_65")]
        code, summary = self._main(same, same)
        self.assertEqual(code, 0)
        self.assertIn("PASSED", summary)
        # different issued sets
        code, summary = self._main(same, [("AAA", "Trend Following", 70.0, "rr_floor_stop_cap")] + same[1:])
        self.assertEqual(code, 1)
        self.assertIn("FAILED", summary)
        self.assertIn("| AAA / Trend Following | issued | rr_floor_stop_cap |", summary)
        # a 65+ candidate with a different stage, neither side issuing it
        code, _ = self._main(same + [("CCC", "Trend Following", 66.0, "earnings_full_hold")],
                             same + [("CCC", "Trend Following", 66.0, "rr_floor_stop_cap")])
        self.assertEqual(code, 1)
        code, _ = self._main(same, same + [("DDD", "Cross-Sectional Momentum", 68.9, "earnings_full_hold")])
        self.assertEqual(code, 1)                                         # harness-only at 65+
        # differences below 65 only: green, but still listed in the summary
        code, summary = self._main(same, same + [("EEE", "Trend Following", 62.9, "entry_location"),
                                                  ("BBB", "Pullback Recovery", 51.0, "entry_location")])
        self.assertEqual(code, 0)
        self.assertIn("EEE / Trend Following", summary)


if __name__ == "__main__":
    unittest.main()
