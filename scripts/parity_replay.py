"""
Parity replay: one live session through the live path and the backtest harness
=============================================================================
Compares what the nightly Ideas scan decided for a session (signals_history / signals rows of that
scan date) with what the backtest harness decides for the same session
(validate_backtest_pipeline.py --replay-session, which writes outputs/parity_replay_<date>_harness.csv).

It lists the issued sets and every candidate whose outcome differs, each with its cause:
  * candidate on one side only      the strategy scan saw different inputs (price bars, universe)
  * score differs                   price data or strategy evidence differ (score gap shown)
  * stage differs at the same score a rule read different inputs (stage on each side shown)
Exit code 0 when the issued sets match or every difference has a cause; 1 otherwise.

  python scripts/parity_replay.py --date 2026-10-19 --print-open           # tickers open at the session
  python scripts/parity_replay.py --date 2026-10-19 --harness-csv outputs/parity_replay_2026-10-19_harness.csv
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

SCORE_TOLERANCE = 0.05   # composite points; below this the scores are the same


MATERIAL_SCORE = 65.0   # the Buy threshold: differences at or above it fail the replay
RETURN_TOLERANCE = 0.001   # 63-session return, percentage points: both rankings round it to 4 decimals
CROSS_SECTIONAL = "Cross-Sectional Momentum"


def live_stage(reason: Optional[str]) -> str:
    """Stage name for a live rejection reason (None, NaN or empty = published; pandas 3.1 turns a
    missing string into NaN)."""
    r = reason.strip() if isinstance(reason, str) else ""
    if not r:
        return "issued"
    rules = [("Composite score", "score_below_65"), ("Validation failed", "scoring"), ("Scoring error", "scoring"),
             ("Target calculation invalid", "invalid_plan"), ("Tier ", "tier"), ("Stop cap", "rr_floor_stop_cap"),
             ("Earnings date unknown", "earnings_unknown"),
             ("Earnings date unavailable", "earnings_sec_unavailable"), ("Earnings in ", "earnings_blackout"),
             ("Earnings inside the hold", "earnings_full_hold"), ("Event lock", "event_lock"),
             ("WAIT:", "entry_location"), ("Rejected location", "entry_location"),
             ("Strategy deduplication", "dedupe"), ("Price data stale", "stale")]
    for prefix, stage in rules:
        if r.startswith(prefix):
            return stage
    return "other: " + r[:60]


def failed_closed(ticker: str, exclusions: Optional[Dict]) -> Optional[str]:
    """Class (a): the nightly left the ticker out on purpose (outputs/nightly_exclusions.json)."""
    if not exclusions:
        return None
    lists = [name for name in ("pending", "split_flagged", "stale") if ticker in set(exclusions.get(name) or [])]
    return f"(a) failed closed live: {', '.join(lists)}" if lists else None


def ranking_comparison(ticker: str, live: Dict, harness: pd.DataFrame) -> Dict:
    """Where a one-sided Cross-Sectional candidate sits in the live ranking (the nightly's saved
    cs_ranking) and in the harness's: rank, 63-session return and cutoff on each side; the tickers
    ranked above it on one side only; the tickers both sides rank but above it on one side only (their
    returns differ); and whether its own return differs."""
    from src.pipeline_steps import CROSS_SECTIONAL_TOP_FRACTION
    lv = pd.DataFrame(live["rows"], columns=["ticker", "ret63_pct", "rank"]).set_index("ticker")
    hv = harness.set_index("ticker")

    def place(df, cutoff):
        ranked = ticker in df.index
        return {"rank": int(df.at[ticker, "rank"]) if ranked else None,
                "ret63_pct": float(df.at[ticker, "ret63_pct"]) if ranked else None, "cutoff": cutoff, "ranked": int(len(df))}

    lp = place(lv, int(live["cutoff"]))
    hp = place(hv, max(1, int(len(hv) * CROSS_SECTIONAL_TOP_FRACTION)))
    above_l = set(lv.index[lv["rank"] < lp["rank"]]) if lp["rank"] else None
    above_h = set(hv.index[hv["rank"] < hp["rank"]]) if hp["rank"] else None
    both = set(lv.index) & set(hv.index)
    return {"live": lp, "harness": hp,
            "live_only_above": None if above_l is None else sorted(above_l - set(hv.index), key=lambda t: lv.at[t, "rank"]),
            "harness_only_above": None if above_h is None else sorted(above_h - set(lv.index), key=lambda t: hv.at[t, "rank"]),
            "crossing": None if above_l is None or above_h is None else sorted((above_l ^ above_h) & both),
            "own_return_differs": None if lp["rank"] is None or hp["rank"] is None
            else abs(lp["ret63_pct"] - hp["ret63_pct"]) > RETURN_TOLERANCE}


def ranking_line(ticker: str, r: Dict) -> str:
    """One summary line for ranking_comparison."""
    def side(p):
        return "not ranked" if p["rank"] is None else f"rank {p['rank']} of {p['ranked']} (cutoff {p['cutoff']})"

    def few(xs):
        return "n/a" if xs is None else (f"{len(xs)}" + (f" ({', '.join(xs[:15])}{f' +{len(xs) - 15}' if len(xs) > 15 else ''})" if xs else ""))

    own = ("not comparable" if r["own_return_differs"] is None else
           f"{'differs' if r['own_return_differs'] else 'same'} ({r['live']['ret63_pct']}% live, {r['harness']['ret63_pct']}% harness)")
    return (f"{ticker}: live {side(r['live'])}, harness {side(r['harness'])}; own 63-session return {own}; "
            f"ranked above it live only {few(r['live_only_above'])}, harness only {few(r['harness_only_above'])}; "
            f"ranked on both, above it on one side only {few(r['crossing'])}")


def compare(live: pd.DataFrame, harness: pd.DataFrame, live_open: List[str], exclusions: Optional[Dict] = None,
            harness_ranking: Optional[pd.DataFrame] = None) -> Dict:
    """live: ticker, strategy, composite_score, stage. harness: ticker, strategy, composite_score, stage.
    With the nightly's cs_ranking (in exclusions) and the harness's ranking, each one-sided
    Cross-Sectional candidate gets a ranking comparison (information only)."""
    key = ["ticker", "strategy"]
    m = live.merge(harness, on=key, how="outer", suffixes=("_live", "_bt"), indicator="side")
    diffs = []
    num = lambda v: None if pd.isna(v) else round(float(v), 2)
    for r in m.itertuples(index=False):
        k = f"{r.ticker} / {r.strategy}"
        side = r.side
        scores = {"score_live": num(r.composite_score_live), "score_harness": num(r.composite_score_bt)}
        if side == "left_only":
            if r.stage_live == "score_below_65":
                continue  # both sides reject it; the harness only logs its own candidates
            diffs.append({"candidate": k, "live": r.stage_live, "harness": None, **scores,
                          "cause": "candidate in the live scan only: the strategy scan saw different inputs (price bars, universe)"})
        elif side == "right_only":
            if r.stage_bt in ("score_below_65", "scoring"):
                continue
            if r.stage_bt == "already_active" and r.ticker in live_open:
                continue  # live re-evaluates open ideas without logging a row
            diffs.append({"candidate": k, "live": None, "harness": r.stage_bt, **scores,
                          "cause": "candidate in the harness only: the strategy scan saw different inputs (price bars, universe)"})
        else:
            if r.stage_live == r.stage_bt:
                continue
            gap = None if pd.isna(r.composite_score_live) or pd.isna(r.composite_score_bt) else float(r.composite_score_live) - float(r.composite_score_bt)
            if gap is not None and abs(gap) > SCORE_TOLERANCE:
                cause = f"score differs by {gap:+.2f} (live - harness): price data or strategy evidence differ"
            else:
                cause = f"same score, different stage: a rule read different inputs ({r.stage_live} live vs {r.stage_bt} harness)"
            diffs.append({"candidate": k, "live": r.stage_live, "harness": r.stage_bt, **scores, "cause": cause})
    issued_live = sorted(f"{t} / {s}" for t, s in live.loc[live.stage == "issued", key].itertuples(index=False))
    issued_bt = sorted(f"{t} / {s}" for t, s in harness.loc[harness.stage == "issued", key].itertuples(index=False))
    # A difference fails the replay when the candidate scores at least 65 on either side: there the
    # stage decides whether an idea is issued. Below 65 both sides reject it before any rule.
    material = [d for d in diffs if max(d["score_live"] or 0, d["score_harness"] or 0) >= MATERIAL_SCORE]
    # Information only (user decision 2026-10-26): pass/fail does not change with the class
    for d in material:
        d["class"] = (failed_closed(d["candidate"].split(" / ")[0], exclusions) if d["live"] is None else None) or "unclassified"
    live_ranking = (exclusions or {}).get("cs_ranking")
    for d in diffs:
        t, strat = d["candidate"].split(" / ", 1)
        if strat == CROSS_SECTIONAL and (d["live"] is None or d["harness"] is None):
            d["ranking"] = (ranking_comparison(t, live_ranking, harness_ranking)
                            if live_ranking and harness_ranking is not None else None)
    return {"issued_live": issued_live, "issued_harness": issued_bt, "issued_sets_equal": issued_live == issued_bt,
            "candidates_live": int(len(live)), "candidates_harness": int(len(harness)), "differences": diffs,
            "material_differences": material, "unexplained": [d for d in diffs if not d.get("cause")],
            "passed": issued_live == issued_bt and not material}


def summary_markdown(res: Dict) -> str:
    """The summary line and the difference table, for $GITHUB_STEP_SUMMARY."""
    lines = [f"### Parity replay {res.get('date', '')}: {'PASSED' if res['passed'] else 'FAILED'}", "",
             f"Issued live {res['issued_live']} | harness {res['issued_harness']} | equal {res['issued_sets_equal']}. "
             f"Candidates live {res['candidates_live']} | harness {res['candidates_harness']} | differences "
             f"{len(res['differences'])} ({len(res['material_differences'])} at {MATERIAL_SCORE:g}+).", ""]
    if res["differences"]:
        lines += ["| Candidate | Live | Harness | Score live | Score harness | Class (65+, information only) | Cause |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {d['candidate']} | {d['live']} | {d['harness']} | {d['score_live']} | {d['score_harness']} | "
                  f"{d.get('class', '')} | {d['cause']} |" for d in res["differences"]]
        if res.get("exclusions_source"):
            lines += ["", f"Class (a) lists: {res['exclusions_source']}"]
        one_sided_cs = [d for d in res["differences"] if "ranking" in d]
        if one_sided_cs:
            lines += ["", "Cross-Sectional rankings, one-sided candidates (information only):", ""]
            lines += [f"- {ranking_line(d['candidate'].split(' / ')[0], d['ranking'])}" if d["ranking"] else
                      f"- {d['candidate']}: no ranking to compare (the nightly saved none for this session, or the "
                      "harness wrote none)" for d in one_sided_cs]
    return "\n".join(lines) + "\n"


def load_live(client, scan_date: str) -> Tuple[pd.DataFrame, List[str]]:
    """Live decisions of the latest run for scan_date, and tickers open before it."""
    rows = (client.table("signals_history").select("ticker,strategy,composite_score,rejection_reason,outcome,created_at")
            .eq("scan_date", scan_date).limit(20000).execute().data or [])
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit(f"No live rows for {scan_date}")
    df = df[df.created_at == df.created_at.max()]
    # An idea without a rejection reason was issued, whatever its outcome is now (open, stopped, a target)
    df["stage"] = [live_stage(r) for r in df.rejection_reason]
    opened = (client.table("signals").select("ticker,scan_date,status").in_("status", ["open", "pending"])
              .lt("scan_date", scan_date).execute().data or [])
    return df[["ticker", "strategy", "composite_score", "stage"]], sorted({o["ticker"].upper() for o in opened})


def latest_successful_session(client, days: int, today=None) -> Optional[str]:
    """The latest scan date of the past `days` days (up to the latest completed session) whose nightly
    logged status success, or None. The weekly replay uses it, so a stale Friday never means no check."""
    from datetime import timedelta
    from src.utils.market_date import latest_completed_session
    last = today or latest_completed_session()
    rows = (client.table("scan_log").select("scan_date,status").gte("scan_date", (last - timedelta(days=days)).isoformat())
            .lte("scan_date", last.isoformat()).execute().data or [])
    ok = sorted(str(r["scan_date"])[:10] for r in rows if r.get("status") == "success")
    return ok[-1] if ok else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--harness-csv")
    ap.add_argument("--print-open", action="store_true", help="print the tickers open before the session and exit")
    ap.add_argument("--print-status", action="store_true", help="print the session's scan_log status and exit")
    ap.add_argument("--latest-success", type=int, metavar="DAYS",
                    help="print the latest session of the past DAYS days whose nightly succeeded; exit 1 if none")
    ap.add_argument("--out", default=None)
    ap.add_argument("--exclusions", default=None, help="the session's nightly_exclusions.json (class (a) labels)")
    ap.add_argument("--harness-ranking", default=None, help="the harness's Cross-Sectional ranking of the session")
    args = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.supabase_client import get_client
    client = get_client()
    if args.latest_success is not None:
        session = latest_successful_session(client, args.latest_success)
        if session is None:
            print(f"No session of the past {args.latest_success} days has a successful nightly", file=sys.stderr)
            return 1
        print(session)
        return 0
    if not args.date:
        ap.error("--date is required")
    if args.print_status:
        rows = client.table("scan_log").select("status").eq("scan_date", args.date).execute().data or []
        print(rows[0]["status"] if rows else "missing")
        return 0
    live, live_open = load_live(client, args.date)
    if args.print_open:
        print(",".join(live_open))
        return 0
    harness = pd.read_csv(args.harness_csv)
    exclusions, source = None, "not available"
    if args.exclusions and os.path.exists(args.exclusions):
        doc = json.load(open(args.exclusions, encoding="utf-8"))
        if doc.get("session") == args.date:
            exclusions, source = doc, f"nightly_exclusions.json of session {args.date}"
        else:
            source = f"exclusions file is for {doc.get('session')}, not {args.date}: not used"
    harness_ranking = (pd.read_csv(args.harness_ranking)
                       if args.harness_ranking and os.path.exists(args.harness_ranking) else None)
    res = compare(live, harness, live_open, exclusions, harness_ranking)
    res.update({"date": args.date, "live_open_at_session": live_open, "exclusions_source": source})
    out = args.out or os.path.join(PROJECT_ROOT, "outputs", f"parity_replay_{args.date}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=str)
    print(f"Issued live {res['issued_live']} | harness {res['issued_harness']} | equal {res['issued_sets_equal']}")
    print(f"Candidates live {res['candidates_live']} | harness {res['candidates_harness']} | differences {len(res['differences'])}")
    for d in res["differences"]:
        print(f"  {d['candidate']:40s} live {str(d['live']):20s} harness {str(d['harness']):20s} {d['cause']}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary_markdown(res))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
