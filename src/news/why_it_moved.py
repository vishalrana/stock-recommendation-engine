"""
Why It Moved Explanation Generator
===================================
Produces a <= 25-word summary with citations for BUY, SELL, or large moves (|ret| >= 2*ATR14/close).
Labelled: "AI summary - check sources".
Never influences or alters trading decisions.
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def generate_why_it_moved(
    ticker: str,
    action: str,  # 'BUY', 'SELL', 'MOVE'
    news_items: List[Dict[str, Any]],
    return_pct: Optional[float] = None,
    atr_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Summarize sources from sessions t-2..t in <= 25 words with source citations.
    If no source: 'No company-specific news found in SEC filings or headlines.'
    """
    if not news_items:
        return {
            "summary": "No company-specific news found in SEC filings or headlines.",
            "label": "AI summary - check sources",
            "sources": [],
            "action": action,
        }

    # Extract distinct headlines/sources up to 3
    cited_sources = []
    headline_fragments = []
    for idx, item in enumerate(news_items[:3], start=1):
        title = item.get("title", "")
        url = item.get("url", "")
        source_name = item.get("source", "News")
        cited_sources.append({
            "index": idx,
            "title": title,
            "url": url,
            "source": source_name,
        })
        # Clean title to <= 8 words
        words = title.split()[:6]
        headline_fragments.append(f"{' '.join(words)} [{idx}]")

    if not headline_fragments:
        summary_text = "No company-specific news found in SEC filings or headlines."
    else:
        raw_summary = "; ".join(headline_fragments)
        words = raw_summary.split()
        if len(words) > 25:
            summary_text = " ".join(words[:24]) + "..."
        else:
            summary_text = raw_summary

    return {
        "summary": summary_text,
        "label": "AI summary - check sources",
        "sources": cited_sources,
        "action": action,
    }
