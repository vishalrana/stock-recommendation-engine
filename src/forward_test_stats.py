"""
Statistics of the pre-registered forward-test reviews (docs/forward-test-reviews.md)
====================================================================================
M1 is the mean beta-adjusted % per closed idea with a 95% CI from 2,000 signal-month block-bootstrap
resamples (counts only below 20 closed ideas); M2 is the cumulative equal-unit beta-adjusted P&L of
all ideas, open ones marked at the latest close. The STOP threshold is the 1st percentile of 2,000
signal-month block-bootstrap paths of run 40205392302's issued ideas with the same number of ideas.
"""

from typing import Any, Dict, Optional, Sequence

import numpy as np

BOOTSTRAP = 2000
MIN_CLOSED_FOR_M1 = 20
STOP_PERCENTILE = 1.0
SEED = 7   # the harness's block_bootstrap_ci seed


def m1(values: Sequence[float], months: Sequence[str]) -> Dict[str, Any]:
    """Mean beta-adjusted % per closed idea and its 95% signal-month block-bootstrap CI."""
    from scripts.validate_backtest_pipeline import block_bootstrap_ci
    v = np.asarray(values, dtype=float)
    out: Dict[str, Any] = {"closed": int(len(v))}
    if len(v) < MIN_CLOSED_FOR_M1:
        return dict(out, note=f"below {MIN_CLOSED_FOR_M1} closed ideas: counts only")
    lo, hi = block_bootstrap_ci(v, np.asarray(months), np.mean, n_boot=BOOTSTRAP, seed=SEED)
    return dict(out, mean_pct=round(float(v.mean()), 4), ci95=None if lo is None else [round(lo, 4), round(hi, 4)])


def m2(closed: Sequence[float], open_marked: Sequence[float]) -> float:
    """Cumulative equal-unit beta-adjusted P&L in units of % per idea: closed plus open marked."""
    return round(float(np.sum(closed) + np.sum(open_marked)), 4)


def stop_threshold(values: Sequence[float], months: Sequence[str], n: int, paths: int = BOOTSTRAP,
                   seed: int = SEED, pct: float = STOP_PERCENTILE) -> Optional[float]:
    """The pct-th percentile of the cumulative beta-adjusted P&L of n ideas over `paths` bootstrap
    paths: whole signal months drawn with replacement, in draw order, until n ideas, cut at n."""
    if n <= 0:
        return None
    v, m = np.asarray(values, dtype=float), np.asarray(months)
    groups = [v[m == k] for k in np.unique(m)]
    rng = np.random.default_rng(seed)
    sums = np.empty(paths)
    for p in range(paths):
        picked, total = [], 0
        while total < n:
            g = groups[rng.integers(len(groups))]
            picked.append(g)
            total += len(g)
        sums[p] = np.concatenate(picked)[:n].sum()
    return round(float(np.percentile(sums, pct)), 4)
