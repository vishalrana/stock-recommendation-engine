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

Earnings calendar (since 2026-10-11): SEC EDGAR 8-K item 2.02 filings give point-in-time
announcement dates and reaction sessions (src/providers/context/sec_filings.py). PEAD reads the
last announcement on or before each bar, Pullback's earnings buffer the next one, and issued ideas
get the live fixed-days blackout (EARNINGS_BLACKOUT_DAYS). Future announcement dates are treated as
known at the signal, as a calendar would show them; earnings results are never used. Without
SEC_USER_AGENT the run falls back to no calendar (and refuses the hygiene variants).

Known limitations (reported in the output):
  * Survivorship: before the universe date the universe holds only stocks that survived to it,
    and tickers that delisted afterwards have no downloadable history.
  * Earnings dates come from SEC 8-K item 2.02 filings known at the decision (src/earnings_pit.py,
    shared with the live scan): the next report is a point-in-time estimate, never the actual later
    filing. Filers without them (foreign private issuers) have no date and are not issued.
  * No historical news/analyst context: context is excluded from the score with renormalized
    weights, exactly as the live scan scores (context is display-only there since 2026-10-20).
  * No VIX history in the cache: the VIX>40 emergency override is not applied.
  * Thresholds were tuned by hand before this harness existed, so even the later "test"
    period is not a clean out-of-sample period for those choices.

Selection variants (--variants): the same pass can evaluate other selection settings
(src.pipeline_steps.SelectionSettings: momentum model, entry-location mode, context components)
against identical setups, evidence and execution. Each variant keeps its own book (one active idea
per ticker); the production settings always run first and alone produce the standard outputs.
"fundamentals_in_score" scores the context from point-in-time SEC fundamentals (facts filed
before the signal date; the only context with history).

Profitability-program measurements (CLAUDE.md, pre-registered 2026-10-11), for production and
every variant:
  * LATE period: signals from 2025-04-09 (fixed, LATE_FROM), whatever the window.
  * Random-stock placebo (--placebo N, default 200; 20 in --limit smoke runs; 0 = off): the book's
    percentile among N placebo books, and the paired diff vs the per-idea placebo median.
  * Cost scenarios: 10 bps/side, + Corwin-Schultz half-spread (trailing 20 sessions, point-in-time),
    twice that, and the break-even cost per side.
  * Multiple testing: one-sided paired-bootstrap p-values, Holm across the variants of the run, and
    StepM (arch, stationary bootstrap) on per-signal-month series when arch is installed.
  * Trials registry: one record per book per run in outputs/research_trials.jsonl (smoke runs marked),
    merged into the committed config/research_trials.jsonl by the workflow (scripts/persist_trials.py).
  * Survivorship gauge: share of S&P 500 member-days with no bar in the price cache.

Outputs:
  outputs/production_backtest_summary.json   statistics
  outputs/production_backtest_trades.csv     every simulated trade
  outputs/strategy_performance_<universe>.json  per-strategy evidence of this run. Production scoring reads
                                                config/strategy_performance.json, which is adopted only from a
                                                production-universe run (scripts/adopt_strategy_evidence.py)
  outputs/production_backtest_variants.json  variant statistics and paired differences (with --variants)

Usage:
  python scripts/validate_backtest_pipeline.py [--refresh-data] [--workers 4] [--variants all]
         [--placebo 200] [--trial-phase phase2]
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("SKIP_NLP", "true")

from src.indicators import calculate_indicators
from src.regime import classify_regime
from src.pipeline_steps import CROSS_SECTIONAL_TOP_FRACTION, cross_sectional_screen  # shared with the live scan
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
SEC_FACTS_DIR = os.path.join(BACKTEST_DATA_DIR, "sec")
LATE_FROM = "2025-04-09"        # pre-registered LATE period: signals from this date (after the universe date)
PLACEBO_DRAWS_FULL = 200
PLACEBO_DRAWS_SMOKE = 20
PLACEBO_SEED = 20261011
SEC_SUBMISSIONS_DIR = os.path.join(SEC_FACTS_DIR, "submissions")
SEC_FILINGS_TTL_HOURS = 72      # refetch filings older than this (the window's last days need fresh 8-Ks)
SEC_HISTORY_START = date(2021, 1, 1)

# Trade-hygiene variants (Phase 2, rule R1): production settings plus issuance filters
# (src/trade_hygiene.py). They need the SEC calendar and filings.
HYGIENE_VARIANTS: Dict[str, Tuple[str, ...]] = {
    "earnings_full_hold": ("earnings_full_hold",),
    "event_lock": ("event_lock",),
    "rr_floor_stop_cap": ("rr_floor_stop_cap",),
    "hygiene_all": ("earnings_full_hold", "event_lock", "rr_floor_stop_cap"),
}
# Production with one production hygiene rule switched off (extra trials, report only).
RULE_OFF_VARIANTS: Dict[str, Tuple[str, ...]] = {
    "stop_cap_off": ("rr_floor_stop_cap",),
}
# Production with REJECT_UNKNOWN_EARNINGS off: an idea without a known or estimated report date is
# issued (blackout and full-hold still apply to dated ideas). Extra trial, report only.
UNKNOWN_EARNINGS_VARIANTS = ("allow_unknown_earnings",)
# Pattern veto (Phase 5, rule R2): no idea within 20 sessions of a confirmed head-and-shoulders top.
PATTERN_VARIANTS = ("hs_top_veto",)

# Selection / structure variants (Phase 4, rule R2, Holm over these six). The ranking signal is
# C6, adopted under R3 (Phase 3): equal-weight mean of the MOM12_1, RESMOM, HI52, -FIP, -REV21 and
# -RVOL252 cross-sectional ranks (scripts/research_signal_ic.py), as a percentile among liquid stocks.
#   rank_select         qualify on C6 >= 80th percentile instead of composite >= 65 (tier not used)
#   cs_mom_12_1         Cross-Sectional Momentum screen: C6 top 10% instead of 63-day return top 15%
#   momentum_rank_book  weekly book: C6 top decile with price above a rising 200-DMA; exit below the
#                       70th percentile at a weekly check, after 60 sessions, or at a 3 x ATR stop
#   trail_after_t1      after T1, the remainder trails at highest close - 3 x ATR (40-session limit)
#   mr_liquid_only      Mean Reversion only among the 500 most liquid stocks (20-day dollar volume)
#   no_rebound_entries  no rebound entries: Pullback Recovery and Mean Reversion ideas dropped
PHASE4_VARIANTS = ("rank_select", "cs_mom_12_1", "momentum_rank_book", "trail_after_t1", "mr_liquid_only",
                   "no_rebound_entries")
PHASE4_PARAMS = {"rank_select": 1, "cs_mom_12_1": 1, "momentum_rank_book": 4, "trail_after_t1": 2,
                 "mr_liquid_only": 1, "no_rebound_entries": 0}
REBOUND_STRATEGIES = ("Pullback Recovery", "Mean Reversion")
C6_VARIANTS = ("rank_select", "cs_mom_12_1", "momentum_rank_book")


def c6_percentiles(stock_frames: Dict[str, pd.DataFrame], etf_frames: Dict[str, pd.DataFrame],
                   spy: pd.DataFrame) -> pd.DataFrame:
    """C6 percentile (0-1] among liquid stocks per session, point-in-time (research_signal_ic features)."""
    from scripts.research_signal_ic import compute_features, composite_c6, liquid_mask, SECTOR_TO_ETF
    idx = spy.index
    close = pd.DataFrame({t: f["CLOSE"] for t, f in stock_frames.items()}).reindex(idx)
    high = pd.DataFrame({t: f["HIGH"] for t, f in stock_frames.items()}).reindex(idx)
    vol = pd.DataFrame({t: f["VOLUME"] for t, f in stock_frames.items()}).reindex(idx)
    etf_close = pd.DataFrame({e: f["CLOSE"] for e, f in etf_frames.items()}).reindex(idx)
    try:
        with open(os.path.join(PROJECT_ROOT, "data", "cache", "universe", "us_equities_master.json"), encoding="utf-8") as fh:
            master = {x["ticker"]: x.get("sector") for x in json.load(fh)["securities"]}
    except Exception:
        master = {}
    sectors = {t: (SECTOR_TO_ETF.get(master.get(t) or master.get(t.replace("-", ".")) or "")
                   if SECTOR_TO_ETF.get(master.get(t) or "") in etf_close else None) for t in close.columns}
    feats = compute_features(close, high, vol, spy["CLOSE"], etf_close, sectors)
    mask = liquid_mask(close, vol)
    c6 = composite_c6(feats, mask)
    return c6.where(mask).rank(axis=1, pct=True)


def trail_after_t1_outcome(frame: pd.DataFrame, loc: int, fill: float, stop: float, t1: float,
                           t2: Optional[float], t3: Optional[float], hold_days: int,
                           mult: float = 3.0, limit: int = 40) -> Optional[Dict[str, Any]]:
    """
    Production exits until T1 (STOP_FIRST, gap fills, expiry at hold_days). At T1 the plan's first
    scale-out fills; the remainder's stop is breakeven, then trails at the highest close since T1
    minus mult x ATR14 (updated after each close), with a limit of `limit` sessions from the fill.
    """
    from src.research.placebo import _weights
    o, h, l, c = (frame[k].to_numpy(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE"))
    atr = frame["ATR_14"].to_numpy(float)
    w1 = _weights(t1, t2, t3)[0]
    after_t1, cur, rem, realized, hd, best = False, float(stop), 1.0, 0.0, 0, None

    def done(reason, i, px):
        return {"realized_return_pct": round(realized, 4), "exit_reason": reason,
                "outcome": "hit_t1" if after_t1 else ("stopped" if reason == "stop" else "expired"),
                "outcome_date": frame.index[i].date().isoformat(), "holding_days": hd, "exit_price": px}

    for i in range(loc + 1, len(c)):
        if not all(np.isfinite(x) and x > 0 for x in (o[i], h[i], l[i], c[i])):
            continue
        hd += 1
        if not after_t1:
            px = o[i] if o[i] <= cur else (cur if (o[i] < t1 and l[i] <= cur) else None)
            if px is not None:
                realized += rem * (px - fill) / fill * 100.0
                return done("stop", i, px)
            if h[i] >= t1:
                realized += w1 * (max(t1, o[i]) - fill) / fill * 100.0
                rem, after_t1, cur, best = 1.0 - w1, True, max(cur, fill), c[i]
                if l[i] <= cur:                       # STOP_FIRST on the ratcheted stop
                    realized += rem * (cur - fill) / fill * 100.0
                    return done("stop", i, cur)
                if np.isfinite(atr[i]):
                    cur = max(cur, best - mult * atr[i])
            elif hd >= hold_days:
                realized += rem * (c[i] - fill) / fill * 100.0
                return done("expiry", i, c[i])
        else:
            px = o[i] if o[i] <= cur else (cur if l[i] <= cur else None)
            if px is not None:
                realized += rem * (px - fill) / fill * 100.0
                return done("stop", i, px)
            best = max(best, c[i])
            if np.isfinite(atr[i]):
                cur = max(cur, best - mult * atr[i])
            if hd >= limit:
                realized += rem * (c[i] - fill) / fill * 100.0
                return done("expiry", i, c[i])
    return None


def momentum_rank_book(stock_frames: Dict[str, pd.DataFrame], c6pct: pd.DataFrame, eval_dates: List[pd.Timestamp],
                       regimes: Dict[pd.Timestamp, str], reject=None) -> Tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    """Weekly C6 top-decile book (see PHASE4_VARIANTS). reject(ticker, d, sig) -> reason applies production hygiene."""
    weeks: Dict[Tuple[int, int], pd.Timestamp] = {}
    for d in eval_dates:
        weeks.setdefault(tuple(d.isocalendar()[:2]), d)
    checks = sorted(weeks.values())
    check_set = set(checks)
    trades, censored, funnel = [], 0, defaultdict(int)
    active_until: Dict[str, pd.Timestamp] = {}
    sma_cache: Dict[str, pd.Series] = {}
    for d in checks:
        row = c6pct.loc[d] if d in c6pct.index else None
        if row is None:
            continue
        for t in sorted(row[row >= 0.90].index):
            if t in active_until and active_until[t] >= d:
                continue
            f = stock_frames[t]
            if d not in f.index:
                continue
            loc = f.index.get_loc(d)
            if loc < 220 or loc + 1 >= len(f):
                continue
            if t not in sma_cache:
                sma_cache[t] = f["CLOSE"].rolling(200).mean()
            sma = sma_cache[t]
            if not (f["CLOSE"].iat[loc] > sma.iat[loc] > sma.iat[loc - 20]):
                continue
            funnel["qualified"] += 1
            atr, close = float(f["ATR_14"].iat[loc]), float(f["CLOSE"].iat[loc])
            fill = float(f["OPEN"].iat[loc + 1])
            if not (np.isfinite(atr) and atr > 0 and np.isfinite(fill) and fill > 0):
                continue
            sig = {"entry_price": close, "stop_loss": close - 3 * atr, "target_1": None, "atr_14": atr,
                   "strategy": "Momentum Rank Book"}
            if reject is not None and reject(t, d, sig, loc):
                funnel["hygiene_blocked"] += 1
                continue
            stop = fill - 3 * atr
            o, h, l, c = (f[k].to_numpy(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE"))
            res, hd = None, 0
            for i in range(loc + 1, len(c)):
                if not all(np.isfinite(x) and x > 0 for x in (o[i], h[i], l[i], c[i])):
                    continue
                hd += 1
                px = o[i] if o[i] <= stop else (stop if l[i] <= stop else None)
                if px is not None:
                    res = ("stop", i, px)
                    break
                di = f.index[i]
                if hd >= 60:
                    res = ("expiry", i, c[i])
                    break
                if di in check_set and di in c6pct.index:
                    p = c6pct.at[di, t]
                    if not np.isfinite(p) or p < 0.70:
                        res = ("rank_exit", i, c[i])
                        break
            funnel["issued"] += 1
            if res is None:
                censored += 1
                active_until[t] = pd.Timestamp.max
                continue
            reason, i, px = res
            raw = (px - fill) / fill * 100.0
            trades.append({
                "signal_date": d.date().isoformat(), "fill_date": f.index[loc + 1].date().isoformat(),
                "exit_date": f.index[i].date().isoformat(), "ticker": t, "strategy": "Momentum Rank Book",
                "regime": regimes.get(d), "composite_score": round(100 * float(row[t]), 2),
                "entry_signal_close": close, "fill_price": round(fill, 4), "stop_loss": close - 3 * atr,
                "stop_pct": round(3 * atr / close * 100, 2), "target_1": None, "target_2": None, "target_3": None,
                "has_t2": False, "has_t3": False, "reach_prob_source": None, "atr_14": round(atr, 6),
                "outcome": "stopped" if reason == "stop" else "expired", "exit_reason": reason,
                "exit_price": round(float(px), 4), "holding_days": hd, "raw_return_pct": round(raw, 4),
                "net_return_pct": round(raw - 2.0 * TRANSACTION_COST_PCT_PER_SIDE, 4),
                "adv20_usd": round(float((f["CLOSE"].iloc[max(0, loc - 19): loc + 1] * f["VOLUME"].iloc[max(0, loc - 19): loc + 1]).mean()), 0),
            })
            active_until[t] = f.index[i]
    return trades, censored, dict(funnel)


def production_hygiene_rules() -> Tuple[str, ...]:
    """
    Hygiene rules in production (src/quant_config.py switches), in the order the live scan applies
    them (jobs/generate_signals._trade_hygiene_rejection): stop cap, earnings, event lock. Every book
    applies them; the order makes the per-stage funnel counts comparable with the live scan's.
    """
    from src import quant_config as qc
    from src.trade_hygiene import rr_stop_enabled
    rules = []
    if rr_stop_enabled():  # same switch as the live scan
        rules.append("rr_floor_stop_cap")
    rules.append("earnings")  # unknown date, fixed-days blackout, full hold (trade_hygiene.earnings_gate)
    if qc.EVENT_LOCK_ENABLED:
        rules.append("event_lock")
    return tuple(rules)

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


def load_sec_events(tickers: List[str], end: date) -> Optional[Dict[str, Any]]:
    """
    SEC filings per ticker (earnings events, deal filings) and rights-offering hits, cached under
    data/backtest_history/sec/submissions. None when SEC access is not configured.
    """
    from src.providers.context.sec_filings import SecFilingsProvider, earnings_events, rights_days_for
    prov = SecFilingsProvider(cache_dir=SEC_SUBMISSIONS_DIR, ttl_hours=SEC_FILINGS_TTL_HOURS, since=SEC_HISTORY_START)
    if not prov.available:
        logger.warning("SEC_USER_AGENT not set: no earnings calendar or event filings for this run.")
        return None
    t0 = time.time()
    prov.sec.ticker_map()  # load once before the worker threads share it
    filings: Dict[str, Any] = {}
    from concurrent.futures import ThreadPoolExecutor
    # Threads only overlap network latency; the provider's lock keeps requests under SEC's rate limit.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (t, f) in enumerate(zip(tickers, pool.map(prov.filings, tickers)), 1):
            filings[t] = f
            if i % 500 == 0:
                logger.info("SEC filings %d/%d (%s, %.0fs)", i, len(tickers), prov.stats, time.time() - t0)
    rights = prov.rights_offering_days(SEC_HISTORY_START, end)
    if rights is None:
        logger.warning("SEC full-text search unavailable: rights offerings are not part of the event lock.")
    events = {t: earnings_events(f) for t, f in filings.items() if f is not None}
    # Why a ticker's filings are missing (trade_hygiene.SEC_UNAVAILABLE_CAUSES), as the live scan records it
    unavailable = {t: "no_cik" if prov.sec.cik_for(t) is None else "fetch_failed" for t, f in filings.items() if f is None}
    rights_by_ticker = {t: rights_days_for(rights, prov.sec.cik_for(t)) for t in tickers}
    stats = dict(prov.stats)
    stats.update({
        "tickers": len(tickers),
        "with_filings": sum(1 for f in filings.values() if f is not None),
        "unavailable_no_cik": sum(1 for v in unavailable.values() if v == "no_cik"),
        "unavailable_fetch_failed": sum(1 for v in unavailable.values() if v == "fetch_failed"),
        "sec_degraded": not any(f is not None for f in filings.values()),   # SEC returned nothing usable
        "with_earnings_events": sum(1 for e in events.values() if e),
        "earnings_events": sum(len(e) for e in events.values()),
        "rights_offering_search": rights is not None,
        "tickers_with_rights_offering_hits": sum(1 for v in rights_by_ticker.values() if v),
        "seconds": round(time.time() - t0, 1),
    })
    logger.info("SEC calendar: %s", stats)
    return {"filings": filings, "events": events, "rights": rights_by_ticker, "stats": stats, "unavailable": unavailable}


def truncate_unadjusted_splits(raw: Dict[str, pd.DataFrame]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str]]:
    """
    Bars from the first split Yahoo left unadjusted (src/data/splits.py, the live cache's check) on
    are dropped: the ticker is then neither scanned nor ranked after the split, as live excludes a
    flagged ticker, and a trade open across it ends censored instead of on a false stop or target.
    Histories stored before 2026-10-22 carry no split events and are not checked.
    """
    from src.data.splits import flagged_split_dates
    out, cut = {}, {}
    for t, df in raw.items():
        if "SPLIT" in df.columns:
            ev = [(d, r) for d, r in df["SPLIT"].fillna(0).items() if r and r > 0]
            dates = flagged_split_dates(df["CLOSE"], ev) if ev else []
            if dates:
                cut[t] = dates[0]
                df = df[df.index < pd.Timestamp(dates[0])]
            df = df.drop(columns=["SPLIT"])
        out[t] = df
    if cut:
        logger.warning("Unadjusted splits: %d tickers truncated at the split (%s)", len(cut),
                       ", ".join(f"{t} {d}" for t, d in sorted(cut.items())[:20]))
    return out, cut


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
        raw = yf.download(chunk, period=HISTORY_PERIOD, group_by="ticker", auto_adjust=True, actions=True,
                          progress=False, threads=True)
        for t in chunk:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df.dropna(how="all")
                if df.empty:
                    continue
                df.columns = [str(c).upper() for c in df.columns]
                df = df.dropna(subset=["CLOSE"])
                # Split events kept for the unadjusted-split check (src/data/splits.py), as the live cache does
                df = df[["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"] + (["STOCK SPLITS"] if "STOCK SPLITS" in df.columns else [])]
                df = df.rename(columns={"STOCK SPLITS": "SPLIT"})
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
def _register_sec_events(ticker: str, events) -> None:
    """
    The strategies read earnings dates through src/earnings_pit.py, as in the live scan: PEAD the
    last report known at the bar (accepted by 20:00 ET), Pullback a point-in-time estimate of the
    next one. Here the lookup serves this worker's ticker from the preloaded SEC events (None when
    the company has no SEC filings), so no later filing can leak into a decision.
    """
    from src.earnings_pit import set_events_lookup
    t0 = ticker.upper()
    set_events_lookup(lambda t, recent_only=False: events if t == t0 else None)


def scan_ticker(args) -> List[Dict[str, Any]]:
    ticker, frame, date_regimes, cs_eligible_dates, is_etf, events, only_date = args
    _register_sec_events(ticker, events)
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
        if only_date is not None and date != only_date:
            continue  # replay of one session (--replay-session)
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


def _phase1_cache_key(frames: Dict[str, pd.DataFrame], limit: Optional[int],
                      events: Optional[Dict[str, Any]] = None, extra: str = "") -> str:
    """Hash of strategy/indicator source code and the data extent, so cached scans never go stale."""
    import hashlib
    import inspect
    h = hashlib.sha256()
    sources = sorted(glob.glob(os.path.join(PROJECT_ROOT, "jobs", "strategies", "*.py")))
    sources += [os.path.join(PROJECT_ROOT, "src", x) for x in ("indicators.py", "regime.py", "earnings_pit.py", "trade_hygiene.py")]
    for p in sources:
        with open(p, "rb") as f:
            h.update(f.read())
    # Phase-1 code in this script (later phases do not affect the cached scans)
    for fn in (scan_ticker, _register_sec_events, prepare_frame, cross_sectional_screen):
        h.update(inspect.getsource(fn).encode())
    h.update(f"{SCAN_WINDOW_BARS}:{WARMUP_BARS}:{CROSS_SECTIONAL_TOP_FRACTION}".encode())
    # Which strategies run in which regime (scan_ticker reads it), e.g. a strategy switched off
    from jobs.generate_signals import REGIME_STRATEGY_MAP
    h.update(json.dumps(REGIME_STRATEGY_MAP, sort_keys=True).encode())
    h.update(str(limit).encode())
    h.update(extra.encode())
    h.update(json.dumps({t: [e.accepted.isoformat() for e in v] for t, v in sorted((events or {}).items())}).encode())
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
        "atr_14": round(float(sig.get("atr_14") or 0.0), 6),
        "outcome": res["outcome"],
        "exit_reason": res["exit_reason"],
        "exit_price": round(float(res["exit_price"]), 4),
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
    """
    mean(a) - mean(b) of `key`, with a 95% CI resampling the same signal months for both books,
    and one-sided bootstrap p-values (p_gt0: H0 diff <= 0; p_lt0: H0 diff >= 0) for Holm.
    """
    from src.research.stats import paired_month_bootstrap
    va = [t for t in a if t.get(key) is not None]
    vb = [t for t in b if t.get(key) is not None]
    r = paired_month_bootstrap([t["signal_date"][:7] for t in va], [t[key] for t in va],
                               [t["signal_date"][:7] for t in vb], [t[key] for t in vb], n_boot=n_boot, seed=seed)
    if r["diff"] is None:
        return {"diff": None, "ci95": None, "p_gt0": None, "p_lt0": None}
    return {"diff": round(r["diff"], 3), "ci95": [round(x, 3) for x in r["ci95"]],
            "p_gt0": round(r["p_gt0"], 4), "p_lt0": round(r["p_lt0"], 4)}


def point_in_time_betas(frames: Dict[str, pd.DataFrame], spy: pd.DataFrame) -> Dict[str, pd.Series]:
    """Beta vs SPY from the 252 sessions before each bar (shifted one bar, so no look-ahead)."""
    spy_r = spy["CLOSE"].pct_change()
    betas = {}
    for tk, fr in frames.items():
        r = fr["CLOSE"].pct_change()
        s_r = spy_r.reindex(fr.index)
        betas[tk] = (r.rolling(252, min_periods=120).cov(s_r) / s_r.rolling(252, min_periods=120).var()).shift(1)
    return betas


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


def left_tail(trades: List[Dict[str, Any]], events: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """R1 left-tail report: 5th-percentile and worst idea, stops gapped through, earnings inside the hold."""
    ba = np.array([t["beta_adjusted_pct"] for t in trades if t.get("beta_adjusted_pct") is not None], dtype=float)
    stopped = [t for t in trades if t.get("exit_reason") == "stop" and t.get("outcome") == "stopped"]
    gapped = [t for t in stopped if t.get("exit_price") is not None and t["exit_price"] < t["stop_loss"] * (1 - 1e-6)]
    out: Dict[str, Any] = {
        "p05_beta_adjusted_pct": round(float(np.percentile(ba, 5)), 3) if len(ba) else None,
        "worst_beta_adjusted_pct": round(float(ba.min()), 3) if len(ba) else None,
        "initial_stop_exits": len(stopped),
        "share_stops_gapped_through_pct": round(100.0 * len(gapped) / len(stopped), 2) if stopped else None,
    }
    if events is not None:
        covered = [t for t in trades if events["events"].get(t["ticker"])]
        inside = [t for t in covered if any(t["fill_date"] <= e.reaction.isoformat() <= t["exit_date"]
                                            for e in events["events"][t["ticker"]])]
        out.update({
            "ideas_with_earnings_calendar": len(covered),
            "share_earnings_inside_hold_pct": round(100.0 * len(inside) / len(covered), 2) if covered else None,
        })
    return out


def hygiene_rejection(rules: Tuple[str, ...], sig: Dict[str, Any], ticker: str, d: pd.Timestamp, is_etf: bool,
                      events: Dict[str, Any], session_cal, hold_days: int, pinned: Optional[bool],
                      reject_unknown: bool = True) -> Tuple[Optional[str], Optional[str], Dict[str, bool]]:
    """
    (rule, reason) of the first hygiene rule that rejects the idea, else (None, None); plus
    unknown-data flags. Earnings use trade_hygiene.earnings_gate, the live scan's function, on the
    SEC events known at the decision: the next report is a point-in-time estimate, never the actual
    later filing (no look-ahead). Returned earnings rules: earnings_sec_unavailable, earnings_unknown,
    earnings_blackout, earnings_full_hold.
    """
    from src.trade_hygiene import earnings_gate, rr_stop_rejection
    from src.providers.context.sec_filings import event_lock
    flags = {"earnings_unknown": False, "earnings_sec_unavailable": False, "event_lock_unknown": False}
    for rule in rules:
        if rule in ("earnings", "earnings_full_hold"):
            ev = events["events"].get(ticker) if events is not None else None
            cause = (events or {}).get("unavailable", {}).get(ticker)
            gate_rule, reason = earnings_gate(sig["strategy"], is_etf, d.date(), ev, session_cal, hold_days,
                                              unavailable_cause=cause)
            if gate_rule in ("earnings_unknown", "earnings_sec_unavailable"):
                flags["earnings_unknown"] = True
            if gate_rule == "earnings_sec_unavailable":
                flags["earnings_sec_unavailable"] = True
            if reason and (reject_unknown or gate_rule not in ("earnings_unknown", "earnings_sec_unavailable")):
                return gate_rule, reason, flags
        elif rule == "event_lock" and not is_etf:
            filings = events["filings"].get(ticker)
            if filings is None:
                flags["event_lock_unknown"] = True
            else:
                lock = event_lock(filings, d.date(), events["rights"].get(ticker, []))
                if lock:
                    return rule, f"Event lock: {lock['form']} since {lock['since']}", flags
            if pinned:
                return rule, "Price pinned after a > 15% move (deal backstop)", flags
        elif rule == "rr_floor_stop_cap":
            reason = rr_stop_rejection(sig.get("entry_price"), sig.get("stop_loss"), sig.get("target_1"), sig.get("atr_14"))
            if reason:
                return rule, reason, flags
    return None, None, flags


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
        variants: Optional[List[str]] = None, universe: str = "cache_start", phase1_only: bool = False,
        placebo: Optional[int] = None, trial_phase: str = "adhoc", record_trials: bool = True,
        replay_session: Optional[str] = None, replay_active: Sequence[str] = ()) -> Dict[str, Any]:
    """
    replay_session (parity test, scripts/parity_replay.py): evaluate only that session with the
    production settings, the adopted evidence file (as the live scan) and the live scan's open ideas
    (replay_active) as the book, and write every candidate's stage to outputs/parity_replay_<date>_harness.csv.
    """
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
    raw, split_truncated = truncate_unadjusted_splits(raw)
    missing_stocks = [t for t in stocks if t not in raw]

    # Regime per date (production rule)
    spy = raw["SPY"].dropna(subset=["CLOSE"]).sort_index()
    spy_dma200 = spy["CLOSE"].rolling(200, min_periods=200).mean()
    date_regimes = {
        d: classify_regime(float(c), float(m))
        for d, c, m in zip(spy.index, spy["CLOSE"], spy_dma200)
        if np.isfinite(m)
    }

    bar_dates = {t: df.dropna(subset=["CLOSE"]).index for t, df in raw.items() if t != "SPY"}
    # Cross-Sectional Momentum screen: the live scan's definition and ranking universe (every stock
    # with closes, including those too short to scan; live ranks them too)
    cs_closes = pd.DataFrame({t: df["CLOSE"] for t, df in raw.items() if t != "SPY" and t not in SECTOR_ETFS}).sort_index()
    cs_screen = cross_sectional_screen(cs_closes)
    if replay_session and pd.Timestamp(replay_session) in cs_closes.index:   # the screen's full ranking, for the replay
        from src.pipeline_steps import cross_sectional_ranking
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        cross_sectional_ranking(cs_closes, replay_session).to_csv(
            os.path.join(OUTPUTS_DIR, f"parity_replay_{replay_session}_screen.csv"), index=False)
    del cs_closes
    frames = {t: prepare_frame(df) for t, df in raw.items() if t != "SPY" and len(df) > WARMUP_BARS + 5}
    del raw  # the frames hold everything needed from here on (keeps the 5,600-ticker run in memory)
    stock_frames = {t: f for t, f in frames.items() if t not in SECTOR_ETFS}

    cs_eligible: Dict[str, set] = defaultdict(set)
    for d, top in cs_screen.items():
        for t, _ in top:
            cs_eligible[t].add(d)

    c6pct = None
    cs63 = {t: set(v) for t, v in cs_eligible.items()}
    csc6: Dict[str, set] = defaultdict(set)
    phase1_extra = ""
    if any(v in C6_VARIANTS for v in (variants or [])):
        tc = time.time()
        c6pct = c6_percentiles(stock_frames, {t: f for t, f in frames.items() if t in SECTOR_ETFS}, spy)
        logger.info("C6 percentiles computed (%.0fs)", time.time() - tc)
        if "cs_mom_12_1" in (variants or []):
            top = c6pct >= 0.90
            for t in top.columns:
                csc6[t] = set(top.index[top[t].to_numpy()])
                cs_eligible[t] = cs_eligible[t] | csc6[t]
            import hashlib
            phase1_extra = "cs_c6:" + hashlib.sha256(json.dumps(
                {t: len(v) for t, v in sorted(csc6.items())}).encode()).hexdigest()[:12]

    eval_dates = sorted(d for d in date_regimes if d >= spy.index[min(len(spy) - 1, WARMUP_BARS)])
    sec_events = load_sec_events(sorted(stock_frames), spy.index[-1].date())
    hygiene_requested = [v for v in (variants or []) if v in HYGIENE_VARIANTS] + list(production_hygiene_rules())
    if hygiene_requested and sec_events is None:
        raise RuntimeError(f"Variants {hygiene_requested} need the SEC calendar and filings (set SEC_USER_AGENT).")
    # SEC 8-K item 2.02 events per ticker (with acceptance times, so the 20:00 ET cutoff applies);
    # a ticker without SEC filings has none, as in the live scan
    events_by_ticker = sec_events["events"] if sec_events is not None else {}
    logger.info("Evaluation window %s -> %s (%d sessions); %d stocks with data (%d missing), %d ETFs",
                eval_dates[0].date(), eval_dates[-1].date(), len(eval_dates), len(stock_frames),
                len(missing_stocks), len(frames) - len(stock_frames))

    # Phase 1: parallel point-in-time scans (cached; invalidated when strategy code or data change)
    replay_day = None
    if replay_session:
        replay_day = pd.Timestamp(replay_session)
        if replay_day not in set(eval_dates):
            raise RuntimeError(f"No bars for the replay session {replay_session} (latest {eval_dates[-1].date()}); use --refresh-data")
        eval_dates = [replay_day]
        variants = []
        phase1_extra += f"|replay:{replay_session}"
    phase1_key = _phase1_cache_key(frames, limit, events_by_ticker, phase1_extra)
    phase1_path = os.path.join(BACKTEST_DATA_DIR, f"_phase1_{phase1_key}.pkl")
    if os.path.exists(phase1_path) and not refresh_data:
        candidates = pd.read_pickle(phase1_path)
        logger.info("Phase 1 loaded from cache: %d raw strategy candidates", len(candidates))
    else:
        jobs_args = [
            (t, f, date_regimes, cs_eligible.get(t, set()), t in SECTOR_ETFS, events_by_ticker.get(t), replay_day)
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
    # Cross-Sectional Momentum candidates carry which screen admitted them (production: 63-day)
    for c in candidates:
        if c["strategy"] == "Cross-Sectional Momentum":
            t = c["ticker"].upper()
            c["_cs63"] = c["_date"] in cs63.get(t, ())
            c["_csc6"] = c["_date"] in csc6.get(t, ())
    raw_by_strategy = defaultdict(int)
    for c in candidates:
        if c.get("_cs63", True):
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
        elif (v in HYGIENE_VARIANTS or v in PATTERN_VARIANTS or v in PHASE4_VARIANTS or v in RULE_OFF_VARIANTS
              or v in UNKNOWN_EARNINGS_VARIANTS):
            settings_list.append(replace(production, name=v))
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
    from src.trade_hygiene import SessionCalendar
    from src.providers.context.sec_filings import price_pin
    session_cal = SessionCalendar([x.date() for x in spy.index])
    pins: Dict[str, np.ndarray] = {}
    prod_rules = production_hygiene_rules()
    decisions: Optional[List[Dict[str, Any]]] = None
    live_evidence = None
    if replay_day is not None:
        from src.strategy_evidence import load_strategy_evidence
        decisions, live_evidence = [], load_strategy_evidence()
        books[production.name]["active_until"].update({t.upper(): pd.Timestamp.max for t in replay_active})

    def _note(settings, sig, stage, reason=""):
        if decisions is not None and settings.name == production.name:
            decisions.append({"ticker": sig["ticker"].upper(), "strategy": sig["strategy"],
                              "composite_score": sig.get("composite_score"), "stage": stage, "reason": reason})
    hs_tops: Dict[str, np.ndarray] = {}     # per ticker: bars at which an H&S top became known
    sim_args = (build_trade_plan, evaluate_signal_outcome, holding_days_for, SAME_DAY_AMBIGUITY_POLICY)
    adv_rank = None
    if "mr_liquid_only" in [v.name for v in settings_list]:
        dv = pd.DataFrame({t: (f["CLOSE"] * f["VOLUME"]).rolling(20).mean().where(f["_LIQUID"])
                           for t, f in stock_frames.items()}).reindex(spy.index)
        adv_rank = dv.rank(axis=1, ascending=False)

    funnel_by_date: List[Dict[str, Any]] = []   # production book, per session (diagnostics)
    for d in eval_dates:
        day = by_date.get(d)
        if not day:
            continue
        d_str = d.date().isoformat()
        evidence = live_evidence if live_evidence is not None else evidence_book.evidence_as_of(d_str)
        prod_before = dict(books[production.name]["funnel"])

        # Shadow trades: every setup that passed its strategy's rules (one open per strategy+ticker)
        shadow_results: Dict[Tuple[str, str], Tuple[str, Optional[Dict[str, Any]]]] = {}
        for sig in day:
            if not sig.get("_cs63", True):
                continue  # admitted only by the C6 screen (cs_mom_12_1); not a production setup
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
            if settings.name == "momentum_rank_book":
                continue  # built separately after the replay
            for base in day:
                if flt and base["strategy"] == flt[0] and (flt[1] is None or not flt[1](base, frames[base["ticker"].upper()])):
                    continue
                if base["strategy"] == "Cross-Sectional Momentum":
                    if not base.get("_csc6" if settings.name == "cs_mom_12_1" else "_cs63", True):
                        continue
                if settings.name == "no_rebound_entries" and base["strategy"] in REBOUND_STRATEGIES:
                    continue
                if settings.name == "mr_liquid_only" and base["strategy"] == "Mean Reversion":
                    if adv_rank is None or not (adv_rank.at[d, base["ticker"].upper()] <= 500):
                        continue
                sig = dict(base)
                regime = sig["regime"]
                prepare_candidate_scores(sig, regime, evidence, settings=settings)
                apply_backtest_context(sig, settings, sec_table, context_scorer, d_str, _mark_context_unavailable)
                score_err = composite_score(sig, regime, ranker)
                if score_err is None:
                    scored.append(sig)
                else:
                    _note(settings, sig, "scoring", score_err)
            if settings.name == "rank_select":
                for sg in scored:
                    v = c6pct.at[d, sg["ticker"].upper()] if sg["ticker"].upper() in c6pct.columns else np.nan
                    sg["_c6pct"] = float(v) if np.isfinite(v) else -1.0
                scored.sort(key=lambda s: s["_c6pct"], reverse=True)
            else:
                scored.sort(key=lambda s: float(s.get("composite_score", 0.0)), reverse=True)
            funnel["scored"] += len(scored)

            issued_today = set()
            for sig in scored:
                ticker = sig["ticker"].upper()
                score = float(sig["composite_score"])
                rank_mode = settings.name == "rank_select"
                if (sig["_c6pct"] < 0.80) if rank_mode else (score < BUY_THRESHOLD):
                    _note(settings, sig, "score_below_65")
                    continue
                funnel["score_qualified"] += 1
                if ticker in active_until and active_until[ticker] >= d:
                    _note(settings, sig, "already_active")
                    continue  # already an active idea (production keeps one per ticker)
                if ticker in issued_today:
                    _note(settings, sig, "dedupe")
                    continue
                frame = frames[ticker]
                loc = sig["_loc"]
                # Same evidence and data as the shadow run that day -> identical plan and outcome; reuse it
                key = (sig["strategy"], ticker)
                status, res = shadow_results.get(key) or simulate(sig, frame, loc, evidence, d_str, *sim_args)
                if status == "invalid_plan":
                    _note(settings, sig, "invalid_plan")
                    continue
                if not rank_mode and assign_tier(score) not in ("Strong Buy", "Buy"):
                    _note(settings, sig, "tier")
                    continue
                flags = {"earnings_unknown": None, "earnings_sec_unavailable": None, "event_lock_unknown": None}
                rules = tuple(r for r in dict.fromkeys(prod_rules + HYGIENE_VARIANTS.get(settings.name, ()))
                              if r not in RULE_OFF_VARIANTS.get(settings.name, ()))
                if rules:
                    if "event_lock" in rules and ticker not in pins:
                        pins[ticker] = price_pin(frame["CLOSE"]).to_numpy()
                    rule, why, flags = hygiene_rejection(
                        rules, sig, ticker, d, ticker in SECTOR_ETFS, sec_events, session_cal,
                        holding_days_for(sig["strategy"]) or 20, bool(pins[ticker][loc]) if ticker in pins else None,
                        reject_unknown=settings.name not in UNKNOWN_EARNINGS_VARIANTS)
                    if rule:
                        funnel[f"{rule}_blocked"] += 1
                        _note(settings, sig, rule, why)
                        continue
                if settings.name == "hs_top_veto":
                    if ticker not in hs_tops:
                        from src.patterns import pattern_events
                        evs = pattern_events(frame["HIGH"].to_numpy(float), frame["LOW"].to_numpy(float),
                                             frame["CLOSE"].to_numpy(float))
                        hs_tops[ticker] = np.array([e.index for e in evs if e.kind == "hs_top"], dtype=int)
                    tops = hs_tops[ticker]
                    if len(tops) and ((tops <= loc) & (tops > loc - 20)).any():
                        funnel["hs_top_veto_blocked"] += 1
                        continue
                if key not in entry_states:
                    entry_states[key] = evaluate_entry_location(
                        sig, frame.iloc[max(0, loc - SCAN_WINDOW_BARS + 1): loc + 1], sig["strategy"]).state
                if settings.blocks_entry(entry_states[key]):
                    funnel["entry_location_blocked"] += 1
                    _note(settings, sig, "entry_location", entry_states[key])
                    continue
                if settings.name == "trail_after_t1" and status in ("closed", "censored"):
                    fwd_fill = float(frame["OPEN"].iat[loc + 1]) if loc + 1 < len(frame) else float("nan")
                    alt = (trail_after_t1_outcome(frame, loc, fwd_fill, float(sig["stop_loss"]), float(sig["target_1"]),
                                                  sig.get("target_2"), sig.get("target_3"),
                                                  holding_days_for(sig["strategy"]) or 20)
                           if np.isfinite(fwd_fill) and fwd_fill > 0 else None)
                    if alt is None:
                        status, res = "censored", None
                    else:
                        base_res = res or simulate(sig, frame, loc, evidence, d_str, *sim_args)[1]
                        if base_res is None:
                            status, res = "censored", None
                        else:
                            res = dict(base_res, exit_date=alt["outcome_date"], outcome=alt["outcome"],
                                       exit_reason=alt["exit_reason"], exit_price=round(float(alt["exit_price"]), 4),
                                       holding_days=alt["holding_days"],
                                       raw_return_pct=alt["realized_return_pct"],
                                       net_return_pct=round(alt["realized_return_pct"] - 2.0 * TRANSACTION_COST_PCT_PER_SIDE, 4))
                            status = "closed"
                issued_today.add(ticker)
                funnel["issued"] += 1
                _note(settings, sig, "issued")
                if status == "censored":
                    book["censored"] += 1
                    active_until[ticker] = pd.Timestamp.max
                    continue
                trade = dict(res)
                trade["composite_score"] = round(score, 2)
                trade["entry_state"] = entry_states[key]
                trade.update({k: v for k, v in flags.items() if v is not None})
                book["trades"].append(trade)
                active_until[ticker] = pd.Timestamp(res["exit_date"])

        prod_after = books[production.name]["funnel"]
        row: Dict[str, Any] = {"date": d_str}
        row.update({k: prod_after[k] - prod_before.get(k, 0) for k in prod_after})
        for base in day:
            if base["strategy"] != "Cross-Sectional Momentum" or base.get("_cs63", True):
                row[f"raw_{base['strategy']}"] = row.get(f"raw_{base['strategy']}", 0) + 1
        funnel_by_date.append(row)

    if decisions is not None:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        out = os.path.join(OUTPUTS_DIR, f"parity_replay_{replay_session}_harness.csv")
        pd.DataFrame(decisions, columns=["ticker", "strategy", "composite_score", "stage", "reason"]).to_csv(out, index=False)
        logger.info("Replay of %s: %d candidate decisions written to %s", replay_session, len(decisions), out)
        return {"replay_session": replay_session, "decisions": len(decisions), "path": out}

    if "momentum_rank_book" in books:
        def _mrb_reject(t, d, sig, loc):
            # Not a production strategy: the earnings rules use Trend Following's blackout window
            if not prod_rules:
                return False
            if "event_lock" in prod_rules and t not in pins:
                pins[t] = price_pin(frames[t]["CLOSE"]).to_numpy()
            rule, _, _ = hygiene_rejection(tuple(r for r in prod_rules if r != "rr_floor_stop_cap"),
                                           dict(sig, strategy="Trend Following"), t, d, False,
                                           sec_events, session_cal, 60, bool(pins[t][loc]) if t in pins else None)
            return rule is not None
        mrb_trades, mrb_censored, mrb_funnel = momentum_rank_book(stock_frames, c6pct, eval_dates, date_regimes, _mrb_reject)
        books["momentum_rank_book"].update({"trades": mrb_trades, "censored": mrb_censored})
        books["momentum_rank_book"]["funnel"].update(mrb_funnel)
        logger.info("Momentum rank book: %d trades (%d censored)", len(mrb_trades), mrb_censored)

    prod_book = books[production.name]
    trades, censored, funnel = prod_book["trades"], prod_book["censored"], prod_book["funnel"]
    logger.info("Phase 2 complete: %d issued trades (%d censored), %d shadow setup trades (%.0fs)",
                len(trades), censored, len(shadow_trades), time.time() - t0)

    # Benchmark-relative return: SPY bought at the same fill-day open, sold at the exit-day close.
    # Beta-adjusted return: net - beta * SPY return, with beta from the 252 sessions before the
    # signal (point-in-time). Plain excess flatters high-beta selections in a rising market.
    betas = point_in_time_betas(frames, spy)
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

    # Corwin-Schultz half-spread at the signal close (trailing 20 sessions, point-in-time)
    from src.research.costs import rolling_half_spread_pct, half_spread_at, cost_scenarios
    spreads: Dict[str, pd.Series] = {}
    for t in [t for b in books.values() for t in b["trades"]]:
        tk = t["ticker"]
        if tk not in spreads:
            spreads[tk] = rolling_half_spread_pct(frames[tk]["HIGH"], frames[tk]["LOW"], close=frames[tk]["CLOSE"])
        hs = half_spread_at(spreads[tk], t["signal_date"])
        t["half_spread_pct"] = round(hs, 4) if hs is not None else None

    # Partitions: chronological by signal date with an embargo
    split_idx = int(len(eval_dates) * TRAIN_FRACTION)
    split_date = eval_dates[split_idx].date().isoformat()
    embargo_end = eval_dates[min(len(eval_dates) - 1, split_idx + EMBARGO_DAYS)].date().isoformat()
    early = [t for t in trades if t["signal_date"] < split_date]
    late = [t for t in trades if t["signal_date"] >= LATE_FROM]

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

    from src.research.survivorship import survivorship_gauge
    try:
        gauge = survivorship_gauge(bar_dates, eval_dates)
    except Exception as e:  # reference file missing or unreadable: report, never guess
        gauge = {"missing_share_pct": None, "error": str(e)}
    gauge_line = (f"Survivorship gauge: {gauge['missing_share_pct']}% of S&P 500 member-days in the window have no bar "
                  f"in the price cache ({gauge.get('members_never_seen')} historical members never seen; "
                  f"fja05680/sp500, MIT). Symbol changes overstate it slightly; delistings are true gaps."
                  if gauge.get("missing_share_pct") is not None else
                  f"Survivorship gauge unavailable: {gauge.get('error', 'no member-days in the window')}")

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
        "split": {"early_period_before": split_date, "late_period_from": LATE_FROM,
                  "late_period_note": "pre-registered LATE period (signals from 2025-04-09)",
                  "train_split_embargo_end": embargo_end, "embargo_sessions": EMBARGO_DAYS},
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
            f"is chosen with hindsight (survivors only). The late period from {LATE_FROM} is the cleaner test."
            if universe_date > eval_dates[0].date().isoformat() else
            "Survivorship: tickers delisted after the start date have no history and are excluded.",
            gauge_line,
            ("Earnings dates from SEC 8-K item 2.02 filings known at each decision (accepted by 20:00 ET), as in the "
             "live scan: the next report is a point-in-time estimate (+/-5-session band in the full-hold rule), never "
             "the actual later filing; filers without 8-K 2.02 (foreign private issuers) have no date and are not issued."
             if sec_events is not None else
             "No earnings calendar (SEC_USER_AGENT not set): earnings blackout not applied; PEAD cannot fire."),
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

    summary["survivorship_gauge"] = gauge
    summary["sec_calendar"] = sec_events["stats"] if sec_events is not None else None
    summary["sec_degraded"] = sec_events is None or bool(sec_events["stats"]["sec_degraded"])  # SEC returned nothing usable
    summary["unadjusted_splits"] = split_truncated   # ticker -> split date: bars from it on dropped
    summary["left_tail"] = left_tail(trades, sec_events)

    # Random-stock placebo, cost scenarios and monthly series for every book
    from src.research.placebo import PlaceboEngine
    from src.research.stats import monthly_series
    n_placebo = (PLACEBO_DRAWS_SMOKE if limit else PLACEBO_DRAWS_FULL) if placebo is None else int(placebo)

    def _ci(values, months):
        return block_bootstrap_ci(np.asarray(values, dtype=float), np.asarray(months), np.mean)

    placebo_engine = None
    if n_placebo > 0:
        placebo_engine = PlaceboEngine(stock_frames, spy, betas, holding_days_for,
                                       cost_pct_per_side=TRANSACTION_COST_PCT_PER_SIDE, seed=PLACEBO_SEED)
    book_extras: Dict[str, Dict[str, Any]] = {}
    for settings in settings_list:
        b_trades = books[settings.name]["trades"]
        extra: Dict[str, Any] = {"cost_scenarios": cost_scenarios(b_trades, _ci)}
        if placebo_engine is not None:
            tp = time.time()
            extra["placebo"] = placebo_engine.evaluate_book(
                b_trades, lambda t: float(t.get("atr_14") or 0.0), n_placebo, _ci)
            logger.info("Placebo (%d draws) for %s: %s (%.0fs)", n_placebo, settings.name,
                        {k: extra["placebo"].get(k) for k in ("placebo_percentile", "paired_diff_vs_placebo_median",
                                                              "paired_diff_ci95")}, time.time() - tp)
        extra["monthly_beta_adjusted"] = monthly_series([t["signal_date"][:7] for t in b_trades],
                                                        [t.get("beta_adjusted_pct") for t in b_trades])
        book_extras[settings.name] = extra
    prod_extra = book_extras[production.name]
    summary["cost_scenarios"] = prod_extra["cost_scenarios"]
    if "placebo" in prod_extra:
        summary["placebo"] = prod_extra["placebo"]
        summary["placebo_percentile"] = prod_extra["placebo"].get("placebo_percentile")

    if len(settings_list) > 1:
        variant_summary = {}
        for settings in settings_list:
            b = books[settings.name]
            v_trades = b["trades"]
            v_late = [t for t in v_trades if t["signal_date"] >= LATE_FROM]
            entry = {
                "settings": {"momentum_model": settings.momentum_model,
                             "entry_location_mode": settings.entry_location_mode,
                             "context_components": list(settings.context_components),
                             "candidate_filter": settings.name if settings.name in candidate_filters else None,
                             "hygiene_rules": list(dict.fromkeys(production_hygiene_rules() + HYGIENE_VARIANTS.get(settings.name, ())))},
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
            extra = book_extras[settings.name]
            entry["left_tail"] = left_tail(v_trades, sec_events)
            entry["late_left_tail"] = left_tail(v_late, sec_events)
            entry["cost_scenarios"] = extra["cost_scenarios"]
            if "placebo" in extra:
                entry["placebo"] = extra["placebo"]
                entry["placebo_percentile"] = extra["placebo"].get("placebo_percentile")
            variant_summary[settings.name] = entry
        summary["selection_variants"] = variant_summary

        # Multiple testing across this run's variants (the phase's Holm family when the run holds them all)
        from src.research.stats import holm, stepm_superior
        names = [v.name for v in settings_list if v.name != production.name]
        p_gt0 = [variant_summary[n]["vs_production"]["beta_adjusted_pct"].get("p_gt0") for n in names]
        valid = [(n, p) for n, p in zip(names, p_gt0) if p is not None]
        mt: Dict[str, Any] = {"family": names, "metric": "beta_adjusted_pct, full period, one-sided (variant > production)"}
        if valid:
            adj = holm([p for _, p in valid])
            mt["holm_adjusted_p_gt0"] = {n: round(a, 4) for (n, _), a in zip(valid, adj)}
        try:
            mt["stepm"] = stepm_superior(prod_extra["monthly_beta_adjusted"],
                                         {n: book_extras[n]["monthly_beta_adjusted"] for n in names})
        except ImportError:
            mt["stepm"] = {"superior": None, "note": "arch not installed (pip install -r requirements-research.txt)"}
        summary["multiple_testing"] = mt

    if record_trials:
        from src.research.trials import record_trial
        series_path = os.path.join("outputs", "production_backtest_variant_trades.csv" if len(settings_list) > 1
                                   else f"production_backtest_trades{suffix}.csv")
        for settings in settings_list:
            b_trades = books[settings.name]["trades"]
            st = compile_stats(b_trades)
            lt = compile_stats([t for t in b_trades if t["signal_date"] >= LATE_FROM])
            extra = book_extras[settings.name]
            record_trial(
                name=settings.name, phase=trial_phase,
                params={"momentum_model": settings.momentum_model, "entry_location_mode": settings.entry_location_mode,
                        "context_components": list(settings.context_components),
                        "candidate_filter": settings.name if settings.name in candidate_filters else None,
                        "hygiene_rules": [r for r in dict.fromkeys(production_hygiene_rules() + HYGIENE_VARIANTS.get(settings.name, ()))
                                          if r not in RULE_OFF_VARIANTS.get(settings.name, ())],
                        "reject_unknown_earnings": settings.name not in UNKNOWN_EARNINGS_VARIANTS,
                        "universe": universe, "limit": limit, "late_from": LATE_FROM},
                n_ideas=len(b_trades), mean_beta_adj=st.get("mean_beta_adjusted_pct"),
                ci=st.get("beta_adjusted_ci95_block_bootstrap"), late_mean=lt.get("mean_beta_adjusted_pct"),
                series_path=series_path, smoke=bool(limit),
                extra={"evaluation_window": metadata["evaluation_window"],
                       "vs_production": summary.get("selection_variants", {}).get(settings.name, {}).get("vs_production"),
                       "placebo": extra.get("placebo"), "cost_scenarios": extra["cost_scenarios"],
                       "left_tail": left_tail(b_trades, sec_events),
                       "monthly_beta_adjusted": extra["monthly_beta_adjusted"]},
            )

    if not write_outputs:
        if limit:
            os.makedirs(OUTPUTS_DIR, exist_ok=True)
            with open(os.path.join(OUTPUTS_DIR, f"production_backtest_summary{suffix}_smoke.json"), "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, default=str)
        return summary

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    with open(os.path.join(OUTPUTS_DIR, f"production_backtest_summary{suffix}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    if trades:
        pd.DataFrame(trades).to_csv(os.path.join(OUTPUTS_DIR, f"production_backtest_trades{suffix}.csv"), index=False)
    if shadow_trades:
        pd.DataFrame(shadow_trades).to_csv(os.path.join(OUTPUTS_DIR, f"production_backtest_setup_trades{suffix}.csv"), index=False)
        pd.DataFrame(funnel_by_date).fillna(0).to_csv(os.path.join(OUTPUTS_DIR, f"production_backtest_funnel_by_date{suffix}.csv"), index=False)
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
    all_variants = (list(VARIANT_PRESETS) + list(STRATEGY_VARIANTS) + list(HYGIENE_VARIANTS) + list(PATTERN_VARIANTS)
                    + list(PHASE4_VARIANTS) + list(RULE_OFF_VARIANTS) + list(UNKNOWN_EARNINGS_VARIANTS))
    parser.add_argument("--variants", default="", help=f"Comma-separated variants or 'all': {', '.join(all_variants)}")
    parser.add_argument("--placebo", type=int, default=None,
                        help=f"Random-stock placebo draws per idea (default {PLACEBO_DRAWS_FULL}; "
                             f"{PLACEBO_DRAWS_SMOKE} with --limit; 0 = off)")
    parser.add_argument("--replay-session", default=None,
                        help="Parity test: evaluate only this session (YYYY-MM-DD) and write each candidate's stage")
    parser.add_argument("--replay-active", default="",
                        help="Comma-separated tickers the live scan had open at the replay session")
    parser.add_argument("--trial-phase", default="adhoc",
                        help="Phase label for the trials registry (e.g. phase2); pre-registered trials only")
    args = parser.parse_args()
    chosen = all_variants if args.variants.strip() == "all" else [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in chosen if v not in all_variants]
    if unknown:
        parser.error(f"Unknown variants {unknown}; choose from {all_variants}")
    s = run(refresh_data=args.refresh_data, workers=args.workers, limit=args.limit, write_outputs=args.limit is None,
            variants=chosen, universe=args.universe, phase1_only=args.phase1_only,
            placebo=args.placebo, trial_phase=args.trial_phase, replay_session=args.replay_session,
            replay_active=[t.strip() for t in args.replay_active.split(",") if t.strip()])
    if args.phase1_only or args.replay_session:
        print(json.dumps(s, indent=2))
        sys.exit(0)
    print(json.dumps({k: s[k] for k in ("edge_verdict_all", "edge_verdict_late_period",
                                        "edge_vs_spy_verdict_all", "edge_vs_spy_verdict_late_period",
                                        "edge_beta_adjusted_verdict_all", "edge_beta_adjusted_verdict_late_period",
                                        "verdict_note")}, indent=2))
    print(json.dumps(s["all_trades"], indent=2))
    print(json.dumps({k: s.get(k) for k in ("placebo", "cost_scenarios", "survivorship_gauge", "multiple_testing",
                                            "sec_calendar", "left_tail")},
                     indent=2, default=str))
    print(json.dumps({k: {kk: v.get(kk) for kk in ("trade_count", "win_rate_pct", "net_expectancy_pct", "net_expectancy_ci95_block_bootstrap")} for k, v in s["by_strategy"].items()}, indent=2))
    for name, v in s.get("selection_variants", {}).items():
        a = v["all_trades"]
        print(f"{name:26s} trades {a.get('trade_count', 0):5d} | net {a.get('net_expectancy_pct')} | "
              f"excess vs SPY {a.get('mean_excess_vs_spy_pct')} | beta-adj {a.get('mean_beta_adjusted_pct')} "
              f"CI {a.get('beta_adjusted_ci95_block_bootstrap')} | vs production: full {v.get('vs_production', {}).get('beta_adjusted_pct')} "
              f"late {v.get('vs_production', {}).get('late_beta_adjusted_pct')} | placebo pct {v.get('placebo_percentile')}")
