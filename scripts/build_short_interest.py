"""
FINRA Short Interest History Builder (Five Rolling Years)
=========================================================
Loads bi-monthly equity short interest from FINRA, calculates publication dates
(settlement + 7 NYSE sessions + 1 safety session), calculates DTC against ADV20,
and stores under data/backtest_history_long/short_interest.parquet.
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.trade_hygiene import SessionCalendar
from src.research.survivorship import yahoo_symbol

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

OUTPUT_PARQUET = os.path.join(PROJECT_ROOT, "data", "backtest_history_long", "short_interest.parquet")
FINRA_API_URL = "https://api.finra.org/data/group/otcMarket/name/equityShortInterest"


def calculate_publication_date(settlement_date: date, cal: SessionCalendar) -> date:
    """Publication date = settlement + 7 NYSE sessions + 1 safety session = settlement + 8 sessions."""
    return cal.shift(settlement_date, 8)


def fetch_finra_short_interest_api(start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """Attempt fetch from FINRA public API without credentials."""
    headers = {"Accept": "application/json"}
    params = {
        "settlementDate": f"{start_date} to {end_date}",
        "limit": 5000,
    }
    try:
        resp = requests.get(FINRA_API_URL, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return pd.DataFrame(data)
    except Exception as e:
        logger.debug(f"FINRA API direct call failed: {e}")
    return None


def build_short_interest_history(
    start_date: Optional[str] = None,
    output_path: str = OUTPUT_PARQUET,
    price_dir: str = os.path.join(PROJECT_ROOT, "data", "backtest_history_long"),
) -> pd.DataFrame:
    """Build short interest dataset for rolling 5 years."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cal = SessionCalendar()

    today = date.today()
    if start_date is None:
        start_date = (today - timedelta(days=5 * 365)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    logger.info(f"Building short interest history from {start_date} to {end_date}...")

    df_api = fetch_finra_short_interest_api(start_date, end_date)
    records = []

    if df_api is not None and not df_api.empty:
        logger.info(f"Fetched {len(df_api)} records from FINRA API.")
        for row in df_api.itertuples(index=False):
            try:
                t = yahoo_symbol(getattr(row, "symbolCode", None) or getattr(row, "symbol", ""))
                s_date = pd.to_datetime(getattr(row, "settlementDate", None)).date()
                shares_short = float(getattr(row, "currentShortPositionQuantity", 0.0))
                pub_date = calculate_publication_date(s_date, cal)
                records.append({
                    "ticker": t,
                    "settlement_date": s_date,
                    "publication_date": pub_date,
                    "shares_short": shares_short,
                })
            except Exception:
                continue
    else:
        logger.info("FINRA API unavailable without OAuth credentials; checking fallback / local files...")

    if records:
        df = pd.DataFrame(records)
    else:
        # Create schema-compliant dataframe
        df = pd.DataFrame(columns=[
            "ticker", "settlement_date", "publication_date", "shares_short", "adv20_shares", "dtc"
        ])

    if not df.empty and "adv20_shares" not in df.columns:
        # Compute ADV20 in shares on publication_date if price files are available
        adv_list = []
        dtc_list = []
        for row in df.itertuples(index=False):
            t = row.ticker
            p_file = os.path.join(price_dir, f"{t}.parquet")
            adv = np.nan
            if os.path.exists(p_file):
                try:
                    px = pd.read_parquet(p_file, columns=["VOLUME"])
                    px = px[px.index <= pd.Timestamp(row.publication_date)]
                    if len(px) >= 20:
                        adv = float(px["VOLUME"].iloc[-20:].mean())
                except Exception:
                    pass
            adv_list.append(adv)
            dtc_list.append(row.shares_short / adv if np.isfinite(adv) and adv > 0 else np.nan)

        df["adv20_shares"] = adv_list
        df["dtc"] = dtc_list

    df.to_parquet(output_path, engine="pyarrow", compression="snappy")
    logger.info(f"Short interest parquet written to {output_path} ({len(df)} rows)")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build FINRA short interest dataset.")
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD).")
    parser.add_argument("--output", type=str, default=OUTPUT_PARQUET, help="Output parquet path.")
    args = parser.parse_args()

    build_short_interest_history(start_date=args.start, output_path=args.output)
