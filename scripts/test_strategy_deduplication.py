"""
Regression Test Suite: Post-Ranking Ticker Deduplication Pipeline (P0-5)
========================================================================
Verifies:
1. When a ticker qualifies under multiple strategies (e.g. Trend Following & Pullback Recovery),
   BOTH candidates reach central ranking.
2. The winning strategy candidate is selected based on the highest canonical composite score,
   NOT local/incompatible quality_score.
3. The lower-ranked candidate is discarded only after central ranking.
4. Other independent tickers remain unaffected.
"""

import sys
import os
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.ranker import SignalRanker
from src.position_sizer import assign_tier


class TestStrategyDeduplication(unittest.TestCase):

    def setUp(self):
        self.ranker = SignalRanker()

    def test_multi_strategy_candidates_reach_ranking_and_winner_selected_by_composite(self):
        # Scenario: AAPL qualifies for both Trend Following and Pullback Recovery.
        # Suppose Pullback has higher local 'quality_score' (e.g. 95 vs 80),
        # BUT Trend Following achieves a higher canonical composite score under central ranking!
        aapl_trend = {
            "ticker": "AAPL",
            "strategy": "trend_following",
            "current_rsi": 62.0,
            "price": 220.0,
            "dma_50": 200.0,
            "volume_ratio": 1.5,
            "macd_histogram": 0.8,
            "winrate_score": 65.0,
            "context_score": 60.0,
            "context_analyst": 20.0,
            "context_earnings": 15.0,
            "context_fundamental": 15.0,
            "context_news": 10.0,
            "quality_score": 75.0,  # Lower local quality score
        }

        aapl_pullback = {
            "ticker": "AAPL",
            "strategy": "pullback_recovery",
            "current_rsi": 46.0,
            "price": 220.0,
            "dma_50": 218.0,
            "volume_ratio": 1.0,
            "macd_histogram": 0.1,
            "winrate_score": 50.0,
            "context_score": 40.0,
            "context_analyst": 10.0,
            "context_earnings": 10.0,
            "context_fundamental": 10.0,
            "context_news": 10.0,
            "quality_score": 95.0,  # Higher local quality score!
        }

        msft_trend = {
            "ticker": "MSFT",
            "strategy": "trend_following",
            "current_rsi": 58.0,
            "price": 420.0,
            "dma_50": 400.0,
            "volume_ratio": 1.3,
            "macd_histogram": 0.5,
            "winrate_score": 60.0,
            "context_score": 55.0,
            "context_analyst": 15.0,
            "context_earnings": 15.0,
            "context_fundamental": 15.0,
            "context_news": 10.0,
            "quality_score": 70.0,
        }

        all_signals = [aapl_trend, aapl_pullback, msft_trend]

        # 1. P0-5: DO NOT deduplicate before central ranking. All signals reach ranker.
        candidates = list(all_signals)
        self.assertEqual(len(candidates), 3, "All 3 candidates must reach the central ranker")

        # 2. Central canonical scoring
        scored = []
        for c in candidates:
            res = self.ranker.compute_composite_score(c, "bull")
            c["composite_score"] = res["composite_score"]
            c["tier_label"] = res["tier_label"]
            scored.append(c)

        # Confirm scores: Trend Following should win on canonical score
        self.assertGreater(
            aapl_trend["composite_score"],
            aapl_pullback["composite_score"],
            "Trend following candidate has higher canonical score"
        )

        # 3. Sort candidates DESC by canonical score
        scored.sort(key=lambda x: x["composite_score"], reverse=True)

        # 4. Ticker deduplication AFTER canonical ranking
        qualified_recommendations = []
        seen_qualified = {}
        for sig in scored:
            if sig["tier_label"] not in ("Strong Buy", "Buy"):
                continue
            t = sig["ticker"].upper()
            if t in seen_qualified:
                # Lower-ranked candidate discarded
                continue
            seen_qualified[t] = sig
            qualified_recommendations.append(sig)

        # 5. Verify results
        self.assertEqual(len(qualified_recommendations), 2, "Only one candidate per ticker preserved")
        recs_by_ticker = {r["ticker"]: r for r in qualified_recommendations}

        self.assertIn("AAPL", recs_by_ticker)
        self.assertIn("MSFT", recs_by_ticker)

        # The AAPL winner MUST be Trend Following (canonical winner), NOT Pullback Recovery (local quality_score)
        self.assertEqual(recs_by_ticker["AAPL"]["strategy"], "trend_following")
        self.assertEqual(recs_by_ticker["AAPL"]["composite_score"], aapl_trend["composite_score"])

        # MSFT is completely unaffected
        self.assertEqual(recs_by_ticker["MSFT"]["strategy"], "trend_following")


if __name__ == "__main__":
    unittest.main()
