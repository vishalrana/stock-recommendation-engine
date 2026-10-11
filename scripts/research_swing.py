"""
Swing signals research harness (pre-registered, CLAUDE.md "Swing signals program")
==================================================================================
Holdout: signals with t from 2008-01-02 to 2022-09-30 (exits may run later on real data).
All sleeve inputs come from src/swing/engine.py, the same code the nightly job runs.

Stages (run as separate GitHub Actions jobs; see .github/workflows/swing_research.yml):
  replication   monthly IC (20-session beta-adjusted target) of C6, MOM12_1, RESMOM, HI52, -FIP,
                -REV21, -RVOL252 and MAD in U1 and U2, NW t, three five-year blocks; U2 member-days
                without a bar, by year
  patterns      incremental IC of every PATTERN_FEATURES feature (residualized on MOM12_1, HI52)
  sleeve        one sleeve (--sleeve) in U2 and U1: standalone book with G0, exits, 200-run placebo,
                P-rule inputs, cost scenarios, five-year blocks, report-only analyses
  decide        Holm over the five sleeves, P1-P8 -> LIVE / LIVE-SP500 / SHADOW;
                writes outputs/swing/decisions.json and config/swing_sleeves.json
Only a full run on GitHub Actions (GITHUB_RUN_ID set, no --limit) may write config/swing_sleeves.json;
every output records the run id, git sha and whether it was a smoke run.
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("SKIP_NLP", "true")

from src.research.stats import holm  # noqa: E402
from src.research.trials import git_sha, record_trial  # noqa: E402
from src.swing.engine import SwingPlacebo, build_context, run_book  # noqa: E402
from src.swing.evaluate import (  # noqa: E402
    decide_sleeve_status, evaluate_sleeve_trades, monte_carlo_trade_shuffle,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("research_swing")

OUT_DIR = os.path.join(PROJECT_ROOT, "outputs", "swing")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "swing_sleeves.json")
HOLDOUT = (date(2008, 1, 2), date(2022, 9, 30))
RECENT = (date(2022, 10, 1), date(2026, 10, 9))
PANEL_WINDOW = ("2005-10-01", None)            # features need ~525 sessions before the holdout starts
SLEEVES = ("T", "E", "N", "P", "I")


def provenance(smoke: bool) -> Dict[str, Any]:
    return {"github_run_id": os.environ.get("GITHUB_RUN_ID"), "git_sha": git_sha(), "smoke": bool(smoke),
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "holdout": [HOLDOUT[0].isoformat(), HOLDOUT[1].isoformat()]}


def load_all(limit: Optional[int], need_filings: bool = True, need_insider: bool = False):
    from scripts.research_signal_ic import SECTOR_TO_ETF
    from scripts.validate_backtest_pipeline import production_universe
    from src.research.survivorship import load_membership
    from src.swing.data import CikResolver, load_filings, load_insider, load_price_frames, sector_map
    frames = load_price_frames()
    if "SPY" not in frames:
        raise SystemExit("SPY long history missing: run scripts/build_long_history.py first.")
    spy = frames.pop("SPY")
    etfs = sorted(set(SECTOR_TO_ETF.values()))
    etf_close = pd.DataFrame({e: frames[e]["TR_CLOSE"] for e in etfs if e in frames})
    for e in etfs:
        frames.pop(e, None)
    u1, _ = production_universe()
    membership = load_membership()
    if limit:
        keep = sorted(frames)[:limit]
        frames = {t: frames[t] for t in keep}
    resolver = CikResolver()
    filings, rights, sec_stats = (load_filings(frames, resolver) if need_filings else ({}, {}, {}))
    insider = load_insider(frames, resolver) if need_insider else None
    logger.info("Loaded %d price frames, SEC %s, insider rows %s", len(frames), sec_stats,
                None if insider is None else len(insider))
    return frames, spy, etf_close, sector_map(frames), filings, rights, set(u1), membership, insider, sec_stats


# ----------------------------------------------------------------------------
def stage_replication(limit: Optional[int], smoke: bool) -> Dict[str, Any]:
    from scripts.research_signal_ic import (
        compute_features, composite_c6, ic_series, liquid_mask, month_ends, summarize_ic, targets,
    )
    from src.swing.engine import u2_membership
    frames, spy, etf_close, sectors, _, _, u1, membership, _, _ = load_all(limit, need_filings=False)
    idx = spy.loc[PANEL_WINDOW[0]:"2022-12-31"].index
    wide = lambda col: pd.DataFrame({t: f[col] for t, f in frames.items()}).reindex(idx)
    close, high, vol = wide("TR_CLOSE"), wide("TR_HIGH"), wide("VOLUME")
    raw = pd.DataFrame({t: (f["UNADJ_CLOSE"] if "UNADJ_CLOSE" in f else f["CLOSE"]) for t, f in frames.items()}).reindex(idx)
    nominal = wide("CLOSE")
    spy_c = spy["TR_CLOSE"].reindex(idx)
    etf = etf_close.reindex(idx)
    sec = {t: (s if s in etf.columns else None) for t, s in sectors.items()}
    feats = compute_features(close, high, vol, spy_c, etf, sec)
    feats["MAD"] = close.rolling(21, min_periods=21).mean() / close.rolling(200, min_periods=200).mean()
    liq = liquid_mask(nominal, vol, raw_close=raw)
    tgt = targets(close, spy_c, feats["BETA252"], 20)
    dates = month_ends(idx, HOLDOUT[0].isoformat(), pd.Timestamp(HOLDOUT[1]))
    out: Dict[str, Any] = {"provenance": provenance(smoke), "universes": {}}
    u2m = u2_membership(idx, list(close.columns), membership)
    masks = {"u1": liq & pd.DataFrame({t: (t in u1) for t in close.columns}, index=idx), "u2": liq & u2m}
    for u, mask in masks.items():
        f = dict(feats)
        f["C6"] = composite_c6(feats, mask)
        res = {}
        for name in ("C6", "MOM12_1", "RESMOM", "HI52", "FIP", "REV21", "RVOL252", "MAD"):
            ics = ic_series(f[name], tgt, mask, dates)
            s = summarize_ic(ics)
            s["blocks_5y"] = {b: (round(float(np.mean(v)), 4) if v else None) for b, v in (
                ("2008_2012", [x for k, x in ics.items() if k <= "2012-12-31"]),
                ("2013_2017", [x for k, x in ics.items() if "2013-01-01" <= k <= "2017-12-31"]),
                ("2018_2022", [x for k, x in ics.items() if k >= "2018-01-01"]))}
            res[name] = s
        out["universes"][u] = res
        logger.info("Replication %s: %s", u, {k: (v.get("mean_ic"), v.get("nw_t")) for k, v in res.items()})
    c6 = out["universes"]["u2"]["C6"]
    out["P8_C6_U2_PASS"] = bool((c6.get("mean_ic") or 0) > 0 and (c6.get("nw_t") or 0) >= 2.0)
    # U2 member-days without a bar, by year (survivorship of the point-in-time universe)
    full_idx = spy.loc["2008-01-01":"2022-12-31"].index
    have = {t: set(f.index) for t, f in frames.items()}
    by_year = defaultdict(lambda: {"member_days": 0, "no_bar": 0})
    late_first = []
    for row in membership.itertuples(index=False):
        end = row.end_date if pd.notna(row.end_date) else full_idx[-1]
        days = full_idx[(full_idx >= row.start_date) & (full_idx <= end)]
        if not len(days):
            continue
        h = have.get(row.ticker)
        if h and min(h) > days[0]:
            late_first.append({"ticker": row.ticker, "member_from": str(row.start_date.date()), "first_bar": str(min(h).date())})
        for d in days:
            y = by_year[d.year]
            y["member_days"] += 1
            if not h or d not in h:
                y["no_bar"] += 1
    out["u2_member_days_without_bar_by_year"] = {
        str(y): dict(v, share_pct=round(100.0 * v["no_bar"] / v["member_days"], 2)) for y, v in sorted(by_year.items())}
    out["u2_symbols_first_bar_after_membership_start"] = late_first
    record_trial("swing_replication", "swing", {"universes": ["u1", "u2"]}, len(dates), None, None, None,
                 series_path="outputs/swing/replication.json", smoke=smoke,
                 extra={"metric": "monthly rank IC", "u2_c6": c6, "u1_c6": out["universes"]["u1"]["C6"]})
    return out


def stage_patterns(limit: Optional[int], smoke: bool) -> Dict[str, Any]:
    from scripts.research_signal_ic import compute_features, ic_series, liquid_mask, month_ends, residualize, summarize_ic, targets
    from src.patterns import PATTERN_FEATURES, pattern_feature_panel
    from src.swing.engine import u2_membership
    frames, spy, etf_close, sectors, _, _, u1, membership, _, _ = load_all(limit, need_filings=False)
    idx = spy.loc[PANEL_WINDOW[0]:"2022-12-31"].index
    wide = lambda col: pd.DataFrame({t: f[col] for t, f in frames.items()}).reindex(idx)
    close, high, low, vol = wide("TR_CLOSE"), wide("TR_HIGH"), wide("TR_LOW"), wide("VOLUME")
    raw = pd.DataFrame({t: (f["UNADJ_CLOSE"] if "UNADJ_CLOSE" in f else f["CLOSE"]) for t, f in frames.items()}).reindex(idx)
    spy_c = spy["TR_CLOSE"].reindex(idx)
    etf = etf_close.reindex(idx)
    feats = compute_features(close, high, vol, spy_c, etf, {t: (s if s in etf.columns else None) for t, s in sectors.items()},
                             with_resmom=False)
    liq = liquid_mask(wide("CLOSE"), vol, raw_close=raw)
    dates = month_ends(idx, HOLDOUT[0].isoformat(), pd.Timestamp(HOLDOUT[1]))
    pats = pattern_feature_panel(close, high, low, vol, dates)
    tgt = targets(close, spy_c, feats["BETA252"], 20)
    u2m = u2_membership(idx, list(close.columns), membership)
    out: Dict[str, Any] = {"provenance": provenance(smoke), "universes": {}}
    for u, mask in (("u1", liq & pd.DataFrame({t: (t in u1) for t in close.columns}, index=idx)), ("u2", liq & u2m)):
        res = {}
        for name in PATTERN_FEATURES:
            r = residualize(pats[name], [feats["MOM12_1"], feats["HI52"]], mask, dates)
            res[name] = summarize_ic(ic_series(r, tgt, mask, dates))
        out["universes"][u] = res
    out["candidate_vetoes_for_future_study"] = [
        n for n in ("breakout_volume_ratio", "base_depth_pct")
        if all((out["universes"][u][n].get("mean_ic") or 0) < 0 and (out["universes"][u][n].get("nw_t") or 0) <= -3 for u in ("u1", "u2"))]
    record_trial("swing_pattern_replication", "swing", {"features": list(PATTERN_FEATURES)}, len(dates), None, None, None,
                 series_path="outputs/swing/patterns.json", smoke=smoke, extra={"report_only": True})
    return out


def _trade_rows(trades) -> List[Dict[str, Any]]:
    return [{"ticker": t.ticker, "sleeve": t.sleeve, "signal_date": t.signal_date.isoformat(), "fill_date": t.fill_date.isoformat(),
             "exit_date": t.exit_date.isoformat() if t.exit_date else None, "fill_price": t.fill_price, "exit_price": t.exit_price,
             "stop_loss": t.stop_loss, "target": t.target, "exit_reason": t.exit_reason, "holding_sessions": t.holding_sessions,
             "beta": t.beta, "net_return_pct": t.net_return_pct, "spy_return_pct": t.spy_return_pct,
             "beta_adj_pct": t.beta_adj_pct, "half_spread_pct": t.half_spread_pct, **(t.details or {})} for t in trades]


def concurrency(trades) -> Dict[str, Any]:
    if not trades:
        return {"median": 0, "p95": 0, "max": 0}
    ev = defaultdict(int)
    for t in trades:
        ev[t.fill_date] += 1
        ev[t.exit_date] -= 1
    cur, counts = 0, []
    for d in sorted(ev):
        cur += ev[d]
        counts.append(cur)
    return {"median": int(np.median(counts)), "p95": int(np.percentile(counts, 95)), "max": int(max(counts))}


def stage_sleeve(sleeve: str, limit: Optional[int], n_placebo: int, smoke: bool) -> Dict[str, Any]:
    frames, spy, etf_close, sectors, filings, rights, u1, membership, insider, sec_stats = load_all(
        limit, need_filings=True, need_insider=(sleeve == "I"))
    out: Dict[str, Any] = {"provenance": provenance(smoke), "sleeve": sleeve, "sec": sec_stats, "universes": {}}
    if sleeve == "I" and (insider is None or insider.empty):
        out["untested"] = "insider data sets unavailable"
        return out
    from src.swing.engine import build_ticker_arrays
    tick = build_ticker_arrays(frames, filings, rights)
    frames = {t: None for t in tick}                       # free the raw frames; arrays hold what is needed
    for u in ("u2", "u1"):
        t0 = time.time()
        ctx = build_context(u, frames, spy, etf_close, sectors, filings, rights, u1_tickers=u1, membership=membership,
                            window=(PANEL_WINDOW[0], spy.index[-1].date().isoformat()), with_c6=(sleeve == "T"), insider=insider,
                            tick=tick)
        book = run_book(ctx, sleeve, *HOLDOUT)
        trades = book["trades"]
        logger.info("Sleeve %s %s: %d trades, %d censored, funnel %s (%.0fs)", sleeve, u, len(trades), len(book["censored"]),
                    book["funnel"], time.time() - t0)
        pl = SwingPlacebo(ctx).run(sleeve, trades, n_runs=n_placebo) if trades and n_placebo else {"means": [], "per_trade_median": []}
        med = np.array(pl.get("per_trade_median", []), dtype=float)
        paired = [(tr, m) for tr, m in zip(trades, med) if np.isfinite(m)] if len(med) == len(trades) else []
        ev = evaluate_sleeve_trades([tr for tr, _ in paired] if paired else trades, sleeve, u,
                                    placebo_means=pl.get("means"), placebo_trade_returns=[m for _, m in paired] if paired else None)
        ev["placebo_coverage"] = pl.get("coverage")
        ev["n_signals_all"] = len(trades)
        ev["censored"] = len(book["censored"])
        ev["funnel"] = book["funnel"]
        ev["monte_carlo_drawdown_units"] = monte_carlo_trade_shuffle(trades)
        ev["concurrent_open_signals"] = concurrency(trades)
        # Report-only: close-checked stops, and the recent period (unseen only for E, N, P, I)
        cc = run_book(ctx, sleeve, *HOLDOUT, close_checked_stops=True)["trades"]
        ev["report_close_checked_stops_mean_beta_adj"] = (round(float(np.mean([x.beta_adj_pct for x in cc if x.beta_adj_pct is not None])), 4)
                                                          if cc else None)
        rec = run_book(ctx, sleeve, *RECENT)["trades"]
        rb = [x.beta_adj_pct for x in rec if x.beta_adj_pct is not None]
        ev["report_recent_period"] = {"n": len(rb), "mean_beta_adj": round(float(np.mean(rb)), 4) if rb else None,
                                      "unseen": sleeve != "T"}
        out["universes"][u] = ev
        pd.DataFrame(_trade_rows(trades)).to_csv(os.path.join(OUT_DIR, f"trades_{sleeve}_{u}.csv"), index=False)
        record_trial(f"S-{sleeve}", "swing", {"sleeve": sleeve, "universe": u, "n_placebo": n_placebo, "limit": limit},
                     ev.get("n_signals", 0), ev.get("mean_beta_adj_pct"), ev.get("ci95_beta_adj"), None,
                     series_path=f"outputs/swing/trades_{sleeve}_{u}.csv", smoke=smoke,
                     extra={"placebo_percentile": ev.get("placebo_percentile"), "p_gt0": ev.get("p_gt0"),
                            "break_even_bps": ev.get("break_even_bps_per_side")})
    return out


def stage_decide(smoke: bool) -> Dict[str, Any]:
    rep_path = os.path.join(OUT_DIR, "replication.json")
    rep = json.load(open(rep_path)) if os.path.exists(rep_path) else {}
    c6_u2 = rep.get("universes", {}).get("u2", {}).get("C6")
    res = {}
    for s in SLEEVES:
        p = os.path.join(OUT_DIR, f"sleeve_{s}.json")
        res[s] = json.load(open(p)) if os.path.exists(p) else {"untested": "sleeve job produced no result"}
    pvals = {s: (r.get("universes", {}).get("u2", {}).get("p_gt0") if "untested" not in r else None) for s, r in res.items()}
    tested = [s for s in SLEEVES if pvals[s] is not None]
    adj = dict(zip(tested, holm([pvals[s] for s in tested]))) if tested else {}
    decisions, config = {}, {}
    any_smoke = smoke or any(r.get("provenance", {}).get("smoke") for r in res.values()) or bool(rep.get("provenance", {}).get("smoke"))
    for s in SLEEVES:
        r = res[s]
        if "untested" in r:
            status, reasons = "SHADOW", [f"untested: {r['untested']}"]
            u2 = u1 = {}
        else:
            u2 = dict(r["universes"].get("u2", {"sleeve": s, "n_signals": 0}))
            u1 = r["universes"].get("u1", {"sleeve": s, "n_signals": 0})
            if s == "T":                                   # P8 from the U2 replication of C6
                u2.setdefault("p_rules", {})["P8"] = bool(((c6_u2 or {}).get("mean_ic") or 0) > 0
                                                         and ((c6_u2 or {}).get("nw_t") or 0) >= 2.0)
            status, reasons = decide_sleeve_status(u2, u1, adj.get(s, 1.0))
        decisions[s] = {"status": status, "reasons": reasons, "holm_adjusted_p": adj.get(s), "u2": u2, "u1": u1}
        config[s] = {
            "status": status,
            "universe": "u1" if status == "LIVE" else ("sp500" if status == "LIVE-SP500" else "shadow"),
            "holdout": [HOLDOUT[0].isoformat(), HOLDOUT[1].isoformat()],
            "u2": {k: u2.get(k) for k in ("n_signals", "mean_beta_adj_pct", "ci95_beta_adj", "placebo_percentile",
                                           "paired_placebo_diff", "paired_placebo_ci", "break_even_bps_per_side")} if u2 else None,
            "u1": {k: u1.get(k) for k in ("n_signals", "mean_beta_adj_pct", "ci95_beta_adj", "placebo_percentile")} if u1 else None,
            "reasons": reasons,
        }
    out = {"provenance": provenance(any_smoke), "holm_adjusted_p": adj, "decisions": decisions,
           "overlays": "not run: overlays are evaluated only on LIVE sleeves" if not any(
               d["status"].startswith("LIVE") for d in decisions.values()) else "pending",
           "config": config}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("replication", "patterns", "sleeve", "decide"), required=True)
    ap.add_argument("--sleeve", choices=SLEEVES)
    ap.add_argument("--placebo", type=int, default=200)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    smoke = bool(args.limit) or not os.environ.get("GITHUB_RUN_ID")
    os.makedirs(OUT_DIR, exist_ok=True)
    if args.stage == "replication":
        out, name = stage_replication(args.limit, smoke), "replication.json"
    elif args.stage == "patterns":
        out, name = stage_patterns(args.limit, smoke), "patterns.json"
    elif args.stage == "sleeve":
        out, name = stage_sleeve(args.sleeve, args.limit, args.placebo, smoke), f"sleeve_{args.sleeve}.json"
    else:
        out, name = stage_decide(smoke), "decisions.json"
        if not out["provenance"]["smoke"]:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"provenance": out["provenance"], "sleeves": out["config"]}, f, indent=2, default=str)
            logger.info("Wrote %s from run %s", CONFIG_PATH, out["provenance"]["github_run_id"])
        else:
            logger.warning("Smoke or local run: config/swing_sleeves.json NOT written.")
    with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    logger.info("Wrote outputs/swing/%s", name)


if __name__ == "__main__":
    main()
