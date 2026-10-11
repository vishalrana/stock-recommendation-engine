"""
SEC Filings Long-History Builder (2006 Onward)
==============================================
Builds 8-K items, deal forms, rights-offering days and earnings_events from 2006-01-01
for U1 and U2 universes using SecFilingsProvider(since=2006-01-01).
Reports earnings-event coverage per year.
"""

import argparse
import json
import logging
import os
import sys
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.providers.context.sec_filings import (
    SecFilingsProvider, earnings_events, event_lock, compact_rows, filings_from_rows
)
from src.research.survivorship import load_membership, yahoo_symbol

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "backtest_history_long", "sec_filings")
REPORT_PATH = os.path.join(PROJECT_ROOT, "outputs", "swing", "sec_coverage_summary.json")


def get_u1_u2_tickers() -> Tuple[List[str], List[str]]:
    """Return distinct U1 and U2 tickers."""
    u1_tickers: Set[str] = set()
    u2_tickers: Set[str] = set()

    try:
        import jobs.generate_signals as gs
        u1, _, _ = gs.load_universe("expanded")
        for t in u1:
            if t and isinstance(t, str):
                u1_tickers.add(yahoo_symbol(t))
    except Exception as e:
        logger.warning(f"Could not load U1: {e}")

    try:
        m = load_membership()
        start_bound = pd.Timestamp("2008-01-01")
        end_bound = pd.Timestamp("2022-12-31")
        for row in m.itertuples(index=False):
            end_d = row.end_date if pd.notna(row.end_date) else pd.Timestamp.max
            if row.start_date <= end_bound and end_d >= start_bound:
                u2_tickers.add(yahoo_symbol(row.ticker))
    except Exception as e:
        logger.warning(f"Could not load U2: {e}")

    return sorted(u1_tickers), sorted(u2_tickers)


def build_sec_history(
    u1_tickers: List[str],
    u2_tickers: List[str],
    cache_dir: str = CACHE_DIR,
    limit: Optional[int] = None,
) -> Dict[str, object]:
    """Build SEC history and compute earnings-event coverage by year."""
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    provider = SecFilingsProvider(cache_dir=cache_dir, ttl_hours=None, since=date(2006, 1, 1))

    all_tickers = sorted(set(u1_tickers) | set(u2_tickers))
    if limit is not None and limit > 0:
        all_tickers = all_tickers[:limit]

    logger.info(f"SEC Provider available: {provider.available}. Target tickers: {len(all_tickers)}")

    # Rights offerings search (2006 to 2026)
    logger.info("Checking rights offerings...")
    rights = None
    if provider.available:
        try:
            rights = provider.rights_offering_days(date(2006, 1, 1), date.today())
        except Exception as e:
            logger.warning(f"Rights offering search error: {e}")

    # Track coverage by year for U1 and U2
    # year -> {total_members, members_with_events, total_events}
    years = list(range(2006, date.today().year + 1))
    u1_by_year: Dict[int, Dict[str, int]] = {y: {"filers": 0, "events": 0} for y in years}
    u2_by_year: Dict[int, Dict[str, int]] = {y: {"filers": 0, "events": 0} for y in years}

    u1_set = set(u1_tickers)
    u2_set = set(u2_tickers)

    total_checked = 0
    total_filings_found = 0

    for idx, t in enumerate(all_tickers):
        filings = provider.filings(t)
        if filings:
            total_filings_found += 1
            evs = earnings_events(filings)
            ev_years = set()
            for ev in evs:
                y = ev.reaction.year
                if y in u1_by_year and t in u1_set:
                    u1_by_year[y]["events"] += 1
                if y in u2_by_year and t in u2_set:
                    u2_by_year[y]["events"] += 1
                ev_years.add(y)

            for y in ev_years:
                if y in u1_by_year and t in u1_set:
                    u1_by_year[y]["filers"] += 1
                if y in u2_by_year and t in u2_set:
                    u2_by_year[y]["filers"] += 1

        total_checked += 1
        if (idx + 1) % 100 == 0:
            logger.info(f"Processed {idx + 1}/{len(all_tickers)} tickers...")

    summary = {
        "provider_available": provider.available,
        "total_tickers_checked": total_checked,
        "tickers_with_filings": total_filings_found,
        "stats": provider.stats,
        "u1_coverage_by_year": {str(k): v for k, v in sorted(u1_by_year.items())},
        "u2_coverage_by_year": {str(k): v for k, v in sorted(u2_by_year.items())},
        "rights_offerings_hit_count": len(rights) if rights else 0,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    logger.info(f"SEC history coverage report written to {REPORT_PATH}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build SEC long history filings and coverage report.")
    parser.add_argument("--limit", type=int, default=None, help="Limit tickers for smoke testing.")
    parser.add_argument("--cache-dir", type=str, default=CACHE_DIR, help="SEC cache directory.")
    args = parser.parse_args()

    u1, u2 = get_u1_u2_tickers()
    build_sec_history(u1, u2, cache_dir=args.cache_dir, limit=args.limit)
