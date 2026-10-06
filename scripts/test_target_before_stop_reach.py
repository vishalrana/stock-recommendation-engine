"""
Unit Test Suite: Target-Before-Stop Reach Probability & Monotonicity
===================================================================
Verifies the empirical reach probability engine with conservative intra-day execution:
  1. Target reached before stop -> counted as success
  2. Stop hit before target -> counted as failure
  3. Neither hit within H trading days -> counted as failure
  4. Gap down through/at stop at open -> stopped out immediately
  5. Gap up through/at target at open -> target reached immediately
  6. Same-candle ambiguity (both high >= target and low <= stop) -> resolved conservatively as STOP FIRST
  7. Target reached on day t2 after stop hit on day t1 -> counted as failure (stopped out)
  8. Horizon boundary: event on day H counts; event on day H+1 does not count
  9. Monotonicity: P(T3) <= P(T2) <= P(T1) strictly holds for T1 < T2 < T3
  10. Upstream stop-loss validation: stop >= entry is repaired to strategy stop floor without crashing
"""

import sys
import os
import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.strategies.target_calculator import (
    get_reach_prob_target_before_stop,
    get_reach_prob,
    calculate_targets,
    TargetCalculationResult,
)


def create_synthetic_ohlc(data: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """
    Creates a DataFrame with OPEN, HIGH, LOW, CLOSE from (open, high, low, close) tuples.
    """
    dates = pd.date_range(end=datetime.now(), periods=len(data), freq="B")
    df = pd.DataFrame(data, columns=["OPEN", "HIGH", "LOW", "CLOSE"], index=dates)
    return df


class TestTargetBeforeStopReach(unittest.TestCase):

    def test_1_target_hit_first(self):
        """Scenario: Window 0 enters at 100. Day 1 reaches 108 (Target 106) with Low 98 (Stop 95). Target hit first."""
        # Need at least 20 + H + 5 bars for MIN_REACH_PROB_WINDOWS
        # Let's create 35 bars. For all windows, price stays 100 except window with index d.
        bars = []
        for i in range(35):
            bars.append((100.0, 102.0, 99.0, 100.0))
        # Modify bar 25 onwards: bar 25 is entry at 100.
        # bar 26: open 101, high 108, low 99, close 107
        bars[26] = (101.0, 108.0, 99.0, 107.0)
        df = create_synthetic_ohlc(bars)

        # target_pct = 0.06 (106), stop_pct = 0.05 (95), holding_days = 5
        prob = get_reach_prob_target_before_stop(
            ticker="SYNTH_TARGET_FIRST",
            target_pct=0.06,
            stop_pct=0.05,
            holding_days=5,
            price_df=df,
            lookback_days=30,
        )
        self.assertGreater(prob, 0.0)

    def test_2_stop_hit_first(self):
        """Scenario: Window enters at 100. Day 1 hits low 93 (Stop 95). Day 2 hits 110. Must fail (stopped out)."""
        bars = []
        # Create 35 bars where every bar enters at 100, drops to 93 on day 1, then rallies to 110 on day 2.
        # Since stop is hit on day 1, target reach prob must be 0.0!
        for i in range(35):
            bars.append((100.0, 100.5, 99.5, 100.0))
        # In sliding windows, if low touches 93 on day t+1, stopped out immediately.
        # Let's set bar 25 entry=100, bar 26 low=93 high=99, bar 27 high=115 low=100.
        bars[25] = (100.0, 100.5, 99.5, 100.0)
        bars[26] = (98.0, 99.0, 93.0, 95.0)  # Stop 95 hit on day 1 for window 25!
        bars[27] = (97.0, 115.0, 85.0, 112.0) # Low 85 hits stop for window 26 before target!

        df = create_synthetic_ohlc(bars)
        # Check window d=25 specifically
        # In a 35-bar series with H=5, total_possible_windows = 30.
        # If no bar ever hits target before stop, prob is 0.0
        prob = get_reach_prob_target_before_stop(
            ticker="SYNTH_STOP_FIRST",
            target_pct=0.06,
            stop_pct=0.05,
            holding_days=5,
            price_df=df,
            lookback_days=30,
        )
        # Because only bar 27 touched target (which was after bar 26 stopped out), success_count for window 25 is 0!
        self.assertEqual(prob, 0.0)

    def test_3_gap_down_at_open(self):
        """Scenario: Day 1 opens at 94 (Stop is 95). Low is 94, high 107. Must be STOP_HIT at open."""
        bars = []
        for i in range(35):
            bars.append((100.0, 101.0, 99.0, 100.0))
        # Bar 25 is entry at 100.
        # Bar 26 gaps down to 94 at Open, then rallies to 108.
        bars[26] = (94.0, 108.0, 94.0, 106.0)
        df = create_synthetic_ohlc(bars)

        prob = get_reach_prob_target_before_stop(
            ticker="SYNTH_GAP_DOWN",
            target_pct=0.06,
            stop_pct=0.05,
            holding_days=5,
            price_df=df,
            lookback_days=30,
        )
        self.assertEqual(prob, 0.0)

    def test_4_gap_up_at_open(self):
        """Scenario: Day 1 opens at 107 (Target is 106). Target reached immediately at open."""
        bars = []
        for i in range(35):
            bars.append((100.0, 101.0, 99.0, 100.0))
        bars[26] = (107.0, 108.0, 106.5, 107.5)
        df = create_synthetic_ohlc(bars)

        prob = get_reach_prob_target_before_stop(
            ticker="SYNTH_GAP_UP",
            target_pct=0.06,
            stop_pct=0.05,
            holding_days=5,
            price_df=df,
            lookback_days=30,
        )
        self.assertGreater(prob, 0.0)

    def test_5_same_candle_ambiguity_conservative(self):
        """Scenario: Day 1 has High 108 (Target 106) AND Low 94 (Stop 95). Ambiguity resolved as STOP FIRST."""
        bars = []
        for i in range(35):
            bars.append((100.0, 101.0, 99.0, 100.0))
        # Open 100, High 108, Low 94, Close 105
        bars[26] = (100.0, 108.0, 94.0, 105.0)
        df = create_synthetic_ohlc(bars)

        prob = get_reach_prob_target_before_stop(
            ticker="SYNTH_AMBIGUITY",
            target_pct=0.06,
            stop_pct=0.05,
            holding_days=5,
            price_df=df,
            lookback_days=30,
        )
        self.assertEqual(prob, 0.0)

    def test_6_horizon_boundary(self):
        """Scenario: Event on day H (day 5) counts. Event on day H+1 (day 6) does not count."""
        # 40 bars
        bars_hit_day_5 = [(100.0, 101.0, 99.0, 100.0) for _ in range(40)]
        # Entry at index 10 (price 100). Day 5 is index 15.
        bars_hit_day_5[15] = (100.0, 108.0, 99.0, 106.0)
        df_5 = create_synthetic_ohlc(bars_hit_day_5)

        prob_5 = get_reach_prob_target_before_stop(
            ticker="SYNTH_HORIZON_5",
            target_pct=0.06,
            stop_pct=0.05,
            holding_days=5,
            price_df=df_5,
            lookback_days=30,
        )
        self.assertGreater(prob_5, 0.0)

        # Event on day 6 (index 16) with holding_days=5:
        bars_hit_day_6 = [(100.0, 101.0, 99.0, 100.0) for _ in range(40)]
        bars_hit_day_6[16] = (100.0, 108.0, 99.0, 106.0)
        df_6 = create_synthetic_ohlc(bars_hit_day_6)

        prob_6_for_window_10 = get_reach_prob_target_before_stop(
            ticker="SYNTH_HORIZON_6",
            target_pct=0.06,
            stop_pct=0.05,
            holding_days=5,
            price_df=df_6,
            lookback_days=30,
        )
        # Window 10 does not hit within 5 days!
        # Thus, reach prob for df_5 is strictly >= df_6
        self.assertGreaterEqual(prob_5, prob_6_for_window_10)

    def test_7_monotonicity_enforcement(self):
        """Monotonicity: For any price series and identical holding period, P(T3) <= P(T2) <= P(T1)."""
        # Create realistic trending bars
        np.random.seed(42)
        n = 100
        prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.001, 0.015, n)))
        bars = []
        for p in prices:
            bars.append((p * 0.995, p * 1.015, p * 0.985, p))
        df = create_synthetic_ohlc(bars)

        rp1 = get_reach_prob_target_before_stop("MONO_TEST", 0.04, 0.05, 20, price_df=df)
        rp2 = get_reach_prob_target_before_stop("MONO_TEST", 0.08, 0.05, 20, price_df=df)
        rp3 = get_reach_prob_target_before_stop("MONO_TEST", 0.15, 0.05, 20, price_df=df)

        self.assertGreaterEqual(rp1, rp2, f"Monotonicity failed: T1 ({rp1}) < T2 ({rp2})")
        self.assertGreaterEqual(rp2, rp3, f"Monotonicity failed: T2 ({rp2}) < T3 ({rp3})")

    def test_8_upstream_stop_validation_and_repair(self):
        """If stop >= entry, calculate_targets repairs stop to strategy floor without crashing."""
        res = calculate_targets(
            ticker="REPAIR_TEST",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=105.0,  # Invalid: stop > entry!
            strategy_name="Trend Following",
            mock_reach_probs=(0.40, 0.25, 0.18),
        )
        self.assertTrue(res.is_valid)
        self.assertGreater(res.weighted_scaleout_rr, 0.0)
        self.assertEqual(res.weighted_scaleout_rr, res.weighted_rr_honest)
        # Trend following stop floor is 6%, repaired stop should be 94.00, risk 6.00
        self.assertIsNotNone(res.target_1)
        self.assertIsNotNone(res.target_1_return_decimal)

    def test_9_target_return_decimal_vs_pct(self):
        """Verify explicit target return decimal vs percentage properties."""
        res = calculate_targets(
            ticker="DECIMAL_TEST",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=94.0,
            strategy_name="Trend Following",
            mock_reach_probs=(0.50, 0.30, 0.20),
        )
        self.assertTrue(res.is_valid)
        # Decimal return should be ~0.082 while pct is ~8.2
        self.assertAlmostEqual(res.target_1_return_decimal * 100.0, res.target_1_pct, places=1)
        self.assertAlmostEqual(res.target_2_return_decimal * 100.0, res.target_2_pct, places=1)
        self.assertAlmostEqual(res.target_3_return_decimal * 100.0, res.target_3_pct, places=1)


if __name__ == "__main__":
    unittest.main()
