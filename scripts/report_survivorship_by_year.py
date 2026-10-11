"""
Survivorship by Year and Recycled Symbols Reporter (2008-2022)
==============================================================
Calculates the share of U2 (point-in-time S&P 500) member-days without a bar by year,
flags recycled/renamed symbols whose first yfinance bar comes after their membership start,
and outputs: outputs/swing/survivorship_by_year.json.
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.research.survivorship import load_membership, yahoo_symbol
from src.trade_hygiene import SessionCalendar

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

REPORT_PATH = os.path.join(PROJECT_ROOT, "outputs", "swing", "survivorship_by_year.json")
PRICE_DIR = os.path.join(PROJECT_ROOT, "data", "backtest_history_long")


def evaluate_survivorship_by_year(
    price_dir: str = PRICE_DIR,
    report_path: str = REPORT_PATH,
) -> Dict[str, object]:
    """Evaluate U2 membership coverage year by year (2008-2022)."""
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    m = load_membership()

    # Get trading sessions 2008-2022 from NYSE calendar
    from datetime import date
    from src.utils.market_date import _nyse_holidays
    h = _nyse_holidays(date(2007, 1, 1), date(2023, 1, 1))
    sessions_all = pd.bdate_range("2008-01-01", "2022-12-31", holidays=h, freq="C")

    # Scan available price files to determine first/last bar and bar dates
    ticker_first_bar: Dict[str, pd.Timestamp] = {}
    ticker_bar_dates: Dict[str, Set[pd.Timestamp]] = {}

    if os.path.exists(price_dir):
        for f in os.listdir(price_dir):
            if f.endswith(".parquet") and not f.startswith("short_interest") and not f.startswith("insider"):
                t = f[:-8].upper()
                try:
                    df = pd.read_parquet(os.path.join(price_dir, f), columns=["CLOSE"])
                    if not df.empty:
                        ticker_first_bar[t] = df.index[0]
                        ticker_bar_dates[t] = set(df.index)
                except Exception:
                    pass

    # Flag recycled symbols: first yfinance bar comes strictly after membership start
    recycled_symbols = []
    for row in m.itertuples(index=False):
        t = row.ticker
        if t in ticker_first_bar:
            first_bar = ticker_first_bar[t]
            if first_bar > row.start_date + pd.Timedelta(days=30):  # allow 30-day grace for IPO/listing timing
                recycled_symbols.append({
                    "ticker": t,
                    "membership_start": row.start_date.strftime("%Y-%m-%d"),
                    "first_bar": first_bar.strftime("%Y-%m-%d"),
                    "gap_days": (first_bar - row.start_date).days,
                })

    recycled_set = {r["ticker"] for r in recycled_symbols}

    # Year-by-year analysis
    by_year: Dict[int, Dict[str, object]] = {}
    for year in range(2008, 2023):
        year_sessions = sessions_all[sessions_all.year == year]
        n_sessions = len(year_sessions)
        total_member_days = 0
        missing_member_days = 0
        missing_due_to_recycled = 0

        for row in m.itertuples(index=False):
            t = row.ticker
            end = row.end_date if pd.notna(row.end_date) else pd.Timestamp.max
            inside = year_sessions[(year_sessions >= row.start_date) & (year_sessions <= end)]
            if len(inside) == 0:
                continue

            # If symbol is recycled, exclude member-days before first bar per Section 1:
            # "Flag symbols whose first yfinance bar comes after their membership start and exclude member-days before that first bar"
            if t in recycled_set and t in ticker_first_bar:
                first_b = ticker_first_bar[t]
                valid_inside = inside[inside >= first_b]
                missing_due_to_recycled += len(inside) - len(valid_inside)
                inside = valid_inside
                if len(inside) == 0:
                    continue

            total_member_days += len(inside)
            dates = ticker_bar_dates.get(t, set())
            n_missing = sum(1 for s in inside if s not in dates)
            missing_member_days += n_missing

        missing_pct = round(100.0 * missing_member_days / total_member_days, 2) if total_member_days > 0 else 0.0
        by_year[year] = {
            "trading_sessions": n_sessions,
            "total_member_days": total_member_days,
            "missing_member_days": missing_member_days,
            "missing_share_pct": missing_pct,
            "excluded_recycled_member_days": missing_due_to_recycled,
        }

    out = {
        "evaluation_window": "2008-01-01 to 2022-12-31",
        "recycled_symbols_count": len(recycled_symbols),
        "recycled_symbols": recycled_symbols,
        "by_year": by_year,
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    logger.info(f"Survivorship by year written to {report_path}")
    return out


if __name__ == "__main__":
    evaluate_survivorship_by_year()
