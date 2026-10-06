"""
Dedicated Earnings Calendar Refresh Utility
==========================================
Explicit, controlled operational script for refreshing earnings dates from the
external provider (yfinance) and persisting them into local JSON cache and Supabase.

Normal nightly market scans execute with zero provider network requests (budget=0).
This script runs on a dedicated schedule or operational trigger to refresh stale
earnings records under strict rate limits and circuit breaker control.

Usage:
    python jobs/refresh_earnings.py --universe benchmark --max-fetches 50
    python jobs/refresh_earnings.py --tickers AAPL MSFT NVDA --max-fetches 10
    python jobs/refresh_earnings.py --universe expanded --dry-run
"""

import sys
import os
import argparse
import logging
from typing import List, Optional

# Ensure project root in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.filters.earnings_filter import (
    fetch_earnings_calendar,
    GLOBAL_EARNINGS_TRACKER,
    GLOBAL_PROVIDER_BUDGET,
    reset_session_cache,
)
from jobs.generate_signals import load_universe
from jobs.supabase_client import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("refresh_earnings")


def refresh_earnings(
    tickers: Optional[List[str]] = None,
    universe_source: Optional[str] = "benchmark",
    max_fetches: int = 50,
    max_workers: int = 5,
    dry_run: bool = False,
) -> dict:
    logger.info("=" * 60)
    logger.info("Dedicated Earnings Calendar Refresh")
    logger.info("=" * 60)

    # Initialize Supabase client
    supabase = None
    if not dry_run:
        try:
            supabase = get_client()
            logger.info("Connected to Supabase client.")
        except Exception as e:
            logger.warning(f"Could not initialize Supabase client: {e}. Falling back to local cache only.")

    # Determine ticker list
    if tickers:
        target_tickers = [t.strip().upper() for t in tickers if t.strip()]
        logger.info(f"Targeting {len(target_tickers)} specific tickers: {', '.join(target_tickers[:10])}...")
    else:
        universe_tickers, _, _ = load_universe(source=universe_source)
        target_tickers = universe_tickers
        logger.info(f"Loaded {len(target_tickers)} tickers from universe '{universe_source}'")

    GLOBAL_EARNINGS_TRACKER.reset()
    reset_session_cache()
    GLOBAL_PROVIDER_BUDGET.set_budget(max_fetches)

    logger.info(
        "Beginning earnings refresh: allow_network=%s, budget=%d, workers=%d",
        not dry_run,
        max_fetches,
        max_workers,
    )

    calendar_map = fetch_earnings_calendar(
        tickers=target_tickers,
        supabase=supabase if not dry_run else None,
        allow_network=not dry_run,
        max_provider_fetches=max_fetches,
        max_workers=max_workers,
    )

    logger.info(GLOBAL_EARNINGS_TRACKER.format_summary())
    logger.info("Earnings refresh complete. Processed %d records.", len(calendar_map))
    logger.info("=" * 60)
    return calendar_map


def main():
    parser = argparse.ArgumentParser(description="Dedicated Earnings Calendar Refresh Utility")
    parser.add_argument(
        "--universe",
        choices=["expanded", "benchmark"],
        default="benchmark",
        help="Universe source ('benchmark' for S&P 500 + Nasdaq-100, 'expanded' for broad US common equities)",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Specific tickers to refresh (e.g. --tickers AAPL MSFT NVDA)",
    )
    parser.add_argument(
        "--max-fetches",
        type=int,
        default=50,
        help="Maximum external provider fetches allowed for this run (default: 50)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Worker threads for provider queries (default: 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run without network requests or persistence",
    )
    args = parser.parse_args()

    refresh_earnings(
        tickers=args.tickers,
        universe_source=args.universe,
        max_fetches=args.max_fetches,
        max_workers=args.workers,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
