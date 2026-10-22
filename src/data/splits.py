"""
Unadjusted splits: Yahoo sometimes lists a split but leaves the price history unadjusted (IGR,
1-for-3 on 2026-09-08: a false +147% move). The live price cache and the backtest harness both use
this check; a flagged ticker is data that cannot be trusted across the split.
"""

import math

import pandas as pd


def unadjusted_split(closes: pd.Series, split_date, ratio: float) -> bool:
    """
    True when the close-to-close move into the split date, or into the next session (Yahoo's date
    can be off by one), is nearer the unadjusted move, 1 / ratio, than no change on a log scale.
    ratio is Yahoo's "Stock Splits" value: 2.0 for a 2-for-1, 1/3 for a 1-for-3 reverse split.
    """
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return False
    if not (ratio > 0) or abs(ratio - 1.0) < 1e-9:
        return False
    c = closes.dropna()
    c = c[c > 0].sort_index()
    target = -math.log(ratio)
    i = int(c.index.searchsorted(pd.Timestamp(split_date)))
    for j in (i, i + 1):
        if 1 <= j < len(c):
            move = math.log(float(c.iloc[j]) / float(c.iloc[j - 1]))
            if abs(move - target) < abs(move):
                return True
    return False


def flagged_split_dates(closes: pd.Series, splits) -> list:
    """The split dates (ISO) in `splits` [(date, ratio), ...] that the history does not reflect."""
    return sorted(str(d)[:10] for d, r in splits if unadjusted_split(closes, d, r))
