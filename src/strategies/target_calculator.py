"""
Target Calculator — Strategy-Specific ATR Targets with Reach Probability Filtering
===================================================================================
Replaces fixed global targets with volatility-adjusted, empirical reach-probability
filtered targets to calculate honest weighted risk-to-reward ratios and scale-out weights.

Layer 1: Strategy-Specific ATR-Based Targets with Fixed Floors
Layer 2: Reach Probability Filtering over 504 trading days
Layer 3: Honest Weighted R:R Recalculation
"""

from dataclasses import dataclass, asdict
import os
import logging
from typing import Optional, Dict, Tuple, Any
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Canonical Quantitative Configuration (Single Source of Truth)
from src.quant_config import (
    STRATEGY_TARGET_CONFIG,
    STRATEGY_STOP_CONFIG,
    T3_REACH_PROB_SURVIVAL_THRESHOLD,
    SCALE_OUT_WEIGHTS,
    MIN_REACH_PROB_WINDOWS,
)


def normalize_strategy_name(name: str) -> str:
    """Normalize any strategy string variant to its canonical configuration key."""
    n = str(name).lower().strip().replace("-", "_").replace(" ", "_")
    mapping = {
        "trend_following": "trend_following",
        "trend": "trend_following",
        "52_week_high": "52w_high_breakout",
        "52w_high": "52w_high_breakout",
        "52w_high_breakout": "52w_high_breakout",
        "pullback_recovery": "pullback_recovery",
        "pullback": "pullback_recovery",
        "cross_sectional_momentum": "cross_sectional_momentum",
        "cross_sectional": "cross_sectional_momentum",
        "pead": "pead",
        "post_earnings_drift": "pead",
        "sector_rotation": "sector_rotation",
        "mean_reversion": "mean_reversion",
    }
    return mapping.get(n, "trend_following")


@dataclass
class TargetCalculationResult:
    """
    Result container for strategy-specific ATR targets, reach probability filtering,
    scale-out weights, and honest risk-to-reward ratio.
    """
    target_1: Optional[float]
    target_2: Optional[float]
    target_3: Optional[float]
    target_1_atr: float
    target_2_atr: float
    target_3_atr: float
    target_1_pct: Optional[float]
    target_2_pct: Optional[float]
    target_3_pct: Optional[float]
    reach_prob_t1: float
    reach_prob_t2: float
    reach_prob_t3: float
    scale_out_weights: str
    weighted_scaleout_rr: float
    weighted_rr_honest: float
    is_valid: bool
    rejection_reason: Optional[str] = None
    reach_prob_raw: Optional[float] = None
    reach_prob_adjusted: Optional[float] = None
    target_1_return_decimal: Optional[float] = None
    target_2_return_decimal: Optional[float] = None
    target_3_return_decimal: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


# Global in-memory cache for reach distributions: (ticker, holding_days) or (ticker, holding_days, as_of_date) -> np.ndarray
_REACH_DIST_CACHE: Dict[Any, np.ndarray] = {}
# Global in-memory cache for target-before-stop reach prob: (ticker, target_pct_round, stop_pct_round, holding_days) or with as_of -> float
_TARGET_STOP_REACH_CACHE: Dict[Any, float] = {}


def reset_reach_prob_cache() -> None:
    """Clear all in-memory reach probability caches."""
    _REACH_DIST_CACHE.clear()
    _TARGET_STOP_REACH_CACHE.clear()


def get_reach_prob_distribution(
    ticker: str,
    holding_days: int,
    price_df: Optional[pd.DataFrame] = None,
    lookback_days: int = 504,
) -> np.ndarray:
    """
    Compute or retrieve cached forward max-gain distribution for a ticker and holding period H.
    For each start day d in the lookback window:
        max_gain_d = (max(Close[d : d + H + 1]) - Close[d]) / Close[d]
    """
    t_up = ticker.upper()
    h = int(holding_days)
    as_of = str(price_df.index[-1])[:10] if (price_df is not None and len(price_df) > 0) else ""
    cache_key = (t_up, h, as_of)
    cache_key_compat = (t_up, h)
    if cache_key in _REACH_DIST_CACHE:
        return _REACH_DIST_CACHE[cache_key]
    if not as_of and cache_key_compat in _REACH_DIST_CACHE:
        return _REACH_DIST_CACHE[cache_key_compat]

    cache_dir = os.path.join("data", "cache", "reach_dists")
    cache_file = os.path.join(cache_dir, f"{t_up}.parquet")

    # 1. Check disk cache
    if os.path.exists(cache_file):
        try:
            cached_df = pd.read_parquet(cache_file)
            col_name = f"max_gain_{holding_days}d"
            if col_name in cached_df.columns:
                vals = cached_df[col_name].dropna().to_numpy(dtype=float)
                if len(vals) > 0:
                    _REACH_DIST_CACHE[cache_key] = vals
                    _REACH_DIST_CACHE[cache_key_compat] = vals
                    return vals
        except Exception as e:
            logger.debug("Failed reading reach_dist cache for %s: %s", ticker, e)

    # Fetch price history if needed
    if price_df is None or price_df.empty:
        try:
            from src.data.cache_manager import get_cache_manager
            import datetime
            cm = get_cache_manager()
            end_date = datetime.date.today().isoformat()
            start_date = (datetime.date.today() - datetime.timedelta(days=int(lookback_days * 1.6) + holding_days + 30)).isoformat()
            price_df = cm.get_ticker_history(ticker, start_date, end_date)
        except Exception as e:
            logger.debug("Could not load price history for reach prob %s: %s", ticker, e)

    if price_df is None or price_df.empty:
        return np.array([], dtype=float)

    # Extract Close series
    close_col = "CLOSE" if "CLOSE" in price_df.columns else "Close"
    if close_col not in price_df.columns:
        return np.array([], dtype=float)

    closes = price_df[close_col].dropna().to_numpy(dtype=float)
    n = len(closes)
    h = int(holding_days)

    if n <= h + 5:
        return np.array([], dtype=float)

    # Slide window across the available history (up to lookback_days windows)
    total_possible_windows = n - h
    num_windows = min(lookback_days, total_possible_windows)
    start_idx = total_possible_windows - num_windows

    max_gains = []
    for d in range(start_idx, total_possible_windows):
        window = closes[d : d + h + 1]
        base_price = closes[d]
        if base_price > 0:
            max_gain = (np.max(window) - base_price) / base_price
            max_gains.append(max_gain)

    arr = np.array(max_gains, dtype=float)

    # Cache distribution to parquet
    try:
        os.makedirs(cache_dir, exist_ok=True)
        col_name = f"max_gain_{h}d"
        save_df = pd.DataFrame({col_name: arr})
        if os.path.exists(cache_file):
            existing_df = pd.read_parquet(cache_file)
            existing_df[col_name] = pd.Series(arr)
            existing_df.to_parquet(cache_file, engine="pyarrow")
        else:
            save_df.to_parquet(cache_file, engine="pyarrow")
    except Exception as e:
        logger.debug("Failed saving reach_dist cache for %s: %s", ticker, e)

    _REACH_DIST_CACHE[cache_key] = arr
    _REACH_DIST_CACHE[cache_key_compat] = arr
    return arr


def get_reach_prob_target_before_stop(
    ticker: str,
    target_pct: float,
    stop_pct: float,
    holding_days: int,
    price_df: Optional[pd.DataFrame] = None,
    lookback_days: int = 504,
) -> float:
    """
    Calculate empirical reach probability where Target is reached BEFORE Stop is hit
    within the forward holding period H (in trading days).

    Zero lookahead: Evaluates historical sliding windows up to lookback_days.
    Conservative intra-day execution rules:
      1. Gap at Open:
         - Open <= Stop => STOP_HIT (stopped out at open)
         - Open >= Target => TARGET_REACHED (target hit at open)
      2. Intra-day Bar:
         - Low <= Stop AND High >= Target => STOP_HIT (conservative same-day ambiguity policy)
         - Low <= Stop => STOP_HIT
         - High >= Target => TARGET_REACHED
      3. Neither touched within H days => NEITHER_HIT (counted as failure).
    """
    t_up = ticker.upper()
    h = int(holding_days)
    t_pct = float(target_pct)
    s_pct = float(stop_pct)

    if t_pct <= 0 or s_pct <= 0 or h <= 0:
        return 0.0

    as_of = str(price_df.index[-1])[:10] if (price_df is not None and len(price_df) > 0) else ""
    cache_key = (t_up, round(t_pct, 4), round(s_pct, 4), h, as_of)
    cache_key_compat = (t_up, round(t_pct, 4), round(s_pct, 4), h)
    if cache_key in _TARGET_STOP_REACH_CACHE:
        return _TARGET_STOP_REACH_CACHE[cache_key]
    if not as_of and cache_key_compat in _TARGET_STOP_REACH_CACHE:
        return _TARGET_STOP_REACH_CACHE[cache_key_compat]

    # Fetch price history if needed
    if price_df is None or price_df.empty:
        try:
            from src.data.cache_manager import get_cache_manager
            import datetime
            cm = get_cache_manager()
            end_date = datetime.date.today().isoformat()
            start_date = (datetime.date.today() - datetime.timedelta(days=int(lookback_days * 1.6) + h + 30)).isoformat()
            price_df = cm.get_ticker_history(ticker, start_date, end_date)
        except Exception as e:
            logger.debug("Could not load price history for target-before-stop %s: %s", ticker, e)

    if price_df is None or price_df.empty:
        return 0.0

    close_col = "CLOSE" if "CLOSE" in price_df.columns else ("Close" if "Close" in price_df.columns else None)
    if close_col is None:
        return 0.0

    open_col = "OPEN" if "OPEN" in price_df.columns else ("Open" if "Open" in price_df.columns else close_col)
    high_col = "HIGH" if "HIGH" in price_df.columns else ("High" if "High" in price_df.columns else close_col)
    low_col = "LOW" if "LOW" in price_df.columns else ("Low" if "Low" in price_df.columns else close_col)

    # Use joint DataFrame dropna() across all 4 OHLC columns to guarantee row-by-row temporal alignment
    cols_to_check = list(dict.fromkeys([open_col, high_col, low_col, close_col]))
    clean_ohlc = price_df[cols_to_check].dropna()
    n = len(clean_ohlc)
    if n <= h + 5:
        return 0.0

    closes = clean_ohlc[close_col].to_numpy(dtype=float)
    opens = clean_ohlc[open_col].to_numpy(dtype=float)
    highs = clean_ohlc[high_col].to_numpy(dtype=float)
    lows = clean_ohlc[low_col].to_numpy(dtype=float)

    total_possible_windows = n - h
    num_windows = min(lookback_days, total_possible_windows)
    if num_windows < MIN_REACH_PROB_WINDOWS:
        return 0.0

    start_idx = total_possible_windows - num_windows

    success_count = 0
    valid_windows = 0

    for d in range(start_idx, total_possible_windows):
        p0 = closes[d]
        if p0 <= 0:
            continue

        valid_windows += 1
        target_price = p0 * (1.0 + t_pct)
        stop_price = p0 * (1.0 - s_pct)
        target_reached = False

        for t in range(d + 1, min(d + h + 1, n)):
            o_t = opens[t]
            h_t = highs[t]
            l_t = lows[t]

            # 1. Open Gap Check
            if o_t <= stop_price:
                target_reached = False
                break
            if o_t >= target_price:
                target_reached = True
                break

            # 2. Intra-day Bar Check
            if l_t <= stop_price and h_t >= target_price:
                # Same-day ambiguity: conservative STOP_FIRST policy
                target_reached = False
                break
            if l_t <= stop_price:
                target_reached = False
                break
            if h_t >= target_price:
                target_reached = True
                break

        if target_reached:
            success_count += 1

    if valid_windows < MIN_REACH_PROB_WINDOWS:
        return 0.0

    prob = float(success_count / valid_windows)
    _TARGET_STOP_REACH_CACHE[cache_key] = prob
    _TARGET_STOP_REACH_CACHE[cache_key_compat] = prob
    return prob


def get_reach_prob(
    ticker: str,
    target_pct: float,
    holding_days: int,
    price_df: Optional[pd.DataFrame] = None,
    lookback_days: int = 504,
    stop_pct: Optional[float] = None,
) -> float:
    """
    Calculate empirical reach probability.
    If stop_pct is provided (> 0), computes target-before-stop reach probability.
    Otherwise, computes empirical gain reach probability: count(max_gain_d >= target_pct) / total_windows.
    If insufficient historical evidence exists, returns 0.0 (does not manufacture 35%).
    """
    if stop_pct is not None and float(stop_pct) > 0:
        return get_reach_prob_target_before_stop(
            ticker=ticker,
            target_pct=target_pct,
            stop_pct=float(stop_pct),
            holding_days=holding_days,
            price_df=price_df,
            lookback_days=lookback_days,
        )

    gains = get_reach_prob_distribution(ticker, holding_days, price_df, lookback_days)
    if len(gains) < MIN_REACH_PROB_WINDOWS:
        logger.debug(
            "Insufficient historical gain evidence for %s: windows=%d (min %d). Empirical reach prob is 0.0.",
            ticker, len(gains), MIN_REACH_PROB_WINDOWS
        )
        return 0.0
    return float(np.sum(gains >= target_pct) / len(gains))


def calculate_targets(
    ticker: str,
    entry_price: float,
    atr_14: float,
    stop_loss: float,
    strategy_name: str,
    price_df: Optional[pd.DataFrame] = None,
    mock_reach_probs: Optional[Tuple[float, float, float]] = None,
    override_targets: Optional[Tuple[float, float, float]] = None,
    sector: Optional[str] = None,
) -> TargetCalculationResult:
    """
    Full 3-layer target calculation and reach-probability filtering engine.

    Layer 1: Computes ATR targets and fixed-floor targets per strategy, selecting max().
    Layer 2: Applies reach-probability decision tree with target-before-stop and survivorship bias mitigation.
    Layer 3: Computes honest weighted scale-out risk-to-reward ratio.
    """
    strat_key = normalize_strategy_name(strategy_name)
    cfg = STRATEGY_TARGET_CONFIG[strat_key]

    entry = float(entry_price)
    atr = max(0.0, float(atr_14))
    stop = float(stop_loss)

    # Upstream Entry and Stop-Loss Validation (0 < stop < entry)
    # Fail closed with is_valid=False if entry or stop is invalid. Never silently repair invalid financial inputs.
    if entry <= 0:
        return TargetCalculationResult(
            target_1=None, target_2=None, target_3=None,
            target_1_atr=0.0, target_2_atr=0.0, target_3_atr=0.0,
            target_1_pct=None, target_2_pct=None, target_3_pct=None,
            reach_prob_t1=0.0, reach_prob_t2=0.0, reach_prob_t3=0.0,
            scale_out_weights="0/0/0",
            weighted_scaleout_rr=0.0,
            weighted_rr_honest=0.0,
            is_valid=False,
            rejection_reason=f"Non-positive entry price: {entry}",
            reach_prob_raw=0.0, reach_prob_adjusted=0.0,
            target_1_return_decimal=None, target_2_return_decimal=None, target_3_return_decimal=None,
        )

    if stop <= 0 or stop >= entry:
        return TargetCalculationResult(
            target_1=None, target_2=None, target_3=None,
            target_1_atr=0.0, target_2_atr=0.0, target_3_atr=0.0,
            target_1_pct=None, target_2_pct=None, target_3_pct=None,
            reach_prob_t1=0.0, reach_prob_t2=0.0, reach_prob_t3=0.0,
            scale_out_weights="0/0/0",
            weighted_scaleout_rr=0.0,
            weighted_rr_honest=0.0,
            is_valid=False,
            rejection_reason=f"Invalid stop loss: stop ${stop:.2f} must satisfy 0 < stop < entry (${entry:.2f})",
            reach_prob_raw=0.0, reach_prob_adjusted=0.0,
            target_1_return_decimal=None, target_2_return_decimal=None, target_3_return_decimal=None,
        )

    risk = entry - stop
    if risk <= 0:
        return TargetCalculationResult(
            target_1=None, target_2=None, target_3=None,
            target_1_atr=0.0, target_2_atr=0.0, target_3_atr=0.0,
            target_1_pct=None, target_2_pct=None, target_3_pct=None,
            reach_prob_t1=0.0, reach_prob_t2=0.0, reach_prob_t3=0.0,
            scale_out_weights="0/0/0",
            weighted_scaleout_rr=0.0,
            weighted_rr_honest=0.0,
            is_valid=False,
            rejection_reason=f"Invalid risk: entry ${entry:.2f} - stop ${stop:.2f} <= 0",
            reach_prob_raw=0.0, reach_prob_adjusted=0.0,
            target_1_return_decimal=None, target_2_return_decimal=None, target_3_return_decimal=None,
        )

    stop_pct = risk / entry

    # Layer 1 — Candidate Targets (max of fixed percentage floor and ATR multiple)
    # Use full float precision for internal math; round only at output boundary
    t1_atr = entry + (cfg["atr_k1"] * atr)
    t2_atr = entry + (cfg["atr_k2"] * atr)
    t3_atr = entry + (cfg["atr_k3"] * atr)

    if override_targets is not None:
        cand_t1, cand_t2, cand_t3 = override_targets
        if not (entry < cand_t1 < cand_t2 < cand_t3):
            return TargetCalculationResult(
                target_1=None, target_2=None, target_3=None,
                target_1_atr=0.0, target_2_atr=0.0, target_3_atr=0.0,
                target_1_pct=None, target_2_pct=None, target_3_pct=None,
                reach_prob_t1=0.0, reach_prob_t2=0.0, reach_prob_t3=0.0,
                scale_out_weights="0/0/0",
                weighted_scaleout_rr=0.0,
                weighted_rr_honest=0.0,
                is_valid=False,
                rejection_reason=f"Invalid override targets ordering: entry={entry:.2f}, t1={cand_t1:.2f}, t2={cand_t2:.2f}, t3={cand_t3:.2f}",
                reach_prob_raw=0.0, reach_prob_adjusted=0.0,
                target_1_return_decimal=None, target_2_return_decimal=None, target_3_return_decimal=None,
            )
    else:
        cand_t1 = max(entry * (1.0 + cfg["fixed_t1"]), t1_atr)
        cand_t2 = max(entry * (1.0 + cfg["fixed_t2"]), t2_atr)
        cand_t3 = max(entry * (1.0 + cfg["fixed_t3"]), t3_atr)

        # Ensure strict target ordering: entry < cand_t1 < cand_t2 < cand_t3
        # In accordance with quantitative principles: do not manufacture arbitrary +1% targets; reject if non-monotonic
        if not (entry < cand_t1 < cand_t2 < cand_t3):
            return TargetCalculationResult(
                target_1=None, target_2=None, target_3=None,
                target_1_atr=0.0, target_2_atr=0.0, target_3_atr=0.0,
                target_1_pct=None, target_2_pct=None, target_3_pct=None,
                reach_prob_t1=0.0, reach_prob_t2=0.0, reach_prob_t3=0.0,
                scale_out_weights="0/0/0",
                weighted_scaleout_rr=0.0,
                weighted_rr_honest=0.0,
                is_valid=False,
                rejection_reason=f"Invalid target model ordering: entry={entry:.2f}, t1={cand_t1:.2f}, t2={cand_t2:.2f}, t3={cand_t3:.2f}",
                reach_prob_raw=0.0, reach_prob_adjusted=0.0,
                target_1_return_decimal=None, target_2_return_decimal=None, target_3_return_decimal=None,
            )

    t1_ret_dec = (cand_t1 - entry) / entry
    t2_ret_dec = (cand_t2 - entry) / entry
    t3_ret_dec = (cand_t3 - entry) / entry

    # Layer 2 — Reach Probabilities with Target-Before-Stop & Survivorship Bias Adjustment
    if mock_reach_probs is not None:
        rp_t1, rp_t2, rp_t3 = mock_reach_probs
        raw_t1 = rp_t1
    else:
        hold = cfg["hold_days"]
        from src.filters.survivorship_bias import compute_reach_prob_with_survivorship
        rp_t1, raw_t1 = compute_reach_prob_with_survivorship(ticker, t1_ret_dec, hold, price_df, sector=sector, stop_pct=stop_pct)
        rp_t2, _ = compute_reach_prob_with_survivorship(ticker, t2_ret_dec, hold, price_df, sector=sector, stop_pct=stop_pct)
        rp_t3, _ = compute_reach_prob_with_survivorship(ticker, t3_ret_dec, hold, price_df, sector=sector, stop_pct=stop_pct)

    # Monotonic reach probability enforcement: farther targets cannot have higher reach prob over same holding period
    rp_t2 = min(rp_t2, rp_t1)
    rp_t3 = min(rp_t3, rp_t2)

    t1_min = cfg["t1_min"]
    t2_min = cfg["t2_min"]
    t3_min = cfg.get("t3_min", T3_REACH_PROB_SURVIVAL_THRESHOLD)

    # Layer 2 & 3 — Target Hierarchy & Scale-Out Weights (P0-4)
    # T1 must meet t1_min for higher targets to be considered.
    # No T2 without T1; no T3 without T1 and T2.
    t1_survives = (rp_t1 >= t1_min)
    t2_survives = t1_survives and (rp_t2 >= t2_min)
    t3_survives = t2_survives and (rp_t3 >= t3_min)

    rejection_reason = None
    if t1_survives and t2_survives and t3_survives:
        # All three survive: 50% at T1, 30% at T2, 20% at T3
        t1, t2, t3 = round(cand_t1, 2), round(cand_t2, 2), round(cand_t3, 2)
        weights_label = "50/30/20"
        weighted_reward = 0.50 * (cand_t1 - entry) + 0.30 * (cand_t2 - entry) + 0.20 * (cand_t3 - entry)
        t1_pct = round(t1_ret_dec * 100.0, 1)
        t2_pct = round(t2_ret_dec * 100.0, 1)
        t3_pct = round(t3_ret_dec * 100.0, 1)
        t1_dec = round(t1_ret_dec, 4)
        t2_dec = round(t2_ret_dec, 4)
        t3_dec = round(t3_ret_dec, 4)
    elif t1_survives and t2_survives and not t3_survives:
        # T1 and T2 survive, T3 pruned: 60% at T1, 40% at T2, 0% at T3
        t1, t2, t3 = round(cand_t1, 2), round(cand_t2, 2), None
        weights_label = "60/40/0"
        weighted_reward = 0.60 * (cand_t1 - entry) + 0.40 * (cand_t2 - entry)
        t1_pct = round(t1_ret_dec * 100.0, 1)
        t2_pct = round(t2_ret_dec * 100.0, 1)
        t3_pct = None
        t1_dec = round(t1_ret_dec, 4)
        t2_dec = round(t2_ret_dec, 4)
        t3_dec = None
        rejection_reason = f"T3 pruned (reach prob {rp_t3:.1%} < threshold {t3_min:.1%})"
    elif t1_survives and not t2_survives:
        # Only T1 survives: 70% at T1, 30% runner to breakeven
        t1, t2, t3 = round(cand_t1, 2), None, None
        weights_label = "70/30/0"
        weighted_reward = 0.70 * (cand_t1 - entry)
        t1_pct = round(t1_ret_dec * 100.0, 1)
        t2_pct = None
        t3_pct = None
        t1_dec = round(t1_ret_dec, 4)
        t2_dec = None
        t3_dec = None
        rejection_reason = f"T2/T3 pruned (T2 reach prob {rp_t2:.1%} < min {t2_min:.1%})"
    else:
        # T1 did not survive its minimum: T1 kept as sole indicative target; T2 & T3 pruned
        t1, t2, t3 = round(cand_t1, 2), None, None
        weights_label = "70/30/0"
        weighted_reward = 0.70 * (cand_t1 - entry)
        t1_pct = round(t1_ret_dec * 100.0, 1)
        t2_pct = None
        t3_pct = None
        t1_dec = round(t1_ret_dec, 4)
        t2_dec = None
        t3_dec = None
        rejection_reason = f"T1 reach prob {rp_t1:.1%} below minimum {t1_min:.1%}"

    weighted_scaleout_rr = round(weighted_reward / risk, 2)

    return TargetCalculationResult(
        target_1=t1,
        target_2=t2,
        target_3=t3,
        target_1_atr=round(t1_atr, 2),
        target_2_atr=round(t2_atr, 2),
        target_3_atr=round(t3_atr, 2),
        target_1_pct=t1_pct,
        target_2_pct=t2_pct,
        target_3_pct=t3_pct,
        reach_prob_t1=round(rp_t1, 4),
        reach_prob_t2=round(rp_t2, 4),
        reach_prob_t3=round(rp_t3, 4),
        scale_out_weights=weights_label,
        weighted_scaleout_rr=weighted_scaleout_rr,
        weighted_rr_honest=weighted_scaleout_rr,
        is_valid=True,
        rejection_reason=rejection_reason,
        reach_prob_raw=round(raw_t1, 4),
        reach_prob_adjusted=round(rp_t1, 4),
        target_1_return_decimal=t1_dec,
        target_2_return_decimal=t2_dec,
        target_3_return_decimal=t3_dec,
    )
