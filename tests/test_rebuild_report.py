"""Monthly rebuild drift report (scripts/time_price_refresh.drift_report)."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.time_price_refresh import drift_report


class TestDriftReport(unittest.TestCase):
    def test_lists_and_checks(self):
        d = pd.bdate_range("2025-01-01", periods=260)
        old = pd.DataFrame({"A": 100.0, "B": 100.0, "C": 100.0, "S": 100.0}, index=d)
        old.loc[d[:20], "C"] = np.nan                    # 240 live bars (close-less rows of old caches don't count)
        new = old.copy()
        new["A"] = 99.0                                  # re-adjusted: drift
        new["C"] = 100.0                                 # now a full year
        new.loc[d[:50], "S"] = np.nan                    # Yahoo serves fewer bars
        r = drift_report(old, new, ["A", "B", "C", "S"], d[-1].date())
        self.assertEqual((r["drifted"], r["newly_long"], r["shorter"]), (["A"], ["C"], ["S"]))
        self.assertEqual(r["session_coverage_pct"], 100.0)
        self.assertEqual(r["history_kept_pct"], 66.67)   # A, B kept a year; S lost it
        self.assertEqual(r["compared_session"], d[-1].date().isoformat())

    def test_coverage_is_measured_on_the_live_cache_last_session(self):
        d = pd.bdate_range("2025-01-01", periods=260)
        old = pd.DataFrame({"A": 100.0, "B": 100.0}, index=d[:-1])   # the live cache stops a session earlier
        new = pd.DataFrame({"A": 100.0, "B": 100.0}, index=d)
        new.loc[d[-2], "B"] = np.nan
        r = drift_report(old, new, ["A", "B"], d[-1].date())
        self.assertEqual(r["session_coverage_pct"], 50.0)


class TestPartialLastSession(unittest.TestCase):
    def _cache(self, d, last_names):
        from src.data.cache_manager import CacheManager
        cm = CacheManager(cache_dir=d)                         # first: it clears a directory without its version file
        days = pd.bdate_range("2026-10-19", periods=3)          # 10-19, 10-20, 10-21
        for day in days:
            names = last_names if day == days[-1] else [f"T{i}" for i in range(100)]
            idx = pd.MultiIndex.from_tuples([(t, day) for t in names], names=["Ticker", "Date"])
            pd.DataFrame({"CLOSE": 1.0}, index=idx).to_parquet(os.path.join(d, f"{day.date()}.parquet"))
        return cm.cached_closes("0000-00-00", "9999-99-99")

    def test_a_session_one_ticker_covers_is_dropped(self):
        import tempfile
        from scripts.time_price_refresh import trim_partial_last_session
        with tempfile.TemporaryDirectory() as d:
            cov, dropped = trim_partial_last_session(self._cache(d, ["T0"]), d)   # 2026-10-21 at 20:46 ET
            self.assertEqual((cov, dropped), (1.0, "2026-10-21"))
            self.assertFalse(os.path.exists(os.path.join(d, "2026-10-21.parquet")))
            self.assertTrue(os.path.exists(os.path.join(d, "2026-10-20.parquet")))

    def test_a_full_session_is_kept(self):
        import tempfile
        from scripts.time_price_refresh import trim_partial_last_session
        with tempfile.TemporaryDirectory() as d:
            cov, dropped = trim_partial_last_session(self._cache(d, [f"T{i}" for i in range(99)]), d)
            self.assertEqual((cov, dropped), (99.0, None))
            self.assertTrue(os.path.exists(os.path.join(d, "2026-10-21.parquet")))


class TestSecAudit(unittest.TestCase):
    def test_wrongly_parsed_documents_stand_out(self):
        import json
        import tempfile
        from scripts.time_price_refresh import sec_acceptance_audit
        with tempfile.TemporaryDirectory() as d:
            # 2026-10-23 05:00Z: header-correct times; 2026-10-25 13:00Z: 4 h early (07:00 -> 03:00)
            for name, ts, times in (("CIK0000000001", 1792731600, ["17:53:18", "08:00:00"]),
                                    ("CIK0000000002", 1792933200, ["03:00:00", "12:30:00"])):
                rows = [["8-K", f"2026-07-29T{t}", "2.02"] for t in times]
                json.dump({"fetched_at": ts, "recent_rows": rows, "pages": {}}, open(os.path.join(d, name + ".json"), "w"))
            audit = sec_acceptance_audit(d)
        self.assertEqual(audit["2026-10-23T05"]["share_before_06_et"], 0.0)
        self.assertEqual(audit["2026-10-25T13"]["share_before_06_et"], 0.5)


if __name__ == "__main__":
    unittest.main()
