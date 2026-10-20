"""
Live and backtest composite scores are the same rule (parity, 2026-10-20): for identical features
the live scan's score equals the backtest harness's score, whatever context the live scan fetched.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs.generate_signals import _attach_context, _mark_context_unavailable
from scripts.validate_backtest_pipeline import apply_backtest_context
from src import quant_config as qc
from src.pipeline_steps import composite_score, prepare_candidate_scores, selection_settings
from src.ranker import SignalRanker
from src.scorers.context_scorer import ContextScorer


def candidate(strategy: str) -> dict:
    return {"ticker": "TEST", "strategy": strategy, "scan_date": "2026-10-16", "price": 50.0, "entry_price": 50.0,
            "stop_loss": 46.0, "atr_14": 1.4, "current_rsi": 61.0, "dma_50": 47.5, "volume_ratio": 1.3,
            "macd_histogram": 0.12, "adx_value": 27.0}


def live_score(strategy: str, context: tuple) -> float:
    """The live path: shared feature scores, context fetched and stored for display, then scored."""
    settings = selection_settings()
    sig = candidate(strategy)
    prepare_candidate_scores(sig, "bull", settings=settings)
    if context is None:
        _mark_context_unavailable(sig)
    else:
        _attach_context(sig, context, in_score=bool(settings.context_components))
    assert composite_score(sig, "bull", SignalRanker()) is None
    return sig["composite_score"]


def backtest_score(strategy: str) -> float:
    """The harness path (scripts/validate_backtest_pipeline.py): no context history."""
    settings = selection_settings()
    sig = candidate(strategy)
    prepare_candidate_scores(sig, "bull", settings=settings)
    apply_backtest_context(sig, settings, None, ContextScorer(), "2026-10-16", _mark_context_unavailable)
    assert composite_score(sig, "bull", SignalRanker()) is None
    return sig["composite_score"]


# (context_score, analyst, earnings, fundamental, news, de, cr, surprise, finbert, target, pe, max_points)
PERFECT = (100.0, 40.0, 0.0, 0.0, 20.0, 0.4, 2.0, None, 0.9, 80.0, 15.0, 60.0)
WEAK = (37.5, 15.0, 0.0, 0.0, -10.0, 0.4, 2.0, None, -0.6, 49.0, 15.0, 60.0)


class TestScoreParity(unittest.TestCase):
    def test_production_scores_without_context(self):
        self.assertEqual(tuple(qc.CONTEXT_SCORE_COMPONENTS), ())
        self.assertEqual(tuple(qc.CONTEXT_DISPLAY_COMPONENTS), ("analyst", "news"))

    def test_live_equals_backtest_for_identical_features(self):
        for strategy in ("Trend Following", "Cross-Sectional Momentum", "Pullback Recovery", "Post-Earnings Drift"):
            bt = backtest_score(strategy)
            for ctx in (PERFECT, WEAK, None):
                self.assertAlmostEqual(live_score(strategy, ctx), bt, places=9, msg=f"{strategy} context {ctx and ctx[0]}")

    def test_display_fields_are_kept(self):
        settings = selection_settings()
        sig = candidate("Trend Following")
        prepare_candidate_scores(sig, "bull", settings=settings)
        _attach_context(sig, PERFECT, in_score=False)
        self.assertEqual((sig["context_score"], sig["finbert_sentiment"], sig["target_consensus"]), (100.0, 0.9, 80.0))
        self.assertIs(sig["context_available"], False)

    def test_context_would_move_the_score_if_switched_on(self):
        """Sanity: the parity above is not vacuous; with context in the score the two paths differ."""
        settings = selection_settings()
        sig = candidate("Trend Following")
        prepare_candidate_scores(sig, "bull", settings=settings)
        _attach_context(sig, WEAK, in_score=True)
        composite_score(sig, "bull", SignalRanker())
        self.assertNotAlmostEqual(sig["composite_score"], backtest_score("Trend Following"), places=3)


if __name__ == "__main__":
    unittest.main()
