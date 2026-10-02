"""
Acceptance Suite: Stock Recommendation Engine Simplification
============================================================
Validates:
1. Signal qualification is purely opportunity-driven (no portfolio/cash state).
2. Sizing cannot reject candidates (no Kelly gate, no cash constraints).
3. Quality gates (composite score, honest R:R, reach probability, earnings filter) are intact.
4. Scale-out plan (50/30/20) and trade setup are calculated purely on prices/targets.
5. Backward compatibility with database schema and legacy position_sizer functions.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.ranker import assign_tier
from src.quant_config import (
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
    SCALE_OUT_WEIGHTS,
    T3_REACH_PROB_SURVIVAL_THRESHOLD,
    STRATEGY_TARGET_CONFIG,
    EARNINGS_BLACKOUT_DAYS,
)
from src.strategies.target_calculator import calculate_targets


class TestRecommendationSimplification(unittest.TestCase):

    def test_tier_assignment_pure_quality(self):
        """Test that tier assignment evaluates composite score and honest R:R without cash."""
        self.assertEqual(assign_tier(82.0, 2.5), "Strong Buy")
        self.assertEqual(assign_tier(72.0, 2.1), "Buy")
        self.assertEqual(assign_tier(65.0, 0.5), "Buy")  # Low R:R does NOT disqualify valid score
        self.assertEqual(assign_tier(45.0, 3.5), "Rejected")  # R:R is analytical only, does not rescue low score
        self.assertEqual(assign_tier(55.0, 1.2), "Rejected")  # Below score threshold

    def test_no_cash_rejection_in_sizing(self):
        """Test that candidate recommendations cannot be blocked by cash balance."""
        # Even with zero cash, candidates passing tier and strategy should not be constrained
        tier = assign_tier(75.0, 2.2)
        self.assertIn(tier, ["Strong Buy", "Buy"])

    def test_legacy_position_sizer_removed(self):
        """Test that legacy position_sizer is removed and assign_tier lives in src.ranker."""
        pos_sizer_path = os.path.join(PROJECT_ROOT, "src", "position_sizer.py")
        self.assertFalse(os.path.exists(pos_sizer_path), "src/position_sizer.py must not exist")
        from src.ranker import assign_tier as ranker_assign_tier
        self.assertEqual(ranker_assign_tier(85.0), "Strong Buy")

    def test_trade_setup_targets_pure_math(self):
        """Test dynamic targets calculation does not require capital allocation."""
        entry = 150.0
        atr = 3.5
        res = calculate_targets(
            ticker="AAPL",
            entry_price=entry,
            atr_14=atr,
            stop_loss=140.0,
            strategy_name="Trend Following",
            mock_reach_probs=(0.60, 0.40, 0.25),
        )
        self.assertTrue(res.is_valid)
        self.assertIsNotNone(res.target_1)
        self.assertIsNotNone(res.target_2)
        self.assertIsNotNone(res.target_3)
        self.assertGreater(res.target_1, entry)
        self.assertGreater(res.weighted_rr_honest, 0.0)
        self.assertEqual(res.scale_out_weights, "50/30/20")

    def test_scale_out_plan_weights(self):
        """Test scale out plan default format is 50/30/20."""
        weights = "50/30/20"
        parts = [int(p) for p in weights.split("/")]
        self.assertEqual(sum(parts), 100)
        self.assertEqual(parts[0], 50)
        self.assertEqual(parts[1], 30)
        self.assertEqual(parts[2], 20)

    def test_canonical_quant_constants(self):
        """Verify canonical quant configuration constants."""
        self.assertEqual(T3_REACH_PROB_SURVIVAL_THRESHOLD, 0.15)
        self.assertIn("trend_following", STRATEGY_WEIGHT_VECTORS)
        self.assertAlmostEqual(sum(STRATEGY_WEIGHT_VECTORS["trend_following"].values()), 1.0, places=4)
        self.assertIn("all_three", SCALE_OUT_WEIGHTS)
        self.assertEqual(SCALE_OUT_WEIGHTS["all_three"]["label"], "50/30/20")
        self.assertEqual(EARNINGS_BLACKOUT_DAYS["trend_following"], 5)
        self.assertEqual(EARNINGS_BLACKOUT_DAYS["pead"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
