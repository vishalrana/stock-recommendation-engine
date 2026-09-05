"""
Decommissioning and Recommendation Isolation Test Suite
======================================================
Validates that:
1. Decommissioned files (.github/workflows/monitor.yml, src/monitor, market-evaluator.ts, frontend/src/app/api) do NOT exist.
2. The recommendation engine pipeline operates strictly as an opportunity engine without simulated portfolio management.
3. Database functions for simulated exits/pnl are deprecated no-ops.
4. Frontend components and queries are decoupled from portfolio_state and unauthenticated mutation endpoints.
5. Recommendation trade setups (entry, stop, targets, R:R, scale-out plan) remain mathematically sound.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestDecommissioningAndIsolation(unittest.TestCase):

    def test_decommissioned_files_do_not_exist(self):
        """Verify all legacy portfolio automation and unauthenticated routes are deleted."""
        forbidden_paths = [
            os.path.join(PROJECT_ROOT, ".github", "workflows", "monitor.yml"),
            os.path.join(PROJECT_ROOT, "src", "monitor", "monitor_positions.py"),
            os.path.join(PROJECT_ROOT, "src", "monitor"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "lib", "market-evaluator.ts"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "app", "api", "sync-market"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "app", "api", "positions"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "app", "api", "signals"),
            os.path.join(PROJECT_ROOT, "frontend", "src", "app", "api"),
        ]
        for path in forbidden_paths:
            self.assertFalse(
                os.path.exists(path),
                f"Forbidden deprecated path still exists: {path}"
            )

    def test_frontend_decoupled_from_portfolio_state(self):
        """Verify frontend code does not query or depend on portfolio_state."""
        page_path = os.path.join(PROJECT_ROOT, "frontend", "src", "app", "page.tsx")
        db_path = os.path.join(PROJECT_ROOT, "frontend", "src", "lib", "database.ts")
        table_path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "recommendations-table.tsx")
        summary_path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "portfolio-summary.tsx")

        with open(page_path, "r", encoding="utf-8") as f:
            page_content = f.read()
        self.assertNotIn("getLatestPortfolioValue", page_content)
        self.assertNotIn("latestPortfolioValue", page_content)
        self.assertNotIn("portfolio_state", page_content)

        with open(db_path, "r", encoding="utf-8") as f:
            db_content = f.read()
        self.assertNotIn("getLatestPortfolioValue", db_content)
        self.assertNotIn("portfolio_state", db_content)

        with open(table_path, "r", encoding="utf-8") as f:
            table_content = f.read()
        self.assertNotIn("handleRecalculateAll", table_content)
        self.assertNotIn("handleSyncMarket", table_content)
        self.assertNotIn("/api/sync-market", table_content)
        self.assertNotIn("/api/signals/recalculate", table_content)
        self.assertNotIn("/api/positions/close", table_content)
        self.assertNotIn("latestPortfolioValue", table_content)

        with open(summary_path, "r", encoding="utf-8") as f:
            summary_content = f.read()
        self.assertNotIn("latestPortfolioValue", summary_content)

    def test_pipeline_no_simulated_trade_execution(self):
        """Verify jobs/generate_signals.py has no simulated exits or portfolio sizing."""
        gen_path = os.path.join(PROJECT_ROOT, "jobs", "generate_signals.py")
        with open(gen_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Should not call archive_current_signals (legacy exit runner)
        self.assertNotIn("def archive_current_signals", content)
        self.assertNotIn("archive_current_signals(", content)
        self.assertIn("refresh_active_signals_prices", content)

    def test_deprecated_db_functions_are_safe_noop(self):
        """Verify legacy exit and pnl functions in supabase_client are safe no-ops."""
        from jobs.supabase_client import update_portfolio_realized_pnl, execute_position_exit

        # Test execute_position_exit no-op
        res_exit = execute_position_exit(None, "TEST", 100.0, "test_exit", "open")
        self.assertIsNone(res_exit)

        # Test update_portfolio_realized_pnl no-op
        res_pnl = update_portfolio_realized_pnl(50.0)
        self.assertIsNone(res_pnl)

    def test_recommendation_trade_setup_math_isolated(self):
        """Verify that trade setups and scale-out plans compute cleanly without capital management."""
        from src.strategies.target_calculator import calculate_targets

        res = calculate_targets(
            ticker="NVDA",
            entry_price=120.0,
            atr_14=4.0,
            stop_loss=112.0,
            strategy_name="Trend Following",
            mock_reach_probs=(0.65, 0.45, 0.30),
        )
        self.assertTrue(res.is_valid)
        self.assertEqual(res.scale_out_weights, "50/30/20")
        self.assertGreater(res.target_1, 120.0)
        self.assertGreater(res.target_2, res.target_1)
        self.assertGreater(res.target_3, res.target_2)
        self.assertGreater(res.weighted_rr_honest, 1.0)


if __name__ == "__main__":
    unittest.main()
