"""
Validate the SEC EDGAR earnings calendar against the stored calendar
=====================================================================
Compares SEC 8-K item 2.02 announcement dates (src/providers/context/sec_filings.py) with the
last_earnings_date values in the Supabase earnings_calendar table (Yahoo-sourced report dates).
A ticker matches when an SEC announcement lies within +/-1 NYSE session of the stored date.
Target (profitability program, Phase 2.1): match rate >= 90%. Misses are listed with a reason.

Read-only: nothing is written to Supabase. Output: outputs/research/sec_earnings_validation.json

Usage: python scripts/validate_sec_earnings_calendar.py [--limit N]
"""

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from src.providers.context.sec_filings import SecFilingsProvider, earnings_events  # noqa: E402
from src.trade_hygiene import SessionCalendar  # noqa: E402

OUT = os.path.join(PROJECT_ROOT, "outputs", "research", "sec_earnings_validation.json")


def session_gap(a: date, b: date, cal: SessionCalendar) -> int:
    """Signed number of sessions from a to b (0 when both roll to the same session)."""
    lo, hi = (a, b) if a <= b else (b, a)
    n, cur = 0, cal.shift(lo, 0)
    end = cal.shift(hi, 0)
    while cur < end and n < 400:
        cur = cal.shift(cur, 1)
        n += 1
    return n if a <= b else -n


def load_calendar():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.supabase_client import get_client
    c = get_client()
    rows, start = [], 0
    while True:
        r = c.table("earnings_calendar").select("ticker,last_earnings_date,next_earnings_date,updated_at") \
            .range(start, start + 999).execute().data
        rows += r
        if len(r) < 1000:
            return rows
        start += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    rows = [r for r in load_calendar() if r.get("last_earnings_date")]
    if args.limit:
        rows = rows[: args.limit]
    prov = SecFilingsProvider()
    if not prov.available:
        print("SEC_USER_AGENT not set; cannot validate.")
        return 1
    prov.sec.ticker_map()
    cal = SessionCalendar()
    tickers = [r["ticker"].upper() for r in rows]
    with ThreadPoolExecutor(max_workers=4) as pool:
        filings = dict(zip(tickers, pool.map(prov.filings, tickers)))

    results, reasons = [], Counter()
    for r in rows:
        t = r["ticker"].upper()
        stored = date.fromisoformat(str(r["last_earnings_date"])[:10])
        f = filings.get(t)
        if f is None:
            reason = "no SEC filer (no CIK or fetch failed)"
            results.append({"ticker": t, "stored": stored.isoformat(), "match": False, "reason": reason})
            reasons[reason] += 1
            continue
        ev = earnings_events(f)
        if not ev:
            reason = "no 8-K item 2.02 filings"
            results.append({"ticker": t, "stored": stored.isoformat(), "match": False, "reason": reason})
            reasons[reason] += 1
            continue
        nearest = min(ev, key=lambda e: abs((e.announced - stored).days))
        gap = session_gap(stored, nearest.announced, cal)
        ok = abs(gap) <= 1
        reason = None if ok else "date differs by more than one session"
        if reason:
            reasons[reason] += 1
        results.append({"ticker": t, "stored": stored.isoformat(), "sec_announced": nearest.announced.isoformat(),
                        "sec_reaction": nearest.reaction.isoformat(), "session_gap": gap, "match": ok, "reason": reason})

    n = len(results)
    matched = sum(1 for x in results if x["match"])
    covered = [x for x in results if "sec_announced" in x]
    summary = {
        "generated_on": date.today().isoformat(),
        "compared": n,
        "matched_within_1_session": matched,
        "match_rate_pct": round(100.0 * matched / n, 2) if n else None,
        "covered_by_sec_8k_202": len(covered),
        "match_rate_among_covered_pct": round(100.0 * sum(1 for x in covered if x["match"]) / len(covered), 2) if covered else None,
        "target_pct": 90.0,
        "miss_reasons": dict(reasons),
        "session_gap_distribution": dict(Counter(x["session_gap"] for x in covered if abs(x["session_gap"]) <= 5)),
        "misses": [x for x in results if not x["match"]][:200],
        "sec_stats": prov.stats,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps({k: v for k, v in summary.items() if k != "misses"}, indent=2))
    print("first misses:", [(m["ticker"], m["stored"], m.get("sec_announced"), m["reason"]) for m in summary["misses"][:15]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
