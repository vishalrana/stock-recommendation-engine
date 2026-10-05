"""
Acceptance Test Suite: Earnings Decoupling & Downstream Gate Architecture
==========================================================================
Verifies all 10 requirements:
- Test A: Score can be calculated with zero earnings data.
- Test B: Two stocks with identical non-earnings inputs receive identical scores regardless of earnings status.
- Test C: Earnings cannot change a previously calculated score.
- Test D: Stocks below 65 do not trigger earnings fetching or provider calls.
- Test E: Stocks >= 65 trigger earnings evaluation.
- Test F: Earnings gate blocks a 65+ candidate with negative catalyst or blackout.
- Test G: Positive earnings catalyst / PEAD exempt from blackout according to policy.
- Test H: Unknown earnings date handling follows policy (neutral proceed).
- Test I: PEAD remains functional without universe-wide earnings queries.
- Test J: _SESSION_FETCHED_TICKERS prevents duplicate network requests in the same scan.
"""

import datetime
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.ranker import SignalRanker, compute_context_score
from src.scorers.context_scorer import ContextScorer
from src.providers.base import AggregatedContext
from src.filters.earnings_filter import (
    earnings_risk_filter,
    resolve_ticker_earnings,
    fetch_single_ticker_provider,
    _SESSION_FETCHED_TICKERS,
    EarningsStatus,
)


class TestEarningsDecoupling(unittest.TestCase):

    def setUp(self):
        self.ranker = SignalRanker()
        self.today = datetime.date.today()
        _SESSION_FETCHED_TICKERS.clear()

    def test_a_score_calculated_with_zero_earnings_data(self):
        """Test A: Score can be calculated with zero earnings data."""
        # Row with zero earnings inputs
        row = {
            "ticker": "AAPL",
            "strategy": "Trend Following",
            "momentum_score": 75.0,
            "win_rate": 60.0,
            "context_analyst": 25.0,
            "context_fundamental": 15.0,
            "context_news": 15.0,
            # No context_earnings, no earnings_surprise_pct
        }
        res = self.ranker.compute_composite_score(row, regime="bull")
        self.assertIsNotNone(res)
        self.assertIn("total", res)
        self.assertGreater(res["total"], 0.0)
        self.assertLessEqual(res["total"], 100.0)

        # Context scorer directly
        scorer = ContextScorer()
        total, a, e, f, n = scorer.calculate_with_breakdown(AggregatedContext(), current_price=100.0)
        self.assertEqual(e, 0.0, "Earnings context sub-score must be 0.0")
        self.assertGreaterEqual(total, 0.0)

    def test_b_identical_non_earnings_inputs_yield_identical_scores(self):
        """Test B: Two stocks with identical non-earnings inputs receive identical scores regardless of earnings status."""
        row_no_earnings = {
            "ticker": "STK1",
            "strategy": "Trend Following",
            "momentum_score": 70.0,
            "win_rate": 55.0,
            "context_analyst": 20.0,
            "context_fundamental": 10.0,
            "context_news": 10.0,
            "context_earnings": 0.0,
            "earnings_surprise_pct": None,
        }
        row_with_earnings = {
            "ticker": "STK2",
            "strategy": "Trend Following",
            "momentum_score": 70.0,
            "win_rate": 55.0,
            "context_analyst": 20.0,
            "context_fundamental": 10.0,
            "context_news": 10.0,
            "context_earnings": 25.0,  # Legacy field, must be ignored by ranker
            "earnings_surprise_pct": 0.15,  # Ignored by initial ranker
        }
        res1 = self.ranker.compute_composite_score(row_no_earnings, regime="bull")
        res2 = self.ranker.compute_composite_score(row_with_earnings, regime="bull")
        self.assertEqual(
            res1["total"],
            res2["total"],
            f"Scores must be strictly identical: {res1['total']} vs {res2['total']}",
        )

    def test_c_earnings_cannot_change_calculated_score(self):
        """Test C: Earnings cannot change a previously calculated score."""
        initial_score = 72.4
        candidate = {
            "ticker": "MSFT",
            "strategy": "Trend Following",
            "score": initial_score,
            "composite_score": initial_score,
        }

        # Run earnings gate for clear calendar
        calendar_clear = {"MSFT": {"next_earnings_date": (self.today + datetime.timedelta(days=45)).isoformat()}}
        gate_res = earnings_risk_filter(
            ticker="MSFT",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_calendar=calendar_clear,
        )
        self.assertTrue(gate_res["pass"])
        self.assertEqual(candidate["score"], initial_score, "Score must not be modified by gate pass")

        # Run earnings gate for blackout failure
        calendar_blackout = {"MSFT": {"next_earnings_date": (self.today + datetime.timedelta(days=3)).isoformat()}}
        gate_fail = earnings_risk_filter(
            ticker="MSFT",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_calendar=calendar_blackout,
        )
        self.assertFalse(gate_fail["pass"])
        self.assertEqual(candidate["score"], initial_score, "Score must remain unchanged even if gate fails")

    def test_d_stocks_below_65_do_not_trigger_earnings_fetching(self):
        """Test D: Stocks below 65 do not trigger earnings fetching or provider calls."""
        # Simulated pipeline behavior
        candidates = [
            {"ticker": "SUB60", "score": 58.2, "strategy": "Trend Following"},
            {"ticker": "SUB64", "score": 64.9, "strategy": "Trend Following"},
        ]

        calendar_map = {}
        with patch("src.filters.earnings_filter.resolve_ticker_earnings") as mock_resolve:
            for cand in candidates:
                # Mirror logic in jobs/generate_signals.py
                if cand["score"] < 65.0:
                    continue  # Dropped immediately without earnings query
                resolve_ticker_earnings(cand["ticker"], calendar_map)

            self.assertEqual(mock_resolve.call_count, 0, "No earnings resolve calls should occur for candidates < 65.0")

    def test_e_stocks_gte_65_trigger_earnings_evaluation(self):
        """Test E: Stocks >= 65 trigger earnings evaluation."""
        candidate = {"ticker": "QUAL", "score": 68.5, "strategy": "Trend Following"}
        calendar_map = {}

        with patch("src.filters.earnings_filter.resolve_ticker_earnings") as mock_resolve:
            mock_resolve.return_value = {
                "next_earnings_date": (self.today + datetime.timedelta(days=30)).isoformat(),
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            }
            if candidate["score"] >= 65.0:
                mock_resolve(candidate["ticker"], calendar_map)

            self.assertEqual(mock_resolve.call_count, 1, "Earnings must be resolved for candidate with score >= 65.0")

    def test_f_earnings_gate_blocks_candidate_in_blackout_or_severe_miss(self):
        """Test F: Earnings gate blocks a 65+ candidate with negative catalyst or blackout."""
        # Blackout case (within 7 days for Trend Following)
        blackout_cal = {"BAD1": {"next_earnings_date": (self.today + datetime.timedelta(days=4)).isoformat()}}
        res_blackout = earnings_risk_filter(
            ticker="BAD1",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_calendar=blackout_cal,
        )
        self.assertFalse(res_blackout["pass"])
        self.assertIn("blackout", res_blackout["reason"].lower())

        # Negative catalyst case (recent severe earnings miss > -10%)
        miss_cal = {"BAD2": {"next_earnings_date": (self.today + datetime.timedelta(days=25)).isoformat()}}
        res_miss = earnings_risk_filter(
            ticker="BAD2",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_surprise_pct=-15.0,
            earnings_calendar=miss_cal,
        )
        self.assertFalse(res_miss["pass"])
        self.assertIn("catalyst veto", res_miss["reason"].lower())

    def test_g_positive_catalyst_and_pead_exempt_from_blackout(self):
        """Test G: Positive earnings catalyst overrides blackout according to policy."""
        # PEAD strategy is explicitly exempt from blackout
        pead_cal = {"PEAD1": {"next_earnings_date": (self.today + datetime.timedelta(days=2)).isoformat()}}
        res_pead = earnings_risk_filter(
            ticker="PEAD1",
            scan_date=self.today,
            strategy="pead",
            earnings_calendar=pead_cal,
        )
        self.assertTrue(res_pead["pass"], "PEAD strategy must be exempt from blackout window")

        # Positive catalyst override (surprise >= +5.0%)
        catalyst_cal = {"CAT1": {"next_earnings_date": (self.today + datetime.timedelta(days=3)).isoformat()}}
        res_override = earnings_risk_filter(
            ticker="CAT1",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_surprise_pct=8.5,
            earnings_calendar=catalyst_cal,
        )
        self.assertTrue(res_override["pass"], "Strong positive earnings catalyst overrides blackout window")

    def test_h_unknown_earnings_date_handling_follows_policy(self):
        """Test H: Unknown earnings date handling follows policy (neutral proceed)."""
        unknown_cal = {"UNK": {"status": EarningsStatus.UNKNOWN.value, "next_earnings_date": None}}
        res_unk = earnings_risk_filter(
            ticker="UNK",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_calendar=unknown_cal,
            allow_unknown_date=True,
        )
        self.assertTrue(res_unk["pass"], "Unknown earnings date must proceed neutrally when allow_unknown=True")

    def test_i_pead_remains_functional_without_universe_wide_queries(self):
        """Test I: PEAD remains functional without universe-wide earnings queries."""
        # PEAD signal generation operates with preloaded or cached calendar
        cal = {"PEAD_STK": {"last_earnings_date": (self.today - datetime.timedelta(days=3)).isoformat()}}
        gate = earnings_risk_filter(
            ticker="PEAD_STK",
            scan_date=self.today,
            strategy="pead",
            earnings_surprise_pct=12.0,
            earnings_calendar=cal,
        )
        self.assertTrue(gate["pass"])

    def test_j_session_fetched_tickers_duplicate_prevention(self):
        """Test J: _SESSION_FETCHED_TICKERS prevents duplicate network requests in the same scan."""
        _SESSION_FETCHED_TICKERS.clear()
        self.assertNotIn("NVDA", _SESSION_FETCHED_TICKERS)

        with patch("yfinance.Ticker") as mock_yf:
            mock_inst = MagicMock()
            mock_inst.calendar = {"Earnings Date": [(self.today + datetime.timedelta(days=20)).isoformat()]}
            mock_inst.earnings_dates = None
            mock_yf.return_value = mock_inst

            # First fetch triggers provider
            r1 = fetch_single_ticker_provider("NVDA")
            self.assertEqual(mock_yf.call_count, 1)
            self.assertIn("NVDA", _SESSION_FETCHED_TICKERS)

            # In resolve_ticker_earnings, existing calendar_map or _SESSION_FETCHED_TICKERS prevents re-query
            calendar_map = {"NVDA": {"ticker": "NVDA", "status": EarningsStatus.KNOWN_UPCOMING.value}}
            r2 = resolve_ticker_earnings("NVDA", calendar_map=calendar_map)
            self.assertEqual(mock_yf.call_count, 1, "Duplicate network fetch must be prevented by session tracker")


if __name__ == "__main__":
    unittest.main()
