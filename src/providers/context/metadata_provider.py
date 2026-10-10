import logging
import math
import random
import threading
import time
from typing import Any, Dict, Optional

import yfinance as yf

from src.providers.base import AnalystContext, FundamentalContext, DataQuality
from src.providers.context.sec_fundamentals import SecFundamentalsProvider, get_sec_provider

logger = logging.getLogger(__name__)

# Keys whose presence shows Yahoo actually returned fundamentals (an empty/blocked response
# typically carries only a handful of quote fields).
_FUNDAMENTAL_KEYS = ("trailingPE", "forwardPE", "debtToEquity", "currentRatio", "targetMeanPrice", "recommendationKey")


def _finite(value: Any) -> Optional[float]:
    """Yahoo fields as finite floats; strings such as 'Infinity', NaN or junk become None (missing)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class MetadataProvider:
    """
    Fundamentals and analyst data.

    Fundamentals (P/E, D/E, current ratio) come from SEC EDGAR filings first: EDGAR is built for
    programmatic access and works from cloud runners, where Yahoo's quoteSummary is throttled or
    blocked. Yahoo fills only gaps SEC cannot cover (e.g. foreign IFRS filers) and remains the only
    source for analyst targets/recommendations.

    One Yahoo request per ticker is shared by both methods. Failures are reported as
    DataQuality.UNAVAILABLE instead of silently looking like "no qualifying data".
    """

    MAX_ATTEMPTS = 3

    def __init__(self, sec_provider: Optional[SecFundamentalsProvider] = None):
        self._info_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._sec = sec_provider if sec_provider is not None else get_sec_provider()
        self.stats = {"yahoo_ok": 0, "yahoo_failed": 0, "sec_ok": 0, "sec_missing": 0}

    def _get_info(self, ticker: str) -> Optional[Dict[str, Any]]:
        t = ticker.upper()
        with self._lock:
            if t in self._info_cache:
                return self._info_cache[t]
        info = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                data = yf.Ticker(t).info or {}
                if any(data.get(k) is not None for k in _FUNDAMENTAL_KEYS):
                    info = data
                    break
                # Empty or quote-only payload: usually throttling; back off and retry
            except Exception as e:
                logger.debug("Yahoo info fetch failed for %s (attempt %d): %s", t, attempt + 1, e)
            time.sleep((2 ** attempt) + random.random())
        with self._lock:
            self._info_cache[t] = info
            self.stats["yahoo_ok" if info is not None else "yahoo_failed"] += 1
        if info is None:
            logger.debug("Yahoo data unavailable for %s after %d attempts", t, self.MAX_ATTEMPTS)
        return info

    def get_analyst_rating(self, ticker: str) -> AnalystContext:
        info = self._get_info(ticker)
        if info is None:
            return AnalystContext(quality=DataQuality.UNAVAILABLE)
        return AnalystContext(
            target_mean_price=_finite(info.get('targetMeanPrice')),
            recommendation=info.get('recommendationKey'),  # e.g., "buy"
            num_analysts=info.get('numberOfAnalystOpinions'),
            quality=DataQuality.VALID,
        )

    def get_fundamentals(self, ticker: str, price: Optional[float] = None) -> FundamentalContext:
        """
        P/E (price / trailing-12-month EPS), D/E and current ratio. SEC first, Yahoo for gaps.
        `price` is needed for an SEC-based P/E (P/E is None for loss-making companies).
        """
        de = cr = pe = eps = None
        bs_date = None
        sources = []
        sec = None
        if self._sec is not None and self._sec.available:
            try:
                sec = self._sec.fundamentals(ticker)
            except Exception as e:
                logger.debug("SEC fundamentals failed for %s: %s", ticker, e)
                sec = None
        if sec is not None and sec.has_data():
            de, cr, eps, bs_date = sec.debt_to_equity, sec.current_ratio, sec.eps_ttm, sec.balance_sheet_date
            pe = sec.pe_ratio(price)
            sources.append("sec")
            self.stats["sec_ok"] += 1
        else:
            self.stats["sec_missing"] += 1

        # Yahoo only for values SEC could not provide. A loss-maker legitimately has no P/E,
        # so a known non-positive EPS is not a gap.
        pe_gap = pe is None and not (eps is not None and eps <= 0)
        if de is None or cr is None or pe_gap:
            info = self._get_info(ticker)
            if info is not None:
                raw_de = _finite(info.get('debtToEquity'))  # Yahoo reports D/E in percent (103.3 -> 1.033)
                y_cr, y_pe = _finite(info.get('currentRatio')), _finite(info.get('trailingPE'))
                filled = False
                if de is None and raw_de is not None:
                    de, filled = raw_de / 100.0, True
                if cr is None and y_cr is not None:
                    cr, filled = y_cr, True
                if pe_gap and y_pe is not None:
                    pe, filled = y_pe, True
                if filled:
                    sources.append("yahoo")

        if not sources:
            return FundamentalContext(quality=DataQuality.UNAVAILABLE)
        return FundamentalContext(
            debt_to_equity=de,
            current_ratio=cr,
            trailing_pe=pe,
            quality=DataQuality.VALID,
            source="+".join(sources),
            eps_ttm=eps,
            balance_sheet_date=bs_date,
        )
