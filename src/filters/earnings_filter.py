"""
Earnings Risk Filter Module
============================
Protects trading capital by preventing signal emissions during dangerous
pre-earnings volatility blackout windows, with explicit allowance for post-earnings PEAD
and structural exemption for Sector ETFs.

Multi-Layer Architecture:
1. Local JSON Cache (data/cache/earnings_dates_cache.json)
2. Supabase Persistent Cache (earnings_calendar table)
3. External Provider with rate-limit resilience, retry & backoff (yfinance)
4. Atomic persistence to both local JSON and Supabase
5. Fail-closed safety on genuine absence of reliable data
"""

import os
import json
import time
import random
import logging
import datetime
from pathlib import Path
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from src.quant_config import (
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_CACHE_TTL_SECONDS,
    EARNINGS_CATALYST_MAX_AGE_DAYS,
    NEWS_CATALYST_MAX_AGE_DAYS,
    REASON_EARNINGS_POSITIVE_CATALYST_OVERRIDE,
    REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK,
    REASON_EARNINGS_DATE_UNKNOWN_NO_CATALYST,
    REASON_EARNINGS_DATE_UNKNOWN_POSITIVE,
    REASON_EARNINGS_DATE_UNKNOWN_NEGATIVE,
    REASON_EARNINGS_OUTSIDE_BLACKOUT,
    REASON_EARNINGS_BLACKOUT_BLOCK,
    REASON_SECTOR_ETF_EXEMPT,
)

DEFAULT_EARNINGS_CACHE_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "earnings_dates_cache.json"
)


class EarningsStatus(str, Enum):
    KNOWN_UPCOMING = "KNOWN_UPCOMING"
    KNOWN_CLEAR = "KNOWN_CLEAR"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"


class ProviderCircuitBreaker:
    """
    Provider-level circuit breaker to prevent cascade failures and rate-limit loops.
    Trips to OPEN when provider repeatedly returns 429 Too Many Requests.
    """
    def __init__(self, failure_threshold: int = 4, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.consecutive_rate_limits = 0
        self.total_rate_limits = 0
        self.state = "CLOSED"  # "CLOSED", "OPEN", "HALF_OPEN"
        self.tripped_at: Optional[float] = None
        self._lock = threading.Lock()

    def record_success(self):
        with self._lock:
            self.consecutive_rate_limits = 0
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                logger.info("[CIRCUIT BREAKER CLOSED] Provider recovered successfully.")

    def record_rate_limit(self):
        with self._lock:
            self.consecutive_rate_limits += 1
            self.total_rate_limits += 1
            if self.consecutive_rate_limits >= self.failure_threshold:
                if self.state != "OPEN":
                    logger.warning(
                        "[CIRCUIT BREAKER OPEN] Provider hit %d consecutive rate limits. "
                        "Suspending external earnings requests. Using cached records / UNKNOWN fallback.",
                        self.consecutive_rate_limits,
                    )
                self.state = "OPEN"
                self.tripped_at = time.time()

    def can_request(self) -> bool:
        with self._lock:
            if self.state == "CLOSED":
                return True
            if self.state == "OPEN":
                if self.tripped_at and (time.time() - self.tripped_at) > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    logger.info("[CIRCUIT BREAKER HALF_OPEN] Probing provider for recovery.")
                    return True
                return False
            # HALF_OPEN: allow a single probe
            return True

    def reset(self):
        with self._lock:
            self.consecutive_rate_limits = 0
            self.total_rate_limits = 0
            self.state = "CLOSED"
            self.tripped_at = None


GLOBAL_CIRCUIT_BREAKER = ProviderCircuitBreaker()


class ProviderBudget:
    """
    Process-wide central request budget for external earnings provider.
    Enforces a strict upper bound across startup preload, PEAD strategy,
    candidate evaluation, and retries.
    """
    def __init__(self, initial_budget: Optional[int] = None, max_requests: Optional[int] = None):
        budget_val = max_requests if max_requests is not None else initial_budget
        self._budget: Optional[int] = budget_val
        self._total_allocated: Optional[int] = budget_val
        self._total_consumed: int = 0
        self._lock = threading.Lock()

    def set_budget(self, budget: Optional[int]):
        with self._lock:
            if budget is None:
                self._budget = None
                self._total_allocated = None
            else:
                self._budget = max(0, int(budget))
                self._total_allocated = self._budget
            self._total_consumed = 0

    def can_request(self) -> bool:
        with self._lock:
            if self._budget is None:
                return True
            return self._budget > 0

    def consume(self) -> bool:
        """Atomically consume one request from the budget. Returns True if granted, False if exhausted."""
        with self._lock:
            if self._budget is None:
                self._total_consumed += 1
                return True
            if self._budget > 0:
                self._budget -= 1
                self._total_consumed += 1
                return True
            return False

    def record_request(self) -> bool:
        """Alias for consume() for recording a request against the budget."""
        return self.consume()

    def get_remaining(self) -> Optional[int]:
        with self._lock:
            return self._budget

    def get_allocated(self) -> Optional[int]:
        with self._lock:
            return self._total_allocated

    def get_consumed(self) -> int:
        with self._lock:
            return self._total_consumed

    def reset(self, budget: Optional[int] = None):
        self.set_budget(budget)


GLOBAL_PROVIDER_BUDGET = ProviderBudget(None)


@dataclass
class EarningsTracker:
    universe_size: int = 0
    local_cache_hits: int = 0
    supabase_cache_hits: int = 0
    provider_requests: int = 0
    provider_successful: int = 0
    provider_rate_limited: int = 0
    provider_failed: int = 0
    unknown_count: int = 0
    known_upcoming_count: int = 0
    known_clear_count: int = 0
    positive_catalyst_count: int = 0
    negative_catalyst_count: int = 0

    negative_earnings_blocks: int = 0
    blackout_blocks: int = 0
    positive_overrides: int = 0
    unknown_proceeds: int = 0
    provider_fetching_disabled: bool = True

    def reset(self):
        self.universe_size = 0
        self.local_cache_hits = 0
        self.supabase_cache_hits = 0
        self.provider_requests = 0
        self.provider_successful = 0
        self.provider_rate_limited = 0
        self.provider_failed = 0
        self.unknown_count = 0
        self.known_upcoming_count = 0
        self.known_clear_count = 0
        self.positive_catalyst_count = 0
        self.negative_catalyst_count = 0
        self.negative_earnings_blocks = 0
        self.blackout_blocks = 0
        self.positive_overrides = 0
        self.unknown_proceeds = 0
        self.provider_fetching_disabled = True

    def format_summary(self) -> str:
        budget_alloc = GLOBAL_PROVIDER_BUDGET.get_allocated()
        budget_alloc_str = str(budget_alloc) if budget_alloc is not None else "unlimited"
        budget_rem = GLOBAL_PROVIDER_BUDGET.get_remaining()
        budget_rem_str = str(budget_rem) if budget_rem is not None else "unlimited"
        budget_consumed = GLOBAL_PROVIDER_BUDGET.get_consumed()
        return f"""
============================================================
EARNINGS DATA SUMMARY
---------------------
Universe: {self.universe_size}
Local cache hits: {self.local_cache_hits}
Supabase cache hits: {self.supabase_cache_hits}
Provider fetching disabled: {self.provider_fetching_disabled}
Provider request budget: {budget_alloc_str} (remaining: {budget_rem_str})
Provider requests made: {self.provider_requests} (consumed: {budget_consumed})
Provider successful: {self.provider_successful}
Provider rate limited: {self.provider_rate_limited}
Provider failed: {self.provider_failed}
Unknown: {self.unknown_count}
Known upcoming: {self.known_upcoming_count}
Known clear: {self.known_clear_count}
Positive catalyst: {self.positive_catalyst_count}
Negative catalyst: {self.negative_catalyst_count}

EARNINGS IMPACT
---------------
Negative earnings blocks: {self.negative_earnings_blocks}
Blackout blocks: {self.blackout_blocks}
Positive overrides: {self.positive_overrides}
Unknown/no-info proceeds: {self.unknown_proceeds}
============================================================
""".strip()


GLOBAL_EARNINGS_TRACKER = EarningsTracker()

_SESSION_FETCHED_TICKERS: set = set()


def reset_session_cache():
    global _SESSION_FETCHED_TICKERS
    _SESSION_FETCHED_TICKERS.clear()


def normalize_strategy_key(strategy: str) -> str:
    """Normalize any strategy string variant."""
    if not strategy:
        return "trend_following"
    s = str(strategy).strip().lower().replace("-", "_").replace(" ", "_")
    if "52" in s or "breakout" in s or "high" in s:
        return "52w_high_breakout"
    if "trend" in s:
        return "trend_following"
    if "pullback" in s:
        return "pullback_recovery"
    if "cross" in s or "momentum" in s:
        return "cross_sectional_momentum"
    if "pead" in s or "earnings" in s:
        return "pead"
    if "sector" in s or "rotation" in s:
        return "sector_rotation"
    if "mean" in s or "reversion" in s:
        return "mean_reversion"
    return s


def normalize_date_str(val: Any) -> Optional[str]:
    """
    Safely converts various date/datetime/string representations to ISO YYYY-MM-DD.
    Returns None for invalid, None, NaN, or non-date values.
    """
    if val is None:
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        if hasattr(val, "date") and callable(val.date):
            return val.date().isoformat()
        return val.isoformat()[:10]
    if hasattr(val, "strftime") and callable(val.strftime):
        try:
            return val.strftime("%Y-%m-%d")
        except Exception:
            pass
    s = str(val).strip()
    if not s or s.lower() in ("none", "nat", "nan", "null"):
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _get_record_age_seconds(
    record: Dict[str, Any],
    as_of: Optional[Union[str, datetime.datetime, datetime.date]] = None,
) -> Optional[float]:
    updated_at_val = record.get("updated_at") or record.get("cached_at")
    if not updated_at_val:
        return None
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if as_of is not None:
            if isinstance(as_of, str):
                s = as_of.replace("Z", "+00:00")
                if len(s) == 10:
                    if s == now_utc.date().isoformat():
                        ref_dt = now_utc
                    else:
                        ref_dt = datetime.datetime.combine(
                            datetime.date.fromisoformat(s),
                            datetime.time(23, 59, 59),
                            tzinfo=datetime.timezone.utc,
                        )
                else:
                    ref_dt = datetime.datetime.fromisoformat(s)
            elif isinstance(as_of, datetime.datetime):
                ref_dt = as_of
            elif isinstance(as_of, datetime.date):
                if as_of == now_utc.date():
                    ref_dt = now_utc
                else:
                    ref_dt = datetime.datetime.combine(as_of, datetime.time(23, 59, 59), tzinfo=datetime.timezone.utc)
            else:
                ref_dt = now_utc
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=datetime.timezone.utc)
        else:
            ref_dt = now_utc

        if isinstance(updated_at_val, (int, float)):
            dt = datetime.datetime.fromtimestamp(updated_at_val, tz=datetime.timezone.utc)
        elif isinstance(updated_at_val, str):
            s = updated_at_val.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
        elif isinstance(updated_at_val, datetime.datetime):
            dt = updated_at_val
        elif isinstance(updated_at_val, datetime.date):
            dt = datetime.datetime.combine(updated_at_val, datetime.time.min, tzinfo=datetime.timezone.utc)
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        diff = (ref_dt - dt).total_seconds()
        if -60.0 <= diff < 0.0:
            return 0.0
        return diff
    except Exception:
        return None


def is_earnings_record_fresh(
    record: Optional[Dict[str, Any]],
    max_age_seconds: int = EARNINGS_CACHE_TTL_SECONDS,
    today_iso: Optional[str] = None,
) -> bool:
    """
    Evaluates freshness of an earnings record based on event lifecycle and strict age limits:
    1. Future upcoming date (next_earnings_date >= today): Fresh ONLY if record age is known
       and strictly < 14 days (14 * 86400s). Stale if older or age is unknown.
    2. Past upcoming date (next_earnings_date < today): Stale (earnings occurred, needs update).
    3. Known clear (no earnings scheduled): Fresh if record age is known and < 7 days (7 * 86400s).
    4. General record without date / UNKNOWN: Fresh only if age <= max_age_seconds (default 24h).
    """
    if not record or not isinstance(record, dict):
        return False

    if not today_iso:
        today_iso = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    next_d = normalize_date_str(record.get("next_earnings_date") or record.get("next_earnings"))
    age_seconds = _get_record_age_seconds(record, as_of=today_iso)

    # 1. If next_earnings_date has passed, company has reported; record is genuinely stale
    if next_d and next_d < today_iso:
        return False

    # 2. If next_earnings_date is in the future, fresh only if confirmed within the last 14 days
    if next_d and next_d >= today_iso:
        if age_seconds is not None:
            return 0 <= age_seconds < (14 * 86400)
        return False

    # 3. If known clear (no earnings scheduled), fresh for up to 7 days
    status = record.get("status")
    if status == EarningsStatus.KNOWN_CLEAR.value:
        if age_seconds is not None:
            return 0 <= age_seconds < (7 * 86400)
        return False

    # 4. Fallback age check for UNKNOWN or unspecified status
    if age_seconds is not None:
        return 0 <= age_seconds <= max_age_seconds

    return False


def load_local_cache(cache_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Safely loads the local JSON cache, tolerating missing or corrupt files."""
    target = Path(cache_path) if cache_path else DEFAULT_EARNINGS_CACHE_FILE
    if not target.exists():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("Local earnings cache at %s is not a dictionary; ignoring", target)
            return {}
    except Exception as e:
        logger.warning("Failed to load local earnings cache from %s: %s", target, e)
        return {}


def save_local_cache(cache_data: Dict[str, Any], cache_path: Optional[Union[str, Path]] = None) -> bool:
    """Atomically saves the local JSON cache via a temporary file."""
    target = Path(cache_path) if cache_path else DEFAULT_EARNINGS_CACHE_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_file = target.with_suffix(f".tmp_{os.getpid()}_{random.randint(1000, 9999)}")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
        os.replace(temp_file, target)
        return True
    except Exception as e:
        logger.error("Failed to atomically save local earnings cache to %s: %s", target, e)
        if "temp_file" in locals() and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        return False


def fetch_supabase_earnings(tickers: List[str], supabase) -> Dict[str, Dict[str, Any]]:
    """Batch-retrieves cached earnings records from Supabase in URL-safe chunks of <= 100 tickers."""
    if not supabase or not tickers:
        return {}
    results = {}
    chunk_size = 100
    for i in range(0, len(tickers), chunk_size):
        chunk = [t.upper() for t in tickers[i : i + chunk_size]]
        try:
            res = supabase.table("earnings_calendar").select("*").in_("ticker", chunk).execute()
            for row in res.data or []:
                t = row.get("ticker", "").upper()
                if not t:
                    continue
                next_d = normalize_date_str(row.get("next_earnings_date"))
                last_d = normalize_date_str(row.get("last_earnings_date"))
                results[t] = {
                    "ticker": t,
                    "next_earnings_date": next_d,
                    "last_earnings_date": last_d,
                    "fiscal_period": row.get("fiscal_period"),
                    "updated_at": row.get("updated_at"),
                    "status": row.get("status"),
                }
        except Exception as e:
            logger.warning("Supabase earnings_calendar query batch failed: %s", e)
    return results


def persist_supabase_earnings(records: List[Dict[str, Any]], supabase) -> int:
    """Batch-upserts newly fetched earnings records to Supabase earnings_calendar in chunks of <= 100."""
    if not supabase or not records:
        return 0
    persisted_count = 0
    chunk_size = 100
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]
        rows = []
        for r in chunk:
            rows.append(
                {
                    "ticker": r["ticker"].upper(),
                    "next_earnings_date": normalize_date_str(r.get("next_earnings_date")),
                    "last_earnings_date": normalize_date_str(r.get("last_earnings_date")),
                    "fiscal_period": r.get("fiscal_period"),
                    "updated_at": r.get("updated_at") or now_iso,
                }
            )
        try:
            res = supabase.table("earnings_calendar").upsert(rows, on_conflict="ticker").execute()
            if res and res.data:
                persisted_count += len(res.data)
            else:
                persisted_count += len(rows)
        except Exception as e:
            logger.error("Failed to batch upsert earnings to Supabase: %s", e)
    return persisted_count


def fetch_single_ticker_provider(
    ticker_sym: str,
    max_retries: int = 2,
    base_delay: float = 1.0,
) -> Tuple[str, Optional[str], Optional[str], Optional[str], str]:
    """
    Fetches earnings schedule for a single ticker from yfinance with retry/backoff.
    Returns: (ticker, next_earnings_date, last_earnings_date, fiscal_period, status)
    """
    sym_upper = ticker_sym.strip().upper()
    _SESSION_FETCHED_TICKERS.add(sym_upper)

    # Circuit breaker check: fail early if provider is currently open
    if not GLOBAL_CIRCUIT_BREAKER.can_request():
        logger.debug("[CIRCUIT BREAKER] External earnings request blocked for %s; returning UNKNOWN.", sym_upper)
        GLOBAL_EARNINGS_TRACKER.provider_failed += 1
        return sym_upper, None, None, None, EarningsStatus.UNKNOWN.value

    # Provider budget check: fail early if budget is exhausted
    if not GLOBAL_PROVIDER_BUDGET.can_request():
        logger.debug("[BUDGET EXHAUSTED] Provider request budget reached limit for %s; returning UNKNOWN.", sym_upper)
        GLOBAL_EARNINGS_TRACKER.provider_failed += 1
        return sym_upper, None, None, None, EarningsStatus.UNKNOWN.value

    for attempt in range(max_retries + 1):
        if not GLOBAL_PROVIDER_BUDGET.consume():
            logger.debug(
                "[BUDGET EXHAUSTED] Provider request budget reached limit for %s at attempt %d; returning UNKNOWN.",
                sym_upper,
                attempt + 1,
            )
            GLOBAL_EARNINGS_TRACKER.provider_failed += 1
            return sym_upper, None, None, None, EarningsStatus.UNKNOWN.value

        GLOBAL_EARNINGS_TRACKER.provider_requests += 1

        try:
            import yfinance as yf

            yf_ticker = yf.Ticker(sym_upper)
            cal = getattr(yf_ticker, "calendar", None)
            next_date = None
            last_date = None
            fiscal_period = None

            if cal is not None and isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed and len(ed) > 0:
                    next_date = normalize_date_str(ed[0])
            elif cal is not None and hasattr(cal, "empty") and not cal.empty:
                if hasattr(cal, "index") and len(cal.index) > 0:
                    next_date = normalize_date_str(cal.index[0])

            # Also check earnings_dates for past or upcoming dates
            try:
                edates = getattr(yf_ticker, "earnings_dates", None)
                if edates is not None and hasattr(edates, "index") and len(edates.index) > 0:
                    today_iso = datetime.date.today().isoformat()
                    past = [
                        normalize_date_str(d)
                        for d in edates.index
                        if normalize_date_str(d) and normalize_date_str(d) <= today_iso
                    ]
                    if past:
                        last_date = max(past)
                    if not next_date:
                        upcoming = [
                            normalize_date_str(d)
                            for d in edates.index
                            if normalize_date_str(d) and normalize_date_str(d) > today_iso
                        ]
                        if upcoming:
                            next_date = min(upcoming)
            except Exception:
                pass

            GLOBAL_CIRCUIT_BREAKER.record_success()
            GLOBAL_EARNINGS_TRACKER.provider_successful += 1
            status = EarningsStatus.KNOWN_UPCOMING.value if next_date else EarningsStatus.KNOWN_CLEAR.value
            return sym_upper, next_date, last_date, fiscal_period, status

        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = "429" in err_str or "too many requests" in err_str or "rate limit" in err_str
            is_timeout = "timeout" in err_str or "timed out" in err_str
            is_network = (
                "connection" in err_str
                or "failed to establish" in err_str
                or "remotedisconnected" in err_str
            )

            if is_rate_limit:
                GLOBAL_CIRCUIT_BREAKER.record_rate_limit()
                GLOBAL_EARNINGS_TRACKER.provider_rate_limited += 1
                if not GLOBAL_CIRCUIT_BREAKER.can_request():
                    logger.warning("[CIRCUIT BREAKER OPEN] Aborting retries for %s due to circuit breaker trip.", sym_upper)
                    GLOBAL_EARNINGS_TRACKER.provider_failed += 1
                    return sym_upper, None, None, None, EarningsStatus.UNKNOWN.value

            if (is_rate_limit or is_timeout or is_network) and attempt < max_retries:
                delay = (base_delay * (2**attempt)) + random.uniform(0.1, 0.4)
                logger.warning(
                    "Earnings fetch for %s hit %s; retrying in %.2fs (attempt %d/%d)",
                    sym_upper,
                    e,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue
            else:
                logger.debug("Earnings fetch skipped/failed for %s: %s", sym_upper, e)
                GLOBAL_EARNINGS_TRACKER.provider_failed += 1
                return sym_upper, None, None, None, EarningsStatus.UNKNOWN.value

    GLOBAL_EARNINGS_TRACKER.provider_failed += 1
    return sym_upper, None, None, None, EarningsStatus.UNKNOWN.value


def fetch_earnings_calendar(
    tickers: List[str],
    supabase=None,
    cache_path: Optional[Union[str, Path]] = None,
    max_workers: int = 4,
    allow_network: bool = True,
    max_provider_fetches: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Populates and retrieves upcoming earnings dates for universe tickers using a multi-layer cache hierarchy:
    1. Local JSON cache (data/cache/earnings_dates_cache.json)
    2. Supabase persistent cache (earnings_calendar table)
    3. External provider (yfinance with rate-limit resilience, retry, backoff)
    4. Persistence: saves newly fetched results to local JSON and Supabase
    5. Fallback safety: uses prior cached data if provider fails; fail-closed UNKNOWN only if no cache exists.
    """
    normalized_tickers = list(dict.fromkeys([t.strip().upper() for t in tickers if t and t.strip()]))
    GLOBAL_EARNINGS_TRACKER.universe_size = len(normalized_tickers)
    calendar_map: Dict[str, Dict[str, Any]] = {}
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_dt.isoformat()
    now_ts = now_dt.timestamp()
    today_iso = now_dt.date().isoformat()

    # 1. Layer 1: Local JSON Cache
    local_cache = load_local_cache(cache_path)
    cache_dirty = False

    for t in normalized_tickers:
        if t in local_cache:
            entry = local_cache[t]
            next_d = normalize_date_str(entry.get("next_earnings_date") or entry.get("next_earnings"))
            last_d = normalize_date_str(entry.get("last_earnings_date") or entry.get("last_earnings"))
            fresh = is_earnings_record_fresh(entry, EARNINGS_CACHE_TTL_SECONDS, today_iso)
            if fresh:
                st = entry.get("status")
                if not st or st in (EarningsStatus.STALE.value, EarningsStatus.UNKNOWN.value):
                    st = EarningsStatus.KNOWN_UPCOMING.value if next_d else EarningsStatus.KNOWN_CLEAR.value
                calendar_map[t] = {
                    "ticker": t,
                    "next_earnings_date": next_d,
                    "last_earnings_date": last_d,
                    "fiscal_period": entry.get("fiscal_period"),
                    "updated_at": entry.get("updated_at") or now_iso,
                    "status": st,
                    "source": "local_cache",
                }
                GLOBAL_EARNINGS_TRACKER.local_cache_hits += 1

    # 2. Layer 2: Supabase Cache for missing tickers
    missing_from_local = [t for t in normalized_tickers if t not in calendar_map]
    if missing_from_local and supabase is not None:
        sb_results = fetch_supabase_earnings(missing_from_local, supabase)
        for sym, row in sb_results.items():
            next_d = normalize_date_str(row.get("next_earnings_date"))
            last_d = normalize_date_str(row.get("last_earnings_date"))
            fresh = is_earnings_record_fresh(row, EARNINGS_CACHE_TTL_SECONDS, today_iso)
            if fresh:
                st = row.get("status") or (
                    EarningsStatus.KNOWN_UPCOMING.value if next_d else EarningsStatus.KNOWN_CLEAR.value
                )
                calendar_map[sym] = {
                    "ticker": sym,
                    "next_earnings_date": next_d,
                    "last_earnings_date": last_d,
                    "fiscal_period": row.get("fiscal_period"),
                    "updated_at": row.get("updated_at") or now_iso,
                    "status": st,
                    "source": "supabase",
                }
                GLOBAL_EARNINGS_TRACKER.supabase_cache_hits += 1
                # Sync into local cache
                local_cache[sym] = {
                    "ticker": sym,
                    "last_earnings": last_d,
                    "next_earnings": next_d,
                    "last_earnings_date": last_d,
                    "next_earnings_date": next_d,
                    "fiscal_period": row.get("fiscal_period"),
                    "updated_at": row.get("updated_at") or now_iso,
                    "cached_at": now_ts,
                    "status": st,
                }
                cache_dirty = True

    # 3. Layer 3: External Provider for remaining missing tickers
    tickers_to_fetch = [t for t in normalized_tickers if t not in calendar_map]
    if max_provider_fetches is not None:
        GLOBAL_PROVIDER_BUDGET.set_budget(max_provider_fetches)

    if not allow_network or not GLOBAL_PROVIDER_BUDGET.can_request():
        tickers_to_fetch = []
    else:
        rem = GLOBAL_PROVIDER_BUDGET.get_remaining()
        if rem is not None and len(tickers_to_fetch) > rem:
            logger.info(
                "[EARNINGS CALENDAR] Bounding provider queries to %d (out of %d uncached tickers) based on request budget.",
                rem,
                len(tickers_to_fetch),
            )
            tickers_to_fetch = tickers_to_fetch[:rem]

    newly_fetched_records: List[Dict[str, Any]] = []

    if tickers_to_fetch and allow_network:
        logger.info(
            "Fetching earnings data for %d tickers via provider (bounded workers=%d)...",
            len(tickers_to_fetch),
            max_workers,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sym = {
                executor.submit(fetch_single_ticker_provider, sym): sym for sym in tickers_to_fetch
            }
            for future in as_completed(future_to_sym):
                sym_orig = future_to_sym[future]
                try:
                    sym_res, next_d, last_d, fiscal_p, fetch_status = future.result()
                    if fetch_status in (
                        EarningsStatus.KNOWN_UPCOMING.value,
                        EarningsStatus.KNOWN_CLEAR.value,
                    ):
                        rec = {
                            "ticker": sym_res,
                            "next_earnings_date": next_d,
                            "last_earnings_date": last_d,
                            "fiscal_period": fiscal_p,
                            "updated_at": now_iso,
                            "status": fetch_status,
                            "source": "provider",
                        }
                        calendar_map[sym_res] = rec
                        newly_fetched_records.append(rec)
                        local_cache[sym_res] = {
                            "ticker": sym_res,
                            "last_earnings": last_d,
                            "next_earnings": next_d,
                            "last_earnings_date": last_d,
                            "next_earnings_date": next_d,
                            "fiscal_period": fiscal_p,
                            "updated_at": now_iso,
                            "cached_at": now_ts,
                            "status": fetch_status,
                        }
                        cache_dirty = True
                    else:
                        # Provider returned UNKNOWN: Check for cached fallback
                        if sym_orig in local_cache:
                            prior = local_cache[sym_orig]
                            prior_next = normalize_date_str(
                                prior.get("next_earnings_date") or prior.get("next_earnings")
                            )
                            prior_last = normalize_date_str(
                                prior.get("last_earnings_date") or prior.get("last_earnings")
                            )
                            is_fresh = is_earnings_record_fresh(prior, EARNINGS_CACHE_TTL_SECONDS)
                            if is_fresh and prior_next and prior_next >= today_iso:
                                fallback_status = EarningsStatus.KNOWN_UPCOMING.value
                            elif is_fresh and not prior_next:
                                fallback_status = EarningsStatus.KNOWN_CLEAR.value
                            else:
                                fallback_status = EarningsStatus.UNKNOWN.value

                            logger.info(
                                "[EARNINGS CACHE] Provider failed for %s, falling back to cached earnings (status: %s)",
                                sym_orig,
                                fallback_status,
                            )
                            calendar_map[sym_orig] = {
                                "ticker": sym_orig,
                                "next_earnings_date": prior_next,
                                "last_earnings_date": prior_last,
                                "fiscal_period": prior.get("fiscal_period"),
                                "updated_at": prior.get("updated_at") or now_iso,
                                "status": fallback_status,
                                "source": "cache_fallback",
                            }
                        else:
                            calendar_map[sym_orig] = {
                                "ticker": sym_orig,
                                "next_earnings_date": None,
                                "last_earnings_date": None,
                                "fiscal_period": None,
                                "updated_at": now_iso,
                                "status": EarningsStatus.UNKNOWN.value,
                                "source": "failed_fetch",
                            }
                except Exception as e:
                    logger.debug("Future execution error for %s: %s", sym_orig, e)
                    if sym_orig in local_cache:
                        prior = local_cache[sym_orig]
                        prior_next = normalize_date_str(
                            prior.get("next_earnings_date") or prior.get("next_earnings")
                        )
                        prior_last = normalize_date_str(
                            prior.get("last_earnings_date") or prior.get("last_earnings")
                        )
                        is_fresh = is_earnings_record_fresh(prior, EARNINGS_CACHE_TTL_SECONDS)
                        if is_fresh and prior_next and prior_next >= today_iso:
                            fallback_status = EarningsStatus.KNOWN_UPCOMING.value
                        elif is_fresh and not prior_next:
                            fallback_status = EarningsStatus.KNOWN_CLEAR.value
                        else:
                            fallback_status = EarningsStatus.UNKNOWN.value

                        calendar_map[sym_orig] = {
                            "ticker": sym_orig,
                            "next_earnings_date": prior_next,
                            "last_earnings_date": prior_last,
                            "fiscal_period": prior.get("fiscal_period"),
                            "updated_at": prior.get("updated_at") or now_iso,
                            "status": fallback_status,
                            "source": "cache_fallback",
                        }
                    else:
                        calendar_map[sym_orig] = {
                            "ticker": sym_orig,
                            "next_earnings_date": None,
                            "last_earnings_date": None,
                            "fiscal_period": None,
                            "updated_at": now_iso,
                            "status": EarningsStatus.UNKNOWN.value,
                            "source": "failed_fetch",
                        }

    # 4. Fill any remaining unresolved tickers (e.g. allow_network=False or bounded)
    for t in normalized_tickers:
        if t not in calendar_map:
            calendar_map[t] = {
                "ticker": t,
                "next_earnings_date": None,
                "last_earnings_date": None,
                "fiscal_period": None,
                "updated_at": now_iso,
                "status": EarningsStatus.UNKNOWN.value,
                "source": "unresolved",
            }

    # 5. Layer 4: Persistence
    if newly_fetched_records or cache_dirty:
        save_local_cache(local_cache, cache_path)
    if newly_fetched_records and supabase is not None:
        persist_supabase_earnings(newly_fetched_records, supabase)

    # 6. Update Tracker status totals
    for rec in calendar_map.values():
        st = rec.get("status")
        if st == EarningsStatus.KNOWN_UPCOMING.value:
            GLOBAL_EARNINGS_TRACKER.known_upcoming_count += 1
        elif st == EarningsStatus.KNOWN_CLEAR.value:
            GLOBAL_EARNINGS_TRACKER.known_clear_count += 1
        else:
            GLOBAL_EARNINGS_TRACKER.unknown_count += 1

    return calendar_map


def resolve_ticker_earnings(
    ticker: str,
    calendar_map: Dict[str, Dict[str, Any]],
    supabase=None,
    cache_path: Optional[Union[str, Path]] = None,
    allow_network: bool = False,
) -> Dict[str, Any]:
    """
    On-demand single-ticker earnings resolver.
    Used during candidate evaluation or PEAD strategy check to resolve missing/unresolved earnings data.
    Flow:
    1. If ticker is in calendar_map and status != UNKNOWN: return cached record.
    2. Check local JSON cache (data/cache/earnings_dates_cache.json).
    3. Check Supabase table ('earnings_calendar').
    4. If allow_network and provider budget allows and circuit breaker allows: fetch from provider (yfinance) via fetch_single_ticker_provider.
    5. Persist to local JSON cache and Supabase.
    6. Update calendar_map and return record.
    """
    sym = ticker.strip().upper()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_dt.isoformat()
    now_ts = now_dt.timestamp()
    today_iso = now_dt.date().isoformat()

    # 1. Already resolved and confirmed in calendar_map or already fetched in session?
    existing = calendar_map.get(sym)
    if existing:
        if existing.get("status") in (
            EarningsStatus.KNOWN_UPCOMING.value,
            EarningsStatus.KNOWN_CLEAR.value,
        ) or sym in _SESSION_FETCHED_TICKERS:
            return existing

    # 2. Check local JSON cache
    local_cache = load_local_cache(cache_path)
    if sym in local_cache:
        entry = local_cache[sym]
        next_d = normalize_date_str(entry.get("next_earnings_date") or entry.get("next_earnings"))
        last_d = normalize_date_str(entry.get("last_earnings_date") or entry.get("last_earnings"))
        if is_earnings_record_fresh(entry, EARNINGS_CACHE_TTL_SECONDS, today_iso):
            st = entry.get("status") or (
                EarningsStatus.KNOWN_UPCOMING.value if next_d else EarningsStatus.KNOWN_CLEAR.value
            )
            rec = {
                "ticker": sym,
                "next_earnings_date": next_d,
                "last_earnings_date": last_d,
                "fiscal_period": entry.get("fiscal_period"),
                "updated_at": entry.get("updated_at") or now_iso,
                "status": st,
                "source": "local_cache",
            }
            calendar_map[sym] = rec
            GLOBAL_EARNINGS_TRACKER.local_cache_hits += 1
            return rec

    # 3. Check Supabase
    if supabase is not None:
        try:
            res = supabase.table("earnings_calendar").select("*").eq("ticker", sym).execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                next_d = normalize_date_str(row.get("next_earnings_date"))
                last_d = normalize_date_str(row.get("last_earnings_date"))
                if is_earnings_record_fresh(row, EARNINGS_CACHE_TTL_SECONDS, today_iso):
                    st = row.get("status") or (
                        EarningsStatus.KNOWN_UPCOMING.value if next_d else EarningsStatus.KNOWN_CLEAR.value
                    )
                    rec = {
                        "ticker": sym,
                        "next_earnings_date": next_d,
                        "last_earnings_date": last_d,
                        "fiscal_period": row.get("fiscal_period"),
                        "updated_at": row.get("updated_at") or now_iso,
                        "status": st,
                        "source": "supabase",
                    }
                    calendar_map[sym] = rec
                    GLOBAL_EARNINGS_TRACKER.supabase_cache_hits += 1
                    local_cache[sym] = {
                        "ticker": sym,
                        "last_earnings": last_d,
                        "next_earnings": next_d,
                        "last_earnings_date": last_d,
                        "next_earnings_date": next_d,
                        "fiscal_period": row.get("fiscal_period"),
                        "updated_at": row.get("updated_at") or now_iso,
                        "cached_at": now_ts,
                        "status": st,
                    }
                    save_local_cache(local_cache, cache_path)
                    return rec
        except Exception as e:
            logger.debug("Supabase lookup error for %s in resolve_ticker_earnings: %s", sym, e)

    # 4. Fetch from provider if allowed, budget allows, circuit breaker is healthy, and not already fetched this session
    if (
        allow_network
        and GLOBAL_PROVIDER_BUDGET.can_request()
        and GLOBAL_CIRCUIT_BREAKER.can_request()
        and sym not in _SESSION_FETCHED_TICKERS
    ):
        sym_res, next_d, last_d, fiscal_p, fetch_status = fetch_single_ticker_provider(sym)
        if fetch_status in (EarningsStatus.KNOWN_UPCOMING.value, EarningsStatus.KNOWN_CLEAR.value):
            rec = {
                "ticker": sym,
                "next_earnings_date": next_d,
                "last_earnings_date": last_d,
                "fiscal_period": fiscal_p,
                "updated_at": now_iso,
                "status": fetch_status,
                "source": "provider",
            }
            calendar_map[sym] = rec
            local_cache[sym] = {
                "ticker": sym,
                "last_earnings": last_d,
                "next_earnings": next_d,
                "last_earnings_date": last_d,
                "next_earnings_date": next_d,
                "fiscal_period": fiscal_p,
                "updated_at": now_iso,
                "cached_at": now_ts,
                "status": fetch_status,
            }
            save_local_cache(local_cache, cache_path)
            if supabase is not None:
                persist_supabase_earnings([rec], supabase)
            return rec

    # 5. Fallback if prior cache exists
    if sym in local_cache:
        prior = local_cache[sym]
        prior_next = normalize_date_str(prior.get("next_earnings_date") or prior.get("next_earnings"))
        prior_last = normalize_date_str(prior.get("last_earnings_date") or prior.get("last_earnings"))
        is_fresh = is_earnings_record_fresh(prior, EARNINGS_CACHE_TTL_SECONDS)
        st = (
            EarningsStatus.KNOWN_UPCOMING.value
            if (is_fresh and prior_next and prior_next >= today_iso)
            else (
                EarningsStatus.KNOWN_CLEAR.value
                if (is_fresh and not prior_next)
                else EarningsStatus.UNKNOWN.value
            )
        )
        rec = {
            "ticker": sym,
            "next_earnings_date": prior_next,
            "last_earnings_date": prior_last,
            "fiscal_period": prior.get("fiscal_period"),
            "updated_at": prior.get("updated_at") or now_iso,
            "status": st,
            "source": "cache_fallback",
        }
        calendar_map[sym] = rec
        return rec

    # 6. Default UNKNOWN
    unres = {
        "ticker": sym,
        "next_earnings_date": None,
        "last_earnings_date": None,
        "fiscal_period": None,
        "updated_at": now_iso,
        "status": EarningsStatus.UNKNOWN.value,
        "source": "unresolved",
    }
    calendar_map[sym] = unres
    return unres


def earnings_risk_filter(
    ticker: str,
    scan_date: datetime.date,
    strategy: str,
    earnings_calendar: Optional[Dict[str, Any]] = None,
    instrument_type: Optional[str] = None,
    is_etf: Optional[bool] = None,
    earnings_surprise_pct: Optional[float] = None,
    news_sentiment: Optional[float] = None,
    catalyst_type: Optional[str] = None,
    is_unreliable_data: bool = False,
    allow_unknown_date: bool = True,
) -> Dict[str, Any]:
    """
    Evaluates whether a candidate ticker passes earnings risk filtering based on
    event risk and catalyst matrix (Section 6).

    Catalyst Matrix:
    1. Known date, outside blackout + Positive/Neutral -> CAN PROCEED (EARNINGS_OUTSIDE_BLACKOUT)
    2. Known date, inside blackout + Positive catalyst -> CAN PROCEED (EARNINGS_POSITIVE_CATALYST_OVERRIDE)
    3. Known date, inside blackout + Negative/Neutral -> BLOCK/REJECT (EARNINGS_BLACKOUT_BLOCK / EARNINGS_NEGATIVE_CATALYST_BLOCK)
    4. Unknown date + Positive earnings news -> CAN PROCEED (EARNINGS_DATE_UNKNOWN_POSITIVE_CATALYST)
    5. Unknown date + Negative earnings news -> BLOCK/REJECT (EARNINGS_DATE_UNKNOWN_NEGATIVE_CATALYST_BLOCK)
    6. Unknown date + No meaningful info -> CAN PROCEED (EARNINGS_DATE_UNKNOWN_NO_CATALYST)

    Exemptions:
    - Sector ETFs (strat_key == 'sector_rotation' or instrument_type == 'ETF' or is_etf == True)
    - Post-Earnings Announcement Drift (PEAD)
    """
    strat_key = normalize_strategy_key(strategy)
    blackout = EARNINGS_BLACKOUT_DAYS.get(strat_key, 5)

    # 1. Structural exemption for Sector ETFs / ETF instruments
    if strat_key == "sector_rotation" or (instrument_type and str(instrument_type).upper() == "ETF") or is_etf:
        return {
            "pass": True,
            "reason": "Sector ETF (exempt from corporate earnings blackout)",
            "reason_code": REASON_SECTOR_ETF_EXEMPT,
            "status": EarningsStatus.KNOWN_CLEAR.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": None,
        }

    # 2. Hard failure for explicitly unreliable or corrupted data
    if is_unreliable_data:
        return {
            "pass": False,
            "reason": "Earnings data is unreliable - fail closed",
            "reason_code": "DATA_UNRELIABLE_FAIL_CLOSED",
            "status": EarningsStatus.UNKNOWN.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": None,
        }

    entry = (earnings_calendar.get(ticker.upper()) if earnings_calendar else {}) or {}
    next_date_val = entry.get("next_earnings_date") or entry.get("next_earnings")
    last_date_val = entry.get("last_earnings_date") or entry.get("last_earnings")

    next_dt_str = normalize_date_str(next_date_val)
    last_dt_str = normalize_date_str(last_date_val)

    next_dt = datetime.date.fromisoformat(next_dt_str) if next_dt_str else None
    last_dt = datetime.date.fromisoformat(last_dt_str) if last_dt_str else None

    # Calculate days since last earnings release to enforce catalyst recency
    days_since_earnings = (scan_date - last_dt).days if (last_dt and scan_date) else None

    # Recency check: Earnings surprise must be within EARNINGS_CATALYST_MAX_AGE_DAYS (<= 45 days)
    # to be qualified as an active catalyst capable of overriding blackout risk.
    is_old_surprise = (
        days_since_earnings is not None
        and (days_since_earnings > EARNINGS_CATALYST_MAX_AGE_DAYS or days_since_earnings < 0)
    )
    earnings_surprise_is_recent = (
        earnings_surprise_pct is not None
        and earnings_surprise_pct > 0.0
        and not is_old_surprise
    )

    if earnings_surprise_pct is not None and earnings_surprise_pct > 0.0 and not earnings_surprise_is_recent:
        logger.debug(
            "[EARNINGS CATALYST] %s: Positive surprise +%.2f%% from %s days ago exceeds %d-day window; "
            "will not override upcoming blackout.",
            ticker,
            earnings_surprise_pct,
            str(days_since_earnings),
            EARNINGS_CATALYST_MAX_AGE_DAYS,
        )

    # Evaluate Catalyst classification
    is_pos_catalyst = (
        catalyst_type == "positive"
        or earnings_surprise_is_recent
        or (news_sentiment is not None and news_sentiment > 0.20)
    )
    is_neg_surprise_current = False
    if earnings_surprise_pct is not None and earnings_surprise_pct <= -10.0:
        if days_since_earnings is not None:
            # Age is known: must be within EARNINGS_CATALYST_MAX_AGE_DAYS (<= 45 days)
            is_neg_surprise_current = (0 <= days_since_earnings <= EARNINGS_CATALYST_MAX_AGE_DAYS)
        else:
            # Age is not known from calendar:
            # If upcoming earnings date is known, treat negative surprise as active veto;
            # but if earnings date is also unknown, unknown age -> insufficient evidence (avoid indefinite blocking).
            is_neg_surprise_current = (next_dt is not None)

    is_neg_catalyst = (
        catalyst_type == "negative"
        or is_neg_surprise_current
        or (news_sentiment is not None and news_sentiment <= -0.30)
    )

    def _track_and_return(res: Dict[str, Any]) -> Dict[str, Any]:
        if is_pos_catalyst:
            GLOBAL_EARNINGS_TRACKER.positive_catalyst_count += 1
        if is_neg_catalyst:
            GLOBAL_EARNINGS_TRACKER.negative_catalyst_count += 1
        if not res.get("pass", True):
            if is_neg_catalyst:
                GLOBAL_EARNINGS_TRACKER.negative_earnings_blocks += 1
            else:
                GLOBAL_EARNINGS_TRACKER.blackout_blocks += 1
        else:
            if res.get("reason_code") == REASON_EARNINGS_POSITIVE_CATALYST_OVERRIDE:
                GLOBAL_EARNINGS_TRACKER.positive_overrides += 1
            elif res.get("status") == EarningsStatus.UNKNOWN.value and allow_unknown_date:
                GLOBAL_EARNINGS_TRACKER.unknown_proceeds += 1
        return res

    # 3. Special handling for PEAD: Post-Earnings Announcement Drift strategy
    if strat_key == "pead":
        if last_dt is not None:
            days_since = (scan_date - last_dt).days
            if 0 <= days_since <= 3:
                return _track_and_return({
                    "pass": True,
                    "reason": "Post-earnings window",
                    "reason_code": "PEAD_POST_EARNINGS_WINDOW",
                    "status": EarningsStatus.KNOWN_CLEAR.value,
                    "days_to_earnings": -days_since,
                    "next_earnings_date": next_dt.isoformat() if next_dt else None,
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                })
        return _track_and_return({
            "pass": True,
            "reason": "PEAD window (exempt from pre-earnings blackout)",
            "reason_code": "PEAD_EXEMPT",
            "status": entry.get("status", EarningsStatus.KNOWN_CLEAR.value),
            "days_to_earnings": None,
            "next_earnings_date": next_dt.isoformat() if next_dt else None,
            "last_earnings_date": last_dt.isoformat() if last_dt else None,
        })

    # 4. Check status and freshness
    scan_date_iso = scan_date.isoformat() if hasattr(scan_date, "isoformat") else str(scan_date)[:10]
    status = entry.get("status")
    if not status:
        if is_earnings_record_fresh(entry, EARNINGS_CACHE_TTL_SECONDS, today_iso=scan_date_iso):
            status = EarningsStatus.KNOWN_UPCOMING.value if next_dt else EarningsStatus.KNOWN_CLEAR.value
        elif next_dt is not None:
            if next_dt >= scan_date:
                status = EarningsStatus.KNOWN_UPCOMING.value
            else:
                status = EarningsStatus.STALE.value
        else:
            status = EarningsStatus.STALE.value if entry else EarningsStatus.UNKNOWN.value

    # Fail closed on STALE data
    if status == EarningsStatus.STALE.value:
        logger.info(f"[EARNINGS RISK GATE] Rejected {ticker} ({strategy}): Earnings data STALE")
        return _track_and_return({
            "pass": False,
            "reason": f"Earnings status {status} - blackout safety check failed",
            "reason_code": "EARNINGS_DATA_STALE",
            "status": status,
            "days_to_earnings": None,
            "next_earnings_date": next_dt.isoformat() if next_dt else None,
            "last_earnings_date": last_dt.isoformat() if last_dt else None,
        })

    # 5. When upcoming earnings date is known
    if next_dt is not None:
        days_to_earnings = (next_dt - scan_date).days
        if days_to_earnings < 0:
            if is_neg_catalyst:
                return _track_and_return({
                    "pass": False,
                    "reason": "Negative earnings/news catalyst veto",
                    "reason_code": REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK,
                    "status": EarningsStatus.KNOWN_CLEAR.value,
                    "days_to_earnings": None,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                })
            return _track_and_return({
                "pass": True,
                "reason": "Past earnings",
                "reason_code": REASON_EARNINGS_OUTSIDE_BLACKOUT,
                "status": EarningsStatus.KNOWN_CLEAR.value,
                "days_to_earnings": None,
                "next_earnings_date": next_dt.isoformat(),
                "last_earnings_date": last_dt.isoformat() if last_dt else None,
            })

        if days_to_earnings <= blackout:
            if is_pos_catalyst:
                logger.info(f"[EARNINGS RISK GATE] {ticker} ({strategy}): Positive catalyst overrides {days_to_earnings}d blackout")
                return _track_and_return({
                    "pass": True,
                    "reason": f"Positive earnings catalyst overrides {days_to_earnings}d blackout",
                    "reason_code": REASON_EARNINGS_POSITIVE_CATALYST_OVERRIDE,
                    "status": EarningsStatus.KNOWN_UPCOMING.value,
                    "days_to_earnings": days_to_earnings,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                })
            else:
                reason_msg = f"Earnings in {days_to_earnings}d (blackout: {blackout}d)"
                reason_cd = REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK if is_neg_catalyst else REASON_EARNINGS_BLACKOUT_BLOCK
                logger.info(f"[EARNINGS RISK GATE] Rejected {ticker} ({strategy}): {reason_msg}")
                return _track_and_return({
                    "pass": False,
                    "reason": reason_msg,
                    "reason_code": reason_cd,
                    "status": EarningsStatus.KNOWN_UPCOMING.value,
                    "days_to_earnings": days_to_earnings,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                })
        else:
            # Outside blackout
            if is_neg_catalyst:
                return _track_and_return({
                    "pass": False,
                    "reason": "Negative earnings/news catalyst veto",
                    "reason_code": REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK,
                    "status": EarningsStatus.KNOWN_UPCOMING.value,
                    "days_to_earnings": days_to_earnings,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                })
            return _track_and_return({
                "pass": True,
                "reason": "Earnings passed",
                "reason_code": REASON_EARNINGS_OUTSIDE_BLACKOUT,
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "days_to_earnings": days_to_earnings,
                "next_earnings_date": next_dt.isoformat(),
                "last_earnings_date": last_dt.isoformat() if last_dt else None,
            })

    # 6. If confirmed clear (no upcoming earnings scheduled)
    if status == EarningsStatus.KNOWN_CLEAR.value:
        if is_neg_catalyst:
            return _track_and_return({
                "pass": False,
                "reason": "Negative earnings/news catalyst veto",
                "reason_code": REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK,
                "status": EarningsStatus.KNOWN_CLEAR.value,
                "days_to_earnings": None,
                "next_earnings_date": None,
                "last_earnings_date": last_dt.isoformat() if last_dt else None,
            })
        return _track_and_return({
            "pass": True,
            "reason": "Earnings clear",
            "reason_code": REASON_EARNINGS_OUTSIDE_BLACKOUT,
            "status": EarningsStatus.KNOWN_CLEAR.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": last_dt.isoformat() if last_dt else None,
        })

    # 7. When earnings date is unknown (missing from calendar or UNKNOWN)
    if is_neg_catalyst:
        return _track_and_return({
            "pass": False,
            "reason": "Negative earnings news/catalyst veto",
            "reason_code": REASON_EARNINGS_DATE_UNKNOWN_NEGATIVE,
            "status": EarningsStatus.UNKNOWN.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": None,
        })
    elif is_pos_catalyst:
        return _track_and_return({
            "pass": True,
            "reason": "Unknown earnings date with positive catalyst",
            "reason_code": REASON_EARNINGS_DATE_UNKNOWN_POSITIVE,
            "status": EarningsStatus.UNKNOWN.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": None,
        })
    else:
        # No meaningful info
        if allow_unknown_date:
            return _track_and_return({
                "pass": True,
                "reason": "Unknown earnings date without negative catalyst",
                "reason_code": REASON_EARNINGS_DATE_UNKNOWN_NO_CATALYST,
                "status": EarningsStatus.UNKNOWN.value,
                "days_to_earnings": None,
                "next_earnings_date": None,
                "last_earnings_date": None,
            })
        else:
            return _track_and_return({
                "pass": False,
                "reason": f"Earnings status {EarningsStatus.UNKNOWN.value} - unconfirmed earnings schedule",
                "reason_code": "EARNINGS_UNKNOWN_FAIL_CLOSED",
                "status": EarningsStatus.UNKNOWN.value,
                "days_to_earnings": None,
                "next_earnings_date": None,
                "last_earnings_date": None,
            })

