"""
News & Event Ingestion Layer
============================
Point-in-time news items and deterministic SEC 8-K parsing.
Sources:
- SEC EDGAR 8-K / 6-K filings
- Finnhub company news (if FINNHUB_API_KEY set)
- Wire / Google News RSS feeds
"""

import os
import re
import time
import hashlib
import logging
import datetime as dt
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Deterministic 8-K classification mapping
DETERMINISTIC_8K_ITEMS = {
    "2.02": "earnings",
    "1.01": "agreement",
    "2.01": "completion",
    "1.02": "termination_negative",
    "1.03": "bankruptcy_negative",
    "2.04": "default_negative",
    "3.01": "delisting_negative",
    "4.02": "non_reliance_negative",
    "5.02": "exec_departure",
    "7.01": "fd_disclosure",
    "8.01": "other_event",
}

NEG_8K_ITEMS = {"1.02", "1.03", "2.04", "3.01", "4.02", "5.02"}


def classify_8k_items(items: List[str]) -> Dict[str, Any]:
    """Deterministic classification of SEC Form 8-K items without LLM."""
    clean_items = [str(it).strip().replace("Item ", "") for it in items if it]
    is_neg = any(it in NEG_8K_ITEMS for it in clean_items)

    classified_events = [
        DETERMINISTIC_8K_ITEMS.get(it, "other") for it in clean_items
    ]

    event_type = "other"
    if "earnings" in classified_events:
        event_type = "earnings"
    elif is_neg:
        event_type = "negative_event"
    elif "agreement" in classified_events:
        event_type = "agreement"
    elif "exec_departure" in classified_events:
        event_type = "exec_departure"

    direction = -1 if is_neg else (1 if event_type in ("earnings", "agreement") else 0)

    return {
        "event_type": event_type,
        "is_negative": is_neg,
        "direction": direction,
        "items": clean_items,
    }


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


class NewsSources:
    """Ingests real-time and historical news items for swing tickers."""

    def __init__(self):
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY")
        self.last_finnhub_call = 0.0

    def fetch_sec_filings_for_ticker(self, ticker: str, days: int = 5) -> List[Dict[str, Any]]:
        """Fetch recent 8-K filings from SEC EDGAR submissions API."""
        from src.providers.context.sec_filings import SecFilingsProvider

        provider = SecFilingsProvider()
        filings = provider.filings(ticker) or []
        results = []
        for f in filings[-10:]:
            items_list = list(f.items)
            txt = f"SEC Form {f.form} items: {','.join(items_list)}"
            thash = compute_hash(txt)
            det = classify_8k_items(items_list)
            results.append({
                "ticker": ticker.upper(),
                "source": "SEC_EDGAR",
                "source_id": f"{ticker}_{f.day}_{f.form}",
                "title": f"{ticker} Form {f.form} filed ({','.join(items_list)})",
                "url": f"https://www.sec.gov/edgar/browse/?CIK={ticker}",
                "items": items_list,
                "accepted_at": f.accepted.isoformat() if f.accepted else None,
                "first_seen_at": f.accepted.isoformat() if f.accepted else dt.datetime.utcnow().isoformat(),
                "text_hash": thash,
                "raw_text": txt,
                "deterministic": det,
            })
        return results

    def fetch_finnhub_news(self, ticker: str, days: int = 3) -> List[Dict[str, Any]]:
        """Fetch company news from Finnhub (only if key present)."""
        if not self.finnhub_key:
            return []

        import requests

        # Rate limit: <= 50 calls/min (1.2s delay)
        now = time.time()
        if now - self.last_finnhub_call < 1.2:
            time.sleep(1.2 - (now - self.last_finnhub_call))
        self.last_finnhub_call = time.time()

        today_str = dt.date.today().isoformat()
        start_str = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        url = (
            f"https://finnhub.io/api/v1/company-news?symbol={ticker.upper()}&from={start_str}&to={today_str}&token={self.finnhub_key}"
        )
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return []
            items = resp.json()
            out = []
            for itm in items[:15]:
                headline = itm.get("headline", "")
                summary = itm.get("summary", "")
                full_text = f"{headline}. {summary}".strip()
                thash = compute_hash(full_text)
                out.append({
                    "ticker": ticker.upper(),
                    "source": "FINNHUB",
                    "source_id": str(itm.get("id", "")),
                    "title": headline,
                    "url": itm.get("url", ""),
                    "items": [],
                    "accepted_at": dt.datetime.utcfromtimestamp(itm.get("datetime", int(time.time()))).isoformat(),
                    "first_seen_at": dt.datetime.utcnow().isoformat(),
                    "text_hash": thash,
                    "raw_text": full_text,
                    "deterministic": {"event_type": "headline", "direction": 0},
                })
            return out
        except Exception as e:
            logger.debug(f"Finnhub fetch error for {ticker}: {e}")
            return []

    def fetch_rss_news(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch Google News RSS best effort."""
        import requests
        try:
            import feedparser
        except ImportError:
            return []

        clean = ticker.split(".")[0]
        url = f"https://news.google.com/rss/search?q={clean}+stock&hl=en-US&gl=US&ceid=US:en"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            feed = feedparser.parse(resp.text)
            out = []
            for ent in feed.entries[:8]:
                title = ent.get("title", "")
                link = ent.get("link", "")
                thash = compute_hash(title)
                out.append({
                    "ticker": ticker.upper(),
                    "source": "RSS_GOOGLE",
                    "source_id": ent.get("id", link),
                    "title": title,
                    "url": link,
                    "items": [],
                    "accepted_at": None,
                    "first_seen_at": dt.datetime.utcnow().isoformat(),
                    "text_hash": thash,
                    "raw_text": title,
                    "deterministic": {"event_type": "rss_headline", "direction": 0},
                })
            return out
        except Exception as e:
            logger.debug(f"RSS fetch error for {ticker}: {e}")
            return []

    def fetch_all_for_ticker(self, ticker: str) -> List[Dict[str, Any]]:
        """Aggregate all available free sources for a candidate."""
        sec_items = self.fetch_sec_filings_for_ticker(ticker)
        finnhub_items = self.fetch_finnhub_news(ticker)
        rss_items = self.fetch_rss_news(ticker)
        return sec_items + finnhub_items + rss_items
