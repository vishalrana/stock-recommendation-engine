"""
Expanded Universe Historical Ticker Metrics Generator
=====================================================
Calculates point-in-time swing baseline metrics for all liquid operational
US equity universe tickers using local cached daily partitions (zero lookahead).

Strictly read-only with respect to Supabase production database.
Saves results to data/cache/universe/expanded_ticker_metrics.json.
"""

import os
import sys
import time
import json
import logging
import pandas as pd
import numpy as np

# Ensure src and root are in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.data.cache_manager import get_cache_manager
from src.universe.us_equities import USEquitiesUniverseProvider
from src.universe.filters import evaluate_point_in_time_liquidity
from src.indicators import calculate_dma, calculate_rsi, calculate_volume_ma
from jobs.seed_metrics import backtest_ticker_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


def process_ticker_fast(ticker: str, df: pd.DataFrame, industry: str) -> dict:
    """Run fast indicator calculation and baseline swing backtest for a single ticker."""
    try:
        if df is None or len(df) < 201:
            return {
                "ticker": ticker,
                "industry": industry,
                "total_signals": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "expectancy_pct": 0.0,
                "median_holding_days": 0.0,
                "median_win_return": 0.0,
                "insufficient_sample": True,
            }

        df_sorted = df.sort_index().copy()
        if isinstance(df_sorted.columns, pd.MultiIndex):
            df_sorted.columns = df_sorted.columns.get_level_values(0)
        df_sorted.columns = df_sorted.columns.str.upper()

        # Vectorized calculation of indicators required by backtest_ticker_metrics
        df_sorted["DMA_50"] = calculate_dma(df_sorted["CLOSE"], 50)
        df_sorted["DMA_200"] = calculate_dma(df_sorted["CLOSE"], 200)
        df_sorted["RSI_14"] = calculate_rsi(df_sorted["CLOSE"], 14)
        df_sorted["VOLUME_MA_20"] = calculate_volume_ma(df_sorted["VOLUME"], 20)

        res = backtest_ticker_metrics(ticker, df_sorted, industry)
        
        # Enforce sample size flag
        completed = res.get("wins", 0) + res.get("losses", 0)
        res["insufficient_sample"] = completed < 5
        return res
    except Exception as e:
        logger.debug(f"Error computing metrics for {ticker}: {e}")
        return {
            "ticker": ticker,
            "industry": industry,
            "total_signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "expectancy_pct": 0.0,
            "median_holding_days": 0.0,
            "median_win_return": 0.0,
            "insufficient_sample": True,
        }


def main():
    t_start = time.time()
    logger.info("Initializing CacheManager and preloading history up to 2026-10-02...")
    cm = get_cache_manager()
    cm.preload_history("2024-01-01", "2026-10-02")

    logger.info("Loading US Equities Universe...")
    provider = USEquitiesUniverseProvider()
    records = provider.get_universe()
    record_map = {r.data_provider_ticker: r for r in records}

    logger.info("Filtering operational discovery universe (>=60 bars PIT on 2026-10-02)...")
    operational_tickers = []
    for r in records:
        t = r.data_provider_ticker
        df = cm.get_ticker_history(t, "2024-01-01", "2026-10-02")
        if df is not None and not df.empty:
            is_op, _, _ = evaluate_point_in_time_liquidity(df, as_of_date="2026-10-02", min_history_days=60)
            if is_op:
                operational_tickers.append(t)

    logger.info(f"Operational discovery universe: {len(operational_tickers)} tickers.")

    # Run backtest for all operational tickers
    logger.info(f"Computing PIT swing metrics for {len(operational_tickers)} tickers...")
    metrics_map = {}
    completed_samples = 0
    insufficient_samples = 0
    total_signals_sum = 0
    total_wins_sum = 0
    total_losses_sum = 0

    t_calc = time.time()
    for idx, t in enumerate(operational_tickers, 1):
        df = cm.get_ticker_history(t, "2024-01-01", "2026-10-02")
        rec = record_map.get(t)
        ind = rec.sector if rec and rec.sector and rec.sector != "Unknown" else (rec.industry if rec else "Unknown")
        
        m = process_ticker_fast(t, df, ind)
        metrics_map[t] = m
        total_signals_sum += m.get("total_signals", 0)
        total_wins_sum += m.get("wins", 0)
        total_losses_sum += m.get("losses", 0)

        if m.get("insufficient_sample"):
            insufficient_samples += 1
        else:
            completed_samples += 1

        if idx % 500 == 0 or idx == len(operational_tickers):
            logger.info(f"Progress: {idx}/{len(operational_tickers)} (Sufficient sample: {completed_samples}, Insufficient: {insufficient_samples})")

    calc_duration = time.time() - t_calc
    logger.info(f"Metrics calculation complete in {calc_duration:.2f}s ({calc_duration/max(1, len(operational_tickers))*1000:.1f}ms/ticker)")

    # Save to data/cache/universe/expanded_ticker_metrics.json
    out_dir = os.path.join(PROJECT_ROOT, "data", "cache", "universe")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "expanded_ticker_metrics.json")

    summary_payload = {
        "as_of_date": "2026-10-02",
        "universe_size": len(operational_tickers),
        "sufficient_sample_count": completed_samples,
        "insufficient_sample_count": insufficient_samples,
        "total_signals_evaluated": total_signals_sum,
        "total_completed_trades": total_wins_sum + total_losses_sum,
        "total_wins": total_wins_sum,
        "total_losses": total_losses_sum,
        "aggregate_win_rate": round(total_wins_sum / max(1, total_wins_sum + total_losses_sum) * 100, 2),
        "metrics": metrics_map,
        "generated_at": time.time(),
    }

    with open(out_path, "w") as f:
        json.dump(summary_payload, f, indent=2)

    logger.info(f"Saved {len(metrics_map)} expanded metrics to {out_path}")
    print(f"\nCOMPUTATION COMPLETE:")
    print(f"- Total operational tickers: {len(operational_tickers)}")
    print(f"- Tickers with sufficient sample (>= 5 trades): {completed_samples}")
    print(f"- Tickers with insufficient sample (< 5 trades): {insufficient_samples}")
    print(f"- Total signals simulated: {total_signals_sum}")
    print(f"- Total completed trades: {total_wins_sum + total_losses_sum}")
    print(f"- Total wins: {total_wins_sum}, Total losses: {total_losses_sum}")
    print(f"- Aggregate win rate: {round(total_wins_sum / max(1, total_wins_sum + total_losses_sum) * 100, 2)}%")
    print(f"- Total elapsed time: {time.time() - t_start:.2f}s")


if __name__ == "__main__":
    main()
