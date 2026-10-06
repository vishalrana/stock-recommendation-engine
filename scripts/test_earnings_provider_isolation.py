"""
test_earnings_provider_isolation.py
===================================
Automated regression tests verifying strict earnings provider isolation,
central request budgeting, PEAD cache compliance, and downstream risk gating.

Tests 1 to 10:
  1. fetch_earnings_calendar with max_provider_fetches=0 performs 0 provider queries and respects local cache.
  2. fetch_earnings_calendar with max_provider_fetches=N performs at most N provider queries.
  3. Retries count toward the fetch budget and cannot exceed it.
  4. resolve_ticker_earnings with network disabled does not hit provider and returns cached/unknown safely.
  5. resolve_ticker_earnings with budget exhausted does not hit provider and returns safely.
  6. PEAD strategy discovers candidates when earnings data is already cached.
  7. PEAD strategy safely rejects candidates when earnings data is missing/unknown and provider queries are disabled.
  8. Earnings tracker accurately records provider requests consumed, remaining budget, and cache hits.
  9. run_scan with dry_run / normal scan runs with zero provider queries.
 10. Downstream earnings risk filter blocks stocks in earnings blackout when earnings date is known.
"""

import os
import sys
import tempfile
import json
import unittest
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import numpy as np

# Ensure project root and src are in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from src.filters.earnings_filter import (
    fetch_earnings_calendar,
    resolve_ticker_earnings,
    fetch_single_ticker_provider,
    earnings_risk_filter,
    save_local_cache,
    load_local_cache,
    GLOBAL_PROVIDER_BUDGET,
    GLOBAL_EARNINGS_TRACKER,
    ProviderBudget,
    EarningsStatus,
    reset_session_cache,
)
from jobs.strategies.pead import PEADStrategy
from jobs.generate_signals import run_scan


class TestEarningsProviderIsolation(unittest.TestCase):
    def setUp(self):
        GLOBAL_PROVIDER_BUDGET.reset()
        GLOBAL_EARNINGS_TRACKER.reset()
        reset_session_cache()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_cache_file = os.path.join(self.temp_dir.name, "earnings_cache.json")

    def tearDown(self):
        self.temp_dir.cleanup()
        GLOBAL_PROVIDER_BUDGET.reset()
        GLOBAL_EARNINGS_TRACKER.reset()
        reset_session_cache()

    def test_01_fetch_calendar_budget_zero_zero_provider_queries(self):
        """Test 1: fetch_earnings_calendar with max_provider_fetches=0 performs 0 provider queries and respects local cache."""
        today_iso = date.today().isoformat()
        next_iso = (date.today() + timedelta(days=20)).isoformat()
        cached_data = {
            "AAPL": {
                "ticker": "AAPL",
                "next_earnings_date": next_iso,
                "last_earnings_date": (date.today() - timedelta(days=70)).isoformat(),
                "cached_at": datetime.now().timestamp(),
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            }
        }
        save_local_cache(cached_data, self.temp_cache_file)

        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            cal = fetch_earnings_calendar(
                tickers=["AAPL", "MSFT", "NVDA"],
                cache_path=self.temp_cache_file,
                allow_network=False,
                max_provider_fetches=0,
            )
            # Provider should not be called at all
            mock_provider.assert_not_called()
            self.assertEqual(GLOBAL_PROVIDER_BUDGET.get_consumed(), 0)
            self.assertEqual(cal["AAPL"]["status"], EarningsStatus.KNOWN_UPCOMING.value)
            self.assertEqual(cal["AAPL"]["next_earnings_date"], next_iso)
            self.assertEqual(cal["MSFT"]["status"], EarningsStatus.UNKNOWN.value)
            self.assertEqual(cal["NVDA"]["status"], EarningsStatus.UNKNOWN.value)

    def test_02_fetch_calendar_bounded_to_n_queries(self):
        """Test 2: fetch_earnings_calendar with max_provider_fetches=N performs at most N provider queries."""
        save_local_cache({}, self.temp_cache_file)
        budget = 2
        called_tickers = []

        def mock_fetch(sym):
            called_tickers.append(sym)
            GLOBAL_PROVIDER_BUDGET.record_request()
            return sym, "2026-11-01", "2026-08-01", "Q3", EarningsStatus.KNOWN_UPCOMING.value

        with patch("src.filters.earnings_filter.fetch_single_ticker_provider", side_effect=mock_fetch):
            cal = fetch_earnings_calendar(
                tickers=["AAPL", "MSFT", "GOOGL", "AMZN"],
                cache_path=self.temp_cache_file,
                allow_network=True,
                max_provider_fetches=budget,
                max_workers=1,
            )
            self.assertLessEqual(len(called_tickers), budget)
            self.assertEqual(GLOBAL_PROVIDER_BUDGET.get_consumed(), budget)
            self.assertEqual(GLOBAL_PROVIDER_BUDGET.get_remaining(), 0)
            self.assertFalse(GLOBAL_PROVIDER_BUDGET.can_request())

    def test_03_retries_count_toward_fetch_budget(self):
        """Test 3: Retries count toward the fetch budget and cannot exceed it."""
        budget = ProviderBudget(max_requests=2)
        # Mocking attempt + retry
        self.assertTrue(budget.can_request())
        budget.record_request()  # Attempt 1
        self.assertEqual(budget.get_consumed(), 1)
        self.assertTrue(budget.can_request())
        budget.record_request()  # Retry 1
        self.assertEqual(budget.get_consumed(), 2)
        self.assertFalse(budget.can_request())  # Budget exhausted
        # Next attempt blocked
        self.assertEqual(budget.get_remaining(), 0)

    def test_04_resolve_ticker_earnings_network_disabled(self):
        """Test 4: resolve_ticker_earnings with network disabled does not hit provider and returns safely."""
        calendar_map = {}
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            rec = resolve_ticker_earnings(
                ticker="UNCACHED",
                calendar_map=calendar_map,
                cache_path=self.temp_cache_file,
                allow_network=False,
            )
            mock_provider.assert_not_called()
            self.assertEqual(rec["status"], EarningsStatus.UNKNOWN.value)
            self.assertIsNone(rec["next_earnings_date"])

    def test_05_resolve_ticker_earnings_budget_exhausted(self):
        """Test 5: resolve_ticker_earnings with budget exhausted does not hit provider and returns safely."""
        calendar_map = {}
        GLOBAL_PROVIDER_BUDGET.set_budget(0)
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            rec = resolve_ticker_earnings(
                ticker="TSLA",
                calendar_map=calendar_map,
                cache_path=self.temp_cache_file,
                allow_network=True,
            )
            mock_provider.assert_not_called()
            self.assertEqual(rec["status"], EarningsStatus.UNKNOWN.value)

    def test_06_pead_strategy_with_cached_earnings(self):
        """Test 6: PEAD strategy discovers candidates when earnings data is already cached."""
        strategy = PEADStrategy()
        today = date.today()
        ref_earnings_date = today - timedelta(days=2)

        # Preloaded calendar
        earnings_cal = {
            "XYZ": {
                "ticker": "XYZ",
                "last_earnings_date": ref_earnings_date.isoformat(),
                "next_earnings_date": (today + timedelta(days=70)).isoformat(),
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            }
        }
        strategy.set_earnings_calendar(earnings_cal, allow_network=False)

        # Create 60 bars of synthetic data with gap 2 days ago
        dates = [today - timedelta(days=60 - i) for i in range(60)]
        closes = [100.0] * 58 + [106.0, 106.5]  # 6% gap up 2 days ago
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        volumes = [100000.0] * 58 + [300000.0, 120000.0]  # Strong volume on gap

        df = pd.DataFrame({
            "OPEN": closes,
            "HIGH": highs,
            "LOW": lows,
            "CLOSE": closes,
            "VOLUME": volumes,
            "ADX_14": [25.0] * 60,
            "RSI_14": [60.0] * 60,
            "MACD_HIST": [0.05] * 60,
        }, index=pd.DatetimeIndex(dates))

        metrics = {
            "company_name": "XYZ Corp",
            "industry": "Tech",
            "win_rate": 55.0,
            "expectancy_pct": 2.0,
            "total_trades": 20,
        }

        with patch("src.utils.earnings_cache.fetch_single_ticker_provider") as mock_provider:
            res = strategy.scan("XYZ", df, "bull", metrics)
            mock_provider.assert_not_called()
            self.assertIsNotNone(res)
            self.assertEqual(res["ticker"], "XYZ")
            self.assertEqual(res["strategy"], "Post-Earnings Drift")

    def test_07_pead_strategy_rejects_when_earnings_missing(self):
        """Test 7: PEAD strategy safely rejects candidates when earnings data is missing/unknown and provider queries disabled."""
        strategy = PEADStrategy()
        strategy.set_earnings_calendar({}, allow_network=False)

        today = date.today()
        dates = [today - timedelta(days=60 - i) for i in range(60)]
        closes = [100.0] * 58 + [106.0, 106.5]
        df = pd.DataFrame({
            "OPEN": closes,
            "HIGH": [c * 1.01 for c in closes],
            "LOW": [c * 0.99 for c in closes],
            "CLOSE": closes,
            "VOLUME": [100000.0] * 58 + [300000.0, 120000.0],
            "ADX_14": [25.0] * 60,
            "RSI_14": [60.0] * 60,
            "MACD_HIST": [0.05] * 60,
        }, index=pd.DatetimeIndex(dates))

        metrics = {"company_name": "Missing Corp", "industry": "Tech", "win_rate": 55.0, "expectancy_pct": 2.0, "total_trades": 20}

        with patch("src.utils.earnings_cache.fetch_single_ticker_provider") as mock_provider:
            res = strategy.scan("MISS", df, "bull", metrics)
            mock_provider.assert_not_called()
            self.assertIsNone(res)

    def test_08_earnings_tracker_budget_recording(self):
        """Test 8: Earnings tracker accurately records provider requests consumed, remaining budget, and cache hits."""
        tracker = GLOBAL_EARNINGS_TRACKER
        tracker.reset()
        GLOBAL_PROVIDER_BUDGET.set_budget(10)
        tracker.local_cache_hits = 15
        tracker.supabase_cache_hits = 5
        GLOBAL_PROVIDER_BUDGET.record_request()
        GLOBAL_PROVIDER_BUDGET.record_request()

        summary = tracker.format_summary()
        self.assertIn("consumed: 2", summary)
        self.assertIn("remaining: 8", summary)
        self.assertIn("Local cache hits: 15", summary)
        self.assertIn("Supabase cache hits: 5", summary)

    def test_09_run_scan_zero_provider_queries_in_dry_run(self):
        """Test 9: run_scan with dry_run / normal scan runs with zero provider queries."""
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            res = run_scan(
                dry_run=True,
                cache_mode="local",
                target_tickers=["AAPL"],
                max_provider_fetches=0,
            )
            mock_provider.assert_not_called()
            self.assertEqual(GLOBAL_PROVIDER_BUDGET.get_consumed(), 0)

    def test_10_downstream_earnings_risk_filter_blocks_blackout(self):
        """Test 10: Downstream earnings risk filter blocks stocks in earnings blackout when earnings date is known."""
        today = date.today()
        blackout_date = (today + timedelta(days=2)).isoformat()
        clear_date = (today + timedelta(days=25)).isoformat()

        cal = {
            "BLOCKED": {
                "ticker": "BLOCKED",
                "next_earnings_date": blackout_date,
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            },
            "CLEARED": {
                "ticker": "CLEARED",
                "next_earnings_date": clear_date,
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            },
        }

        # Candidate 1: in blackout (2 days to earnings) -> blocked
        res_blocked = earnings_risk_filter(
            ticker="BLOCKED",
            scan_date=today,
            strategy="Trend Following",
            earnings_calendar=cal,
            allow_unknown_date=True,
        )
        self.assertFalse(res_blocked["pass"])
        self.assertEqual(res_blocked["reason_code"], "EARNINGS_BLACKOUT_BLOCK")

        # Candidate 2: 25 days to earnings -> passes
        res_cleared = earnings_risk_filter(
            ticker="CLEARED",
            scan_date=today,
            strategy="Trend Following",
            earnings_calendar=cal,
            allow_unknown_date=True,
        )
        self.assertTrue(res_cleared["pass"])


if __name__ == "__main__":
    unittest.main()
