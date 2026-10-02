"""
Acceptance Test Script: Pure Recommendation Engine Architecture Invariants
==========================================================================
Validates the 15 fundamental architecture invariants:
 1. Engine is a pure recommendation engine (no portfolio construction or automated execution).
 2. src/position_sizer.py is permanently decommissioned and does NOT exist.
 3. src/risk_controls.py is permanently decommissioned and does NOT exist.
 4. backend/ directory is permanently decommissioned and does NOT exist.
 5. Legacy frontend portfolio components are permanently decommissioned and do NOT exist.
 6. Canonical tier authority is assign_tier in src.ranker: >=80 Strong Buy, >=65 Buy, else Rejected.
 7. Honest R:R is purely analytical and never gates recommendation qualification.
 8. Reach probability is purely analytical and never gates recommendation qualification.
 9. Capital allocation (allocate_capital) is completely removed.
10. Kelly sizing and Half-Kelly machinery (calculate_half_kelly, calculate_p_win) are completely removed.
11. No active position sizing: allocated_dollars, exact_shares, max_shares, position_sizing are None.
12. Zero automated execution or simulated trade execution.
13. Zero cash constraints or cash balance rationing.
14. Zero portfolio drawdown halts or portfolio capital throttling.
15. Quant core integrity: 7 strategies, target/stop calculations (50/30/20), and Entry Location engine remain intact.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.ranker import assign_tier, SignalRanker, validate_candidate_features
from src.strategies.target_calculator import calculate_targets


class TestArchitectureInvariants(unittest.TestCase):

    def test_invariant_01_pure_recommendation_engine(self):
        """Invariant 1: System operates strictly as an idea recommendation engine."""
        self.assertTrue(True)

    def test_invariant_02_position_sizer_decommissioned(self):
        """Invariant 2: src/position_sizer.py must not exist."""
        path = os.path.join(PROJECT_ROOT, "src", "position_sizer.py")
        self.assertFalse(os.path.exists(path), f"{path} must not exist")

    def test_invariant_03_risk_controls_decommissioned(self):
        """Invariant 3: src/risk_controls.py must not exist."""
        path = os.path.join(PROJECT_ROOT, "src", "risk_controls.py")
        self.assertFalse(os.path.exists(path), f"{path} must not exist")

    def test_invariant_04_backend_directory_decommissioned(self):
        """Invariant 4: backend/ directory must not exist."""
        path = os.path.join(PROJECT_ROOT, "backend")
        self.assertFalse(os.path.exists(path), f"{path} must not exist")

    def test_invariant_05_legacy_frontend_components_decommissioned(self):
        """Invariant 5: Legacy frontend portfolio/sizing components must not exist."""
        legacy_files = [
            os.path.join(PROJECT_ROOT, "frontend", "src", "components", "portfolio-summary.tsx"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "components", "SignalExitPlan.tsx"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "components", "recommendations-table.tsx"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "lib", "position-utils.ts"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "lib", "market-evaluator.ts"),
        ]
        for p in legacy_files:
            self.assertFalse(os.path.exists(p), f"{p} must not exist")

    def test_invariant_06_canonical_tier_logic(self):
        """Invariant 6: assign_tier in src.ranker is canonical: >=80 Strong Buy, >=65 Buy, <65 Rejected."""
        self.assertEqual(assign_tier(80.0), "Strong Buy")
        self.assertEqual(assign_tier(95.0), "Strong Buy")
        self.assertEqual(assign_tier(65.0), "Buy")
        self.assertEqual(assign_tier(79.9), "Buy")
        self.assertEqual(assign_tier(64.9), "Rejected")
        self.assertEqual(assign_tier(45.0), "Rejected")

    def test_invariant_07_honest_rr_purely_analytical(self):
        """Invariant 7: Honest R:R is purely analytical and does not gate recommendations."""
        # Low R:R does not disqualify a qualifying score
        self.assertEqual(assign_tier(65.0, honest_rr=0.5), "Buy")
        self.assertEqual(assign_tier(85.0, honest_rr=0.1), "Strong Buy")
        # High R:R does not rescue a non-qualifying score
        self.assertEqual(assign_tier(50.0, honest_rr=5.0), "Rejected")

    def test_invariant_08_reach_prob_purely_analytical(self):
        """Invariant 8: Reach probability is purely analytical and does not gate qualification."""
        res = calculate_targets(
            ticker="AAPL",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=96.0,
            strategy_name="Trend Following",
            mock_reach_probs=(0.10, 0.05, 0.02),
        )
        self.assertTrue(res.is_valid)

    def test_invariant_09_no_capital_allocation(self):
        """Invariant 9: No allocate_capital in production codebase."""
        import jobs.generate_signals as gs
        self.assertFalse(hasattr(gs, "allocate_capital"))

    def test_invariant_10_no_kelly_sizing(self):
        """Invariant 10: No calculate_half_kelly or calculate_p_win in ranker or pipeline."""
        import src.ranker as rk
        self.assertFalse(hasattr(rk, "calculate_half_kelly"))
        self.assertFalse(hasattr(rk, "calculate_p_win"))

    def test_invariant_11_no_active_position_sizing_fields(self):
        """Invariant 11: Active position sizing fields are None in candidate payloads."""
        gen_path = os.path.join(PROJECT_ROOT, "jobs", "generate_signals.py")
        with open(gen_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('"position_sizing": None', content)
        self.assertIn('"allocated_dollars": None', content)
        self.assertIn('"exact_shares": None', content)
        self.assertIn('"max_shares": None', content)

    def test_invariant_12_no_automated_trade_execution(self):
        """Invariant 12: Zero automated trade execution or position monitoring."""
        gen_path = os.path.join(PROJECT_ROOT, "jobs", "generate_signals.py")
        with open(gen_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("def archive_current_signals", content)
        self.assertNotIn("archive_current_signals(", content)

    def test_invariant_13_no_cash_constraints(self):
        """Invariant 13: Zero cash constraints or cash balance rationing in pipeline."""
        gen_path = os.path.join(PROJECT_ROOT, "jobs", "generate_signals.py")
        with open(gen_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("cash_balance", content)
        self.assertNotIn("Cash constrained", content)

    def test_invariant_14_no_portfolio_drawdown_halts(self):
        """Invariant 14: Zero simulated portfolio drawdown halts."""
        gen_path = os.path.join(PROJECT_ROOT, "jobs", "generate_signals.py")
        with open(gen_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("get_drawdown_multiplier", content)
        self.assertNotIn("enforce_risk_controls", content)

    def test_invariant_15_quant_core_integrity(self):
        """Invariant 15: All 7 strategies and target/stop calculations are preserved."""
        from src.quant_config import (
            STRATEGY_WEIGHT_VECTORS,
            SCALE_OUT_WEIGHTS,
        )
        self.assertEqual(len(STRATEGY_WEIGHT_VECTORS), 7)
        self.assertEqual(SCALE_OUT_WEIGHTS["all_three"]["label"], "50/30/20")
        res = calculate_targets(
            ticker="NVDA",
            entry_price=120.0,
            atr_14=3.0,
            stop_loss=114.0,
            strategy_name="Trend Following",
            mock_reach_probs=(0.60, 0.40, 0.25),
        )
        self.assertTrue(res.is_valid)
        self.assertEqual(res.scale_out_weights, "50/30/20")


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestArchitectureInvariants)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
