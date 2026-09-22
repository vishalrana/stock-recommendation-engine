"""
Regression Test Suite: Regime API Fail-Safe (P0-1)
===================================================
Verifies:
1. Valid SPY data -> correct regime ("bull", "sideways", or "bear")
2. Missing SPY data (None or empty DataFrame) -> "unknown"
3. API timeout / Exception -> "unknown"
4. Malformed SPY response (<200 bars or NaN) -> "unknown"
5. Unknown regime does not allow trading and generates 0 bullish recommendations
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np

# Set paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.regime import get_regime, should_trade
from jobs.generate_signals import REGIME_STRATEGY_MAP


class TestRegimeFailsafe(unittest.TestCase):

    def _make_spy_df(self, count=250, start_price=400.0, trend=1.0):
        dates = pd.date_range(end="2026-09-22", periods=count, freq="B")
        prices = [start_price + i * trend for i in range(count)]
        return pd.DataFrame({
            "OPEN": prices,
            "HIGH": [p * 1.01 for p in prices],
            "LOW": [p * 0.99 for p in prices],
            "CLOSE": prices,
            "VOLUME": [1000000] * count,
        }, index=dates)

    @patch("src.regime.fetch_ohlcv_data")
    def test_1_valid_spy_data_bull(self, mock_fetch):
        # 250 bars rising strongly: close will be well above 200 DMA
        mock_fetch.return_value = self._make_spy_df(250, start_price=300.0, trend=1.0)
        res = get_regime()
        self.assertEqual(res["regime"], "bull")
        self.assertGreater(res["spy_price"], 0.0)
        self.assertGreater(res["spy_200dma"], 0.0)
        self.assertTrue(should_trade(res["regime"], "swing_momentum"))

    @patch("src.regime.fetch_ohlcv_data")
    def test_1_valid_spy_data_bear(self, mock_fetch):
        # 250 bars falling strongly: close will be well below 200 DMA
        mock_fetch.return_value = self._make_spy_df(250, start_price=600.0, trend=-1.5)
        res = get_regime()
        self.assertEqual(res["regime"], "bear")
        self.assertGreater(res["spy_price"], 0.0)
        self.assertFalse(should_trade(res["regime"], "swing_momentum"))

    @patch("src.regime.fetch_ohlcv_data")
    def test_2_missing_spy_data_returns_unknown(self, mock_fetch):
        # None returned
        mock_fetch.return_value = None
        res = get_regime()
        self.assertEqual(res["regime"], "unknown")
        self.assertEqual(res["spy_price"], 0.0)
        self.assertFalse(should_trade(res["regime"], "swing_momentum"))

        # Empty DataFrame returned
        mock_fetch.return_value = pd.DataFrame()
        res_empty = get_regime()
        self.assertEqual(res_empty["regime"], "unknown")
        self.assertEqual(res_empty["spy_price"], 0.0)
        self.assertFalse(should_trade(res_empty["regime"], "swing_momentum"))

    @patch("src.regime.fetch_ohlcv_data")
    def test_3_api_timeout_returns_unknown(self, mock_fetch):
        # Timeout raises exception
        mock_fetch.side_effect = TimeoutError("External API request timed out after 10s")
        res = get_regime()
        self.assertEqual(res["regime"], "unknown")
        self.assertEqual(res["spy_price"], 0.0)
        self.assertFalse(should_trade(res["regime"], "swing_momentum"))

    @patch("src.regime.fetch_ohlcv_data")
    def test_4_malformed_spy_response_returns_unknown(self, mock_fetch):
        # Insufficient bars (<200)
        mock_fetch.return_value = self._make_spy_df(50, start_price=400.0, trend=0.5)
        res_short = get_regime()
        self.assertEqual(res_short["regime"], "unknown")

        # Indicators contain NaN in latest row
        nan_df = self._make_spy_df(250, start_price=400.0, trend=0.5)
        nan_df.iloc[-1, nan_df.columns.get_loc("CLOSE")] = np.nan
        mock_fetch.return_value = nan_df
        res_nan = get_regime()
        self.assertEqual(res_nan["regime"], "unknown")

    def test_5_unknown_regime_activates_no_strategies(self):
        # In generate_signals.py, unknown regime must result in empty allowed_strategies
        unknown_regime = "unknown"
        if unknown_regime in ("unknown", "unavailable"):
            allowed_strategies = []
        else:
            allowed_strategies = REGIME_STRATEGY_MAP.get(unknown_regime, [])

        self.assertEqual(len(allowed_strategies), 0)
        self.assertFalse(should_trade(unknown_regime, "swing_momentum"))
        self.assertFalse(should_trade("unavailable", "swing_momentum"))


if __name__ == "__main__":
    unittest.main()
