"""
Analyst Snapshot Archive Layer (A1)
===================================
Archives raw analyst consensus/recommendation trend data.
Used for display counts only; never used as a trading gate or upside target.
"""

import os
import time
import logging
import datetime as dt
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class AnalystArchive:
    """Fetches and archives analyst recommendation trends."""

    def __init__(self):
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY")
        self.fmp_key = os.environ.get("FMP_API_KEY")
        self.last_call = 0.0

    def fetch_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch raw analyst recommendation payload from Finnhub or FMP."""
        if not self.finnhub_key and not self.fmp_key:
            return None

        # Rate limit friendly: 1 sec between calls
        now = time.time()
        if now - self.last_call < 1.0:
            time.sleep(1.0 - (now - self.last_call))
        self.last_call = time.time()

        if self.finnhub_key:
            return self._fetch_finnhub(ticker)
        elif self.fmp_key:
            return self._fetch_fmp(ticker)
        return None

    def _fetch_finnhub(self, ticker: str) -> Optional[Dict[str, Any]]:
        import requests

        url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={ticker.upper()}&token={self.finnhub_key}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list):
                    latest = data[0]
                    return {
                        "ticker": ticker.upper(),
                        "as_of_date": dt.date.today().isoformat(),
                        "source": "FINNHUB",
                        "payload": latest,
                        "strong_buy": latest.get("strongBuy", 0),
                        "buy": latest.get("buy", 0),
                        "hold": latest.get("hold", 0),
                        "sell": latest.get("sell", 0),
                        "strong_sell": latest.get("strongSell", 0),
                        "period": latest.get("period", ""),
                    }
        except Exception as e:
            logger.debug(f"Finnhub analyst fetch error for {ticker}: {e}")
        return None

    def _fetch_fmp(self, ticker: str) -> Optional[Dict[str, Any]]:
        import requests

        url = f"https://financialmodelingprep.com/api/v3/analyst-stock-recommendations/{ticker.upper()}?limit=1&apikey={self.fmp_key}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list):
                    latest = data[0]
                    return {
                        "ticker": ticker.upper(),
                        "as_of_date": dt.date.today().isoformat(),
                        "source": "FMP",
                        "payload": latest,
                        "strong_buy": latest.get("analystRatingsbuy", 0),
                        "buy": latest.get("analystRatingsHold", 0),
                        "hold": latest.get("analystRatingsHold", 0),
                        "sell": latest.get("analystRatingsSell", 0),
                        "strong_sell": latest.get("analystRatingsStrongSell", 0),
                    }
        except Exception as e:
            logger.debug(f"FMP analyst fetch error for {ticker}: {e}")
        return None
