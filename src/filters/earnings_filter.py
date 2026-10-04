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

logger = logging.getLogger(__name__)

from src.quant_config import (
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_CACHE_TTL_SECONDS,
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


def is_earnings_record_fresh(
    record: Optional[Dict[str, Any]], max_age_seconds: int = EARNINGS_CACHE_TTL_SECONDS
) -> bool:
    """
    Check if an earnings cache record is fresh (<= 24 hours old).
    Returns False for missing or stale records.
    """
    if not record or not isinstance(record, dict):
        return False
    updated_at_val = record.get("updated_at") or record.get("cached_at")
    if not updated_at_val:
        return bool(record.get("next_earnings_date"))
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
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
            return False

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        age = (now - dt).total_seconds()
        return 0 <= age <= max_age_seconds
    except Exception:
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
    for attempt in range(max_retries + 1):
        try:
            import yfinance as yf

            yf_ticker = yf.Ticker(ticker_sym)
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

            status = EarningsStatus.KNOWN_UPCOMING.value if next_date else EarningsStatus.KNOWN_CLEAR.value
            return ticker_sym, next_date, last_date, fiscal_period, status

        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = "429" in err_str or "too many requests" in err_str or "rate limit" in err_str
            is_timeout = "timeout" in err_str or "timed out" in err_str
            is_network = (
                "connection" in err_str
                or "failed to establish" in err_str
                or "remotedisconnected" in err_str
            )

            if (is_rate_limit or is_timeout or is_network) and attempt < max_retries:
                delay = (base_delay * (2**attempt)) + random.uniform(0.1, 0.4)
                logger.warning(
                    "Earnings fetch for %s hit %s; retrying in %.2fs (attempt %d/%d)",
                    ticker_sym,
                    e,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue
            else:
                logger.debug("Earnings fetch skipped/failed for %s: %s", ticker_sym, e)
                return ticker_sym, None, None, None, EarningsStatus.UNKNOWN.value

    return ticker_sym, None, None, None, EarningsStatus.UNKNOWN.value


def fetch_earnings_calendar(
    tickers: List[str],
    supabase=None,
    cache_path: Optional[Union[str, Path]] = None,
    max_workers: int = 4,
    allow_network: bool = True,
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
            fresh = is_earnings_record_fresh(entry, EARNINGS_CACHE_TTL_SECONDS)
            if fresh or (next_d and next_d >= today_iso):
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

    # 2. Layer 2: Supabase Cache for missing tickers
    missing_from_local = [t for t in normalized_tickers if t not in calendar_map]
    if missing_from_local and supabase is not None:
        sb_results = fetch_supabase_earnings(missing_from_local, supabase)
        for sym, row in sb_results.items():
            next_d = normalize_date_str(row.get("next_earnings_date"))
            last_d = normalize_date_str(row.get("last_earnings_date"))
            fresh = is_earnings_record_fresh(row, EARNINGS_CACHE_TTL_SECONDS)
            if fresh or (next_d and next_d >= today_iso):
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
                            logger.info(
                                "[EARNINGS CACHE] Provider failed for %s, falling back to cached earnings",
                                sym_orig,
                            )
                            calendar_map[sym_orig] = {
                                "ticker": sym_orig,
                                "next_earnings_date": prior_next,
                                "last_earnings_date": prior_last,
                                "fiscal_period": prior.get("fiscal_period"),
                                "updated_at": prior.get("updated_at") or now_iso,
                                "status": (
                                    EarningsStatus.KNOWN_UPCOMING.value
                                    if prior_next
                                    else EarningsStatus.KNOWN_CLEAR.value
                                ),
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
                        calendar_map[sym_orig] = {
                            "ticker": sym_orig,
                            "next_earnings_date": prior_next,
                            "last_earnings_date": prior_last,
                            "fiscal_period": prior.get("fiscal_period"),
                            "updated_at": prior.get("updated_at") or now_iso,
                            "status": (
                                EarningsStatus.KNOWN_UPCOMING.value
                                if prior_next
                                else EarningsStatus.KNOWN_CLEAR.value
                            ),
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

    # 4. Fill any remaining unresolved tickers (e.g. allow_network=False)
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

    return calendar_map


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

    # Evaluate Catalyst classification
    is_pos_catalyst = (
        catalyst_type == "positive"
        or (earnings_surprise_pct is not None and earnings_surprise_pct > 0.0)
        or (news_sentiment is not None and news_sentiment > 0.20)
    )
    is_neg_catalyst = (
        catalyst_type == "negative"
        or (earnings_surprise_pct is not None and earnings_surprise_pct <= -10.0)
        or (news_sentiment is not None and news_sentiment <= -0.30)
    )

    entry = (earnings_calendar.get(ticker.upper()) if earnings_calendar else {}) or {}
    next_date_val = entry.get("next_earnings_date") or entry.get("next_earnings")
    last_date_val = entry.get("last_earnings_date") or entry.get("last_earnings")

    next_dt_str = normalize_date_str(next_date_val)
    last_dt_str = normalize_date_str(last_date_val)

    next_dt = datetime.date.fromisoformat(next_dt_str) if next_dt_str else None
    last_dt = datetime.date.fromisoformat(last_dt_str) if last_dt_str else None

    # 3. Special handling for PEAD: Post-Earnings Announcement Drift strategy
    if strat_key == "pead":
        if last_dt is not None:
            days_since = (scan_date - last_dt).days
            if 0 <= days_since <= 3:
                return {
                    "pass": True,
                    "reason": "Post-earnings window",
                    "reason_code": "PEAD_POST_EARNINGS_WINDOW",
                    "status": EarningsStatus.KNOWN_CLEAR.value,
                    "days_to_earnings": -days_since,
                    "next_earnings_date": next_dt.isoformat() if next_dt else None,
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                }
        return {
            "pass": True,
            "reason": "PEAD window (exempt from pre-earnings blackout)",
            "reason_code": "PEAD_EXEMPT",
            "status": entry.get("status", EarningsStatus.KNOWN_CLEAR.value),
            "days_to_earnings": None,
            "next_earnings_date": next_dt.isoformat() if next_dt else None,
            "last_earnings_date": last_dt.isoformat() if last_dt else None,
        }

    # 4. Check status and freshness
    status = entry.get("status")
    if not status:
        if is_earnings_record_fresh(entry, EARNINGS_CACHE_TTL_SECONDS):
            status = EarningsStatus.KNOWN_UPCOMING.value if next_dt else EarningsStatus.KNOWN_CLEAR.value
        else:
            status = EarningsStatus.STALE.value if entry else EarningsStatus.UNKNOWN.value

    # Fail closed on STALE data
    if status == EarningsStatus.STALE.value:
        logger.info(f"[EARNINGS RISK GATE] Rejected {ticker} ({strategy}): Earnings data STALE")
        return {
            "pass": False,
            "reason": f"Earnings status {status} - blackout safety check failed",
            "reason_code": "EARNINGS_DATA_STALE",
            "status": status,
            "days_to_earnings": None,
            "next_earnings_date": next_dt.isoformat() if next_dt else None,
            "last_earnings_date": last_dt.isoformat() if last_dt else None,
        }

    # 5. When upcoming earnings date is known
    if next_dt is not None:
        days_to_earnings = (next_dt - scan_date).days
        if days_to_earnings < 0:
            if is_neg_catalyst:
                return {
                    "pass": False,
                    "reason": "Negative earnings/news catalyst veto",
                    "reason_code": REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK,
                    "status": EarningsStatus.KNOWN_CLEAR.value,
                    "days_to_earnings": None,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                }
            return {
                "pass": True,
                "reason": "Past earnings",
                "reason_code": REASON_EARNINGS_OUTSIDE_BLACKOUT,
                "status": EarningsStatus.KNOWN_CLEAR.value,
                "days_to_earnings": None,
                "next_earnings_date": next_dt.isoformat(),
                "last_earnings_date": last_dt.isoformat() if last_dt else None,
            }

        if days_to_earnings <= blackout:
            if is_pos_catalyst:
                logger.info(f"[EARNINGS RISK GATE] {ticker} ({strategy}): Positive catalyst overrides {days_to_earnings}d blackout")
                return {
                    "pass": True,
                    "reason": f"Positive earnings catalyst overrides {days_to_earnings}d blackout",
                    "reason_code": REASON_EARNINGS_POSITIVE_CATALYST_OVERRIDE,
                    "status": EarningsStatus.KNOWN_UPCOMING.value,
                    "days_to_earnings": days_to_earnings,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                }
            else:
                reason_msg = f"Earnings in {days_to_earnings}d (blackout: {blackout}d)"
                reason_cd = REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK if is_neg_catalyst else REASON_EARNINGS_BLACKOUT_BLOCK
                logger.info(f"[EARNINGS RISK GATE] Rejected {ticker} ({strategy}): {reason_msg}")
                return {
                    "pass": False,
                    "reason": reason_msg,
                    "reason_code": reason_cd,
                    "status": EarningsStatus.KNOWN_UPCOMING.value,
                    "days_to_earnings": days_to_earnings,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                }
        else:
            # Outside blackout
            if is_neg_catalyst:
                return {
                    "pass": False,
                    "reason": "Negative earnings/news catalyst veto",
                    "reason_code": REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK,
                    "status": EarningsStatus.KNOWN_UPCOMING.value,
                    "days_to_earnings": days_to_earnings,
                    "next_earnings_date": next_dt.isoformat(),
                    "last_earnings_date": last_dt.isoformat() if last_dt else None,
                }
            return {
                "pass": True,
                "reason": "Earnings passed",
                "reason_code": REASON_EARNINGS_OUTSIDE_BLACKOUT,
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "days_to_earnings": days_to_earnings,
                "next_earnings_date": next_dt.isoformat(),
                "last_earnings_date": last_dt.isoformat() if last_dt else None,
            }

    # 6. If confirmed clear (no upcoming earnings scheduled)
    if status == EarningsStatus.KNOWN_CLEAR.value:
        if is_neg_catalyst:
            return {
                "pass": False,
                "reason": "Negative earnings/news catalyst veto",
                "reason_code": REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK,
                "status": EarningsStatus.KNOWN_CLEAR.value,
                "days_to_earnings": None,
                "next_earnings_date": None,
                "last_earnings_date": last_dt.isoformat() if last_dt else None,
            }
        return {
            "pass": True,
            "reason": "Earnings clear",
            "reason_code": REASON_EARNINGS_OUTSIDE_BLACKOUT,
            "status": EarningsStatus.KNOWN_CLEAR.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": last_dt.isoformat() if last_dt else None,
        }

    # 7. When earnings date is unknown (missing from calendar or UNKNOWN)
    if is_neg_catalyst:
        return {
            "pass": False,
            "reason": "Negative earnings news/catalyst veto",
            "reason_code": REASON_EARNINGS_DATE_UNKNOWN_NEGATIVE,
            "status": EarningsStatus.UNKNOWN.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": None,
        }
    elif is_pos_catalyst:
        return {
            "pass": True,
            "reason": "Unknown earnings date with positive catalyst",
            "reason_code": REASON_EARNINGS_DATE_UNKNOWN_POSITIVE,
            "status": EarningsStatus.UNKNOWN.value,
            "days_to_earnings": None,
            "next_earnings_date": None,
            "last_earnings_date": None,
        }
    else:
        # No meaningful info
        if allow_unknown_date:
            return {
                "pass": True,
                "reason": "Unknown earnings date without negative catalyst",
                "reason_code": REASON_EARNINGS_DATE_UNKNOWN_NO_CATALYST,
                "status": EarningsStatus.UNKNOWN.value,
                "days_to_earnings": None,
                "next_earnings_date": None,
                "last_earnings_date": None,
            }
        else:
            return {
                "pass": False,
                "reason": f"Earnings status {EarningsStatus.UNKNOWN.value} - unconfirmed earnings schedule",
                "reason_code": "EARNINGS_UNKNOWN_FAIL_CLOSED",
                "status": EarningsStatus.UNKNOWN.value,
                "days_to_earnings": None,
                "next_earnings_date": None,
                "last_earnings_date": None,
            }

