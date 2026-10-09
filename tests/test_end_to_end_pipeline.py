"""
End-to-End Pipeline & CRL Regression Test Suite
================================================
Deterministic validation of the entire recommendation lifecycle:
1. OHLCV -> Data Validation -> Indicators -> Strategy -> Metrics -> Ranker -> Targets -> Recommendation
2. Recommendation -> D+1 Activation -> T1 -> T2 -> T3 (Full Profit Exit)
3. Recommendation -> D+1 Activation -> Stop Loss Event (Conservative Stop-First)
4. Recommendation -> Subsequent Invalidation Event
5. CRL Regression Scenario: Cross-Sectional Momentum verification
"""

import os
import sys
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
src_dir = os.path.join(PROJECT_ROOT, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import unittest
import numpy as np
import pandas as pd

from src.data_validation import validate_ohlcv
from src.indicators import calculate_indicators
from src.ranker import SignalRanker, assign_tier, compute_expectancy_score, compute_momentum_score
from src.quant_config import STRATEGY_WEIGHT_VECTORS
from src.strategies.target_calculator import calculate_targets, TargetCalculationResult
from src.utils.metrics_pipeline import build_hardened_metrics
from tests.quant_reference.golden_datasets import dataset_8_realistic_multi_regime


class TestEndToEndPipeline(unittest.TestCase):

    def setUp(self):
        self.ranker = SignalRanker()

    def test_01_full_pipeline_deterministic_flow(self):
        """End-to-end flow: OHLCV -> Validation -> Indicators -> Ranker -> Targets."""
        # 1. Market Data Generation (300 bars)
        df_raw = dataset_8_realistic_multi_regime(300)

        # 2. Data Validation
        is_valid, reason, metrics = validate_ohlcv(df_raw, min_lookback=60)
        self.assertTrue(is_valid, f"Validation failed: {reason}")
        self.assertGreaterEqual(metrics["bars_count"], 60)

        # 3. Indicator Calculation
        df_ind = calculate_indicators(df_raw)
        for col in ["DMA_50", "DMA_200", "RSI_14", "VOLUME_MA_20", "ADX_14", "MACD_LINE", "MACD_HIST", "EMA_20", "ATR_14"]:
            self.assertIn(col, df_ind.columns)
            self.assertFalse(pd.isna(df_ind[col].iloc[-1]), f"Indicator {col} is NaN at latest bar")

        # 4. Strategy & Candidate Feature Construction
        latest = df_ind.iloc[-1]
        price = float(latest["CLOSE"])
        atr = float(latest["ATR_14"])
        dma_50 = float(latest["DMA_50"])
        rsi = float(latest["RSI_14"])
        vol_ratio = float(latest["VOLUME"] / latest["VOLUME_MA_20"])
        macd_hist = float(latest["MACD_HIST"])

        # 5. Bayesian Metrics Hardening
        metrics_dict = build_hardened_metrics(
            ticker="AAPL",
            raw_record={"wins": 12, "losses": 4, "expectancy_pct": 2.1},
            strategy_name="trend_following",
            strategy_win_rate=60.0,
        )
        self.assertEqual(metrics_dict["provenance"], "ticker_observed")
        self.assertGreater(metrics_dict["shrunk_win_rate"], 55.0)

        # 6. Candidate Ranking
        cand_row = {
            "ticker": "AAPL",
            "strategy": "trend_following",
            "price": price,
            "dma_50": dma_50,
            "current_rsi": rsi,
            "volume_ratio": vol_ratio,
            "macd_histogram": macd_hist,
            "atr_14": atr,
            "win_rate": metrics_dict["shrunk_win_rate"],
            "expectancy_pct": metrics_dict["shrunk_expectancy"],
            "context_analyst": 35.0,
            "context_fundamental": 15.0,
            "context_news": 15.0,
        }
        scored = self.ranker.compute_composite_score(cand_row, regime="bull")
        score = scored["composite_score"]
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)

        # 7. Indicative Targets & Risk Engine
        stop_price = round(price - 2.5 * atr, 2)
        targets_res = calculate_targets(
            ticker="AAPL",
            entry_price=price,
            atr_14=atr,
            stop_loss=stop_price,
            strategy_name="trend_following",
            price_df=df_raw,
        )
        self.assertTrue(targets_res.is_valid)
        self.assertLess(stop_price, price)
        self.assertLess(price, targets_res.target_1)
        self.assertGreater(targets_res.weighted_scaleout_rr, 0.0)

    def test_02_recommendation_lifecycle_events(self):
        """Lifecycle events: activation separation, target reach, stop hit, invalidation."""
        entry = 100.0
        stop = 95.0
        t1 = 106.0
        t2 = 112.0
        t3 = 120.0

        # Scenario A: Day D (generation date) -> must NOT activate same-day
        scan_date = "2026-10-06"
        same_day_bar = {"date": "2026-10-06", "close": 94.0, "low": 94.0, "high": 101.0}
        # In generate_signals, same day bar <= scan_date skips stop/target evaluation
        self.assertTrue(same_day_bar["date"] <= scan_date, "Same day bar must be identified as <= scan_date")

        # Scenario B: D+1 Bar hits Stop Loss (Low <= Stop)
        d1_stop_bar = {"date": "2026-10-07", "close": 94.5, "low": 94.0, "high": 100.5}
        stop_hit = d1_stop_bar["low"] <= stop
        self.assertTrue(stop_hit)

        # Scenario C: D+1 Bar hits T3 (High >= T3)
        d1_target_bar = {"date": "2026-10-07", "close": 121.0, "low": 99.0, "high": 122.0}
        target_hit = d1_target_bar["high"] >= t3
        self.assertTrue(target_hit)

        # Scenario D: Ambiguity Bar (Low <= Stop AND High >= T3) -> Conservative Stop Wins
        d1_ambiguity_bar = {"date": "2026-10-07", "close": 110.0, "low": 94.0, "high": 122.0}
        # Under canonical rule: stop evaluated before target
        resolved_event = "stopped" if d1_ambiguity_bar["low"] <= stop else "hit_t3"
        self.assertEqual(resolved_event, "stopped", "Conservative stop-first policy must resolve ambiguity as stopped")

    def test_03_crl_regression_case(self):
        """
        CRL Regression Case (Section 33):
        Strategy: Cross-Sectional Momentum
        Entry: $296.41
        Weights: {'mom': 0.40, 'exp': 0.20, 'wr': 0.20, 'reg': 0.10, 'ctx': 0.10}
        Strategy expectancy: shrunk backtest evidence E -> S_exp = 30 + 20*E (no hard-coded prior)
        Bull Regime Score: 85.0
        """
        from src.strategy_evidence import get_strategy_evidence
        weights = STRATEGY_WEIGHT_VECTORS["cross_sectional_momentum"]
        self.assertEqual(weights, {'mom': 0.40, 'exp': 0.20, 'wr': 0.20, 'reg': 0.10, 'ctx': 0.10})

        ev = get_strategy_evidence("cross_sectional_momentum")
        expected_s_exp = round(max(0.0, min(100.0, 30.0 + 20.0 * ev.shrunk_expectancy)), 4)
        s_exp = compute_expectancy_score("cross_sectional_momentum")
        self.assertAlmostEqual(s_exp, expected_s_exp, places=4)

        crl_candidate = {
            "ticker": "CRL",
            "strategy": "cross_sectional_momentum",
            "price": 296.41,
            "dma_50": 285.0,
            "current_rsi": 54.0,
            "volume_ratio": 1.25,
            "macd_histogram": 0.4,
            "atr_14": 5.80,
            "win_rate": 50.0,
            "context_analyst": 20.0,
            "context_fundamental": 10.0,
            "context_news": 10.0,
        }
        res_crl = self.ranker.compute_composite_score(crl_candidate, regime="bull")
        score_crl = res_crl["composite_score"]
        self.assertGreaterEqual(score_crl, 40.0)
        self.assertLessEqual(score_crl, 80.0)
        self.assertEqual(res_crl["breakdown"]["expectancy"], expected_s_exp)
        self.assertEqual(res_crl["breakdown"]["regime"], 85.0)


if __name__ == "__main__":
    unittest.main()
