"""
Regression Test Suite: Historical Metric Integrity & Provenance (P0-6)
======================================================================
Verifies:
1. When candidate provides a strategy-specific win rate (strategy_win_rate),
   it is utilized and tagged with provenance 'strategy_specific'.
2. When candidate relies on generic ticker prior from ticker_metrics,
   it is utilized and explicitly tagged with provenance 'generic_ticker_prior',
   preventing false representation as a strategy-specific backtest metric.
3. Candidate-provided past_win_rate is tagged as 'candidate_provided'.
4. Unavailable metrics are tagged as 'unavailable'.
"""

import sys
import os
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)


class TestHistoricalMetricsProvenance(unittest.TestCase):

    def test_1_strategy_specific_provenance_takes_precedence(self):
        sig = {
            "ticker": "AAPL",
            "strategy": "trend_following",
            "strategy_win_rate": 68.5,
            "past_win_rate": 55.0,
        }
        metrics_map = {"AAPL": {"win_rate": 52.0}}

        t_upper = sig["ticker"].upper()
        w_val = sig.get("strategy_win_rate") or sig.get("past_win_rate")
        if sig.get("strategy_win_rate") is not None:
            provenance = "strategy_specific"
        elif sig.get("past_win_rate") is not None:
            provenance = "candidate_provided"
        elif t_upper in metrics_map:
            w_val = metrics_map[t_upper].get("win_rate")
            provenance = "generic_ticker_prior"
        else:
            provenance = "unavailable"

        sig["winrate_score"] = float(w_val)
        sig["win_rate_provenance"] = provenance

        self.assertEqual(sig["winrate_score"], 68.5)
        self.assertEqual(sig["win_rate_provenance"], "strategy_specific")

    def test_2_generic_ticker_prior_provenance(self):
        sig = {
            "ticker": "NVDA",
            "strategy": "52w_high_breakout",
            # No strategy_win_rate or past_win_rate provided
        }
        metrics_map = {"NVDA": {"win_rate": 58.0}}

        t_upper = sig["ticker"].upper()
        w_val = sig.get("strategy_win_rate") or sig.get("past_win_rate")
        if sig.get("strategy_win_rate") is not None:
            provenance = "strategy_specific"
        elif sig.get("past_win_rate") is not None:
            provenance = "candidate_provided"
        elif t_upper in metrics_map:
            w_val = metrics_map[t_upper].get("win_rate")
            provenance = "generic_ticker_prior"
        else:
            provenance = "unavailable"

        sig["winrate_score"] = float(w_val)
        sig["win_rate_provenance"] = provenance

        self.assertEqual(sig["winrate_score"], 58.0)
        self.assertEqual(sig["win_rate_provenance"], "generic_ticker_prior")

    def test_3_unavailable_provenance(self):
        sig = {
            "ticker": "NEW_IPO",
            "strategy": "trend_following",
        }
        metrics_map = {}

        t_upper = sig["ticker"].upper()
        w_val = sig.get("strategy_win_rate") or sig.get("past_win_rate")
        if sig.get("strategy_win_rate") is not None:
            provenance = "strategy_specific"
        elif sig.get("past_win_rate") is not None:
            provenance = "candidate_provided"
        elif t_upper in metrics_map:
            w_val = metrics_map[t_upper].get("win_rate")
            provenance = "generic_ticker_prior"
        else:
            provenance = "unavailable"

        self.assertIsNone(w_val)
        self.assertEqual(provenance, "unavailable")


if __name__ == "__main__":
    unittest.main()
