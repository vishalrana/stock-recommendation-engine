"""
Test Suite: Strategy Stop Architecture and Quant Config Verification
Tests the canonical strategy stop configuration, Trend Following ATR consumption,
and that each strategy's own structural stop is used as issued (no floor, no cap).
"""

import sys
import os
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

import src.quant_config as quant_config
from src.quant_config import STRATEGY_STOP_CONFIG, normalize_strategy_key
from jobs.strategies.trend_following import TrendFollowingStrategy


class TestStopArchitecture(unittest.TestCase):
    def test_canonical_stop_config_structure(self):
        """Verify STRATEGY_STOP_CONFIG contains all canonical strategies and values."""
        expected_keys = {
            "trend_following",
            "52w_high_breakout",
            "pullback_recovery",
            "pead",
            "cross_sectional_momentum",
            "sector_rotation",
            "mean_reversion",
        }
        self.assertEqual(set(STRATEGY_STOP_CONFIG.keys()), expected_keys)

        expected_mult = {
            "trend_following": 2.5, "52w_high_breakout": 2.0, "pullback_recovery": 1.5, "pead": 2.0,
            "cross_sectional_momentum": 2.0, "sector_rotation": 1.8, "mean_reversion": 1.0,
        }
        for key, mult in expected_mult.items():
            self.assertEqual(STRATEGY_STOP_CONFIG[key]["atr_multiplier"], mult)
            # No minimum-distance floor: the strategy's structural stop is used as issued
            self.assertNotIn("stop_floor", STRATEGY_STOP_CONFIG[key])

        # No maximum-risk clamp
        self.assertFalse(hasattr(quant_config, "MAX_STOP_LOSS_PCT"))

    def test_normalize_strategy_key(self):
        """Verify normalize_strategy_key handles various naming styles."""
        self.assertEqual(normalize_strategy_key("Trend Following"), "trend_following")
        self.assertEqual(normalize_strategy_key("trend_following"), "trend_following")
        self.assertEqual(normalize_strategy_key("52-Week High"), "52w_high_breakout")
        self.assertEqual(normalize_strategy_key("52_week_high_breakout"), "52w_high_breakout")
        self.assertEqual(normalize_strategy_key("Pullback Recovery"), "pullback_recovery")
        self.assertEqual(normalize_strategy_key("PEAD"), "pead")
        self.assertEqual(normalize_strategy_key("Cross-Sectional Momentum"), "cross_sectional_momentum")
        self.assertEqual(normalize_strategy_key("Sector Rotation"), "sector_rotation")
        self.assertEqual(normalize_strategy_key("Mean Reversion"), "mean_reversion")

    def test_trend_following_consumes_centralized_atr_multiplier(self):
        """Verify TrendFollowingStrategy stop loss calculation uses the centralized ATR multiplier."""
        strategy = TrendFollowingStrategy()

        # Build synthetic DataFrame with 250 bars
        dates = pd.date_range("2025-01-01", periods=250, freq="D")
        close_prices = np.linspace(100, 200, 250)
        df = pd.DataFrame({
            "CLOSE": close_prices,
            "HIGH": close_prices * 1.02,
            "LOW": close_prices * 0.98,
            "VOLUME": [1000000] * 250,
            "DMA_50": close_prices * 0.95,
            "DMA_200": close_prices * 0.90,
            "RSI_14": [60.0] * 250,
            "ADX_14": [30.0] * 250,
            "MACD_HIST": [0.5] * 250,
            "ATR_14": [4.0] * 250,
            "volume_ratio": [1.5] * 250,
        }, index=dates)

        price = df["CLOSE"].iloc[-1]  # 200.0
        low_10 = df["LOW"].iloc[-10:].min()
        atr = 4.0
        expected_atr_mult = STRATEGY_STOP_CONFIG["trend_following"]["atr_multiplier"]  # 2.5
        expected_stop = min(low_10, price - expected_atr_mult * atr)

        signal = strategy.scan("TEST", df, "bull", {})
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal["stop_loss"], expected_stop, places=2)

    def test_trade_plan_uses_strategy_stop_unchanged(self):
        """The trade plan keeps a wide (12%) and a tight (3%) structural stop exactly as issued,
        and its R:R is measured against that stop."""
        from src.pipeline_steps import build_trade_plan
        for stop in (88.0, 97.0):
            sig = {"ticker": "STOPX", "strategy": "Trend Following", "entry_price": 100.0,
                   "stop_loss": stop, "atr_14": 2.0}
            calc = build_trade_plan(sig, None)
            self.assertTrue(calc.is_valid)
            self.assertEqual(sig["stop_loss"], stop)
            risk = 100.0 - stop
            weighted_reward = calc.weighted_scaleout_rr * risk
            self.assertGreater(weighted_reward, 0)


if __name__ == "__main__":
    unittest.main()
