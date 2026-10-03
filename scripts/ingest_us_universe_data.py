"""
Ingest OHLCV Price History for Full US Equities Universe
=========================================================
Downloads and caches historical price data for all 5,601 eligible US common equities
into the date-partitioned parquet cache (data/cache/by_date).

Features:
- Incremental: detects existing cached securities and only fetches missing/incomplete tickers.
- Batch downloading: chunks into 100-ticker requests.
- Polite rate-limiting: exponential backoff on 429 errors.
- Thread-safe date-partitioned merge: saves and merges into daily parquet files.
- Zero lookahead bias: fetches strictly up to today's date.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.universe import USEquitiesUniverseProvider
from src.data.cache_manager import get_cache_manager
import yfinance as yf
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("universe_ingest")


def ingest_us_universe(batch_size: int = 100, max_workers: int = 3, force_all: bool = False):
    logger.info("=" * 60)
    logger.info("US EQUITIES UNIVERSE OHLCV INGESTION")
    logger.info("=" * 60)

    # 1. Load universe provider
    provider = USEquitiesUniverseProvider()
    all_records = provider.get_universe()
    all_tickers = [r.data_provider_ticker for r in all_records]
    logger.info(f"Loaded {len(all_tickers)} eligible common equities from Security Master.")

    # 2. Check existing cache
    cache_manager = get_cache_manager()
    end_date_dt = datetime.now().date()
    start_date_dt = end_date_dt - timedelta(days=500)
    start_str = start_date_dt.isoformat()
    end_str = end_date_dt.isoformat()

    logger.info(f"Checking existing cache from {start_str} to {end_str}...")
    cache_manager.preload_history(start_str, end_str)
    cached_tickers = set(cache_manager._history_cache.keys())
    logger.info(f"Existing cache contains data for {len(cached_tickers)} tickers.")

    if force_all:
        missing_tickers = all_tickers
    else:
        # Tickers with missing or incomplete history (< 200 bars)
        missing_tickers = [
            t for t in all_tickers
            if t not in cached_tickers or len(cache_manager._history_cache[t]) < 200
        ]

    logger.info(f"Identified {len(missing_tickers)} tickers requiring OHLCV download.")
    if not missing_tickers:
        logger.info("All tickers are already cached. Nothing to download.")
        return len(cached_tickers)

    # 3. Download in parallel batches
    chunks = [missing_tickers[i:i + batch_size] for i in range(0, len(missing_tickers), batch_size)]
    total_chunks = len(chunks)
    logger.info(f"Processing {len(missing_tickers)} tickers in {total_chunks} batches (size={batch_size}, workers={max_workers})...")

    def fetch_batch(batch_info):
        chunk_idx, chunk_tickers = batch_info
        t0 = time.time()
        for attempt in range(3):
            try:
                df = yf.download(
                    chunk_tickers,
                    start=start_str,
                    end=end_str,
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                    timeout=30,
                )
                if df is not None and not df.empty:
                    valid_cols = [c for c in df.columns if not df[c].isna().all()]
                    return chunk_idx, df, time.time() - t0
            except Exception as e:
                if "429" in str(e):
                    time.sleep(2 * (attempt + 1))
                else:
                    time.sleep(1)
        return chunk_idx, pd.DataFrame(), time.time() - t0

    t_ingest_start = time.time()
    successful_chunks = 0
    total_dates_updated = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_batch, (idx + 1, chunk)): idx + 1 for idx, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            chunk_num = futures[future]
            try:
                idx, chunk_df, duration = future.result()
                if not chunk_df.empty:
                    dates_updated = cache_manager.ingest_dataframe_to_cache(chunk_df)
                    total_dates_updated += dates_updated
                    successful_chunks += 1
                    logger.info(
                        f"[INGESTION PROGRESS] Chunk {chunk_num}/{total_chunks} ingested in {duration:.1f}s ({dates_updated} dates updated)."
                    )
                else:
                    logger.warning(f"[INGESTION PROGRESS] Chunk {chunk_num}/{total_chunks} returned empty dataframe.")
            except Exception as e:
                logger.error(f"[INGESTION PROGRESS] Chunk {chunk_num}/{total_chunks} failed: {e}")

    total_duration = time.time() - t_ingest_start
    logger.info("=" * 60)
    logger.info(f"INGESTION COMPLETE in {total_duration:.1f}s. Successful batches: {successful_chunks}/{total_chunks}")
    logger.info("=" * 60)

    # 4. Verify post-ingestion cache coverage
    logger.info("Verifying updated cache coverage...")
    cache_manager.preload_history(start_str, end_str)
    final_cached = set(cache_manager._history_cache.keys())
    eligible_cached = [t for t in all_tickers if t in final_cached]
    pct = (len(eligible_cached) / len(all_tickers) * 100) if all_tickers else 0.0

    logger.info(f"Final Cache Coverage: {len(eligible_cached)} / {len(all_tickers)} eligible equities ({pct:.1f}%).")
    return len(eligible_cached)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    ingest_us_universe(batch_size=args.batch_size, max_workers=args.workers, force_all=args.force)
