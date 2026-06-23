"""
Sensitivity Test Script — Strategy 1.3 Rev A
============================================
Evaluates how varying parameter values affect the number of qualified tickers.
Pre-calculates static values for cached tickers to run a grid search efficiently.

Usage:
    python -m jobs.sensitivity_test
"""

import os
import sys
import glob
import logging
import pandas as pd
import numpy as np
import itertools

# Add project root and src to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from indicators import calculate_indicators
from jobs.supabase_client import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

SWING_LOW_LOOKBACK = 20
TARGET_R_MULTIPLE = 3.0

def find_swing_low(df_slice: pd.DataFrame) -> float:
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

def main():
    logger.info("Initializing sensitivity test...")
    supabase = get_client()

    # Load metrics map
    metrics_map = {}
    try:
        res = supabase.table("ticker_metrics").select("ticker, total_signals").execute()
        for row in res.data or []:
            ticker = row["ticker"].upper()
            metrics_map[ticker] = int(row["total_signals"] or 0)
        logger.info(f"Loaded ticker_metrics for {len(metrics_map)} tickers.")
    except Exception as e:
        logger.error(f"Failed to fetch ticker_metrics: {e}")
        sys.exit(1)

    # Get cached parquet files
    cache_path = os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet")
    parquet_files = sorted(glob.glob(cache_path))
    logger.info(f"Found {len(parquet_files)} cached files.")

    # Pre-calculate data points for each ticker to speed up grid search
    ticker_candidates = []

    for fpath in parquet_files:
        ticker = os.path.basename(fpath).replace(".parquet", "").upper()
        if ticker in ["XYZ", "TEST", "PLACEHOLDER", "SPY"]:
            continue
        try:
            raw = pd.read_parquet(fpath, engine="pyarrow")
            if len(raw) < 60:
                continue # Skip short history
            df = calculate_indicators(raw).sort_index()
            
            n_bars = len(df)
            if n_bars < 201:
                continue

            t = n_bars - 1
            closes = df["CLOSE"].to_numpy(dtype=float)
            dma50s = df["DMA_50"].to_numpy(dtype=float)
            dma200s = df["DMA_200"].to_numpy(dtype=float)
            rsis = df["RSI_14"].to_numpy(dtype=float)
            volumes = df["VOLUME"].to_numpy(dtype=float)
            vol_mas = df["VOLUME_MA_20"].to_numpy(dtype=float)
            highs = df["HIGH"].to_numpy(dtype=float)
            adxs = df["ADX_14"].to_numpy(dtype=float)
            macd_lines = df["MACD_LINE"].to_numpy(dtype=float)
            macd_sigs = df["MACD_SIGNAL"].to_numpy(dtype=float)

            c = closes[t]
            d50 = dma50s[t]
            d200 = dma200s[t]
            rsi_now = rsis[t]
            vol = volumes[t]
            vma = vol_mas[t]
            adx_now = adxs[t]
            macd_line = macd_lines[t]
            macd_sig = macd_sigs[t]

            if any(np.isnan(x) for x in (c, d50, d200, rsi_now, vol, vma, adx_now, macd_line, macd_sig)):
                continue

            # 1. Trend alignment: Price > 50 DMA > 200 DMA (Independent of search parameters)
            if not (c > d50 > d200):
                continue

            # Precalculate stop, entry, exit, and RR
            stop_loss = find_swing_low(df)
            if stop_loss is None:
                continue
            entry_price = round(highs[t] * 1.001, 2)
            if stop_loss >= entry_price:
                continue
            risk = entry_price - stop_loss
            if risk <= 0:
                continue
            exit_price = round(entry_price + risk * TARGET_R_MULTIPLE, 2)
            
            # Risk/Reward must be >= 3.0 (which it is since target R is 3.0)
            rr = (exit_price - entry_price) / (entry_price - stop_loss)
            if rr < 3.0:
                continue

            # RSI min 10 days
            rsi_window = rsis[max(0, t - 9) : t + 1]
            rsi_min_10d = np.nanmin(rsi_window)

            volume_ratio = vol / vma if vma > 0 else 0.0
            total_trades = metrics_map.get(ticker, 0)

            # Save clean pre-filtered candidate data
            ticker_candidates.append({
                "ticker": ticker,
                "rsi_min_10d": rsi_min_10d,
                "rsi_now": rsi_now,
                "adx_now": adx_now,
                "macd_line": macd_line,
                "macd_sig": macd_sig,
                "volume_ratio": volume_ratio,
                "total_trades": total_trades,
            })
        except Exception as e:
            logger.error(f"Error pre-filtering {ticker}: {e}")

    logger.info(f"Pre-filtering complete. {len(ticker_candidates)} tickers passed Trend, R/R, and Data filters.")

    # Parameter Grids
    RSI_DIP_THRESHOLDS = [40, 43, 45, 48, 50]
    RSI_RECOVERY_MAX = [58, 60, 62, 65]
    ADX_MIN = [15, 18, 20, 22, 25]
    TRADES_FLOOR = [3, 5, 8, 10]
    VOLUME_RATIO_MIN = [0.8, 1.0, 1.2]

    results = []

    # Run grid search
    grid = list(itertools.product(
        RSI_DIP_THRESHOLDS,
        RSI_RECOVERY_MAX,
        ADX_MIN,
        TRADES_FLOOR,
        VOLUME_RATIO_MIN
    ))

    logger.info(f"Running grid search over {len(grid)} parameter combinations...")

    for rsi_dip, rsi_rec_max, adx_min, trades_floor, volume_min in grid:
        passed_count = 0
        passed_tickers = []
        for cand in ticker_candidates:
            # 2. RSI Pullback-recovery: dip < threshold AND 45 <= now <= rec_max
            if not (cand["rsi_min_10d"] < rsi_dip and 45.0 <= cand["rsi_now"] <= rsi_rec_max):
                continue
            # 3. ADX: strength >= min
            if not (cand["adx_now"] >= adx_min):
                continue
            # 4. MACD: line > signal
            if not (cand["macd_line"] > cand["macd_sig"]):
                continue
            # 5. Volume: ratio >= min
            if not (cand["volume_ratio"] >= volume_min):
                continue
            # 7. Trades Floor: total_trades >= floor
            if not (cand["total_trades"] >= trades_floor):
                continue
            
            passed_count += 1
            passed_tickers.append(cand["ticker"])
            
        results.append({
            "rsi_dip": rsi_dip,
            "rsi_recovery_max": rsi_rec_max,
            "adx_min": adx_min,
            "trades_floor": trades_floor,
            "volume_min": volume_min,
            "tickers_passed": passed_count,
            "tickers": ",".join(passed_tickers)
        })

    # Save to CSV
    df_results = pd.DataFrame(results)
    output_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "sensitivity_results.csv")
    df_results.to_csv(csv_path, index=False)
    logger.info(f"Sensitivity results saved to {csv_path}")

    # Display some interesting statistics
    print("\n=== SENSITIVITY GRID HIGHLIGHTS ===")
    print(f"Total combinations tested: {len(df_results)}")
    print(f"Max tickers passed:       {df_results['tickers_passed'].max()}")
    print(f"Min tickers passed:       {df_results['tickers_passed'].min()}")
    
    # Show combinations matching our target 8-20 range
    target_combinations = df_results[(df_results["tickers_passed"] >= 8) & (df_results["tickers_passed"] <= 20)]
    print(f"Combinations returning 8-20 tickers: {len(target_combinations)}")
    
    if not target_combinations.empty:
        # Sort by trades_floor (descending), then rsi_dip (descending) to find closest to Strategy 1.3 settings
        best = target_combinations.sort_values(
            by=["trades_floor", "rsi_dip", "adx_min", "rsi_recovery_max", "volume_min"],
            ascending=[False, False, False, False, False]
        ).iloc[0]
        print("\nBest Match (Closest to Strategy 1.3 parameters):")
        print(f"RSI Dip Threshold:   {best['rsi_dip']}")
        print(f"RSI Recovery Max:    {best['rsi_recovery_max']}")
        print(f"ADX Min:             {best['adx_min']}")
        print(f"Trades Floor:        {best['trades_floor']}")
        print(f"Volume Ratio Min:    {best['volume_min']}")
        print(f"Tickers Passed ({best['tickers_passed']}): {best['tickers']}")
    print("===================================\n")

if __name__ == "__main__":
    main()
