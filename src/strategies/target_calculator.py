"""
Target Calculator — Strategy-Specific ATR Targets with Reach Probability Filtering
===================================================================================
Replaces fixed global targets with volatility-adjusted, empirical reach-probability
filtered targets to calculate honest weighted risk-to-reward ratios and position sizing.

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
    weighted_rr_honest: float
    is_valid: bool
    rejection_reason: Optional[str] = None
    reach_prob_raw: Optional[float] = None
    reach_prob_adjusted: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


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
    cache_dir = os.path.join("data", "cache", "reach_dists")
    cache_file = os.path.join(cache_dir, f"{ticker.upper()}.parquet")

    # Check disk cache if price_df not explicitly passed
    if price_df is None and os.path.exists(cache_file):
        try:
            cached_df = pd.read_parquet(cache_file)
            col_name = f"max_gain_{holding_days}d"
            if col_name in cached_df.columns:
                vals = cached_df[col_name].dropna().to_numpy(dtype=float)
                if len(vals) > 0:
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

    return arr


def get_reach_prob(
    ticker: str,
    target_pct: float,
    holding_days: int,
    price_df: Optional[pd.DataFrame] = None,
    lookback_days: int = 504,
) -> float:
    """
    Calculate empirical reach probability: count(max_gain_d >= target_pct) / total_windows.
    If insufficient historical gain evidence exists, returns 0.0 (does not manufacture 35%).
    """
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
    Layer 2: Applies reach-probability decision tree with survivorship bias mitigation.
    Layer 3: Computes honest weighted risk-to-reward ratio for Half-Kelly sizing.
    """
    strat_key = normalize_strategy_name(strategy_name)
    cfg = STRATEGY_TARGET_CONFIG[strat_key]

    entry = float(entry_price)
    atr = max(0.0, float(atr_14))
    stop = float(stop_loss)
    risk = max(0.01, entry - stop)

    # Layer 1 — Candidate Targets (max of fixed percentage floor and ATR multiple)
    t1_atr = round(entry + (cfg["atr_k1"] * atr), 2)
    t2_atr = round(entry + (cfg["atr_k2"] * atr), 2)
    t3_atr = round(entry + (cfg["atr_k3"] * atr), 2)

    if override_targets is not None:
        cand_t1, cand_t2, cand_t3 = override_targets
    else:
        cand_t1 = round(max(entry * (1.0 + cfg["fixed_t1"]), t1_atr), 2)
        cand_t2 = round(max(entry * (1.0 + cfg["fixed_t2"]), t2_atr), 2)
        cand_t3 = round(max(entry * (1.0 + cfg["fixed_t3"]), t3_atr), 2)

    t1_pct = (cand_t1 - entry) / entry if entry > 0 else 0.0
    t2_pct = (cand_t2 - entry) / entry if entry > 0 else 0.0
    t3_pct = (cand_t3 - entry) / entry if entry > 0 else 0.0

    # Layer 2 — Reach Probabilities with Survivorship Bias Adjustment
    if mock_reach_probs is not None:
        rp_t1, rp_t2, rp_t3 = mock_reach_probs
        raw_t1 = rp_t1
    else:
        hold = cfg["hold_days"]
        from src.filters.survivorship_bias import compute_reach_prob_with_survivorship
        rp_t1, raw_t1 = compute_reach_prob_with_survivorship(ticker, t1_pct, hold, price_df, sector=sector)
        rp_t2, _ = compute_reach_prob_with_survivorship(ticker, t2_pct, hold, price_df, sector=sector)
        rp_t3, _ = compute_reach_prob_with_survivorship(ticker, t3_pct, hold, price_df, sector=sector)

    t1_min = cfg["t1_min"]
    t2_min = cfg["t2_min"]

    # Layer 2 & 3 — Decision Tree & Honest Weighted R:R
    # Gate 1: Check Target 1 survival
    if rp_t1 < t1_min:
        # REJECT signal entirely (do not emit)
        rej_msg = (
            "Insufficient historical data for reach probability"
            if rp_t1 == 0.0 and raw_t1 == 0.0
            else f"ReachProb(T1) {rp_t1:.1%} < StrategyMin.T1 ({t1_min:.1%})"
        )
        return TargetCalculationResult(
            target_1=None,
            target_2=None,
            target_3=None,
            target_1_atr=t1_atr,
            target_2_atr=t2_atr,
            target_3_atr=t3_atr,
            target_1_pct=None,
            target_2_pct=None,
            target_3_pct=None,
            reach_prob_t1=round(rp_t1, 4),
            reach_prob_t2=round(rp_t2, 4),
            reach_prob_t3=round(rp_t3, 4),
            scale_out_weights="0/0/0",
            weighted_rr_honest=0.0,
            is_valid=False,
            rejection_reason=rej_msg,
            reach_prob_raw=round(raw_t1, 4),
            reach_prob_adjusted=round(rp_t1, 4),
        )

    t2_survives = (rp_t2 >= t2_min)
    t3_survives = (rp_t3 >= T3_REACH_PROB_SURVIVAL_THRESHOLD)

    # Determine surviving targets and scale-out weights per Master Spec v2.3+
    if t2_survives and t3_survives:
        # All three survive: 50% at T1, 30% at T2, 20% at T3
        t1, t2, t3 = cand_t1, cand_t2, cand_t3
        weights_label = "50/30/20"
        weighted_reward = 0.50 * (t1 - entry) + 0.30 * (t2 - entry) + 0.20 * (t3 - entry)
        t2_pct = round((t2 / entry - 1.0) * 100.0, 1)
        t3_pct = round((t3 / entry - 1.0) * 100.0, 1)
    elif t2_survives and not t3_survives:
        # T1 and T2 survive, T3 pruned: 60% at T1, 40% at T2, 0% at T3
        t1, t2, t3 = cand_t1, cand_t2, None
        weights_label = "60/40/0"
        weighted_reward = 0.60 * (t1 - entry) + 0.40 * (t2 - entry)
        t2_pct = round((t2 / entry - 1.0) * 100.0, 1)
        t3_pct = None
    else:
        # Only T1 survives (T2 failed): 70% at T1, 30% runner to breakeven
        t1, t2, t3 = cand_t1, None, None
        weights_label = "70/30/0"
        weighted_reward = 0.70 * (t1 - entry)
        t2_pct = None
        t3_pct = None

    weighted_rr = round(weighted_reward / risk, 2)

    return TargetCalculationResult(
        target_1=t1,
        target_2=t2,
        target_3=t3,
        target_1_atr=t1_atr,
        target_2_atr=t2_atr,
        target_3_atr=t3_atr,
        target_1_pct=round((t1 / entry - 1.0) * 100.0, 1),
        target_2_pct=t2_pct,
        target_3_pct=t3_pct,
        reach_prob_t1=round(rp_t1, 4),
        reach_prob_t2=round(rp_t2, 4),
        reach_prob_t3=round(rp_t3, 4),
        scale_out_weights=weights_label,
        weighted_rr_honest=weighted_rr,
        is_valid=True,
        reach_prob_raw=round(raw_t1, 4),
        reach_prob_adjusted=round(rp_t1, 4),
    )
