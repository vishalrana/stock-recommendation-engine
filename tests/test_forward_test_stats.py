"""Forward-test review statistics (src/forward_test_stats.py): M1, M2 and the STOP threshold."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.forward_test_stats import m1, m2, stop_threshold

BACKTEST = os.path.join(ROOT, "data", "forward_test", "backtest_40205392302_trades.csv")


class TestM1M2(unittest.TestCase):
    def test_counts_only_below_20_closed(self):
        out = m1([0.5] * 19, ["2026-11"] * 19)
        self.assertEqual(out["closed"], 19)
        self.assertNotIn("mean_pct", out)

    def test_mean_and_month_block_ci(self):
        vals = [1.0] * 10 + [-1.0] * 10 + [0.0] * 10
        months = ["2026-11"] * 10 + ["2026-12"] * 10 + ["2027-01"] * 10
        out = m1(vals, months)
        self.assertEqual(out["mean_pct"], 0.0)
        self.assertLessEqual(out["ci95"][0], 0.0)
        self.assertGreaterEqual(out["ci95"][1], 0.0)

    def test_m2_adds_open_marks(self):
        self.assertEqual(m2([1.0, -2.0], [0.5]), -0.5)


class TestStopThreshold(unittest.TestCase):
    def test_paths_are_whole_months_cut_at_n(self):
        months = ["A"] * 10 + ["B"] * 10
        vals = [-1.0] * 10 + [1.0] * 10
        # one month covers n = 10: every path is all -1 or all +1, so the 1st percentile is -10
        self.assertEqual(stop_threshold(vals, months, 10), -10.0)
        self.assertEqual(stop_threshold([2.0] * 6, ["A", "A", "B", "B", "C", "C"], 5), 10.0)
        self.assertIsNone(stop_threshold(vals, months, 0))

    def test_on_run_40205392302_ideas(self):
        t = pd.read_csv(BACKTEST)
        self.assertEqual(len(t), 1631)                          # the copied issued ideas with returns
        part = t.iloc[:400]
        p1 = stop_threshold(part.beta_adjusted_pct, part.signal_date.str[:7], 50)
        self.assertEqual(p1, stop_threshold(part.beta_adjusted_pct, part.signal_date.str[:7], 50))   # fixed seed
        self.assertTrue(np.isfinite(p1))
        self.assertLess(p1, 50 * part.beta_adjusted_pct.mean())  # a 1st percentile lies below the mean path


if __name__ == "__main__":
    unittest.main()
