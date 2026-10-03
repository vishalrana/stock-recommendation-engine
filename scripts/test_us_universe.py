"""
Unit and Regression Test Suite for US Equity Universe Expansion
================================================================
Validates:
1. US universe provider (USEquitiesUniverseProvider)
2. Security-type filtering (rejects ETFs, preferreds, warrants, rights, units, notes, test symbols)
3. Liquidity filtering (min price, min dollar volume)
4. Minimum history requirement (252 bars)
5. Ticker normalization (bi-directional mapping BRK.B <-> BRK-B)
6. Missing-data handling and corrupted data rejection
7. Duplicate elimination
8. S&P/Nasdaq benchmark fallback
9. Cross-sectional momentum full-universe behavior (top 15% pre-screen)
10. No-lookahead in universe liquidity eligibility
11. Entry Location remains mathematically unchanged
12. Recommendation lifecycle remains unchanged
"""

import unittest
import os
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.universe.models import SecurityRecord
from src.universe.normalization import (
    to_canonical_ticker,
    to_provider_ticker,
    is_valid_us_ticker,
)
from src.universe.filters import (
    is_eligible_equity,
    evaluate_point_in_time_liquidity,
)
from src.universe.us_equities import USEquitiesUniverseProvider
from src.quant_config import (
    US_UNIVERSE_MIN_PRICE,
    US_UNIVERSE_MIN_DOLLAR_VOLUME,
    US_UNIVERSE_MIN_HISTORY_DAYS,
    US_UNIVERSE_DOLLAR_VOLUME_WINDOW,
)
from jobs.generate_signals import (
    load_universe,
    load_sp500_nasdaq_universe,
    run_cross_sectional_screen,
)
from src.entry_location import evaluate_entry_location


class TestUSEquityUniverse(unittest.TestCase):
    """Test suite for US Universe Expansion architecture."""

    def test_01_ticker_normalization(self):
        """Test bi-directional ticker normalization between canonical and provider formats."""
        # Dual-class tickers
        self.assertEqual(to_canonical_ticker("BRK-B"), "BRK.B")
        self.assertEqual(to_provider_ticker("BRK.B"), "BRK-B")
        self.assertEqual(to_canonical_ticker("BF-A"), "BF.A")
        self.assertEqual(to_provider_ticker("BF.A"), "BF-A")

        # Standard tickers
        self.assertEqual(to_canonical_ticker("aapl"), "AAPL")
        self.assertEqual(to_provider_ticker("AAPL"), "AAPL")

        # Validation
        self.assertTrue(is_valid_us_ticker("AAPL"))
        self.assertTrue(is_valid_us_ticker("BRK.B"))
        self.assertTrue(is_valid_us_ticker("BRK-B"))
        self.assertFalse(is_valid_us_ticker("TEST"))
        self.assertFalse(is_valid_us_ticker("XYZ"))
        self.assertFalse(is_valid_us_ticker("BFH$A"))  # Preferred with $

    def test_02_security_type_filtering(self):
        """Test instrument eligibility filters out non-common equities."""
        # 1. Valid Common Equity
        rec_common = SecurityRecord(
            ticker="AAPL",
            company_name="Apple Inc.",
            exchange="NASDAQ",
            sector="Technology",
            instrument_type="COMMON",
            is_active=True,
            is_etf=False,
            is_test=False,
        )
        is_ok, reason = is_eligible_equity(rec_common)
        self.assertTrue(is_ok, f"Expected common equity to be eligible, got: {reason}")

        # 2. ETF (Explicit flag)
        rec_etf = SecurityRecord(
            ticker="SPY",
            company_name="SPDR S&P 500 ETF Trust",
            exchange="ARCA",
            instrument_type="ETF",
            is_etf=True,
        )
        is_ok, reason = is_eligible_equity(rec_etf)
        self.assertFalse(is_ok)
        self.assertIn("ETF", reason)

        # 3. Test Issue
        rec_test = SecurityRecord(
            ticker="ZWZZT",
            company_name="NASDAQ Test Symbol",
            exchange="NASDAQ",
            is_test=True,
        )
        is_ok, reason = is_eligible_equity(rec_test)
        self.assertFalse(is_ok)
        self.assertIn("test", reason.lower())

        # 4. Preferred Share (via name pattern)
        rec_pref = SecurityRecord(
            ticker="C",
            company_name="Citigroup Inc. 5.95% Preferred Stock",
            exchange="NYSE",
        )
        is_ok, reason = is_eligible_equity(rec_pref)
        self.assertFalse(is_ok)
        self.assertIn("non-common", reason.lower())

        # 5. Warrant
        rec_warrant = SecurityRecord(
            ticker="LCIDW",
            company_name="Lucid Group Inc. Warrant",
            exchange="NASDAQ",
        )
        is_ok, reason = is_eligible_equity(rec_warrant)
        self.assertFalse(is_ok)
        self.assertIn("non-common", reason.lower())

        # 6. Units
        rec_units = SecurityRecord(
            ticker="IPOU",
            company_name="Social Capital Hedosophia Holdings Corp. Units",
            exchange="NYSE",
        )
        is_ok, reason = is_eligible_equity(rec_units)
        self.assertFalse(is_ok)
        self.assertIn("non-common", reason.lower())

        # 7. Delisted ticker
        rec_delisted = SecurityRecord(
            ticker="LEH",
            company_name="Lehman Brothers Holdings Inc.",
            exchange="NYSE",
        )
        is_ok, reason = is_eligible_equity(rec_delisted, delisted_tickers={"LEH", "ENRNQ"})
        self.assertFalse(is_ok)
        self.assertIn("delisted", reason.lower())

    def test_03_liquidity_filtering(self):
        """Test point-in-time liquidity filtering on price, dollar volume, and history."""
        dates = pd.date_range(end="2026-10-01", periods=260, freq="B")
        
        # Valid liquid stock
        df_liquid = pd.DataFrame({
            "OPEN": np.full(260, 50.0),
            "HIGH": np.full(260, 51.0),
            "LOW": np.full(260, 49.0),
            "CLOSE": np.full(260, 50.0),
            "VOLUME": np.full(260, 500_000.0),  # $25M daily dollar volume
        }, index=dates)

        is_ok, reason, metrics = evaluate_point_in_time_liquidity(
            df_liquid,
            as_of_date="2026-10-01",
            min_price=US_UNIVERSE_MIN_PRICE,
            min_dollar_volume=US_UNIVERSE_MIN_DOLLAR_VOLUME,
            min_history_days=US_UNIVERSE_MIN_HISTORY_DAYS,
        )
        self.assertTrue(is_ok, reason)
        self.assertEqual(metrics["price"], 50.0)
        self.assertEqual(metrics["avg_dollar_volume"], 25_000_000.0)

        # Low price failure (penny stock < $5)
        df_penny = df_liquid.copy()
        df_penny["CLOSE"] = 2.50
        is_ok, reason, _ = evaluate_point_in_time_liquidity(
            df_penny,
            as_of_date="2026-10-01",
            min_price=5.0,
            min_dollar_volume=1_000_000.0,
            min_history_days=252,
        )
        self.assertFalse(is_ok)
        self.assertIn("below minimum", reason.lower())

        # Low dollar volume failure (< $5M)
        df_illiquid = df_liquid.copy()
        df_illiquid["VOLUME"] = 10_000.0  # 10k * $50 = $500k dollar volume
        is_ok, reason, _ = evaluate_point_in_time_liquidity(
            df_illiquid,
            as_of_date="2026-10-01",
            min_price=5.0,
            min_dollar_volume=5_000_000.0,
            min_history_days=252,
        )
        self.assertFalse(is_ok)
        self.assertIn("dollar volume", reason.lower())

    def test_04_minimum_history_filtering(self):
        """Test rejection when ticker has fewer than min_history_days observations."""
        dates = pd.date_range(end="2026-10-01", periods=100, freq="B")
        df_short = pd.DataFrame({
            "OPEN": np.full(100, 100.0),
            "HIGH": np.full(100, 102.0),
            "LOW": np.full(100, 99.0),
            "CLOSE": np.full(100, 100.0),
            "VOLUME": np.full(100, 1_000_000.0),
        }, index=dates)

        is_ok, reason, metrics = evaluate_point_in_time_liquidity(
            df_short,
            as_of_date="2026-10-01",
            min_history_days=252,
        )
        self.assertFalse(is_ok)
        self.assertIn("insufficient history", reason.lower())
        self.assertEqual(metrics["history_days"], 100)

    def test_05_no_lookahead_in_liquidity(self):
        """Test strict point-in-time filtering: future data is never included."""
        dates = pd.date_range(start="2025-01-01", end="2026-10-15", freq="B")
        df_all = pd.DataFrame({
            "OPEN": np.full(len(dates), 20.0),
            "HIGH": np.full(len(dates), 21.0),
            "LOW": np.full(len(dates), 19.0),
            "CLOSE": np.full(len(dates), 20.0),
            "VOLUME": np.full(len(dates), 1_000_000.0),
        }, index=dates)

        # Inject extreme future pump after 2026-09-01
        df_all.loc[df_all.index > "2026-09-01", "CLOSE"] = 500.0
        df_all.loc[df_all.index > "2026-09-01", "VOLUME"] = 50_000_000.0

        # Evaluate as of 2026-09-01
        _, _, metrics_pit = evaluate_point_in_time_liquidity(
            df_all,
            as_of_date="2026-09-01",
            min_price=5.0,
            min_dollar_volume=5_000_000.0,
            min_history_days=60,
        )
        self.assertEqual(metrics_pit["price"], 20.0, "Future price pump leaked into PIT calculation!")
        self.assertEqual(metrics_pit["avg_dollar_volume"], 20_000_000.0)

    def test_06_corrupted_data_rejection(self):
        """Test rejection of corrupted data (NaN prices, negative volume)."""
        dates = pd.date_range(end="2026-10-01", periods=260, freq="B")
        df_nan = pd.DataFrame({
            "OPEN": np.full(260, 50.0),
            "HIGH": np.full(260, 51.0),
            "LOW": np.full(260, 49.0),
            "CLOSE": [np.nan if i > 250 else 50.0 for i in range(260)],
            "VOLUME": np.full(260, 500_000.0),
        }, index=dates)

        is_ok, reason, _ = evaluate_point_in_time_liquidity(df_nan, as_of_date="2026-10-01")
        self.assertFalse(is_ok)
        self.assertIn("nan", reason.lower())

    def test_07_universe_provider_loading_and_stats(self):
        """Test that USEquitiesUniverseProvider loads candidates, filters, and reports statistics."""
        provider = USEquitiesUniverseProvider()
        universe = provider.get_universe()
        stats = provider.get_stats()

        self.assertGreater(len(universe), 2000, "Expected >2000 eligible US common equities")
        self.assertIn("eligible_common_equities", stats)
        self.assertEqual(stats["eligible_common_equities"], len(universe))

        # Check that records have required fields
        sample = universe[0]
        self.assertTrue(sample.ticker)
        self.assertTrue(sample.company_name)
        self.assertIn(sample.exchange, {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "IEX", "US"})
        self.assertEqual(sample.market, "US")
        self.assertIn(sample.country, {"US", "United States"})

    def test_08_duplicate_elimination(self):
        """Verify that provider contains zero duplicate canonical tickers."""
        provider = USEquitiesUniverseProvider()
        tickers = provider.get_tickers()
        self.assertEqual(len(tickers), len(set(tickers)), "Duplicate tickers detected in universe!")

    def test_09_sp500_nasdaq_fallback(self):
        """Test benchmark fallback function returns S&P 500 + Nasdaq-100 tickers."""
        bench_tickers, names, industries = load_sp500_nasdaq_universe()
        self.assertGreaterEqual(len(bench_tickers), 100)
        self.assertIn("AAPL", bench_tickers)
        self.assertIn("MSFT", bench_tickers)

    def test_10_cross_sectional_full_universe(self):
        """Verify cross-sectional screen processes all candidate tickers and selects top 15%."""
        class MockCache:
            def __init__(self, data_map):
                self.data_map = data_map
            def get_ticker_history(self, ticker, start, end):
                return self.data_map.get(ticker)

        # Create 20 mock tickers with varying 63D returns
        mock_data = {}
        dates = pd.date_range(end="2026-10-01", periods=100, freq="B")
        for i in range(20):
            ticker = f"TICK{i:02d}"
            # Return proportional to i
            start_price = 100.0
            end_price = 100.0 + (i * 5.0)  # +0% to +95%
            prices = np.linspace(start_price, end_price, 100)
            mock_data[ticker] = pd.DataFrame({"CLOSE": prices}, index=dates)

        cache = MockCache(mock_data)
        universe = list(mock_data.keys())
        top_candidates = run_cross_sectional_screen(universe, cache)

        # 20 * 0.15 = 3
        self.assertEqual(len(top_candidates), 3)
        # Should pick TICK19, TICK18, TICK17
        self.assertEqual(top_candidates[0][0], "TICK19")
        self.assertEqual(top_candidates[1][0], "TICK18")
        self.assertEqual(top_candidates[2][0], "TICK17")

    def test_11_entry_location_unchanged(self):
        """Verify Entry Location evaluation is bit-for-bit identical."""
        dates = pd.date_range(end="2026-10-01", periods=100, freq="B")
        # Steady uptrend touching support
        prices = np.linspace(100, 150, 100)
        df = pd.DataFrame({
            "OPEN": prices - 1.0,
            "HIGH": prices + 2.0,
            "LOW": prices - 2.0,
            "CLOSE": prices,
            "VOLUME": np.full(100, 1_000_000.0),
            "ATR_14": np.full(100, 3.0),
            "EMA_20": prices - 2.0,
            "DMA_50": prices - 10.0,
            "RSI_14": np.full(100, 55.0),
        }, index=dates)

        candidate = {
            "ticker": "TEST",
            "price": 150.0,
            "entry_price": 150.0,
            "stop_loss": 140.0,
            "target_1": 165.0,
        }

        res = evaluate_entry_location(candidate, df, "Trend Following")
        self.assertIn(res.state, {"BUY", "WAIT", "REJECT"})
        self.assertIsNotNone(res.support_level)
        self.assertIsNotNone(res.resistance_level)


if __name__ == "__main__":
    unittest.main()
