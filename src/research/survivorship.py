"""
Survivorship gauge
==================
Share of historical S&P 500 member-days in the evaluation window for which the backtest's price
cache has no bar for that member. Membership comes from data/reference/sp500_ticker_start_end.csv
(fja05680/sp500, MIT licence, Copyright (c) 2019-2020 Farrell J. Aultman; see
data/reference/sp500_LICENSE.txt). Members that delisted, were acquired or changed symbol have no
yfinance history under their old symbol, so they show up as missing. A symbol change therefore
overstates the gap slightly (the company is in the cache under its new symbol); a delisting is a
true gap. The gauge measures how much of the index the backtest never saw; it does not correct for it.
"""

import os
from typing import Dict, Iterable, Optional

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SP500_MEMBERSHIP_CSV = os.path.join(PROJECT_ROOT, "data", "reference", "sp500_ticker_start_end.csv")


def yahoo_symbol(ticker: str) -> str:
    """BRK.B -> BRK-B (Yahoo's share-class separator)."""
    return str(ticker).strip().upper().replace(".", "-")


def load_membership(path: str = SP500_MEMBERSHIP_CSV) -> pd.DataFrame:
    m = pd.read_csv(path, dtype=str)
    m["ticker"] = m["ticker"].map(yahoo_symbol)
    m["start_date"] = pd.to_datetime(m["start_date"])
    m["end_date"] = pd.to_datetime(m["end_date"].fillna(""), errors="coerce")  # empty = current member
    return m


def survivorship_gauge(bar_dates: Dict[str, Iterable[pd.Timestamp]], sessions: Iterable[pd.Timestamp],
                       membership: Optional[pd.DataFrame] = None) -> Dict[str, object]:
    """
    bar_dates: ticker -> dates with a bar in the backtest cache. sessions: the evaluation sessions.
    Returns member-days, missing member-days, the missing share, and the members never seen.
    """
    m = load_membership() if membership is None else membership
    sessions = pd.DatetimeIndex(sorted(pd.Timestamp(s) for s in sessions))
    if not len(sessions):
        return {"member_days": 0, "missing_member_days": 0, "missing_share_pct": None}
    have = {yahoo_symbol(t): pd.DatetimeIndex(list(d)) for t, d in bar_dates.items()}
    total = missing = 0
    never_seen = set()
    for row in m.itertuples(index=False):
        end = row.end_date if pd.notna(row.end_date) else sessions[-1]
        inside = sessions[(sessions >= row.start_date) & (sessions <= end)]
        if not len(inside):
            continue
        total += len(inside)
        dates = have.get(row.ticker)
        n_missing = len(inside) if dates is None else int((~inside.isin(dates)).sum())
        missing += n_missing
        if n_missing == len(inside):
            never_seen.add(row.ticker)
    return {
        "member_days": int(total),
        "missing_member_days": int(missing),
        "missing_share_pct": round(100.0 * missing / total, 2) if total else None,
        "members_never_seen": len(never_seen),
        "members_never_seen_sample": sorted(never_seen)[:25],
        "source": "fja05680/sp500 (MIT), data/reference/sp500_ticker_start_end.csv",
    }
