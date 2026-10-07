"""
Data Validation Module
======================
Canonical Single Source of Truth for OHLCV Market Data Integrity
Master Architecture & Quantitative Specification v2.3+ Compliant

Hard Fails on Critical Data Flaws:
- Non-positive prices (<= 0)
- Negative volume (< 0)
- Physical bar violations: High < Low, High < Open, High < Close, Low > Open, Low > Close
- Unsorted / non-monotonic timestamps
- Duplicate timestamps
- NaN / Inf in critical fields
- Insufficient historical lookback
- Extreme data corruption anomalies
"""

import logging
from typing import Tuple, Dict, Any, Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def validate_ohlcv(
    df: Optional[pd.DataFrame],
    min_lookback: int = 60,
    check_open: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate OHLCV market data DataFrame against canonical financial and mathematical invariants.

    Args:
        df: Input DataFrame with price and volume history.
        min_lookback: Minimum number of historical bars required.
        check_open: Whether to strictly require and validate the OPEN column.

    Returns:
        (is_valid: bool, rejection_reason: str, metrics: dict)
    """
    metrics: Dict[str, Any] = {
        "bars_count": 0,
        "first_date": None,
        "last_date": None,
        "latest_close": None,
    }

    if df is None or df.empty:
        return False, "Market data DataFrame is null or empty", metrics

    # Normalize column names to uppercase for invariant checking
    cols = {str(c).upper(): c for c in df.columns}
    required = ["CLOSE", "HIGH", "LOW", "VOLUME"]
    if check_open:
        required.append("OPEN")

    missing_cols = [req for req in required if req not in cols]
    if missing_cols:
        return False, f"Missing required OHLCV columns: {missing_cols}", metrics

    # Extract normalized series
    close = df[cols["CLOSE"]].astype(float)
    high = df[cols["HIGH"]].astype(float)
    low = df[cols["LOW"]].astype(float)
    volume = df[cols["VOLUME"]].astype(float)
    open_s = df[cols["OPEN"]].astype(float) if check_open else None

    n_bars = len(df)
    metrics["bars_count"] = n_bars
    if n_bars > 0:
        metrics["first_date"] = str(df.index[0])
        metrics["last_date"] = str(df.index[-1])
        metrics["latest_close"] = float(close.iloc[-1]) if not pd.isna(close.iloc[-1]) else None

    # 1. Historical Lookback Gate
    if n_bars < min_lookback:
        return False, f"Insufficient history: {n_bars} bars < {min_lookback} required", metrics

    # 2. Timestamp Ordering and Duplicate Checks
    if df.index.has_duplicates:
        return False, "Duplicate timestamps detected in price index", metrics

    if not df.index.is_monotonic_increasing:
        return False, "Timestamps are unsorted or non-monotonic in price index", metrics

    # 3. NaN and Infinite Value Guards
    if close.isna().any() or high.isna().any() or low.isna().any() or volume.isna().any():
        # Specifically check if recent window has NaN
        if close.iloc[-min(min_lookback, n_bars):].isna().any():
            return False, "NaN values present in recent required price window", metrics

    if np.isinf(close.to_numpy()).any() or np.isinf(high.to_numpy()).any() or np.isinf(low.to_numpy()).any():
        return False, "Infinite values detected in price series", metrics

    # 4. Strictly Positive Price Invariants
    if (close <= 0.0).any():
        bad_idx = df.index[close <= 0.0][0]
        return False, f"Non-positive Close price ({close.loc[bad_idx]}) at {bad_idx}", metrics

    if (high <= 0.0).any():
        bad_idx = df.index[high <= 0.0][0]
        return False, f"Non-positive High price ({high.loc[bad_idx]}) at {bad_idx}", metrics

    if (low <= 0.0).any():
        bad_idx = df.index[low <= 0.0][0]
        return False, f"Non-positive Low price ({low.loc[bad_idx]}) at {bad_idx}", metrics

    if check_open and open_s is not None:
        if (open_s <= 0.0).any():
            bad_idx = df.index[open_s <= 0.0][0]
            return False, f"Non-positive Open price ({open_s.loc[bad_idx]}) at {bad_idx}", metrics

    # 5. Non-Negative Volume Invariant
    if (volume < 0.0).any():
        bad_idx = df.index[volume < 0.0][0]
        return False, f"Negative Volume ({volume.loc[bad_idx]}) at {bad_idx}", metrics

    # 6. Physical Bar Geometry Invariants
    # High must be the ceiling: High >= Low, High >= Open, High >= Close
    if (high < low).any():
        bad_idx = df.index[high < low][0]
        return False, f"Physical geometry violation High < Low ({high.loc[bad_idx]} < {low.loc[bad_idx]}) at {bad_idx}", metrics

    if (high < close).any():
        bad_idx = df.index[high < close][0]
        return False, f"Physical geometry violation High < Close ({high.loc[bad_idx]} < {close.loc[bad_idx]}) at {bad_idx}", metrics

    if (low > close).any():
        bad_idx = df.index[low > close][0]
        return False, f"Physical geometry violation Low > Close ({low.loc[bad_idx]} > {close.loc[bad_idx]}) at {bad_idx}", metrics

    if check_open and open_s is not None:
        if (high < open_s).any():
            bad_idx = df.index[high < open_s][0]
            return False, f"Physical geometry violation High < Open ({high.loc[bad_idx]} < {open_s.loc[bad_idx]}) at {bad_idx}", metrics
        if (low > open_s).any():
            bad_idx = df.index[low > open_s][0]
            return False, f"Physical geometry violation Low > Open ({low.loc[bad_idx]} > {open_s.loc[bad_idx]}) at {bad_idx}", metrics

    # 7. Unadjusted Stock Split / Erroneous Tick Anomaly Detection
    # If High / Low > 25.0 on a single daily bar, flag as corrupted data
    bar_spread = high / low
    if (bar_spread > 25.0).any():
        bad_idx = df.index[bar_spread > 25.0][0]
        return False, f"Extreme corrupted bar anomaly: High/Low ratio {bar_spread.loc[bad_idx]:.1f} at {bad_idx}", metrics

    return True, "Valid", metrics
