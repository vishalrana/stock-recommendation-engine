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


def sec_acceptance_audit(cache_dir: str) -> dict:
    """Per fetch hour (UTC) of the cached SEC documents: 'recent' filings and the share accepted
    before 06:00 ET. EDGAR accepts filings from 06:00 ET, so a large share means that hour's
    documents were parsed with the wrong acceptanceDateTime convention (times 4-5 h early)."""
    out: dict = {}
    for path in glob.glob(os.path.join(cache_dir, "CIK*.json")):
        try:
            doc = json.load(open(path, encoding="utf-8"))
            hour = time.strftime("%Y-%m-%dT%H", time.gmtime(float(doc["fetched_at"])))
        except Exception:
            continue
        b = out.setdefault(hour, {"documents": 0, "filings": 0, "before_06_et": 0})
        b["documents"] += 1
        for r in doc.get("recent_rows", []):
            b["filings"] += 1
            b["before_06_et"] += int(r[1][11:13]) < 6
    for b in out.values():
        b["share_before_06_et"] = round(b["before_06_et"] / b["filings"], 3) if b["filings"] else None
    return dict(sorted(out.items()))


def screen(cm, tickers, session: date, wanted) -> dict:
    """The live Cross-Sectional screen on `session` from the cache: counts, the cutoff, the ranks of
    `wanted`, and the full ranking written to outputs/screen_<session>.csv (uploaded by the workflow)."""
    from src.pipeline_steps import CROSS_SECTIONAL_TOP_FRACTION, cross_sectional_ranking
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
    ranking = cross_sectional_ranking(frame, frame.index[-1])
    os.makedirs("outputs", exist_ok=True)
    ranking.to_csv(os.path.join("outputs", f"screen_{frame.index[-1].date().isoformat()}.csv"), index=False)
    top = max(1, int(len(ranking) * CROSS_SECTIONAL_TOP_FRACTION))
    by = ranking.set_index("ticker")
    return {"session": frame.index[-1].date().isoformat(), "stocks": len(tickers), "with_history": len(closes),
            "ranked": len(ranking), "top": top,
            "wanted": {t: {"rank": int(by.at[t, "rank"]) if t in by.index else None,
                           "ret63_pct": float(by.at[t, "ret63_pct"]) if t in by.index else None,
                           "cached_rows_120d": int(frame[t].notna().sum()) if t in frame else 0} for t in wanted}}


DRIFT_TOLERANCE = 0.001   # a close that moved more than 0.1% between the live cache and the rebuild
LONG_BARS = 252            # a year of bars: what the scan needs


def drift_report(old: pd.DataFrame, new: pd.DataFrame, tickers, session: date) -> dict:
    """
    What the rebuild changes against the live cache (closes, sessions x tickers), before any swap:
    tickers whose close moved more than DRIFT_TOLERANCE on a common session, tickers newly at a
    year of bars, tickers with fewer bars (counted on the rebuild's date range), plus the checks:
    coverage of the live cache's last session and of tickers holding a year of bars.
    Bars are non-missing closes, so close-less rows of older caches do not count.
    """
    wanted = set(tickers)
    if old.empty or new.empty:
        return {"session_coverage_pct": 0.0, "history_kept_pct": 0.0, "last_bar": None,
                "drifted": [], "newly_long": [], "shorter": []}
    common_t = sorted(set(old.columns) & set(new.columns) & wanted)
    common_d = old.index.intersection(new.index)
    rel = (new.loc[common_d, common_t] / old.loc[common_d, common_t] - 1.0).abs()
    drifted = sorted(t for t in common_t if (rel[t] > DRIFT_TOLERANCE).any())
    old_n = old.loc[old.index >= new.index.min()].notna().sum()
    new_n = new.notna().sum()
    newly_long = sorted(t for t in wanted if new_n.get(t, 0) >= LONG_BARS > old_n.get(t, 0))
    shorter = sorted(t for t in wanted if old_n.get(t, 0) > new_n.get(t, 0))
    last_old = old.index.max()                        # the live cache's last session
    had = {t for t in wanted if t in old.columns and pd.notna(old.at[last_old, t])}
    has = {t for t in had if t in new.columns and last_old in new.index and pd.notna(new.at[last_old, t])}
    long_old = {t for t in wanted if old_n.get(t, 0) >= LONG_BARS}
    return {
        "session_coverage_pct": round(100 * len(has) / max(1, len(had)), 2),
        "history_kept_pct": round(float(100 * sum(new_n.get(t, 0) >= LONG_BARS for t in long_old) / max(1, len(long_old))), 2),
        "last_bar": new.index.max().date().isoformat(), "compared_session": last_old.date().isoformat(),
        "drifted": drifted, "newly_long": newly_long, "shorter": shorter,
    }


MIN_SESSION_COVERAGE_PCT = 99.0


def trim_partial_last_session(closes: pd.DataFrame, cache_dir: str):
    """
    Coverage of the rebuild's last session (the one the saved cache will claim as its last bar),
    against the tickers with a close on the session before it. Yahoo posts a session's bars over
    hours, so a rebuild run soon after the close can hold it for a few tickers only; below
    MIN_SESSION_COVERAGE_PCT that session's daily file is deleted, the cache then claims the session
    before, and the next nightly downloads the dropped session for every ticker.
    Returns (coverage %, the dropped session or None).
    """
    if len(closes.index) < 2:
        return None, None
    last, prev = closes.index[-1], closes.index[-2]
    base = closes.loc[prev].notna()
    cov = round(float(100.0 * (closes.loc[last].notna() & base).sum() / max(1, int(base.sum()))), 2)
    if cov >= MIN_SESSION_COVERAGE_PCT:
        return cov, None
    path = os.path.join(cache_dir, f"{last.date().isoformat()}.parquet")
    if os.path.exists(path):
        os.remove(path)
    return cov, last.date().isoformat()


def write_summary(r: dict) -> None:
    """Counts and lists in the GitHub job summary (near-zero counts mean the nightly refetch holds)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    few = lambda xs: ", ".join(xs[:60]) + (f" (+{len(xs) - 60})" if len(xs) > 60 else "") if xs else "none"
    flags = r.get("split_flagged", {})
    lines = [f"### Price cache rebuild: {'PASSED, cache replaced' if r['passed'] else 'FAILED, cache kept'}", "",
             f"Last session {r.get('end_session')}: {r.get('end_coverage_pct')}% of tickers"
             + (f", below {MIN_SESSION_COVERAGE_PCT:g}%: dropped, the nightly downloads it" if r.get("dropped_session") else "")
             + f" · coverage of {r.get('compared_session')}: {r['session_coverage_pct']}% · tickers keeping a year of bars: "
             f"{r['history_kept_pct']}% · last bar {r['last_bar']} · {r.get('seconds')} s", "",
             "| Drift report | Count | Tickers |", "|---|---|---|",
             f"| Closes moved > 0.1% on a common session | {len(r['drifted'])} | {few(r['drifted'])} |",
             f"| Newly at {LONG_BARS}+ bars | {len(r['newly_long'])} | {few(r['newly_long'])} |",
             f"| Fewer bars than the live cache | {len(r['shorter'])} | {few(r['shorter'])} |",
             f"| Split Yahoo left unadjusted | {len(flags)} | {few([f'{t} {d[0]}' for t, d in sorted(flags.items())])} |", ""]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions-since", default=None)
    ap.add_argument("--screen", default=None)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--sec-events", default="", help="comma-separated tickers: their 8-K item 2.02 events as the "
                    "restored cache holds them (no refetch), for replay diagnosis")
    ap.add_argument("--sec-audit", action="store_true", help="acceptance-time check of every cached SEC document")
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
    if a.sec_events:
        from src.providers.context.sec_filings import LIVE_CACHE_DIR, SecFilingsProvider, earnings_events
        cached = SecFilingsProvider(cache_dir=LIVE_CACHE_DIR, ttl_hours=None)   # cached rows only, as the nightly read them
        out["sec_events"] = {}
        for t in [x.strip().upper() for x in a.sec_events.split(",") if x.strip()]:
            f = cached.filings(t)
            cik = cached.sec.cik_for(t)
            try:
                doc = json.load(open(cached._path(int(cik)), encoding="utf-8")) if cik else {}
            except Exception:
                doc = {}
            out.setdefault("sec_fetched_at", {})[t] = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(doc["fetched_at"]))
                                                      if doc.get("fetched_at") else doc.get("fetched_at"))
            out["sec_events"][t] = None if f is None else [
                {"announced": e.announced.isoformat(), "reaction": e.reaction.isoformat(), "accepted": e.accepted.isoformat()}
                for e in earnings_events(f)][-8:]

    if a.sec_audit:
        from src.providers.context.sec_filings import LIVE_CACHE_DIR
        out["sec_audit"] = sec_acceptance_audit(LIVE_CACHE_DIR)

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
        new_closes = rebuilt.cached_closes("0000-00-00", "9999-99-99")
        end_cov, dropped = trim_partial_last_session(new_closes, fresh)
        if dropped:
            new_closes = new_closes.drop(index=pd.Timestamp(dropped))
        report = drift_report(CacheManager(cache_dir=CACHE_DIR).cached_closes("0000-00-00", "9999-99-99"),
                              new_closes, tickers, end)
        claims = new_closes.index.max().date().isoformat() if len(new_closes) else None
        # The saved cache claims `end` only when nearly every ticker has it; otherwise the session before
        passed = (report["session_coverage_pct"] >= 99.0 and report["history_kept_pct"] >= 99.0
                  and claims is not None and (dropped is not None or claims == end.isoformat()))
        out["rebuild"] = dict(report, passed=passed, seconds=round(time.time() - t0, 1), tickers=len(tickers),
                              end_session=end.isoformat(), end_coverage_pct=end_cov, dropped_session=dropped,
                              split_flagged={t: split_flags[t] for t in sorted(split_flags)})
        write_summary(out["rebuild"])
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
