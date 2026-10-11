"""
Signal IC study (profitability program, Phase 3, rule R3)
=========================================================
Monthly rank information coefficients of pre-registered cross-sectional signals on the liquid
universe (price >= $5, 20-day dollar volume >= $5M, >= 252 sessions of history), against the
forward log return minus beta252 x the SPY log return over the same horizon.

Signals (all point-in-time: each value at date t uses bars up to t only):
  MOM12_1   close[t-21] / close[t-252] - 1
  RESMOM    residual momentum: OLS of daily returns on SPY and the stock's sector ETF over the 504
            sessions ending t-21; mean / std of the residuals over t-251..t-21 (Blitz et al.)
  HI52      close / 252-session high
  FIP       frog-in-the-pan information discreteness, used as -ID:
            ID = sign(PRET) x (% negative days - % positive days) over t-251..t-21
  REV21     -(21-session return minus the sector ETF's)
  RVOL252   -(252-session volatility of daily returns)
  C6        equal-weight mean of the six cross-sectional percentile ranks (at least 4 present)
Reported only (never adoptable): MOM63, BETA252, MAX21, ADV20, SECMOM (sector ETF 12-1 momentum).

Pattern features (Phase 5, --patterns): src/patterns.py, evaluated for R4 by their incremental IC
after residualizing on MOM12_1 and HI52 ranks each month.

Monthly non-overlapping evaluation: the last session of each month; decision horizon 20 sessions.
Per signal: mean IC, Newey-West t, both halves, LATE (signals from 2025-04-09), deciles, top-decile
turnover, IC by ADV tier (< $20M, $20-100M, > $100M), and horizons 5/10/20/60.

R3 (verbatim in CLAUDE.md): valid = mean IC > 0 with NW t >= 2.0, positive in both halves and in
LATE. C6 is adopted if valid and its IC >= MOM12_1's (paired monthly diff >= 0); else MOM12_1 if
valid; else nothing.

Outputs: outputs/research/signal_ic_summary.json (+ alphalens tear sheets when installed).
Usage: python scripts/research_signal_ic.py [--universe production|cache_start] [--limit N] [--patterns]
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
os.environ.setdefault("SKIP_NLP", "true")

from src.research.stats import newey_west_t, percentile_of  # noqa: E402,F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("research_signal_ic")

OUT_DIR = os.path.join(PROJECT_ROOT, "outputs", "research")
LATE_FROM = "2025-04-09"
EVAL_START = "2022-10-21"
HORIZONS = (5, 10, 20, 60)
DECISION_HORIZON = 20
MIN_NAMES = 30
ADOPTABLE = ("MOM12_1", "RESMOM", "HI52", "FIP", "REV21", "RVOL252")
REPORT_ONLY = ("MOM63", "BETA252", "MAX21", "ADV20", "SECMOM")
ADV_TIERS = (("lt_20m", 0, 20e6), ("20m_100m", 20e6, 100e6), ("gt_100m", 100e6, float("inf")))
SECTOR_TO_ETF = {
    "Finance": "XLF", "Financials": "XLF", "Consumer Discretionary": "XLY", "Health Care": "XLV",
    "Technology": "XLK", "Industrials": "XLI", "Real Estate": "XLRE", "Energy": "XLE", "Utilities": "XLU",
    "Basic Materials": "XLB", "Materials": "XLB", "Consumer Staples": "XLP",
}


# ----------------------------------------------------------------------------
# Features (wide frames: index = sessions, columns = tickers)
# ----------------------------------------------------------------------------
def _rolling_sum(a: np.ndarray, w: int) -> np.ndarray:
    """Trailing w-row sums along axis 0; NaN until w rows (NaN inputs make the window NaN)."""
    out = np.full(a.shape, np.nan)
    c = np.cumsum(np.nan_to_num(a), axis=0)
    bad = np.cumsum(np.isnan(a).astype(int), axis=0)
    c = np.concatenate([np.zeros((1,) + a.shape[1:]), c], axis=0)
    bad = np.concatenate([np.zeros((1,) + a.shape[1:], dtype=int), bad], axis=0)
    s = c[w:] - c[:-w]
    nb = bad[w:] - bad[:-w]
    s[nb > 0] = np.nan
    out[w - 1:] = s
    return out


def residual_momentum(y: pd.Series, xs: List[pd.Series], reg_window: int = 504, res_window: int = 231,
                      skip: int = 21) -> pd.Series:
    """
    Residual momentum at each date t: coefficients from OLS of y on [1, xs] over the reg_window
    returns ending at t-skip; residual mean / std over the res_window returns ending at t-skip.
    Closed form from trailing sums, so every value uses returns up to t-skip only.
    """
    cols = [np.ones(len(y))] + [x.to_numpy(dtype=float) for x in xs]
    X = np.column_stack(cols)                        # n x k
    yv = y.to_numpy(dtype=float)
    valid = np.isfinite(yv) & np.isfinite(X).all(axis=1)
    X = np.where(valid[:, None], X, np.nan)
    yv = np.where(valid, yv, np.nan)
    k = X.shape[1]
    XX = X[:, :, None] * X[:, None, :]               # n x k x k
    Xy = X * yv[:, None]
    yy = yv * yv
    S_xx, S_xy = _rolling_sum(XX, reg_window), _rolling_sum(Xy, reg_window)
    R_xx, R_xy, R_yy = _rolling_sum(XX, res_window), _rolling_sum(Xy, res_window), _rolling_sum(yy, res_window)
    n = len(yv)
    out = np.full(n, np.nan)
    ok = np.isfinite(S_xx).all(axis=(1, 2)) & np.isfinite(R_xx).all(axis=(1, 2))
    ok &= np.abs(np.linalg.det(np.where(ok[:, None, None], S_xx, np.eye(k)))) > 1e-18
    idx = np.flatnonzero(ok)                          # regression end rows e
    if len(idx):
        b = np.linalg.solve(S_xx[idx], S_xy[idx][..., None])[..., 0]          # m x k
        m = R_xx[idx][:, 0, 0]
        sum_e = R_xy[idx][:, 0] - np.einsum("ij,ij->i", b, R_xx[idx][:, 0, :])
        sum_e2 = (R_yy[idx] - 2 * np.einsum("ij,ij->i", b, R_xy[idx])
                  + np.einsum("ij,ijk,ik->i", b, R_xx[idx], b))
        mean = sum_e / m
        var = sum_e2 / m - mean ** 2
        val = np.where(var > 1e-14, mean / np.sqrt(np.maximum(var, 1e-300)), np.nan)
        t = idx + skip
        keep = t < n
        out[t[keep]] = val[keep]
    return pd.Series(out, index=y.index)


def compute_features(close: pd.DataFrame, high: pd.DataFrame, volume: pd.DataFrame, spy: pd.Series,
                     etf_close: pd.DataFrame, sector_etf: Dict[str, Optional[str]],
                     with_resmom: bool = True) -> Dict[str, pd.DataFrame]:
    """All signal frames. Every rolling window ends at the row's own date (point-in-time)."""
    ret = close.pct_change(fill_method=None)
    spy_r = spy.pct_change(fill_method=None).reindex(close.index)
    etf_r = etf_close.pct_change(fill_method=None).reindex(close.index)
    f: Dict[str, pd.DataFrame] = {}
    f["MOM12_1"] = close.shift(21) / close.shift(252) - 1.0
    f["HI52"] = close / high.rolling(252, min_periods=252).max()
    win = ret.shift(21)
    pos = (win > 0).astype(float).where(win.notna()).rolling(231, min_periods=231).mean()
    neg = (win < 0).astype(float).where(win.notna()).rolling(231, min_periods=231).mean()
    pret = close.shift(21) / close.shift(252) - 1.0
    f["FIP"] = -(np.sign(pret) * (neg - pos))
    sec_ret21 = pd.DataFrame({t: (etf_close[sector_etf[t]] / etf_close[sector_etf[t]].shift(21) - 1.0)
                              if sector_etf.get(t) in etf_close else spy / spy.shift(21) - 1.0
                              for t in close.columns}).reindex(close.index)
    f["REV21"] = -((close / close.shift(21) - 1.0) - sec_ret21)
    f["RVOL252"] = -ret.rolling(252, min_periods=252).std()
    if with_resmom:
        f["RESMOM"] = pd.DataFrame({
            t: residual_momentum(ret[t], [spy_r] + ([etf_r[sector_etf[t]]] if sector_etf.get(t) in etf_r else []))
            for t in close.columns}, index=close.index)
    f["MOM63"] = close / close.shift(63) - 1.0
    cov = ret.rolling(252, min_periods=120).cov(spy_r)
    f["BETA252"] = cov.div(spy_r.rolling(252, min_periods=120).var(), axis=0)
    f["MAX21"] = ret.rolling(21, min_periods=21).max()
    f["ADV20"] = (close * volume).rolling(20, min_periods=20).mean()
    secmom = {e: etf_close[e].shift(21) / etf_close[e].shift(252) - 1.0 for e in etf_close.columns}
    spy_mom = spy.shift(21) / spy.shift(252) - 1.0
    f["SECMOM"] = pd.DataFrame({t: secmom[sector_etf[t]] if sector_etf.get(t) in secmom else spy_mom
                                for t in close.columns}).reindex(close.index)
    return f


def liquid_mask(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    adv = (close * volume).rolling(20, min_periods=20).mean()
    history = close.notna().cumsum()
    return (close >= 5.0) & (adv >= 5e6) & (history >= 252)


def cross_rank(frame: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Percentile rank (0-1] across liquid names per date."""
    return frame.where(mask).rank(axis=1, pct=True)


def composite_c6(feats: Dict[str, pd.DataFrame], mask: pd.DataFrame) -> pd.DataFrame:
    ranks = [cross_rank(feats[k], mask) for k in ADOPTABLE if k in feats]
    stack = np.stack([r.to_numpy() for r in ranks])
    count = np.isfinite(stack).sum(axis=0)
    mean = np.nanmean(np.where(np.isfinite(stack), stack, np.nan), axis=0)
    mean[count < 4] = np.nan
    return pd.DataFrame(mean, index=mask.index, columns=mask.columns)


def targets(close: pd.DataFrame, spy: pd.Series, beta: pd.DataFrame, h: int) -> pd.DataFrame:
    """Forward h-session log return minus beta (known at t) x SPY's forward log return."""
    fwd = np.log(close.shift(-h) / close)
    spy_fwd = np.log(spy.shift(-h) / spy).reindex(close.index)
    return fwd - beta.shift(1).mul(spy_fwd, axis=0)


# ----------------------------------------------------------------------------
# IC statistics
# ----------------------------------------------------------------------------
def month_ends(index: pd.DatetimeIndex, start: str, last_valid: pd.Timestamp) -> List[pd.Timestamp]:
    s = pd.Series(index, index=index)
    ends = s.groupby([index.year, index.month]).max().tolist()
    return [d for d in ends if pd.Timestamp(start) <= d <= last_valid]


def spearman(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < MIN_NAMES:
        return None
    ra = pd.Series(a[ok]).rank().to_numpy()
    rb = pd.Series(b[ok]).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def ic_series(feat: pd.DataFrame, target: pd.DataFrame, mask: pd.DataFrame, dates: List[pd.Timestamp],
              sub: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    out = {}
    for d in dates:
        m = mask.loc[d].to_numpy() & (sub.loc[d].to_numpy() if sub is not None else True)
        v = spearman(np.where(m, feat.loc[d].to_numpy(dtype=float), np.nan), target.loc[d].to_numpy(dtype=float))
        if v is not None:
            out[d.date().isoformat()] = v
    return out


def summarize_ic(ics: Dict[str, float]) -> Dict[str, Any]:
    if len(ics) < 6:
        return {"months": len(ics), "mean_ic": None}
    months = sorted(ics)
    vals = np.array([ics[m] for m in months])
    half = len(vals) // 2
    late = np.array([ics[m] for m in months if m >= LATE_FROM])
    return {
        "months": len(vals),
        "mean_ic": round(float(vals.mean()), 4),
        "nw_t": round(newey_west_t(vals), 2),
        "first_half_mean_ic": round(float(vals[:half].mean()), 4),
        "second_half_mean_ic": round(float(vals[half:].mean()), 4),
        "late_mean_ic": round(float(late.mean()), 4) if len(late) else None,
        "late_months": int(len(late)),
        "share_months_positive": round(float((vals > 0).mean()), 3),
    }


def valid_r3(s: Dict[str, Any]) -> bool:
    return bool(s.get("mean_ic") is not None and s["mean_ic"] > 0 and (s.get("nw_t") or 0) >= 2.0
                and (s.get("first_half_mean_ic") or 0) > 0 and (s.get("second_half_mean_ic") or 0) > 0
                and (s.get("late_mean_ic") or 0) > 0)


def deciles(feat: pd.DataFrame, target: pd.DataFrame, mask: pd.DataFrame, dates) -> Dict[str, Any]:
    by_dec = [[] for _ in range(10)]
    spreads, tops = [], []
    for d in dates:
        m = mask.loc[d] & feat.loc[d].notna() & target.loc[d].notna()
        if m.sum() < 50:
            continue
        x, y = feat.loc[d][m], target.loc[d][m]
        q = pd.qcut(x.rank(method="first"), 10, labels=False)
        means = y.groupby(q).mean()
        for k in range(10):
            by_dec[k].append(float(means.get(k, np.nan)))
        spreads.append(float(means.get(9, np.nan) - means.get(0, np.nan)))
        tops.append(set(x.index[q == 9]))
    turnover = [1 - len(a & b) / max(1, len(b)) for a, b in zip(tops[:-1], tops[1:])]
    sp = np.array([s for s in spreads if np.isfinite(s)])
    return {
        "mean_target_by_decile_pct": [round(100 * float(np.nanmean(v)), 3) if v else None for v in by_dec],
        "top_minus_bottom_pct": round(100 * float(sp.mean()), 3) if len(sp) else None,
        "top_minus_bottom_nw_t": round(newey_west_t(sp), 2) if len(sp) > 3 else None,
        "top_decile_monthly_turnover": round(float(np.mean(turnover)), 3) if turnover else None,
    }


def paired_monthly_diff(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, Any]:
    common = sorted(set(a) & set(b))
    if len(common) < 6:
        return {"months": len(common), "diff": None}
    d = np.array([a[m] - b[m] for m in common])
    rng = np.random.default_rng(31)
    boots = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"months": len(common), "diff": round(float(d.mean()), 4), "nw_t": round(newey_west_t(d), 2),
            "ci95": [round(float(lo), 4), round(float(hi), 4)]}


def residualize(feat: pd.DataFrame, controls: List[pd.DataFrame], mask: pd.DataFrame, dates) -> pd.DataFrame:
    """Per date: rank(feature) minus its OLS fit on the ranks of the controls (liquid names)."""
    out = pd.DataFrame(np.nan, index=feat.index, columns=feat.columns)
    for d in dates:
        y = feat.loc[d].where(mask.loc[d]).rank(pct=True)
        X = pd.concat([c.loc[d].where(mask.loc[d]).rank(pct=True) for c in controls], axis=1)
        ok = y.notna() & X.notna().all(axis=1)
        if ok.sum() < MIN_NAMES:
            continue
        A = np.column_stack([np.ones(ok.sum()), X[ok].to_numpy()])
        b, *_ = np.linalg.lstsq(A, y[ok].to_numpy(), rcond=None)
        out.loc[d, ok[ok].index] = y[ok].to_numpy() - A @ b
    return out


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def load_panel(universe: str, limit: Optional[int]):
    from scripts.validate_backtest_pipeline import start_universe, production_universe, load_history
    stocks, etfs = production_universe() if universe == "production" else start_universe()
    if limit:
        stocks = stocks[:limit]
    etf_list = sorted(set(etfs) | set(SECTOR_TO_ETF.values()))
    raw = load_history(stocks + etf_list + ["SPY"])
    spy = raw["SPY"]["CLOSE"].sort_index()
    sectors: Dict[str, Optional[str]] = {}
    try:
        with open(os.path.join(PROJECT_ROOT, "data", "cache", "universe", "us_equities_master.json"), encoding="utf-8") as f:
            master = {s["ticker"]: s.get("sector") for s in json.load(f)["securities"]}
    except Exception:
        master = {}
    def frame(col, names):
        return pd.DataFrame({t: raw[t][col] for t in names if t in raw}).sort_index().reindex(spy.index)
    names = [t for t in stocks if t in raw and len(raw[t]) > 260]
    for t in names:
        etf = SECTOR_TO_ETF.get(master.get(t) or master.get(t.replace("-", ".")) or "")
        sectors[t] = etf if etf in raw else None
    close, high, low, volume = frame("CLOSE", names), frame("HIGH", names), frame("LOW", names), frame("VOLUME", names)
    etf_close = frame("CLOSE", [e for e in etf_list if e in raw])
    logger.info("Panel: %d stocks, %d sessions, %d sector ETFs; %d stocks mapped to a sector ETF",
                close.shape[1], close.shape[0], etf_close.shape[1], sum(1 for v in sectors.values() if v))
    return close, high, low, volume, spy, etf_close, sectors


# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=("cache_start", "production"), default="production")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--patterns", action="store_true", help="Also evaluate the Phase 5 pattern features (R4)")
    ap.add_argument("--no-alphalens", action="store_true")
    ap.add_argument("--no-trials", action="store_true", help="Do not write the trials registry (re-runs for artifacts)")
    args = ap.parse_args()
    t0 = time.time()
    close, high, low, volume, spy, etf_close, sectors = load_panel(args.universe, args.limit)
    feats = compute_features(close, high, volume, spy, etf_close, sectors)
    mask = liquid_mask(close, volume)
    feats["C6"] = composite_c6(feats, mask)
    logger.info("Features computed (%.0fs)", time.time() - t0)
    if args.patterns:
        from src.patterns import pattern_feature_panel
        from src.patterns import FAILED_TICKERS
        feats.update(pattern_feature_panel(close, high, low, volume))
        if FAILED_TICKERS:
            logger.warning("Pattern features failed for %d tickers (left missing)", len(FAILED_TICKERS))
        logger.info("Pattern features computed (%.0fs)", time.time() - t0)

    beta = feats["BETA252"]
    result: Dict[str, Any] = {"metadata": {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "universe": args.universe, "limit": args.limit,
        "stocks": int(close.shape[1]), "window": [EVAL_START, close.index[-1].date().isoformat()],
        "late_from": LATE_FROM, "decision_horizon": DECISION_HORIZON,
        "target": "forward log return minus beta252 x SPY forward log return",
        "liquid_universe": "price >= $5, ADV20 >= $5M, >= 252 sessions",
        "survivorship": "today's security master applied to the whole window (delisted stocks missing)"
                        if args.universe == "production" else "2025-02-13 cache constituents",
    }, "signals": {}}
    ics_by_feat: Dict[str, Dict[int, Dict[str, float]]] = {}
    tgt = {h: targets(close, spy, beta, h) for h in HORIZONS}
    adv = feats["ADV20"]
    for name, frame in feats.items():
        entry: Dict[str, Any] = {"adoptable": name in ADOPTABLE or name == "C6", "horizons": {}}
        ics_by_feat[name] = {}
        for h in HORIZONS:
            last_valid = close.index[-h - 1]
            dates = month_ends(close.index, EVAL_START, last_valid)
            ics = ic_series(frame, tgt[h], mask, dates)
            ics_by_feat[name][h] = ics
            entry["horizons"][str(h)] = summarize_ic(ics)
            if h == DECISION_HORIZON:
                entry.update(entry["horizons"][str(h)])
                entry["deciles"] = deciles(frame, tgt[h], mask, dates)
                entry["ic_by_adv_tier"] = {
                    tier: summarize_ic(ic_series(frame, tgt[h], mask, dates, sub=(adv >= lo) & (adv < hi)))
                    for tier, lo, hi in ADV_TIERS}
                entry["monthly_ic"] = {k: round(v, 4) for k, v in ics.items()}
                entry["valid_r3"] = valid_r3(entry) if entry["adoptable"] else None
        result["signals"][name] = entry
        logger.info("%-10s IC20 %s t %s halves %s/%s late %s", name, entry.get("mean_ic"), entry.get("nw_t"),
                    entry.get("first_half_mean_ic"), entry.get("second_half_mean_ic"), entry.get("late_mean_ic"))

    if args.patterns:
        from src.patterns import FAILED_TICKERS
        result["metadata"]["pattern_feature_failures"] = {"count": len(FAILED_TICKERS),
                                                          "sample": dict(list(FAILED_TICKERS.items())[:10])}

    # R3 decision
    c6, mom = result["signals"]["C6"], result["signals"]["MOM12_1"]
    pd_c6 = paired_monthly_diff(ics_by_feat["C6"][DECISION_HORIZON], ics_by_feat["MOM12_1"][DECISION_HORIZON])
    if c6.get("valid_r3") and pd_c6.get("diff") is not None and pd_c6["diff"] >= 0:
        adopted, why = "C6", "C6 valid and its IC >= MOM12_1's (paired monthly diff >= 0)"
    elif mom.get("valid_r3"):
        adopted, why = "MOM12_1", "C6 not adopted; MOM12_1 valid"
    else:
        adopted, why = None, "neither C6 nor MOM12_1 is valid"
    result["r3"] = {"adopted": adopted, "reason": why, "c6_minus_mom12_1": pd_c6,
                    "valid": {k: v.get("valid_r3") for k, v in result["signals"].items() if v["adoptable"]},
                    "skip_phases_4_and_7": not (c6.get("valid_r3") or mom.get("valid_r3"))}

    # R4: incremental IC of pattern features (residualized on MOM12_1 and HI52 ranks)
    if args.patterns:
        from src.patterns import PATTERN_FEATURES
        dates = month_ends(close.index, EVAL_START, close.index[-DECISION_HORIZON - 1])
        r4 = {}
        for name in PATTERN_FEATURES:
            if name not in feats:
                continue
            res = residualize(feats[name], [feats["MOM12_1"], feats["HI52"]], mask, dates)
            s = summarize_ic(ic_series(res, tgt[DECISION_HORIZON], mask, dates))
            s["keep_r4"] = bool(s.get("mean_ic") is not None and (s.get("nw_t") or 0) >= 3.0
                                and (s.get("first_half_mean_ic") or 0) > 0 and (s.get("second_half_mean_ic") or 0) > 0)
            r4[name] = s
        result["r4_incremental_ic"] = r4

    # Trials registry (one record per adoptable signal; IC in place of beta-adjusted return)
    from src.research.trials import record_trial
    for name, e in ([] if args.no_trials else result["signals"].items()):
        if not e["adoptable"] and not name.startswith("PAT_"):
            continue
        record_trial(name=name, phase="phase5" if name in result.get("r4_incremental_ic", {}) else "phase3",
                     params={"horizon": DECISION_HORIZON, "universe": args.universe, "limit": args.limit},
                     n_ideas=e.get("months") or 0, mean_beta_adj=None, ci=None, late_mean=None,
                     series_path="outputs/research/signal_ic_summary.json", smoke=bool(args.limit),
                     extra={"metric": "monthly rank IC", "mean_ic": e.get("mean_ic"), "nw_t": e.get("nw_t"),
                            "late_mean_ic": e.get("late_mean_ic"), "valid_r3": e.get("valid_r3"),
                            "r4": result.get("r4_incremental_ic", {}).get(name)})

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "signal_ic_summary.json" if not args.limit else "signal_ic_summary_smoke.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info("R3: %s (%s). Wrote %s (%.0fs)", adopted, why, out_path, time.time() - t0)

    if not args.no_alphalens:
        try:
            alphalens_sheets(close, feats, mask)
        except Exception as e:  # research extra: never fails the study
            logger.warning("alphalens tear sheets skipped: %s", e)
    return 0


def alphalens_sheets(close: pd.DataFrame, feats: Dict[str, pd.DataFrame], mask: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import alphalens as al
    dates = month_ends(close.index, EVAL_START, close.index[-DECISION_HORIZON - 1])
    # alphalens infers a frequency from the price index: give it the session calendar explicitly
    weekdays = pd.bdate_range(close.index[0], close.index[-1])
    cbd = pd.offsets.CustomBusinessDay(holidays=weekdays.difference(close.index))
    close = close.copy()
    close.index = pd.DatetimeIndex(close.index, freq=cbd)
    for name in list(ADOPTABLE) + ["C6"]:
        # Daily factor values (alphalens requires factor dates on the price calendar); the R3
        # decision itself uses the monthly non-overlapping ICs above.
        f = feats[name].where(mask).loc[dates[0]:dates[-1]].stack()
        f.index = f.index.set_names(["date", "asset"])
        f = f.reindex(pd.MultiIndex.from_arrays([pd.DatetimeIndex(f.index.get_level_values(0), freq=None),
                                                 f.index.get_level_values(1)], names=["date", "asset"]))
        data = al.utils.get_clean_factor_and_forward_returns(f, close, periods=(5, 10, 20), quantiles=10, max_loss=0.6)
        al.tears.create_summary_tear_sheet(data)
        for i, num in enumerate(plt.get_fignums()):
            plt.figure(num).savefig(os.path.join(OUT_DIR, f"alphalens_{name}_{i}.png"), dpi=80)
        plt.close("all")
        al.performance.factor_information_coefficient(data).to_csv(os.path.join(OUT_DIR, f"alphalens_{name}_ic.csv"))
        logger.info("alphalens tear sheet written for %s", name)


if __name__ == "__main__":
    sys.exit(main())
