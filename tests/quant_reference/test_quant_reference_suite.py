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
    calculate_dma,
    compute_adx,
    compute_ema,
    compute_macd,
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
    dataset_9_pseudorandom_seeded,
    dataset_10_insufficient_history,
    dataset_11_sporadic_nans,
    dataset_12_extreme_pathological,
)
from tests.quant_reference.reference_models import (
    ref_rsi,
    ref_atr,
    ref_adx,
    ref_ema,
    ref_sma,
    ref_macd,
    ref_composite_score,
    ref_target_hierarchy,
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

    def test_14_sma_production_vs_reference(self):
        """Verify production calculate_dma matches textbook ref_sma across golden dataset 8."""
        df = dataset_8_realistic_multi_regime(200)
        prod_sma = calculate_dma(df["CLOSE"], period=50).dropna()
        ref_vals = [v for v in ref_sma(df["CLOSE"].tolist(), period=50) if v is not None]

        self.assertEqual(len(prod_sma), len(ref_vals))
        np.testing.assert_allclose(prod_sma.values, ref_vals, atol=1e-5)

    def test_15_macd_production_vs_reference(self):
        """Verify production compute_macd matches textbook ref_macd on seeded dataset 9."""
        df = dataset_9_pseudorandom_seeded(150, seed=42)
        p_macd, p_sig, p_hist = compute_macd(df["CLOSE"], fast=12, slow=26, signal=9)
        r_macd, r_sig, r_hist = ref_macd(df["CLOSE"].tolist(), fast=12, slow=26, signal=9)

        # Drop burn-in periods and compare
        np.testing.assert_allclose(p_macd.values, r_macd, atol=1e-5)
        np.testing.assert_allclose(p_sig.values, r_sig, atol=1e-5)
        np.testing.assert_allclose(p_hist.values, r_hist, atol=1e-5)

    def test_16_composite_score_reference_dot_product(self):
        """Verify SignalRanker composite score strictly matches independent linear dot product."""
        ranker = SignalRanker()
        sub_scores = {
            "mom": 75.0,
            "exp": 80.0,
            "wr": 65.0,
            "reg": 100.0,
            "ctx": 60.0,
        }
        weights = {"mom": 0.45, "exp": 0.20, "wr": 0.15, "reg": 0.10, "ctx": 0.10}
        ref_score = ref_composite_score(sub_scores, weights)

        # Construct candidate matching sub_scores
        candidate = {
            "ticker": "COMP_REF",
            "strategy": "trend_following",
            "momentum_score": sub_scores["mom"],
            "expectancy_score": sub_scores["exp"],
            "winrate_score": sub_scores["wr"],
            "context_score": sub_scores["ctx"],
        }
        prod_res = ranker.compute_composite_score(candidate, "bull")
        # In bull regime, trend_following regime score is 100.0
        self.assertAlmostEqual(prod_res["total"], ref_score, places=1)

    def test_17_target_hierarchy_reference_model(self):
        """Verify production calculate_targets hierarchy matches textbook ref_target_hierarchy."""
        # 1. All three survive
        cand = (108.0, 115.0, 125.0)
        r_t1, r_t2, r_t3, r_label = ref_target_hierarchy(cand, (0.50, 0.30, 0.20))
        prod_res = calculate_targets(
            "HIER_TEST", entry_price=100.0, atr_14=2.0, stop_loss=94.0,
            strategy_name="trend_following", mock_reach_probs=(0.50, 0.30, 0.20),
            override_targets=cand,
        )
        self.assertTrue(prod_res.is_valid)
        self.assertEqual(prod_res.scale_out_weights, r_label)
        self.assertEqual(prod_res.target_1, r_t1)
        self.assertEqual(prod_res.target_2, r_t2)
        self.assertEqual(prod_res.target_3, r_t3)

        # 2. T3 pruned
        r_t1, r_t2, r_t3, r_label = ref_target_hierarchy(cand, (0.50, 0.30, 0.05))
        prod_res = calculate_targets(
            "HIER_TEST", entry_price=100.0, atr_14=2.0, stop_loss=94.0,
            strategy_name="trend_following", mock_reach_probs=(0.50, 0.30, 0.05),
            override_targets=cand,
        )
        self.assertTrue(prod_res.is_valid)
        self.assertEqual(prod_res.scale_out_weights, r_label)
        self.assertIsNone(prod_res.target_3)

        # 3. T2 and T3 pruned
        r_t1, r_t2, r_t3, r_label = ref_target_hierarchy(cand, (0.50, 0.10, 0.05))
        prod_res = calculate_targets(
            "HIER_TEST", entry_price=100.0, atr_14=2.0, stop_loss=94.0,
            strategy_name="trend_following", mock_reach_probs=(0.50, 0.10, 0.05),
            override_targets=cand,
        )
        self.assertTrue(prod_res.is_valid)
        self.assertEqual(prod_res.scale_out_weights, r_label)
        self.assertIsNone(prod_res.target_2)
        self.assertIsNone(prod_res.target_3)

    def test_18_golden_datasets_9_through_12_validation(self):
        """Verify quantitative resilience across datasets 9, 10, 11, 12."""
        # Dataset 9: Seeded random walk produces valid RSI and ATR
        df9 = dataset_9_pseudorandom_seeded(100, seed=123)
        rsi9 = calculate_rsi(df9["CLOSE"], period=14).dropna()
        atr9 = calculate_atr(df9["HIGH"], df9["LOW"], df9["CLOSE"], period=14).dropna()
        self.assertTrue((rsi9 >= 0.0).all() and (rsi9 <= 100.0).all())
        self.assertTrue((atr9 > 0.0).all())

        # Dataset 10: Insufficient history (10 bars) correctly yields NaN without crashing
        df10 = dataset_10_insufficient_history(10)
        rsi10 = calculate_rsi(df10["CLOSE"], period=14)
        self.assertTrue(rsi10.isna().all())

        # Dataset 11: Sporadic NaNs handled without unhandled exception
        df11 = dataset_11_sporadic_nans(100)
        rsi11 = calculate_rsi(df11["CLOSE"], period=14)
        self.assertIsNotNone(rsi11)

        # Dataset 12: Numerical stability on penny ($0.05) and mega ($500,000) stocks
        penny_df, mega_df = dataset_12_extreme_pathological(80)
        penny_rsi = calculate_rsi(penny_df["CLOSE"], period=14).dropna()
        mega_rsi = calculate_rsi(mega_df["CLOSE"], period=14).dropna()
        self.assertTrue((penny_rsi >= 0.0).all() and (penny_rsi <= 100.0).all())
        self.assertTrue((mega_rsi >= 0.0).all() and (mega_rsi <= 100.0).all())


if __name__ == "__main__":
    unittest.main()
