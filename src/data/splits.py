"""
Unadjusted splits: Yahoo sometimes lists a split but leaves the price history unadjusted (IGR,
1-for-3 on 2026-09-08: a false +147% move). The live price cache and the backtest harness both use
this check; a flagged ticker is data that cannot be trusted across the split.
"""

import math
from typing import Optional

import pandas as pd

# Only splits of at least 5-for-4 (or 1-for-1.25) are checked. Yahoo also lists stock dividends and
# spin-off adjustments as "splits" of ratio 1.007-1.05: their unadjusted move (-0.7% to -5%) is the
# size of an ordinary day, so the move test cannot tell them apart (the 2026-10-22 sweep flagged
# SCCO, GAB, JRI and the harness truncated BDX from 2022 on them); an unadjusted one is a basis
# error no larger than a dividend's.
MIN_SPLIT_LOG_RATIO = math.log(1.25)
# Yahoo's listed date can also fall one or two sessions after the jump (DHY, BTOC, REVB and MBRX one,
# SKIN two, in 2026-09/10). Those sessions count only when the move lands within a quarter of the
# unadjusted move: a real one lands almost exactly on it, while the looser "nearer than no change"
# test takes any fall of 10.6% or more near a 5-for-4 split (user decision, 2026-10-26 follow-up).
EARLY_SESSIONS_TOLERANCE = 0.25


def unadjusted_move(closes: pd.Series, split_date, ratio: float) -> Optional[dict]:
    """
    The session whose close-to-close move is the split's unadjusted move, -log(ratio) on a log scale,
    or None. ratio is Yahoo's "Stock Splits" value: 2.0 for a 2-for-1, 1/3 for a 1-for-3 reverse split.
      - the listed date and the next session (the jump can land a session after Yahoo's date): the
        move is nearer the unadjusted move than no change;
      - the two sessions before it (the jump can land up to two sessions before Yahoo's date): the
        move is within EARLY_SESSIONS_TOLERANCE x the unadjusted move.
    Returns {"session", "offset" (sessions from the listed date), "move", "target"}.
    """
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return None
    if not (ratio > 0) or abs(math.log(ratio)) < MIN_SPLIT_LOG_RATIO - 1e-12:
        return None
    c = closes.dropna()
    c = c[c > 0].sort_index()
    target = -math.log(ratio)
    i = int(c.index.searchsorted(pd.Timestamp(split_date)))
    for offset in (0, 1, -1, -2):
        j = i + offset
        if 1 <= j < len(c):
            move = math.log(float(c.iloc[j]) / float(c.iloc[j - 1]))
            limit = EARLY_SESSIONS_TOLERANCE * abs(target) if offset < 0 else abs(move)
            if abs(move - target) < limit:
                return {"session": c.index[j], "offset": offset, "move": move, "target": target}
    return None


def unadjusted_split(closes: pd.Series, split_date, ratio: float) -> bool:
    return unadjusted_move(closes, split_date, ratio) is not None


def flagged_split_dates(closes: pd.Series, splits) -> list:
    """For each split in `splits` [(date, ratio), ...] the history does not reflect, the first session
    (ISO) its unadjusted prices start: the listed date, or the earlier session the jump landed on."""
    out = []
    for d, r in splits:
        m = unadjusted_move(closes, d, r)
        if m is not None:
            out.append(str(min(pd.Timestamp(d), m["session"]))[:10])
    return sorted(out)
