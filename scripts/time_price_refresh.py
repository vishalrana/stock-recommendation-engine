"""
Time the nightly price refresh (CacheManager.refresh_with_actions) on a copy of the nightly's cache.
No Supabase access; run by .github/workflows/price_refresh_timing.yml, which does not save the cache.

  python scripts/time_price_refresh.py [--actions-since YYYY-MM-DD]

--actions-since counts dividends and splits from that date as new (e.g. a week back, for a
heavier-than-usual night); the default is the last cached session, as the nightly does.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions-since", default=None)
    a = ap.parse_args()
    import jobs.generate_signals as gs
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    from src.data.cache_manager import get_cache_manager
    from src.utils.market_date import latest_completed_session

    tickers, _, _ = gs.load_universe("expanded")
    tickers = [t for t in dict.fromkeys(tickers + list(SECTOR_ETFS)) if t not in gs.BLACKLIST]
    cm = get_cache_manager()
    last, end = cm.get_last_cached_date(), latest_completed_session()
    if last is None:
        print("No cached price history to refresh.")
        return 1
    since = date.fromisoformat(a.actions_since) if a.actions_since else last
    start = min(last, since) - timedelta(days=gs.INCREMENTAL_OVERLAP_DAYS)
    t0 = time.time()
    stats = cm.refresh_with_actions(tickers, start.isoformat(), end.isoformat(),
                                    full_start=(end - timedelta(days=500)).isoformat(), since=since)
    stats.update({"universe": len(tickers), "last_cached_before": last.isoformat(), "session": end.isoformat(),
                  "actions_since": since.isoformat(), "seconds_total": round(time.time() - t0, 1),
                  "pending_count": len(stats["pending"])})
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
