"""
US Equities Universe Provider
=============================
Authoritative discovery provider for broad US-listed common equities.
Integrates:
- NASDAQ Trader official directory (nasdaqlisted.txt, otherlisted.txt)
- SEC EDGAR exchange directory (company_tickers_exchange.json)
- Broad US equity sector/industry metadata
- Local filesystem caching (data/cache/universe/)
- Resilient fallback to S&P 500 + Nasdaq-100 (Wikipedia / local CSV)
"""

import os
import json
import logging
import time
from typing import List, Dict, Optional, Set, Tuple
from pathlib import Path
import requests

from src.universe.models import SecurityRecord
from src.universe.base import UniverseProvider
from src.universe.normalization import to_canonical_ticker, to_provider_ticker
from src.universe.filters import is_eligible_equity

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/cache/universe")
MASTER_CACHE_FILE = DEFAULT_CACHE_DIR / "us_equities_master.json"
CACHE_TTL_HOURS = 24


class USEquitiesUniverseProvider(UniverseProvider):
    """
    Provides broad US-listed common equity securities.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        cache_ttl_hours: int = CACHE_TTL_HOURS,
        delisted_tickers_path: Optional[str] = "config/delisted_tickers.json",
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_file = self.cache_dir / "us_equities_master.json"
        self.cache_ttl_seconds = cache_ttl_hours * 3600
        self.delisted_tickers: Set[str] = set()
        self._delisted_records: Dict[str, SecurityRecord] = {}

        if delisted_tickers_path and os.path.exists(delisted_tickers_path):
            try:
                with open(delisted_tickers_path, "r", encoding="utf-8") as f:
                    delist_data = json.load(f)
                    for item in delist_data.get("delisted_tickers", []):
                        t = str(item.get("ticker", "")).strip().upper()
                        if t:
                            self.delisted_tickers.add(t)
                            d_date = item.get("delisted_date")
                            self._delisted_records[t] = SecurityRecord(
                                ticker=t,
                                company_name=item.get("company_name", t),
                                exchange="US",
                                sector=item.get("sector", "Unknown"),
                                industry=item.get("sector", "Unknown"),
                                instrument_type="COMMON",
                                market="US",
                                country="US",
                                currency="USD",
                                is_active=False,
                                delisted_date=d_date,
                                data_provider_ticker=t.replace(".", "-"),
                            )
            except Exception as e:
                logger.warning("Failed to load delisted tickers file %s: %s", delisted_tickers_path, e)

        self._universe: Dict[str, SecurityRecord] = {}
        self._provider_ticker_map: Dict[str, str] = {}  # provider_ticker -> canonical_ticker
        self._stats: Dict[str, int] = {}
        self.is_fallback: bool = False
        self._load_universe_data()

    @property
    def market(self) -> str:
        return "US"

    def get_universe(self, as_of_date: Optional[str] = None) -> List[SecurityRecord]:
        """
        Return list of all eligible common equity records in the US universe.
        If as_of_date is provided, reconstitutes point-in-time universe:
        - Includes active records
        - Reconstitutes historical delisted tickers where delisted_date >= as_of_date[:10]
        - Excludes delisted tickers where delisted_date < as_of_date[:10]
        """
        if as_of_date is None:
            return list(self._universe.values())

        as_of = str(as_of_date)[:10]
        records = list(self._universe.values())
        # Reconstitute delisted records that were still listed on as_of_date
        for rec in self._delisted_records.values():
            if rec.delisted_date and rec.delisted_date >= as_of:
                records.append(rec)
        return records

    def get_tickers(self, as_of_date: Optional[str] = None) -> List[str]:
        """Return list of canonical ticker symbols for the requested point-in-time universe."""
        return [rec.ticker for rec in self.get_universe(as_of_date=as_of_date)]

    def get_data_provider_tickers(self, as_of_date: Optional[str] = None) -> List[str]:
        """Return list of data provider tickers for the requested point-in-time universe."""
        return [rec.data_provider_ticker for rec in self.get_universe(as_of_date=as_of_date)]

    def get_security_record(self, ticker: str) -> Optional[SecurityRecord]:
        """Lookup security record by canonical ticker or provider ticker."""
        canonical = to_canonical_ticker(ticker)
        if canonical in self._universe:
            return self._universe[canonical]
        if canonical in self._delisted_records:
            return self._delisted_records[canonical]
        provider = to_provider_ticker(ticker)
        if provider in self._provider_ticker_map:
            return self._universe.get(self._provider_ticker_map[provider])
        return None

    def get_universe_provenance(self, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns point-in-time universe provenance and audit metadata, documenting
        survivorship mitigation and historical tape coverage limitations.
        """
        as_of = str(as_of_date)[:10] if as_of_date else None
        delisted_included = 0
        delisted_prior_excluded = 0
        if as_of:
            for rec in self._delisted_records.values():
                if rec.delisted_date and rec.delisted_date >= as_of:
                    delisted_included += 1
                else:
                    delisted_prior_excluded += 1
        return {
            "as_of_date": as_of,
            "is_point_in_time": as_of is not None,
            "active_ticker_count": len(self._universe),
            "delisted_registry_count": len(self._delisted_records),
            "delisted_reconstituted_count": delisted_included,
            "delisted_prior_excluded_count": delisted_prior_excluded,
            "tape_coverage_limitations": (
                "Historical S&P 500 delisted tickers reconstituted from curated registry "
                "(config/delisted_tickers.json). Micro-cap delistings outside S&P 500 historical "
                "constituents may not be fully represented without a commercial point-in-time tape."
            ),
        }

    def get_stats(self) -> Dict[str, int]:
        """Return dictionary of universe discovery audit metrics."""
        return dict(self._stats)

    # -------------------------------------------------------------------------
    # Internal Loading and Caching Architecture
    # -------------------------------------------------------------------------

    def _load_universe_data(self) -> None:
        """Load universe from local cache if fresh; otherwise fetch from authoritative sources."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if self._is_cache_valid():
            logger.info("Loading US equities universe from local cache: %s", self.cache_file)
            if self._load_from_cache():
                return

        logger.info("Local cache missing or expired. Fetching US security master from sources...")
        try:
            records, stats = self._fetch_authoritative_universe()
            self._universe = records
            self._provider_ticker_map = {r.data_provider_ticker: r.ticker for r in records.values()}
            self._stats = stats
            self._save_to_cache()
            logger.info("Successfully loaded and cached %d eligible US common equities.", len(self._universe))
        except Exception as e:
            logger.error("Authoritative universe fetch failed: %s. Attempting fallback...", e, exc_info=True)
            if self._load_from_cache(ignore_expiration=True):
                logger.info("Recovered from expired local cache with %d tickers.", len(self._universe))
            else:
                self._load_fallback_sp500_nasdaq()

    def _is_cache_valid(self) -> bool:
        if not self.cache_file.exists():
            return False
        mtime = self.cache_file.stat().st_mtime
        return (time.time() - mtime) < self.cache_ttl_seconds

    def _load_from_cache(self, ignore_expiration: bool = False) -> bool:
        if not self.cache_file.exists():
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = {}
            for item in data.get("securities", []):
                rec = SecurityRecord.from_dict(item)
                records[rec.ticker] = rec
            self._universe = records
            self._provider_ticker_map = {r.data_provider_ticker: r.ticker for r in records.values()}
            self._stats = data.get("stats", {})
            return len(self._universe) > 0
        except Exception as e:
            logger.warning("Error reading cache file %s: %s", self.cache_file, e)
            return False

    def _save_to_cache(self) -> None:
        try:
            payload = {
                "generated_at": time.time(),
                "market": "US",
                "total_count": len(self._universe),
                "stats": self._stats,
                "securities": [r.to_dict() for r in self._universe.values()],
            }
            tmp_file = self.cache_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp_file.replace(self.cache_file)
            logger.debug("Saved US equities master cache to %s", self.cache_file)
        except Exception as e:
            logger.error("Failed to write universe cache %s: %s", self.cache_file, e)

    def _fetch_authoritative_universe(self) -> Tuple[Dict[str, SecurityRecord], Dict[str, int]]:
        """
        Fetch directory files from NASDAQ Trader and SEC EDGAR, apply instrument filtering,
        and assemble canonical SecurityRecords.
        """
        stats = {
            "total_raw_candidates": 0,
            "excluded_test_issues": 0,
            "excluded_etfs": 0,
            "excluded_non_common": 0,
            "eligible_common_equities": 0,
        }

        # 1. Fetch SEC EDGAR CIK and official exchange master
        sec_map = self._fetch_sec_exchange_master()

        # 2. Fetch sector & industry enrichment dataset
        sector_map = self._fetch_sector_enrichment()

        records: Dict[str, SecurityRecord] = {}

        # 3. Fetch NASDAQ Listed symbols
        nasdaq_url = "http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
        r_nasdaq = requests.get(nasdaq_url, timeout=15)
        r_nasdaq.raise_for_status()
        nasdaq_lines = [l for l in r_nasdaq.text.strip().splitlines() if not l.startswith("File Creation Time:")]

        for line in nasdaq_lines[1:]:
            parts = line.split("|")
            if len(parts) < 8:
                continue
            stats["total_raw_candidates"] += 1
            sym, name, mkt, test, fin, lot, etf, nextsh = parts[:8]
            canonical = to_canonical_ticker(sym)
            sec_info = sec_map.get(canonical, sec_map.get(sym, {}))
            sec_meta = sector_map.get(canonical, sector_map.get(sym, {}))

            rec = SecurityRecord(
                ticker=canonical,
                company_name=name.strip(),
                exchange="NASDAQ",
                sector=sec_meta.get("sector", "Unknown"),
                industry=sec_meta.get("industry", "Unknown"),
                instrument_type="ETF" if etf == "Y" else "COMMON",
                market="US",
                country=sec_meta.get("country", "US"),
                currency="USD",
                is_active=True,
                is_etf=(etf == "Y"),
                is_test=(test == "Y"),
                cik=sec_info.get("cik"),
                data_provider_ticker=to_provider_ticker(canonical),
            )

            is_eligible, reason = is_eligible_equity(rec, self.delisted_tickers)
            if not is_eligible:
                if "test" in reason.lower():
                    stats["excluded_test_issues"] += 1
                elif "etf" in reason.lower():
                    stats["excluded_etfs"] += 1
                else:
                    stats["excluded_non_common"] += 1
                continue

            records[canonical] = rec

        # 4. Fetch Other Listed (NYSE, AMEX, ARCA, BATS) symbols
        other_url = "http://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
        r_other = requests.get(other_url, timeout=15)
        r_other.raise_for_status()
        other_lines = [l for l in r_other.text.strip().splitlines() if not l.startswith("File Creation Time:")]

        EXCHANGE_MAP = {"N": "NYSE", "A": "AMEX", "P": "ARCA", "Z": "BATS", "V": "IEX"}

        for line in other_lines[1:]:
            parts = line.split("|")
            if len(parts) < 8:
                continue
            stats["total_raw_candidates"] += 1
            act_sym, name, exch_code, cqs, etf, lot, test, nasdaq_sym = parts[:8]
            canonical = to_canonical_ticker(act_sym)
            sec_info = sec_map.get(canonical, sec_map.get(act_sym, {}))
            sec_meta = sector_map.get(canonical, sector_map.get(act_sym, {}))

            exchange_name = EXCHANGE_MAP.get(exch_code, "NYSE")

            rec = SecurityRecord(
                ticker=canonical,
                company_name=name.strip(),
                exchange=exchange_name,
                sector=sec_meta.get("sector", "Unknown"),
                industry=sec_meta.get("industry", "Unknown"),
                instrument_type="ETF" if etf == "Y" else "COMMON",
                market="US",
                country=sec_meta.get("country", "US"),
                currency="USD",
                is_active=True,
                is_etf=(etf == "Y"),
                is_test=(test == "Y"),
                cik=sec_info.get("cik"),
                data_provider_ticker=to_provider_ticker(canonical),
            )

            is_eligible, reason = is_eligible_equity(rec, self.delisted_tickers)
            if not is_eligible:
                if "test" in reason.lower():
                    stats["excluded_test_issues"] += 1
                elif "etf" in reason.lower():
                    stats["excluded_etfs"] += 1
                else:
                    stats["excluded_non_common"] += 1
                continue

            records[canonical] = rec

        stats["eligible_common_equities"] = len(records)
        return records, stats

    def _fetch_sec_exchange_master(self) -> Dict[str, Dict]:
        """Fetch SEC EDGAR security master with CIK and primary exchange."""
        sec_map = {}
        try:
            url = "https://www.sec.gov/files/company_tickers_exchange.json"
            headers = {"User-Agent": "StockRecommendationEngine quant@user-engine.internal"}
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code == 200:
                data = r.json()
                for row in data.get("data", []):
                    cik, name, ticker, exch = row
                    t_canon = to_canonical_ticker(ticker)
                    sec_map[t_canon] = {
                        "cik": cik,
                        "sec_name": name,
                        "sec_exchange": exch,
                    }
        except Exception as e:
            logger.warning("SEC EDGAR security master fetch failed: %s", e)
        return sec_map

    def _fetch_sector_enrichment(self) -> Dict[str, Dict]:
        """Fetch US equities sector and industry metadata from public datasets."""
        sector_map = {}
        for exch in ["nasdaq", "nyse", "amex"]:
            try:
                url = f"https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/{exch}/{exch}_full_tickers.json"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    for item in r.json():
                        sym = item.get("symbol", "").strip().upper()
                        if sym:
                            t_canon = to_canonical_ticker(sym)
                            sector = item.get("sector")
                            industry = item.get("industry")
                            c_raw = item.get("country", "US")
                            country = "US" if c_raw in {"US", "United States", "USA"} else c_raw
                            sector_map[t_canon] = {
                                "sector": sector if sector and sector != "n/a" else "Unknown",
                                "industry": industry if industry and industry != "n/a" else "Unknown",
                                "country": country if country and country != "n/a" else "US",
                            }
            except Exception as e:
                logger.debug("Sector enrichment fetch for %s failed: %s", exch, e)
        return sector_map

    def _load_fallback_sp500_nasdaq(self) -> None:
        """
        Resilient fallback to S&P 500 + Nasdaq-100 when external network calls fail completely.
        """
        logger.warning("[UNIVERSE DEGRADED] Engaging fallback universe loader: S&P 500 + Nasdaq-100 constituents.")
        self.is_fallback = True
        records = {}
        # Try local CSV first
        csv_path = Path("outputs/backtest_summary.csv")
        if csv_path.exists():
            try:
                import pandas as pd
                df = pd.read_csv(csv_path)
                for _, row in df.iterrows():
                    ticker = str(row["ticker"]).strip().upper()
                    canonical = to_canonical_ticker(ticker)
                    records[canonical] = SecurityRecord(
                        ticker=canonical,
                        company_name=canonical,
                        exchange="US",
                        sector=str(row.get("industry", "Unknown")),
                        industry=str(row.get("industry", "Unknown")),
                        instrument_type="COMMON",
                        data_provider_ticker=to_provider_ticker(canonical),
                    )
                logger.info("Loaded %d fallback tickers from outputs/backtest_summary.csv", len(records))
            except Exception as e:
                logger.error("Failed to read fallback CSV: %s", e)

        self._universe = records
        self._provider_ticker_map = {r.data_provider_ticker: r.ticker for r in records.values()}
        self._stats = {"fallback_tickers": len(records)}
