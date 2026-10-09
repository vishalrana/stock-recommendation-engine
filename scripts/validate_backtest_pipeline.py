"""
Production-Pipeline Backtest
============================
Replays the nightly recommendation pipeline day by day over ~5 years of history and measures
the trades it would have produced, using the SAME code paths as jobs/generate_signals.py:

  * Regime:       SPY vs its 200 DMA with the +/-2% sideways band (src.regime.classify_regime)
                  and the production REGIME_STRATEGY_MAP strategy activation.
  * Universe:     the tickers present on the FIRST day of the local price cache (fixed at the
                  start, so it is not chosen with hindsight), stocks and sector ETFs separated as in
                  production, with the production point-in-time liquidity filter and the
                  cross-sectional top-15% 63-day momentum screen.
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
  * Survivorship: tickers that delisted after the start have no downloadable history.
  * No historical earnings calendar: the earnings blackout cannot be applied and PEAD
    (which needs past earnings dates) cannot fire.
  * No historical news/analyst context: context is "unavailable" and excluded from the score
    with renormalized weights, as production does when context is missing.
  * No VIX history in the cache: the VIX>40 emergency override is not applied.
  * Thresholds were tuned by hand before this harness existed, so even the later "test"
    period is not a clean out-of-sample period for those choices.

Outputs:
  outputs/production_backtest_summary.json   statistics
  outputs/production_backtest_trades.csv     every simulated trade
  config/strategy_performance.json           per-strategy evidence used by production scoring

Usage:
  python scripts/validate_backtest_pipeline.py [--refresh-data] [--workers 4]
"""

import argparse
import glob
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
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
EVIDENCE_OUT = os.path.join(PROJECT_ROOT, "config", "strategy_performance.json")

HISTORY_PERIOD = "5y"
SCAN_WINDOW_BARS = 260          # >= 252 needed by the 52-week strategy
WARMUP_BARS = 260
TRANSACTION_COST_PCT_PER_SIDE = 0.10
TRAIN_FRACTION = 0.60
EMBARGO_DAYS = 20
BOOTSTRAP_SAMPLES = 2000
CROSS_SECTIONAL_TOP_FRACTION = 0.15


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
    }


# ----------------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------------
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
def run(refresh_data: bool = False, workers: int = 4, limit: Optional[int] = None, write_outputs: bool = True) -> Dict[str, Any]:
    t0 = time.time()
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    from src.ranker import SignalRanker, assign_tier
    from src.entry_location import evaluate_entry_location
    from src.pipeline_steps import prepare_candidate_scores, composite_score, build_trade_plan, holding_days_for, BUY_THRESHOLD
    from src.strategy_evidence import aggregate_trades, evidence_from_aggregate
    from src.outcome.outcome_calculator import evaluate_signal_outcome, SAME_DAY_AMBIGUITY_POLICY
    from src.strategies.target_calculator import reset_reach_prob_cache
    from jobs.generate_signals import _mark_context_unavailable

    stocks, etfs = start_universe()
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

    # Phase 2: chronological production qualification, execution and walk-forward evidence
    by_date: Dict[pd.Timestamp, List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        by_date[c["_date"]].append(c)

    reset_reach_prob_cache()
    ranker = SignalRanker()
    trades: List[Dict[str, Any]] = []           # issued recommendations (what the user would see)
    shadow_trades: List[Dict[str, Any]] = []    # every strategy setup (evidence)
    shadow_by_exit: List[Dict[str, Any]] = []   # shadow trades ordered by exit date
    shadow_active_until: Dict[Tuple[str, str], pd.Timestamp] = {}
    active_until: Dict[str, pd.Timestamp] = {}
    censored = 0
    funnel = defaultdict(int)
    sim_args = (build_trade_plan, evaluate_signal_outcome, holding_days_for, SAME_DAY_AMBIGUITY_POLICY)

    for d in eval_dates:
        day = by_date.get(d)
        if not day:
            continue
        d_str = d.date().isoformat()
        prior = [t for t in shadow_by_exit if t["exit_date"] < d_str]
        agg = aggregate_trades(prior)
        evidence = {k: evidence_from_aggregate(k, v, source="walk_forward", as_of=d_str) for k, v in agg.items()}

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
                shadow_by_exit.append(res)
                shadow_active_until[key] = pd.Timestamp(res["exit_date"])
            elif status == "censored":
                shadow_active_until[key] = pd.Timestamp.max
        if shadow_results:
            shadow_by_exit.sort(key=lambda t: t["exit_date"])

        scored = []
        for sig in day:
            regime = sig["regime"]
            prepare_candidate_scores(sig, regime, evidence)
            _mark_context_unavailable(sig)
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
            loc_res = evaluate_entry_location(sig, frame.iloc[max(0, loc - SCAN_WINDOW_BARS + 1): loc + 1], sig["strategy"])
            if loc_res.state in ("WAIT", "REJECT"):
                funnel["entry_location_blocked"] += 1
                continue
            issued_today.add(ticker)
            funnel["issued"] += 1
            if status == "censored":
                censored += 1
                active_until[ticker] = pd.Timestamp.max
                continue
            trade = dict(res)
            trade["composite_score"] = round(score, 2)
            trades.append(trade)
            active_until[ticker] = pd.Timestamp(res["exit_date"])

    logger.info("Phase 2 complete: %d issued trades (%d censored), %d shadow setup trades (%.0fs)",
                len(trades), censored, len(shadow_trades), time.time() - t0)

    # Benchmark-relative return: SPY bought at the same fill-day open, sold at the exit-day close.
    spy_open, spy_close = spy["OPEN"], spy["CLOSE"]
    for t in trades + shadow_trades:
        fill_d, exit_d = pd.Timestamp(t["fill_date"]), pd.Timestamp(t["exit_date"])
        o = spy_open.loc[fill_d:]
        c = spy_close.loc[:exit_d]
        if o.empty or c.empty:
            t["spy_return_pct"] = t["excess_vs_spy_pct"] = None
            continue
        spy_ret = (float(c.iloc[-1]) / float(o.iloc[0]) - 1.0) * 100.0
        t["spy_return_pct"] = round(spy_ret, 4)
        t["excess_vs_spy_pct"] = round(t["net_return_pct"] - spy_ret, 4)

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
        "universe": f"{len(stocks)} stocks + {len(etfs)} sector ETFs present on the first cache date; "
                    f"{len(missing_stocks)} stocks had no downloadable history (likely delisted)",
        "transaction_cost_pct_per_side": TRANSACTION_COST_PCT_PER_SIDE,
        "execution": "Fill at next bar open; STOP_FIRST; ratcheted stops; gap fills at open; expiry at strategy hold_days",
        "split": {"early_period_before": split_date, "late_period_from": embargo_end, "embargo_sessions": EMBARGO_DAYS},
        "censored_trades": censored,
        "raw_candidates_by_strategy": dict(raw_by_strategy),
        "shadow_setup_trades": len(shadow_trades),
        "funnel": dict(funnel),
        "evidence_as_of": evidence_as_of,
        "limitations": [
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
        "all_trades": all_stats,
        "early_period": compile_stats(early),
        "late_period": late_stats,
        "by_strategy": per_strategy,
        "by_regime": per_regime,
        "all_strategy_setups_by_strategy": setups_by_strategy,
        "benchmark": {"symbol": "SPY", "buy_and_hold_return_pct_over_window": spy_return},
    }

    if not write_outputs:
        return summary

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    with open(os.path.join(OUTPUTS_DIR, "production_backtest_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    if trades:
        pd.DataFrame(trades).to_csv(os.path.join(OUTPUTS_DIR, "production_backtest_trades.csv"), index=False)
    if shadow_trades:
        pd.DataFrame(shadow_trades).to_csv(os.path.join(OUTPUTS_DIR, "production_backtest_setup_trades.csv"), index=False)

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
            "limitations": metadata["limitations"],
        },
        "strategy_evidence": evidence_agg,
    }
    with open(EVIDENCE_OUT, "w", encoding="utf-8") as f:
        json.dump(evidence_doc, f, indent=2)
    logger.info("Wrote %s and outputs/ (%.0fs total)", EVIDENCE_OUT, time.time() - t0)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Production-pipeline backtest")
    parser.add_argument("--refresh-data", action="store_true", help="Re-download the backtest price history")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None, help="Smoke test on the first N stocks (outputs not written)")
    args = parser.parse_args()
    s = run(refresh_data=args.refresh_data, workers=args.workers, limit=args.limit, write_outputs=args.limit is None)
    print(json.dumps({k: s[k] for k in ("edge_verdict_all", "edge_verdict_late_period",
                                        "edge_vs_spy_verdict_all", "edge_vs_spy_verdict_late_period")}, indent=2))
    print(json.dumps(s["all_trades"], indent=2))
    print(json.dumps({k: {kk: v.get(kk) for kk in ("trade_count", "win_rate_pct", "net_expectancy_pct", "net_expectancy_ci95_block_bootstrap")} for k, v in s["by_strategy"].items()}, indent=2))
