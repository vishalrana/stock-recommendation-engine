"""
Seed Ticker Metrics — Strategy 1.3 Rev B
========================================
Calculates Strategy 1.1 Beta backtest metrics for all S&P 500 + Nasdaq-100 tickers
and seeds/updates them in the Supabase ticker_metrics table.

Usage:
    python -m jobs.seed_metrics [--force-refresh] [--tickers AAPL,MSFT,TSLA]

Database prerequisites:
    ALTER TABLE ticker_metrics ADD COLUMN IF NOT EXISTS median_win_return FLOAT DEFAULT 0.0;
"""

import os
import sys
import math
import logging
import argparse
from collections import defaultdict
from statistics import mean, median
from datetime import datetime
from io import StringIO
import requests

import numpy as np
import pandas as pd
import yfinance as yf

# Add project root and src to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from indicators import calculate_indicators
from jobs.supabase_client import get_client

# Strategy parameters (Strategy 1.1 Beta backtest logic)
RSI_PULLBACK_THRESHOLD = 45
RSI_RECOVERY_MIN = 45
RSI_RECOVERY_MAX = 65
VOLUME_MULTIPLIER = 1.0
TARGET_R_MULTIPLE = 3.0
ENTRY_EXPIRY_DAYS = 5
LOOKBACK_RSI_DAYS = 10
SWING_LOW_LOOKBACK = 20
TRANSACTION_COST_PCT = 0.10  # round-trip

BLACKLIST = {"XYZ", "TEST", "PLACEHOLDER"}

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
            "median_win_return": 0.0,
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

        total_signals += 1

        trade_res = simulate_trade_with_gaps(df, t, entry_price, stop_loss, target_price)
        if trade_res is not None:
            completed_trades.append(trade_res)

    wins = sum(1 for t in completed_trades if t["outcome"] == "win")
    losses = sum(1 for t in completed_trades if t["outcome"] == "loss")
    completed = wins + losses

    win_rate = round((wins / completed) * 100.0, 2) if completed > 0 else 0.0
    expectancy_pct = round(float(mean([t["return_pct"] for t in completed_trades])), 4) if completed > 0 else 0.0
    median_holding_days = round(float(median([t["holding_days"] for t in completed_trades])), 1) if completed > 0 else 0.0

    # Median win return % (winning trades only)
    winning_returns = [t["return_pct"] for t in completed_trades if t["outcome"] == "win"]
    median_win_return = round(float(median(winning_returns)), 4) if winning_returns else 0.0

    return {
        "ticker": ticker,
        "industry": industry,
        "total_signals": total_signals,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "expectancy_pct": expectancy_pct,
        "median_holding_days": median_holding_days,
        "median_win_return": median_win_return,
        "updated_at": datetime.utcnow().isoformat(),
    }


def fetch_sp500_tickers_all() -> tuple[list, dict, dict]:
    """Fetch S&P 500 + Nasdaq-100 universe from Wikipedia."""
    tickers: list[str] = []
    company_names: dict[str, str] = {}
    industries: dict[str, str] = {}
    sp500_set: set[str] = set()

    # S&P 500
    try:
        logger.info("Fetching S&P 500 company info from Wikipedia...")
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        response = requests.get(
            url,
            headers={"User-Agent": "stock-recommendation-engine/1.3b"},
            timeout=15,
        )
        response.raise_for_status()
        raw_table = pd.read_html(StringIO(response.text))[0]
        for _, row in raw_table.iterrows():
            ticker = str(row["Symbol"]).strip().upper().replace(".", "-")
            if ticker in BLACKLIST:
                continue
            tickers.append(ticker)
            sp500_set.add(ticker)
            company_names[ticker] = str(row["Security"]).strip()
            industries[ticker] = str(row["GICS Sub-Industry"]).strip()
        logger.info(f"S&P 500: loaded {len(sp500_set)} tickers from Wikipedia.")
    except Exception as e:
        logger.warning(f"S&P 500 Wikipedia fetch failed: {e}.")

    sp500_count = len(sp500_set)

    # Nasdaq-100
    ndx_unique_count = 0
    try:
        logger.info("Fetching Nasdaq-100 company info from Wikipedia...")
        ndx_url = "https://en.wikipedia.org/wiki/Nasdaq-100"
        ndx_response = requests.get(
            ndx_url,
            headers={"User-Agent": "stock-recommendation-engine/1.3b"},
            timeout=15,
        )
        ndx_response.raise_for_status()
        ndx_tables = pd.read_html(StringIO(ndx_response.text))
        ndx_table = None
        for t in ndx_tables:
            if "Ticker" in t.columns:
                ndx_table = t
                break
        if ndx_table is not None:
            for _, row in ndx_table.iterrows():
                ticker = str(row["Ticker"]).strip().upper().replace(".", "-")
                if ticker in BLACKLIST or ticker in sp500_set:
                    continue
                tickers.append(ticker)
                company_names[ticker] = str(row.get("Company", ticker)).strip()
                industries[ticker] = str(row.get("GICS Sector", "Unknown")).strip()
                ndx_unique_count += 1
            logger.info(f"Nasdaq-100: added {ndx_unique_count} non-overlapping tickers.")
        else:
            logger.warning("Nasdaq-100 Wikipedia table not found (no 'Ticker' column).")
    except Exception as e:
        logger.warning(f"Nasdaq-100 Wikipedia fetch failed: {e}.")

    total = len(tickers)
    logger.info(
        f"Universe: {sp500_count} S&P 500 + {ndx_unique_count} Nasdaq-100 non-overlapping = {total} total tickers"
    )
    return tickers, company_names, industries


def main():
    parser = argparse.ArgumentParser(description="Seed Ticker Metrics")
    parser.add_argument("--force-refresh", action="store_true", help="Re-process all tickers even if they exist")
    parser.add_argument("--tickers", type=str, help="Comma-separated list of specific tickers to seed")
    args = parser.parse_args()

    logger.info("Starting Strategy 1.3 metrics seeding...")

    # Initialize Supabase client
    try:
        supabase = get_client()
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        sys.exit(1)

    # Fetch existing metrics from Supabase
    existing_map = {}
    try:
        logger.info("Fetching existing metrics from Supabase...")
        res = supabase.table("ticker_metrics").select("ticker, total_signals, win_rate").execute()
        for row in res.data:
            ticker = row["ticker"].upper()
            existing_map[ticker] = {
                "total_signals": int(row.get("total_signals") or 0),
                "win_rate": float(row.get("win_rate") or 0.0),
            }
        logger.info(f"Loaded {len(existing_map)} existing rows from database.")
    except Exception as e:
        logger.warning(f"Failed to fetch existing metrics: {e}")

    # Fetch full S&P 500 + NDX universe
    tickers_all, company_names, industries = fetch_sp500_tickers_all()
    total_universe = len(tickers_all)

    # Decide targets based on CLI arguments and idempotency
    target_tickers = []
    skipped_count = 0

    if args.tickers:
        cli_list = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        for t in cli_list:
            target_tickers.append(t)
            if t not in company_names:
                company_names[t] = t
                industries[t] = "Unknown"
        logger.info(f"Using CLI specific tickers: {target_tickers}")
    else:
        for ticker in tickers_all:
            exist = existing_map.get(ticker)
            if exist and not args.force_refresh:
                # If it exists AND has total_trades >= 10, skip it
                if False:  # exist["total_signals"] >= 10
                    skipped_count += 1
                    continue
            target_tickers.append(ticker)
        logger.info(f"Idempotency Filter: skipped {skipped_count} already satisfied tickers. Tickers to process: {len(target_tickers)}")

    # Telemetry tracking
    processed_count = 0
    seeded_new = 0
    updated_existing = 0
    failure_count = 0
    failed_tickers = []
    batch_metrics = []
    total_upserted = 0

    # Seeding loop
    for idx, ticker in enumerate(target_tickers, 1):
        try:
            # Download 5 years of daily data (about 1260 trading bars)
            raw = yf.download(ticker, period="5y", progress=False, timeout=15)
            if raw.empty:
                logger.warning(f"  [{idx}/{len(target_tickers)}] {ticker}: yfinance returned no data. Skipping.")
                failed_tickers.append(ticker)
                failure_count += 1
                continue

            # Normalize column names
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw.columns = raw.columns.str.upper()

            # Ensure required columns
            required_cols = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
            if not all(col in raw.columns for col in required_cols):
                logger.warning(f"  [{idx}/{len(target_tickers)}] {ticker}: Missing columns. Have: {list(raw.columns)}. Skipping.")
                failed_tickers.append(ticker)
                failure_count += 1
                continue

            # Calculate technical indicators and run backtest
            df = calculate_indicators(raw).sort_index()
            industry = industries.get(ticker, "Unknown")
            
            ticker_metrics = backtest_ticker_metrics(ticker, df, industry)
            batch_metrics.append(ticker_metrics)

            # Track seeded vs updated
            if ticker in existing_map:
                updated_existing += 1
            else:
                seeded_new += 1
            
            processed_count += 1

        except Exception as e:
            logger.error(f"  [{idx}/{len(target_tickers)}] FAILED to process {ticker}: {e}")
            failed_tickers.append(ticker)
            failure_count += 1

        # Batch upsert every 20 processed tickers to keep DB writes safe and efficient
        if len(batch_metrics) >= 20 or idx == len(target_tickers):
            if batch_metrics:
                try:
                    supabase.table("ticker_metrics").upsert(batch_metrics, on_conflict="ticker").execute()
                    total_upserted += len(batch_metrics)
                except Exception as upsert_err:
                    logger.error(f"Batch upsert failed: {upsert_err}. Attempting row-by-row fallback...")
                    for row in batch_metrics:
                        try:
                            supabase.table("ticker_metrics").upsert(row, on_conflict="ticker").execute()
                            total_upserted += 1
                        except Exception as row_err:
                            logger.error(f"Row upsert failed for {row['ticker']}: {row_err}")
                batch_metrics = []

        # Print progress every 50 tickers processed
        if idx % 50 == 0 or idx == len(target_tickers):
            print(f"Processed {idx}/{len(target_tickers)} tickers. Seeded {seeded_new} new. Updated {updated_existing} existing. Skipped {skipped_count}.")

    print("\n" + "=" * 80)
    print("  SEEDING RUN COMPLETE SUMMARY")
    print("=" * 80)

    # Fetch final summary statistics directly from Supabase
    try:
        final_res = supabase.table("ticker_metrics").select("ticker, total_signals").execute()
        final_rows = final_res.data or []
        total_seeded = len(final_rows)
        total_gt_10 = sum(1 for r in final_rows if int(r.get("total_signals") or 0) >= 10)
        total_lt_10 = sum(1 for r in final_rows if int(r.get("total_signals") or 0) < 10)
    except Exception as e:
        logger.error(f"Failed to fetch final summary stats: {e}")
        total_seeded = total_universe - skipped_count + processed_count
        total_gt_10 = "N/A"
        total_lt_10 = "N/A"

    print(f"Total tickers in universe                 : {total_universe}")
    print(f"Total seeded in ticker_metrics            : {total_seeded}")
    print(f"Total with total_trades >= 10             : {total_gt_10}")
    print(f"Total with total_trades < 10 (insufficient): {total_lt_10}")
    print(f"Total downloads/runs failed               : {failure_count}")
    if failed_tickers:
        print(f"Failed Tickers List                       : {', '.join(failed_tickers)}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
