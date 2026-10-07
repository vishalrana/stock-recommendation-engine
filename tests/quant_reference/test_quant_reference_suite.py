"""
Independent Quantitative Reference Test Suite
==============================================
Validates that production quant code matches textbook independent reference models
across deterministic golden datasets with zero look-ahead and correct indexing.
"""

import os
import sys
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
src_dir = os.path.join(PROJECT_ROOT, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import unittest
import numpy as np
import pandas as pd

from src.indicators import (
    calculate_rsi,
    calculate_atr,
    compute_adx,
    compute_ema,
)
from src.utils.metrics_pipeline import (
    calculate_shrunk_win_rate,
    calculate_shrunk_expectancy,
    build_hardened_metrics,
)
from src.ranker import (
    compute_expectancy_score,
    SignalRanker,
)
from src.strategies.target_calculator import (
    get_reach_prob_target_before_stop,
    calculate_targets,
)
from tests.quant_reference.golden_datasets import (
    dataset_1_monotonic_uptrend,
    dataset_2_monotonic_downtrend,
    dataset_3_flat_market,
    dataset_4_alternating,
    dataset_5_volatile_market,
    dataset_6_gap_market,
    dataset_8_realistic_multi_regime,
)
from tests.quant_reference.reference_models import (
    ref_rsi,
    ref_atr,
    ref_adx,
    ref_ema,
    ref_bayesian_win_rate,
    ref_expectancy,
    ref_reach_target_before_stop,
)


class TestQuantReferenceSuite(unittest.TestCase):

    def test_01_rsi_production_vs_reference_uptrend(self):
        """Verify production RSI matches textbook reference on monotonic uptrend."""
        df = dataset_1_monotonic_uptrend(100)
        prod_rsi = calculate_rsi(df["CLOSE"], period=14).dropna()
        ref_vals = [v for v in ref_rsi(df["CLOSE"].tolist(), period=14) if v is not None]

        self.assertEqual(len(prod_rsi), len(ref_vals))
        np.testing.assert_allclose(prod_rsi.values, ref_vals, atol=1e-5)
        # On continuous gains, RSI must approach 100
        self.assertGreater(prod_rsi.iloc[-1], 99.0)

    def test_02_rsi_production_vs_reference_downtrend(self):
        """Verify production RSI matches textbook reference on monotonic downtrend."""
        df = dataset_2_monotonic_downtrend(100)
        prod_rsi = calculate_rsi(df["CLOSE"], period=14).dropna()
        ref_vals = [v for v in ref_rsi(df["CLOSE"].tolist(), period=14) if v is not None]

        self.assertEqual(len(prod_rsi), len(ref_vals))
        np.testing.assert_allclose(prod_rsi.values, ref_vals, atol=1e-5)
        # On continuous losses, RSI must be 0.0
        self.assertLess(prod_rsi.iloc[-1], 1.0)

    def test_03_rsi_production_vs_reference_flat(self):
        """Verify production RSI matches textbook reference on flat market (exact 50.0)."""
        df = dataset_3_flat_market(50)
        prod_rsi = calculate_rsi(df["CLOSE"], period=14).dropna()
        ref_vals = [v for v in ref_rsi(df["CLOSE"].tolist(), period=14) if v is not None]

        self.assertEqual(len(prod_rsi), len(ref_vals))
        np.testing.assert_allclose(prod_rsi.values, ref_vals, atol=1e-5)
        self.assertTrue((prod_rsi == 50.0).all())

    def test_04_rsi_production_vs_reference_volatile(self):
        """Verify production RSI matches textbook reference on volatile oscillations."""
        df = dataset_5_volatile_market(100)
        prod_rsi = calculate_rsi(df["CLOSE"], period=14).dropna()
        ref_vals = [v for v in ref_rsi(df["CLOSE"].tolist(), period=14) if v is not None]

        self.assertEqual(len(prod_rsi), len(ref_vals))
        np.testing.assert_allclose(prod_rsi.values, ref_vals, atol=1e-5)

    def test_05_atr_production_vs_reference_all_datasets(self):
        """Verify production ATR matches textbook reference across multiple datasets."""
        datasets = [
            dataset_1_monotonic_uptrend(80),
            dataset_2_monotonic_downtrend(80),
            dataset_4_alternating(80),
            dataset_5_volatile_market(80),
            dataset_8_realistic_multi_regime(200),
        ]
        for df in datasets:
            prod_atr = calculate_atr(df["HIGH"], df["LOW"], df["CLOSE"], period=14).dropna()
            ref_vals = [
                v for v in ref_atr(df["HIGH"].tolist(), df["LOW"].tolist(), df["CLOSE"].tolist(), period=14)
                if v is not None
            ]
            self.assertEqual(len(prod_atr), len(ref_vals))
            np.testing.assert_allclose(prod_atr.values, ref_vals, atol=1e-5)

    def test_06_adx_production_vs_reference(self):
        """Verify production ADX matches canonical Wilder reference model."""
        datasets = [
            dataset_1_monotonic_uptrend(100),
            dataset_2_monotonic_downtrend(100),
            dataset_5_volatile_market(100),
            dataset_8_realistic_multi_regime(200),
        ]
        for df in datasets:
            prod_adx = compute_adx(df["HIGH"], df["LOW"], df["CLOSE"], period=14).dropna()
            ref_vals = [
                v for v in ref_adx(df["HIGH"].tolist(), df["LOW"].tolist(), df["CLOSE"].tolist(), period=14)
                if v is not None
            ]
            self.assertEqual(len(prod_adx), len(ref_vals))
            np.testing.assert_allclose(prod_adx.values, ref_vals, atol=1e-5)

    def test_07_ema_production_vs_reference(self):
        """Verify EMA matches independent recursive formula."""
        df = dataset_8_realistic_multi_regime(150)
        prod_ema = compute_ema(df["CLOSE"], period=20)
        ref_vals = ref_ema(df["CLOSE"].tolist(), period=20)
        np.testing.assert_allclose(prod_ema.values, ref_vals, atol=1e-5)

    def test_08_bayesian_win_rate_shrinkage(self):
        """Verify Beta-Binomial shrinkage matches independent reference."""
        test_cases = [
            (0, 0, 50.0),
            (1, 0, 50.0),
            (3, 1, 55.0),
            (10, 2, 45.0),
            (50, 50, 50.0),
            (0, 10, 55.0),
        ]
        for w, l, p in test_cases:
            prod = calculate_shrunk_win_rate(w, l, alpha=5.0, prior_win_rate=p)
            ref = ref_bayesian_win_rate(w, l, alpha=5.0, prior=p)
            self.assertAlmostEqual(prod, ref, places=2)

    def test_09_expectancy_score_clamping_and_slope(self):
        """Verify expectancy score maps E_adjusted to [0, 100] exactly."""
        cases = [
            (1.44, 58.8),
            (0.0, 30.0),
            (3.5, 100.0),
            (-2.0, 0.0),
        ]
        for e_pct, expected_score in cases:
            prod = compute_expectancy_score("trend_following", adjusted_expectancy_pct=e_pct)
            ref = ref_expectancy(e_pct)
            self.assertAlmostEqual(prod, expected_score, places=1)
            self.assertAlmostEqual(prod, ref, places=1)

    def test_10_reach_target_before_stop_simulation(self):
        """Verify forward reach probability target-before-stop matches reference simulation."""
        df = dataset_8_realistic_multi_regime(200)
        prod_prob = get_reach_prob_target_before_stop(
            ticker="TEST_TICKER",
            target_pct=0.08,
            stop_pct=0.04,
            holding_days=15,
            price_df=df,
            lookback_days=100,
        )
        ref_prob = ref_reach_target_before_stop(
            closes=df["CLOSE"].dropna().tolist(),
            highs=df["HIGH"].dropna().tolist(),
            lows=df["LOW"].dropna().tolist(),
            opens=df["OPEN"].dropna().tolist(),
            t_pct=0.08,
            s_pct=0.04,
            hold_days=15,
            lookback_days=100,
        )
        self.assertAlmostEqual(prod_prob, ref_prob, places=4)

    def test_11_win_rate_provenance_cases_a_through_e(self):
        """
        Verify explicit provenance taxonomy (Section 12 Cases A-E):
        Case A: No observations.
        Case B: Ticker observations only.
        Case C: Strategy-specific observations only.
        Case D: Both ticker and strategy observations.
        Case E: Conflicting ticker and strategy statistics.
        """
        # Case A: No observations
        m_a = build_hardened_metrics("AAPL", raw_record={"wins": 0, "losses": 0})
        self.assertEqual(m_a["provenance"], "unavailable")
        self.assertEqual(m_a["sample_size"], 0)
        self.assertEqual(m_a["shrunk_win_rate"], 50.0)

        # Case B: Ticker observations only
        m_b = build_hardened_metrics("AAPL", raw_record={"wins": 8, "losses": 2})
        self.assertEqual(m_b["provenance"], "ticker_observed")
        self.assertEqual(m_b["source"], "ticker_historical_trades")
        self.assertEqual(m_b["sample_size"], 10)
        # Prior is 50.0: (8*100 + 5*50) / 15 = (800 + 250)/15 = 70.0
        self.assertAlmostEqual(m_b["shrunk_win_rate"], 70.0, places=1)

        # Case C: Strategy-specific observations only
        m_c = build_hardened_metrics("AAPL", raw_record={"wins": 0, "losses": 0}, strategy_win_rate=62.0)
        self.assertEqual(m_c["provenance"], "strategy_prior")
        self.assertEqual(m_c["sample_size"], 0)
        self.assertEqual(m_c["shrunk_win_rate"], 62.0)

        # Case D: Both ticker and strategy observations
        m_d = build_hardened_metrics("AAPL", raw_record={"wins": 4, "losses": 1}, strategy_win_rate=60.0)
        self.assertEqual(m_d["provenance"], "ticker_observed")
        self.assertEqual(m_d["source"], "ticker_observed_with_strategy_prior")
        self.assertEqual(m_d["sample_size"], 5)
        # Prior is 60.0: (4*100 + 5*60) / 10 = (400 + 300) / 10 = 70.0
        self.assertAlmostEqual(m_d["shrunk_win_rate"], 70.0, places=1)

        # Case E: Conflicting statistics (small ticker sample 100% vs low strategy prior 40%)
        m_e = build_hardened_metrics("AAPL", raw_record={"wins": 2, "losses": 0}, strategy_win_rate=40.0)
        self.assertEqual(m_e["provenance"], "ticker_observed")
        self.assertEqual(m_e["source"], "ticker_observed_with_strategy_prior")
        # Prior is 40.0: (2*100 + 5*40) / 7 = 400 / 7 = 57.14
        self.assertAlmostEqual(m_e["shrunk_win_rate"], 57.14, places=1)


if __name__ == "__main__":
    unittest.main()
