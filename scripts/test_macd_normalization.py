"""
Unit test suite for MACD normalization and RSI momentum curve (P1-1, P1-2, P1-3).
Verifies:
1. Price-scale invariance: $20 stock vs $500 stock with identical ATR-relative MACD moves produce identical scores.
2. RSI momentum curve rewards sweet spot [50, 70] and penalizes overbought (>75) and oversold (<40).
"""

import unittest
from src.ranker import compute_momentum_score, SignalRanker


class TestMACDNormalizationAndMomentum(unittest.TestCase):
    def test_macd_scale_invariance(self):
        """A $20 stock and a $500 stock with identical ATR-relative moves must score identically."""
        # Low price stock: $20, ATR = $0.40 (2%), MACD hist = $0.16 (0.4 ATR)
        low_price_stock = {
            "current_rsi": 58.0,
            "price": 20.0,
            "dma_50": 19.5,
            "volume_ratio": 1.5,
            "macd_histogram": 0.16,
            "atr_14": 0.40,
        }

        # High price stock: $500, ATR = $10.00 (2%), MACD hist = $4.00 (0.4 ATR)
        high_price_stock = {
            "current_rsi": 58.0,
            "price": 500.0,
            "dma_50": 487.5,
            "volume_ratio": 1.5,
            "macd_histogram": 4.00,
            "atr_14": 10.00,
        }

        score_low = compute_momentum_score(low_price_stock)
        score_high = compute_momentum_score(high_price_stock)

        self.assertAlmostEqual(score_low, score_high, places=2,
                               msg=f"Scores should match: low={score_low}, high={score_high}")

    def test_macd_directionality(self):
        """Positive MACD hist > zero > negative MACD hist."""
        base = {
            "current_rsi": 55.0,
            "price": 100.0,
            "dma_50": 98.0,
            "volume_ratio": 1.2,
            "atr_14": 2.0,
        }
        bull_row = dict(base, macd_histogram=1.0)
        flat_row = dict(base, macd_histogram=0.0)
        bear_row = dict(base, macd_histogram=-1.0)

        s_bull = compute_momentum_score(bull_row)
        s_flat = compute_momentum_score(flat_row)
        s_bear = compute_momentum_score(bear_row)

        self.assertGreater(s_bull, s_flat)
        self.assertGreater(s_flat, s_bear)

    def test_rsi_sweet_spot(self):
        """RSI in [50, 60] range should score higher than overbought (80) or oversold (20)."""
        base = {
            "price": 100.0,
            "dma_50": 98.0,
            "volume_ratio": 1.2,
            "macd_histogram": 0.5,
            "atr_14": 2.0,
        }
        rsi_median = compute_momentum_score(dict(base, current_rsi=50.0))
        rsi_pullback = compute_momentum_score(dict(base, current_rsi=58.0))
        rsi_overbought = compute_momentum_score(dict(base, current_rsi=80.0))
        rsi_oversold = compute_momentum_score(dict(base, current_rsi=20.0))

        self.assertGreater(rsi_median, rsi_overbought)
        self.assertGreater(rsi_pullback, rsi_overbought)
        self.assertGreater(rsi_pullback, rsi_oversold)


if __name__ == "__main__":
    unittest.main()
