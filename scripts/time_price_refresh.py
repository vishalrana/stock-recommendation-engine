"""
Nightly price cache tools, run by .github/workflows/price_refresh_timing.yml on a copy of the
nightly's cache (no Supabase access).

  python scripts/time_price_refresh.py [--actions-since YYYY-MM-DD]     time refresh_with_actions
  python scripts/time_price_refresh.py --screen YYYY-MM-DD --tickers A,B  the live Cross-Sectional
                                         screen on that session: stocks ranked, and those tickers' ranks
  python scripts/time_price_refresh.py --rebuild                        full adjusted history into a
                                         fresh directory; replaces data/cache/by_date only if it passes
                                         coverage, history-length and last-bar checks (exit 1 otherwise)

--actions-since counts dividends and splits from that date as new (a heavier-than-usual night);
the default is the last cached session, as the nightly does.
"""

import argparse
import glob
import json
import os
import shutil
import sys
import time
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACHE_DIR = os.path.join("data", "cache", "by_date")


def rows_per_ticker(cache_dir: str) -> pd.Series:
    idx = [pd.read_parquet(f, columns=["CLOSE"]).index.get_level_values("Ticker")
           for f in glob.glob(os.path.join(cache_dir, "*.parquet"))]
    return pd.Series(pd.Index([]).append(idx) if idx else [], dtype=object).value_counts()


def on_session(cache_dir: str, session: date) -> set:
    p = os.path.join(cache_dir, f"{session.isoformat()}.parquet")
    return set(pd.read_parquet(p, columns=["CLOSE"]).index.get_level_values("Ticker")) if os.path.exists(p) else set()


def screen(cm, tickers, session: date, wanted) -> dict:
    from src.pipeline_steps import CROSS_SECTIONAL_LOOKBACK, CROSS_SECTIONAL_TOP_FRACTION
    start = (session - timedelta(days=120)).isoformat()
    cm.preload_history(start, session.isoformat())   # as the nightly does; per-ticker disk reads take hours
    closes = {}
    for t in tickers:
        h = cm.get_ticker_history(t, start, session.isoformat())
        if h is not None and len(h):
            closes[t] = h["CLOSE"]
    frame = pd.DataFrame(closes).sort_index()
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
    frame = frame.loc[:pd.Timestamp(session)]
    ret = (frame / frame.shift(CROSS_SECTIONAL_LOOKBACK) - 1.0).iloc[-1].dropna()
    ranked = sorted(ret.items(), key=lambda kv: (-kv[1], kv[0]))
    top = max(1, int(len(ranked) * CROSS_SECTIONAL_TOP_FRACTION))
    rank = {t: i + 1 for i, (t, _) in enumerate(ranked)}
    return {"session": frame.index[-1].date().isoformat(), "stocks": len(tickers), "with_history": len(closes),
            "ranked": len(ranked), "top": top,
            "wanted": {t: {"rank": rank.get(t), "ret63_pct": round(100 * float(ret[t]), 3) if t in ret else None,
                           "cached_rows_120d": int(frame[t].notna().sum()) if t in frame else 0} for t in wanted}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions-since", default=None)
    ap.add_argument("--screen", default=None)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    import jobs.generate_signals as gs
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    from src.data.cache_manager import CacheManager, get_cache_manager
    from src.utils.market_date import latest_completed_session

    universe, _, _ = gs.load_universe("expanded")
    stocks = [t for t in dict.fromkeys(universe) if t not in SECTOR_ETFS]
    from src.data.cache_manager import OPEN_TICKERS_FILE
    try:   # open and pending ideas, as the nightly last saved them (this tool has no Supabase access)
        with open(OPEN_TICKERS_FILE, encoding="utf-8") as f:
            open_now = [str(t).upper() for t in json.load(f)]
    except Exception:
        open_now = []
    tickers = [t for t in dict.fromkeys(universe + list(SECTOR_ETFS) + open_now) if t not in gs.BLACKLIST]
    end = latest_completed_session()
    full_start = (end - timedelta(days=500)).isoformat()
    out = {}

    if a.screen:
        out["screen"] = screen(get_cache_manager(), stocks, date.fromisoformat(a.screen),
                               [t.strip().upper() for t in a.tickers.split(",") if t.strip()])

    if a.rebuild:
        fresh = CACHE_DIR + ".rebuild"
        shutil.rmtree(fresh, ignore_errors=True)
        t0 = time.time()
        rebuilt = CacheManager(cache_dir=fresh)
        rebuilt.refresh_cache(tickers, full_start, end.isoformat(), actions=True)   # split events seed the check
        split_flags = rebuilt.update_split_flags()
        old_rows, new_rows = rows_per_ticker(CACHE_DIR), rows_per_ticker(fresh)
        old_on, new_on = on_session(CACHE_DIR, end), on_session(fresh, end)
        long_old = set(old_rows[old_rows >= 253].index) & set(tickers)
        checks = {
            # every ticker the live cache had on the session is there again
            "session_coverage_pct": round(100 * len(old_on & new_on & set(tickers)) / max(1, len(old_on & set(tickers))), 2),
            # every ticker with a full year of history keeps it
            "history_kept_pct": round(100 * sum(new_rows.get(t, 0) >= 253 for t in long_old) / max(1, len(long_old)), 2),
            "last_bar": max((os.path.basename(f)[:10] for f in glob.glob(os.path.join(fresh, "*.parquet"))), default=None),
        }
        passed = checks["session_coverage_pct"] >= 99.0 and checks["history_kept_pct"] >= 99.0 and checks["last_bar"] == end.isoformat()
        out["rebuild"] = dict(checks, passed=passed, seconds=round(time.time() - t0, 1), tickers=len(tickers),
                              old_files=len(glob.glob(os.path.join(CACHE_DIR, "*.parquet"))),
                              new_files=len(glob.glob(os.path.join(fresh, "*.parquet"))),
                              newly_long=int(sum(new_rows.get(t, 0) >= 253 > old_rows.get(t, 0) for t in tickers)),
                              split_flagged={t: split_flags[t] for t in sorted(split_flags)})
        if passed:
            shutil.rmtree(CACHE_DIR)
            os.replace(fresh, CACHE_DIR)
        print(json.dumps(out, indent=1))
        return 0 if passed else 1

    cm = get_cache_manager()
    last = cm.get_last_cached_date()
    if last is None:
        print("No cached price history to refresh.")
        return 1
    since = date.fromisoformat(a.actions_since) if a.actions_since else last
    start = min(last, since) - timedelta(days=gs.INCREMENTAL_OVERLAP_DAYS)
    t0 = time.time()
    stats = cm.refresh_with_actions(tickers, start.isoformat(), end.isoformat(), full_start=full_start, since=since)
    stats.update({"universe": len(tickers), "last_cached_before": last.isoformat(), "session": end.isoformat(),
                  "actions_since": since.isoformat(), "seconds_total": round(time.time() - t0, 1),
                  "pending_count": len(stats["pending"])})
    out["refresh"] = stats
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
