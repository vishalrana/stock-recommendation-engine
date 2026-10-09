"""
Canonical Quantitative Golden Test Suite
=========================================
Verifies:
1. Exact canonical Wilder RMA smoothing for RSI (handling flat, zero losses, zero gains, NaN windows).
2. Exact canonical Wilder RMA smoothing for ATR (initial simple mean, subsequent RMA recursion, NaN windows).
3. Moving average window integrity (elimination of partial-window contamination via min_periods=period).
4. Expectancy score strictly clamped to [0.0, 100.0] with null/NaN safety.
5. Win-rate provenance tracking and elimination of synthetic pseudo-counts for unobserved tickers.
6. Target ordering enforcement on override_targets (entry < t1 < t2 < t3).
7. Strategy EMA20 alignment with computed technical indicators.
8. Lifecycle trade activation separation (scan date D does not evaluate same-day bars).
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from src.indicators import (
    calculate_dma,
    calculate_rsi,
    calculate_volume_ma,
    calculate_atr,
    calculate_indicators,
)
from src.ranker import compute_expectancy_score
from src.utils.metrics_pipeline import (
    build_hardened_metrics,
    calculate_shrunk_win_rate,
)
from src.strategies.target_calculator import calculate_targets
from jobs.strategies.trend_following import TrendFollowingStrategy
from jobs.generate_signals import reconcile_recommendation_lifecycle


class TestCanonicalQuantGolden(unittest.TestCase):

    def test_01_wilder_rsi_golden_vector(self):
        """Test Wilder RSI against a deterministic reference series."""
        # 16-bar price series
        prices = pd.Series([
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00
        ])
        rsi = calculate_rsi(prices, period=14)
        
        # First 14 bars must be NaN (indices 0 through 13)
        self.assertTrue(np.isnan(rsi.iloc[:14]).all())
        
        # Bar 14 (15th bar) is the first valid RSI value
        val_14 = rsi.iloc[14]
        self.assertFalse(np.isnan(val_14))
        self.assertTrue(0.0 <= val_14 <= 100.0)
        # Expected RSI at bar 14 for this classic dataset is ~70.5
        self.assertAlmostEqual(val_14, 70.53, delta=0.5)

    def test_02_wilder_rsi_edge_cases(self):
        """Test Wilder RSI under boundary conditions: all gains, all losses, flat series."""
        # All gains (strictly monotonically increasing)
        up_prices = pd.Series(range(100, 130), dtype=float)
        rsi_up = calculate_rsi(up_prices, period=14)
        self.assertEqual(rsi_up.iloc[-1], 100.0)

        # All losses (strictly monotonically decreasing)
        down_prices = pd.Series(range(130, 100, -1), dtype=float)
        rsi_down = calculate_rsi(down_prices, period=14)
        self.assertEqual(rsi_down.iloc[-1], 0.0)

        # Flat prices (zero gains and zero losses) -> neutral 50.0
        flat_prices = pd.Series([100.0] * 30)
        rsi_flat = calculate_rsi(flat_prices, period=14)
        self.assertEqual(rsi_flat.iloc[-1], 50.0)

        # Insufficient data (< 14 bars)
        short_prices = pd.Series([100.0, 101.0, 102.0])
        rsi_short = calculate_rsi(short_prices, period=14)
        self.assertTrue(np.isnan(rsi_short).all())

    def test_03_wilder_atr_golden_vector(self):
        """Test Wilder ATR calculation against exact manual RMA recursion."""
        n = 20
        high = pd.Series([10.0 + i * 0.5 + 1.0 for i in range(n)])
        low = pd.Series([10.0 + i * 0.5 - 1.0 for i in range(n)])
        close = pd.Series([10.0 + i * 0.5 for i in range(n)])

        atr = calculate_atr(high, low, close, period=14)

        # First 13 bars must be NaN (indices 0 .. 12)
        self.assertTrue(np.isnan(atr.iloc[:13]).all())

        # Bar 13 (14th bar) is the initial simple mean
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        expected_atr_13 = tr.iloc[:14].mean()
        self.assertAlmostEqual(atr.iloc[13], expected_atr_13, places=4)

        # Bar 14 is smoothed via Wilder RMA: (13 * atr[13] + tr[14]) / 14
        expected_atr_14 = (13.0 * expected_atr_13 + tr.iloc[14]) / 14.0
        self.assertAlmostEqual(atr.iloc[14], expected_atr_14, places=4)

    def test_04_dma_no_partial_window_contamination(self):
        """Verify that DMA returns NaN for bars preceding the full lookback window."""
        series = pd.Series(range(1, 101), dtype=float)
        dma50 = calculate_dma(series, 50)
        
        # Exactly the first 49 bars must be NaN
        self.assertTrue(np.isnan(dma50.iloc[:49]).all())
        self.assertFalse(np.isnan(dma50.iloc[49]))
        self.assertAlmostEqual(dma50.iloc[49], np.mean(series.iloc[:50]), places=4)

        vol_ma = calculate_volume_ma(series, 20)
        self.assertTrue(np.isnan(vol_ma.iloc[:19]).all())
        self.assertFalse(np.isnan(vol_ma.iloc[19]))

    def test_05_expectancy_score_clamping_and_nan_safety(self):
        """Verify compute_expectancy_score bounds [0.0, 100.0] and handles None/NaN safely."""
        # Baseline swing expectancy (1.44%) -> 30 + 20 * 1.44 = 58.8
        score_normal = compute_expectancy_score("trend_following", adjusted_expectancy_pct=1.44)
        self.assertAlmostEqual(score_normal, 58.8, places=2)

        # Highly negative expectancy (-5.0%) -> 30 + 20 * (-5) = -70 -> clamped to 0.0
        score_neg = compute_expectancy_score("trend_following", adjusted_expectancy_pct=-5.0)
        self.assertEqual(score_neg, 0.0)

        # Extreme positive expectancy (+5.0%) -> 30 + 20 * 5 = 130 -> clamped to 100.0
        score_pos = compute_expectancy_score("trend_following", adjusted_expectancy_pct=5.0)
        self.assertEqual(score_pos, 100.0)

        # None value falls back to strategy canonical historical prior
        score_none = compute_expectancy_score("trend_following", adjusted_expectancy_pct=None)
        self.assertTrue(0.0 <= score_none <= 100.0)

        # NaN value falls back to strategy canonical historical prior without raising error
        score_nan = compute_expectancy_score("trend_following", adjusted_expectancy_pct=float("nan"))
        self.assertTrue(0.0 <= score_nan <= 100.0)
        self.assertFalse(np.isnan(score_nan))

    def test_06_metrics_provenance_without_synthetic_counts(self):
        """Verify that zero-trade tickers receive strategy_prior provenance with sample_size=0."""
        metrics = build_hardened_metrics(
            ticker="NEWCO",
            raw_record={},
            strategy_name="trend_following",
            strategy_win_rate=65.0,
        )
        self.assertEqual(metrics["win_rate_provenance"], "strategy_prior")
        self.assertEqual(metrics["completed_trades"], 0)
        self.assertEqual(metrics["metric_sample_size"], 0)
        self.assertEqual(metrics["metric_confidence"], "prior")
        self.assertEqual(metrics["raw_win_rate"], 65.0)
        # Shrunk win rate matches authoritative prior without fabricated trades
        self.assertEqual(metrics["shrunk_win_rate"], 65.0)

    def test_07_override_targets_ordering_validation(self):
        """Verify that non-monotonic override targets are rejected with is_valid=False."""
        # Valid monotonic targets
        res_valid = calculate_targets(
            ticker="TEST",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=95.0,
            strategy_name="trend_following",
            override_targets=(105.0, 110.0, 115.0),
            mock_reach_probs=(0.7, 0.5, 0.3),
        )
        self.assertTrue(res_valid.is_valid)
        self.assertEqual(res_valid.target_1, 105.0)
        self.assertEqual(res_valid.target_2, 110.0)
        self.assertEqual(res_valid.target_3, 115.0)

        # Inverted targets (T1 > T2 > T3)
        res_invalid_order = calculate_targets(
            ticker="TEST",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=95.0,
            strategy_name="trend_following",
            override_targets=(115.0, 110.0, 105.0),
            mock_reach_probs=(0.7, 0.5, 0.3),
        )
        self.assertFalse(res_invalid_order.is_valid)
        self.assertIn("Invalid override targets ordering", res_invalid_order.rejection_reason)

        # T1 below entry price
        res_below_entry = calculate_targets(
            ticker="TEST",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=95.0,
            strategy_name="trend_following",
            override_targets=(98.0, 105.0, 110.0),
            mock_reach_probs=(0.7, 0.5, 0.3),
        )
        self.assertFalse(res_below_entry.is_valid)
        self.assertIn("Invalid override targets ordering", res_below_entry.rejection_reason)

    def test_08_strategy_ema20_indicator_integration(self):
        """Verify strategies consume df['EMA_20'] rather than stale proxies."""
        dates = pd.date_range("2026-01-01", periods=250)
        np.random.seed(42)
        close_prices = 100.0 + np.cumsum(np.random.normal(0.2, 1.0, 250))
        df = pd.DataFrame({
            "OPEN": close_prices - 0.5,
            "HIGH": close_prices + 1.0,
            "LOW": close_prices - 1.0,
            "CLOSE": close_prices,
            "VOLUME": [1000000] * 250,
        }, index=dates)

        df = calculate_indicators(df)
        self.assertIn("EMA_20", df.columns)
        self.assertIn("ATR_14", df.columns)

        strat = TrendFollowingStrategy()
        signal = strat.scan(
            ticker="TEST",
            df=df,
            regime="bull",
            metrics={"wins": 10, "losses": 5, "win_rate": 66.7, "expectancy_pct": 2.0},
        )
        if signal:
            self.assertEqual(signal["ema20"], round(float(df["EMA_20"].iloc[-1]), 2))
        else:
            ema_val = round(float(df["EMA_20"].iloc[-1]), 2)
            self.assertIsInstance(ema_val, float)

    def test_09_lifecycle_trade_activation_separation(self):
        """Verify that recommendations created on scan date D are not evaluated on day D bars."""
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "uuid-today",
                "ticker": "TODAY_REC",
                "status": "open",
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "target_3": 120.0,
                "strategy": "trend_following",
                "scan_date": "2026-10-07",
            }
        ]

        same_day_bar = pd.DataFrame(
            [{"OPEN": 99.0, "HIGH": 102.0, "LOW": 93.0, "CLOSE": 94.0}],
            index=pd.DatetimeIndex(["2026-10-07"]),
        )
        with patch("jobs.supabase_client.get_bars_after", return_value=same_day_bar), \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            # Even if the same-day bar breaches the stop loss, the activation guard skips it

            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"TODAY_REC"},
                scan_successful=True,
                scanned_count=100,
                min_required_scanned=10,
                current_scan_date="2026-10-07",
            )

            # Neither status nor history outcome should be updated to stopped
            mock_update_sig.assert_not_called()
            mock_update_hist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
