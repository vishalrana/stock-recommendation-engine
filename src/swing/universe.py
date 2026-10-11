"""
Swing Signals — Universe Definitions
====================================
Pure and point-in-time universe definitions:
- U1: Today's broad security master (production universe), restricted to Liquid(t).
- U2: Point-in-time S&P 500 membership (data/reference/sp500_ticker_start_end.csv), restricted to Liquid(t).
- U1-top500: Top 500 U1 names by ADV20(t).
- Liquid(t): Unadjusted close >= $5, ADV20 >= $5,000,000 nominal, and >= 252 bars of history.
- breadth(t): Share of the evaluated universe's Liquid names with Close > MA200.
"""

import os
from datetime import date
from typing import Dict, Iterable, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMBERSHIP_CSV = os.path.join(PROJECT_ROOT, "data", "reference", "sp500_ticker_start_end.csv")

from src.research.survivorship import load_membership, yahoo_symbol


def get_u1_universe() -> List[str]:
    """U1 broad security master tickers (common equities, excluding sector ETFs)."""
    try:
        from scripts.validate_backtest_pipeline import production_universe
        stocks, _ = production_universe()
        return stocks
    except Exception:
        import jobs.generate_signals as gs
        tickers, _, _ = gs.load_universe("expanded")
        from jobs.strategies.sector_rotation import SECTOR_ETFS
        return sorted({yahoo_symbol(t) for t in tickers if t and yahoo_symbol(t) not in SECTOR_ETFS})


def get_u2_members_on_date(
    d: date,
    membership: Optional[pd.DataFrame] = None,
    recycled_first_bars: Optional[Dict[str, date]] = None,
) -> Set[str]:
    """
    Symbols that are S&P 500 members on date `d` per membership table.
    Excludes member-days before first bar for recycled/renamed symbols.
    """
    m = load_membership() if membership is None else membership
    ts = pd.Timestamp(d)
    valid_members = set()

    for row in m.itertuples(index=False):
        end = row.end_date if pd.notna(row.end_date) else pd.Timestamp.max
        if row.start_date <= ts <= end:
            t = row.ticker
            if recycled_first_bars and t in recycled_first_bars:
                first_bar = recycled_first_bars[t]
                if d < first_bar:
                    continue  # exclude member-days before first bar
            valid_members.add(t)

    return valid_members


def check_liquid(
    unadj_close: float,
    adv20: float,
    history_len: int,
) -> bool:
    """
    Liquid(t):
    - unadjusted close >= $5.0
    - ADV20 >= $5,000,000 nominal
    - >= 252 sessions of history
    """
    return bool(
        unadj_close >= 5.0 and
        adv20 >= 5_000_000.0 and
        history_len >= 252
    )


def compute_breadth(
    liquid_tickers: Iterable[str],
    close_dict: Dict[str, float],
    ma200_dict: Dict[str, float],
) -> float:
    """
    breadth(t) = share of the evaluated universe's Liquid names with Close > MA200.
    """
    liquid_list = list(liquid_tickers)
    if not liquid_list:
        return 0.0

    above_count = 0
    total = 0
    for t in liquid_list:
        c = close_dict.get(t)
        ma = ma200_dict.get(t)
        if c is not None and ma is not None and np.isfinite(c) and np.isfinite(ma):
            total += 1
            if c > ma:
                above_count += 1

    return float(above_count / total) if total > 0 else 0.0


def select_u1_top500(
    u1_liquid_tickers: Iterable[str],
    adv20_dict: Dict[str, float],
) -> Set[str]:
    """Select the 500 U1 liquid tickers with highest ADV20."""
    sorted_by_adv = sorted(
        u1_liquid_tickers,
        key=lambda t: adv20_dict.get(t, 0.0),
        reverse=True,
    )
    return set(sorted_by_adv[:500])
