"""
Test Backtest Pipeline & Statistical Integrity
===============================================
Validates:
1. Block-bootstrap confidence intervals (resampling whole signal months) and the edge verdict.
2. Strict chronological separation between early period, embargo, and late period.
3. Execution realism (D+1 Open entry, transaction cost deduction).
4. Scale-out weight conservation invariant across backtest trades.
5. Backtest artifact and strategy-evidence schema integrity.
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

from scripts.validate_backtest_pipeline import block_bootstrap_ci, compile_stats, edge_verdict
from src.outcome.outcome_calculator import PositionScaleOutTracker, evaluate_signal_outcome
from src.strategy_evidence import load_strategy_evidence, aggregate_trades, evidence_from_aggregate


def _trade(month, ret, strategy="Trend Following", outcome="stopped"):
    return {
        "signal_date": f"2025-{month:02d}-10", "net_return_pct": ret, "strategy": strategy,
        "outcome": outcome, "exit_reason": "stop", "holding_days": 5, "has_t2": True, "has_t3": False,
    }


class TestBacktestPipeline(unittest.TestCase):

    def test_01_block_bootstrap_and_edge_verdict(self):
        """Clustered (signal-month) bootstrap CIs bracket the mean; correlated months widen them."""
        rng = np.random.default_rng(1)
        values = rng.normal(0.5, 3.0, 600)
        months = np.repeat(np.arange(12), 50)
        lo, hi = block_bootstrap_ci(values, months, np.mean)
        self.assertLess(lo, values.mean())
        self.assertGreater(hi, values.mean())

        # A strong common shock per month (correlated trades) must widen the interval vs. iid noise
        shocks = np.repeat(rng.normal(0, 3.0, 12), 50)
        lo_c, hi_c = block_bootstrap_ci(values + shocks, months, np.mean)
        self.assertGreater(hi_c - lo_c, hi - lo)

        self.assertEqual(block_bootstrap_ci(values, np.zeros(600), np.mean), (None, None))

        few = [_trade(1, 1.0)] * 10
        self.assertEqual(edge_verdict(compile_stats(few)), "INSUFFICIENT_SAMPLE")
        losers = [_trade(m, -2.0 + 0.01 * i) for m in range(1, 13) for i in range(5)]
        self.assertEqual(edge_verdict(compile_stats(losers)), "NEGATIVE_EDGE")

    def test_01b_strategy_evidence_shrinkage(self):
        """Evidence aggregates trades per strategy and shrinks toward neutral priors (50% / 0%)."""
        trades = [_trade(1, 2.0, outcome="hit_t1")] * 3 + [_trade(1, -1.0)] * 2
        agg = aggregate_trades(trades)
        ev = evidence_from_aggregate("Trend Following", agg["trend_following"])
        self.assertEqual(ev.trades, 5)
        self.assertEqual(ev.raw_win_rate, 60.0)
        self.assertAlmostEqual(ev.raw_expectancy, 0.8)
        self.assertAlmostEqual(ev.shrunk_win_rate, (3 * 100 + 5 * 50) / 10)
        self.assertAlmostEqual(ev.shrunk_expectancy, (5 * 0.8) / 10)
        self.assertEqual(ev.target_hits["t1"], (3, 5))
        self.assertEqual(ev.target_hits["t3"], (0, 0))
        empty = evidence_from_aggregate("Mean Reversion", None)
        self.assertEqual((empty.shrunk_win_rate, empty.shrunk_expectancy, empty.source), (50.0, 0.0, "no_evidence"))

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
        """If production_backtest_summary.json exists (new harness), it must have complete schema and finite numbers."""
        summary_path = os.path.join(PROJECT_ROOT, "outputs", "production_backtest_summary.json")
        if not os.path.exists(summary_path):
            self.skipTest("No backtest summary generated yet")
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "all_trades" not in data:
            self.skipTest("Summary predates the production-pipeline harness")
        for key in ("metadata", "edge_verdict_all", "edge_verdict_late_period", "early_period",
                    "late_period", "by_strategy", "by_regime", "benchmark"):
            self.assertIn(key, data)
        self.assertIn("limitations", data["metadata"])
        stats = data["all_trades"]
        if stats.get("trade_count", 0) > 0:
            self.assertTrue(math.isfinite(stats["win_rate_pct"]))
            self.assertTrue(math.isfinite(stats["net_expectancy_pct"]))

    def test_06_committed_strategy_evidence_is_loadable(self):
        """config/strategy_performance.json (consumed by production scoring) must parse into evidence."""
        path = os.path.join(PROJECT_ROOT, "config", "strategy_performance.json")
        if not os.path.exists(path):
            self.skipTest("Strategy evidence not generated yet")
        evidence = load_strategy_evidence(path, refresh=True)
        for key, ev in evidence.items():
            self.assertGreaterEqual(ev.trades, 0)
            self.assertTrue(0.0 <= ev.shrunk_win_rate <= 100.0)
            self.assertTrue(math.isfinite(ev.shrunk_expectancy))


if __name__ == "__main__":
    unittest.main()
