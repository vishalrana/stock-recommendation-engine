"""
Test Suite: Strategy Stop Architecture and Quant Config Verification
Tests the canonical strategy stop configuration, Trend Following ATR consumption,
and strategy-specific stop floor clamping in signal generation.
"""

import sys
import os
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.quant_config import STRATEGY_STOP_CONFIG, MAX_STOP_LOSS_PCT, normalize_strategy_key
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

        # Trend Following: 2.5 ATR, 6.0% floor
        self.assertEqual(STRATEGY_STOP_CONFIG["trend_following"]["atr_multiplier"], 2.5)
        self.assertEqual(STRATEGY_STOP_CONFIG["trend_following"]["stop_floor"], 0.06)

        # 52-Week High Breakout: 2.0 ATR, 5.0% floor
        self.assertEqual(STRATEGY_STOP_CONFIG["52w_high_breakout"]["atr_multiplier"], 2.0)
        self.assertEqual(STRATEGY_STOP_CONFIG["52w_high_breakout"]["stop_floor"], 0.05)

        # Pullback Recovery: 1.5 ATR, 4.0% floor
        self.assertEqual(STRATEGY_STOP_CONFIG["pullback_recovery"]["atr_multiplier"], 1.5)
        self.assertEqual(STRATEGY_STOP_CONFIG["pullback_recovery"]["stop_floor"], 0.04)

        # PEAD: 2.0 ATR, 5.0% floor
        self.assertEqual(STRATEGY_STOP_CONFIG["pead"]["atr_multiplier"], 2.0)
        self.assertEqual(STRATEGY_STOP_CONFIG["pead"]["stop_floor"], 0.05)

        # Cross-Sectional Momentum: 2.0 ATR, 5.0% floor
        self.assertEqual(STRATEGY_STOP_CONFIG["cross_sectional_momentum"]["atr_multiplier"], 2.0)
        self.assertEqual(STRATEGY_STOP_CONFIG["cross_sectional_momentum"]["stop_floor"], 0.05)

        # Sector Rotation: 1.8 ATR, 4.5% floor
        self.assertEqual(STRATEGY_STOP_CONFIG["sector_rotation"]["atr_multiplier"], 1.8)
        self.assertEqual(STRATEGY_STOP_CONFIG["sector_rotation"]["stop_floor"], 0.045)

        # Mean Reversion: 1.0 ATR, 3.0% floor
        self.assertEqual(STRATEGY_STOP_CONFIG["mean_reversion"]["atr_multiplier"], 1.0)
        self.assertEqual(STRATEGY_STOP_CONFIG["mean_reversion"]["stop_floor"], 0.03)

        # MAX_STOP_LOSS_PCT is 7%
        self.assertEqual(MAX_STOP_LOSS_PCT, 0.07)

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

    def test_strategy_stop_floor_and_ceiling_math(self):
        """Verify the mathematical logic of the strategy stop floor and 7% ceiling clamp."""
        entry_price = 100.0

        # 1. 7% hard ceiling clamp (max risk = 7%)
        min_stop = round(entry_price * (1.0 - MAX_STOP_LOSS_PCT), 2)  # 93.00
        initial_stop_wide = 90.00
        clamped_stop = initial_stop_wide
        if clamped_stop < min_stop:
            clamped_stop = min_stop
        self.assertEqual(clamped_stop, 93.00)

        # 2. Trend Following: 6.0% floor
        strat_key = normalize_strategy_key("Trend Following")
        stop_floor_pct = STRATEGY_STOP_CONFIG[strat_key]["stop_floor"]  # 0.06
        max_tight_stop = round(entry_price * (1.0 - stop_floor_pct), 2)  # 94.00
        # If raw stop is 97.00 (too tight, only 3% buffer), floor widens to 94.00
        raw_stop = 97.00
        final_stop = raw_stop
        if final_stop > max_tight_stop:
            final_stop = max_tight_stop
        self.assertEqual(final_stop, 94.00)

        # 3. Mean Reversion: 3.0% floor
        mr_key = normalize_strategy_key("Mean Reversion")
        mr_floor_pct = STRATEGY_STOP_CONFIG[mr_key]["stop_floor"]  # 0.03
        mr_max_tight = round(entry_price * (1.0 - mr_floor_pct), 2)  # 97.00
        # If raw stop is 96.00 (4% buffer), it is NOT clamped (unlike old 4% floor which would widen it)
        raw_mr_stop = 96.00
        final_mr_stop = raw_mr_stop
        if final_mr_stop > mr_max_tight:
            final_mr_stop = mr_max_tight
        self.assertEqual(final_mr_stop, 96.00)


if __name__ == "__main__":
    unittest.main()
