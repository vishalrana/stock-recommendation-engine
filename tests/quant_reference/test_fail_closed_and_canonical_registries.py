"""
Clean-Room Mathematical Reference & Invariant Test Suite
=========================================================
Validates:
1. Canonical Strategy Registry fail-closed invariants (Section 3).
2. Canonical Regime Registry fail-closed invariants (Section 4).
3. Bad Feature Fallback elimination: DMA50, MACD histogram, RSI, ATR (Section 6 & 8).
4. Scale-Out runner accounting & weight conservation state machine (Section 16 & 17).
5. Open-gap event resolution and deterministic STOP_FIRST policy (Section 12).
6. CRL deterministic quantitative regression baseline (Section 28).
"""

import math
import sys
import os
import unittest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.quant_config import (
    CANONICAL_STRATEGIES,
    CANONICAL_REGIMES,
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
    SCALE_OUT_WEIGHTS,
    normalize_strategy_key,
    normalize_regime_key,
)
from src.ranker import (
    compute_regime_alignment,
    compute_momentum_score,
    validate_candidate_features,
    SignalRanker,
)
from src.strategies.target_calculator import (
    calculate_targets,
    resolve_bar_event,
)
from src.outcome.outcome_calculator import (
    PositionState,
    PositionScaleOutTracker,
    get_scale_out_plan_weights,
    evaluate_signal_outcome,
)


class TestFailClosedAndCanonicalRegistries(unittest.TestCase):

    def test_canonical_strategy_registry_fail_closed(self):
        """Invariant: Unknown strategy names must raise ValueError. Substring heuristics are forbidden."""
        # 1. Exactly 7 canonical strategies
        self.assertEqual(len(CANONICAL_STRATEGIES), 7)
        for s in CANONICAL_STRATEGIES:
            self.assertEqual(normalize_strategy_key(s), s)

        # 2. Supported aliases normalize correctly
        self.assertEqual(normalize_strategy_key("Trend Following"), "trend_following")
        self.assertEqual(normalize_strategy_key("52-Week High"), "52w_high_breakout")
        self.assertEqual(normalize_strategy_key("Cross-Sectional Momentum"), "cross_sectional_momentum")
        self.assertEqual(normalize_strategy_key("PEAD"), "pead")
        self.assertEqual(normalize_strategy_key("Post-Earnings Drift"), "pead")
        self.assertEqual(normalize_strategy_key("Pullback Recovery"), "pullback_recovery")
        self.assertEqual(normalize_strategy_key("Sector Rotation"), "sector_rotation")
        self.assertEqual(normalize_strategy_key("Mean Reversion"), "mean_reversion")

        # 3. Substring heuristics must NOT match unknown strategies
        with self.assertRaises(ValueError):
            normalize_strategy_key("momentum")  # substring heuristic forbidden

        with self.assertRaises(ValueError):
            normalize_strategy_key("high")

        with self.assertRaises(ValueError):
            normalize_strategy_key("arbitrary_alpha_strategy")

        with self.assertRaises(ValueError):
            normalize_strategy_key(None)

    def test_canonical_regime_registry_fail_closed(self):
        """Invariant: Unknown market regimes must raise ValueError. No silent fallback to sideways."""
        self.assertEqual(CANONICAL_REGIMES, {"bull", "sideways", "bear"})
        self.assertEqual(normalize_regime_key("bull"), "bull")
        self.assertEqual(normalize_regime_key("BULL"), "bull")
        self.assertEqual(normalize_regime_key(" Sideways "), "sideways")
        self.assertEqual(normalize_regime_key("BEAR"), "bear")

        # Unknown regimes must raise
        with self.assertRaises(ValueError):
            normalize_regime_key("hyper_bull")

        with self.assertRaises(ValueError):
            normalize_regime_key(None)

        with self.assertRaises(ValueError):
            normalize_regime_key("")

        # compute_regime_alignment must fail closed on invalid inputs
        with self.assertRaises(ValueError):
            compute_regime_alignment("trend_following", "unknown_regime")

        with self.assertRaises(ValueError):
            compute_regime_alignment("unknown_strategy", "bull")

    def test_bad_feature_fallbacks_eliminated(self):
        """Invariant: DMA50, MACD histogram, RSI, ATR cannot be silently defaulted or substituted."""
        valid_row = {
            "ticker": "AAPL",
            "strategy": "trend_following",
            "current_rsi": 55.0,
            "price": 150.0,
            "dma_50": 140.0,
            "volume_ratio": 1.2,
            "macd_histogram": 0.5,
            "atr_14": 3.0,
            "winrate_score": 60.0,
        }

        # Valid row computes finite momentum score
        score = compute_momentum_score(valid_row)
        self.assertTrue(0.0 <= score <= 100.0)

        # 1. Missing DMA50 must raise and reject (no sma50 or ema20 fallback)
        row_no_dma = dict(valid_row, dma_50=None, sma50=140.0, ema20=145.0)
        with self.assertRaises(ValueError):
            compute_momentum_score(row_no_dma)
        is_val, reason = validate_candidate_features(row_no_dma)
        self.assertFalse(is_val)
        self.assertIn("dma_50", reason)

        # 2. Missing MACD histogram must raise and reject (cannot become 0.0)
        row_no_macd = dict(valid_row, macd_histogram=None)
        with self.assertRaises(ValueError):
            compute_momentum_score(row_no_macd)
        is_val, reason = validate_candidate_features(row_no_macd)
        self.assertFalse(is_val)
        self.assertIn("macd_histogram", reason)

        # 3. Missing RSI must raise and reject
        row_no_rsi = dict(valid_row, current_rsi=None)
        with self.assertRaises(ValueError):
            compute_momentum_score(row_no_rsi)
        is_val, reason = validate_candidate_features(row_no_rsi)
        self.assertFalse(is_val)

        # 4. Non-positive ATR must raise and reject
        row_bad_atr = dict(valid_row, atr_14=0.0)
        with self.assertRaises(ValueError):
            compute_momentum_score(row_bad_atr)
        is_val, reason = validate_candidate_features(row_bad_atr)
        self.assertFalse(is_val)

    def test_scale_out_runner_accounting_and_weight_conservation(self):
        """Invariant: Position weights must sum to exactly 1.0 across every state transition."""
        # 1. Plan weights for t1_only include 30% runner
        w1, w2, w3, runner = get_scale_out_plan_weights(target_1=110.0, target_2=None, target_3=None)
        self.assertEqual(w1, 0.70)
        self.assertEqual(w2, 0.0)
        self.assertEqual(w3, 0.0)
        self.assertEqual(runner, 0.30)
        self.assertAlmostEqual(w1 + w2 + w3 + runner, 1.0, places=6)

        # 2. State machine tracks 30% runner without weight leakage
        tracker = PositionScaleOutTracker(
            entry_price=100.0,
            stop_loss=95.0,
            target_1=110.0,
            target_2=None,
            target_3=None,
        )
        self.assertEqual(tracker.state, PositionState.OPEN)
        self.assertEqual(tracker.realized_weight, 0.0)
        self.assertEqual(tracker.remaining_weight, 1.0)

        # Step A: T1 Hit
        tracker.on_t1_hit(110.0)
        self.assertEqual(tracker.state, PositionState.T1_HIT)
        self.assertEqual(tracker.realized_weight, 0.70)
        self.assertEqual(tracker.remaining_weight, 0.30)
        self.assertEqual(tracker.current_stop, 100.0)  # Breakeven ratchet
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0, places=6)

        # Step B: Runner stopped out at Breakeven
        tracker.on_stop_hit(100.0)
        self.assertEqual(tracker.realized_weight, 1.0)
        self.assertEqual(tracker.remaining_weight, 0.0)
        self.assertAlmostEqual(tracker.realized_return_pct, 7.0, places=4)  # 0.70 * 10% + 0.30 * 0% = 7.0%

    def test_canonical_event_resolver_gap_down(self):
        """Invariant: Open gaps below stop execute at open_price (capturing slippage)."""
        stop_hit, target_hit, exit_p = resolve_bar_event(
            open_price=90.0,
            high_price=92.0,
            low_price=89.0,
            close_price=91.0,
            stop_price=95.0,
            target_price=110.0,
            ambiguity_policy="STOP_FIRST",
        )
        self.assertTrue(stop_hit)
        self.assertFalse(target_hit)
        self.assertEqual(exit_p, 90.0)  # Slipped to Open

    def test_crl_deterministic_regression_baseline(self):
        """CRL regression benchmark: deterministic output with identical inputs."""
        ranker = SignalRanker()
        crl_candidate = {
            "ticker": "CRL",
            "strategy": "Cross-Sectional Momentum",
            "strategy_name": "Cross-Sectional Momentum",
            "price": 296.41,
            "entry_price": 296.41,
            "dma_50": 280.0,
            "volume_ratio": 1.25,
            "current_rsi": 58.5,
            "adx_value": 24.0,
            "macd_histogram": 0.35,
            "atr_14": 5.80,
            "winrate_score": 50.0,
            "context_score": 50.0,
            "expectancy_pct": 1.53,
        }

        # 1. Validation passes
        is_valid, msg = validate_candidate_features(crl_candidate)
        self.assertTrue(is_valid, f"CRL candidate validation failed: {msg}")

        # 2. Composite score calculation
        scored = ranker.compute_composite_score(crl_candidate, regime="bull")
        self.assertTrue(0.0 <= scored["total"] <= 100.0)
        self.assertIn("momentum", scored["breakdown"])
        self.assertIn("expectancy", scored["breakdown"])
        self.assertIn("regime", scored["breakdown"])
        self.assertEqual(scored["breakdown"]["regime"], 85.0)  # Discrete matrix for cross_sectional in bull

        # 3. Target calculation
        res = calculate_targets(
            ticker="CRL",
            entry_price=296.41,
            atr_14=5.80,
            stop_loss=284.81,
            strategy_name="Cross-Sectional Momentum",
            mock_reach_probs=(0.45, 0.25, 0.18),
        )
        self.assertTrue(res.is_valid)
        self.assertTrue(296.41 < res.target_1 < res.target_2 < res.target_3)
        self.assertEqual(res.scale_out_weights, "50/30/20")
        self.assertTrue(res.weighted_rr_honest > 1.5)


if __name__ == "__main__":
    unittest.main()
