"""
SEC EDGAR Fundamentals
======================
P/E, debt/equity and current ratio computed from companies' own XBRL filings via the SEC's
public JSON API (https://data.sec.gov). Unlike Yahoo's quoteSummary (which throttles or blocks
shared cloud IPs such as GitHub Actions runners), EDGAR is built for programmatic access and
works from servers. It also records when each number was filed, so fundamentals can be taken
point-in-time (no look-ahead) for backtests.

Definitions (chosen to match Yahoo's conventions closely):
  * EPS (TTM): trailing-twelve-month diluted EPS from 10-K / 10-Q facts.
      - Period ending on a fiscal year end: the annual figure.
      - Otherwise: last annual + current year-to-date - prior year-to-date (standard TTM build).
      - Fallback: sum of the last four consecutive quarters.
      - Split check: if reported-EPS TTM disagrees with net income(TTM) / latest diluted shares by
        more than 35%, a split happened between filings and the net-income version is used.
  * P/E: price / EPS(TTM); None when EPS(TTM) <= 0 (no meaningful P/E for loss makers).
  * Current ratio: AssetsCurrent / LiabilitiesCurrent at the latest balance-sheet date.
  * Debt/equity: total debt (long-term debt incl. current maturities + short-term borrowings or
    commercial paper + finance and operating lease liabilities, as Yahoo's Total Debt) / total equity
    incl. non-controlling interest. None when equity <= 0 or when borrowings cannot be identified.

SEC fair-access rules require a User-Agent with a contact address. It is read from the
SEC_USER_AGENT environment variable (local .env / GitHub secret); without it the provider is
disabled and callers fall back to other sources. Requests are throttled below 10/second.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "cache", "fundamentals")

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Periodic report forms whose financial statements we trust for these metrics
REPORT_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-QT", "20-F", "20-F/A", "40-F", "40-F/A"}

EPS_TAGS = (
    "EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic",
    "IncomeLossFromContinuingOperationsPerDilutedShare",
)
NET_INCOME_TAGS = (
    "NetIncomeLossAvailableToCommonStockholdersDiluted", "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLoss",
)
DILUTED_SHARES_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted")
EQUITY_TAGS = ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity")
DEBT_TAGS = (
    "LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "ShortTermBorrowings", "CommercialPaper", "DebtCurrent",
    "FinanceLeaseLiability", "FinanceLeaseLiabilityNoncurrent", "FinanceLeaseLiabilityCurrent",
)
# Debt reported under other labels (REITs, small caps); used only when the main debt tags are absent
OTHER_DEBT_TAGS = (
    "SecuredDebt", "UnsecuredDebt", "SecuredLongTermDebt", "UnsecuredLongTermDebt", "NotesPayable",
    "LongTermNotesPayable", "SeniorNotes", "ConvertibleNotesPayable", "LineOfCredit", "LongTermLineOfCredit",
    "OtherLongTermDebt",
)
LEASE_TAGS = ("OperatingLeaseLiability", "OperatingLeaseLiabilityNoncurrent", "OperatingLeaseLiabilityCurrent")
# A split between filings makes the EPS-based and the (split-invariant) net-income-based EPS disagree
SPLIT_MISMATCH_TOLERANCE = 0.35

# EPS(TTM) older than this is treated as stale (company stopped filing / delisted)
MAX_EPS_AGE_DAYS = 200
MAX_BALANCE_AGE_DAYS = 200


@dataclass
class SecFundamentals:
    eps_ttm: Optional[float] = None
    eps_period_end: Optional[str] = None
    eps_method: Optional[str] = None
    current_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    balance_sheet_date: Optional[str] = None
    total_debt: Optional[float] = None
    total_equity: Optional[float] = None

    def pe_ratio(self, price: Optional[float]) -> Optional[float]:
        if price is None or self.eps_ttm is None or self.eps_ttm <= 0 or price <= 0:
            return None
        return round(float(price) / self.eps_ttm, 4)

    def has_data(self) -> bool:
        return any(v is not None for v in (self.eps_ttm, self.current_ratio, self.debt_to_equity))

    @property
    def negative_equity(self) -> Optional[bool]:
        """True when total equity is zero or negative (D/E is then undefined); None when unknown."""
        if self.total_equity is None:
            return None
        return self.total_equity <= 0


# ----------------------------------------------------------------------------
# Pure extraction (unit-testable, point-in-time aware)
# ----------------------------------------------------------------------------
def _d(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s[:10])


def _facts(gaap: Dict[str, Any], tag: str, unit: str, as_of: Optional[_dt.date]) -> List[Dict[str, Any]]:
    items = (gaap.get(tag) or {}).get("units", {}).get(unit, [])
    out = []
    for f in items:
        if f.get("form") not in REPORT_FORMS or f.get("val") is None or not f.get("end"):
            continue
        if as_of is not None and (not f.get("filed") or _d(f["filed"]) > as_of):
            continue
        out.append(f)
    return out


def _latest_by_key(facts: Iterable[Dict[str, Any]], key) -> Dict[Any, Dict[str, Any]]:
    """Keep the most recently filed version of each fact (restatements supersede originals)."""
    best: Dict[Any, Dict[str, Any]] = {}
    for f in facts:
        k = key(f)
        if k not in best or f.get("filed", "") > best[k].get("filed", ""):
            best[k] = f
    return best


def _duration_days(f: Dict[str, Any]) -> Optional[int]:
    if not f.get("start"):
        return None
    return (_d(f["end"]) - _d(f["start"])).days + 1


def _near(a: _dt.date, b: _dt.date, tol_days: int) -> bool:
    return abs((a - b).days) <= tol_days


def compute_ttm(gaap: Dict[str, Any], tags: Tuple[str, ...], unit: str, as_of: Optional[_dt.date] = None,
                reference_date: Optional[_dt.date] = None) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """Trailing-twelve-month value of a flow item -> (value, period_end, method)."""
    ref = reference_date or as_of or _dt.date.today()
    for tag in tags:
        raw = _facts(gaap, tag, unit, as_of)
        if not raw:
            continue
        facts = list(_latest_by_key((f for f in raw if f.get("start")), key=lambda f: (f["start"], f["end"])).values())
        facts = [dict(f, _days=_duration_days(f)) for f in facts]
        facts = [f for f in facts if f["_days"] and 80 <= f["_days"] <= 380]
        if not facts:
            continue
        annual = [f for f in facts if 350 <= f["_days"] <= 380]
        quarters = [f for f in facts if 80 <= f["_days"] <= 100]
        latest_end = max(_d(f["end"]) for f in facts)
        if (ref - latest_end).days > MAX_EPS_AGE_DAYS:
            return None, latest_end.isoformat(), "stale"

        # 1. Latest period is a fiscal year end
        for a in annual:
            if _d(a["end"]) == latest_end:
                return float(a["val"]), latest_end.isoformat(), f"annual:{tag}"

        # 2. Last annual + current YTD - prior YTD
        prior_annuals = sorted((a for a in annual if _d(a["end"]) < latest_end), key=lambda a: a["end"])
        if prior_annuals:
            A = prior_annuals[-1]
            a_start, a_end = _d(A["start"]), _d(A["end"])
            ytd_cur = next((f for f in facts if _d(f["end"]) == latest_end
                            and _near(_d(f["start"]), a_end + _dt.timedelta(days=1), 5)
                            and f["_days"] < 350), None)
            if ytd_cur is not None:
                prior_end = latest_end - _dt.timedelta(days=365)
                ytd_prior = next((f for f in facts if _near(_d(f["end"]), prior_end, 10)
                                  and _near(_d(f["start"]), a_start, 5)
                                  and abs(f["_days"] - ytd_cur["_days"]) <= 10), None)
                if ytd_prior is not None:
                    val = float(A["val"]) + float(ytd_cur["val"]) - float(ytd_prior["val"])
                    return round(val, 4), latest_end.isoformat(), f"annual+ytd:{tag}"

        # 3. Four consecutive quarters ending at the latest period
        q_by_end = {_d(q["end"]): q for q in quarters}
        chain = []
        end = latest_end
        for _ in range(4):
            q = q_by_end.get(end) or next((q for e, q in q_by_end.items() if _near(e, end, 7)), None)
            if q is None:
                break
            chain.append(q)
            end = _d(q["start"]) - _dt.timedelta(days=1)
        if len(chain) == 4:
            return round(sum(float(q["val"]) for q in chain), 4), latest_end.isoformat(), f"4q:{tag}"
        return None, latest_end.isoformat(), "insufficient"
    return None, None, "no_facts"


def latest_diluted_shares(gaap: Dict[str, Any], period_end: Optional[str], as_of: Optional[_dt.date] = None) -> Optional[float]:
    """Diluted weighted shares for the period ending at period_end (the newest share basis)."""
    if not period_end:
        return None
    pe = _d(period_end)
    for tag in DILUTED_SHARES_TAGS:
        facts = [f for f in _facts(gaap, tag, "shares", as_of) if f.get("start") and _near(_d(f["end"]), pe, 7)]
        if facts:
            # shortest duration ending at that date (the quarter) reflects the current share basis
            candidates = list(_latest_by_key(facts, key=lambda f: (f["start"], f["end"])).values())
            best = min(candidates, key=lambda f: _duration_days(f) or 999)
            if best.get("val"):
                return float(best["val"])
    return None


def compute_eps_ttm(gaap: Dict[str, Any], as_of: Optional[_dt.date] = None,
                    reference_date: Optional[_dt.date] = None) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """
    Trailing-twelve-month diluted EPS -> (eps, period_end, method).

    Reported EPS is summed across filings, so a stock split between filings mixes share bases
    (e.g. pre-split annual EPS + post-split year-to-date EPS). Net income is split-invariant, so
    NI(TTM) / latest diluted shares is computed as a cross-check and used when they disagree.
    """
    eps, eps_end, method = compute_ttm(gaap, EPS_TAGS, "USD/shares", as_of, reference_date)
    if method == "stale":
        return None, eps_end, method
    ni, ni_end, ni_method = compute_ttm(gaap, NET_INCOME_TAGS, "USD", as_of, reference_date)
    shares = latest_diluted_shares(gaap, ni_end, as_of) if ni is not None else None
    eps_from_ni = round(ni / shares, 4) if (ni is not None and shares) else None
    if eps is None:
        if eps_from_ni is not None and ni_method != "stale":
            return eps_from_ni, ni_end, f"ni/shares:{ni_method}"
        return None, eps_end, method
    if eps_from_ni is not None and eps_end == ni_end:
        denom = max(abs(eps_from_ni), 1e-9)
        if abs(eps - eps_from_ni) / denom > SPLIT_MISMATCH_TOLERANCE:
            return eps_from_ni, ni_end, f"ni/shares(split-check):{ni_method}"
    return eps, eps_end, method


def compute_balance_sheet(gaap: Dict[str, Any], as_of: Optional[_dt.date] = None,
                          reference_date: Optional[_dt.date] = None) -> Dict[str, Any]:
    """Current ratio and debt/equity at the latest balance-sheet date (all items from that date)."""
    ref = reference_date or as_of or _dt.date.today()
    inst: Dict[str, Dict[_dt.date, float]] = {}
    for tag in ("AssetsCurrent", "LiabilitiesCurrent") + EQUITY_TAGS + DEBT_TAGS + OTHER_DEBT_TAGS + LEASE_TAGS:
        latest = _latest_by_key((f for f in _facts(gaap, tag, "USD", as_of) if not f.get("start")),
                                key=lambda f: f["end"])
        inst[tag] = {_d(e): float(f["val"]) for e, f in latest.items()}

    anchor_dates = set(inst["AssetsCurrent"]) | set(inst["StockholdersEquity"]) | \
        set(inst["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])
    if not anchor_dates:
        return {}
    D = max(anchor_dates)
    if (ref - D).days > MAX_BALANCE_AGE_DAYS:
        return {"balance_sheet_date": D.isoformat(), "stale": True}

    def v(tag: str) -> Optional[float]:
        return inst.get(tag, {}).get(D)

    out: Dict[str, Any] = {"balance_sheet_date": D.isoformat()}
    ca, cl = v("AssetsCurrent"), v("LiabilitiesCurrent")
    if ca is not None and cl is not None and cl > 0:
        out["current_ratio"] = round(ca / cl, 4)

    equity = next((v(t) for t in EQUITY_TAGS if v(t) is not None), None)

    # Long-term debt including current maturities
    ltd = v("LongTermDebt")
    if ltd is None:
        nc = v("LongTermDebtNoncurrent")
        if nc is None:
            nc = v("LongTermDebtAndCapitalLeaseObligations")
        cur = v("LongTermDebtCurrent")
        if cur is None:
            cur = v("LongTermDebtAndCapitalLeaseObligationsCurrent")
        if nc is not None or cur is not None:
            ltd = (nc or 0.0) + (cur or 0.0)
    # Short-term borrowings (commercial paper is usually a component of short-term borrowings)
    short = v("ShortTermBorrowings")
    if short is None:
        short = v("CommercialPaper")
    if short is None and v("DebtCurrent") is not None and v("LongTermDebtCurrent") is not None:
        short = max(0.0, v("DebtCurrent") - v("LongTermDebtCurrent"))
    # Finance (capital) leases count as debt
    leases = v("FinanceLeaseLiability")
    if leases is None and (v("FinanceLeaseLiabilityNoncurrent") is not None or v("FinanceLeaseLiabilityCurrent") is not None):
        leases = (v("FinanceLeaseLiabilityNoncurrent") or 0.0) + (v("FinanceLeaseLiabilityCurrent") or 0.0)
    if v("LongTermDebtAndCapitalLeaseObligations") is not None and v("LongTermDebt") is None:
        leases = None  # already included in the combined debt-and-lease tag
    # Operating lease liabilities are debt-like obligations under ASC 842 (Yahoo's Total Debt and
    # rating agencies include them); omitting them understates leverage for lease-heavy businesses.
    op_leases = v("OperatingLeaseLiability")
    if op_leases is None and (v("OperatingLeaseLiabilityNoncurrent") is not None or v("OperatingLeaseLiabilityCurrent") is not None):
        op_leases = (v("OperatingLeaseLiabilityNoncurrent") or 0.0) + (v("OperatingLeaseLiabilityCurrent") or 0.0)

    borrowings = [x for x in (ltd, short) if x is not None]
    if not borrowings:
        # Debt under other labels (REITs / small caps), summed only when the main tags are absent
        other = [v(t) for t in OTHER_DEBT_TAGS if v(t) is not None]
        if other:
            borrowings = [float(sum(other))]
    ever_tagged_debt = any(inst.get(t) for t in DEBT_TAGS + OTHER_DEBT_TAGS)
    if borrowings:
        debt = float(sum(borrowings)) + (leases or 0.0) + (op_leases or 0.0)
    elif not ever_tagged_debt:
        # No borrowing tag in any period: treat as no borrowings (leases still count)
        debt = (leases or 0.0) + (op_leases or 0.0)
    else:
        debt = None  # borrowings tagged in other periods but not on this date: unknown, never assume zero
    if debt is not None:
        out["total_debt"] = debt
    if equity is not None:
        out["total_equity"] = equity
    if debt is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = round(debt / equity, 4)
    return out


def extract_fundamentals(company_facts: Dict[str, Any], as_of: Optional[_dt.date] = None,
                         reference_date: Optional[_dt.date] = None) -> SecFundamentals:
    gaap = (company_facts or {}).get("facts", {}).get("us-gaap", {})
    if not gaap:
        return SecFundamentals()
    eps, eps_end, method = compute_eps_ttm(gaap, as_of, reference_date)
    bs = compute_balance_sheet(gaap, as_of, reference_date)
    return SecFundamentals(
        eps_ttm=eps,
        eps_period_end=eps_end,
        eps_method=method,
        current_ratio=bs.get("current_ratio"),
        debt_to_equity=bs.get("debt_to_equity"),
        balance_sheet_date=bs.get("balance_sheet_date"),
        total_debt=bs.get("total_debt"),
        total_equity=bs.get("total_equity"),
    )


# ----------------------------------------------------------------------------
# Network client with throttling and caching
# ----------------------------------------------------------------------------
class SecFundamentalsProvider:
    MIN_INTERVAL_S = 0.15        # < 7 requests/second (SEC limit: 10/second)
    MAX_ATTEMPTS = 4
    TICKER_MAP_TTL_DAYS = 7
    SUMMARY_TTL_HOURS = 72       # filings change quarterly; P/E is recomputed from the live price

    def __init__(self, user_agent: Optional[str] = None, cache_dir: str = DEFAULT_CACHE_DIR,
                 raw_facts_dir: Optional[str] = None):
        if user_agent is None and not os.environ.get("SEC_USER_AGENT"):
            try:  # local runs keep the contact in the project's (gitignored) .env
                from dotenv import load_dotenv
                load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
            except Exception:
                pass
        self.user_agent = (user_agent if user_agent is not None else os.environ.get("SEC_USER_AGENT", "")).strip()
        self.cache_dir = cache_dir
        self.raw_facts_dir = raw_facts_dir  # optional on-disk copy of full company facts (backtests)
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._ticker_map: Optional[Dict[str, int]] = None
        self._summaries: Optional[Dict[str, Any]] = None
        self.stats = {"requests": 0, "ok": 0, "failed": 0, "cache_hits": 0, "no_cik": 0}
        if not self.available:
            logger.warning("[SEC] SEC_USER_AGENT is not set; SEC fundamentals disabled (SEC requires a contact User-Agent).")

    @property
    def available(self) -> bool:
        return "@" in self.user_agent

    # -- HTTP -------------------------------------------------------------
    def _get_json(self, url: str) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        for attempt in range(self.MAX_ATTEMPTS):
            with self._lock:
                wait = self.MIN_INTERVAL_S - (time.time() - self._last_request)
                if wait > 0:
                    time.sleep(wait)
                self._last_request = time.time()
                self.stats["requests"] += 1
            try:
                resp = self._session.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    self.stats["ok"] += 1
                    return resp.json()
                if resp.status_code == 404:
                    return None
                logger.debug("[SEC] %s -> HTTP %s (attempt %d)", url, resp.status_code, attempt + 1)
            except Exception as e:
                logger.debug("[SEC] %s failed (attempt %d): %s", url, attempt + 1, e)
            time.sleep(1.5 * (2 ** attempt))
        self.stats["failed"] += 1
        return None

    # -- Ticker -> CIK ----------------------------------------------------
    def _cache_path(self, name: str) -> str:
        os.makedirs(self.cache_dir, exist_ok=True)
        return os.path.join(self.cache_dir, name)

    def ticker_map(self) -> Dict[str, int]:
        if self._ticker_map is not None:
            return self._ticker_map
        path = self._cache_path("sec_company_tickers.json")
        data = None
        try:
            if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < self.TICKER_MAP_TTL_DAYS * 86400:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = None
        if data is None:
            data = self._get_json(TICKER_MAP_URL)
            if data:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                except Exception:
                    pass
        mapping: Dict[str, int] = {}
        for row in (data or {}).values():
            t = str(row.get("ticker", "")).upper()
            if t:
                mapping.setdefault(t, int(row["cik_str"]))
        self._ticker_map = mapping
        return mapping

    def cik_for(self, ticker: str) -> Optional[int]:
        t = ticker.upper()
        m = self.ticker_map()
        return m.get(t) or m.get(t.replace("-", ".")) or m.get(t.replace(".", "-"))

    # -- Company facts ------------------------------------------------------
    def company_facts(self, cik: int) -> Optional[Dict[str, Any]]:
        if self.raw_facts_dir:
            os.makedirs(self.raw_facts_dir, exist_ok=True)
            raw_path = os.path.join(self.raw_facts_dir, f"CIK{cik:010d}.json")
            if os.path.exists(raw_path):
                try:
                    with open(raw_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        data = self._get_json(COMPANY_FACTS_URL.format(cik=cik))
        if data and self.raw_facts_dir:
            try:
                with open(os.path.join(self.raw_facts_dir, f"CIK{cik:010d}.json"), "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception:
                pass
        return data

    # -- Summaries (production) ----------------------------------------------
    def _load_summaries(self) -> Dict[str, Any]:
        if self._summaries is None:
            try:
                with open(self._cache_path("sec_summaries.json"), "r", encoding="utf-8") as f:
                    self._summaries = json.load(f)
            except Exception:
                self._summaries = {}
        return self._summaries

    def _save_summaries(self) -> None:
        try:
            with open(self._cache_path("sec_summaries.json"), "w", encoding="utf-8") as f:
                json.dump(self._summaries or {}, f)
        except Exception as e:
            logger.debug("[SEC] could not save summary cache: %s", e)

    def fundamentals(self, ticker: str) -> Optional[SecFundamentals]:
        """Latest fundamentals for a ticker (cached for SUMMARY_TTL_HOURS), or None if unavailable."""
        if not self.available:
            return None
        t = ticker.upper()
        with self._lock:
            summaries = self._load_summaries()
            cached = summaries.get(t)
        if cached and (time.time() - cached.get("_fetched_at", 0)) < self.SUMMARY_TTL_HOURS * 3600:
            self.stats["cache_hits"] += 1
            fields = {k: v for k, v in cached.items() if not k.startswith("_")}
            return SecFundamentals(**fields)
        cik = self.cik_for(t)
        if cik is None:
            self.stats["no_cik"] += 1
            return None
        facts = self.company_facts(cik)
        if not facts:
            return None
        result = extract_fundamentals(facts)
        with self._lock:
            summaries = self._load_summaries()
            summaries[t] = {**asdict(result), "_fetched_at": time.time()}
            self._save_summaries()
        return result

    def fundamentals_as_of(self, ticker: str, as_of: _dt.date) -> SecFundamentals:
        """Point-in-time fundamentals using only facts filed on or before `as_of` (backtests)."""
        cik = self.cik_for(ticker)
        if cik is None:
            return SecFundamentals()
        facts = self.company_facts(cik)
        return extract_fundamentals(facts or {}, as_of=as_of, reference_date=as_of)


_DEFAULT_PROVIDER: Optional[SecFundamentalsProvider] = None
_DEFAULT_LOCK = threading.Lock()


def get_sec_provider() -> SecFundamentalsProvider:
    global _DEFAULT_PROVIDER
    with _DEFAULT_LOCK:
        if _DEFAULT_PROVIDER is None:
            _DEFAULT_PROVIDER = SecFundamentalsProvider()
        return _DEFAULT_PROVIDER
