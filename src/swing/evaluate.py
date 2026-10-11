"""
Swing Signals — Performance Evaluation, Placebo & Pre-Registered Decisions
==========================================================================
Implements all pre-registered quantitative evaluation rules:
- 10,000 signal-month block-bootstrap 95% CIs for beta-adjusted returns
- 200-run random-stock placebo engine (via PlaceboEngine / stable_seed)
- 3 five-year blocks stability analysis (2008-2012, 2013-2017, 2018-2022)
- Cost scenarios and break-even cost per side
- S-PASS rules check: P1 through P8
- Rule O overlay evaluation: G1, V1, X1
- Monte Carlo trade-order shuffle (2,000 shuffles)
"""

from collections import defaultdict
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.research.costs import BASE_COST_PCT_PER_SIDE, cost_scenarios
from src.research.placebo import PlaceboEngine, stable_seed
from src.research.stats import holm, percentile_of
from src.swing.exits import SwingTrade


def block_bootstrap_ci_10k(
    values: np.ndarray,
    clusters: np.ndarray,
    n_boot: int = 10000,
    seed: int = 7,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    10,000 signal-month block-bootstrap 95% CI and one-sided p-value (H0: mean <= 0).
    Returns (ci_lo, ci_hi, p_gt0).
    """
    valid = np.isfinite(values)
    values = values[valid]
    clusters = clusters[valid]

    uniq = np.unique(clusters)
    if len(uniq) < 2 or len(values) < 5:
        return None, None, None

    groups = [values[clusters == c] for c in uniq]
    rng = np.random.default_rng(seed)
    stats = []

    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        sample = np.concatenate([groups[i] for i in pick])
        if len(sample):
            stats.append(float(np.mean(sample)))

    if not stats:
        return None, None, None

    lo, hi = np.percentile(stats, [2.5, 97.5])
    p_gt0 = float((np.sum(np.array(stats) <= 0.0) + 1) / (len(stats) + 1))

    return round(float(lo), 4), round(float(hi), 4), round(p_gt0, 4)


def paired_block_diff_10k(
    a_values: np.ndarray,
    a_clusters: np.ndarray,
    b_values: np.ndarray,
    b_clusters: np.ndarray,
    n_boot: int = 10000,
    seed: int = 11,
) -> Tuple[Optional[float], Optional[Tuple[float, float]], Optional[float]]:
    """
    Paired signal-month block bootstrap difference: mean(a) - mean(b).
    Returns (diff, (ci_lo, ci_hi), p_gt0).
    """
    if len(a_values) < 5 or len(b_values) < 5:
        return None, None, None

    all_clusters = sorted(set(a_clusters) | set(b_clusters))
    ga = {c: a_values[a_clusters == c] for c in all_clusters}
    gb = {c: b_values[b_clusters == c] for c in all_clusters}

    rng = np.random.default_rng(seed)
    diffs = []

    for _ in range(n_boot):
        pick = rng.choice(all_clusters, len(all_clusters))
        x = np.concatenate([ga[c] for c in pick if len(ga[c])])
        y = np.concatenate([gb[c] for c in pick if len(gb[c])])
        if len(x) and len(y):
            diffs.append(float(np.mean(x) - np.mean(y)))

    if not diffs:
        return None, None, None

    point = float(np.mean(a_values) - np.mean(b_values))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    p_gt0 = float((np.sum(np.array(diffs) <= 0.0) + 1) / (len(diffs) + 1))

    return round(point, 4), (round(float(lo), 4), round(float(hi), 4)), round(p_gt0, 4)


def evaluate_five_year_blocks(trades: List[SwingTrade]) -> Dict[str, Any]:
    """
    Evaluate 3 five-year blocks stability (P4):
    - Block 1: 2008-01-01 .. 2012-12-31
    - Block 2: 2013-01-01 .. 2017-12-31
    - Block 3: 2018-01-01 .. 2022-09-30
    Check: mean > 0 in at least 2 blocks, and no block mean < -0.50.
    """
    blocks = {
        "block1_2008_2012": (date(2008, 1, 1), date(2012, 12, 31)),
        "block2_2013_2017": (date(2013, 1, 1), date(2017, 12, 31)),
        "block3_2018_2022": (date(2018, 1, 1), date(2022, 9, 30)),
    }

    results: Dict[str, Any] = {}
    pos_count = 0
    worst_block = float("inf")

    for name, (start, end) in blocks.items():
        subset = [
            t.beta_adj_pct for t in trades
            if start <= t.signal_date <= end and t.beta_adj_pct is not None and np.isfinite(t.beta_adj_pct)
        ]
        if subset:
            m = float(np.mean(subset))
            results[name] = {"n": len(subset), "mean_beta_adj": round(m, 4)}
            if m > 0:
                pos_count += 1
            if m < worst_block:
                worst_block = m
        else:
            results[name] = {"n": 0, "mean_beta_adj": None}

    passes_p4 = (pos_count >= 2) and (worst_block >= -0.50)
    results["passes_p4"] = passes_p4
    results["pos_blocks"] = pos_count
    results["worst_block_mean"] = round(worst_block, 4) if worst_block != float("inf") else None
    return results


def monte_carlo_trade_shuffle(
    trades: List[SwingTrade],
    n_shuffles: int = 2000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Monte Carlo trade-order shuffle (2,000 shuffles per sleeve):
    Returns max drawdown distribution in equal units.
    """
    rets = np.array([
        t.beta_adj_pct for t in trades
        if t.beta_adj_pct is not None and np.isfinite(t.beta_adj_pct)
    ], dtype=float)

    if len(rets) < 5:
        return {"n_trades": len(rets), "drawdown_median": None}

    rng = np.random.default_rng(seed)
    max_dds = []

    for _ in range(n_shuffles):
        shuffled = rng.permutation(rets)
        cum = np.cumsum(shuffled)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum  # Drawdown in units
        max_dds.append(float(np.max(dd)))

    p5, p50, p95 = np.percentile(max_dds, [5, 50, 95])
    return {
        "n_trades": len(rets),
        "drawdown_p5": round(float(p5), 2),
        "drawdown_median": round(float(p50), 2),
        "drawdown_p95": round(float(p95), 2),
    }


def evaluate_sleeve_trades(
    trades: List[SwingTrade],
    sleeve: str,
    universe_name: str,
    c6_ic_stats: Optional[Dict[str, Any]] = None,
    placebo_means: Optional[List[float]] = None,
    placebo_trade_returns: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Complete quantitative evaluation for one sleeve in a universe:
    Calculates returns, CIs, cost scenarios, 5-year blocks, placebo comparisons,
    and checks P1-P8 rules.
    """
    completed = [t for t in trades if t.exit_price is not None and t.beta_adj_pct is not None]
    n_signals = len(completed)

    if n_signals == 0:
        return {
            "sleeve": sleeve,
            "universe": universe_name,
            "n_signals": 0,
            "outcome": "SHADOW",
            "decision_reasons": ["No completed signals"],
        }

    beta_adjs = np.array([t.beta_adj_pct for t in completed], dtype=float)
    net_rets = np.array([t.net_return_pct for t in completed], dtype=float)
    spy_rets = np.array([t.spy_return_pct for t in completed], dtype=float)
    months = np.array([t.signal_date.strftime("%Y-%m") for t in completed])
    distinct_months = sorted(set(months))

    mean_ba = float(np.mean(beta_adjs))
    mean_net = float(np.mean(net_rets))
    mean_spy = float(np.mean(spy_rets))

    ci_lo, ci_hi, p_gt0 = block_bootstrap_ci_10k(beta_adjs, months)

    # Cost scenarios
    trade_dicts = [
        {
            "beta_adjusted_pct": t.beta_adj_pct,
            "half_spread_pct": t.half_spread_pct or 0.0,
            "signal_date": t.signal_date.isoformat(),
        }
        for t in completed
    ]
    costs = cost_scenarios(trade_dicts)
    break_even_bps = costs.get("break_even_bps_per_side", 0.0)

    # 5-year blocks stability
    block_eval = evaluate_five_year_blocks(completed)

    # Exit reason distribution
    reason_counts = defaultdict(int)
    for t in completed:
        reason_counts[t.exit_reason or "unknown"] += 1
    exit_mix = {k: round(v / n_signals, 4) for k, v in reason_counts.items()}

    # Left tail
    p5_ret = float(np.percentile(beta_adjs, 5))
    worst_ret = float(np.min(beta_adjs))

    # Placebo comparison
    placebo_pctile = None
    paired_placebo_diff = None
    paired_placebo_ci = None
    if placebo_means and len(placebo_means) >= 50:
        placebo_pctile = percentile_of(mean_ba, placebo_means)
        if placebo_trade_returns and len(placebo_trade_returns) == len(beta_adjs):
            paired_diff, p_ci, _ = paired_block_diff_10k(
                beta_adjs, months,
                np.array(placebo_trade_returns), months,
            )
            paired_placebo_diff = paired_diff
            paired_placebo_ci = p_ci

    # PASS rules evaluation (P1-P8)
    p_rules: Dict[str, bool] = {}
    # P1: mean beta_adj_pct > 0 and CI lower bound > 0
    p_rules["P1"] = bool(mean_ba > 0 and ci_lo is not None and ci_lo > 0)

    # P2: book mean > 95th pct of 200 placebos AND paired diff > 0 with CI lower bound > 0
    p2_pass = False
    if placebo_pctile is not None and placebo_pctile >= 95.0:
        if paired_placebo_diff is not None and paired_placebo_diff > 0:
            if paired_placebo_ci is not None and paired_placebo_ci[0] > 0:
                p2_pass = True
    p_rules["P2"] = p2_pass

    # P3: U1 confirmation: mean beta_adj_pct > 0 AND placebo percentile >= 90
    p3_pass = bool(mean_ba > 0 and placebo_pctile is not None and placebo_pctile >= 90.0)
    p_rules["P3"] = p3_pass

    # P4: U2 stability
    p_rules["P4"] = bool(block_eval.get("passes_p4", False))

    # P6: Costs: break-even cost >= 15 bps per side
    p_rules["P6"] = bool(break_even_bps >= 15.0)

    # P7: Sample: >= 100 signals over >= 36 distinct signal months
    p_rules["P7"] = bool(n_signals >= 100 and len(distinct_months) >= 36)

    # P8 (T only): C6 replication in U2 holdout has mean IC > 0 with NW t >= 2.0
    if sleeve == "T" and c6_ic_stats:
        c6_mean_ic = c6_ic_stats.get("mean_ic") or 0.0
        c6_nw_t = c6_ic_stats.get("nw_t") or 0.0
        p_rules["P8"] = bool(c6_mean_ic > 0 and c6_nw_t >= 2.0)
    elif sleeve == "T":
        p_rules["P8"] = False
    else:
        p_rules["P8"] = True  # N/A for non-T sleeves

    return {
        "sleeve": sleeve,
        "universe": universe_name,
        "n_signals": n_signals,
        "distinct_months": len(distinct_months),
        "mean_beta_adj_pct": round(mean_ba, 4),
        "ci95_beta_adj": [ci_lo, ci_hi],
        "p_gt0": p_gt0,
        "mean_net_return_pct": round(mean_net, 4),
        "mean_spy_return_pct": round(mean_spy, 4),
        "break_even_bps_per_side": break_even_bps,
        "costs": costs,
        "blocks_5y": block_eval,
        "exit_mix": exit_mix,
        "p5_return": round(p5_ret, 4),
        "worst_return": round(worst_ret, 4),
        "placebo_percentile": round(placebo_pctile, 2) if placebo_pctile is not None else None,
        "paired_placebo_diff": paired_placebo_diff,
        "paired_placebo_ci": paired_placebo_ci,
        "p_rules": p_rules,
    }


def decide_sleeve_status(
    u2_result: Dict[str, Any],
    u1_result: Dict[str, Any],
    p5_holm_p: float,
) -> Tuple[str, List[str]]:
    """
    Decide sleeve outcome per pre-registered S-PASS rules:
    - P1-P8 all pass -> LIVE on U1
    - P1, P2 and P4-P8 pass but P3 fails -> LIVE-SP500
    - Anything else -> SHADOW
    """
    sleeve = u2_result["sleeve"]
    p_rules = dict(u2_result.get("p_rules", {}))
    # P5 Multiple testing: Holm adjusted p < 0.05
    p_rules["P5"] = bool(p5_holm_p < 0.05)
    # P3 comes from U1 result
    p_rules["P3"] = bool(u1_result.get("p_rules", {}).get("P3", False))

    reasons = []
    for r in ["P1", "P2", "P4", "P5", "P6", "P7", "P8"]:
        if not p_rules.get(r, False):
            reasons.append(f"{r} failed on U2")

    if not p_rules.get("P3", False):
        reasons.append("P3 failed on U1 confirmation")

    all_pass = all(p_rules.get(f"P{i}", False) for i in range(1, 9))
    if all_pass:
        return "LIVE", reasons

    core_u2_pass = all(p_rules.get(f"P{i}", False) for i in [1, 2, 4, 5, 6, 7, 8])
    if core_u2_pass and not p_rules.get("P3", False):
        return "LIVE-SP500", reasons

    return "SHADOW", reasons
