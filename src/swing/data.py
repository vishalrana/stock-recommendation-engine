"""
Swing Signals — data loading shared by research and live
=========================================================
  load_price_frames()   long-history parquet frames (scripts/build_long_history.py schema:
                        TR_OPEN/HIGH/LOW/CLOSE total-return prices, CLOSE, UNADJ_CLOSE, VOLUME)
  sector_map()          stock -> SPDR sector ETF from the security master (None -> SPY is used)
  CikResolver           ticker -> SEC CIK: today's SEC ticker map, else the historical
                        ISSUERTRADINGSYMBOL -> ISSUERCIK map from the insider data sets
                        (delisted S&P 500 members)
  load_filings()        SEC submissions since 2006 (8-K items incl. 6-K, deal forms), by ticker
  load_insider()        qualifying sleeve-I purchases mapped to price tickers
"""

import glob
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from src.providers.context.sec_filings import Filing, SecFilingsProvider, rights_days_for

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LONG_DIR = os.path.join(PROJECT_ROOT, "data", "backtest_history_long")
SEC_DIR = os.path.join(LONG_DIR, "sec_filings")
INSIDER_PURCHASES = os.path.join(LONG_DIR, "insider_purchases.parquet")
INSIDER_SYMBOLS = os.path.join(LONG_DIR, "insider_symbol_cik.parquet")
MASTER = os.path.join(PROJECT_ROOT, "data", "cache", "universe", "us_equities_master.json")
SEC_HISTORY_START = date(2006, 1, 1)


def load_price_frames(directory: str = LONG_DIR, tickers: Optional[Iterable[str]] = None) -> Dict[str, pd.DataFrame]:
    want = set(tickers) if tickers is not None else None
    out = {}
    for f in glob.glob(os.path.join(directory, "*.parquet")):
        t = os.path.basename(f)[:-8]
        if t.startswith("insider_") or (want is not None and t not in want):
            continue
        try:
            df = pd.read_parquet(f)
        except Exception as e:
            logger.warning("Unreadable price file %s: %s", f, e)
            continue
        if {"TR_OPEN", "TR_HIGH", "TR_LOW", "TR_CLOSE", "VOLUME"} <= set(df.columns) and len(df):
            df.index = pd.DatetimeIndex(df.index).tz_localize(None)
            out[t] = df.sort_index()
    return out


def sector_map(tickers: Iterable[str]) -> Dict[str, Optional[str]]:
    from scripts.research_signal_ic import SECTOR_TO_ETF
    try:
        with open(MASTER, encoding="utf-8") as f:
            master = {x["ticker"]: x.get("sector") for x in json.load(f)["securities"]}
    except Exception:
        master = {}
    return {t: SECTOR_TO_ETF.get(master.get(t) or master.get(t.replace("-", ".")) or "") for t in tickers}


class CikResolver:
    def __init__(self, sec=None, symbol_map_path: str = INSIDER_SYMBOLS):
        from src.providers.context.sec_fundamentals import get_sec_provider
        self.sec = sec or get_sec_provider()
        self.hist: Dict[str, int] = {}
        if os.path.exists(symbol_map_path):
            m = pd.read_parquet(symbol_map_path).sort_values(["symbol", "last"])
            for sym, g in m.groupby("symbol"):
                self.hist[sym] = int(g.iloc[-1]["cik"])      # most recent issuer that filed under the symbol

    def __call__(self, ticker: str) -> Optional[int]:
        return self.sec.cik_for(ticker) or self.hist.get(ticker)

    def ticker_map(self, tickers: Iterable[str]) -> Dict[int, str]:
        out: Dict[int, str] = {}
        for t in tickers:
            c = self(t)
            if c is not None:
                out.setdefault(int(c), t)
        return out


def load_filings(tickers: Iterable[str], resolver: CikResolver, cache_dir: str = SEC_DIR,
                 ttl_hours: Optional[float] = None, workers: int = 4,
                 refresh_ciks: Iterable[int] = ()) -> Tuple[Dict[str, Optional[List[Filing]]], Dict[str, List[date]], dict]:
    """Filings by ticker (None = no SEC filer found), rights-offering days by ticker, and stats."""
    prov = SecFilingsProvider(cache_dir=cache_dir, ttl_hours=ttl_hours, since=SEC_HISTORY_START)
    if not prov.available:
        raise RuntimeError("SEC_USER_AGENT is required for the swing program's SEC data.")
    prov.sec.ticker_map()
    tickers = sorted(set(tickers))
    refresh = set(int(c) for c in refresh_ciks)
    for c in refresh:                                       # live: refetch the recent block for new filers
        path = prov._path(c)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
                doc["fetched_at"] = 0
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(doc, f)
            except Exception:
                pass
    if refresh:
        prov.ttl_hours = 0.0 if ttl_hours is None else ttl_hours

    def one(t):
        c = resolver(t)
        return t, (prov.filings_for_cik(int(c)) if c is not None else None)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        filings = dict(pool.map(one, tickers))
    rights = prov.rights_offering_days(SEC_HISTORY_START, date.today()) if prov.available else None
    rights_by = {t: rights_days_for(rights, resolver(t)) for t in tickers}
    stats = dict(prov.stats, tickers=len(tickers), with_filings=sum(1 for v in filings.values() if v is not None),
                 rights_search=rights is not None)
    return filings, rights_by, stats


def load_insider(tickers: Iterable[str], resolver: CikResolver, path: str = INSIDER_PURCHASES) -> Optional[pd.DataFrame]:
    from src.swing.engine import qualifying_purchases
    if not os.path.exists(path):
        return None
    tickers = set(tickers)
    cik_to_t = resolver.ticker_map(tickers)
    raw = pd.read_parquet(path)

    known = set(cik_to_t.values())

    def ticker_of(cik: int, sym: str, fd) -> Optional[str]:
        t = cik_to_t.get(int(cik))
        if t is not None:
            return t                                     # issuer CIK matches the price ticker's CIK
        if sym and sym in tickers and sym not in known:
            return sym                                   # no CIK on record for that ticker: filed symbol
        return None                                      # recycled symbols are not guessed

    return qualifying_purchases(raw, ticker_of)
