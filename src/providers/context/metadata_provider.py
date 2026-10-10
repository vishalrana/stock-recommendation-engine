import logging
import random
import threading
import time
from typing import Any, Dict, Optional

import yfinance as yf

from src.providers.base import AnalystContext, FundamentalContext, DataQuality

logger = logging.getLogger(__name__)

# Keys whose presence shows Yahoo actually returned fundamentals (an empty/blocked response
# typically carries only a handful of quote fields).
_FUNDAMENTAL_KEYS = ("trailingPE", "forwardPE", "debtToEquity", "currentRatio", "targetMeanPrice", "recommendationKey")


class MetadataProvider:
    """
    Analyst and fundamental data from Yahoo's quoteSummary (yf.Ticker(...).info).

    One request per ticker is shared by get_analyst_rating() and get_fundamentals() (the old code
    made two identical calls per ticker, doubling rate-limit pressure on CI runners). Failures are
    reported as DataQuality.UNAVAILABLE instead of silently looking like "no qualifying data".
    """

    MAX_ATTEMPTS = 3

    def __init__(self):
        self._info_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._lock = threading.Lock()

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
        if info is None:
            logger.debug("Yahoo fundamentals unavailable for %s after %d attempts", t, self.MAX_ATTEMPTS)
        with self._lock:
            self._info_cache[t] = info
        return info

    def get_analyst_rating(self, ticker: str) -> AnalystContext:
        info = self._get_info(ticker)
        if info is None:
            return AnalystContext(quality=DataQuality.UNAVAILABLE)
        return AnalystContext(
            target_mean_price=info.get('targetMeanPrice'),
            recommendation=info.get('recommendationKey'),  # e.g., "buy"
            num_analysts=info.get('numberOfAnalystOpinions'),
            quality=DataQuality.VALID,
        )

    def get_fundamentals(self, ticker: str) -> FundamentalContext:
        info = self._get_info(ticker)
        if info is None:
            return FundamentalContext(quality=DataQuality.UNAVAILABLE)
        raw_de = info.get('debtToEquity')  # Yahoo reports D/E in percent (103.3 -> 1.033)
        return FundamentalContext(
            debt_to_equity=(raw_de / 100.0) if raw_de is not None else None,
            current_ratio=info.get('currentRatio'),
            trailing_pe=info.get('trailingPE'),
            quality=DataQuality.VALID,
        )
