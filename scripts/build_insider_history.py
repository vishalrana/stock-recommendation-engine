"""
SEC Insider History Builder (Forms 3/4/5, 2006Q1 Onward)
========================================================
Downloads quarterly Form 3/4/5 ZIP data sets from SEC DERA (https://www.sec.gov/dera/data/form-345),
filters for qualifying open-market director/officer purchases, and writes:
data/backtest_history_long/insider_purchases.parquet.
"""

import argparse
import io
import logging
import os
import sys
import zipfile
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.providers.context.sec_fundamentals import get_sec_provider

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

OUTPUT_PARQUET = os.path.join(PROJECT_ROOT, "data", "backtest_history_long", "insider_purchases.parquet")
DERA_BASE_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets"


def get_cik_to_ticker_map() -> Dict[int, str]:
    """Build CIK -> ticker map from SEC provider."""
    sec = get_sec_provider()
    mapping: Dict[int, str] = {}
    if sec and sec._ciks:
        for ticker, cik in sec._ciks.items():
            if cik not in mapping:
                mapping[cik] = ticker.upper()
    return mapping


def parse_quarter_zip(zip_bytes: bytes, cik_map: Dict[int, str]) -> pd.DataFrame:
    """
    Parse a single quarterly Form 3/4/5 ZIP and return qualifying insider purchases.
    Qualifying purchases (Section 2 Sleeve I):
      - Form 4 original (not /A), non-derivative table
      - transaction code P, acquired (A)
      - reporting owner is a director or officer
      - shares x price >= $10,000
      - AFF10B5ONE is not True (where present)
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        # Find submission, reporting owner, and non-derivative transaction files
        sub_name = next((n for n in names if "SUBMISSION" in n.upper() and (n.endswith(".tsv") or n.endswith(".txt"))), None)
        owner_name = next((n for n in names if "REPORTING_OWNER" in n.upper() or "RPTOWNER" in n.upper() or "OWNER" in n.upper()), None)
        trans_name = next((n for n in names if "NONDERIV_TRANS" in n.upper() or "NONDERIV" in n.upper()), None)

        if not (sub_name and owner_name and trans_name):
            logger.warning(f"Missing required tables in ZIP: {names}")
            return pd.DataFrame()

        # Read submissions (ACCESSION_NUMBER, FILING_DATE, ISSUER_CIK)
        with z.open(sub_name) as f:
            sub_df = pd.read_csv(f, sep="\t", dtype=str, on_bad_lines="skip", low_memory=False)
        sub_cols = {c: c.upper().strip() for c in sub_df.columns}
        sub_df.rename(columns=sub_cols, inplace=True)

        acc_col = next((c for c in sub_df.columns if "ACCESSION" in c), "ACCESSION_NUMBER")
        fdate_col = next((c for c in sub_df.columns if "FILING_DATE" in c or "FILED" in c), "FILING_DATE")
        cik_col = next((c for c in sub_df.columns if "ISSUERCIK" in c or "ISSUER_CIK" in c), "ISSUER_CIK")
        form_col = next((c for c in sub_df.columns if "DOCUMENT_TYPE" in c or "FORM" in c or "SUBMISSION_TYPE" in c), None)

        # Filter to Form 4 original (not /A) if form column present
        if form_col and form_col in sub_df.columns:
            sub_df = sub_df[sub_df[form_col].str.strip().str.upper() == "4"]

        sub_df = sub_df[[acc_col, fdate_col, cik_col]].dropna()
        sub_df.rename(columns={acc_col: "accession_number", fdate_col: "filing_date", cik_col: "issuer_cik"}, inplace=True)
        sub_df["issuer_cik"] = pd.to_numeric(sub_df["issuer_cik"], errors="coerce")
        sub_df = sub_df.dropna(subset=["issuer_cik"])
        sub_df["issuer_cik"] = sub_df["issuer_cik"].astype(np.int64)

        # Read reporting owners (director or officer)
        with z.open(owner_name) as f:
            own_df = pd.read_csv(f, sep="\t", dtype=str, on_bad_lines="skip", low_memory=False)
        own_cols = {c: c.upper().strip() for c in own_df.columns}
        own_df.rename(columns=own_cols, inplace=True)

        own_acc = next((c for c in own_df.columns if "ACCESSION" in c), "ACCESSION_NUMBER")
        own_cik = next((c for c in own_df.columns if "RPTOWNER_CIK" in c or "OWNER_CIK" in c or "RPT_CIK" in c), "RPTOWNER_CIK")
        is_dir_col = next((c for c in own_df.columns if "DIRECTOR" in c), "IS_DIRECTOR")
        is_off_col = next((c for c in own_df.columns if "OFFICER" in c), "IS_OFFICER")

        # Keep director or officer == True / 1 / Y
        def is_true(s: pd.Series) -> pd.Series:
            return s.fillna("").astype(str).str.strip().str.upper().isin(["1", "Y", "TRUE", "YES"])

        is_dir = is_true(own_df[is_dir_col]) if is_dir_col in own_df.columns else pd.Series(False, index=own_df.index)
        is_off = is_true(own_df[is_off_col]) if is_off_col in own_df.columns else pd.Series(False, index=own_df.index)
        own_df = own_df[is_dir | is_off].copy()
        own_df["relationship"] = np.where(is_dir[own_df.index] & is_off[own_df.index], "Director, Officer",
                                  np.where(is_dir[own_df.index], "Director", "Officer"))
        own_df = own_df[[own_acc, own_cik, "relationship"]].rename(
            columns={own_acc: "accession_number", own_cik: "owner_id"}
        )

        # Read non-derivative transactions (code P, acquired A, value >= 10000)
        with z.open(trans_name) as f:
            trans_df = pd.read_csv(f, sep="\t", dtype=str, on_bad_lines="skip", low_memory=False)
        trans_cols = {c: c.upper().strip() for c in trans_df.columns}
        trans_df.rename(columns=trans_cols, inplace=True)

        t_acc = next((c for c in trans_df.columns if "ACCESSION" in c), "ACCESSION_NUMBER")
        t_code = next((c for c in trans_df.columns if "TRANS_CODE" in c or "CODE" in c), "TRANS_CODE")
        t_ad = next((c for c in trans_df.columns if "ACQUIRED_DISP" in c or "ACQ_DISP" in c), "TRANS_ACQUIRED_DISP_CD")
        t_shares = next((c for c in trans_df.columns if "SHARES" in c), "TRANS_SHARES")
        t_price = next((c for c in trans_df.columns if "PRICE" in c), "TRANS_PRICEPERSHARE")
        t_10b5 = next((c for c in trans_df.columns if "10B5" in c or "AFF10B5" in c), None)

        # Filter: Code P, Acquired A
        p_mask = trans_df[t_code].astype(str).str.strip().str.upper() == "P"
        a_mask = trans_df[t_ad].astype(str).str.strip().str.upper() == "A"
        trans_df = trans_df[p_mask & a_mask].copy()

        trans_df["shares"] = pd.to_numeric(trans_df[t_shares], errors="coerce").fillna(0.0)
        trans_df["price"] = pd.to_numeric(trans_df[t_price], errors="coerce").fillna(0.0)
        trans_df["value"] = trans_df["shares"] * trans_df["price"]

        # Value >= $10,000
        trans_df = trans_df[trans_df["value"] >= 10000.0]

        # AFF10B5ONE check: False or None (not True)
        if t_10b5 and t_10b5 in trans_df.columns:
            aff_true = is_true(trans_df[t_10b5])
            trans_df = trans_df[~aff_true].copy()
            trans_df["aff10b5one"] = False
        else:
            trans_df["aff10b5one"] = False

        trans_df = trans_df[[t_acc, "shares", "price", "value", "aff10b5one"]].rename(
            columns={t_acc: "accession_number"}
        )

        # Merge transactions + submissions + owners
        merged = trans_df.merge(sub_df, on="accession_number", how="inner")
        merged = merged.merge(own_df, on="accession_number", how="inner")

        if merged.empty:
            return pd.DataFrame()

        merged["transaction_code"] = "P"
        merged["ticker"] = merged["issuer_cik"].map(cik_map).fillna("")
        merged["filing_date"] = pd.to_datetime(merged["filing_date"]).dt.date

        cols = [
            "issuer_cik", "ticker", "owner_id", "relationship", "transaction_code",
            "shares", "price", "value", "filing_date", "aff10b5one"
        ]
        return merged[cols]


def build_insider_history(
    start_year: int = 2006,
    end_year: Optional[int] = None,
    output_path: str = OUTPUT_PARQUET,
    limit_quarters: Optional[int] = None,
) -> pd.DataFrame:
    """Download quarterly Form 3/4/5 ZIPs and build master insider purchases parquet."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cik_map = get_cik_to_ticker_map()
    logger.info(f"Loaded {len(cik_map)} CIK -> Ticker mappings.")

    sec = get_sec_provider()
    headers = {"User-Agent": sec.user_agent if sec else "QuantitativeResearch personal@research.local"}

    if end_year is None:
        end_year = date.today().year

    quarters = []
    for y in range(start_year, end_year + 1):
        for q in range(1, 5):
            if y == end_year and q > (date.today().month - 1) // 3 + 1:
                break
            quarters.append((y, q))

    if limit_quarters is not None and limit_quarters > 0:
        quarters = quarters[:limit_quarters]

    all_frames: List[pd.DataFrame] = []
    for y, q in quarters:
        url = f"{DERA_BASE_URL}/{y}q{q}_form345.zip"
        logger.info(f"Fetching DERA insider data {y}Q{q} from {url}...")
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            if resp.status_code == 200:
                df_q = parse_quarter_zip(resp.content, cik_map)
                if not df_q.empty:
                    logger.info(f"  -> Extracted {len(df_q)} qualifying purchases from {y}Q{q}")
                    all_frames.append(df_q)
            else:
                logger.warning(f"  -> HTTP {resp.status_code} for {y}Q{q}")
        except Exception as e:
            logger.warning(f"  -> Failed {y}Q{q}: {e}")

    if all_frames:
        master = pd.concat(all_frames, ignore_index=True)
    else:
        logger.warning("No DERA files extracted; writing empty schema template.")
        master = pd.DataFrame(columns=[
            "issuer_cik", "ticker", "owner_id", "relationship", "transaction_code",
            "shares", "price", "value", "filing_date", "aff10b5one"
        ])

    master.to_parquet(output_path, engine="pyarrow", compression="snappy")
    logger.info(f"Master insider purchases written to {output_path} ({len(master)} rows)")
    return master


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build SEC Insider Purchases History.")
    parser.add_argument("--start-year", type=int, default=2006, help="Start year.")
    parser.add_argument("--end-year", type=int, default=None, help="End year.")
    parser.add_argument("--limit-quarters", type=int, default=None, help="Limit number of quarters.")
    parser.add_argument("--output", type=str, default=OUTPUT_PARQUET, help="Output parquet path.")
    args = parser.parse_args()

    build_insider_history(
        start_year=args.start_year,
        end_year=args.end_year,
        output_path=args.output,
        limit_quarters=args.limit_quarters,
    )
