"""
Trading-cost realism for the profitability program
===================================================
Corwin & Schultz (2012) high-low spread estimator, point-in-time: the two-day estimate dated t
uses days t-1 and t only, and the trailing 20-session mean ending at t is the spread estimate
available at the close of t. Negative two-day estimates are set to 0, as is standard. With closes
supplied, day t's high and low are first shifted by the overnight gap when the whole day trades
above (below) the prior close, the overnight adjustment of Corwin & Schultz (2012, section 4.2).
High-low estimators still overstate the spread of very liquid stocks (volatility leaks in), so
scenario (b) is conservative for large caps; the break-even cost does not depend on the estimate.

Cost scenarios for a book (per-idea beta-adjusted returns already net of 10 bps per side):
  (a) 10 bps per side (as backtested)
  (b) 10 bps + estimated half-spread per side
  (c) twice (b)
and the break-even cost per side at which the mean beta-adjusted return reaches 0.
"""

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

BASE_COST_PCT_PER_SIDE = 0.10
_K = 3.0 - 2.0 * math.sqrt(2.0)


def corwin_schultz_daily(high: pd.Series, low: pd.Series, close: Optional[pd.Series] = None) -> pd.Series:
    """
    Two-day Corwin-Schultz spread estimates (fraction of price), dated on the second day. With
    closes, each pair's second day is shifted by the overnight gap when it trades entirely above
    (below) the first day's close; the first day of the pair is left as it is.
    """
    h = high.astype(float)
    l = low.astype(float)
    h_prev, l_prev = h.shift(1), l.shift(1)
    if close is not None:
        prev = close.astype(float).shift(1)
        up = (l - prev).where(l > prev, 0.0).fillna(0.0)       # whole day above the prior close
        down = (prev - h).where(h < prev, 0.0).fillna(0.0)     # whole day below the prior close
        h, l = h - up + down, l - up + down
    beta = np.log(h / l) ** 2 + np.log(h_prev / l_prev) ** 2
    h2 = pd.concat([h, h_prev], axis=1).max(axis=1, skipna=False)
    l2 = pd.concat([l, l_prev], axis=1).min(axis=1, skipna=False)
    gamma = np.log(h2 / l2) ** 2
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / _K - np.sqrt(gamma / _K)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return spread.clip(lower=0.0)


def rolling_half_spread_pct(high: pd.Series, low: pd.Series, window: int = 20,
                            close: Optional[pd.Series] = None) -> pd.Series:
    """Trailing `window`-session mean half-spread in percent, using only bars up to each date."""
    return corwin_schultz_daily(high, low, close).rolling(window, min_periods=max(5, window // 2)).mean() * 50.0


def cost_scenarios(trades: List[Dict[str, Any]], ci_fn=None) -> Dict[str, Any]:
    """
    Mean beta-adjusted %/idea under the three cost scenarios, and the break-even cost per side.
    `ci_fn(values, months)` (optional) returns a 95% CI for a mean, e.g. a block bootstrap.
    Trades need beta_adjusted_pct, half_spread_pct (may be missing: then 0) and signal_date.
    """
    rows = [t for t in trades if t.get("beta_adjusted_pct") is not None and np.isfinite(t["beta_adjusted_pct"])]
    if not rows:
        return {"n": 0}
    ba = np.array([float(t["beta_adjusted_pct"]) for t in rows])
    hs = np.array([float(t.get("half_spread_pct") or 0.0) if np.isfinite(t.get("half_spread_pct") or 0.0) else 0.0
                   for t in rows])
    months = np.array([t["signal_date"][:7] for t in rows])
    gross = ba + 2.0 * BASE_COST_PCT_PER_SIDE                 # beta-adjusted return before any cost
    scen = {
        "a_10bps": ba,
        "b_10bps_plus_half_spread": gross - 2.0 * (BASE_COST_PCT_PER_SIDE + hs),
        "c_twice_b": gross - 4.0 * (BASE_COST_PCT_PER_SIDE + hs),
    }
    out: Dict[str, Any] = {"n": int(len(rows)), "mean_half_spread_pct": round(float(hs.mean()), 4)}
    for name, vals in scen.items():
        entry = {"mean_beta_adjusted_pct": round(float(vals.mean()), 4)}
        if ci_fn is not None:
            lo, hi = ci_fn(vals, months)
            entry["ci95"] = None if lo is None else [round(lo, 4), round(hi, 4)]
        out[name] = entry
    # mean(gross) - 2c = 0  ->  c = mean(gross) / 2 (percent per side); negative = loses even at zero cost
    out["break_even_bps_per_side"] = round(float(gross.mean()) / 2.0 * 100.0, 2)
    return out


def half_spread_at(series: Optional[pd.Series], date) -> Optional[float]:
    if series is None:
        return None
    try:
        v = float(series.get(pd.Timestamp(date), np.nan))
    except Exception:
        return None
    return v if np.isfinite(v) else None
