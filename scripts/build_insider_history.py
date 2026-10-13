"""
SEC Insider Transactions Data Sets -> purchases table (swing sleeve I)
=====================================================================
Source: SEC DERA "Insider Transactions Data Sets" (Forms 3/4/5), one ZIP per quarter from 2006Q1:
https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/<YYYY>q<Q>_form345.zip

Table and field names are the data set's own (insider_transactions_readme.htm; checked on the
2022Q3 and 2024Q3 files on 2026-10-11):
  SUBMISSION.tsv       ACCESSION_NUMBER, FILING_DATE (DD-MON-YYYY), DOCUMENT_TYPE ("4", "4/A", ...),
                       ISSUERCIK, ISSUERTRADINGSYMBOL, AFF10B5ONE (later files only; 0/1/true/false)
  REPORTINGOWNER.tsv   ACCESSION_NUMBER, RPTOWNERCIK, RPTOWNER_RELATIONSHIP
                       ("Director", "Officer", "Director,Officer", "TenPercentOwner", ...)
  NONDERIV_TRANS.tsv   ACCESSION_NUMBER, TRANS_DATE, TRANS_CODE, TRANS_SHARES, TRANS_PRICEPERSHARE,
                       TRANS_ACQUIRED_DISP_CD

Outputs (data/backtest_history_long/):
  insider_purchases.parquet  every non-derivative code-P / acquired row from an original Form 4,
                             one row per (accession, owner, transaction) with the owner's role, value
                             and the 10b5-1 flag. Qualification (director/officer, >= $10,000, not
                             10b5-1, not routine) is applied by src/swing/engine.py so research and
                             live share it.
  insider_symbol_cik.parquet ISSUERTRADINGSYMBOL -> ISSUERCIK with first/last filing dates, used to
                             find SEC filings for historical S&P 500 members that no longer trade.
"""

import argparse
import io
import logging
import os
import sys
import time
import zipfile
from datetime import date
from typing import List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.providers.context.sec_fundamentals import get_sec_provider  # noqa: E402
from src.research.survivorship import yahoo_symbol  # noqa: E402

logger = logging.getLogger("build_insider_history")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

OUT_DIR = os.path.join(PROJECT_ROOT, "data", "backtest_history_long")
PURCHASES = os.path.join(OUT_DIR, "insider_purchases.parquet")
SYMBOL_MAP = os.path.join(OUT_DIR, "insider_symbol_cik.parquet")
ZIP_CACHE = os.path.join(OUT_DIR, "insider_zips")
URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip"


def clean_symbol(raw) -> str:
    """Issuer symbols as filed sometimes carry quotes, brackets or exchange prefixes ("(CALX)", "'LTRX")."""
    import re
    if raw is None or (isinstance(raw, float) and raw != raw):
        return ""
    s = re.sub(r"[^A-Za-z0-9.\-]", "", str(raw or "")).upper()
    return yahoo_symbol(s) if s else ""


def truthy(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower().isin({"1", "true", "y", "yes"})


def parse_quarter(blob: bytes) -> tuple:
    """(purchases, symbol rows) from one quarterly ZIP."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    read = lambda n, cols: pd.read_csv(z.open(n), sep="\t", dtype=str, usecols=lambda c: c in cols,
                                       on_bad_lines="skip", low_memory=False, quoting=3)
    sub = read("SUBMISSION.tsv", {"ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK",
                                  "ISSUERTRADINGSYMBOL", "AFF10B5ONE"})
    sub["FILING_DATE"] = pd.to_datetime(sub["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
    sub["ISSUERCIK"] = pd.to_numeric(sub["ISSUERCIK"], errors="coerce")
    sub = sub.dropna(subset=["FILING_DATE", "ISSUERCIK"])
    sym = sub[["ISSUERTRADINGSYMBOL", "ISSUERCIK", "FILING_DATE"]].dropna()

    f4 = sub[sub["DOCUMENT_TYPE"].str.strip() == "4"].copy()          # original Form 4 only (not 4/A)
    if "AFF10B5ONE" in f4.columns:
        raw = f4["AFF10B5ONE"]
        f4["aff10b5one"] = np.where(raw.isna(), None, truthy(raw))
    else:
        f4["aff10b5one"] = None

    nd = read("NONDERIV_TRANS.tsv", {"ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                     "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"})
    nd = nd[(nd["TRANS_CODE"].str.strip() == "P") & (nd["TRANS_ACQUIRED_DISP_CD"].str.strip() == "A")].copy()
    nd["shares"] = pd.to_numeric(nd["TRANS_SHARES"], errors="coerce")
    nd["price"] = pd.to_numeric(nd["TRANS_PRICEPERSHARE"], errors="coerce")
    nd["trans_date"] = pd.to_datetime(nd["TRANS_DATE"], format="%d-%b-%Y", errors="coerce")

    own = read("REPORTINGOWNER.tsv", {"ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"})
    rel = own["RPTOWNER_RELATIONSHIP"].fillna("")
    own["is_director"] = rel.str.contains("Director")
    own["is_officer"] = rel.str.contains("Officer")
    own["owner_cik"] = pd.to_numeric(own["RPTOWNERCIK"], errors="coerce")

    m = (nd.merge(f4[["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK", "ISSUERTRADINGSYMBOL", "aff10b5one"]],
                  on="ACCESSION_NUMBER", how="inner")
           .merge(own[["ACCESSION_NUMBER", "owner_cik", "RPTOWNER_RELATIONSHIP", "is_director", "is_officer"]],
                  on="ACCESSION_NUMBER", how="inner"))
    m = m.dropna(subset=["owner_cik", "shares"])
    out = pd.DataFrame({
        "accession": m["ACCESSION_NUMBER"],
        "filing_date": m["FILING_DATE"].dt.date,
        "trans_date": m["trans_date"].dt.date,
        "issuer_cik": m["ISSUERCIK"].astype(np.int64),
        "symbol": m["ISSUERTRADINGSYMBOL"].map(clean_symbol),
        "owner_cik": m["owner_cik"].astype(np.int64),
        "relationship": m["RPTOWNER_RELATIONSHIP"],
        "is_director": m["is_director"].astype(bool),
        "is_officer": m["is_officer"].astype(bool),
        "shares": m["shares"].astype(float),
        "price": m["price"].astype(float),
        "aff10b5one": m["aff10b5one"],
    })
    out["value"] = out["shares"] * out["price"]
    return out, sym


def quarters(start: date, end: date) -> List[tuple]:
    out, y, q = [], start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def build(start: date = date(2006, 1, 1), end: Optional[date] = None, limit: Optional[int] = None) -> dict:
    sec = get_sec_provider()
    if not sec.available:
        raise SystemExit("SEC_USER_AGENT is required for the SEC data sets.")
    os.makedirs(ZIP_CACHE, exist_ok=True)
    qs = quarters(start, end or date.today())
    if limit:
        qs = qs[-limit:]
    parts, syms, missing = [], [], []
    for y, q in qs:
        path = os.path.join(ZIP_CACHE, f"{y}q{q}.parquet")
        spath = os.path.join(ZIP_CACHE, f"{y}q{q}_sym.parquet")
        if os.path.exists(path) and os.path.exists(spath) and (y, q) != qs[-1]:
            parts.append(pd.read_parquet(path))
            syms.append(pd.read_parquet(spath))
            continue
        time.sleep(0.2)
        r = sec._session.get(URL.format(y=y, q=q), headers={"User-Agent": sec.user_agent}, timeout=300)
        if r.status_code != 200:
            missing.append(f"{y}q{q}:{r.status_code}")
            continue
        p, s = parse_quarter(r.content)
        p.to_parquet(path)
        s.to_parquet(spath)
        parts.append(p)
        syms.append(s)
        logger.info("%dQ%d: %d code-P purchase rows", y, q, len(p))
    if not parts:
        raise SystemExit(f"No insider data set could be read (missing: {missing}).")
    purchases = pd.concat(parts, ignore_index=True)
    purchases.to_parquet(PURCHASES)
    sym = pd.concat(syms, ignore_index=True)
    sym["symbol"] = sym["ISSUERTRADINGSYMBOL"].map(clean_symbol)
    sym = sym[sym["symbol"] != ""]
    sym = (sym.groupby(["symbol", "ISSUERCIK"])["FILING_DATE"].agg(["min", "max", "count"]).reset_index()
              .rename(columns={"ISSUERCIK": "cik", "min": "first", "max": "last"}))
    sym["cik"] = sym["cik"].astype(np.int64)
    sym.to_parquet(SYMBOL_MAP)
    summary = {"quarters": len(qs), "missing": missing, "purchase_rows": int(len(purchases)),
               "symbols": int(sym["symbol"].nunique())}
    logger.info("Insider data sets: %s", summary)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Only the latest N quarters (smoke)")
    ap.add_argument("--start", default="2006-01-01")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    build(start=date.fromisoformat(args.start), end=date.fromisoformat(args.end) if args.end else None,
          limit=args.limit)
