"""
Swing Signals — live data (nightly job only)
============================================
  latest_completed_session()  the last NYSE session whose close has happened (16:15 ET buffer)
  update_live_prices()        incremental daily bars (yfinance auto_adjust=False, actions=True) in the
                              long-history schema; a ticker whose new bars carry a dividend or split is
                              refetched in full, because Yahoo re-adjusts its whole Adj Close history
  daily_index()               EDGAR daily form index (form.YYYYMMDD.idx), cached
  recent_filer_ciks()         CIKs with 8-K / 6-K / deal / delisting filings on given days (their SEC
                              submissions are refreshed; everyone else's cache is reused)
  form4_purchases()           Form 4 XML filed on given days for given issuers, parsed into the same
                              schema as scripts/build_insider_history.py
"""

import io
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVE_DIR = os.path.join(PROJECT_ROOT, "data", "swing_live")
PRICE_DIR = os.path.join(LIVE_DIR, "prices")
INDEX_DIR = os.path.join(LIVE_DIR, "edgar_index")
FORM4_DIR = os.path.join(LIVE_DIR, "form4")
LIVE_HISTORY_YEARS = 4                  # C6 needs ~525 sessions; exits need the bars since each signal
FILER_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A", "425", "PREM14A", "DEFM14A", "S-4", "S-4/A", "SC TO-T", "SC TO-T/A",
               "SC TO-I", "SC TO-I/A", "SC 14D9", "SC 14D9/A", "SC 13E3", "SC 13E3/A", "25", "25-NSE",
               "15-12B", "15-12G", "15-15D"}


from src.utils.market_date import latest_completed_session  # noqa: E402  (shared with the Ideas scan)


MIN_FRESH_PCT = 95.0   # share of recently trading tickers that must have the session's bar


def fresh_share(frames: Dict[str, pd.DataFrame], session: date) -> Tuple[float, int]:
    """(% of tickers with a bar on `session`, tickers counted). Counted: a bar in the 14 days
    before it, so delisted names do not count. Yahoo posts some chunks' bars hours late."""
    lasts = [f.index[-1].date() for f in frames.values() if len(f)]
    recent = [d for d in lasts if d >= session - timedelta(days=14)]
    if not recent:
        return 0.0, 0
    return 100.0 * sum(d >= session for d in recent) / len(recent), len(recent)


def update_live_prices(tickers: Sequence[str], price_dir: str = PRICE_DIR, chunk: int = 200) -> Dict[str, object]:
    from scripts.build_long_history import download_chunk
    os.makedirs(price_dir, exist_ok=True)
    full_start = (date.today() - timedelta(days=365 * LIVE_HISTORY_YEARS)).isoformat()
    have: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        p = os.path.join(price_dir, f"{t}.parquet")
        if os.path.exists(p):
            try:
                have[t] = pd.read_parquet(p)
            except Exception:
                pass
    fresh = [t for t in tickers if t not in have]
    incr = [t for t in tickers if t in have]
    stats = {"full": 0, "incremental": 0, "refetched_after_action": 0, "failed": []}

    cutoff = pd.Timestamp(latest_completed_session())

    def save(t, df):
        # A bar for a session that has not closed is still forming: never store or use it
        df[df.index <= cutoff].to_parquet(os.path.join(price_dir, f"{t}.parquet"))

    groups: Dict[str, List[str]] = {}
    for t in incr:
        last = have[t].index[-1].date()
        groups.setdefault((last - timedelta(days=7)).isoformat(), []).append(t)   # 7-day overlap heals gaps
    refetch: List[str] = []
    for start, names in groups.items():
        for i in range(0, len(names), chunk):
            part = names[i:i + chunk]
            got = download_chunk(part, start_date=start)
            for t in part:
                new = got.get(t)
                if new is None or new.empty:
                    stats["failed"].append(t)
                    continue
                old = have[t]
                after = new[new.index > old.index[-1]]
                if ((after.get("DIVIDEND", 0) > 0).any() or (after.get("SPLIT", 0) > 0).any()):
                    refetch.append(t)                  # adjusted history changes: refetch in full
                    continue
                df = pd.concat([old[old.index < new.index[0]], new])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                save(t, df)
                stats["incremental"] += 1
            time.sleep(0.25)
    todo = fresh + refetch
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        got = download_chunk(part, start_date=full_start)
        for t in part:
            df = got.get(t)
            if df is None or df.empty:
                stats["failed"].append(t)
                continue
            save(t, df)
            stats["refetched_after_action" if t in refetch else "full"] += 1
        time.sleep(0.25)
    stats["failed"] = sorted(set(stats["failed"]))
    stats["failed_count"] = len(stats["failed"])
    stats["failed"] = stats["failed"][:50]
    return stats


def daily_index(day: date, sec, index_dir: str = INDEX_DIR) -> Optional[pd.DataFrame]:
    """EDGAR daily form index for `day` (None when SEC publishes none, e.g. holidays)."""
    os.makedirs(index_dir, exist_ok=True)
    path = os.path.join(index_dir, f"{day.isoformat()}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    q = (day.month - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"
    time.sleep(0.15)
    r = sec._session.get(url, headers={"User-Agent": sec.user_agent}, timeout=60)
    if r.status_code != 200:
        return None
    rows = []
    for line in r.text.splitlines():
        m = re.match(r"^(\S.*?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/\S+)\s*$", line)
        if m:
            rows.append({"form": m.group(1).strip(), "company": m.group(2).strip(), "cik": int(m.group(3)),
                         "date": m.group(4), "path": m.group(5)})
    df = pd.DataFrame(rows)
    if day < date.today():                        # today's index can still grow
        df.to_parquet(path)
    return df


def recent_filer_ciks(days: Iterable[date], sec) -> Set[int]:
    out: Set[int] = set()
    for d in days:
        idx = daily_index(d, sec)
        if idx is not None and len(idx):
            out |= set(idx.loc[idx["form"].isin(FILER_FORMS), "cik"].astype(int))
    return out


def _text(node, path) -> Optional[str]:
    x = node.find(path)
    if x is None:
        return None
    v = x.find("value")
    return (v.text if v is not None else x.text or "").strip() or None


def parse_form4(raw: str, filing_date: date, accession: str) -> List[dict]:
    """Non-derivative code-P / acquired rows of one Form 4 submission, in the data-set schema."""
    m = re.search(r"<XML>(.*?)</XML>", raw, flags=re.S | re.I)
    if not m:
        return []
    try:
        doc = ET.fromstring(m.group(1).strip())
    except ET.ParseError:
        return []
    if (_text(doc, "documentType") or "") != "4":
        return []
    issuer_cik = _text(doc, "issuer/issuerCik")
    sym = _text(doc, "issuer/issuerTradingSymbol") or ""
    aff = _text(doc, "aff10b5One")
    aff_v = None if aff is None else aff.lower() in ("1", "true")
    owners = []
    for o in doc.findall("reportingOwner"):
        cik = _text(o, "reportingOwnerId/rptOwnerCik")
        is_dir = (_text(o, "reportingOwnerRelationship/isDirector") or "").lower() in ("1", "true")
        is_off = (_text(o, "reportingOwnerRelationship/isOfficer") or "").lower() in ("1", "true")
        if cik:
            owners.append((int(cik), is_dir, is_off))
    rows = []
    for tr in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _text(tr, "transactionCoding/transactionCode") != "P":
            continue
        if _text(tr, "transactionAmounts/transactionAcquiredDisposedCode") != "A":
            continue
        try:
            shares = float(_text(tr, "transactionAmounts/transactionShares") or "nan")
            price = float(_text(tr, "transactionAmounts/transactionPricePerShare") or "nan")
        except ValueError:
            continue
        td = _text(tr, "transactionDate")
        for cik, is_dir, is_off in owners:
            rows.append({"accession": accession, "filing_date": filing_date,
                         "trans_date": date.fromisoformat(td[:10]) if td else None,
                         "issuer_cik": int(issuer_cik) if issuer_cik else None,
                         "symbol": re.sub(r"[^A-Za-z0-9.\-]", "", sym).upper().replace(".", "-"),
                         "owner_cik": cik, "relationship": ",".join(x for x, f in (("Director", is_dir), ("Officer", is_off)) if f),
                         "is_director": is_dir, "is_officer": is_off, "shares": shares, "price": price,
                         "aff10b5one": aff_v, "value": shares * price})
    return rows


def form4_purchases(days: Iterable[date], issuer_ciks: Set[int], sec, budget: int = 6000,
                    form4_dir: str = FORM4_DIR) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Form 4 code-P purchases filed on `days` by U1 issuers (cached per filing; `budget` new fetches)."""
    os.makedirs(form4_dir, exist_ok=True)
    rows, fetched, skipped_budget, days_done = [], 0, 0, []
    for d in sorted(days, reverse=True):          # newest first: the cluster window matters most
        idx = daily_index(d, sec)
        if idx is None or not len(idx):
            days_done.append(d.isoformat())
            continue
        f4 = idx[(idx["form"] == "4") & idx["cik"].isin(issuer_ciks)]
        complete = True
        for path in f4["path"].unique():
            acc = os.path.basename(path)[:-4]
            cpath = os.path.join(form4_dir, f"{acc}.parquet")
            if os.path.exists(cpath):
                rows.append(pd.read_parquet(cpath))
                continue
            if fetched >= budget:
                skipped_budget += 1
                complete = False
                continue
            time.sleep(0.15)
            r = sec._session.get(f"https://www.sec.gov/Archives/{path}", headers={"User-Agent": sec.user_agent}, timeout=60)
            fetched += 1
            if r.status_code != 200:
                complete = False
                continue
            df = pd.DataFrame(parse_form4(r.text, d, acc))
            if df.empty:
                df = pd.DataFrame(columns=["accession"])
            df.to_parquet(cpath)
            rows.append(df)
        if complete:
            days_done.append(d.isoformat())
    out = pd.concat([r for r in rows if len(r)], ignore_index=True) if any(len(r) for r in rows) else pd.DataFrame()
    return out, {"fetched": fetched, "skipped_for_budget": skipped_budget, "complete_days": len(days_done)}
