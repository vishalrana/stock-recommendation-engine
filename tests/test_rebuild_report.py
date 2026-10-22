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


if __name__ == "__main__":
    unittest.main()
