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
EXPANDED_METRICS_FILE = os.path.join("data", "cache", "universe", "expanded_ticker_metrics.json")
CONFIG_METRICS_FILE = os.path.join("config", "expanded_ticker_metrics.json")
TTL_SECONDS = int(os.environ.get("METRICS_CACHE_TTL_HOURS", "24")) * 3600


def load_cached_metrics() -> dict | None:
    """Load metrics from local JSON cache if fresh enough. Merges expanded metrics if available."""
    metrics_map = {}
    
    # 1. Load expanded universe metrics if available (data cache or config)
    target_exp_file = None
    if os.path.exists(EXPANDED_METRICS_FILE):
        target_exp_file = EXPANDED_METRICS_FILE
    elif os.path.exists(CONFIG_METRICS_FILE):
        target_exp_file = CONFIG_METRICS_FILE

    if target_exp_file:
        try:
            with open(target_exp_file, "r") as f:
                exp_data = json.load(f)
            exp_metrics = exp_data.get("metrics", {})
            if exp_metrics:
                metrics_map.update(exp_metrics)
                logger.info("Loaded %d expanded operational metrics from %s", len(exp_metrics), target_exp_file)
        except Exception as e:
            logger.warning("Failed to load expanded ticker metrics: %s", e)

    # 2. Load standard cache file
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
            if time.time() - data.get("updated_at", 0) < TTL_SECONDS:
                cached = data.get("metrics", {})
                metrics_map.update(cached)
                logger.info("Ticker metrics local cache HIT (%d total tickers merged)", len(metrics_map))
                return metrics_map
            logger.info("Ticker metrics local cache EXPIRED (age: %.1fh)", (time.time() - data.get("updated_at", 0)) / 3600)
        except Exception as e:
            logger.warning("Failed to load ticker metrics cache: %s", e)

    return metrics_map if metrics_map else None



def save_cached_metrics(metrics_map: dict) -> None:
    """Save metrics dict to local JSON cache."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"metrics": metrics_map, "updated_at": time.time()}, f)
        logger.debug("Saved ticker metrics cache (%d tickers)", len(metrics_map))
    except Exception as e:
        logger.warning("Failed to save ticker metrics cache: %s", e)
