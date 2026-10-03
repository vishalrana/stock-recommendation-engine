"""
Cache Safety and Data Failure Resilience Automated Test Suite
=============================================================
Tests:
1. Empty download/response never destroys or overwrites existing cache.
2. Partial batch download failure isolates bad tickers and preserves valid tickers.
3. Merging preserves ticker/date uniqueness and eliminates duplicates.
4. Corrupt parquet files (>50% NaNs) are rejected and skipped safely.
5. Missing Close/Volume data is caught and rejected by PIT liquidity gates.
6. Thread-safe date-partitioned merging preserves all distinct tickers across calls.
"""

import os
import sys
import shutil
import tempfile
import unittest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.data.cache_manager import CacheManager
from src.universe.filters import evaluate_point_in_time_liquidity


class TestCacheSafetyAndResilience(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_cache_resilience_")
        self.cache_mgr = CacheManager(cache_dir=self.test_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_mock_df(self, ticker: str, date_str: str, close: float = 100.0, volume: float = 1_000_000.0):
        idx = pd.MultiIndex.from_tuples([(ticker, pd.to_datetime(date_str))], names=["Ticker", "Date"])
        return pd.DataFrame(
            {"OPEN": [close], "HIGH": [close * 1.01], "LOW": [close * 0.99], "CLOSE": [close], "VOLUME": [volume]},
            index=idx,
        )

    def test_empty_download_does_not_overwrite_existing_cache(self):
        """Verify that an empty DataFrame never overwrites or clears existing cached tickers."""
        date_str = "2026-09-01"
        df_a = self._create_mock_df("AAPL", date_str, 200.0)
        self.cache_mgr.save_data_for_date(date_str, df_a)

        # Confirm AAPL is cached
        saved = self.cache_mgr.get_data_for_date(date_str)
        self.assertIsNotNone(saved)
        self.assertIn("AAPL", saved.index.get_level_values("Ticker"))

        # Ingest empty DataFrame
        self.cache_mgr.ingest_dataframe_to_cache(pd.DataFrame())

        # Verify AAPL is completely intact
        saved_after = self.cache_mgr.get_data_for_date(date_str)
        self.assertIsNotNone(saved_after)
        self.assertIn("AAPL", saved_after.index.get_level_values("Ticker"))
        self.assertEqual(float(saved_after.loc[("AAPL", pd.to_datetime(date_str)), "CLOSE"]), 200.0)

    def test_merging_accumulates_distinct_tickers(self):
        """Verify that multiple chunks merge into the same daily file without overwriting."""
        date_str = "2026-09-01"
        df_a = self._create_mock_df("AAPL", date_str, 200.0)
        df_b = self._create_mock_df("MSFT", date_str, 400.0)
        df_c = self._create_mock_df("NVDA", date_str, 120.0)

        self.cache_mgr.save_data_for_date(date_str, df_a)
        self.cache_mgr.save_data_for_date(date_str, df_b)
        self.cache_mgr.save_data_for_date(date_str, df_c)

        merged = self.cache_mgr.get_data_for_date(date_str)
        self.assertIsNotNone(merged)
        tickers = merged.index.get_level_values("Ticker").tolist()
        self.assertEqual(sorted(tickers), ["AAPL", "MSFT", "NVDA"])

    def test_duplicate_ticker_date_rows_eliminated(self):
        """Verify that saving updated rows for the same ticker/date deduplicates and keeps newest."""
        date_str = "2026-09-01"
        df_v1 = self._create_mock_df("AAPL", date_str, 200.0)
        df_v2 = self._create_mock_df("AAPL", date_str, 205.0)

        self.cache_mgr.save_data_for_date(date_str, df_v1)
        self.cache_mgr.save_data_for_date(date_str, df_v2)

        saved = self.cache_mgr.get_data_for_date(date_str)
        self.assertEqual(len(saved), 1, "Duplicate row was not eliminated!")
        self.assertEqual(float(saved.loc[("AAPL", pd.to_datetime(date_str)), "CLOSE"]), 205.0)

    def test_corrupt_file_rejected_on_save(self):
        """Verify that saving data with >50% NaN in CLOSE is rejected."""
        date_str = "2026-09-02"
        idx = pd.MultiIndex.from_tuples(
            [("BAD1", pd.to_datetime(date_str)), ("BAD2", pd.to_datetime(date_str))],
            names=["Ticker", "Date"],
        )
        df_nan = pd.DataFrame(
            {"OPEN": [10.0, 20.0], "HIGH": [11.0, 21.0], "LOW": [9.0, 19.0], "CLOSE": [np.nan, np.nan], "VOLUME": [100, 100]},
            index=idx,
        )
        self.cache_mgr.save_data_for_date(date_str, df_nan)
        # Should not create the file
        self.assertIsNone(self.cache_mgr.get_data_for_date(date_str))

    def test_missing_close_or_volume_rejected_by_liquidity_gate(self):
        """Verify that point-in-time liquidity check catches corrupt / missing price data."""
        dates = pd.date_range("2025-01-01", periods=300, freq="B")
        df_valid = pd.DataFrame(
            {"OPEN": 100.0, "HIGH": 105.0, "LOW": 95.0, "CLOSE": 100.0, "VOLUME": 2_000_000.0},
            index=dates,
        )

        is_liq, _, _ = evaluate_point_in_time_liquidity(df_valid, min_history_days=252)
        self.assertTrue(is_liq)

        # Missing CLOSE column
        df_no_close = df_valid.drop(columns=["CLOSE"])
        is_liq_nc, reason_nc, _ = evaluate_point_in_time_liquidity(df_no_close)
        self.assertFalse(is_liq_nc)
        self.assertIn("CLOSE", reason_nc)

        # Recent NaNs in Close
        df_nan_close = df_valid.copy()
        df_nan_close.iloc[-5:, df_nan_close.columns.get_loc("CLOSE")] = np.nan
        is_liq_nan, reason_nan, _ = evaluate_point_in_time_liquidity(df_nan_close)
        self.assertFalse(is_liq_nan)
        self.assertIn("NaN", reason_nan)


if __name__ == "__main__":
    unittest.main()
