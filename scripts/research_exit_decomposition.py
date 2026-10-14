"""
Exit decomposition (research only, no live changes)
===================================================
Re-scores the issued ideas of a production-universe backtest with fixed-horizon exits (no stop,
no target: exit at the close of holding day h, the fill bar being day 1), and runs the
random-stock placebo under the same exits. Same beta (252 sessions before the signal), same
10 bps/side cost, same fill (next open) and the same placebo draws as the harness
(src/research/placebo.PlaceboEngine). Writes outputs/exit_decomposition.json.

  python scripts/research_exit_decomposition.py <artifact dir> [--horizons 5,10,20] [--placebo 200]
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.validate_backtest_pipeline import (  # noqa: E402
    LATE_FROM, PLACEBO_SEED, TRANSACTION_COST_PCT_PER_SIDE, WARMUP_BARS,
    block_bootstrap_ci, load_history, point_in_time_betas, prepare_frame, production_universe)
from jobs.strategies.sector_rotation import SECTOR_ETFS  # noqa: E402
from src.research.costs import cost_scenarios  # noqa: E402
from src.research.placebo import PlaceboEngine, fast_outcome  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("exit_decomposition")
TRADES = "production_backtest_trades_production_universe.csv"


class FixedHorizonEngine(PlaceboEngine):
    """PlaceboEngine whose exit is the close of holding day `horizon`; stop and targets are ignored."""
    horizon: Optional[int] = None

    def _simulate(self, ticker, date, m_stop, m_targets, hold):
        if self.horizon is None:
            return super()._simulate(ticker, date, m_stop, m_targets, hold)
        d = self.data[ticker]
        p = self._pos(d, date)
        if p is None or p + 1 >= len(d["c"]):
            return None
        fill = d["o"][p + 1]
        if not (np.isfinite(fill) and fill > 0):
            return None
        # stop 0 never triggers and an infinite T1 never fills: fast_outcome then exits at expiry
        res = fast_outcome(d["o"], d["h"], d["l"], d["c"], p + 1, fill, 0.0, float("inf"), None, None, self.horizon)
        if res is None:
            return None                       # fewer than `horizon` bars left: censored, never forced
        ret, reason, _, exit_i = res
        so, sc = self.spy_open.get(d["index"][p + 1]), self.spy_close.get(d["index"][exit_i])
        beta = float(d["beta"][p])
        if so is None or sc is None or not np.isfinite(beta):
            return None
        return ret - 2.0 * self.cost - beta * (float(sc) / float(so) - 1.0) * 100.0, reason


def _ci(values, months):
    return block_bootstrap_ci(np.asarray(values, dtype=float), np.asarray(months), np.mean)


def summarize(trades: List[Dict[str, Any]], engine: FixedHorizonEngine, n_placebo: int) -> Dict[str, Any]:
    ba = np.array([t["beta_adjusted_pct"] for t in trades], dtype=float)
    lo, hi = _ci(ba, [t["signal_date"][:7] for t in trades]) if len(trades) else (None, None)
    out = {"n": len(trades), "mean_beta_adjusted_pct": round(float(ba.mean()), 4) if len(ba) else None,
           "ci95": None if lo is None else [round(lo, 4), round(hi, 4)],
           "break_even_bps_per_side": cost_scenarios(trades).get("break_even_bps_per_side")}
    if n_placebo and len(trades) >= 30:
        pl = engine.evaluate_book(trades, lambda t: float(t.get("atr_14") or 0.0), n_placebo, _ci)
        out["placebo"] = {k: pl.get(k) for k in ("n_ideas", "placebo_percentile", "placebo_mean_of_means", "placebo_p05_p95",
                                                 "paired_diff_vs_placebo_median", "paired_diff_ci95")}
    return out


def rescore(trades: List[Dict[str, Any]], stock_engine: FixedHorizonEngine, etf_engine: FixedHorizonEngine,
            horizon: Optional[int]) -> Tuple[List[Dict[str, Any]], int]:
    """Each idea's own return under the engine's exit; None horizon = the live exit rules (self-check)."""
    out, censored = [], 0
    for t in trades:
        eng = etf_engine if t["ticker"] in SECTOR_ETFS else stock_engine
        if t["ticker"] not in eng.data or not np.isfinite(float(t.get("atr_14") or np.nan)):
            censored += 1
            continue
        entry, atr = float(t["entry_signal_close"]), float(t["atr_14"])
        m_t = [(float(t[k]) - entry) / atr if pd.notna(t.get(k)) else None for k in ("target_1", "target_2", "target_3")]
        eng.horizon = horizon
        sim = eng._simulate(t["ticker"], pd.Timestamp(t["signal_date"]), (entry - float(t["stop_loss"])) / atr, m_t,
                            int(eng.hold_days_for(t["strategy"]) or 20))
        if sim is None:
            censored += 1
            continue
        out.append(dict(t, beta_adjusted_pct=sim[0], half_spread_pct=None))
    return out, censored


def gap_through(trades: pd.DataFrame) -> Dict[str, Any]:
    """Initial-stop exits that filled below the stop. Opens already below the stop at the fill are
    counted apart: they exit at the fill for about 0%, so overshoot past the stop is not a loss there."""
    stops = trades[(trades["exit_reason"] == "stop") & (trades["outcome"] == "stopped")].copy()
    stops["planned_loss_pct"] = (stops["fill_price"] - stops["stop_loss"]) / stops["fill_price"] * 100.0
    stops["realized_loss_pct"] = (stops["fill_price"] - stops["exit_price"]) / stops["fill_price"] * 100.0
    at_fill = stops[stops["fill_price"] <= stops["stop_loss"] * (1 + 1e-6)]
    held = stops.drop(at_fill.index)
    g = held[held["exit_price"] < held["stop_loss"] * (1 - 1e-6)].copy()
    g["overshoot_pct"] = (g["stop_loss"] - g["exit_price"]) / g["stop_loss"] * 100.0

    def r(v):
        return None if v is None or not np.isfinite(v) else round(float(v), 3)

    worst = g.sort_values("realized_loss_pct", ascending=False).head(10)
    return {
        "initial_stop_exits": int(len(stops)),
        "fill_already_below_stop": int(len(at_fill)),
        "fill_already_below_stop_mean_return_pct": r(-at_fill["realized_loss_pct"].mean()) if len(at_fill) else None,
        "gapped_through_during_hold": int(len(g)),
        "share_of_initial_stops_pct": round(100.0 * len(g) / max(1, len(stops)), 2),
        "overshoot_past_stop_pct": {"mean": r(g["overshoot_pct"].mean()), "median": r(g["overshoot_pct"].median()),
                                    "p95": r(g["overshoot_pct"].quantile(0.95)), "max": r(g["overshoot_pct"].max())},
        "gapped_planned_vs_realized_loss_pct": [r(g["planned_loss_pct"].mean()), r(g["realized_loss_pct"].mean())],
        "all_held_stops_planned_vs_realized_loss_pct": [r(held["planned_loss_pct"].mean()), r(held["realized_loss_pct"].mean())],
        "realized_loss_over_10pct": int((held["realized_loss_pct"] > 10).sum()),
        "max_realized_loss_pct": r(held["realized_loss_pct"].max()),
        "worst": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in rec.items()}
                  for rec in worst[["ticker", "signal_date", "strategy", "fill_price", "stop_loss", "exit_price",
                                    "planned_loss_pct", "realized_loss_pct", "overshoot_pct"]].to_dict("records")],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact_dir")
    ap.add_argument("--horizons", default="5,10,20")
    ap.add_argument("--placebo", type=int, default=200)
    ap.add_argument("--out", default=os.path.join(PROJECT_ROOT, "outputs", "exit_decomposition.json"))
    args = ap.parse_args()
    tdf = pd.read_csv(os.path.join(args.artifact_dir, TRADES))
    trades = tdf.to_dict("records")
    report: Dict[str, Any] = {"source_trades": len(trades), "gap_through": gap_through(tdf)}

    stocks, etfs = production_universe()
    raw = load_history(stocks + etfs + ["SPY"])
    spy = raw["SPY"].dropna(subset=["CLOSE"]).sort_index()
    frames = {t: prepare_frame(df) for t, df in raw.items() if t != "SPY" and len(df) > WARMUP_BARS + 5}
    del raw
    betas = point_in_time_betas(frames, spy)
    from src.pipeline_steps import holding_days_for
    stock_frames = {t: f for t, f in frames.items() if t not in SECTOR_ETFS}
    etf_frames = {t: f for t, f in frames.items() if t in SECTOR_ETFS}
    kw = dict(cost_pct_per_side=TRANSACTION_COST_PCT_PER_SIDE, seed=PLACEBO_SEED)
    eng = FixedHorizonEngine(stock_frames, spy, betas, holding_days_for, **kw)
    etf_eng = FixedHorizonEngine(etf_frames, spy, betas, holding_days_for, **kw)
    report["universe_stocks_with_history"] = len(stock_frames)

    # Self-check: the engine's own simulation of the live exit rules vs the harness's recorded returns
    live, cens = rescore(trades, eng, etf_eng, None)
    rec = {(t["ticker"], t["signal_date"], t["strategy"]): t["beta_adjusted_pct"] for t in trades}
    diffs = np.array([t["beta_adjusted_pct"] - rec[(t["ticker"], t["signal_date"], t["strategy"])] for t in live
                      if pd.notna(rec[(t["ticker"], t["signal_date"], t["strategy"])])])
    report["self_check_live_rules"] = {"n": len(live), "censored": cens,
                                       "mean_abs_diff_pct": round(float(np.abs(diffs).mean()), 4),
                                       "share_within_0_05pct": round(float((np.abs(diffs) <= 0.05).mean() * 100), 2)}
    orig = [t for t in trades if pd.notna(t.get("beta_adjusted_pct"))]
    eng.horizon = None
    report["live_rules"] = {"full": summarize(orig, eng, args.placebo),
                            "late": summarize([t for t in orig if t["signal_date"] >= LATE_FROM], eng, args.placebo)}
    logger.info("Live rules: %s", report["live_rules"])

    report["fixed_horizon"] = {}
    for h in (int(x) for x in args.horizons.split(",")):
        scored, censored = rescore(trades, eng, etf_eng, h)
        eng.horizon = h
        eng.hold_days_for = lambda s, h=h: h
        eng._cache.clear()                       # draws are cached per idea; a new horizon needs new ones
        report["fixed_horizon"][str(h)] = {
            "censored": censored,
            "full": summarize(scored, eng, args.placebo),
            "late": summarize([t for t in scored if t["signal_date"] >= LATE_FROM], eng, args.placebo),
        }
        logger.info("Horizon %d: %s", h, report["fixed_horizon"][str(h)])
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
