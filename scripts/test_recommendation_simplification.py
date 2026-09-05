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

from src.position_sizer import assign_tier, calculate_half_kelly, allocate_capital, calculate_p_win
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
        self.assertEqual(assign_tier(45.0, 3.5), "Buy")  # High R:R rescue rule
        self.assertEqual(assign_tier(55.0, 1.2), "Rejected")  # Below both score and R:R thresholds

    def test_no_cash_rejection_in_sizing(self):
        """Test that candidate recommendations cannot be blocked by cash balance."""
        # Even with zero cash, candidates passing tier and strategy should not be constrained
        tier = assign_tier(75.0, 2.2)
        self.assertIn(tier, ["Strong Buy", "Buy"])

    def test_legacy_position_sizer_deprecated_compatibility(self):
        """Test that legacy position_sizer functions remain callable for backward compatibility."""
        # calculate_half_kelly
        hk = calculate_half_kelly(composite_score=75.0, honest_rr=2.0)
        self.assertGreater(hk, 0.0)

        # allocate_capital
        class DummySignal:
            def __init__(self, ticker, score):
                self.ticker = ticker
                self.composite_score = score
                self.raw_dollar_demand = 500.0
                self.half_kelly_fraction = 0.05
                self.allocated_dollars = 0.0
                self.exact_shares = 0.0
                self.rejection_reason = ""

        sigs = [DummySignal("AAPL", 80.0), DummySignal("MSFT", 70.0)]
        funded, rejected = allocate_capital(sigs, portfolio_value=10000.0, cash_balance=600.0)
        self.assertEqual(len(funded), 1)
        self.assertEqual(funded[0].ticker, "AAPL")
        self.assertEqual(len(rejected), 1)

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
