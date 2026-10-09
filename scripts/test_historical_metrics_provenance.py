"""
Regression Test Suite: Historical Metric Integrity & Provenance (P0-6 & Phase 7)
================================================================================
Directly tests production `build_hardened_metrics` from `src.utils.metrics_pipeline`
across all 7 required provenance scenarios:
1. Strategy precedence: strategy_win_rate takes precedence over candidate and ticker priors.
2. Candidate provided: past_win_rate is utilized when strategy_win_rate is absent.
3. Generic ticker prior: ticker win_rate prior is utilized when candidate priors are absent.
4. Unavailable: unseeded default prior when no metrics exist.
5. Legitimate zero: win_rate = 0.0 is preserved faithfully as 0.0 (not inflated to 50.0).
6. Non-finite values: NaN and infinity inputs fail closed without corrupting calculations.
7. Correct source tags: metric_source, source, confidence, and sample_size tagging.
"""

import sys
import os
import unittest
import math
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.utils.metrics_pipeline import build_hardened_metrics


class TestHistoricalMetricsProvenance(unittest.TestCase):

    def test_1_strategy_specific_precedence(self):
        """Strategy-specific win rate must take precedence over candidate and generic ticker priors."""
        m = build_hardened_metrics(
            ticker="AAPL",
            raw_record={"win_rate": 52.0, "wins": 0, "losses": 0},
            strategy_name="trend_following",
            strategy_win_rate=68.5,
            past_win_rate=55.0,
        )
        self.assertEqual(m["shrunk_win_rate"], 68.5)
        self.assertEqual(m["prior"], 68.5)
        self.assertEqual(m["provenance"], "strategy_prior")
        self.assertEqual(m["metric_source"], "strategy_backtest")
        self.assertEqual(m["sample_size"], 0)

    def test_2_candidate_provided_precedence(self):
        """Candidate-provided past win rate is utilized when strategy_win_rate is omitted."""
        m = build_hardened_metrics(
            ticker="MSFT",
            raw_record={"win_rate": 48.0, "wins": 0, "losses": 0},
            strategy_name="pullback_recovery",
            past_win_rate=61.0,
        )
        self.assertEqual(m["shrunk_win_rate"], 61.0)
        self.assertEqual(m["prior"], 61.0)
        self.assertEqual(m["provenance"], "candidate_provided")
        self.assertEqual(m["metric_source"], "candidate_payload")
        self.assertEqual(m["sample_size"], 0)

    def test_3_generic_ticker_prior(self):
        """Generic ticker prior is utilized when candidate does not supply priors."""
        m = build_hardened_metrics(
            ticker="NVDA",
            raw_record={"win_rate": 58.0, "wins": 0, "losses": 0},
            strategy_name="52w_high_breakout",
        )
        self.assertEqual(m["shrunk_win_rate"], 58.0)
        self.assertEqual(m["prior"], 58.0)
        self.assertEqual(m["provenance"], "generic_ticker_prior")
        self.assertEqual(m["metric_source"], "ticker_metrics")
        self.assertEqual(m["sample_size"], 0)

    def test_4_unavailable_unseeded(self):
        """Unavailable unseeded candidate defaults safely to neutral 50% prior."""
        m = build_hardened_metrics(
            ticker="NEW_IPO",
            raw_record=None,
            strategy_name="trend_following",
        )
        self.assertEqual(m["shrunk_win_rate"], 50.0)
        self.assertEqual(m["prior"], 50.0)
        self.assertEqual(m["provenance"], "unavailable")
        self.assertEqual(m["metric_source"], "unseeded_prior")
        self.assertEqual(m["sample_size"], 0)

    def test_5_legitimate_zero_prior_preserved(self):
        """A legitimate 0.0% win rate prior must be preserved as 0.0% and NOT inflated to 50%."""
        m = build_hardened_metrics(
            ticker="DOG_STOCK",
            raw_record={"win_rate": 0.0, "wins": 0, "losses": 0},
            strategy_name="mean_reversion",
        )
        self.assertEqual(m["shrunk_win_rate"], 0.0)
        self.assertEqual(m["prior"], 0.0)
        self.assertEqual(m["provenance"], "generic_ticker_prior")
        self.assertEqual(m["metric_source"], "ticker_metrics")

    def test_6_non_finite_values_handled_fail_closed(self):
        """NaN and infinite priors must be caught safely and fallback to unavailable without crash or NaN propagation."""
        # Case A: NaN in strategy_win_rate
        m_nan = build_hardened_metrics(
            ticker="CORRUPT_A",
            raw_record={"win_rate": 55.0},
            strategy_win_rate=float("nan"),
        )
        # Should fallback to valid raw_record generic ticker prior
        self.assertEqual(m_nan["provenance"], "generic_ticker_prior")
        self.assertEqual(m_nan["shrunk_win_rate"], 55.0)

        # Case B: Inf in raw_record win_rate
        m_inf = build_hardened_metrics(
            ticker="CORRUPT_B",
            raw_record={"win_rate": float("inf")},
        )
        self.assertEqual(m_inf["provenance"], "unavailable")
        self.assertEqual(m_inf["shrunk_win_rate"], 50.0)

        # Case C: -Inf in past_win_rate
        m_neginf = build_hardened_metrics(
            ticker="CORRUPT_C",
            raw_record=None,
            past_win_rate=float("-inf"),
        )
        self.assertEqual(m_neginf["provenance"], "unavailable")
        self.assertEqual(m_neginf["shrunk_win_rate"], 50.0)

    def test_7_correct_source_tags_and_bayesian_posterior(self):
        """When empirical trades exist, posterior shrinkage combines prior with empirical evidence."""
        # 8 wins, 2 losses (raw = 80%), with 50% prior -> shrunk to 70.0%
        m = build_hardened_metrics(
            ticker="AAPL",
            raw_record={"wins": 8, "losses": 2},
            strategy_win_rate=60.0,
        )
        self.assertEqual(m["provenance"], "ticker_observed")
        self.assertEqual(m["metric_source"], "ticker_observed_with_strategy_prior")
        self.assertEqual(m["source"], "ticker_observed_with_strategy_prior")
        self.assertEqual(m["confidence"], "medium")
        self.assertEqual(m["sample_size"], 10)
        self.assertEqual(m["raw_win_rate"], 80.0)
        # Shrunk calculation: (8 * 100 + 5 * 60) / (10 + 5) = (800 + 300) / 15 = 1100 / 15 = 73.33%
        self.assertAlmostEqual(m["shrunk_win_rate"], 73.33, places=2)


if __name__ == "__main__":
    unittest.main()
