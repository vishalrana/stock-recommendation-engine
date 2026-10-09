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
    assign_tier,
)
from src.indicators import (
    calculate_dma,
    calculate_rsi,
    calculate_atr,
    compute_adx,
    compute_macd,
    compute_ema,
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

    def test_invalid_atr_rejections(self):
        """Invariant: NaN, infinite, zero, and negative ATRs must fail closed with explicit reason."""
        invalid_atrs = [
            float("nan"),
            float("inf"),
            float("-inf"),
            0.0,
            -1.5,
            -0.0001,
        ]
        for bad_atr in invalid_atrs:
            res = calculate_targets(
                ticker="TEST",
                entry_price=100.0,
                atr_14=bad_atr,
                stop_loss=95.0,
                strategy_name="Trend Following",
            )
            self.assertFalse(res.is_valid, f"Expected invalid for bad ATR={bad_atr}")
            self.assertIsNone(res.target_1)
            self.assertIn("ATR", res.rejection_reason)

    def test_invalid_financial_inputs_rejections(self):
        """Invariant: Non-finite entry/stop, stop >= entry, or non-monotonic targets must reject."""
        # Non-positive or non-finite entry
        for bad_entry in [0.0, -10.0, float("nan"), float("inf")]:
            res = calculate_targets("TEST", bad_entry, 2.0, 95.0, "Trend Following")
            self.assertFalse(res.is_valid)
            self.assertIsNone(res.target_1)

        # Stop >= entry or stop <= 0
        for bad_stop in [100.0, 105.0, 0.0, -5.0, float("nan")]:
            res = calculate_targets("TEST", 100.0, 2.0, bad_stop, "Trend Following")
            self.assertFalse(res.is_valid)
            self.assertIsNone(res.target_1)

        # Non-monotonic or non-finite override targets
        bad_overrides = [
            (110.0, 105.0, 120.0),  # non-monotonic
            (110.0, 110.0, 120.0),  # equal targets
            (110.0, float("nan"), 130.0),  # NaN target
            (95.0, 110.0, 120.0),   # T1 below entry
        ]
        for ovr in bad_overrides:
            res = calculate_targets("TEST", 100.0, 2.0, 95.0, "Trend Following", override_targets=ovr)
            self.assertFalse(res.is_valid)

        # Non-finite mock reach probs
        bad_probs = [
            (float("nan"), 0.3, 0.2),
            (0.5, float("inf"), 0.2),
        ]
        for bp in bad_probs:
            res = calculate_targets("TEST", 100.0, 2.0, 95.0, "Trend Following", mock_reach_probs=bp)
            self.assertFalse(res.is_valid)

    def test_position_scale_out_tracker_lifecycle_and_invariants(self):
        """Invariant: PositionScaleOutTracker must strictly enforce weight conservation and closed trade immutability."""
        # 1. Full 3-target lifecycle (T1 -> T2 -> T3)
        tracker = PositionScaleOutTracker(
            entry_price=100.0,
            stop_loss=90.0,
            target_1=110.0,
            target_2=120.0,
            target_3=130.0,
        )
        self.assertEqual(tracker.state, PositionState.OPEN)
        self.assertEqual(tracker.realized_weight, 0.0)
        self.assertEqual(tracker.remaining_weight, 1.0)
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0)
        self.assertFalse(tracker.is_closed)

        # Hit T1
        tracker.on_t1_hit(110.0)
        self.assertEqual(tracker.state, PositionState.T1_HIT)
        self.assertEqual(tracker.realized_weight, 0.50)
        self.assertEqual(tracker.remaining_weight, 0.50)
        self.assertEqual(tracker.current_stop, 100.0)  # Ratchet to breakeven
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0)
        self.assertFalse(tracker.is_closed)

        # Hit T2
        tracker.on_t2_hit(120.0)
        self.assertEqual(tracker.state, PositionState.T2_HIT)
        self.assertEqual(tracker.realized_weight, 0.80)
        self.assertEqual(tracker.remaining_weight, 0.20)
        self.assertEqual(tracker.current_stop, 110.0)  # Ratchet to T1
        self.assertAlmostEqual(tracker.realized_weight + tracker.remaining_weight, 1.0)
        self.assertFalse(tracker.is_closed)

        # Hit T3
        tracker.on_t3_hit(130.0)
        self.assertEqual(tracker.state, PositionState.T3_HIT)
        self.assertEqual(tracker.realized_weight, 1.0)
        self.assertEqual(tracker.remaining_weight, 0.0)
        self.assertTrue(tracker.is_closed)
        # Expected return: 0.5 * 10% + 0.3 * 20% + 0.2 * 30% = 5 + 6 + 6 = 17.0%
        self.assertAlmostEqual(tracker.realized_return_pct, 17.0, places=4)

        # Calling any transition on closed tracker is a no-op (preserves closed state and return)
        tracker.on_stop_hit(90.0)
        self.assertEqual(tracker.state, PositionState.T3_HIT)
        self.assertEqual(tracker.realized_weight, 1.0)
        self.assertAlmostEqual(tracker.realized_return_pct, 17.0, places=4)

        tracker.on_t1_hit(110.0)
        self.assertEqual(tracker.realized_weight, 1.0)
        self.assertAlmostEqual(tracker.realized_return_pct, 17.0, places=4)

        tracker.on_expired(125.0)
        self.assertEqual(tracker.realized_weight, 1.0)
        self.assertAlmostEqual(tracker.realized_return_pct, 17.0, places=4)

        # 2. Stop before T1
        tracker_stop_early = PositionScaleOutTracker(
            entry_price=100.0,
            stop_loss=90.0,
            target_1=110.0,
            target_2=120.0,
            target_3=130.0,
        )
        tracker_stop_early.on_stop_hit(90.0)
        self.assertEqual(tracker_stop_early.state, PositionState.STOPPED)
        self.assertEqual(tracker_stop_early.realized_weight, 1.0)
        self.assertEqual(tracker_stop_early.remaining_weight, 0.0)
        self.assertTrue(tracker_stop_early.is_closed)
        self.assertAlmostEqual(tracker_stop_early.realized_return_pct, -10.0, places=4)

        # 3. Stop after T1 at breakeven
        tracker_stop_after_t1 = PositionScaleOutTracker(
            entry_price=100.0,
            stop_loss=90.0,
            target_1=110.0,
            target_2=120.0,
            target_3=130.0,
        )
        tracker_stop_after_t1.on_t1_hit(110.0)
        tracker_stop_after_t1.on_stop_hit(100.0)  # Breakeven stop hit
        self.assertEqual(tracker_stop_after_t1.state, PositionState.T1_HIT)
        self.assertEqual(tracker_stop_after_t1.realized_weight, 1.0)
        self.assertEqual(tracker_stop_after_t1.remaining_weight, 0.0)
        self.assertTrue(tracker_stop_after_t1.is_closed)
        # 0.50 * 10% + 0.50 * 0% = 5.0%
        self.assertAlmostEqual(tracker_stop_after_t1.realized_return_pct, 5.0, places=4)

        # 4. Stop after T2 at T1 level
        tracker_stop_after_t2 = PositionScaleOutTracker(
            entry_price=100.0,
            stop_loss=90.0,
            target_1=110.0,
            target_2=120.0,
            target_3=130.0,
        )
        tracker_stop_after_t2.on_t1_hit(110.0)
        tracker_stop_after_t2.on_t2_hit(120.0)
        tracker_stop_after_t2.on_stop_hit(110.0)  # Trailing stop at T1 hit
        self.assertEqual(tracker_stop_after_t2.state, PositionState.T2_HIT)
        self.assertEqual(tracker_stop_after_t2.realized_weight, 1.0)
        self.assertEqual(tracker_stop_after_t2.remaining_weight, 0.0)
        self.assertTrue(tracker_stop_after_t2.is_closed)
        # 0.50 * 10% + 0.30 * 20% + 0.20 * 10% = 5 + 6 + 2 = 13.0%
        self.assertAlmostEqual(tracker_stop_after_t2.realized_return_pct, 13.0, places=4)

        # 5. Idempotency guards (repeated target hit calls do not double count)
        tracker_idemp = PositionScaleOutTracker(
            entry_price=100.0,
            stop_loss=90.0,
            target_1=110.0,
            target_2=120.0,
            target_3=130.0,
        )
        tracker_idemp.on_t1_hit(110.0)
        self.assertEqual(tracker_idemp.realized_weight, 0.50)
        tracker_idemp.on_t1_hit(110.0)
        self.assertEqual(tracker_idemp.realized_weight, 0.50)  # Unchanged

    def test_independent_clean_room_indicator_reference_math(self):
        """Clean-room mathematical verification of technical indicators from first principles."""
        # Generate deterministic synthetic price series (50 bars)
        np.random.seed(42)
        base_price = 100.0
        returns = np.array([
            0.01, -0.005, 0.015, -0.02, 0.008, 0.012, -0.003, 0.007,
            -0.01, 0.018, -0.015, 0.005, 0.02, -0.008, 0.011, 0.003,
            -0.012, 0.015, -0.005, 0.008, 0.014, -0.022, 0.009, 0.004,
            -0.007, 0.013, -0.011, 0.016, -0.004, 0.01, 0.002, -0.018,
            0.014, -0.006, 0.012, 0.005, -0.009, 0.017, -0.013, 0.006,
            0.015, -0.008, 0.01, 0.003, -0.014, 0.018, -0.007, 0.011, 0.002, -0.005
        ])
        closes = [base_price]
        for r in returns:
            closes.append(closes[-1] * (1.0 + r))
        closes = np.array(closes)
        highs = closes * 1.015
        lows = closes * 0.985
        df = pd.DataFrame({"CLOSE": closes, "HIGH": highs, "LOW": lows})

        # --- A. Clean-room DMA ---
        for period in [10, 20]:
            dma_actual = calculate_dma(df["CLOSE"], period)
            for i in range(period - 1, len(closes)):
                expected_mean = np.mean(closes[i - period + 1 : i + 1])
                self.assertAlmostEqual(dma_actual.iloc[i], expected_mean, places=6)

        # --- B. Clean-room EMA ---
        span = 12
        alpha = 2.0 / (span + 1.0)
        ema_actual = compute_ema(df["CLOSE"], span)
        expected_ema = closes[0]
        self.assertAlmostEqual(ema_actual.iloc[0], expected_ema, places=6)
        for i in range(1, len(closes)):
            expected_ema = alpha * closes[i] + (1.0 - alpha) * expected_ema
            self.assertAlmostEqual(ema_actual.iloc[i], expected_ema, places=6)

        # --- C. Clean-room RSI (Wilder's RMA) ---
        rsi_actual = calculate_rsi(df["CLOSE"], 14)
        period = 14
        deltas = np.diff(closes)
        gains = np.maximum(deltas, 0.0)
        losses = np.maximum(-deltas, 0.0)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        rs = avg_gain / avg_loss if avg_loss > 0 else 0
        expected_rsi = 100.0 - (100.0 / (1.0 + rs))
        self.assertAlmostEqual(rsi_actual.iloc[period], expected_rsi, places=6)
        for i in range(period + 1, len(closes)):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            rs = avg_gain / avg_loss if avg_loss > 0 else 0
            expected_rsi = 100.0 - (100.0 / (1.0 + rs))
            self.assertAlmostEqual(rsi_actual.iloc[i], expected_rsi, places=6)

        # --- D. Clean-room ATR (Wilder's RMA) ---
        atr_actual = calculate_atr(df["HIGH"], df["LOW"], df["CLOSE"], 14)
        tr = [highs[0] - lows[0]]
        for i in range(1, len(closes)):
            tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        tr = np.array(tr)
        expected_atr = np.mean(tr[:period])
        self.assertAlmostEqual(atr_actual.iloc[period - 1], expected_atr, places=6)
        for i in range(period, len(closes)):
            expected_atr = ((period - 1) * expected_atr + tr[i]) / period
            self.assertAlmostEqual(atr_actual.iloc[i], expected_atr, places=6)

        # --- E. Clean-room MACD ---
        macd_line, sig_line, hist = compute_macd(df["CLOSE"], fast=12, slow=26, signal=9)
        # Check that histogram == macd_line - signal_line for every element
        for i in range(len(closes)):
            self.assertAlmostEqual(hist.iloc[i], macd_line.iloc[i] - sig_line.iloc[i], places=6)

        # --- F. Clean-room ADX ---
        adx_actual = compute_adx(df["HIGH"], df["LOW"], df["CLOSE"], 14)
        # Verify ADX values are finite in [0, 100] once seeded
        seed_idx = 2 * 14 - 1
        for i in range(seed_idx, len(closes)):
            val = adx_actual.iloc[i]
            self.assertTrue(math.isfinite(val))
            self.assertTrue(0.0 <= val <= 100.0)

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
        self.assertEqual(scored["total"], 60.2741)
        self.assertEqual(scored["composite_score"], 60.2741)
        self.assertEqual(scored["tier_label"], "Rejected")
        self.assertEqual(scored["strategy"], "cross_sectional_momentum")
        self.assertEqual(scored["weights"], {"mom": 0.4, "exp": 0.2, "wr": 0.2, "reg": 0.1, "ctx": 0.1})
        self.assertEqual(scored["breakdown"]["momentum"], 61.6352)
        self.assertEqual(scored["breakdown"]["expectancy"], 60.6)
        self.assertEqual(scored["breakdown"]["winrate"], 50.0)
        self.assertEqual(scored["breakdown"]["regime"], 85.0)  # Discrete matrix for cross_sectional in bull
        self.assertEqual(scored["breakdown"]["context"], 50.0)

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
        self.assertEqual(res.target_1, 311.23)
        self.assertEqual(res.target_2, 329.02)
        self.assertEqual(res.target_3, 349.76)
        self.assertEqual(res.reach_prob_t1, 0.45)
        self.assertEqual(res.reach_prob_t2, 0.25)
        self.assertEqual(res.reach_prob_t3, 0.18)
        self.assertEqual(res.scale_out_weights, "50/30/20")
        self.assertEqual(res.weighted_scaleout_rr, 2.4)
        self.assertEqual(res.weighted_rr_honest, 2.4)


if __name__ == "__main__":
    unittest.main()
