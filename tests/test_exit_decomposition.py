"""Fixed-horizon exit of scripts/research_exit_decomposition.py on a hand-checkable path."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.research_exit_decomposition import FixedHorizonEngine


def frame(closes, idx):
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"OPEN": c, "HIGH": c * 1.01, "LOW": c * 0.5, "CLOSE": c, "ATR_14": 1.0, "_LIQUID": True}, index=idx)


class TestFixedHorizon(unittest.TestCase):
    def test_exit_at_close_of_day_h_ignoring_stop(self):
        idx = pd.bdate_range("2024-01-01", periods=12)
        # Lows at half price would hit any stop: the fixed-horizon exit must ignore them
        a = frame([10, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], idx)
        spy = frame([100] * 12, idx)
        betas = {"AAA": pd.Series(1.0, index=idx)}
        eng = FixedHorizonEngine({"AAA": a}, spy, betas, lambda s: 20, cost_pct_per_side=0.10)
        eng.horizon = 5
        ret, reason = eng._simulate("AAA", idx[0], 3.0, [5.0, None, None], 20)
        # fill = open of bar 1 (10); day 5 = bar 5, close 14; SPY flat; costs 2 x 0.10
        self.assertAlmostEqual(ret, (14 / 10 - 1) * 100 - 0.20, places=6)
        self.assertEqual(reason, "expiry")
        eng.horizon = 20
        self.assertIsNone(eng._simulate("AAA", idx[0], 3.0, [5.0, None, None], 20))   # censored, not forced
        eng.horizon = None                          # live rules: stop at 3 x ATR below the close is hit
        ret_live, reason_live = eng._simulate("AAA", idx[0], 3.0, [50.0, None, None], 20)
        self.assertEqual(reason_live, "stop")


if __name__ == "__main__":
    unittest.main()
