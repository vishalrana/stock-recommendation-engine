"""
Generate Signals
================
Evaluates Strategy 1.1 Beta on the latest bar of all cached tickers,
clears previous signals, inserts the new qualified signals (with ranking score),
and logs the scan. Supports automatic data downloading in CI/CD.

Usage:
    python -m jobs.generate_signals
"""

import os
import sys
import glob
import math
import time
import logging
from datetime import datetime, timedelta
from io import StringIO

import numpy as np
import pandas as pd
import requests

# Add project root and src to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from indicators import calculate_indicators
from downloader import fetch_ohlcv_data
from jobs.supabase_client import get_client

# Strategy parameters
RSI_PULLBACK_THRESHOLD = 45
RSI_RECOVERY_MIN = 45
RSI_RECOVERY_MAX = 65
VOLUME_MULTIPLIER = 1.0
TARGET_R_MULTIPLE = 3.0
LOOKBACK_RSI_DAYS = 10
SWING_LOW_LOOKBACK = 20

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


def fetch_sp500_tickers_100() -> tuple[list, dict, dict]:
    """Fetch the first 100 constituents of S&P 500 from Wikipedia with fallbacks."""
    tickers = []
    company_names = {}
    industries = {}

    try:
        logger.info("Fetching S&P 500 company info from Wikipedia...")
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        response = requests.get(
            url,
            headers={"User-Agent": "stock-recommendation-engine/1.0"},
            timeout=15,
        )
        response.raise_for_status()
        table = pd.read_html(StringIO(response.text))[0].head(100)
        for _, row in table.iterrows():
            ticker = str(row["Symbol"]).strip().upper().replace(".", "-")
            tickers.append(ticker)
            company_names[ticker] = str(row["Security"]).strip()
            industries[ticker] = str(row["GICS Sub-Industry"]).strip()
        logger.info(f"Loaded {len(tickers)} tickers from Wikipedia.")
    except Exception as e:
        logger.warning(f"Wikipedia fetch failed: {e}. Loading local fallback...")
        # Fallback to local backtest_summary.csv
        csv_path = os.path.join(PROJECT_ROOT, "outputs", "backtest_summary.csv")
        if os.path.exists(csv_path):
            try:
                summary_df = pd.read_csv(csv_path)
                for _, row in summary_df.iterrows():
                    ticker = str(row["ticker"]).strip().upper()
                    tickers.append(ticker)
                    industries[ticker] = str(row["industry"]).strip()
                    company_names[ticker] = ticker
                logger.info(f"Loaded {len(tickers)} fallback tickers from local CSV.")
            except Exception as csv_err:
                logger.error(f"Could not load fallback CSV: {csv_err}")
                
    return tickers, company_names, industries


def check_latest_signal(
    ticker: str,
    df: pd.DataFrame,
    company_name: str,
    industry: str,
    metrics_map: dict
) -> dict:
    """Evaluate Strategy 1.1 Beta and compute ranking score on the latest bar."""
    n_bars = len(df)
    if n_bars < 201:
        return None

    # Latest bar index is len(df) - 1
    t = n_bars - 1
    
    closes = df["CLOSE"].to_numpy(dtype=float)
    dma50s = df["DMA_50"].to_numpy(dtype=float)
    dma200s = df["DMA_200"].to_numpy(dtype=float)
    rsis = df["RSI_14"].to_numpy(dtype=float)
    volumes = df["VOLUME"].to_numpy(dtype=float)
    vol_mas = df["VOLUME_MA_20"].to_numpy(dtype=float)
    highs = df["HIGH"].to_numpy(dtype=float)
    dates = df.index

    # Check for NaNs in latest values
    c, d50, d200, rsi_now = closes[t], dma50s[t], dma200s[t], rsis[t]
    vol, vma = volumes[t], vol_mas[t]
    if any(np.isnan(x) for x in (c, d50, d200, rsi_now, vol, vma)):
        return None

    # 1. Trend: Price > 50 DMA > 200 DMA
    if not (c > d50 > d200):
        return None

    # 2. RSI Pullback: min RSI in last 10 days < 45
    rsi_window = rsis[max(0, t - LOOKBACK_RSI_DAYS + 1) : t + 1]
    min_rsi = np.nanmin(rsi_window)
    if min_rsi >= RSI_PULLBACK_THRESHOLD:
        return None

    # 3. Current RSI Recovery: 45 <= RSI <= 65
    if not (RSI_RECOVERY_MIN <= rsi_now <= RSI_RECOVERY_MAX):
        return None

    # 4. Volume > 1.0x 20-day average
    if not (vol > vma * VOLUME_MULTIPLIER):
        return None

    # 5. Swing Low Stop-loss
    stop_loss = find_swing_low(df)
    if stop_loss is None:
        return None

    entry_price = round(highs[t] * 1.001, 2)
    if stop_loss >= entry_price:
        return None

    risk = entry_price - stop_loss
    if risk <= 0:
        return None
    exit_price = round(entry_price + risk * TARGET_R_MULTIPLE, 2)

    latest_date = dates[t]
    if hasattr(latest_date, 'date'):
        signal_date = latest_date.date().isoformat()
    else:
        signal_date = str(latest_date)[:10]

    upside_pct = round(((exit_price - entry_price) / entry_price) * 100.0, 2)
    volume_ratio = round(vol / vma, 2) if vma > 0 else 0.0

    # Calculate Ranking Score: 40% win_rate + 40% expectancy + 20% upside
    m_info = metrics_map.get(ticker, {"win_rate": 0.0, "expectancy_pct": 0.0})
    win_rate = m_info["win_rate"]
    expectancy_pct = m_info["expectancy_pct"]
    score = round(0.4 * win_rate + 0.4 * expectancy_pct + 0.2 * upside_pct, 4)

    return {
        "scan_date": signal_date,
        "ticker": ticker,
        "company_name": company_name,
        "industry": industry,
        "price": round(c, 2),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "exit_price": exit_price,
        "upside_pct": upside_pct,
        "risk_reward": float(TARGET_R_MULTIPLE),
        "current_rsi": round(rsi_now, 2),
        "volume_ratio": volume_ratio,
        "score": score
    }


def main():
    start_time = time.time()
    logger.info("Starting Strategy 1.1 Beta nightly signal generator...")

    # Load S&P 500 tickers
    tickers, company_names, industries = fetch_sp500_tickers_100()

    # Create directories if they do not exist
    os.makedirs(os.path.join(PROJECT_ROOT, "data", "cache"), exist_ok=True)

    # Check if we should download fresh data (default: in CI/GitHub Actions, or when cache is empty)
    is_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    force_download = os.environ.get("DOWNLOAD_DATA") == "true"
    cache_empty = len(glob.glob(os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet"))) == 0

    if is_ci or force_download or cache_empty:
        logger.info("CI environment, forced download, or empty cache detected. Fetching daily data from yfinance...")
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=500)  # ~1.5 years for indicator calculation
        
        for i, ticker in enumerate(tickers, 1):
            logger.info(f"[{i}/{len(tickers)}] Downloading fresh data for {ticker}...")
            fetch_ohlcv_data(ticker, start_date=start_date, end_date=end_date)
        logger.info("Finished downloading daily data.")

    # Find cached files
    cache_path = os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet")
    parquet_files = sorted(glob.glob(cache_path))
    total_files = len(parquet_files)
    logger.info(f"Found {total_files} cached files in data/cache/")

    if total_files == 0:
        logger.error("No cached files found. Cannot generate signals.")
        sys.exit(1)

    # Initialize Supabase client
    try:
        supabase = get_client()
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        sys.exit(1)

    # Fetch historical metrics from Supabase to compute ranking score
    metrics_map = {}
    try:
        logger.info("Fetching historical metrics from Supabase ticker_metrics...")
        res = supabase.table("ticker_metrics").select("ticker, win_rate, expectancy_pct").execute()
        for row in res.data:
            ticker = row["ticker"].upper()
            metrics_map[ticker] = {
                "win_rate": float(row["win_rate"] or 0),
                "expectancy_pct": float(row["expectancy_pct"] or 0)
            }
        logger.info(f"Successfully loaded metrics for {len(metrics_map)} tickers from Supabase.")
    except Exception as e:
        logger.warning(f"Could not load ticker metrics from Supabase: {e}. Using fallback 0.0 values.")

    signals = []
    scanned_count = 0
    signal_date = None

    # Process all tickers
    for idx, fpath in enumerate(parquet_files, 1):
        ticker = os.path.basename(fpath).replace(".parquet", "").upper()
        
        # Only process if in our 100 tickers list (prevents processing random cache files)
        if tickers and ticker not in tickers:
            continue

        try:
            raw = pd.read_parquet(fpath, engine="pyarrow")
            df = calculate_indicators(raw).sort_index()
            scanned_count += 1
            
            # Determine scan date from the latest row of the first successful file
            if signal_date is None and len(df) > 0:
                latest_date = df.index[-1]
                if hasattr(latest_date, 'date'):
                    signal_date = latest_date.date().isoformat()
                else:
                    signal_date = str(latest_date)[:10]

            company_name = company_names.get(ticker, ticker)
            industry = industries.get(ticker, "Unknown")
            
            sig = check_latest_signal(ticker, df, company_name, industry, metrics_map)
            if sig is not None:
                signals.append(sig)
                logger.info(
                    f"[QUALIFIED] {ticker} | Entry: ${sig['entry_price']:.2f} | "
                    f"Stop: ${sig['stop_loss']:.2f} | Exit: ${sig['exit_price']:.2f} | "
                    f"RSI: {sig['current_rsi']:.1f} | Score: {sig['score']:.2f} | Vol Ratio: {sig['volume_ratio']:.2f}x"
                )

        except Exception as e:
            logger.error(f"Error scanning {ticker}: {e}")

    logger.info(f"Scan complete. Scanned: {scanned_count}, Qualified signals: {len(signals)}")
    
    if signal_date is None:
        logger.error("No valid scan date could be determined.")
        sys.exit(1)
        
    duration = round(time.time() - start_time, 2)
    status = "success"
    error_msg = None

    try:
        # 1. Clear previous signals from signals table
        logger.info("Clearing previous signals from Supabase...")
        supabase.table("signals").delete().neq("ticker", "").execute()
        logger.info("Previous signals cleared.")

        # 2. Insert new signals
        if signals:
            logger.info(f"Inserting {len(signals)} new signals into Supabase...")
            supabase.table("signals").insert(signals).execute()
            logger.info("Signals inserted successfully.")
        else:
            logger.info("No new signals to insert.")

    except Exception as e:
        status = "failed"
        error_msg = str(e)
        logger.error(f"Database update failed: {e}")

    # 3. Log results into scan_log
    scan_log_row = {
        "scan_date": signal_date,
        "tickers_scanned": scanned_count,
        "signals_generated": len(signals),
        "scan_duration_secs": duration,
        "status": status,
        "error_message": error_msg
    }

    try:
        logger.info(f"Logging scan execution to scan_log: {scan_log_row}...")
        supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
        logger.info("Scan log recorded successfully.")
    except Exception as e:
        logger.error(f"Failed to record scan log: {e}")
        sys.exit(1)

    if status == "failed":
        sys.exit(1)

    logger.info("Generate signals task finished successfully.")


if __name__ == "__main__":
    main()
