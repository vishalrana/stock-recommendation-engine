"""
Production-Pipeline Backtest
============================
Replays the nightly recommendation pipeline day by day over ~5 years of history and measures
the trades it would have produced, using the SAME code paths as jobs/generate_signals.py:

  * Regime:       SPY vs its 200 DMA with the +/-2% sideways band (src.regime.classify_regime)
                  and the production REGIME_STRATEGY_MAP strategy activation.
  * Universe:     the tickers present on the FIRST day of the local price cache, stocks and
                  sector ETFs separated as in production, with the production point-in-time
                  liquidity filter and the cross-sectional top-15% 63-day momentum screen. The cache
                  starts later than the evaluation window (2025-02-13 vs 2022-10-21 in the 2026-10
                  runs), so BEFORE the universe date the universe is chosen with hindsight (survivors
                  only). The late period (after the split) starts after it and is the cleaner test.
  * Signals:      the production strategy classes, on point-in-time data only.
  * Scoring:      src.pipeline_steps (momentum, strategy evidence, regime, composite >= 65).
                  Strategy evidence (win rate / expectancy / target-hit rates) is measured on ALL of
                  a strategy's setups ("shadow trades": every signal passing the strategy's rules,
                  one open per strategy+ticker), not only the subset that later scored >= 65 —
                  otherwise strategies whose score leans on evidence could never earn any. It is
                  walk-forward: only shadow trades that closed BEFORE the signal date count.
  * Trade plan:   the strategy's own stop (no clamp), canonical targets, reach probabilities
                  (setup-conditional from walk-forward evidence once >= 30 trades), entry-location gate.
  * One active idea per ticker, exactly like production.
  * Execution:    fill at the next bar's open (no gap filter, same as the live lifecycle);
                  exits replayed with PositionScaleOutTracker, STOP_FIRST, ratcheted stops,
                  target fills at the open on gaps, expiry at the strategy holding period;
                  10 bps per side transaction cost. Trades whose data ends first are censored.

Statistics are honest about dependence: trades overlap in time and are cross-sectionally
correlated, so confidence intervals come from a block bootstrap that resamples whole signal
months, not from formulas that assume independent trades. There is no per-trade "Sharpe".

Known limitations (reported in the output):
  * Survivorship: before the universe date the universe holds only stocks that survived to it,
    and tickers that delisted afterwards have no downloadable history.
  * No historical earnings calendar: the earnings blackout cannot be applied and PEAD
    (which needs past earnings dates) cannot fire.
  * No historical news/analyst context: context is "unavailable" and excluded from the score
    with renormalized weights, as production does when context is missing.
  * No VIX history in the cache: the VIX>40 emergency override is not applied.
  * Thresholds were tuned by hand before this harness existed, so even the later "test"
    period is not a clean out-of-sample period for those choices.

Selection variants (--variants): the same pass can evaluate other selection settings
(src.pipeline_steps.SelectionSettings: momentum model, entry-location mode, context components)
against identical setups, evidence and execution. Each variant keeps its own book (one active idea
per ticker); the production settings always run first and alone produce the standard outputs.
"fundamentals_in_score" scores the context from point-in-time SEC fundamentals (facts filed
before the signal date; the only context with history).

Outputs:
  outputs/production_backtest_summary.json   statistics
  outputs/production_backtest_trades.csv     every simulated trade
  outputs/strategy_performance_<universe>.json  per-strategy evidence of this run. Production scoring reads
                                                config/strategy_performance.json, which is adopted only from a
                                                production-universe run (scripts/adopt_strategy_evidence.py)
  outputs/production_backtest_variants.json  variant statistics and paired differences (with --variants)

Usage:
  python scripts/validate_backtest_pipeline.py [--refresh-data] [--workers 4] [--variants all]
"""

import argparse
import bisect
import glob
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("SKIP_NLP", "true")

from src.indicators import calculate_indicators
from src.regime import classify_regime
from src.quant_config import (
    normalize_strategy_key,
    US_UNIVERSE_MIN_PRICE,
    US_UNIVERSE_MIN_DOLLAR_VOLUME,
    US_UNIVERSE_MIN_HISTORY_DAYS,
    US_UNIVERSE_DOLLAR_VOLUME_WINDOW,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("validate_backtest_pipeline")

CACHE_BY_DATE = os.path.join(PROJECT_ROOT, "data", "cache", "by_date")
BACKTEST_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "backtest_history")
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")

HISTORY_PERIOD = "5y"
SCAN_WINDOW_BARS = 260          # >= 252 needed by the 52-week strategy
WARMUP_BARS = 260
TRANSACTION_COST_PCT_PER_SIDE = 0.10
TRAIN_FRACTION = 0.60
EMBARGO_DAYS = 20
BOOTSTRAP_SAMPLES = 2000
CROSS_SECTIONAL_TOP_FRACTION = 0.15
SEC_FACTS_DIR = os.path.join(BACKTEST_DATA_DIR, "sec")

# Selection variants evaluated against the production settings (--variants)
VARIANT_PRESETS: Dict[str, Dict[str, Any]] = {
    "rs_momentum": {"momentum_model": "relative_strength_12m"},
    "entry_info": {"entry_location_mode": "info"},
    "rs_momentum_entry_info": {"momentum_model": "relative_strength_12m", "entry_location_mode": "info"},
    "fundamentals_in_score": {"context_components": ("analyst", "fundamental", "news")},
}

W52 = "52-Week High"


def _w52_features(frame: pd.DataFrame, loc: int) -> Dict[str, Any]:
    """The 52-Week High strategy's own inputs at bar `loc` (same windows as its scan)."""
    close = frame["CLOSE"].to_numpy()
    high = frame["HIGH"].to_numpy()
    vol = frame["VOLUME"].to_numpy()
    px = close[loc]
    vavg = vol[loc - 19: loc + 1].mean()
    return {
        "pct_vs_52w": (px / high[loc - 251: loc + 1].max() - 1.0) * 100.0,
        "pct_vs_20h": (px / high[loc - 19: loc + 1].max() - 1.0) * 100.0,
        "volume_ratio": vol[loc] / vavg if vavg > 0 else 0.0,
        "rsi": float(frame["RSI_14"].iat[loc]),
        "adx": float(frame["ADX_14"].iat[loc]),
        "new_52w_close": bool(px > close[loc - 251: loc].max()),
    }


def w52_original_rules(sig: Dict[str, Any], frame: pd.DataFrame) -> bool:
    """The strategy's pre-relaxation thresholds, as documented in jobs/strategies/week_52_high.py."""
    f = _w52_features(frame, sig["_loc"])
    return (f["pct_vs_52w"] >= -2.0 and 55.0 <= f["rsi"] <= 75.0 and f["adx"] >= 20.0
            and f["volume_ratio"] >= 1.2 and f["pct_vs_20h"] >= -3.0)


def w52_breakout(sig: Dict[str, Any], frame: pd.DataFrame) -> bool:
    """A true breakout: the close is above every close of the prior 251 sessions, on >= 1.5x average volume."""
    f = _w52_features(frame, sig["_loc"])
    return f["new_52w_close"] and f["volume_ratio"] >= 1.5


# Strategy variants (--variants): production selection settings, but only the candidates of
# `strategy` that pass `keep` are eligible for issue (keep=None drops the strategy). Screening
# only: the strategy's walk-forward evidence still comes from all of its setups, so a variant
# that wins must be implemented in the strategy and re-validated with a full run. The w52 variants
# need 52-Week High active in REGIME_STRATEGY_MAP (switched off since 2026-10-10); otherwise no-ops.
STRATEGY_VARIANTS: Dict[str, Tuple[str, Any]] = {
    "w52_off": (W52, None),
    "w52_original_rules": (W52, w52_original_rules),
    "w52_breakout": (W52, w52_breakout),
}


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def start_universe() -> Tuple[List[str], List[str]]:
    """Tickers in the earliest daily cache file (fixed at the start date), split stocks / sector ETFs."""
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    files = sorted(glob.glob(os.path.join(CACHE_BY_DATE, "*.parquet")))
    if not files:
        raise RuntimeError("No daily cache files found in data/cache/by_date.")
    first = pd.read_parquet(files[0])
    tickers = sorted(set(first.index.get_level_values("Ticker")))
    etfs = sorted(SECTOR_ETFS.keys())
    stocks = [t for t in tickers if t not in SECTOR_ETFS]
    logger.info("Start-date universe (%s): %d stocks, %d sector ETFs", os.path.basename(files[0])[:10], len(stocks), len(etfs))
    return stocks, etfs


def production_universe() -> Tuple[List[str], List[str]]:
    """
    The universe the nightly scan uses today (broad US common equities), split stocks / sector ETFs.
    Fails rather than silently testing the benchmark list when the security master is unavailable.
    Survivorship: today's list, applied to the whole window (see the limitations in the output).
    """
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    import jobs.generate_signals as gs
    tickers, _, _ = gs.load_universe("expanded")
    if gs.LAST_UNIVERSE_IS_FALLBACK:
        raise RuntimeError("Production security master unavailable (fell back to the benchmark list).")
    stocks = sorted({t.upper() for t in tickers if t.upper() not in SECTOR_ETFS})
    etfs = sorted(SECTOR_ETFS.keys())
    logger.info("Production universe (today's security master): %d stocks, %d sector ETFs", len(stocks), len(etfs))
    return stocks, etfs


def load_history(tickers: List[str], refresh: bool = False) -> Dict[str, pd.DataFrame]:
    """Daily adjusted OHLCV per ticker, downloaded once and stored under data/backtest_history/."""
    import yfinance as yf
    os.makedirs(BACKTEST_DATA_DIR, exist_ok=True)
    out: Dict[str, pd.DataFrame] = {}
    missing = []
    for t in tickers:
        path = os.path.join(BACKTEST_DATA_DIR, f"{t}.parquet")
        if os.path.exists(path) and not refresh:
            out[t] = pd.read_parquet(path)
        else:
            missing.append(t)
    for i in range(0, len(missing), 100):
        chunk = missing[i:i + 100]
        logger.info("Downloading %s history for %d tickers (%d/%d)...", HISTORY_PERIOD, len(chunk), i + len(chunk), len(missing))
        raw = yf.download(chunk, period=HISTORY_PERIOD, group_by="ticker", auto_adjust=True, progress=False, threads=True)
        for t in chunk:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df.dropna(how="all")
                if df.empty:
                    continue
                df.columns = [str(c).upper() for c in df.columns]
                df = df[["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]]
                df.index = pd.to_datetime(df.index).tz_localize(None)
                df.to_parquet(os.path.join(BACKTEST_DATA_DIR, f"{t}.parquet"))
                out[t] = df
            except Exception as e:
                logger.debug("No data for %s: %s", t, e)
    # Today's bar may still be forming (market open); only completed sessions are used.
    today_ny = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    return {t: df[df.index < today_ny] for t, df in out.items()}


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Production preprocessing: drop bars with missing OHLC, causal indicators, point-in-time liquidity."""
    df = df.dropna(subset=["HIGH", "LOW", "CLOSE"]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    ind = calculate_indicators(df)
    dollar_vol = (ind["CLOSE"] * ind["VOLUME"]).rolling(US_UNIVERSE_DOLLAR_VOLUME_WINDOW).mean()
    ind["_LIQUID"] = (
        (np.arange(len(ind)) + 1 >= US_UNIVERSE_MIN_HISTORY_DAYS)
        & (ind["CLOSE"] >= US_UNIVERSE_MIN_PRICE)
        & (dollar_vol >= US_UNIVERSE_MIN_DOLLAR_VOLUME)
    )
    return ind


# ----------------------------------------------------------------------------
# Phase 1: point-in-time strategy scans (parallel by ticker)
# ----------------------------------------------------------------------------
def _stub_live_only_lookups():
    """Earnings calendars are live-only (no history): disable them so no current data leaks in."""
    import jobs.strategies.pullback as pullback_mod
    import jobs.strategies.pead as pead_mod
    pullback_mod.get_earnings_date = lambda *a, **k: None
    pead_mod.get_last_earnings_date = lambda *a, **k: None


def scan_ticker(args) -> List[Dict[str, Any]]:
    ticker, frame, date_regimes, cs_eligible_dates, is_etf = args
    _stub_live_only_lookups()
    from jobs.strategies import STRATEGIES
    from jobs.generate_signals import REGIME_STRATEGY_MAP
    from src.utils.metrics_pipeline import build_hardened_metrics

    metrics = build_hardened_metrics(ticker=ticker, raw_record=None)
    metrics["company_name"] = ticker
    metrics["industry"] = "Unknown"

    out: List[Dict[str, Any]] = []
    index = frame.index
    liquid = frame["_LIQUID"].to_numpy()
    atr = frame["ATR_14"].to_numpy()
    for loc in range(WARMUP_BARS, len(frame)):
        date = index[loc]
        regime = date_regimes.get(date)
        if regime is None:
            continue
        if not is_etf and not liquid[loc]:
            continue
        allowed = REGIME_STRATEGY_MAP.get(regime, [])
        window = frame.iloc[loc - SCAN_WINDOW_BARS + 1: loc + 1]
        for strat in STRATEGIES:
            if strat.name not in allowed:
                continue
            if (strat.name == "Sector Rotation") != is_etf:
                continue
            if strat.name == "Cross-Sectional Momentum" and date not in cs_eligible_dates:
                continue
            try:
                sig = strat.scan(ticker, window, regime, metrics)
            except Exception:
                sig = None
            if not sig:
                continue
            sig["atr_14"] = float(atr[loc]) if np.isfinite(atr[loc]) else 0.0
            sig["_loc"] = loc
            sig["_date"] = date
            sig["regime"] = regime
            out.append(sig)
    return out


def _phase1_cache_key(frames: Dict[str, pd.DataFrame], limit: Optional[int]) -> str:
    """Hash of strategy/indicator source code and the data extent, so cached scans never go stale."""
    import hashlib
    import inspect
    h = hashlib.sha256()
    sources = sorted(glob.glob(os.path.join(PROJECT_ROOT, "jobs", "strategies", "*.py")))
    sources += [os.path.join(PROJECT_ROOT, "src", "indicators.py"), os.path.join(PROJECT_ROOT, "src", "regime.py")]
    for p in sources:
        with open(p, "rb") as f:
            h.update(f.read())
    # Phase-1 code in this script (later phases do not affect the cached scans)
    for fn in (scan_ticker, _stub_live_only_lookups, prepare_frame):
        h.update(inspect.getsource(fn).encode())
    h.update(f"{SCAN_WINDOW_BARS}:{WARMUP_BARS}:{CROSS_SECTIONAL_TOP_FRACTION}".encode())
    # Which strategies run in which regime (scan_ticker reads it), e.g. a strategy switched off
    from jobs.generate_signals import REGIME_STRATEGY_MAP
    h.update(json.dumps(REGIME_STRATEGY_MAP, sort_keys=True).encode())
    h.update(str(limit).encode())
    for t in sorted(frames):
        h.update(f"{t}:{len(frames[t])}:{frames[t].index[-1]}".encode())
    return h.hexdigest()[:16]


def simulate(sig: Dict[str, Any], frame: pd.DataFrame, loc: int, evidence, d_str: str, build_trade_plan,
             evaluate_signal_outcome, holding_days_for, policy) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Trade plan + D+1-open execution for one candidate. Returns (status, result)."""
    pit = frame.iloc[: loc + 1]
    sig["industry"] = None  # no delisted-sector lookups (no point-in-time sector registry)
    calc = build_trade_plan(sig, pit, evidence, as_of_date=d_str)
    if not calc.is_valid:
        return "invalid_plan", None
    fwd = frame.iloc[loc + 1:]
    if fwd.empty or not np.isfinite(fwd["OPEN"].iloc[0]) or fwd["OPEN"].iloc[0] <= 0:
        return "censored", None
    fill = float(fwd["OPEN"].iloc[0])
    res = evaluate_signal_outcome(
        fwd, fill, float(sig["stop_loss"]), sig["target_1"], sig.get("target_2"), sig.get("target_3"),
        max_holding_days=holding_days_for(sig["strategy"]) or 20, ambiguity_policy=policy,
    )
    if res is None:
        return "censored", None
    net = float(res["realized_return_pct"]) - 2.0 * TRANSACTION_COST_PCT_PER_SIDE
    return "closed", {
        "signal_date": d_str,
        "fill_date": fwd.index[0].date().isoformat(),
        "exit_date": res["outcome_date"],
        "ticker": sig["ticker"].upper(),
        "strategy": sig["strategy"],
        "regime": sig["regime"],
        "composite_score": round(float(sig.get("composite_score") or 0.0), 2),
        "entry_signal_close": float(sig["entry_price"]),
        "fill_price": round(fill, 4),
        "stop_loss": float(sig["stop_loss"]),
        "stop_pct": round((float(sig["entry_price"]) - float(sig["stop_loss"])) / float(sig["entry_price"]) * 100, 2),
        "target_1": sig["target_1"], "target_2": sig.get("target_2"), "target_3": sig.get("target_3"),
        "has_t2": sig.get("target_2") is not None, "has_t3": sig.get("target_3") is not None,
        "reach_prob_source": sig.get("reach_prob_source"),
        "outcome": res["outcome"],
        "exit_reason": res["exit_reason"],
        "holding_days": res["holding_days"],
        "raw_return_pct": round(float(res["realized_return_pct"]), 4),
        "net_return_pct": round(net, 4),
        "adv20_usd": round(float((frame["CLOSE"].iloc[max(0, loc - 19): loc + 1]
                                  * frame["VOLUME"].iloc[max(0, loc - 19): loc + 1]).mean()), 0),
    }


# ----------------------------------------------------------------------------
# Selection-variant inputs: relative strength and point-in-time SEC fundamentals
# ----------------------------------------------------------------------------
def attach_relative_strength(candidates: List[Dict[str, Any]], frames: Dict[str, pd.DataFrame], etfs) -> None:
    """12-1 month relative-strength percentile on each candidate's date (stocks among liquid stocks, ETFs among ETFs)."""
    from src.pipeline_steps import relative_strength_percentiles
    stock_t = [t for t in frames if t not in etfs]
    etf_t = [t for t in frames if t in etfs]
    rs_stock = relative_strength_percentiles(
        pd.DataFrame({t: frames[t]["CLOSE"] for t in stock_t}),
        eligible=pd.DataFrame({t: frames[t]["_LIQUID"] for t in stock_t}),
    )
    rs_etf = relative_strength_percentiles(pd.DataFrame({t: frames[t]["CLOSE"] for t in etf_t}))
    for c in candidates:
        t = c["ticker"].upper()
        table = rs_etf if t in etfs else rs_stock
        try:
            v = float(table.at[c["_date"], t])
        except KeyError:
            v = float("nan")
        c["rs_percentile_12m"] = v if np.isfinite(v) else None


def load_sec_point_in_time(tickers: List[str]) -> Dict[str, Tuple[List[str], List[Tuple[Any, Any, Any]]]]:
    """
    Per ticker: the filing dates and the (D/E, current ratio, balance-sheet date) that production's
    SEC provider would have computed right after each of them, from only the facts filed on or
    before that date. Company facts are cached in data/backtest_history/sec/; the extracted table is
    cached by extraction code and universe.
    """
    import hashlib
    import inspect
    import src.providers.context.sec_fundamentals as secmod
    h = hashlib.sha256(inspect.getsource(secmod).encode())
    h.update(",".join(sorted(tickers)).encode())
    path = os.path.join(BACKTEST_DATA_DIR, f"_sec_pit_{h.hexdigest()[:16]}.parquet")
    if os.path.exists(path):
        df = pd.read_parquet(path)
    else:
        sec = secmod.SecFundamentalsProvider(raw_facts_dir=SEC_FACTS_DIR)
        if not sec.available:
            raise RuntimeError("SEC_USER_AGENT (with a contact email) is required to download SEC company facts.")
        rows = []
        for i, t in enumerate(tickers, 1):
            cik = sec.cik_for(t)
            facts = sec.company_facts(cik) if cik else None
            gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
            filed = sorted({
                f["filed"] for tag in gaap.values() for unit in tag.get("units", {}).values() for f in unit
                if f.get("form") in secmod.REPORT_FORMS and f.get("filed")
            })
            for fd in filed:
                day = date.fromisoformat(fd)
                fund = secmod.extract_fundamentals(facts, as_of=day, reference_date=day)
                rows.append((t, fd, fund.debt_to_equity, fund.current_ratio, fund.balance_sheet_date))
            if i % 50 == 0:
                logger.info("SEC point-in-time fundamentals: %d/%d tickers", i, len(tickers))
        df = pd.DataFrame(rows, columns=["ticker", "filed", "de", "cr", "bs_date"])
        df.to_parquet(path)
    table = {}
    for t, g in df.sort_values(["ticker", "filed"]).groupby("ticker"):
        bs = [b if isinstance(b, str) and b else None for b in g["bs_date"]]  # parquet turns None into NaN
        table[t] = (g["filed"].tolist(), list(zip(g["de"], g["cr"], bs)))
    return table


def sec_fundamentals_on(table, ticker: str, d_str: str) -> Tuple[Optional[float], Optional[float]]:
    """(D/E, current ratio) known before `d_str` from filings strictly before it, if the balance sheet is fresh."""
    from src.providers.context.sec_fundamentals import MAX_BALANCE_AGE_DAYS
    entry = table.get(ticker)
    if not entry:
        return None, None
    filed, vals = entry
    i = bisect.bisect_left(filed, d_str) - 1
    if i < 0:
        return None, None
    de, cr, bs = vals[i]
    if not isinstance(bs, str) or (date.fromisoformat(d_str) - date.fromisoformat(bs)).days > MAX_BALANCE_AGE_DAYS:
        return None, None
    fin = lambda v: float(v) if v is not None and np.isfinite(v) else None
    return fin(de), fin(cr)


def apply_backtest_context(sig, settings, sec_table, scorer, d_str, mark_unavailable) -> None:
    """Context as production would score it; only SEC fundamentals have history (analyst / news never)."""
    mark_unavailable(sig)
    if sec_table is None or "fundamental" not in settings.context_components:
        return
    from src.providers.base import AggregatedContext, DataQuality, FundamentalContext
    de, cr = sec_fundamentals_on(sec_table, sig["ticker"].upper(), d_str)
    if de is None and cr is None:
        return
    ctx = AggregatedContext(fundamental=FundamentalContext(debt_to_equity=de, current_ratio=cr, quality=DataQuality.VALID))
    total, _, fund_pts, _, avail = scorer.calculate_with_components(ctx, float(sig["entry_price"]), settings.context_components)
    if total is None:
        return
    sig.update(context_available=True, context_score=total, context_fundamental=fund_pts,
               context_max_points=avail, de_ratio=de, current_ratio=cr)


# ----------------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------------
def paired_block_diff(a: List[Dict[str, Any]], b: List[Dict[str, Any]], key: str,
                      n_boot: int = BOOTSTRAP_SAMPLES, seed: int = 11) -> Dict[str, Any]:
    """mean(a) - mean(b) of `key`, with a 95% CI resampling the same signal months for both books."""
    va = [(t["signal_date"][:7], t[key]) for t in a if t.get(key) is not None]
    vb = [(t["signal_date"][:7], t[key]) for t in b if t.get(key) is not None]
    if len(va) < 30 or len(vb) < 30:
        return {"diff": None, "ci95": None}
    months = sorted({m for m, _ in va} | {m for m, _ in vb})
    ga = {m: np.array([v for mm, v in va if mm == m]) for m in months}
    gb = {m: np.array([v for mm, v in vb if mm == m]) for m in months}
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        pick = rng.choice(months, len(months))
        x = np.concatenate([ga[m] for m in pick])
        y = np.concatenate([gb[m] for m in pick])
        if len(x) and len(y):
            diffs.append(x.mean() - y.mean())
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    point = float(np.mean([v for _, v in va]) - np.mean([v for _, v in vb]))
    return {"diff": round(point, 3), "ci95": [round(float(lo), 3), round(float(hi), 3)]}


def block_bootstrap_ci(values: np.ndarray, clusters: np.ndarray, stat, n_boot: int = BOOTSTRAP_SAMPLES, seed: int = 7):
    """95% CI of stat(values) resampling whole clusters (signal months) with replacement."""
    uniq = np.unique(clusters)
    if len(uniq) < 2:
        return None, None
    groups = [values[clusters == c] for c in uniq]
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        sample = np.concatenate([groups[i] for i in pick])
        if len(sample):
            stats.append(stat(sample))
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def compile_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trade_count": 0}
    net = np.array([t["net_return_pct"] for t in trades], dtype=float)
    months = np.array([t["signal_date"][:7] for t in trades])
    wins = net > 0
    exp_lo, exp_hi = block_bootstrap_ci(net, months, np.mean)
    wr_lo, wr_hi = block_bootstrap_ci(wins.astype(float), months, np.mean)
    gains = net[wins].sum()
    losses = -net[~wins].sum()
    exits = defaultdict(int)
    for t in trades:
        exits[t["exit_reason"]] += 1
    outcomes = defaultdict(int)
    for t in trades:
        outcomes[t["outcome"]] += 1
    excess_stats = {}
    if all(t.get("excess_vs_spy_pct") is not None for t in trades):
        excess = np.array([t["excess_vs_spy_pct"] for t in trades], dtype=float)
        ex_lo, ex_hi = block_bootstrap_ci(excess, months, np.mean)
        excess_stats = {
            "mean_spy_return_same_window_pct": round(float(np.mean([t["spy_return_pct"] for t in trades])), 3),
            "mean_excess_vs_spy_pct": round(float(excess.mean()), 3),
            "excess_vs_spy_ci95_block_bootstrap": [round(ex_lo, 3), round(ex_hi, 3)] if ex_lo is not None else None,
        }
    with_beta = [t for t in trades if t.get("beta_adjusted_pct") is not None]
    if with_beta:
        ba = np.array([t["beta_adjusted_pct"] for t in with_beta], dtype=float)
        ba_lo, ba_hi = block_bootstrap_ci(ba, np.array([t["signal_date"][:7] for t in with_beta]), np.mean)
        excess_stats.update({
            "mean_beta": round(float(np.mean([t["beta"] for t in with_beta])), 3),
            "mean_beta_adjusted_pct": round(float(ba.mean()), 3),
            "beta_adjusted_ci95_block_bootstrap": [round(ba_lo, 3), round(ba_hi, 3)] if ba_lo is not None else None,
            "beta_adjusted_trade_count": len(with_beta),
        })
    return {
        "trade_count": n,
        "signal_months": int(len(np.unique(months))),
        "win_rate_pct": round(100.0 * wins.mean(), 2),
        "win_rate_ci95_block_bootstrap": [round(100 * wr_lo, 2), round(100 * wr_hi, 2)] if wr_lo is not None else None,
        "net_expectancy_pct": round(float(net.mean()), 3),
        "net_expectancy_ci95_block_bootstrap": [round(exp_lo, 3), round(exp_hi, 3)] if exp_lo is not None else None,
        "median_net_return_pct": round(float(np.median(net)), 3),
        "avg_win_pct": round(float(net[wins].mean()), 3) if wins.any() else None,
        "avg_loss_pct": round(float(net[~wins].mean()), 3) if (~wins).any() else None,
        "profit_factor": round(float(gains / losses), 3) if losses > 0 else None,
        "median_holding_days": float(np.median([t["holding_days"] for t in trades])),
        "exit_reasons": dict(exits),
        "outcomes": dict(outcomes),
        **excess_stats,
    }


def edge_verdict(stats: Dict[str, Any], ci_key: str = "net_expectancy_ci95_block_bootstrap") -> str:
    """Verdict from a block-bootstrap CI: absolute net expectancy (default) or excess vs SPY."""
    if stats.get("trade_count", 0) < 30 or not stats.get(ci_key):
        return "INSUFFICIENT_SAMPLE"
    lo, hi = stats[ci_key]
    if lo > 0:
        return "POSITIVE_EDGE_SUPPORTED"
    if hi < 0:
        return "NEGATIVE_EDGE"
    return "NO_EDGE_DETECTED_CI_INCLUDES_ZERO"


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def run(refresh_data: bool = False, workers: int = 4, limit: Optional[int] = None, write_outputs: bool = True,
        variants: Optional[List[str]] = None, universe: str = "cache_start", phase1_only: bool = False) -> Dict[str, Any]:
    t0 = time.time()
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    from src.ranker import SignalRanker, assign_tier
    from src.entry_location import evaluate_entry_location
    from src.pipeline_steps import (
        prepare_candidate_scores, composite_score, build_trade_plan, holding_days_for, BUY_THRESHOLD, selection_settings,
    )
    from src.scorers.context_scorer import ContextScorer
    from src.strategy_evidence import aggregate_trades, WalkForwardEvidenceBook
    from src.outcome.outcome_calculator import evaluate_signal_outcome, SAME_DAY_AMBIGUITY_POLICY
    from src.strategies.target_calculator import reset_reach_prob_cache
    from jobs.generate_signals import _mark_context_unavailable

    if universe == "production":
        stocks, etfs = production_universe()
        universe_date = datetime.now(timezone.utc).date().isoformat()
    else:
        stocks, etfs = start_universe()
        universe_date = os.path.basename(sorted(glob.glob(os.path.join(CACHE_BY_DATE, "*.parquet")))[0])[:10]
    suffix = "_production_universe" if universe == "production" else ""
    if limit:
        stocks = stocks[:limit]  # smoke-test mode only
    raw = load_history(stocks + etfs + ["SPY"], refresh=refresh_data)
    if "SPY" not in raw:
        raise RuntimeError("SPY history unavailable; cannot apply the production regime rule.")
    missing_stocks = [t for t in stocks if t not in raw]

    # Regime per date (production rule)
    spy = raw["SPY"].dropna(subset=["CLOSE"]).sort_index()
    spy_dma200 = spy["CLOSE"].rolling(200, min_periods=200).mean()
    date_regimes = {
        d: classify_regime(float(c), float(m))
        for d, c, m in zip(spy.index, spy["CLOSE"], spy_dma200)
        if np.isfinite(m)
    }

    frames = {t: prepare_frame(df) for t, df in raw.items() if t != "SPY" and len(df) > WARMUP_BARS + 5}
    del raw  # the frames hold everything needed from here on (keeps the 5,600-ticker run in memory)
    stock_frames = {t: f for t, f in frames.items() if t not in SECTOR_ETFS}

    # Cross-sectional screen: top 15% of 63-day returns across the stock universe each date
    closes = pd.DataFrame({t: f["CLOSE"] for t, f in stock_frames.items()})
    ret63 = closes / closes.shift(63) - 1.0
    cs_eligible: Dict[str, set] = defaultdict(set)
    for d, row in ret63.iterrows():
        valid = row.dropna()
        if valid.empty:
            continue
        ordered = sorted(valid.items(), key=lambda kv: (-kv[1], kv[0]))
        for t, _ in ordered[: max(1, int(len(ordered) * CROSS_SECTIONAL_TOP_FRACTION))]:
            cs_eligible[t].add(d)

    eval_dates = sorted(d for d in date_regimes if d >= spy.index[min(len(spy) - 1, WARMUP_BARS)])
    logger.info("Evaluation window %s -> %s (%d sessions); %d stocks with data (%d missing), %d ETFs",
                eval_dates[0].date(), eval_dates[-1].date(), len(eval_dates), len(stock_frames),
                len(missing_stocks), len(frames) - len(stock_frames))

    # Phase 1: parallel point-in-time scans (cached; invalidated when strategy code or data change)
    phase1_key = _phase1_cache_key(frames, limit)
    phase1_path = os.path.join(BACKTEST_DATA_DIR, f"_phase1_{phase1_key}.pkl")
    if os.path.exists(phase1_path) and not refresh_data:
        candidates = pd.read_pickle(phase1_path)
        logger.info("Phase 1 loaded from cache: %d raw strategy candidates", len(candidates))
    else:
        jobs_args = [
            (t, f, date_regimes, cs_eligible.get(t, set()), t in SECTOR_ETFS)
            for t, f in frames.items()
        ]
        candidates = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, res in enumerate(pool.map(scan_ticker, jobs_args, chunksize=4), 1):
                candidates.extend(res)
                if i % 50 == 0:
                    logger.info("Scanned %d/%d tickers (%d raw candidates, %.0fs)", i, len(jobs_args), len(candidates), time.time() - t0)
        pd.to_pickle(candidates, phase1_path)
        logger.info("Phase 1 complete: %d raw strategy candidates in %.0fs", len(candidates), time.time() - t0)
    raw_by_strategy = defaultdict(int)
    for c in candidates:
        raw_by_strategy[c["strategy"]] += 1
    logger.info("Raw candidates by strategy: %s", dict(raw_by_strategy))
    if phase1_only:
        logger.info("Phase 1 only: candidates cached at %s", phase1_path)
        return {"phase1_path": phase1_path, "raw_candidates_by_strategy": dict(raw_by_strategy)}

    production = selection_settings()
    settings_list = [production]
    candidate_filters: Dict[str, Tuple[str, Any]] = {}
    for v in variants or []:
        if v in VARIANT_PRESETS:
            settings_list.append(replace(production, name=v, **VARIANT_PRESETS[v]))
        else:
            settings_list.append(replace(production, name=v))
            candidate_filters[v] = STRATEGY_VARIANTS[v]
    if any(v.momentum_model == "relative_strength_12m" for v in settings_list):
        attach_relative_strength(candidates, frames, set(SECTOR_ETFS))
        logger.info("12-1 month relative strength attached (%d candidates without it)",
                    sum(1 for c in candidates if c.get("rs_percentile_12m") is None))
    sec_table = None
    if any("fundamental" in v.context_components for v in settings_list):
        sec_table = load_sec_point_in_time(sorted(stock_frames))
        logger.info("SEC point-in-time fundamentals for %d tickers", len(sec_table))

    # Phase 2: chronological production qualification, execution and walk-forward evidence
    by_date: Dict[pd.Timestamp, List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        by_date[c["_date"]].append(c)

    reset_reach_prob_cache()
    ranker = SignalRanker()
    context_scorer = ContextScorer()
    shadow_trades: List[Dict[str, Any]] = []    # every strategy setup (evidence)
    # Walk-forward evidence: a shadow trade counts from the first evaluation date after its exit
    evidence_book = WalkForwardEvidenceBook()
    shadow_active_until: Dict[Tuple[str, str], pd.Timestamp] = {}
    # One book per selection setting: issued recommendations (what the user would see)
    books = {v.name: {"trades": [], "active_until": {}, "censored": 0, "funnel": defaultdict(int)} for v in settings_list}
    sim_args = (build_trade_plan, evaluate_signal_outcome, holding_days_for, SAME_DAY_AMBIGUITY_POLICY)

    for d in eval_dates:
        day = by_date.get(d)
        if not day:
            continue
        d_str = d.date().isoformat()
        evidence = evidence_book.evidence_as_of(d_str)

        # Shadow trades: every setup that passed its strategy's rules (one open per strategy+ticker)
        shadow_results: Dict[Tuple[str, str], Tuple[str, Optional[Dict[str, Any]]]] = {}
        for sig in day:
            key = (sig["strategy"], sig["ticker"].upper())
            if key in shadow_active_until and shadow_active_until[key] >= d:
                continue
            status, res = simulate(sig, frames[sig["ticker"].upper()], sig["_loc"], evidence, d_str, *sim_args)
            shadow_results[key] = (status, res)
            if status == "closed":
                shadow_trades.append(res)
                evidence_book.add(res)
                shadow_active_until[key] = pd.Timestamp(res["exit_date"])
            elif status == "censored":
                shadow_active_until[key] = pd.Timestamp.max

        entry_states: Dict[Tuple[str, str], str] = {}  # entry location reads only price structure
        for settings in settings_list:
            book = books[settings.name]
            active_until, funnel = book["active_until"], book["funnel"]
            scored = []
            flt = candidate_filters.get(settings.name)
            for base in day:
                if flt and base["strategy"] == flt[0] and (flt[1] is None or not flt[1](base, frames[base["ticker"].upper()])):
                    continue
                sig = dict(base)
                regime = sig["regime"]
                prepare_candidate_scores(sig, regime, evidence, settings=settings)
                apply_backtest_context(sig, settings, sec_table, context_scorer, d_str, _mark_context_unavailable)
                if composite_score(sig, regime, ranker) is None:
                    scored.append(sig)
            scored.sort(key=lambda s: float(s.get("composite_score", 0.0)), reverse=True)
            funnel["scored"] += len(scored)

            issued_today = set()
            for sig in scored:
                ticker = sig["ticker"].upper()
                score = float(sig["composite_score"])
                if score < BUY_THRESHOLD:
                    continue
                funnel["score_qualified"] += 1
                if ticker in active_until and active_until[ticker] >= d:
                    continue  # already an active idea (production keeps one per ticker)
                if ticker in issued_today:
                    continue
                frame = frames[ticker]
                loc = sig["_loc"]
                # Same evidence and data as the shadow run that day -> identical plan and outcome; reuse it
                key = (sig["strategy"], ticker)
                status, res = shadow_results.get(key) or simulate(sig, frame, loc, evidence, d_str, *sim_args)
                if status == "invalid_plan":
                    continue
                if assign_tier(score) not in ("Strong Buy", "Buy"):
                    continue
                if key not in entry_states:
                    entry_states[key] = evaluate_entry_location(
                        sig, frame.iloc[max(0, loc - SCAN_WINDOW_BARS + 1): loc + 1], sig["strategy"]).state
                if settings.blocks_entry(entry_states[key]):
                    funnel["entry_location_blocked"] += 1
                    continue
                issued_today.add(ticker)
                funnel["issued"] += 1
                if status == "censored":
                    book["censored"] += 1
                    active_until[ticker] = pd.Timestamp.max
                    continue
                trade = dict(res)
                trade["composite_score"] = round(score, 2)
                trade["entry_state"] = entry_states[key]
                book["trades"].append(trade)
                active_until[ticker] = pd.Timestamp(res["exit_date"])

    prod_book = books[production.name]
    trades, censored, funnel = prod_book["trades"], prod_book["censored"], prod_book["funnel"]
    logger.info("Phase 2 complete: %d issued trades (%d censored), %d shadow setup trades (%.0fs)",
                len(trades), censored, len(shadow_trades), time.time() - t0)

    # Benchmark-relative return: SPY bought at the same fill-day open, sold at the exit-day close.
    # Beta-adjusted return: net - beta * SPY return, with beta from the 252 sessions before the
    # signal (point-in-time). Plain excess flatters high-beta selections in a rising market.
    spy_r = spy["CLOSE"].pct_change()
    betas = {}
    for tk, fr in frames.items():
        r = fr["CLOSE"].pct_change()
        s_r = spy_r.reindex(fr.index)
        betas[tk] = (r.rolling(252, min_periods=120).cov(s_r) / s_r.rolling(252, min_periods=120).var()).shift(1)
    spy_open, spy_close = spy["OPEN"], spy["CLOSE"]
    for t in [t for b in books.values() for t in b["trades"]] + shadow_trades:
        fill_d, exit_d = pd.Timestamp(t["fill_date"]), pd.Timestamp(t["exit_date"])
        o = spy_open.loc[fill_d:]
        c = spy_close.loc[:exit_d]
        if o.empty or c.empty:
            t["spy_return_pct"] = t["excess_vs_spy_pct"] = t["beta"] = t["beta_adjusted_pct"] = None
            continue
        spy_ret = (float(c.iloc[-1]) / float(o.iloc[0]) - 1.0) * 100.0
        t["spy_return_pct"] = round(spy_ret, 4)
        t["excess_vs_spy_pct"] = round(t["net_return_pct"] - spy_ret, 4)
        b_ser = betas.get(t["ticker"])
        beta = float(b_ser.get(pd.Timestamp(t["signal_date"]), np.nan)) if b_ser is not None else float("nan")
        t["beta"] = round(beta, 4) if np.isfinite(beta) else None
        t["beta_adjusted_pct"] = round(t["net_return_pct"] - beta * spy_ret, 4) if np.isfinite(beta) else None

    # Partitions: chronological by signal date with an embargo
    split_idx = int(len(eval_dates) * TRAIN_FRACTION)
    split_date = eval_dates[split_idx].date().isoformat()
    embargo_end = eval_dates[min(len(eval_dates) - 1, split_idx + EMBARGO_DAYS)].date().isoformat()
    early = [t for t in trades if t["signal_date"] < split_date]
    late = [t for t in trades if t["signal_date"] >= embargo_end]

    all_stats = compile_stats(trades)
    late_stats = compile_stats(late)
    per_strategy = {s: compile_stats([t for t in trades if t["strategy"] == s]) for s in sorted({t["strategy"] for t in trades})}
    per_regime = {r: compile_stats([t for t in trades if t["regime"] == r]) for r in sorted({t["regime"] for t in trades})}

    spy_period = spy.loc[eval_dates[0]:eval_dates[-1], "CLOSE"]
    spy_return = round((float(spy_period.iloc[-1]) / float(spy_period.iloc[0]) - 1) * 100, 2)

    setups_by_strategy = {
        s: compile_stats([t for t in shadow_trades if t["strategy"] == s])
        for s in sorted({t["strategy"] for t in shadow_trades})
    }
    evidence_agg = aggregate_trades(shadow_trades)
    evidence_as_of = max((t["exit_date"] for t in shadow_trades), default=None)

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(time.time() - t0, 1),
        "evaluation_window": [eval_dates[0].date().isoformat(), eval_dates[-1].date().isoformat()],
        "evaluation_sessions": len(eval_dates),
        "universe": (f"{len(stocks)} stocks from today's production security master ({universe_date}) + {len(etfs)} sector ETFs; "
                     if universe == "production" else
                     f"{len(stocks)} stocks + {len(etfs)} sector ETFs present on the first cache date ({universe_date}); ")
                    + f"{len(missing_stocks)} stocks had no downloadable history (likely delisted)",
        "transaction_cost_pct_per_side": TRANSACTION_COST_PCT_PER_SIDE,
        "execution": "Fill at next bar open; STOP_FIRST; ratcheted stops; gap fills at open; expiry at strategy hold_days",
        "split": {"early_period_before": split_date, "late_period_from": embargo_end, "embargo_sessions": EMBARGO_DAYS},
        "censored_trades": censored,
        "raw_candidates_by_strategy": dict(raw_by_strategy),
        "shadow_setup_trades": len(shadow_trades),
        "funnel": dict(funnel),
        "evidence_as_of": evidence_as_of,
        "limitations": [
            f"Survivorship: the universe is today's ({universe_date}) security master applied to the whole window, so "
            f"every period is chosen with hindsight (stocks that delisted are missing). Results are biased upward, "
            f"most for small caps, which delist more often."
            if universe == "production" else
            f"Survivorship: the universe is the {universe_date} cache constituents; before that date it "
            f"is chosen with hindsight (survivors only). The late period from {embargo_end} is the cleaner test."
            if universe_date > eval_dates[0].date().isoformat() else
            "Survivorship: tickers delisted after the start date have no history and are excluded.",
            "No historical earnings calendar: earnings blackout not applied; PEAD cannot fire.",
            "No historical context data: context excluded from scores (weights renormalized).",
            "No VIX history: VIX>40 override not applied.",
            "Strategy thresholds were hand-tuned beforehand; the late period is not a clean out-of-sample test of them.",
            "Confidence intervals use a signal-month block bootstrap; trades are not independent.",
        ],
    }
    summary = {
        "metadata": metadata,
        "edge_verdict_all": edge_verdict(all_stats),
        "edge_verdict_late_period": edge_verdict(late_stats),
        "edge_vs_spy_verdict_all": edge_verdict(all_stats, "excess_vs_spy_ci95_block_bootstrap"),
        "edge_vs_spy_verdict_late_period": edge_verdict(late_stats, "excess_vs_spy_ci95_block_bootstrap"),
        "edge_beta_adjusted_verdict_all": edge_verdict(all_stats, "beta_adjusted_ci95_block_bootstrap"),
        "edge_beta_adjusted_verdict_late_period": edge_verdict(late_stats, "beta_adjusted_ci95_block_bootstrap"),
        "verdict_note": "edge_verdict_* tests absolute net return, which a rising market lifts on its own. "
                        "Selection skill is judged by the vs-SPY and beta-adjusted verdicts.",
        "all_trades": all_stats,
        "early_period": compile_stats(early),
        "late_period": late_stats,
        "by_strategy": per_strategy,
        "by_regime": per_regime,
        "all_strategy_setups_by_strategy": setups_by_strategy,
        "benchmark": {"symbol": "SPY", "buy_and_hold_return_pct_over_window": spy_return},
    }

    if len(settings_list) > 1:
        variant_summary = {}
        for settings in settings_list:
            b = books[settings.name]
            v_trades = b["trades"]
            v_late = [t for t in v_trades if t["signal_date"] >= embargo_end]
            entry = {
                "settings": {"momentum_model": settings.momentum_model,
                             "entry_location_mode": settings.entry_location_mode,
                             "context_components": list(settings.context_components),
                             "candidate_filter": settings.name if settings.name in candidate_filters else None},
                "funnel": dict(b["funnel"]),
                "censored_trades": b["censored"],
                "edge_vs_spy_verdict_all": None,
                "all_trades": compile_stats(v_trades),
                "late_period": compile_stats(v_late),
                "by_strategy": {s: compile_stats([t for t in v_trades if t["strategy"] == s])
                                for s in sorted({t["strategy"] for t in v_trades})},
            }
            entry["edge_vs_spy_verdict_all"] = edge_verdict(entry["all_trades"], "excess_vs_spy_ci95_block_bootstrap")
            if settings.name != production.name:
                entry["vs_production"] = {
                    "excess_vs_spy_pct": paired_block_diff(v_trades, trades, "excess_vs_spy_pct"),
                    "net_return_pct": paired_block_diff(v_trades, trades, "net_return_pct"),
                    "late_excess_vs_spy_pct": paired_block_diff(v_late, late, "excess_vs_spy_pct"),
                    "beta_adjusted_pct": paired_block_diff(v_trades, trades, "beta_adjusted_pct"),
                    "late_beta_adjusted_pct": paired_block_diff(v_late, late, "beta_adjusted_pct"),
                }
            variant_summary[settings.name] = entry
        summary["selection_variants"] = variant_summary

    if not write_outputs:
        return summary

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    with open(os.path.join(OUTPUTS_DIR, f"production_backtest_summary{suffix}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    if trades:
        pd.DataFrame(trades).to_csv(os.path.join(OUTPUTS_DIR, f"production_backtest_trades{suffix}.csv"), index=False)
    if shadow_trades:
        pd.DataFrame(shadow_trades).to_csv(os.path.join(OUTPUTS_DIR, f"production_backtest_setup_trades{suffix}.csv"), index=False)
    if "selection_variants" in summary:
        with open(os.path.join(OUTPUTS_DIR, "production_backtest_variants.json"), "w", encoding="utf-8") as f:
            json.dump({"metadata": {"generated_at": metadata["generated_at"],
                                    "evaluation_window": metadata["evaluation_window"],
                                    "note": "Same setups, evidence and execution for every variant; paired CIs "
                                            "resample signal months for both books."},
                       "variants": summary["selection_variants"]}, f, indent=2)
        pd.DataFrame([dict(t, variant=name) for name, b in books.items() for t in b["trades"]]).to_csv(
            os.path.join(OUTPUTS_DIR, "production_backtest_variant_trades.csv"), index=False)

    evidence_doc = {
        "metadata": {
            "description": "Per-strategy evidence from the production-pipeline backtest "
                           "(scripts/validate_backtest_pipeline.py), measured on all of each strategy's "
                           "setups (one open per strategy+ticker), net of costs. Consumed by src/strategy_evidence.py.",
            "generated_at": metadata["generated_at"],
            "evaluation_window": metadata["evaluation_window"],
            "evidence_as_of": evidence_as_of,
            "net_of_costs_pct_per_side": TRANSACTION_COST_PCT_PER_SIDE,
            "edge_verdict_all": summary["edge_verdict_all"],
            "edge_vs_spy_verdict_all": summary["edge_vs_spy_verdict_all"],
            "edge_beta_adjusted_verdict_all": summary["edge_beta_adjusted_verdict_all"],
            "verdict_note": summary["verdict_note"],
            "limitations": metadata["limitations"],
        },
        "strategy_evidence": evidence_agg,
    }
    # Every run writes its evidence next to its outputs. config/strategy_performance.json (read by
    # production scoring) is adopted only from a production-universe run, as a separate step
    # (scripts/adopt_strategy_evidence.py), so a cache_start (516-stock) run never replaces it.
    evidence_out = os.path.join(OUTPUTS_DIR, f"strategy_performance{suffix or '_cache_start'}.json")
    with open(evidence_out, "w", encoding="utf-8") as f:
        json.dump(evidence_doc, f, indent=2)
    logger.info("Wrote %s and outputs/ (%.0fs total)", evidence_out, time.time() - t0)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Production-pipeline backtest")
    parser.add_argument("--refresh-data", action="store_true", help="Re-download the backtest price history")
    parser.add_argument("--universe", choices=("cache_start", "production"), default="cache_start",
                        help="cache_start: constituents of the first local cache file (default); "
                             "production: today's broad US universe used by the nightly scan")
    parser.add_argument("--phase1-only", action="store_true", help="Run and cache the strategy scans, then stop")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None, help="Smoke test on the first N stocks (outputs not written)")
    all_variants = list(VARIANT_PRESETS) + list(STRATEGY_VARIANTS)
    parser.add_argument("--variants", default="", help=f"Comma-separated variants or 'all': {', '.join(all_variants)}")
    args = parser.parse_args()
    chosen = all_variants if args.variants.strip() == "all" else [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in chosen if v not in all_variants]
    if unknown:
        parser.error(f"Unknown variants {unknown}; choose from {all_variants}")
    s = run(refresh_data=args.refresh_data, workers=args.workers, limit=args.limit, write_outputs=args.limit is None,
            variants=chosen, universe=args.universe, phase1_only=args.phase1_only)
    if args.phase1_only:
        print(json.dumps(s, indent=2))
        sys.exit(0)
    print(json.dumps({k: s[k] for k in ("edge_verdict_all", "edge_verdict_late_period",
                                        "edge_vs_spy_verdict_all", "edge_vs_spy_verdict_late_period",
                                        "edge_beta_adjusted_verdict_all", "edge_beta_adjusted_verdict_late_period",
                                        "verdict_note")}, indent=2))
    print(json.dumps(s["all_trades"], indent=2))
    print(json.dumps({k: {kk: v.get(kk) for kk in ("trade_count", "win_rate_pct", "net_expectancy_pct", "net_expectancy_ci95_block_bootstrap")} for k, v in s["by_strategy"].items()}, indent=2))
    for name, v in s.get("selection_variants", {}).items():
        a = v["all_trades"]
        print(f"{name:26s} trades {a.get('trade_count', 0):5d} | net {a.get('net_expectancy_pct')} | "
              f"excess vs SPY {a.get('mean_excess_vs_spy_pct')} | beta-adj {a.get('mean_beta_adjusted_pct')} "
              f"CI {a.get('beta_adjusted_ci95_block_bootstrap')} | vs production: full {v.get('vs_production', {}).get('beta_adjusted_pct')} "
              f"late {v.get('vs_production', {}).get('late_beta_adjusted_pct')}")
