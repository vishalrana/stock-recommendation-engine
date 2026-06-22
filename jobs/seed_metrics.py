"""
Seed Ticker Metrics
===================
Calculates Strategy 1.1 Beta backtest metrics for all cached tickers
and seeds them into the Supabase database.

Usage:
    python -m jobs.seed_metrics
"""

import os
import sys
import glob
import math
import logging
from collections import defaultdict
from statistics import mean, median
from datetime import datetime

import numpy as np
import pandas as pd

# Add project root and src to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from indicators import calculate_indicators
from jobs.supabase_client import get_client

# Strategy parameters
RSI_PULLBACK_THRESHOLD = 45
RSI_RECOVERY_MIN = 45
RSI_RECOVERY_MAX = 65
VOLUME_MULTIPLIER = 1.0
TARGET_R_MULTIPLE = 3.0
ENTRY_EXPIRY_DAYS = 5
LOOKBACK_RSI_DAYS = 10
SWING_LOW_LOOKBACK = 20
TRANSACTION_COST_PCT = 0.10  # round-trip

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


def find_swing_low(df_slice: pd.DataFrame) -> float:
    """Find the most recent valid swing low in the last 20 trading days."""
    if len(df_slice) < SWING_LOW_LOOKBACK:
        return None
    lookback = df_slice.tail(SWING_LOW_LOOKBACK)
    if "LOW" not in lookback.columns or "CLOSE" not in lookback.columns:
        return None
    lows = lookback["LOW"].to_numpy(dtype=float)
    current_price = float(lookback["CLOSE"].iloc[-1])
    for i in range(len(lows) - 3, 1, -1):
        c = lows[i]
        if c >= current_price:
            continue
        if c < lows[i - 2] and c < lows[i - 1] and c < lows[i + 1] and c < lows[i + 2]:
            return float(c)
    return None


def simulate_trade_with_gaps(
    df: pd.DataFrame,
    signal_idx: int,
    entry_price: float,
    stop_loss: float,
    target: float,
) -> dict:
    """
    Simulate one trade with realistic execution:
      - Entry gaps: fill at max(entry_price, open) on trigger day
      - Stop gaps: if day opens <= stop_loss, exit at open (loss > 1R possible)
      - Target gaps: if day opens >= target, exit at open (win > 3R possible)
      - Stop-first collision rule on same-bar ambiguity
      - 0.10% round-trip transaction cost deducted
    """
    first = signal_idx + 1
    last = min(signal_idx + ENTRY_EXPIRY_DAYS, len(df) - 1)
    entry_idx = None
    actual_entry = None

    for idx in range(first, last + 1):
        if idx >= len(df):
            break
        high = float(df["HIGH"].iloc[idx])
        if high >= entry_price:
            entry_idx = idx
            day_open = float(df["OPEN"].iloc[idx])
            actual_entry = max(entry_price, day_open)
            break

    if entry_idx is None:
        return None

    for idx in range(entry_idx, len(df)):
        day_open = float(df["OPEN"].iloc[idx])
        low = float(df["LOW"].iloc[idx])
        high = float(df["HIGH"].iloc[idx])

        # Skip open-gap check on the entry bar itself (we just entered intraday)
        if idx > entry_idx:
            if day_open <= stop_loss:
                ret = ((day_open - actual_entry) / actual_entry) * 100.0 - TRANSACTION_COST_PCT
                return {"outcome": "loss", "holding_days": idx - entry_idx, "return_pct": ret}
            if day_open >= target:
                ret = ((day_open - actual_entry) / actual_entry) * 100.0 - TRANSACTION_COST_PCT
                return {"outcome": "win", "holding_days": idx - entry_idx, "return_pct": ret}

        stop_hit = low <= stop_loss
        target_hit = high >= target

        if stop_hit and target_hit:
            # Conservative: stop wins ambiguity
            ret = ((stop_loss - actual_entry) / actual_entry) * 100.0 - TRANSACTION_COST_PCT
            return {"outcome": "loss", "holding_days": idx - entry_idx, "return_pct": ret}
        if stop_hit:
            ret = ((stop_loss - actual_entry) / actual_entry) * 100.0 - TRANSACTION_COST_PCT
            return {"outcome": "loss", "holding_days": idx - entry_idx, "return_pct": ret}
        if target_hit:
            ret = ((target - actual_entry) / actual_entry) * 100.0 - TRANSACTION_COST_PCT
            return {"outcome": "win", "holding_days": idx - entry_idx, "return_pct": ret}

    return None  # Unresolved


def backtest_ticker_metrics(ticker: str, df: pd.DataFrame, industry: str) -> dict:
    """Run full historical backtest under Strategy 1.1 Beta and compute aggregate metrics."""
    n_bars = len(df)
    if n_bars < 201:
        logger.warning(f"{ticker}: Insufficient data ({n_bars} bars, need at least 201)")
        return {
            "ticker": ticker,
            "industry": industry,
            "total_signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "expectancy_pct": 0.0,
            "median_holding_days": 0.0,
        }

    closes = df["CLOSE"].to_numpy(dtype=float)
    dma50s = df["DMA_50"].to_numpy(dtype=float)
    dma200s = df["DMA_200"].to_numpy(dtype=float)
    rsis = df["RSI_14"].to_numpy(dtype=float)
    volumes = df["VOLUME"].to_numpy(dtype=float)
    vol_mas = df["VOLUME_MA_20"].to_numpy(dtype=float)
    highs = df["HIGH"].to_numpy(dtype=float)

    total_signals = 0
    completed_trades = []

    for t in range(200, n_bars - 1):  # need at least T+1 bar
        # NaN guards
        c, d50, d200, rsi_now = closes[t], dma50s[t], dma200s[t], rsis[t]
        vol, vma = volumes[t], vol_mas[t]
        if any(np.isnan(x) for x in (c, d50, d200, rsi_now, vol, vma)):
            continue

        # 1. Trend: Price > 50 DMA > 200 DMA
        if not (c > d50 > d200):
            continue

        # 2. RSI Pullback: min RSI in last 10 days < 45
        rsi_window = rsis[max(0, t - LOOKBACK_RSI_DAYS + 1) : t + 1]
        min_rsi = np.nanmin(rsi_window)
        if min_rsi >= RSI_PULLBACK_THRESHOLD:
            continue

        # 3. Current RSI Recovery: 45 <= RSI <= 65
        if not (RSI_RECOVERY_MIN <= rsi_now <= RSI_RECOVERY_MAX):
            continue

        # 4. Volume > 1.0x 20-day average
        if not (vol > vma * VOLUME_MULTIPLIER):
            continue

        # 5. Swing Low Stop-loss
        stop_loss = find_swing_low(df.iloc[: t + 1])
        if stop_loss is None:
            continue

        entry_price = round(highs[t] * 1.001, 2)
        if stop_loss >= entry_price:
            continue

        risk = entry_price - stop_loss
        if risk <= 0:
            continue
        target_price = round(entry_price + risk * TARGET_R_MULTIPLE, 2)

        # Increment total signals
        total_signals += 1

        # Simulate
        trade_res = simulate_trade_with_gaps(df, t, entry_price, stop_loss, target_price)
        if trade_res is not None:
            completed_trades.append(trade_res)

    wins = sum(1 for t in completed_trades if t["outcome"] == "win")
    losses = sum(1 for t in completed_trades if t["outcome"] == "loss")
    completed = wins + losses

    win_rate = round((wins / completed) * 100.0, 2) if completed > 0 else 0.0
    expectancy_pct = round(float(mean([t["return_pct"] for t in completed_trades])), 4) if completed > 0 else 0.0
    median_holding_days = round(float(median([t["holding_days"] for t in completed_trades])), 1) if completed > 0 else 0.0

    return {
        "ticker": ticker,
        "industry": industry,
        "total_signals": total_signals,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "expectancy_pct": expectancy_pct,
        "median_holding_days": median_holding_days,
        "updated_at": datetime.utcnow().isoformat(),
    }


def load_industries() -> dict:
    """Load GICS Sub-Industry mappings from Wikipedia and local backtest summary CSV."""
    industry_map = {}

    # 1. Read from Wikipedia constituents if possible
    try:
        from backtester import fetch_validation_universe
        logger.info("Attempting to fetch current S&P 500 industries from Wikipedia...")
        _, wiki_industries = fetch_validation_universe()
        industry_map.update(wiki_industries)
        logger.info(f"Loaded {len(wiki_industries)} industry mappings from Wikipedia.")
    except Exception as e:
        logger.warning(f"Could not fetch industry mappings from Wikipedia: {e}")

    # 2. Fallback to local CSV
    csv_path = os.path.join(PROJECT_ROOT, "outputs", "backtest_summary.csv")
    if os.path.exists(csv_path):
        try:
            logger.info(f"Reading fallback industry mappings from {csv_path}...")
            summary_df = pd.read_csv(csv_path)
            csv_count = 0
            for _, row in summary_df.iterrows():
                ticker = str(row["ticker"]).strip().upper()
                industry = str(row["industry"]).strip()
                if ticker not in industry_map or industry_map[ticker] == "nan" or not industry_map[ticker]:
                    industry_map[ticker] = industry
                    csv_count += 1
            logger.info(f"Loaded/updated {csv_count} industry mappings from local CSV.")
        except Exception as e:
            logger.warning(f"Could not read local CSV industry mappings: {e}")

    return industry_map


def main():
    logger.info("Starting Strategy 1.1 Beta metrics seeding...")

    # Load industries
    industry_map = load_industries()

    # Find cached parquet files
    cache_path = os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet")
    parquet_files = sorted(glob.glob(cache_path))
    total_files = len(parquet_files)
    logger.info(f"Found {total_files} cached tickers in {os.path.join(PROJECT_ROOT, 'data', 'cache')}")

    if total_files == 0:
        logger.error("No cached Parquet files found. Please ensure the cache exists in data/cache/.")
        sys.exit(1)

    # Initialize Supabase client
    try:
        supabase = get_client()
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        sys.exit(1)

    all_metrics = []
    success_count = 0
    failure_count = 0

    # Process tickers one by one
    for idx, fpath in enumerate(parquet_files, 1):
        ticker = os.path.basename(fpath).replace(".parquet", "").upper()
        logger.info(f"[{idx}/{total_files}] Processing {ticker}...")

        try:
            raw = pd.read_parquet(fpath, engine="pyarrow")
            df = calculate_indicators(raw).sort_index()
            industry = industry_map.get(ticker, "Unknown")
            
            ticker_metrics = backtest_ticker_metrics(ticker, df, industry)
            all_metrics.append(ticker_metrics)
            
            logger.info(
                f"  -> Signals: {ticker_metrics['total_signals']} | "
                f"Completed: {ticker_metrics['wins'] + ticker_metrics['losses']} | "
                f"Win Rate: {ticker_metrics['win_rate']:.2f}% | "
                f"Expectancy: {ticker_metrics['expectancy_pct']:+.4f}%"
            )
            success_count += 1

        except Exception as e:
            logger.error(f"  -> FAILED to process {ticker}: {e}")
            failure_count += 1

    logger.info(f"Processing complete. Success: {success_count}, Failures: {failure_count}")

    # Upsert in batches of 20
    batch_size = 20
    upserted_count = 0
    logger.info(f"Upserting {len(all_metrics)} ticker metrics into Supabase in batches of {batch_size}...")

    for i in range(0, len(all_metrics), batch_size):
        batch = all_metrics[i : i + batch_size]
        try:
            response = supabase.table("ticker_metrics").upsert(batch, on_conflict="ticker").execute()
            upserted_count += len(batch)
            logger.info(f"Successfully upserted batch {i//batch_size + 1} ({len(batch)} rows). Total upserted: {upserted_count}")
        except Exception as e:
            logger.error(f"Failed to upsert batch {i//batch_size + 1}: {e}")
            # If batch fails, log and try row by row so we don't lose the whole batch
            logger.info("Attempting row-by-row fallback for failed batch...")
            for row in batch:
                try:
                    supabase.table("ticker_metrics").upsert(row, on_conflict="ticker").execute()
                    upserted_count += 1
                except Exception as row_err:
                    logger.error(f"Failed to upsert row for {row['ticker']}: {row_err}")

    logger.info(f"Seeding completed. Total rows successfully upserted: {upserted_count}/{len(all_metrics)}")


if __name__ == "__main__":
    main()
