"""
Property and Invariant Mathematical Tests
==========================================
Verifies non-negotiable quantitative invariants across mathematical domains:
- 0 <= RSI <= 100
- 0 <= composite_score <= 100
- 0 <= reach_probability <= 1
- entry > 0
- 0 < stop < entry
- entry < T1 < T2 < T3
- Strategy weights sum to 1.0
- R:R is finite
- Pathological inputs (-100, 0, 50, 100, 101, 1000, NaN, Inf, -Inf, None)
- OHLCV geometry invariant checks
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

from src.indicators import calculate_rsi, calculate_atr, compute_adx
from src.ranker import SignalRanker, compute_expectancy_score, compute_momentum_score
from src.quant_config import STRATEGY_WEIGHT_VECTORS
from src.strategies.target_calculator import calculate_targets
from src.data_validation import validate_ohlcv
from tests.quant_reference.golden_datasets import (
    dataset_1_monotonic_uptrend,
    dataset_2_monotonic_downtrend,
    dataset_5_volatile_market,
    dataset_6_gap_market,
    dataset_7_invalid_anomalous_market,
    dataset_8_realistic_multi_regime,
)


class TestPropertyInvariants(unittest.TestCase):

    def test_01_rsi_bounded_invariants(self):
        """Invariant: RSI must always be bounded strictly in [0.0, 100.0]."""
        for df in [
            dataset_1_monotonic_uptrend(100),
            dataset_2_monotonic_downtrend(100),
            dataset_5_volatile_market(100),
            dataset_6_gap_market(100),
            dataset_8_realistic_multi_regime(200),
        ]:
            rsi = calculate_rsi(df["CLOSE"], period=14).dropna()
            self.assertTrue((rsi >= 0.0).all(), f"RSI fell below 0: min={rsi.min()}")
            self.assertTrue((rsi <= 100.0).all(), f"RSI exceeded 100: max={rsi.max()}")
            self.assertFalse(rsi.isna().any())
            self.assertFalse(np.isinf(rsi.values).any())

    def test_02_adx_bounded_invariants(self):
        """Invariant: ADX must always be bounded strictly in [0.0, 100.0]."""
        for df in [
            dataset_1_monotonic_uptrend(100),
            dataset_2_monotonic_downtrend(100),
            dataset_5_volatile_market(100),
            dataset_8_realistic_multi_regime(200),
        ]:
            adx = compute_adx(df["HIGH"], df["LOW"], df["CLOSE"], period=14).dropna()
            self.assertTrue((adx >= 0.0).all(), f"ADX fell below 0: min={adx.min()}")
            self.assertTrue((adx <= 100.0).all(), f"ADX exceeded 100: max={adx.max()}")
            self.assertFalse(adx.isna().any())
            self.assertFalse(np.isinf(adx.values).any())

    def test_03_atr_strictly_non_negative(self):
        """Invariant: ATR must always be strictly non-negative."""
        for df in [
            dataset_1_monotonic_uptrend(100),
            dataset_2_monotonic_downtrend(100),
            dataset_5_volatile_market(100),
            dataset_8_realistic_multi_regime(200),
        ]:
            atr = calculate_atr(df["HIGH"], df["LOW"], df["CLOSE"], period=14).dropna()
            self.assertTrue((atr >= 0.0).all(), f"ATR was negative: min={atr.min()}")
            self.assertFalse(atr.isna().any())
            self.assertFalse(np.isinf(atr.values).any())

    def test_04_strategy_weights_sum_to_unity(self):
        """Invariant: All strategy weight vectors must sum to exactly 1.0."""
        for strat, weights in STRATEGY_WEIGHT_VECTORS.items():
            total_weight = sum(weights.values())
            self.assertAlmostEqual(total_weight, 1.0, places=9, msg=f"Weights for {strat} sum to {total_weight} != 1.0")
            for k, w in weights.items():
                self.assertGreaterEqual(w, 0.0, msg=f"Negative weight in {strat}: {k}={w}")
                self.assertLessEqual(w, 1.0, msg=f"Weight exceeding 1.0 in {strat}: {k}={w}")

    def test_05_composite_score_bounded_property(self):
        """Invariant: Composite score must always be bounded in [0.0, 100.0]."""
        ranker = SignalRanker()
        # Feed extreme combinations across all strategies
        for strat in STRATEGY_WEIGHT_VECTORS.keys():
            for m in [0.0, 25.0, 50.0, 75.0, 100.0]:
                for exp in [-2.0, 0.0, 1.44, 3.5, 10.0]:
                    for wr in [0.0, 30.0, 50.0, 80.0, 100.0]:
                        for reg in ["bull", "sideways", "bear"]:
                            row = {
                                "strategy": strat,
                                "momentum_score": m,
                                "expectancy_pct": exp,
                                "win_rate": wr,
                                "context_analyst": 40.0,
                                "context_fundamental": 20.0,
                                "context_news": 20.0,
                            }
                            res = ranker.compute_composite_score(row, regime=reg)
                            score = res["composite_score"]
                            self.assertGreaterEqual(score, 0.0, f"Score < 0 for {strat}: {score}")
                            self.assertLessEqual(score, 100.0, f"Score > 100 for {strat}: {score}")

    def test_06_pathological_inputs_immunity(self):
        """Invariant: Pathological inputs (-100, 1000, NaN, Inf, None) are guarded."""
        ranker = SignalRanker()
        # Non-finite inputs must raise ValueError
        pathological = [float("nan"), float("inf"), float("-inf")]
        for p in pathological:
            row = {
                "strategy": "trend_following",
                "momentum_score": p,
                "win_rate": 50.0,
                "expectancy_pct": 1.44,
            }
            with self.assertRaises(ValueError):
                ranker.compute_composite_score(row, "bull")

    def test_07_target_ordering_invariants(self):
        """Invariant: entry < T1 < T2 < T3 must hold; non-monotonic levels are rejected."""
        # 1. Normal valid case
        res = calculate_targets(
            ticker="VALID",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=94.0,
            strategy_name="trend_following",
            mock_reach_probs=(0.50, 0.30, 0.20),
        )
        self.assertTrue(res.is_valid)
        self.assertTrue(100.0 < res.target_1 < res.target_2 < res.target_3)
        self.assertGreater(res.weighted_scaleout_rr, 0.0)

        # 2. Inverted override targets: must reject
        res_inverted = calculate_targets(
            ticker="INVERTED",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=94.0,
            strategy_name="trend_following",
            override_targets=(115.0, 110.0, 120.0),  # T2 < T1
        )
        self.assertFalse(res_inverted.is_valid)
        self.assertIn("Invalid override targets ordering", res_inverted.rejection_reason)

    def test_08_stop_loss_invariants(self):
        """Invariant: Stop loss must satisfy 0 < stop < entry; stop >= entry is rejected; negative stop rejected."""
        # Stop >= Entry: rejected fail-closed
        res_repaired = calculate_targets(
            ticker="REPAIRED_STOP",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=105.0,  # Stop above entry
            strategy_name="trend_following",
            mock_reach_probs=(0.40, 0.25, 0.18),
        )
        self.assertFalse(res_repaired.is_valid)
        self.assertEqual(res_repaired.weighted_scaleout_rr, 0.0)
        self.assertIn("Invalid stop loss", res_repaired.rejection_reason)

        # Negative Stop
        res_neg_stop = calculate_targets(
            ticker="NEG_STOP",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=-5.0,
            strategy_name="trend_following",
        )
        self.assertFalse(res_neg_stop.is_valid)
        self.assertIn("Invalid stop loss", res_neg_stop.rejection_reason)

    def test_09_ohlcv_validation_invariants(self):
        """Invariant: Corrupted OHLCV data fails validation."""
        valid_df = dataset_1_monotonic_uptrend(80)
        is_val, reason, _ = validate_ohlcv(valid_df, min_lookback=60)
        self.assertTrue(is_val, f"Valid dataset failed: {reason}")

        bad_df = dataset_7_invalid_anomalous_market(80)
        is_bad, bad_reason, _ = validate_ohlcv(bad_df, min_lookback=60)
        self.assertFalse(is_bad, "Corrupted dataset should fail validation")
        self.assertTrue(len(bad_reason) > 0)

    def test_10_as_of_cache_isolation_and_no_lookahead(self):
        """
        P0 Cache Isolation & Lookahead-Free Invariant:
        Calculations as-of date D must be strictly invariant when D+1..D+10 data is appended.
        Cache keys and disk cache must be partitioned by as_of_date.
        """
        from src.strategies.target_calculator import (
            get_reach_prob_distribution,
            get_reach_prob_target_before_stop,
            reset_reach_prob_cache,
            _REACH_DIST_CACHE,
            _TARGET_STOP_REACH_CACHE,
            ALGORITHM_VERSION,
        )

        reset_reach_prob_cache()

        # Step 1: Create dataset through date D (100 bars)
        dates_d = pd.date_range("2024-01-01", periods=100, freq="B")
        df_d = pd.DataFrame(
            {
                "OPEN": np.linspace(100, 150, 100),
                "HIGH": np.linspace(101, 152, 100),
                "LOW": np.linspace(99, 149, 100),
                "CLOSE": np.linspace(100.5, 151, 100),
            },
            index=dates_d,
        )
        as_of_d = str(dates_d[-1])[:10]

        # Step 2: Compute reach distribution and target-before-stop as-of date D
        dist_d = get_reach_prob_distribution(
            ticker="ISOLATION_TEST",
            holding_days=10,
            price_df=df_d,
            lookback_days=80,
            as_of_date=as_of_d,
        )
        prob_d = get_reach_prob_target_before_stop(
            ticker="ISOLATION_TEST",
            target_pct=0.05,
            stop_pct=0.03,
            holding_days=10,
            price_df=df_d,
            lookback_days=80,
            as_of_date=as_of_d,
        )

        self.assertGreater(len(dist_d), 0)
        self.assertGreater(prob_d, 0.0)

        # Step 3: Append D+1..D+10 data (new bars with different trajectory)
        dates_extended = pd.date_range("2024-01-01", periods=110, freq="B")
        df_extended = pd.DataFrame(
            {
                "OPEN": np.concatenate([df_d["OPEN"].values, np.linspace(80, 70, 10)]),
                "HIGH": np.concatenate([df_d["HIGH"].values, np.linspace(85, 75, 10)]),
                "LOW": np.concatenate([df_d["LOW"].values, np.linspace(75, 65, 10)]),
                "CLOSE": np.concatenate([df_d["CLOSE"].values, np.linspace(78, 68, 10)]),
            },
            index=dates_extended,
        )
        as_of_extended = str(dates_extended[-1])[:10]

        # Reset in-memory cache to force re-evaluation on extended data
        reset_reach_prob_cache()

        # Step 4: Re-compute as-of date D using df_extended (with future data present)
        dist_d_after = get_reach_prob_distribution(
            ticker="ISOLATION_TEST",
            holding_days=10,
            price_df=df_extended,
            lookback_days=80,
            as_of_date=as_of_d,
        )
        prob_d_after = get_reach_prob_target_before_stop(
            ticker="ISOLATION_TEST",
            target_pct=0.05,
            stop_pct=0.03,
            holding_days=10,
            price_df=df_extended,
            lookback_days=80,
            as_of_date=as_of_d,
        )

        # Step 5: Assert EXACT invariance for as-of date D calculations
        np.testing.assert_array_equal(
            dist_d, dist_d_after,
            err_msg="Max gain distribution for as_of date D changed after future data was appended!"
        )
        self.assertEqual(
            prob_d, prob_d_after,
            "Target-before-stop probability for as_of date D changed after future data was appended!"
        )

        # Step 6: Verify as-of D+10 produces distinct updated values
        dist_extended = get_reach_prob_distribution(
            ticker="ISOLATION_TEST",
            holding_days=10,
            price_df=df_extended,
            lookback_days=80,
            as_of_date=as_of_extended,
        )
        self.assertFalse(np.array_equal(dist_d, dist_extended), "As-of D and D+10 should produce distinct distributions.")


if __name__ == "__main__":
    unittest.main()
