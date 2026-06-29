"""
Local cache for Supabase ticker_metrics data.
Avoids a network call on every local run.
"""

import os
import json
import time
import logging

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join("data", "cache", "ticker_metrics_cache.json")
TTL_SECONDS = int(os.environ.get("METRICS_CACHE_TTL_HOURS", "24")) * 3600


def load_cached_metrics() -> dict | None:
    """Load metrics from local JSON cache if fresh enough. Returns dict or None."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        if time.time() - data.get("updated_at", 0) < TTL_SECONDS:
            logger.info("Ticker metrics local cache HIT (%d tickers)", len(data.get("metrics", {})))
            return data["metrics"]
        logger.info("Ticker metrics local cache EXPIRED (age: %.1fh)", (time.time() - data.get("updated_at", 0)) / 3600)
    except Exception as e:
        logger.warning("Failed to load ticker metrics cache: %s", e)
    return None


def save_cached_metrics(metrics_map: dict) -> None:
    """Save metrics dict to local JSON cache."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"metrics": metrics_map, "updated_at": time.time()}, f)
        logger.debug("Saved ticker metrics cache (%d tickers)", len(metrics_map))
    except Exception as e:
        logger.warning("Failed to save ticker metrics cache: %s", e)
