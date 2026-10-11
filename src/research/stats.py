"""
Multiple-testing and inference helpers for the profitability program
=====================================================================
- holm(): Holm-Bonferroni step-down adjusted p-values (pure numpy).
- newey_west_t(): t-statistic of a mean with a Bartlett-kernel HAC standard error.
- paired_month_bootstrap(): paired signal-month block bootstrap of a mean difference, with a
  CI and one-sided p-values (the inputs to Holm).
- monthly_series(): per-signal-month mean of a per-idea metric (the series StepM compares).
- stepm_superior(): Romano-Wolf StepM (arch.bootstrap.StepM, stationary bootstrap) over
  per-signal-month series of variants against production. Needs the research requirements.
- deflated_sharpe(): Bailey & Lopez de Prado deflated Sharpe ratio (optional diagnostic).
"""

import math
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

_N = NormalDist()


def holm(pvalues: Sequence[float]) -> List[float]:
    """Holm step-down adjusted p-values, in the input order (monotone, capped at 1)."""
    p = np.asarray(list(pvalues), dtype=float)
    m = len(p)
    if m == 0:
        return []
    order = np.argsort(p)
    adjusted = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p[idx]))
        adjusted[idx] = running
    return adjusted.tolist()


def newey_west_t(x: Sequence[float], lags: Optional[int] = None) -> float:
    """t-stat of mean(x) with a Newey-West (Bartlett) variance; lags default 4(n/100)^(2/9)."""
    x = np.asarray([v for v in x if v is not None and np.isfinite(v)], dtype=float)
    n = len(x)
    if n < 3:
        return float("nan")
    if lags is None:
        lags = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    e = x - x.mean()
    var = float(e @ e) / n
    for lag in range(1, min(lags, n - 1) + 1):
        cov = float(e[lag:] @ e[:-lag]) / n
        var += 2.0 * (1.0 - lag / (lags + 1.0)) * cov
    if var <= 0:
        return float("nan")
    return float(x.mean() / math.sqrt(var / n))


def paired_month_bootstrap(a_months: Sequence[str], a_vals: Sequence[float],
                           b_months: Sequence[str], b_vals: Sequence[float],
                           n_boot: int = 2000, seed: int = 11) -> Dict[str, Optional[float]]:
    """
    mean(a) - mean(b), resampling whole signal months for both books. Returns the point
    estimate, 95% CI and one-sided bootstrap p-values p_gt0 (H0: diff <= 0) and p_lt0.
    """
    a = [(m, float(v)) for m, v in zip(a_months, a_vals) if v is not None and np.isfinite(v)]
    b = [(m, float(v)) for m, v in zip(b_months, b_vals) if v is not None and np.isfinite(v)]
    if len(a) < 30 or len(b) < 30:
        return {"diff": None, "ci95": None, "p_gt0": None, "p_lt0": None}
    months = sorted({m for m, _ in a} | {m for m, _ in b})
    ga = {m: np.array([v for mm, v in a if mm == m]) for m in months}
    gb = {m: np.array([v for mm, v in b if mm == m]) for m in months}
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        pick = rng.choice(months, len(months))
        x = np.concatenate([ga[m] for m in pick])
        y = np.concatenate([gb[m] for m in pick])
        if len(x) and len(y):
            diffs.append(x.mean() - y.mean())
    diffs = np.asarray(diffs)
    point = float(np.mean([v for _, v in a]) - np.mean([v for _, v in b]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {
        "diff": round(point, 4),
        "ci95": [round(float(lo), 4), round(float(hi), 4)],
        "p_gt0": float((np.sum(diffs <= 0) + 1) / (len(diffs) + 1)),
        "p_lt0": float((np.sum(diffs >= 0) + 1) / (len(diffs) + 1)),
    }


def monthly_series(months: Iterable[str], values: Iterable[float]) -> Dict[str, float]:
    """Per-signal-month mean of a per-idea metric (non-finite values ignored)."""
    acc: Dict[str, List[float]] = {}
    for m, v in zip(months, values):
        if v is not None and np.isfinite(v):
            acc.setdefault(m, []).append(float(v))
    return {m: float(np.mean(v)) for m, v in sorted(acc.items())}


def stepm_superior(production: Dict[str, float], variants: Dict[str, Dict[str, float]],
                   size: float = 0.05, reps: int = 2000, seed: int = 23) -> Dict[str, object]:
    """
    StepM (Romano & Wolf 2005, arch implementation, stationary bootstrap) on per-signal-month
    series: which variants beat production at familywise level `size`. Months missing from any
    series are dropped (all series aligned on common months). Losses are negative returns.
    """
    from arch.bootstrap import StepM  # research requirement (requirements-research.txt)
    import pandas as pd

    names = sorted(variants)
    months = sorted(set(production).intersection(*[set(variants[n]) for n in names])) if names else []
    if len(months) < 12 or not names:
        return {"superior": [], "months": len(months), "note": "too few common months"}
    bench = -np.array([production[m] for m in months])
    models = pd.DataFrame({n: -np.array([variants[n][m] for m in months]) for n in names}, index=months)
    stepm = StepM(bench, models, size=size, reps=reps, bootstrap="stationary", seed=seed)
    stepm.compute()
    superior = [str(s) for s in (stepm.superior_models or [])]
    return {"superior": superior, "months": len(months), "size": size}


def deflated_sharpe(sharpe: float, n_trials: int, n_obs: int, skew: float = 0.0, kurt: float = 3.0,
                    trial_sharpe_var: Optional[float] = None) -> float:
    """
    Deflated Sharpe ratio: P(true SR > SR0) where SR0 is the expected maximum of n_trials null
    Sharpe ratios (Bailey & Lopez de Prado 2014). Per-period (not annualised) Sharpe ratios.
    """
    if n_trials < 1 or n_obs < 3:
        return float("nan")
    gamma = 0.5772156649
    var = trial_sharpe_var if trial_sharpe_var is not None else 1.0 / (n_obs - 1)
    sr0 = 0.0
    if n_trials > 1:
        sr0 = math.sqrt(var) * ((1 - gamma) * _N.inv_cdf(1 - 1.0 / n_trials)
                                + gamma * _N.inv_cdf(1 - 1.0 / (n_trials * math.e)))
    denom = math.sqrt(max(1e-12, 1 - skew * sharpe + (kurt - 1) / 4.0 * sharpe ** 2))
    return float(_N.cdf((sharpe - sr0) * math.sqrt(n_obs - 1) / denom))


def percentile_of(value: float, distribution: Sequence[float]) -> Optional[float]:
    """Percentile (0-100) of value within a distribution (share strictly below, ties half)."""
    d = np.asarray([x for x in distribution if x is not None and np.isfinite(x)], dtype=float)
    if not len(d) or value is None or not np.isfinite(value):
        return None
    return float(100.0 * (np.sum(d < value) + 0.5 * np.sum(d == value)) / len(d))
