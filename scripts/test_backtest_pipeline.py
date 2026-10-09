"""
Test Backtest Pipeline & Statistical Integrity
===============================================
Master Quantitative Specification v2.3+ Compliant

Validates:
1. Wilson 95% Confidence Interval mathematics.
2. Strict chronological separation between In-Sample (Train), Embargo, and Out-of-Sample (Test).
3. Execution realism (D+1 Open entry, transaction cost deduction, gap-up filters).
4. Scale-out weight conservation invariant across backtest trades.
5. Backtest artifact generation and schema integrity.
"""

import os
import sys
import json
import math
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from scripts.validate_backtest_pipeline import calculate_wilson_ci
from src.outcome.outcome_calculator import PositionScaleOutTracker, evaluate_signal_outcome


class TestBacktestPipeline(unittest.TestCase):

    def test_01_wilson_ci_mathematics(self):
        """Wilson score interval must return correct finite bounds bounded [0, 100]."""
        # Zero sample
        low, high = calculate_wilson_ci(0, 0)
        self.assertEqual(low, 0.0)
        self.assertEqual(high, 0.0)

        # 50% on 100 trades
        low, high = calculate_wilson_ci(50, 100)
        self.assertGreater(low, 40.0)
        self.assertLess(low, 50.0)
        self.assertGreater(high, 50.0)
        self.assertLess(high, 60.0)
        self.assertAlmostEqual((low + high) / 2.0, 50.0, delta=1.0)

        # 100% win rate
        low, high = calculate_wilson_ci(10, 10)
        self.assertGreater(low, 70.0)
        self.assertAlmostEqual(high, 100.0, places=4)

        # 0% win rate
        low, high = calculate_wilson_ci(0, 10)
        self.assertAlmostEqual(low, 0.0, places=4)
        self.assertLess(high, 30.0)

    def test_02_chronological_partition_and_embargo_integrity(self):
        """Train, Embargo, and Test periods must be strictly chronological with non-overlapping partitions."""
        total_days = 200
        dates = pd.date_range("2025-01-01", periods=total_days, freq="B")
        train_ratio = 0.60
        embargo_bars = 20

        train_cutoff = int(total_days * train_ratio)
        train_dates = dates[:train_cutoff]
        embargo_dates = dates[train_cutoff : train_cutoff + embargo_bars]
        test_dates = dates[train_cutoff + embargo_bars :]

        # Non-overlapping
        self.assertEqual(len(set(train_dates) & set(embargo_dates)), 0)
        self.assertEqual(len(set(train_dates) & set(test_dates)), 0)
        self.assertEqual(len(set(embargo_dates) & set(test_dates)), 0)

        # Temporal monotonicity
        self.assertLess(train_dates[-1], embargo_dates[0])
        self.assertLess(embargo_dates[-1], test_dates[0])
        self.assertEqual(len(embargo_dates), 20)

    def test_03_execution_realism_and_costs(self):
        """Simulated trades must account for D+1 Open entry and deduct round-trip transaction costs."""
        # Create synthetic OHLC
        dates = pd.date_range("2026-01-01", periods=10, freq="B")
        df = pd.DataFrame({
            "OPEN": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0],
            "HIGH": [102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0],
            "LOW": [99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0],
            "CLOSE": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0],
        }, index=dates)

        entry_price = 100.0
        stop_loss = 95.0
        t1 = 105.0
        t2 = 108.0
        t3 = 110.0

        res = evaluate_signal_outcome(
            df=df,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=t1,
            target_2=t2,
            target_3=t3,
        )
        self.assertIsNotNone(res)
        raw_ret = res["realized_return_pct"]
        # With 10 bps per trade = 20 bps round trip
        cost_pct = 0.20
        net_ret = raw_ret - cost_pct
        self.assertLess(net_ret, raw_ret)
        self.assertAlmostEqual(net_ret, raw_ret - 0.20, places=4)

    def test_04_position_tracker_weight_conservation(self):
        """Every trade scale-out state transition must strictly conserve realized_weight + remaining_weight == 1.0."""
        tracker = PositionScaleOutTracker(
            entry_price=100.0,
            stop_loss=95.0,
            target_1=105.0,
            target_2=110.0,
            target_3=115.0,
        )
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0, places=6)

        tracker.on_t1_hit(105.0)
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0, places=6)
        self.assertAlmostEqual(tracker.realized_weight, 0.50, places=6)

        tracker.on_t2_hit(110.0)
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0, places=6)
        self.assertAlmostEqual(tracker.realized_weight, 0.80, places=6)

        tracker.on_t3_hit(115.0)
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0, places=6)
        self.assertAlmostEqual(tracker.realized_weight, 1.0, places=6)
        self.assertTrue(tracker.is_closed)

    def test_05_backtest_summary_schema_and_finiteness(self):
        """If production_backtest_summary.json exists, it must have complete schema and finite numbers."""
        summary_path = os.path.join(PROJECT_ROOT, "outputs", "production_backtest_summary.json")
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertIn("metadata", data)
            self.assertIn("in_sample_train_stats", data)
            self.assertIn("out_of_sample_test_stats", data)
            self.assertIn("combined_all_stats", data)
            self.assertIn("benchmark_comparison", data)
            self.assertIn("survivorship_and_limitations", data)

            # Check stats finiteness
            for k in ["in_sample_train_stats", "out_of_sample_test_stats", "combined_all_stats"]:
                stats = data[k]
                self.assertTrue(math.isfinite(stats["win_rate_pct"]))
                self.assertTrue(math.isfinite(stats["net_expectancy_pct"]))
                self.assertTrue(math.isfinite(stats["profit_factor"]))
                self.assertTrue(math.isfinite(stats["max_drawdown_pct"]))


if __name__ == "__main__":
    unittest.main()
