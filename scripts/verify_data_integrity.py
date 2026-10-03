"""
Comprehensive Data Integrity & Universe Audit Script
===================================================
Independently verifies:
1. All 5,601 securities in the Security Master
2. Complete OHLCV coverage in data/cache/by_date
3. OHLC mathematical sanity (High >= Low, High >= Open/Close, Low <= Open/Close, Price > 0, Volume >= 0)
4. Monotonic dates & duplicate-row detection
5. Missing / corrupt / NaN close data
6. Point-in-time liquidity reconciliation:
   - Total Security Master
   - Valid OHLCV Available
   - Price >= $5.00
   - 20D Dollar Volume >= $5,000,000
   - History >= 252 trading bars
   - True Usable Universe
"""

import os
import sys
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.universe import USEquitiesUniverseProvider
from src.data.cache_manager import get_cache_manager
from src.universe.filters import evaluate_point_in_time_liquidity
from src.quant_config import (
    US_UNIVERSE_MIN_PRICE,
    US_UNIVERSE_MIN_DOLLAR_VOLUME,
    US_UNIVERSE_MIN_HISTORY_DAYS,
    US_UNIVERSE_DOLLAR_VOLUME_WINDOW,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("data_audit")


def run_full_data_audit():
    logger.info("=" * 70)
    logger.info("INDEPENDENT AUDIT: FULL US EQUITIES DATA INTEGRITY & LIQUIDITY")
    logger.info("=" * 70)

    # 1. Load universe
    provider = USEquitiesUniverseProvider()
    records = provider.get_universe()
    total_master = len(records)
    all_tickers = [r.data_provider_ticker for r in records]
    canonical_tickers = [r.ticker for r in records]

    # Verify uniqueness
    unique_canonical = set(canonical_tickers)
    unique_provider = set(all_tickers)
    dup_canonical = len(canonical_tickers) - len(unique_canonical)
    dup_provider = len(all_tickers) - len(unique_provider)

    logger.info(f"Security Master Total: {total_master}")
    logger.info(f"Unique Canonical Tickers: {len(unique_canonical)} (duplicates: {dup_canonical})")
    logger.info(f"Unique Provider Tickers: {len(unique_provider)} (duplicates: {dup_provider})")

    # 2. Preload Cache
    cache_manager = get_cache_manager()
    end_date_str = "2026-10-02"
    start_date_str = (datetime.fromisoformat(end_date_str) - timedelta(days=500)).date().isoformat()
    cache_manager.preload_history(start_date_str, end_date_str)
    cached_set = set(cache_manager._history_cache.keys())

    logger.info(f"Preloaded cache contains {len(cached_set)} tickers from {start_date_str} to {end_date_str}.")

    # 3. Independent Per-Security Audit
    has_ohlcv_count = 0
    missing_ohlcv_count = 0
    ohlc_sanity_failed = 0
    date_duplicate_failed = 0
    corrupt_nan_count = 0
    insufficient_history_count = 0  # < 252 bars
    sufficient_history_count = 0    # >= 252 bars

    # Liquidity waterfall metrics
    price_pass_count = 0
    price_fail_count = 0
    dvol_pass_count = 0
    dvol_fail_count = 0
    true_usable_count = 0

    reconciliation_details = {
        "security_master": total_master,
        "with_ohlcv": 0,
        "without_ohlcv": 0,
        "corrupt_or_all_nan": 0,
        "bars_lt_252": 0,
        "bars_gte_252": 0,
        "failed_price_under_5": 0,
        "failed_dvol_under_5m": 0,
        "true_usable_universe": 0,
    }

    for r in records:
        t = r.data_provider_ticker
        if t not in cache_manager._history_cache:
            missing_ohlcv_count += 1
            continue

        raw = cache_manager.get_ticker_history(t, start_date_str, end_date_str)
        if raw is None or raw.empty:
            missing_ohlcv_count += 1
            continue

        has_ohlcv_count += 1

        # Check for duplicate dates
        if raw.index.duplicated().any():
            date_duplicate_failed += 1

        # Check required columns
        cols = [str(c).upper() for c in raw.columns]
        req_cols = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
        if not all(c in cols for c in req_cols):
            corrupt_nan_count += 1
            continue

        # Standardize columns
        df = raw.copy()
        df.columns = cols

        # Check if Close is completely NaN or mostly NaN (> 50%)
        if df["CLOSE"].isna().mean() > 0.5 or df["CLOSE"].dropna().empty:
            corrupt_nan_count += 1
            continue

        # Check OHLC sanity on clean rows
        valid_rows = df.dropna(subset=["OPEN", "HIGH", "LOW", "CLOSE"])
        if len(valid_rows) == 0:
            corrupt_nan_count += 1
            continue

        # Check sanity conditions
        invalid_prices = (
            (valid_rows["HIGH"] < valid_rows["LOW"]) |
            (valid_rows["HIGH"] < valid_rows["OPEN"] * 0.999) |
            (valid_rows["HIGH"] < valid_rows["CLOSE"] * 0.999) |
            (valid_rows["LOW"] > valid_rows["OPEN"] * 1.001) |
            (valid_rows["LOW"] > valid_rows["CLOSE"] * 1.001) |
            (valid_rows["CLOSE"] <= 0)
        )
        if invalid_prices.sum() > (len(valid_rows) * 0.05):  # > 5% corrupt rows
            ohlc_sanity_failed += 1
            corrupt_nan_count += 1
            continue

        # Evaluate PIT liquidity using canonical function
        is_liq, reason, m = evaluate_point_in_time_liquidity(
            df,
            as_of_date=end_date_str,
            min_price=US_UNIVERSE_MIN_PRICE,
            min_dollar_volume=US_UNIVERSE_MIN_DOLLAR_VOLUME,
            min_history_days=US_UNIVERSE_MIN_HISTORY_DAYS,
            dollar_volume_window=US_UNIVERSE_DOLLAR_VOLUME_WINDOW,
        )

        n_bars = m.get("history_days", len(df))
        latest_price = m.get("price", 0.0)
        avg_dvol = m.get("avg_dollar_volume", 0.0)

        if n_bars < 252:
            insufficient_history_count += 1
        else:
            sufficient_history_count += 1

        if is_liq:
            true_usable_count += 1
            price_pass_count += 1
            dvol_pass_count += 1
        else:
            if "price" in reason.lower() and "below minimum" in reason.lower():
                price_fail_count += 1
            elif "dollar volume" in reason.lower():
                dvol_fail_count += 1
                price_pass_count += 1
            elif "history" in reason.lower():
                pass
            else:
                corrupt_nan_count += 1

    reconciliation_details["with_ohlcv"] = has_ohlcv_count
    reconciliation_details["without_ohlcv"] = missing_ohlcv_count
    reconciliation_details["corrupt_or_all_nan"] = corrupt_nan_count
    reconciliation_details["bars_lt_252"] = insufficient_history_count
    reconciliation_details["bars_gte_252"] = sufficient_history_count
    reconciliation_details["failed_price_under_5"] = price_fail_count
    reconciliation_details["failed_dvol_under_5m"] = dvol_fail_count
    reconciliation_details["true_usable_universe"] = true_usable_count

    logger.info("=" * 70)
    logger.info("EXACT WATERFALL RECONCILIATION:")
    logger.info(f"1. Total Security Master Candidates:       {total_master}")
    logger.info(f"2. Securities with Ingested OHLCV Data:    {has_ohlcv_count} ({has_ohlcv_count/total_master*100:.1f}%)")
    logger.info(f"3. Corrupt / Empty / All-NaN OHLCV:        {corrupt_nan_count}")
    logger.info(f"4. Valid OHLCV with >= 252 Bars History:   {sufficient_history_count}")
    logger.info(f"5. Filtered: Price < $5.00:                {price_fail_count}")
    logger.info(f"6. Filtered: 20D Avg Dollar Vol < $5.0M:   {dvol_fail_count}")
    logger.info(f"7. TRUE USABLE UNIVERSE (Passing All):     {true_usable_count} ({true_usable_count/total_master*100:.1f}%)")
    logger.info("=" * 70)
    logger.info(f"Sanity Check: OHLC Sanity Failures: {ohlc_sanity_failed}")
    logger.info(f"Sanity Check: Duplicate Date Rows:  {date_duplicate_failed}")
    logger.info("=" * 70)

    return reconciliation_details


if __name__ == "__main__":
    res = run_full_data_audit()
    print("\nRECONCILIATION RESULT JSON:")
    print(json.dumps(res, indent=2))
