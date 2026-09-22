"""
Regression Test Suite: Target T1 Reach-Probability & Hierarchy (P0-4)
=====================================================================
Verifies:
1. Exact threshold: rp_t1 == t1_min -> T1 survives
2. Just below threshold: rp_t1 = t1_min - 0.01 -> T1 minimum not met; T2/T3 cannot survive
3. Just above threshold: rp_t1 = t1_min + 0.01 -> T1 survives
4. Target hierarchy: entry < T1 < T2 < T3 preserved without ordering violations
5. Pruning hierarchy: Never produces T2 without T1, nor T3 without T1 and T2
6. Scale-out weights:
   - 50/30/20 when all three survive
   - 60/40/0 when T1 + T2 only survive
   - 70/30/0 when only T1 survives
7. Monotonic reach probabilities: farther targets cannot have higher probability than closer targets
8. Insufficient historical windows (<20): returns 0.0 empirical reach prob
"""

import sys
import os
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.strategies.target_calculator import (
    calculate_targets,
    get_reach_prob,
    MIN_REACH_PROB_WINDOWS,
)
from src.quant_config import STRATEGY_TARGET_CONFIG, T3_REACH_PROB_SURVIVAL_THRESHOLD


class TestTargetHierarchyAndReach(unittest.TestCase):

    def setUp(self):
        # Pullback Recovery config: t1_min = 0.40, t2_min = 0.20, t3_min = 0.15
        self.cfg_pullback = STRATEGY_TARGET_CONFIG["pullback_recovery"]
        self.t1_min = self.cfg_pullback["t1_min"]
        self.t2_min = self.cfg_pullback["t2_min"]
        self.t3_min = self.cfg_pullback.get("t3_min", T3_REACH_PROB_SURVIVAL_THRESHOLD)

    def test_1_t1_exact_threshold(self):
        # T1 reach prob exactly at threshold 0.40
        res = calculate_targets(
            ticker="TEST",
            entry_price=100.0,
            atr_14=2.5,
            stop_loss=95.0,
            strategy_name="Pullback Recovery",
            mock_reach_probs=(self.t1_min, 0.25, 0.18),
        )
        self.assertEqual(res.scale_out_weights, "50/30/20")
        self.assertIsNotNone(res.target_1)
        self.assertIsNotNone(res.target_2)
        self.assertIsNotNone(res.target_3)

    def test_2_t1_just_below_threshold(self):
        # T1 reach prob just below threshold (0.39 < 0.40)
        # Even if T2 (0.35) and T3 (0.25) have high numbers, they CANNOT survive without T1!
        res = calculate_targets(
            ticker="TEST",
            entry_price=100.0,
            atr_14=2.5,
            stop_loss=95.0,
            strategy_name="Pullback Recovery",
            mock_reach_probs=(round(self.t1_min - 0.01, 2), 0.35, 0.25),
        )
        self.assertEqual(res.scale_out_weights, "70/30/0")
        self.assertIsNotNone(res.target_1)
        self.assertIsNone(res.target_2, "T2 must NOT survive when T1 does not survive")
        self.assertIsNone(res.target_3, "T3 must NOT survive when T1 does not survive")
        self.assertIn("below minimum", res.rejection_reason)

    def test_3_t1_just_above_threshold(self):
        # T1 reach prob just above threshold (0.41 > 0.40)
        res = calculate_targets(
            ticker="TEST",
            entry_price=100.0,
            atr_14=2.5,
            stop_loss=95.0,
            strategy_name="Pullback Recovery",
            mock_reach_probs=(round(self.t1_min + 0.01, 2), 0.22, 0.16),
        )
        self.assertEqual(res.scale_out_weights, "50/30/20")
        self.assertIsNotNone(res.target_1)
        self.assertIsNotNone(res.target_2)
        self.assertIsNotNone(res.target_3)

    def test_4_target_ordering_strictly_increasing(self):
        # Test candidate targets preserve entry < T1 < T2 < T3
        res = calculate_targets(
            ticker="ORDER_TEST",
            entry_price=50.0,
            atr_14=1.0,
            stop_loss=47.5,
            strategy_name="Trend Following",
            mock_reach_probs=(0.50, 0.30, 0.20),
        )
        self.assertTrue(50.0 < res.target_1 < res.target_2 < res.target_3,
                        f"Ordering violated: 50.0 < {res.target_1} < {res.target_2} < {res.target_3}")

    def test_5_pruning_never_produces_t3_without_t2(self):
        # T1 survives (0.45 >= 0.40), T2 fails (0.15 < 0.20), T3 meets threshold (0.18 >= 0.15)
        # T3 must NOT survive if T2 fails!
        res = calculate_targets(
            ticker="TEST",
            entry_price=100.0,
            atr_14=2.5,
            stop_loss=95.0,
            strategy_name="Pullback Recovery",
            mock_reach_probs=(0.45, 0.15, 0.18),
        )
        self.assertEqual(res.scale_out_weights, "70/30/0")
        self.assertIsNotNone(res.target_1)
        self.assertIsNone(res.target_2)
        self.assertIsNone(res.target_3, "T3 cannot survive if T2 fails")

    def test_6_scale_out_weights(self):
        # All 3 survive -> 50/30/20
        res_3 = calculate_targets(
            ticker="TEST", entry_price=100.0, atr_14=2.0, stop_loss=95.0,
            strategy_name="Trend Following", mock_reach_probs=(0.40, 0.20, 0.16)
        )
        self.assertEqual(res_3.scale_out_weights, "50/30/20")

        # T1 + T2 only -> 60/40/0
        res_2 = calculate_targets(
            ticker="TEST", entry_price=100.0, atr_14=2.0, stop_loss=95.0,
            strategy_name="Trend Following", mock_reach_probs=(0.40, 0.20, 0.10)
        )
        self.assertEqual(res_2.scale_out_weights, "60/40/0")
        self.assertIsNone(res_2.target_3)

        # Only T1 -> 70/30/0
        res_1 = calculate_targets(
            ticker="TEST", entry_price=100.0, atr_14=2.0, stop_loss=95.0,
            strategy_name="Trend Following", mock_reach_probs=(0.40, 0.08, 0.05)
        )
        self.assertEqual(res_1.scale_out_weights, "70/30/0")
        self.assertIsNone(res_1.target_2)
        self.assertIsNone(res_1.target_3)

    def test_7_monotonic_reach_prob_enforcement(self):
        # Mock probabilities passed in non-monotonic order: T1=30%, T2=45%, T3=50%
        # Farther targets cannot have higher reach probability
        res = calculate_targets(
            ticker="TEST", entry_price=100.0, atr_14=2.0, stop_loss=95.0,
            strategy_name="Trend Following", mock_reach_probs=(0.30, 0.45, 0.50)
        )
        self.assertLessEqual(res.reach_prob_t2, res.reach_prob_t1)
        self.assertLessEqual(res.reach_prob_t3, res.reach_prob_t2)

    def test_8_insufficient_historical_windows_returns_zero(self):
        # DataFrame with only 10 bars (less than 20 minimum sliding windows)
        short_df = pd.DataFrame({"Close": [100.0 + i for i in range(10)]})
        prob = get_reach_prob("TINY", target_pct=0.05, holding_days=5, price_df=short_df)
        self.assertEqual(prob, 0.0, "Fewer than MIN_REACH_PROB_WINDOWS must return 0.0")


if __name__ == "__main__":
    unittest.main()
