"""
Survivorship Bias Mitigation Module
====================================
Prevents backtest look-ahead bias and reach probability inflation by incorporating
delisted ticker historical paths and applying strategy backtest haircuts.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

logger = logging.getLogger(__name__)

# Canonical Single Source of Truth
from src.quant_config import REACH_PROB_FALLBACK_HAIRCUT


# In-memory cache for static delisted tickers
_DELISTED_TICKERS_CACHE: Optional[List[Dict[str, Any]]] = None


def load_delisted_tickers() -> List[Dict[str, Any]]:
    """Loads the static registry of 50+ delisted S&P 500 tickers."""
    global _DELISTED_TICKERS_CACHE
    if _DELISTED_TICKERS_CACHE is not None:
        return _DELISTED_TICKERS_CACHE

    root = Path(__file__).resolve().parent.parent.parent
    config_path = root / "config" / "delisted_tickers.json"
    if not config_path.exists():
        _DELISTED_TICKERS_CACHE = []
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            _DELISTED_TICKERS_CACHE = data.get("delisted_tickers", [])
            return _DELISTED_TICKERS_CACHE
    except Exception as e:
        logger.warning(f"Failed loading delisted_tickers.json: {e}")
        return []


def get_delisted_tickers_by_sector(sector: str) -> List[str]:
    """Returns delisted ticker symbols matching a given sector."""
    if not sector:
        return []
    sec_lower = str(sector).strip().lower()
    tickers = []
    if not sec_lower:
        return []
    for d in load_delisted_tickers():
        d_sec = str(d.get("sector", "")).strip().lower()
        # An empty registry sector is a substring of every sector; it must never match.
        if not d_sec:
            continue
        if sec_lower in d_sec or d_sec in sec_lower:
            tickers.append(d["ticker"])
    return tickers


def compute_reach_prob_with_survivorship(
    ticker: str,
    target_pct: float,
    holding_days: int,
    price_df: Optional[Any] = None,
    sector: Optional[str] = None,
    delisted_reach_override: Optional[float] = None,
    stop_pct: Optional[float] = None,
    as_of_date: Optional[str] = None,
) -> Any:
    """
    Computes reach probability incorporating survivorship bias mitigation.

    MODELING ASSUMPTIONS:
    1. 70/30 Empirical Blend: Active ticker empirical path is weighted at 70% and
       sector-matched historical delisted constituents are weighted at 30% to account
       for historical mortality and downside survivorship inflation.
    2. Fallback Haircut (0.92): When historical delisted price records for the sector
       are unavailable, active empirical reach probability is discounted by an 8% haircut
       (multiplier 0.92 = REACH_PROB_FALLBACK_HAIRCUT from src.quant_config).
    (No multiplicative haircut is applied to expectancy: scaling a negative expectancy
    toward zero would make it look better, the opposite of a survivorship correction.)

    Returns:
        ReachProbabilityResult (iterable 2-tuple backwards compatible with (adjusted_prob, raw_prob))
        exposing .status, .provenance, .sample_count, .delisted_samples, and .as_of_date.
    """
    from src.strategies.target_calculator import (
        get_reach_prob_structured,
        ReachProbabilityResult,
        STATUS_VALID_ESTIMATE,
        STATUS_CUTOFF_FAILURE,
        STATUS_INVALID_INPUT,
        STATUS_INSUFFICIENT_OBSERVATIONS,
    )

    # 1. Compute raw reach probability on the current active ticker using structured evaluator
    raw_res = get_reach_prob_structured(
        ticker,
        target_pct,
        holding_days,
        price_df=price_df,
        stop_pct=stop_pct,
        as_of_date=as_of_date,
    )

    # Fail closed if cutoff evaluation or input validation failed
    if raw_res.status in (STATUS_CUTOFF_FAILURE, STATUS_INVALID_INPUT):
        return raw_res

    raw_reach = raw_res.raw_prob

    # If active ticker has insufficient observations, preserve status
    if raw_res.status == STATUS_INSUFFICIENT_OBSERVATIONS:
        return ReachProbabilityResult(
            adjusted_prob=0.0,
            raw_prob=0.0,
            status=STATUS_INSUFFICIENT_OBSERVATIONS,
            provenance="insufficient_active_observations",
            sample_count=raw_res.sample_count,
            delisted_samples=0,
            as_of_date=raw_res.as_of_date,
        )

    # 2. Blend with delisted proxy if delisted sector history is explicitly overridden
    if delisted_reach_override is not None:
        try:
            avg_delisted = float(delisted_reach_override)
            blended = (0.70 * raw_reach) + (0.30 * avg_delisted)
            return ReachProbabilityResult(
                adjusted_prob=round(blended, 4),
                raw_prob=round(raw_reach, 4),
                status=STATUS_VALID_ESTIMATE,
                provenance="empirical_override_delisted_blend",
                sample_count=raw_res.sample_count,
                delisted_samples=1,
                as_of_date=raw_res.as_of_date,
                effective_samples=raw_res.effective_samples,
                ci_low=raw_res.ci_low,
                ci_high=raw_res.ci_high,
            )
        except (ValueError, TypeError):
            pass

    # 3. Lookup sector-matched historical delisted constituents
    delisted_same_sector = get_delisted_tickers_by_sector(sector) if sector else []
    
    if delisted_same_sector:
        delisted_reaches = []
        for dt in delisted_same_sector[:3]:
            try:
                dt_res = get_reach_prob_structured(
                    dt,
                    target_pct,
                    holding_days,
                    stop_pct=stop_pct,
                    as_of_date=as_of_date,
                )
                # A valid 0% reach rate is real evidence of failed paths; excluding it would
                # bias the survivorship blend upward, defeating its purpose.
                if dt_res.status == STATUS_VALID_ESTIMATE:
                    delisted_reaches.append(dt_res.raw_prob)
            except Exception:
                pass

        if delisted_reaches:
            avg_delisted = float(np.mean(delisted_reaches))
            blended = (0.70 * raw_reach) + (0.30 * avg_delisted)
            return ReachProbabilityResult(
                adjusted_prob=round(blended, 4),
                raw_prob=round(raw_reach, 4),
                status=STATUS_VALID_ESTIMATE,
                provenance="empirical_sector_delisted_blend",
                sample_count=raw_res.sample_count,
                delisted_samples=len(delisted_reaches),
                as_of_date=raw_res.as_of_date,
                effective_samples=raw_res.effective_samples,
                ci_low=raw_res.ci_low,
                ci_high=raw_res.ci_high,
            )

    # 4. Fallback modeling assumption: flat 8% haircut from canonical quant_config
    blended = raw_reach * REACH_PROB_FALLBACK_HAIRCUT
    return ReachProbabilityResult(
        adjusted_prob=round(blended, 4),
        raw_prob=round(raw_reach, 4),
        status=STATUS_VALID_ESTIMATE,
        provenance="fallback_haircut_assumption_delisted_unavailable",
        sample_count=raw_res.sample_count,
        delisted_samples=0,
        as_of_date=raw_res.as_of_date,
        effective_samples=raw_res.effective_samples,
        ci_low=raw_res.ci_low,
        ci_high=raw_res.ci_high,
    )

