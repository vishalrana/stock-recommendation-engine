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

# Mandatory conservative haircut for backtest expectancy
SURVIVORSHIP_BIAS_HAIRCUT: float = 0.85  # 15% reduction


def load_delisted_tickers() -> List[Dict[str, Any]]:
    """Loads the static registry of 50+ delisted S&P 500 tickers."""
    root = Path(__file__).resolve().parent.parent.parent
    config_path = root / "config" / "delisted_tickers.json"
    if not config_path.exists():
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("delisted_tickers", [])
    except Exception as e:
        logger.warning(f"Failed loading delisted_tickers.json: {e}")
        return []


def get_delisted_tickers_by_sector(sector: str) -> List[str]:
    """Returns delisted ticker symbols matching a given sector."""
    if not sector:
        return []
    sec_lower = str(sector).strip().lower()
    tickers = []
    for d in load_delisted_tickers():
        d_sec = str(d.get("sector", "")).strip().lower()
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
) -> Tuple[float, float]:
    """
    Computes reach probability incorporating survivorship bias mitigation.
    # ponytail: 70/30 blend with sector proxy when available, or flat 8% haircut (0.92x).
    
    Returns:
        (adjusted_reach_prob, raw_reach_prob)
    """
    from src.strategies.target_calculator import get_reach_prob

    # 1. Compute raw reach probability on the current active ticker
    raw_reach = get_reach_prob(ticker, target_pct, holding_days, price_df)

    # 2. Blend with delisted proxy if delisted sector history is available
    if delisted_reach_override is not None:
        avg_delisted = float(delisted_reach_override)
        blended = (0.70 * raw_reach) + (0.30 * avg_delisted)
        return round(blended, 4), round(raw_reach, 4)

    delisted_same_sector = get_delisted_tickers_by_sector(sector) if sector else []
    
    if delisted_same_sector:
        delisted_reaches = []
        for dt in delisted_same_sector[:3]:
            try:
                rp = get_reach_prob(dt, target_pct, holding_days)
                if rp > 0:
                    delisted_reaches.append(rp)
            except Exception:
                pass

        if delisted_reaches:
            avg_delisted = float(np.mean(delisted_reaches))
            blended = (0.70 * raw_reach) + (0.30 * avg_delisted)
            return round(blended, 4), round(raw_reach, 4)

    # 3. Graceful fallback: flat 8% haircut (0.92 multiplier)
    blended = raw_reach * 0.92
    return round(blended, 4), round(raw_reach, 4)


def apply_expectancy_haircut(expectancy_pct: float) -> float:
    """Applies global 15% haircut to strategy historical expectancy."""
    return round(expectancy_pct * SURVIVORSHIP_BIAS_HAIRCUT, 4)
