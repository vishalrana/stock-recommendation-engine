"""
Forward-test review (.github/workflows/forward_test_review.yml)
===============================================================
Applies the rules pre-registered in docs/forward-test-reviews.md, mechanically, on the review dates
R1-R4. A review runs only when its date has passed and docs/forward-test-reviews/<date>.md does not
exist yet; it uses data through the latest completed session on its date.

Populations: LIVE (Ideas issued by a successful nightly from scan date 2026-10-20), REPLAY
(data/forward_test/replay_ideas.jsonl; empty: no session lacked a nightly), COMBINED (both, one
open idea per ticker: a later idea for a ticker open in the other set is dropped and counted).
Measures, per population: ideas, ideas a session, open and closed counts; M1 (mean beta-adjusted %
per closed idea, 95% CI from 2,000 signal-month block-bootstrap resamples; counts only below 20
closed); M2 (cumulative equal-unit beta-adjusted P&L, open ideas marked at the latest close); the
per-idea random-stock placebo paired difference with its CI; the share with a report inside the
hold; the scan funnel. Outcomes are recomputed with the harness's exit engine from the issued
levels (rebased onto the bars' basis), at its default costs; LIVE differences from the live
lifecycle's recorded exits are listed. Decisions: STOP when COMBINED M2 is below the 1st percentile
of 2,000 signal-month block-bootstrap paths of run 40205392302's issued ideas with as many ideas;
at R3 and R4, END when the COMBINED M1 CI is entirely below 0. On STOP or END the status file is
written (config/forward_test_status.json and its copy in frontend/src/lib, shown as a banner).
The swing report (per sleeve, report only) comes from the swing job (--review-report).

  python scripts/forward_test_review.py [--as-of YYYY-MM-DD] [--swing-report PATH] [--write]
  python scripts/forward_test_review.py --dry-run          # fixtures only: no live outcomes, no files
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.forward_test_stats import BOOTSTRAP, SEED, m1, m2, stop_threshold  # noqa: E402

DOC = os.path.join(PROJECT_ROOT, "docs", "forward-test-reviews.md")
REPORTS = os.path.join(PROJECT_ROOT, "docs", "forward-test-reviews")
STATUS_FILES = (os.path.join(PROJECT_ROOT, "config", "forward_test_status.json"),
                os.path.join(PROJECT_ROOT, "frontend", "src", "lib", "forward-test-status.json"))
BACKTEST = os.path.join(PROJECT_ROOT, "data", "forward_test", "backtest_40205392302_trades.csv")
REPLAY = os.path.join(PROJECT_ROOT, "data", "forward_test", "replay_ideas.jsonl")
REPLAY_SESSIONS = 0    # sessions the backfill covered: none, every session from 2026-10-26 had a nightly (2026-11-28)
LIVE_START = "2026-10-20"
COST = 0.10            # % per side: the harness's TRANSACTION_COST_PCT_PER_SIDE
PLACEBO_DRAWS = 200    # the harness's per-idea placebo
POPULATIONS = ("LIVE", "REPLAY", "COMBINED")


def reviews(doc_path: str = DOC) -> List[Tuple[str, date]]:
    """The pre-registered review dates, read from the doc itself."""
    text = open(doc_path, encoding="utf-8").read()
    line = next(l for l in text.splitlines() if l.startswith("R1 "))
    return [(m.group(1), date.fromisoformat(m.group(2))) for m in re.finditer(r"(R\d) (\d{4}-\d{2}-\d{2})", line)]


def due(today: date, done: Sequence[str], doc_path: str = DOC) -> Optional[Tuple[str, date]]:
    """The oldest review whose date has passed and that has no report yet."""
    for label, d in reviews(doc_path):
        if d <= today and d.isoformat() not in done:
            return label, d
    return None


# ---------------------------------------------------------------------------- outcomes
def outcome(idea: Dict[str, Any], frame: pd.DataFrame, spy: pd.DataFrame, beta: Optional[float], end: date) -> Dict[str, Any]:
    """An idea recomputed with the harness's exit engine from its issued levels, through `end`: levels
    rebased onto the bars' basis; fill at the next open; closed ideas net of costs, open ones marked at
    the latest close; beta-adjusted with SPY bought at the fill-day open (the harness's convention)."""
    from src.outcome.outcome_calculator import SAME_DAY_AMBIGUITY_POLICY, evaluate_signal_outcome, rebase_levels
    from src.pipeline_steps import holding_days_for
    f = frame[frame.index <= pd.Timestamp(end)]
    sd = pd.Timestamp(idea["signal_date"])
    if sd not in f.index or f.index.get_loc(sd) + 1 >= len(f):
        return {"status": "unfilled"}
    loc = f.index.get_loc(sd)
    fwd = f.iloc[loc + 1:]
    (stop, t1, t2, t3), _ = rebase_levels([idea["stop"], idea["t1"], idea.get("t2"), idea.get("t3")], idea["entry"],
                                          float(f["CLOSE"].iloc[loc]))
    fill = float(fwd["OPEN"].iloc[0])
    res = evaluate_signal_outcome(fwd, fill, stop, t1, t2, t3, max_holding_days=holding_days_for(idea["strategy"]) or 20,
                                  ambiguity_policy=SAME_DAY_AMBIGUITY_POLICY, return_open=True)
    if res is None:
        return {"status": "unfilled"}
    closed = bool(res["is_closed"])
    gross = res["realized_return_pct"] if closed else (
        res["realized_return_pct"] + res["remaining_weight"] * (float(res["last_close"]) / fill - 1.0) * 100.0)
    exit_day = pd.Timestamp(res["outcome_date"]) if closed else f.index[-1]
    s = spy[(spy.index >= fwd.index[0]) & (spy.index <= exit_day)]
    spy_ret = (float(s["CLOSE"].iloc[-1]) / float(s["OPEN"].iloc[0]) - 1.0) * 100.0 if len(s) else np.nan
    net = gross - 2.0 * COST
    ba = net - beta * spy_ret if beta is not None and np.isfinite(beta) and np.isfinite(spy_ret) else None
    return {"status": "closed" if closed else "open", "fill_date": fwd.index[0].date().isoformat(),
            "exit_date": exit_day.date().isoformat() if closed else None, "outcome": res["outcome"],
            "beta_adjusted_pct": ba, "signal_month": idea["signal_date"][:7],
            "atr_14": float(f["ATR_14"].iloc[loc]) if "ATR_14" in f else None, "rebased_entry": float(f["CLOSE"].iloc[loc]),
            "rebased_stop": stop, "rebased_targets": [t1, t2, t3]}


def combine(live: List[Dict], replay: List[Dict]) -> Tuple[List[Dict], int]:
    """LIVE plus REPLAY with one open idea per ticker: an idea is dropped when an earlier idea of the
    other set for the same ticker is still open on its signal date."""
    kept, dropped = [], 0
    for idea in sorted([dict(i, src="LIVE") for i in live] + [dict(i, src="REPLAY") for i in replay],
                       key=lambda i: (i["signal_date"], i["src"], i["ticker"])):
        clash = any(k["ticker"] == idea["ticker"] and k["src"] != idea["src"] and k["signal_date"] < idea["signal_date"]
                    and (k["out"]["status"] == "open" or (k["out"].get("exit_date") or "") >= idea["signal_date"])
                    for k in kept)
        if clash:
            dropped += 1
        else:
            kept.append(idea)
    return kept, dropped


def measures(ideas: List[Dict], sessions: int, report_in_hold: Callable[[Dict], Optional[bool]],
             placebo: Optional[Callable[[List[Dict]], Dict]]) -> Dict[str, Any]:
    done = [i for i in ideas if i["out"]["status"] in ("closed", "open")]
    closed = [i for i in done if i["out"]["status"] == "closed" and i["out"]["beta_adjusted_pct"] is not None]
    opened = [i for i in done if i["out"]["status"] == "open" and i["out"]["beta_adjusted_pct"] is not None]
    in_hold = [x for x in (report_in_hold(i) for i in done) if x is not None]
    out = {"ideas": len(ideas), "sessions": sessions, "ideas_a_session": round(len(ideas) / sessions, 3) if sessions else None,
           "open": sum(i["out"]["status"] == "open" for i in ideas), "closed": sum(i["out"]["status"] == "closed" for i in ideas),
           "unfilled": sum(i["out"]["status"] == "unfilled" for i in ideas),
           "M1": m1([i["out"]["beta_adjusted_pct"] for i in closed], [i["out"]["signal_month"] for i in closed]),
           "M2": m2([i["out"]["beta_adjusted_pct"] for i in closed], [i["out"]["beta_adjusted_pct"] for i in opened]),
           "report_inside_hold_share": round(sum(in_hold) / len(in_hold), 3) if in_hold else None}
    out["placebo"] = ({"note": "not run (dry run on fixtures)"} if placebo is None else
                      {"note": "below 20 closed ideas: counts only"} if len(closed) < 20 else placebo(closed))
    return out


def decide(label: str, combined: Dict[str, Any], p1: Optional[float]) -> Dict[str, Any]:
    """The pre-registered decision rules, mechanically."""
    if p1 is not None and combined["M2"] < p1:
        return {"state": "STOP", "rule": 1, "text": f"COMBINED M2 {combined['M2']} is below the 1st percentile {p1} of run "
                                                     "40205392302's bootstrap paths: STOP ends the Ideas forward test."}
    ci = combined["M1"].get("ci95")
    if label in ("R3", "R4") and ci:
        if ci[1] < 0:
            return {"state": "END", "rule": 2, "text": "The COMBINED M1 95% CI is entirely below 0: END. The live record "
                                                         "confirms the backtest, and Ideas stay a screener."}
        if ci[0] > 0:
            return {"state": "CONTINUE", "rule": 2, "text": "Live better than backtest (the COMBINED M1 CI is entirely above 0). "
                                                              "Ideas stay SHADOW unless a new pre-registered study passes."}
        if label == "R4":
            return {"state": "USER_DECIDES", "rule": 2, "text": "After R4 with the CI including 0: the user decides."}
    return {"state": "CONTINUE", "rule": None, "text": "No decision rule applies: the forward test continues to the next review."}


def status_doc(label: str, review_date: date, decision: Dict[str, Any], run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The app's banner status on STOP or END, else None."""
    if decision["state"] not in ("STOP", "END"):
        return None
    msg = (f"Forward test stopped by its pre-registered rule on {review_date.isoformat()}" if decision["state"] == "STOP"
           else f"Forward test ended by its pre-registered rule on {review_date.isoformat()}: the live record confirms the backtest")
    return {"state": decision["state"], "review": label, "date": review_date.isoformat(), "run_id": run_id, "message": msg}


def swing_report(rows: List[Dict], marks: Dict[Any, Optional[float]], placebo_median: Dict[Any, Optional[float]]) -> Dict[str, Any]:
    """Per sleeve, report only: M1 and M2 on shadow signals (voided excluded) and the paired difference
    against the live placebo median per signal (SwingPlacebo, 200 runs) with its CI."""
    from scripts.validate_backtest_pipeline import block_bootstrap_ci
    out = {}
    for sl in ("T", "E", "N", "P", "I"):
        rs = [r for r in rows if r["sleeve"] == sl and r["status"] != "void"]
        closed = [r for r in rs if r["status"] == "closed" and r.get("beta_adj_pct") is not None]
        o = [marks[r["id"]] for r in rs if r["status"] == "open" and marks.get(r["id"]) is not None]
        diffs = [(r["beta_adj_pct"] - placebo_median[r["id"]], r["signal_date"][:7]) for r in closed
                 if placebo_median.get(r["id"]) is not None]
        pd_ = {"note": "below 20 closed signals: counts only"}
        if len(diffs) >= 20:
            v, mo = np.array([d for d, _ in diffs]), np.array([m for _, m in diffs])
            lo, hi = block_bootstrap_ci(v, mo, np.mean, n_boot=BOOTSTRAP, seed=SEED)
            pd_ = {"n": len(diffs), "mean_pct": round(float(v.mean()), 4), "ci95": None if lo is None else [round(lo, 4), round(hi, 4)]}
        out[sl] = {"signals": len(rs), "open": sum(r["status"] == "open" for r in rs), "closed": len(closed),
                   "M1": m1([r["beta_adj_pct"] for r in closed], [r["signal_date"][:7] for r in closed]),
                   "M2": m2([r["beta_adj_pct"] for r in closed], o), "placebo_paired_diff": pd_}
    return out


# ---------------------------------------------------------------------------- report
def _m1_text(x: Dict[str, Any]) -> str:
    return (f"{x['mean_pct']:+.2f}% (CI {x['ci95'][0]:+.2f} to {x['ci95'][1]:+.2f}), n {x['closed']}" if "mean_pct" in x
            else f"counts only (n {x['closed']})")


def render(label: str, review_date: date, end: date, run_id: Optional[str], pops: Dict[str, Dict], dropped: int,
           p1: Optional[float], decision: Dict[str, Any], diffs: List[Dict], funnel: Dict[str, int],
           swing: Optional[Dict[str, Any]], dry_run: bool) -> str:
    rows = [("Ideas", "ideas"), ("Sessions", "sessions"), ("Ideas a session", "ideas_a_session"), ("Open", "open"),
            ("Closed", "closed"), ("Unfilled", "unfilled"), ("Report inside the hold (share)", "report_inside_hold_share")]
    L = [f"# Forward-test review {label}, {review_date.isoformat()}{' (DRY RUN on fixtures)' if dry_run else ''}", "",
         f"Rules: docs/forward-test-reviews.md (pre-registered 2026-11-28). Data through session {end.isoformat()}. "
         f"GitHub Actions run {run_id or 'n/a'}.", "", "## Measures", "",
         "| | " + " | ".join(POPULATIONS) + " |", "|---|" + "---|" * len(POPULATIONS)]
    L += [f"| {name} | " + " | ".join(str(pops[p][k]) for p in POPULATIONS) + " |" for name, k in rows]
    L.append("| M1, beta-adjusted %/closed idea | " + " | ".join(_m1_text(pops[p]["M1"]) for p in POPULATIONS) + " |")
    L.append("| M2, cumulative beta-adjusted % (open marked) | " + " | ".join(f"{pops[p]['M2']:+.2f}" for p in POPULATIONS) + " |")
    L.append("| Placebo: paired difference vs the per-idea median | " + " | ".join(
        (f"{x['paired_diff_vs_placebo_median']:+.2f} (CI {x['paired_diff_ci95'][0]:+.2f} to {x['paired_diff_ci95'][1]:+.2f})"
         if x.get("paired_diff_ci95") else x.get("note", "n/a")) for x in (pops[p]["placebo"] for p in POPULATIONS)) + " |")
    L += ["", f"Dropped from COMBINED (a ticker open in the other set): {dropped}.", "",
          "## Decision", "", f"- STOP threshold (1st percentile, {BOOTSTRAP} paths of run 40205392302 with "
          f"{pops['COMBINED']['ideas']} ideas): {p1}; COMBINED M2: {pops['COMBINED']['M2']}.",
          f"- **{decision['state']}**: {decision['text']}", "",
          "## LIVE: recomputed exits that differ from the live lifecycle", ""]
    L += ([f"- {d['ticker']} (signal {d['signal_date']}): recorded {d['recorded']}, recomputed {d['recomputed']}" for d in diffs]
          or ["- none"])
    L += ["", "## Scan funnel (successful nightlies in the window)", "", "| Count | Sum |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in funnel.items()]
    if swing:
        L += ["", "## Swing sleeves (report only)", "", "| Sleeve | Signals | Open | Closed | M1 | M2 | Placebo paired diff |",
              "|---|---|---|---|---|---|---|"]
        for sl, x in swing.items():
            pdx = x["placebo_paired_diff"]
            L.append(f"| {sl} | {x['signals']} | {x['open']} | {x['closed']} | {_m1_text(x['M1'])} | {x['M2']:+.2f} | "
                     + (f"{pdx['mean_pct']:+.2f} (CI {pdx['ci95'][0]:+.2f} to {pdx['ci95'][1]:+.2f})" if pdx.get("ci95") else pdx.get("note", "n/a"))
                     + " |")
    return "\n".join(L) + "\n"


def run_review(label: str, review_date: date, end: date, live: List[Dict], replay: List[Dict], frames: Dict[str, pd.DataFrame],
               spy: pd.DataFrame, betas: Dict[str, float], backtest: pd.DataFrame, sessions: int,
               report_in_hold: Callable[[Dict], Optional[bool]], placebo: Optional[Callable[[List[Dict]], Dict]],
               recorded: Dict[Tuple[str, str], Dict], funnel: Dict[str, int], swing: Optional[Dict],
               run_id: Optional[str], dry_run: bool = False) -> Dict[str, Any]:
    for i in live + replay:
        f = frames.get(i["ticker"])
        i["out"] = outcome(i, f, spy, betas.get(i["ticker"]), end) if f is not None else {"status": "unfilled"}
    combined, dropped = combine(live, replay)
    pops = {"LIVE": measures(live, sessions, report_in_hold, placebo),
            "REPLAY": measures(replay, REPLAY_SESSIONS, report_in_hold, placebo),
            "COMBINED": measures(combined, sessions + REPLAY_SESSIONS, report_in_hold, placebo)}
    bt = backtest.dropna(subset=["beta_adjusted_pct"])   # issued ideas with a beta-adjusted return, as M1 counts them
    p1 = stop_threshold(bt["beta_adjusted_pct"], bt["signal_date"].astype(str).str[:7], pops["COMBINED"]["ideas"])
    decision = decide(label, pops["COMBINED"], p1)
    diffs = []
    for i in live:
        rec = recorded.get((i["ticker"], i["signal_date"]))
        if rec is None or i["out"]["status"] == "unfilled":
            continue
        a = (rec.get("exit_date") or "open")
        b = (i["out"].get("exit_date") or "open")
        if a != b:
            diffs.append({"ticker": i["ticker"], "signal_date": i["signal_date"], "recorded": a, "recomputed": b})
    text = render(label, review_date, end, run_id, pops, dropped, p1, decision, diffs, funnel, swing, dry_run)
    return {"text": text, "decision": decision, "status": status_doc(label, review_date, decision, run_id), "populations": pops}


# ---------------------------------------------------------------------------- fixtures (dry run)
def fixtures() -> Dict[str, Any]:
    """Synthetic prices and ideas only, so a dry run touches no live outcome."""
    rng = np.random.default_rng(SEED)
    idx = pd.bdate_range("2026-06-01", "2027-01-15")
    frames = {}
    for k in range(40):
        c = 50 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, len(idx))))
        frames[f"FX{k:02d}"] = pd.DataFrame({"OPEN": c * (1 + rng.normal(0, 0.003, len(idx))), "HIGH": c * 1.012, "LOW": c * 0.988,
                                             "CLOSE": c, "VOLUME": 1e6, "ATR_14": c * 0.02, "_LIQUID": True}, index=idx)
    spy_c = 500 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(idx))))
    spy = pd.DataFrame({"OPEN": spy_c, "CLOSE": spy_c}, index=idx)
    days = [d for d in idx if pd.Timestamp(LIVE_START) <= d <= pd.Timestamp("2026-12-31")]
    live = []
    for k in range(45):
        t, d = f"FX{k % 40:02d}", days[int(rng.integers(0, len(days)))]
        c = float(frames[t].loc[d, "CLOSE"])
        live.append({"ticker": t, "strategy": "Trend Following", "signal_date": d.date().isoformat(), "entry": c,
                     "stop": c * 0.94, "t1": c * 1.06, "t2": c * 1.12, "t3": None})
    return {"frames": frames, "spy": spy, "betas": {t: 1.0 for t in frames}, "live": live,
            "backtest": pd.read_csv(BACKTEST).iloc[:400], "sessions": len(days)}


def dry_run(as_of: date) -> str:
    """The review a run on `as_of` would make (R1 when none is due yet), on fixtures through its date."""
    fx = fixtures()
    label, review_date = due(as_of, []) or reviews()[0]
    swing = swing_report([{"id": k, "sleeve": "T", "status": "closed" if k % 3 else "open", "beta_adj_pct": float(np.sin(k)),
                           "signal_date": f"2026-{11 + k % 2:02d}-0{1 + k % 9}"} for k in range(30)],
                         {k: 0.1 for k in range(30)}, {k: 0.0 for k in range(30)})
    res = run_review(label, review_date, review_date, fx["live"], [], fx["frames"], fx["spy"], fx["betas"],
                     fx["backtest"], fx["sessions"], lambda i: False, None, {}, {"signals_recommended": 45}, swing,
                     os.environ.get("GITHUB_RUN_ID"), dry_run=True)
    return res["text"]


# ---------------------------------------------------------------------------- live inputs
def live_inputs(end: date) -> Dict[str, Any]:
    """LIVE ideas and recorded exits, REPLAY ideas, sessions and funnel from Supabase (read only)."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.generate_swing_signals import fetch_rows
    from jobs.supabase_client import get_client
    client = get_client()
    logs = [r for r in fetch_rows(client, "scan_log") if LIVE_START <= str(r["scan_date"])[:10] <= end.isoformat()]
    ok = {str(r["scan_date"])[:10] for r in logs if r["status"] == "success"}
    # Every issued idea; the nightly keeps its latest rejected setups in `signals` for display (status rejected)
    sig = [r for r in fetch_rows(client, "signals") if str(r["scan_date"])[:10] in ok and r.get("status") != "rejected"]
    live = [{"ticker": r["ticker"], "strategy": r["strategy"], "signal_date": str(r["scan_date"])[:10],
             "entry": float(r["entry_price"]), "stop": float(r["stop_loss"]),
             **{f"t{k}": (float(r[f"target_{k}"]) if r.get(f"target_{k}") is not None else None) for k in (1, 2, 3)}}
            for r in sig]
    recorded = {(r["ticker"], str(r["scan_date"])[:10]): {"exit_date": (str(r["exit_date"])[:10] if r.get("exit_date") else None)}
                for r in sig}
    replay = [json.loads(l) for l in open(REPLAY, encoding="utf-8") if l.strip()] if os.path.exists(REPLAY) else []
    keys = [k for k in logs[0].keys() if k.startswith(("failed_", "signals_", "tickers_"))] if logs else []
    funnel = {k: int(sum(int(r.get(k) or 0) for r in logs if str(r["scan_date"])[:10] in ok)) for k in keys}
    return {"live": live, "replay": replay, "recorded": recorded, "sessions": len(ok), "funnel": funnel}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="the run's date (default: today, UTC)")
    ap.add_argument("--dry-run", action="store_true", help="fixtures only: no live outcomes, nothing written")
    ap.add_argument("--swing-report", default=None, help="the swing job's --review-report JSON")
    ap.add_argument("--write", action="store_true", help="write the report (and the status file on STOP or END)")
    ap.add_argument("--print-due", action="store_true", help="print the due review's date (nothing when none is due)")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.as_of) if a.as_of else datetime.now(timezone.utc).date()
    if a.print_due:
        d = due(today, [f[:-3] for f in os.listdir(REPORTS)] if os.path.isdir(REPORTS) else [])
        print(d[1].isoformat() if d else "")
        return 0
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if a.dry_run:
        text = dry_run(today)
        print(text)
        if summary:
            open(summary, "a", encoding="utf-8").write(text)
        return 0
    done = [f[:-3] for f in os.listdir(REPORTS)] if os.path.isdir(REPORTS) else []
    d = due(today, done)
    if d is None:
        print(f"No review due on {today}: reviews {[(l, x.isoformat()) for l, x in reviews()]}, written {sorted(done)}.")
        return 0
    label, review_date = d
    from src.utils.market_date import latest_completed_session
    end = latest_completed_session(datetime.combine(review_date, datetime.max.time()).replace(tzinfo=timezone.utc))
    import scripts.validate_backtest_pipeline as h   # the harness's own loading, betas, placebo and SEC events
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    from src.pipeline_steps import holding_days_for
    from src.research.placebo import PlaceboEngine
    inp = live_inputs(end)
    pop = inp["live"] + inp["replay"]
    stocks, _ = h.production_universe()
    raw = h.load_history(sorted(set(stocks) | {i["ticker"] for i in pop}) + ["SPY"], refresh=True)
    raw, _ = h.truncate_unadjusted_splits(raw)
    raw = {t: df[df.index <= pd.Timestamp(end)] for t, df in raw.items()}   # data through the review's session only
    spy = raw.pop("SPY").dropna(subset=["CLOSE"]).sort_index()
    frames = {t: h.prepare_frame(df) for t, df in raw.items() if len(df) > h.WARMUP_BARS + 5}
    beta_series = h.point_in_time_betas(frames, spy)
    betas = {i["ticker"]: float(beta_series[i["ticker"]].get(pd.Timestamp(i["signal_date"]), np.nan))
             for i in pop if i["ticker"] in beta_series}
    engine = PlaceboEngine({t: f for t, f in frames.items() if t not in SECTOR_ETFS}, spy, beta_series, holding_days_for,
                           cost_pct_per_side=h.TRANSACTION_COST_PCT_PER_SIDE, seed=h.PLACEBO_SEED)

    def placebo(closed: List[Dict]) -> Dict:
        trades = [{"ticker": i["ticker"], "signal_date": i["signal_date"], "strategy": i["strategy"],
                   "entry_signal_close": i["out"]["rebased_entry"], "stop_loss": i["out"]["rebased_stop"],
                   "target_1": i["out"]["rebased_targets"][0], "target_2": i["out"]["rebased_targets"][1],
                   "target_3": i["out"]["rebased_targets"][2], "beta_adjusted_pct": i["out"]["beta_adjusted_pct"],
                   "atr_14": i["out"]["atr_14"]} for i in closed]
        return engine.evaluate_book(trades, lambda t: float(t.get("atr_14") or 0.0), h.PLACEBO_DRAWS_FULL,
                                    lambda v, m: h.block_bootstrap_ci(np.asarray(v, dtype=float), np.asarray(m), np.mean))

    sec = h.load_sec_events(sorted({i["ticker"] for i in pop}), end)

    def report_in_hold(i: Dict) -> Optional[bool]:
        """The harness's left-tail test: a reaction session from fill to exit (open ideas: to the review's session)."""
        ev = (sec or {}).get("events", {}).get(i["ticker"])
        if not ev:
            return None
        last = i["out"].get("exit_date") or end.isoformat()
        return any(i["out"]["fill_date"] <= e.reaction.isoformat() <= last for e in ev)

    swing = json.load(open(a.swing_report, encoding="utf-8")) if a.swing_report else None
    res = run_review(label, review_date, end, inp["live"], inp["replay"], frames, spy, betas, pd.read_csv(BACKTEST),
                     inp["sessions"], report_in_hold, placebo, inp["recorded"], inp["funnel"], swing, os.environ.get("GITHUB_RUN_ID"))
    print(res["text"])
    if summary:
        open(summary, "a", encoding="utf-8").write(res["text"])
    if a.write:
        os.makedirs(REPORTS, exist_ok=True)
        open(os.path.join(REPORTS, f"{review_date.isoformat()}.md"), "w", encoding="utf-8").write(res["text"])
        if res["status"]:
            for p in STATUS_FILES:
                json.dump(res["status"], open(p, "w", encoding="utf-8"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
