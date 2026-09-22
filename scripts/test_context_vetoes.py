"""
Regression Test Suite: Context Veto Data Mapping (P0-2) & Data Quality (P1-4)
=============================================================================
Verifies:
CASE A: D/E = 3.0, Current Ratio = 0.8 => Veto fires (cap context at 30.0)
CASE B: D/E = 3.0, Current Ratio = 1.2 => That specific veto does not fire
CASE C: FinBERT = -0.31 => Veto fires (cap context at 40.0)
CASE D: FinBERT = -0.29 => That specific veto does not fire
CASE E: Earnings surprise = -10.1% => Veto fires (penalty -20.0)
CASE F: Earnings surprise = -9.9% => That specific veto does not fire
Missing values: Missing data receives explicit UNAVAILABLE status, 0 bonus points,
                and does not fire false vetoes.
End-to-End: Proves candidate dict with nested context data mapping properly reaches
            SignalRanker.compute_composite_score without being overwritten.
"""

import sys
import os
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.ranker import compute_context_score, SignalRanker
from src.providers.base import (
    DataQuality,
    AnalystContext,
    FundamentalContext,
    EarningsContext,
    NewsContext,
    AggregatedContext,
)


class TestContextVetoes(unittest.TestCase):

    def setUp(self):
        self.ranker = SignalRanker()

    def test_case_a_leverage_liquidity_veto_fires(self):
        # Base context points = 60.0. D/E = 3.0 (>2.5) AND CR = 0.8 (<1.0)
        score = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            de_ratio=3.0,
            current_ratio=0.8,
        )
        self.assertEqual(score, 30.0, "D/E > 2.5 and CR < 1.0 must cap context score at 30.0")

    def test_case_b_leverage_only_veto_does_not_fire(self):
        # Base context points = 60.0. D/E = 3.0 (>2.5) BUT CR = 1.2 (>=1.0)
        score = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            de_ratio=3.0,
            current_ratio=1.2,
        )
        self.assertEqual(score, 60.0, "D/E > 2.5 with CR >= 1.0 must NOT trigger balance sheet cap")

    def test_case_c_finbert_negative_sentiment_veto_fires(self):
        # Base context points = 60.0. FinBERT = -0.31 (<-0.30)
        score = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            finbert_sentiment=-0.31,
        )
        self.assertEqual(score, 40.0, "FinBERT < -0.30 must cap context score at 40.0")

    def test_case_d_finbert_borderline_sentiment_veto_does_not_fire(self):
        # Base context points = 60.0. FinBERT = -0.29 (not < -0.30)
        score = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            finbert_sentiment=-0.29,
        )
        self.assertEqual(score, 60.0, "FinBERT >= -0.30 must NOT trigger news sentiment cap")

    def test_case_e_severe_earnings_miss_veto_fires(self):
        # Base context points = 60.0. Earnings surprise = -10.1% (<-10.0%)
        score = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            earnings_surprise_pct=-10.1,
        )
        self.assertEqual(score, 40.0, "Earnings surprise < -10.0% must apply a -20.0 penalty")

        # Decimal format test (-0.101)
        score_dec = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            earnings_surprise_pct=-0.101,
        )
        self.assertEqual(score_dec, 40.0, "Earnings surprise -0.101 must apply a -20.0 penalty")

    def test_case_f_borderline_earnings_miss_veto_does_not_fire(self):
        # Base context points = 60.0. Earnings surprise = -9.9% (not < -10.0%)
        score = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            earnings_surprise_pct=-9.9,
        )
        self.assertEqual(score, 60.0, "Earnings surprise >= -10.0% must NOT apply severe miss penalty")

    def test_missing_values_do_not_fire_false_vetoes(self):
        # All None
        score = compute_context_score(
            analyst_pts=20.0,
            earnings_pts=20.0,
            fundamental_pts=10.0,
            news_pts=10.0,
            de_ratio=None,
            current_ratio=None,
            earnings_surprise_pct=None,
            finbert_sentiment=None,
            target_consensus=None,
            price=100.0,
        )
        self.assertEqual(score, 60.0, "Missing data must not trigger false vetoes")

    def test_aggregated_context_properties_and_data_quality(self):
        ctx = AggregatedContext(
            analyst=AnalystContext(target_mean_price=150.0, quality=DataQuality.VALID),
            fundamental=FundamentalContext(debt_to_equity=3.0, current_ratio=0.8, quality=DataQuality.VALID),
            earnings=EarningsContext(surprise_percent=-10.5, quality=DataQuality.VALID),
            news=NewsContext(headline_sentiment=-0.35, quality=DataQuality.VALID),
            quality=DataQuality.VALID,
        )

        self.assertEqual(ctx.de_ratio, 3.0)
        self.assertEqual(ctx.current_ratio, 0.8)
        self.assertEqual(ctx.earnings_surprise_pct, -10.5)
        self.assertEqual(ctx.finbert_sentiment, -0.35)
        self.assertEqual(ctx.target_consensus, 150.0)
        self.assertEqual(ctx.quality, DataQuality.VALID)

    def test_end_to_end_composite_score_applies_vetoes(self):
        candidate_distressed = {
            "ticker": "DISTRESSED_CO",
            "strategy": "trend_following",
            "current_rsi": 55.0,
            "price": 100.0,
            "dma_50": 95.0,
            "volume_ratio": 1.2,
            "macd_histogram": 0.5,
            "winrate_score": 60.0,
            "context_score": 70.0,  # Uncapped pre-veto score
            "context_analyst": 20.0,
            "context_earnings": 20.0,
            "context_fundamental": 15.0,
            "context_news": 15.0,
            "de_ratio": 3.0,
            "current_ratio": 0.8,  # Triggers cap at 30.0!
        }
        res = self.ranker.compute_composite_score(candidate_distressed, "bull")
        # Context score in breakdown must be 30.0, NOT 70.0!
        self.assertEqual(res["breakdown"]["context"], 30.0, "Distressed balance sheet must cap context at 30.0 in composite score")


if __name__ == "__main__":
    unittest.main()
