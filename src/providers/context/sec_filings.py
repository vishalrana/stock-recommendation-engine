"""
SEC EDGAR filings: point-in-time earnings dates and corporate-event locks
=========================================================================
Source: the EDGAR submissions JSON (data.sec.gov/submissions/CIK##########.json plus its older
pages), which lists every filing with its form, items and acceptance time. Requests use the
SEC_USER_AGENT contact (see sec_fundamentals.py) and its < 7 requests/second throttle.

Earnings dates
  An 8-K with item 2.02 (Results of Operations) marks an earnings release. Its acceptance time
  gives the reaction session: accepted before 16:00 ET on a session -> that session (before the
  09:30 open the whole session reacts; during the session the rest of it does); accepted at or
  after 16:00 ET, or on a non-session day -> the next session. Some filers use item 2.02 for other
  results (royalty trusts announce monthly distributions with it); royalty trusts are excluded from
  the universe by name (src/universe/filters.py).

acceptanceDateTime
  Checked against the EDGAR filing headers on 2026-10-11 (AAPL, JPM, TEVA, 2015 to 2026): the JSON
  value is the Eastern acceptance time shifted forward by twice the Eastern UTC offset (+8 h under
  daylight time, +10 h otherwise) and labelled "Z". acceptance_et() undoes that.

Event lock (corporate actions that break a technical setup)
  Locked from a deal filing: 425, PREM14A, DEFM14A, S-4, SC TO-T, SC TO-I, SC 14D9, SC 13E3 (and
  their /A amendments), or a prospectus (424B1/3/4/5, S-1, S-3) found by EDGAR full-text search for
  an actual rights offering. Unlocked by an 8-K with item 2.01 (completion of an acquisition or
  disposition), a Form 25 / 25-NSE (delisting) or 15 (deregistration) filed on or after the latest
  deal filing, or 180 days with no further deal filing. Form strings were checked against the
  EDGAR daily indexes on 2026-10-11. The bare phrase "rights offering" also appears as boilerplate
  in ordinary shelf and note prospectuses (about 1,200 hits in six months of 2026), so the search
  requires "rights offering", "subscription price" and "record date" together (114 hits, which
  include the actual offerings, e.g. PBT Land & Minerals' rights certificates).
  The price-pin backstop catches deals not visible in filings: within 30 sessions after a > 15%
  one-day move, a realized volatility of the returns since the move (up to 20, at least 10) below
  15% of the one-year median marks the stock as pinned to a deal price (sessions 10-30 after it).
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.providers.context.sec_fundamentals import PROJECT_ROOT, get_sec_provider

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
LIVE_CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "cache", "sec_filings")

EARNINGS_ITEM = "2.02"
COMPLETION_ITEM = "2.01"
DEAL_FORMS = frozenset({"425", "PREM14A", "DEFM14A", "S-4", "SC TO-T", "SC TO-I", "SC 14D9", "SC 13E3"})
UNLOCK_FORMS = frozenset({"25", "25-NSE", "15-12B", "15-12G", "15-15D"})
LOCK_EXPIRY_DAYS = 180
RIGHTS_OFFERING_QUERY = '"rights offering" "subscription price" "record date"'
RIGHTS_OFFERING_FORMS = "424B1,424B3,424B4,424B5,S-1,S-3"
PIN_JUMP = 0.15
PIN_VOL_RATIO = 0.15
PIN_WINDOW_SESSIONS = 30

_KEEP_FORMS = DEAL_FORMS | UNLOCK_FORMS | {"8-K"}


def base_form(form: str) -> str:
    f = str(form or "").strip().upper()
    return f[:-2] if f.endswith("/A") else f


def acceptance_et(raw: str) -> Optional[datetime]:
    """Naive Eastern acceptance time from the submissions JSON acceptanceDateTime (see module notes)."""
    try:
        shifted = datetime.strptime(str(raw)[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    offset = -NY.utcoffset(shifted - timedelta(hours=8))   # 4 h (EDT) or 5 h (EST)
    return shifted - 2 * offset


def reaction_session(accepted: datetime) -> date:
    """First session whose prices can react to a filing accepted at `accepted` (Eastern time)."""
    from src.utils.market_date import is_nyse_session, session_on_or_after
    day = accepted.date()
    if is_nyse_session(day) and accepted.time() < datetime.strptime("16:00", "%H:%M").time():
        return day
    return session_on_or_after(day + timedelta(days=1))


@dataclass(frozen=True)
class Filing:
    form: str              # base form (amendments folded in)
    accepted: datetime     # Eastern time
    items: Tuple[str, ...]

    @property
    def day(self) -> date:
        return self.accepted.date()


@dataclass(frozen=True)
class EarningsEvent:
    announced: date        # Eastern calendar date of acceptance (what calendars call the report date)
    reaction: date         # first session that can react
    accepted: datetime


def parse_filings(columns: Dict[str, Sequence[Any]], since: Optional[date] = None) -> List[Filing]:
    """Filings of the forms this module uses, from a submissions 'recent' block or an older page."""
    out: List[Filing] = []
    forms = columns.get("form") or []
    acc = columns.get("acceptanceDateTime") or []
    items = columns.get("items") or [""] * len(forms)
    for f, a, it in zip(forms, acc, items):
        bf = base_form(f)
        if bf not in _KEEP_FORMS:
            continue
        ts = acceptance_et(a)
        if ts is None or (since is not None and ts.date() < since):
            continue
        out.append(Filing(bf, ts, tuple(x.strip() for x in str(it or "").split(",") if x.strip())))
    return out


def compact_rows(filings: Iterable[Filing]) -> List[List[Any]]:
    return [[f.form, f.accepted.isoformat(), ",".join(f.items)] for f in filings]


def filings_from_rows(rows: Iterable[Sequence[Any]]) -> List[Filing]:
    return sorted((Filing(r[0], datetime.fromisoformat(r[1]), tuple(x for x in str(r[2]).split(",") if x))
                   for r in rows), key=lambda f: f.accepted)


def earnings_events(filings: Iterable[Filing]) -> List[EarningsEvent]:
    """One event per reaction session, from 8-Ks with item 2.02, in time order."""
    events: Dict[date, EarningsEvent] = {}
    for f in sorted(filings, key=lambda x: x.accepted):
        if f.form == "8-K" and EARNINGS_ITEM in f.items:
            r = reaction_session(f.accepted)
            if r not in events:
                events[r] = EarningsEvent(f.accepted.date(), r, f.accepted)
    return [events[k] for k in sorted(events)]


def event_lock(filings: Iterable[Filing], as_of: date, rights_offering_days: Iterable[date] = ()) -> Optional[Dict[str, Any]]:
    """
    Lock state on `as_of` from filings accepted on or before that date. Returns None when not
    locked, else {"form", "since", "last_deal_filing"}.
    """
    deal_days: List[Tuple[date, str]] = []
    unlock_days: List[date] = []
    for f in filings:
        if f.day > as_of:
            continue
        if f.form in DEAL_FORMS:
            deal_days.append((f.day, f.form))
        elif f.form in UNLOCK_FORMS or (f.form == "8-K" and COMPLETION_ITEM in f.items):
            unlock_days.append(f.day)
    deal_days += [(d, "rights offering") for d in rights_offering_days if d <= as_of]
    if not deal_days:
        return None
    deal_days.sort()
    last_day, last_form = deal_days[-1]
    if (as_of - last_day).days > LOCK_EXPIRY_DAYS:
        return None
    if any(u >= last_day for u in unlock_days):
        return None
    # Start of the current episode: walk back while consecutive deal filings are <= 180 days apart.
    since = last_day
    for d, _ in reversed(deal_days[:-1]):
        if (since - d).days > LOCK_EXPIRY_DAYS or any(d <= u < since for u in unlock_days):
            break
        since = d
    return {"form": last_form, "since": since.isoformat(), "last_deal_filing": last_day.isoformat()}


def price_pin(close: pd.Series) -> pd.Series:
    """
    Price-pin backstop per session (point-in-time): within 30 sessions after a > 15% one-day move,
    the realized volatility of the daily log returns since that move (the last min(20, sessions since)
    returns, at least 10) is below 15% of the one-year median of the 20-session realized volatility.
    """
    r = np.log(close.astype(float)).diff()
    n = len(r)
    jump = (r.abs() > np.log1p(PIN_JUMP)).to_numpy()
    med = r.rolling(20, min_periods=10).std().rolling(252, min_periods=126).median().to_numpy()
    by_window = {w: r.rolling(w, min_periods=w).std().to_numpy() for w in range(10, 21)}
    out = np.zeros(n, dtype=bool)
    last_jump = -10 ** 9
    for t in range(n):
        if jump[t]:
            last_jump = t
            continue
        k = t - last_jump                      # returns since the move, excluding the move itself
        if 10 <= k <= PIN_WINDOW_SESSIONS:
            rv = by_window[min(20, k)][t]
            m = med[t]
            out[t] = bool(np.isfinite(rv) and np.isfinite(m) and rv < PIN_VOL_RATIO * m)
    return pd.Series(out, index=close.index)


class SecFilingsProvider:
    """Cached SEC filings per ticker. ttl_hours=None never refetches (backtests)."""

    def __init__(self, cache_dir: str = LIVE_CACHE_DIR, ttl_hours: Optional[float] = 24.0,
                 since: date = date(2021, 1, 1), sec=None):
        self.sec = sec or get_sec_provider()
        self.cache_dir = cache_dir
        self.ttl_hours = ttl_hours
        self.since = since
        self.stats = {"fetched": 0, "cache_hits": 0, "no_cik": 0, "failed": 0}

    @property
    def available(self) -> bool:
        return self.sec.available

    def _path(self, cik: int) -> str:
        os.makedirs(self.cache_dir, exist_ok=True)
        return os.path.join(self.cache_dir, f"CIK{cik:010d}.json")

    def _fresh(self, doc: Dict[str, Any]) -> bool:
        if self.ttl_hours is None:
            return True
        return (time.time() - float(doc.get("fetched_at", 0))) < self.ttl_hours * 3600

    def filings_for_cik(self, cik: int) -> Optional[List[Filing]]:
        """
        Filings since `since`. The 'recent' block is refetched after ttl_hours; older submission
        pages never change, so each is fetched once and kept.
        """
        path = self._path(cik)
        doc: Dict[str, Any] = {}
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
        except Exception:
            doc = {}
        if doc.get("since", "9999") > self.since.isoformat() or "recent_rows" not in doc:
            doc = {}  # older history needed, or a cache file in an earlier format
        if doc and self._fresh(doc):
            self.stats["cache_hits"] += 1
            return filings_from_rows(doc.get("recent_rows", []) + [r for rows in doc.get("pages", {}).values() for r in rows])
        if not self.available:
            return None
        data = self.sec._get_json(SUBMISSIONS_URL.format(cik=cik))
        if not data:
            self.stats["failed"] += 1
            return None
        recent = parse_filings(data.get("filings", {}).get("recent", {}), self.since)
        pages: Dict[str, List[List[Any]]] = {}
        old_pages = doc.get("pages", {})
        for page in data.get("filings", {}).get("files", []) or []:
            if str(page.get("filingTo", "9999")) < self.since.isoformat():
                continue
            name = page["name"]
            if name in old_pages:
                pages[name] = old_pages[name]
                continue
            older = self.sec._get_json(SUBMISSIONS_PAGE_URL.format(name=name))
            if older is None:
                self.stats["failed"] += 1
                return None  # incomplete history: unavailable rather than silently partial
            pages[name] = compact_rows(parse_filings(older, self.since))
        self.stats["fetched"] += 1
        new_doc = {"fetched_at": time.time(), "since": self.since.isoformat(), "name": data.get("name"),
                   "sic": data.get("sicDescription"), "recent_rows": compact_rows(recent), "pages": pages}
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(new_doc, f)
            os.replace(tmp, path)
        except Exception as e:
            logger.debug("[SEC] could not cache filings for CIK %s: %s", cik, e)
        return filings_from_rows(new_doc["recent_rows"] + [r for rows in pages.values() for r in rows])

    def filings(self, ticker: str) -> Optional[List[Filing]]:
        """Filings since `since`, or None when unavailable (no CIK, SEC disabled, fetch failed)."""
        cik = self.sec.cik_for(ticker)
        if cik is None:
            self.stats["no_cik"] += 1
            return None
        return self.filings_for_cik(cik)

    def rights_offering_days(self, start: date, end: date) -> Optional[Dict[int, List[date]]]:
        """CIK -> filing dates of prospectuses for actual rights offerings (EDGAR full-text search)."""
        path = os.path.join(self.cache_dir, f"rights_offerings_{start.isoformat()}_{end.isoformat()}.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                if self._fresh(doc):
                    return {int(k): [date.fromisoformat(x) for x in v] for k, v in doc["hits"].items()}
        except Exception:
            pass
        if not self.available:
            return None
        hits: Dict[int, set] = {}
        # One request window per half-year keeps every window under the search's 10,000-hit cap.
        cursor = start
        while cursor <= end:
            stop = min(end, date(cursor.year + (cursor.month > 6), 1 if cursor.month > 6 else 7, 1) - timedelta(days=1))
            frm = 0
            while True:
                data = self._fts(cursor, stop, frm)
                if data is None:
                    return None
                rows = data.get("hits", {}).get("hits", [])
                for h in rows:
                    src = h.get("_source", {})
                    for c in src.get("ciks", []) or []:
                        try:
                            hits.setdefault(int(c), set()).add(date.fromisoformat(src["file_date"]))
                        except (KeyError, ValueError):
                            continue
                frm += len(rows)
                total = int(data.get("hits", {}).get("total", {}).get("value", 0))
                if not rows or frm >= total:
                    break
            cursor = stop + timedelta(days=1)
        out = {k: sorted(v) for k, v in hits.items()}
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": time.time(), "query": RIGHTS_OFFERING_QUERY, "forms": RIGHTS_OFFERING_FORMS,
                           "hits": {str(k): [d.isoformat() for d in v] for k, v in out.items()}}, f)
        except Exception:
            pass
        return out

    def _fts(self, start: date, end: date, frm: int) -> Optional[Dict[str, Any]]:
        params = {"q": RIGHTS_OFFERING_QUERY, "forms": RIGHTS_OFFERING_FORMS, "dateRange": "custom",
                  "startdt": start.isoformat(), "enddt": end.isoformat(), "from": frm}
        for attempt in range(4):
            with self.sec._lock:
                wait = self.sec.MIN_INTERVAL_S - (time.time() - self.sec._last_request)
                if wait > 0:
                    time.sleep(wait)
                self.sec._last_request = time.time()
            try:
                resp = self.sec._session.get(FTS_URL, params=params, timeout=60,
                                             headers={"User-Agent": self.sec.user_agent, "Accept-Encoding": "gzip, deflate"})
                if resp.status_code == 200:
                    return resp.json()
            except Exception as e:
                logger.debug("[SEC] full-text search failed (attempt %d): %s", attempt + 1, e)
            time.sleep(1.5 * (2 ** attempt))
        return None


def rights_days_for(rights: Optional[Dict[int, List[date]]], cik: Optional[int]) -> List[date]:
    if not rights or cik is None:
        return []
    return rights.get(int(cik), [])
