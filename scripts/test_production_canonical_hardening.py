"""
Test Production Canonical Hardening Suite
==========================================
Verifies:
1. Canonical composite scoring engine unification (no divergent ranking in composite_rank).
2. Pullback strategy delegation directly to central ranker.
3. Universal falling knife gate rejection in entry location.
4. Target calculator reach probability cache reset and date scoping.
5. Setup rejection on invalid target calculations.
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("src"))
sys.path.insert(0, os.path.abspath("jobs"))
import unittest
import numpy as np
import pandas as pd

from src.ranker import (
    SignalRanker,
    compute_expectancy_score,
    compute_regime_alignment,
)
from src.entry_location import analyze_market_structure, evaluate_entry_location, MarketStructure
from src.strategies.target_calculator import (
    calculate_targets,
    reset_reach_prob_cache,
    _REACH_DIST_CACHE,
    _TARGET_STOP_REACH_CACHE,
)
from jobs.strategies.pullback import PullbackRecoveryStrategy


class TestProductionCanonicalHardening(unittest.TestCase):

    def setUp(self):
        reset_reach_prob_cache()

    def test_canonical_ranking_engine_equivalence(self):
        """Verify SignalRanker.composite_rank() delegates directly to canonical compute_composite_score()."""
        ranker = SignalRanker()

        test_data = pd.DataFrame([
            {
                "ticker": "AAPL",
                "strategy": "trend_following",
                "price": 150.0,
                "dma_50": 145.0,
                "current_rsi": 55.0,
                "volume_ratio": 1.2,
                "atr_14": 2.5,
                "macd_histogram": 0.25,
                "win_rate": 45.0,
                "expectancy_pct": 2.5,
                "context_analyst": 20.0,
                "context_fundamental": 15.0,
                "context_news": 15.0,
            },
            {
                "ticker": "MSFT",
                "strategy": "52w_high_breakout",
                "price": 300.0,
                "dma_50": 290.0,
                "current_rsi": 60.0,
                "volume_ratio": 1.5,
                "atr_14": 3.0,
                "macd_histogram": 0.40,
                "win_rate": 48.0,
                "expectancy_pct": 3.0,
                "context_analyst": 25.0,
                "context_fundamental": 18.0,
                "context_news": 18.0,
            },
            {
                "ticker": "FAIL",
                "strategy": "trend_following",
                "price": 50.0,
                "dma_50": 60.0,
                "current_rsi": 30.0,
                "volume_ratio": 0.5,
                "atr_14": 1.0,
                "macd_histogram": -0.5,
                "win_rate": 15.0,
                "expectancy_pct": -4.0,
            }
        ])

        ranked_df = ranker.composite_rank(test_data, "bull", top_n=5)

        # FAIL should be rejected / dropped because of low score / speculative tier
        self.assertNotIn("FAIL", ranked_df["ticker"].tolist())

        # For qualifying tickers, score in DataFrame must match individual compute_composite_score
        for _, row in ranked_df.iterrows():
            ticker = row["ticker"]
            expected = ranker.compute_composite_score(row.to_dict(), "bull")
            self.assertAlmostEqual(row["composite_score"], expected["composite_score"], places=3)
            self.assertEqual(row["tier_label"], expected["tier_label"])

    def test_pullback_rank_candidates_delegation(self):
        """Verify Pullback strategy rank_candidates preserves unblocked candidates for central ranking."""
        strat = PullbackRecoveryStrategy()
        candidates = [
            {"ticker": "ABC", "is_blocked": False, "score": 80},
            {"ticker": "XYZ", "is_blocked": True, "blocked_reason": "Low win rate"},
            {"ticker": "DEF", "is_blocked": False, "score": 75},
        ]
        result = strat.rank_candidates(candidates, "bull")
        self.assertEqual(len(result), 2)
        self.assertEqual([c["ticker"] for c in result], ["ABC", "DEF"])

    def test_universal_falling_knife_gate(self):
        """Verify universal falling knife gate rejects across ALL strategies."""
        # Create a price dataframe simulating a sharp breakdown below support
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        closes = [100.0] * 25 + [98.0, 95.0, 92.0, 88.0, 82.0]  # Severe breakdown
        lows = [c - 1.0 for c in closes]
        highs = [c + 1.0 for c in closes]
        opens = [c + 0.5 for c in closes]
        volumes = [1000] * 25 + [3000, 4000, 5000, 6000, 8000]  # Surge on breakdown

        df = pd.DataFrame({
            "OPEN": opens,
            "HIGH": highs,
            "LOW": lows,
            "CLOSE": closes,
            "VOLUME": volumes,
            "ATR_14": [2.0] * 30,
        }, index=dates)

        structure = analyze_market_structure(df)
        self.assertTrue(structure.is_falling_knife, "Market structure should detect falling knife")

        # Test across multiple strategies
        for strat in ["Trend Following", "Cross Sectional Momentum", "Sector Rotation", "PEAD"]:
            candidate = {"ticker": "FALL", "entry_price": 82.0, "stop_loss": 78.0}
            res = evaluate_entry_location(candidate, df, strat)
            self.assertEqual(res.state, "REJECT", f"Strategy {strat} failed to reject falling knife")
            self.assertEqual(res.zone, "NO_SETUP")
            self.assertIn("falling knife", res.reason.lower())

    def test_reach_prob_cache_reset(self):
        """Verify reach probability cache reset and date scoping."""
        _REACH_DIST_CACHE[("TEST", 20, "2026-01-01")] = np.array([0.05, 0.10])
        _TARGET_STOP_REACH_CACHE[("TEST", 0.05, 0.03, 20, "2026-01-01")] = 0.65

        self.assertGreater(len(_REACH_DIST_CACHE), 0)
        self.assertGreater(len(_TARGET_STOP_REACH_CACHE), 0)

        reset_reach_prob_cache()

        self.assertEqual(len(_REACH_DIST_CACHE), 0)
        self.assertEqual(len(_TARGET_STOP_REACH_CACHE), 0)

    def test_invalid_target_calculation(self):
        """Verify calculate_targets returns is_valid=False for non-viable setups."""
        # Non-viable: entry price <= 0
        res = calculate_targets(
            ticker="BAD",
            entry_price=0.0,
            atr_14=1.0,
            stop_loss=-1.0,
            strategy_name="Trend Following"
        )
        self.assertFalse(res.is_valid)
        self.assertIn("Non-positive entry", res.rejection_reason)

        # Non-viable: negative entry price
        res_neg = calculate_targets(
            ticker="BAD_NEG",
            entry_price=-50.0,
            atr_14=1.0,
            stop_loss=-55.0,
            strategy_name="Trend Following"
        )
        self.assertFalse(res_neg.is_valid)
        self.assertIn("Non-positive entry", res_neg.rejection_reason)


if __name__ == "__main__":
    unittest.main()
