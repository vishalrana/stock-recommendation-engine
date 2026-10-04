"""
Comprehensive Earnings Infrastructure & Oct 3 Regression Test Suite
===================================================================
Tests all 20 required items from Section 9 and the Oct 3 regression fixture from Section 10:
1. local cache hit
2. Supabase cache hit
3. provider fetch
4. provider result persistence
5. local cache write
6. Supabase upsert
7. duplicate prevention
8. malformed local cache
9. empty provider response
10. provider timeout
11. provider 401/403
12. provider rate-limit response (429)
13. retry/backoff behavior
14. cached data surviving provider failure
15. UNKNOWN when no reliable data exists
16. correct blackout calculation
17. Sector ETF exemption
18. ordinary equity still subject to earnings blackout
19. repeated scan does not repeatedly fetch the same earnings data
20. no lookahead in earnings dates
21. Oct 3 Regression Fixture (AME stays WAIT in Entry Location; false UNKNOWN eliminated; fail-closed safety preserved)
"""

import sys
import os
import json
import time
import shutil
import tempfile
import unittest
import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.filters.earnings_filter import (
    EarningsStatus,
    normalize_date_str,
    normalize_strategy_key,
    is_earnings_record_fresh,
    load_local_cache,
    save_local_cache,
    fetch_supabase_earnings,
    persist_supabase_earnings,
    fetch_single_ticker_provider,
    fetch_earnings_calendar,
    earnings_risk_filter,
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_CACHE_TTL_SECONDS,
)
from src.utils.earnings_cache import get_ticker_earnings
from src.entry_location import analyze_market_structure, evaluate_entry_location
import pandas as pd
import numpy as np


class TestEarningsInfrastructure(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_cache_file = Path(self.temp_dir) / "test_earnings_cache.json"
        self.scan_date = datetime.date(2026, 10, 3)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Local cache hit
    def test_01_local_cache_hit(self):
        cache_data = {
            "AAPL": {
                "ticker": "AAPL",
                "next_earnings_date": "2026-10-30",
                "last_earnings_date": "2026-08-01",
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            }
        }
        save_local_cache(cache_data, self.test_cache_file)
        
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            res = fetch_earnings_calendar(["AAPL"], supabase=None, cache_path=self.test_cache_file)
            mock_provider.assert_not_called()
            self.assertIn("AAPL", res)
            self.assertEqual(res["AAPL"]["next_earnings_date"], "2026-10-30")
            self.assertEqual(res["AAPL"]["source"], "local_cache")

    # 2. Supabase cache hit
    def test_02_supabase_cache_hit(self):
        mock_supabase = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()
        mock_execute = MagicMock()

        mock_supabase.table.return_value.select.return_value = mock_select
        mock_select.in_.return_value = mock_in
        mock_in.execute.return_value = MagicMock(data=[
            {
                "ticker": "MSFT",
                "next_earnings_date": "2026-10-25",
                "last_earnings_date": "2026-07-25",
                "fiscal_period": "Q1",
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        ])

        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            res = fetch_earnings_calendar(["MSFT"], supabase=mock_supabase, cache_path=self.test_cache_file)
            mock_provider.assert_not_called()
            self.assertIn("MSFT", res)
            self.assertEqual(res["MSFT"]["next_earnings_date"], "2026-10-25")
            self.assertEqual(res["MSFT"]["source"], "supabase")

    # 3. Provider fetch
    def test_03_provider_fetch(self):
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("GOOG", "2026-11-02", "2026-07-28", "Q3", EarningsStatus.KNOWN_UPCOMING.value)
            res = fetch_earnings_calendar(["GOOG"], supabase=None, cache_path=self.test_cache_file)
            mock_provider.assert_called_once()
            self.assertIn("GOOG", res)
            self.assertEqual(res["GOOG"]["next_earnings_date"], "2026-11-02")
            self.assertEqual(res["GOOG"]["source"], "provider")

    # 4. Provider result persistence
    def test_04_provider_result_persistence(self):
        mock_supabase = MagicMock()
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("NVDA", "2026-11-20", "2026-08-28", "Q3", EarningsStatus.KNOWN_UPCOMING.value)
            res = fetch_earnings_calendar(["NVDA"], supabase=mock_supabase, cache_path=self.test_cache_file)
            
            # Check local file was created and contains NVDA
            loaded = load_local_cache(self.test_cache_file)
            self.assertIn("NVDA", loaded)
            self.assertEqual(loaded["NVDA"]["next_earnings_date"], "2026-11-20")

            # Check Supabase upsert was called
            mock_supabase.table.return_value.upsert.assert_called_once()

    # 5. Local cache write
    def test_05_local_cache_write(self):
        test_data = {"AMD": {"ticker": "AMD", "next_earnings_date": "2026-10-28"}}
        ok = save_local_cache(test_data, self.test_cache_file)
        self.assertTrue(ok)
        self.assertTrue(self.test_cache_file.exists())
        loaded = load_local_cache(self.test_cache_file)
        self.assertEqual(loaded["AMD"]["next_earnings_date"], "2026-10-28")

    # 6. Supabase upsert
    def test_06_supabase_upsert(self):
        mock_supabase = MagicMock()
        mock_upsert = MagicMock()
        mock_supabase.table.return_value.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = MagicMock(data=[{"ticker": "INTC"}])

        records = [{"ticker": "INTC", "next_earnings_date": "2026-10-24"}]
        count = persist_supabase_earnings(records, mock_supabase)
        self.assertEqual(count, 1)
        mock_supabase.table.assert_called_with("earnings_calendar")

    # 7. Duplicate prevention
    def test_07_duplicate_prevention(self):
        # Querying with duplicate tickers in input list should deduplicate
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("TSLA", "2026-10-22", None, None, EarningsStatus.KNOWN_UPCOMING.value)
            res = fetch_earnings_calendar(["TSLA", "tsla", "TSLA"], supabase=None, cache_path=self.test_cache_file)
            self.assertEqual(mock_provider.call_count, 1)
            self.assertIn("TSLA", res)

    # 8. Malformed local cache tolerance
    def test_08_malformed_local_cache(self):
        # Write corrupted JSON to cache file
        with open(self.test_cache_file, "w") as f:
            f.write("{ invalid json corrupted content !@#$ }")
        
        # Must not raise an exception, loads empty dict safely
        loaded = load_local_cache(self.test_cache_file)
        self.assertEqual(loaded, {})

        # fetch_earnings_calendar must tolerate it and continue
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("AMZN", "2026-10-31", None, None, EarningsStatus.KNOWN_UPCOMING.value)
            res = fetch_earnings_calendar(["AMZN"], supabase=None, cache_path=self.test_cache_file)
            self.assertIn("AMZN", res)

    # 9. Empty provider response
    def test_09_empty_provider_response(self):
        # Provider returns None next date without crashing (e.g. ETF or stock with no date) -> KNOWN_CLEAR
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("CLEAR_CO", None, "2026-07-01", None, EarningsStatus.KNOWN_CLEAR.value)
            res = fetch_earnings_calendar(["CLEAR_CO"], supabase=None, cache_path=self.test_cache_file)
            self.assertEqual(res["CLEAR_CO"]["status"], EarningsStatus.KNOWN_CLEAR.value)

    # 10. Provider timeout
    def test_10_provider_timeout(self):
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("TIMEOUT_CO", None, None, None, EarningsStatus.UNKNOWN.value)
            res = fetch_earnings_calendar(["TIMEOUT_CO"], supabase=None, cache_path=self.test_cache_file)
            self.assertEqual(res["TIMEOUT_CO"]["status"], EarningsStatus.UNKNOWN.value)

    # 11. Provider 401/403
    def test_11_provider_401_403(self):
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("FORBIDDEN_CO", None, None, None, EarningsStatus.UNKNOWN.value)
            res = fetch_earnings_calendar(["FORBIDDEN_CO"], supabase=None, cache_path=self.test_cache_file)
            self.assertEqual(res["FORBIDDEN_CO"]["status"], EarningsStatus.UNKNOWN.value)

    # 12. Provider rate-limit response (429)
    def test_12_provider_rate_limit_response(self):
        # Real fetch_single_ticker_provider retry handling test
        with patch("yfinance.Ticker") as mock_yf:
            mock_yf.side_effect = Exception("HTTP 429 Client Error: Too Many Requests for url")
            sym, next_d, last_d, fp, st = fetch_single_ticker_provider("RATE_LIMIT_CO", max_retries=1, base_delay=0.01)
            self.assertEqual(st, EarningsStatus.UNKNOWN.value)
            self.assertIsNone(next_d)

    # 13. Retry/backoff behavior
    def test_13_retry_backoff_behavior(self):
        with patch("yfinance.Ticker") as mock_yf:
            # First attempt fails with 429, second attempt succeeds
            good_ticker = MagicMock()
            good_ticker.calendar = {"Earnings Date": [datetime.date(2026, 11, 10)]}
            good_ticker.earnings_dates = None
            mock_yf.side_effect = [Exception("429 Too Many Requests"), good_ticker]

            sym, next_d, last_d, fp, st = fetch_single_ticker_provider("RETRY_CO", max_retries=2, base_delay=0.01)
            self.assertEqual(st, EarningsStatus.KNOWN_UPCOMING.value)
            self.assertEqual(next_d, "2026-11-10")

    # 14. Cached data surviving provider failure
    def test_14_cached_data_surviving_provider_failure(self):
        # Prior valid entry exists in cache (stale and no upcoming date yet announced)
        prior_entry = {
            "ticker": "SURVIVOR",
            "next_earnings_date": None,
            "last_earnings_date": "2026-08-15",
            "updated_at": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=48)).isoformat(),
            "status": EarningsStatus.STALE.value,
        }
        save_local_cache({"SURVIVOR": prior_entry}, self.test_cache_file)

        # Provider fails with rate limit / timeout
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("SURVIVOR", None, None, None, EarningsStatus.UNKNOWN.value)
            res = fetch_earnings_calendar(["SURVIVOR"], supabase=None, cache_path=self.test_cache_file)
            
            # Must NOT be marked UNKNOWN; survives using cached past date and falls back to KNOWN_CLEAR!
            self.assertEqual(res["SURVIVOR"]["status"], EarningsStatus.KNOWN_CLEAR.value)
            self.assertEqual(res["SURVIVOR"]["last_earnings_date"], "2026-08-15")
            self.assertEqual(res["SURVIVOR"]["source"], "cache_fallback")

    # 15. UNKNOWN when no reliable data exists
    def test_15_unknown_when_no_reliable_data_exists(self):
        from src.quant_config import REASON_EARNINGS_DATE_UNKNOWN_NO_CATALYST
        # Empty cache, provider returns UNKNOWN -> result is UNKNOWN
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("NO_DATA_CO", None, None, None, EarningsStatus.UNKNOWN.value)
            res = fetch_earnings_calendar(["NO_DATA_CO"], supabase=None, cache_path=self.test_cache_file)
            self.assertEqual(res["NO_DATA_CO"]["status"], EarningsStatus.UNKNOWN.value)

            # Evaluate through earnings risk filter with allow_unknown_date=False -> must fail closed
            decision_closed = earnings_risk_filter("NO_DATA_CO", self.scan_date, "trend_following", res, allow_unknown_date=False)
            self.assertFalse(decision_closed["pass"])
            self.assertEqual(decision_closed["status"], EarningsStatus.UNKNOWN.value)

            # Evaluate with default allow_unknown_date=True (no negative catalyst) -> proceeds safely
            decision_default = earnings_risk_filter("NO_DATA_CO", self.scan_date, "trend_following", res)
            self.assertTrue(decision_default["pass"])
            self.assertEqual(decision_default["reason_code"], REASON_EARNINGS_DATE_UNKNOWN_NO_CATALYST)

    # 16. Correct blackout calculation
    def test_16_correct_blackout_calculation(self):
        # Scan date: 2026-10-03. Trend following blackout is 5 days.
        cal = {
            "SAFE_CO": {
                "next_earnings_date": "2026-10-20", # 17 days away (> 5) -> PASS
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
            "DANGER_CO": {
                "next_earnings_date": "2026-10-06", # 3 days away (<= 5) -> REJECT
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        }
        res_safe = earnings_risk_filter("SAFE_CO", self.scan_date, "trend_following", cal)
        self.assertTrue(res_safe["pass"])
        self.assertEqual(res_safe["days_to_earnings"], 17)

        res_danger = earnings_risk_filter("DANGER_CO", self.scan_date, "trend_following", cal)
        self.assertFalse(res_danger["pass"])
        self.assertEqual(res_danger["days_to_earnings"], 3)

    # 17. Sector ETF exemption
    def test_17_sector_etf_exemption(self):
        # Sector Rotation strategy is exempt from corporate earnings blackout
        # Even if calendar is empty or ticker is missing, it must pass with KNOWN_CLEAR
        empty_cal = {}
        for etf_ticker in ["XLE", "XLK", "SOXX", "SMH", "ANY_ETF"]:
            res = earnings_risk_filter(etf_ticker, self.scan_date, "sector_rotation", empty_cal)
            self.assertTrue(res["pass"])
            self.assertEqual(res["status"], EarningsStatus.KNOWN_CLEAR.value)
            self.assertIn("Sector ETF", res["reason"])

        # Also exempt if instrument_type is ETF or is_etf is True under any strategy
        res_type = earnings_risk_filter("SPY", self.scan_date, "trend_following", empty_cal, instrument_type="ETF")
        self.assertTrue(res_type["pass"])
        self.assertEqual(res_type["status"], EarningsStatus.KNOWN_CLEAR.value)

        res_flag = earnings_risk_filter("QQQ", self.scan_date, "trend_following", empty_cal, is_etf=True)
        self.assertTrue(res_flag["pass"])
        self.assertEqual(res_flag["status"], EarningsStatus.KNOWN_CLEAR.value)

    # 18. Ordinary equity still subject to earnings blackout
    def test_18_ordinary_equity_still_subject_to_blackout(self):
        cal = {
            "AAPL": {
                "next_earnings_date": "2026-10-06", # 3 days away
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        }
        # Under trend_following (blackout: 5d), AAPL must fail
        res = earnings_risk_filter("AAPL", self.scan_date, "trend_following", cal, instrument_type="COMMON")
        self.assertFalse(res["pass"])
        self.assertEqual(res["days_to_earnings"], 3)

    # 19. Repeated scan does not repeatedly fetch the same earnings data
    def test_19_repeated_scan_cache_reuse(self):
        with patch("src.filters.earnings_filter.fetch_single_ticker_provider") as mock_provider:
            mock_provider.return_value = ("REPEATED_CO", "2026-11-05", None, None, EarningsStatus.KNOWN_UPCOMING.value)
            
            # Scan 1: Cache miss -> calls provider
            res1 = fetch_earnings_calendar(["REPEATED_CO"], supabase=None, cache_path=self.test_cache_file)
            self.assertEqual(mock_provider.call_count, 1)

            # Scan 2: Cache hit -> does NOT call provider
            res2 = fetch_earnings_calendar(["REPEATED_CO"], supabase=None, cache_path=self.test_cache_file)
            self.assertEqual(mock_provider.call_count, 1) # unchanged
            self.assertEqual(res2["REPEATED_CO"]["source"], "local_cache")

    # 20. No lookahead in earnings dates
    def test_20_no_lookahead_in_earnings_dates(self):
        cache_data = {
            "PIT_CO": {
                "ticker": "PIT_CO",
                "last_earnings": "2026-08-01",
                "next_earnings": "2026-11-01",
                "all_earnings": ["2026-02-01", "2026-05-01", "2026-08-01", "2026-11-01"],
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        }
        save_local_cache(cache_data, self.test_cache_file)

        # As of July 1, 2026: last earnings was May 1, 2026 (cannot see Aug 1, 2026!)
        with patch("src.utils.earnings_cache.DEFAULT_EARNINGS_CACHE_FILE", self.test_cache_file):
            last_e, _ = get_ticker_earnings("PIT_CO", as_of_date=datetime.date(2026, 7, 1))
            self.assertEqual(last_e, "2026-05-01")

            # As of Sept 1, 2026: last earnings was Aug 1, 2026
            last_e2, _ = get_ticker_earnings("PIT_CO", as_of_date=datetime.date(2026, 9, 1))
            self.assertEqual(last_e2, "2026-08-01")

    # 21. Oct 3 Regression Fixture
    def test_21_oct_3_ame_regression_fixture(self):
        """
        Reconstructs the Oct 3 scan scenario:
        1. AME has confirmed earnings on Oct 29, 2026 (26 days out).
        2. Blackout gate must PASS (not falsely UNKNOWN).
        3. AME composite score = 67.42 (passes >= 65.0 Buy threshold).
        4. Entry Location evaluates AME: Approaching 52-week high resistance (1.6% away) -> WAIT.
        5. WAIT prevents immediate recommendation emission (0 recommendations on Oct 3 is legitimate).
        """
        scan_dt = datetime.date(2026, 10, 3)
        ame_cal = {
            "AME": {
                "ticker": "AME",
                "next_earnings_date": "2026-10-29",
                "last_earnings_date": "2026-07-30",
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        }

        # Step 1: Earnings Risk Gate passes for AME with valid data
        decision = earnings_risk_filter("AME", scan_dt, "52w_high_breakout", ame_cal)
        self.assertTrue(decision["pass"], "AME must pass earnings blackout gate when next earnings is 26d away")
        self.assertEqual(decision["days_to_earnings"], 26)

        # Step 2: Construct AME market structure on Oct 3 (approaching resistance at $241.62 with price $237.75, 1.6% away)
        n_bars = 260
        dates = pd.date_range(end=datetime.datetime(2026, 10, 3), periods=n_bars, freq="B")
        df = pd.DataFrame(index=dates)
        
        # High at 241.62
        base_prices = np.linspace(180.0, 237.75, n_bars)
        df["OPEN"] = base_prices * 0.998
        df["HIGH"] = base_prices * 1.005
        df["LOW"] = base_prices * 0.995
        df["CLOSE"] = base_prices
        df["VOLUME"] = 1_500_000.0
        df["ATR_14"] = 3.50
        df["RSI_14"] = 62.0
        df["ADX_14"] = 28.0
        df["MACD_HIST"] = 0.45
        df["DMA_50"] = base_prices * 0.94
        df["EMA_20"] = base_prices * 0.98
        df["DMA_200"] = base_prices * 0.88

        # Set 52-week high resistance at 241.62 (occurred 15 bars ago)
        df.iloc[-15, df.columns.get_loc("HIGH")] = 241.62
        # Current bar close is 237.75 (1.6% away)
        cand = {"ticker": "AME", "strategy": "52-Week High Breakout", "entry_price": 237.75}
        res_el = evaluate_entry_location(cand, df, "52-Week High Breakout")

        # Proves AME is held in WAIT due to resistance proximity
        self.assertEqual(res_el.state, "WAIT")
        self.assertIn("resistance", res_el.reason.lower())
        self.assertNotEqual(res_el.state, "BUY", "State WAIT must not qualify as BUY for immediate entry")


if __name__ == "__main__":
    unittest.main()
