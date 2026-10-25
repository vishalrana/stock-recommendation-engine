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


def compare(live: pd.DataFrame, harness: pd.DataFrame, live_open: List[str]) -> Dict:
    """live: ticker, strategy, composite_score, stage. harness: ticker, strategy, composite_score, stage."""
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
        lines += ["| Candidate | Live | Harness | Score live | Score harness | Cause |", "|---|---|---|---|---|---|"]
        lines += [f"| {d['candidate']} | {d['live']} | {d['harness']} | {d['score_live']} | {d['score_harness']} | {d['cause']} |"
                  for d in res["differences"]]
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--harness-csv")
    ap.add_argument("--print-open", action="store_true", help="print the tickers open before the session and exit")
    ap.add_argument("--print-status", action="store_true", help="print the session's scan_log status and exit")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.supabase_client import get_client
    client = get_client()
    if args.print_status:
        rows = client.table("scan_log").select("status").eq("scan_date", args.date).execute().data or []
        print(rows[0]["status"] if rows else "missing")
        return 0
    live, live_open = load_live(client, args.date)
    if args.print_open:
        print(",".join(live_open))
        return 0
    harness = pd.read_csv(args.harness_csv)
    res = compare(live, harness, live_open)
    res.update({"date": args.date, "live_open_at_session": live_open})
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
