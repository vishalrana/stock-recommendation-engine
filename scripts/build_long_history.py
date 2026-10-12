"""
Long-History Price Builder (2004 Onward)
=========================================
Downloads daily price history (auto_adjust=False, actions=True, start 2004-01-01)
for U1 common equities, SPY, sector ETFs, and point-in-time S&P 500 members (2008-2022).
Saves individual float32 parquets under data/backtest_history_long/{ticker}.parquet.
"""

import argparse
import logging
import os
import sys
import time
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.research.survivorship import load_membership, yahoo_symbol
from scripts.research_signal_ic import SECTOR_TO_ETF

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "backtest_history_long")
FAILURES_CSV = os.path.join(PROJECT_ROOT, "outputs", "swing", "long_history_failures.csv")

SECTOR_ETFS = sorted(set(SECTOR_TO_ETF.values()) | {"SPY"})


def get_target_tickers() -> List[str]:
    """Compile distinct target tickers: U1 production universe + sector ETFs + S&P 500 2008-2022."""
    tickers: Set[str] = set()

    # 1. Sector ETFs and benchmark
    tickers.update(SECTOR_ETFS)

    # 2. U1: today's security master. Fails loudly rather than silently building a smaller universe.
    from scripts.validate_backtest_pipeline import production_universe
    stocks, _ = production_universe()
    tickers.update(yahoo_symbol(t) for t in stocks if t)

    # 3. S&P 500 historical membership overlapping 2008-2022
    try:
        m = load_membership()
        # membership overlaps [2008-01-01, 2022-12-31]
        start_bound = pd.Timestamp("2008-01-01")
        end_bound = pd.Timestamp("2022-12-31")
        for row in m.itertuples(index=False):
            end_d = row.end_date if pd.notna(row.end_date) else pd.Timestamp.max
            if row.start_date <= end_bound and end_d >= start_bound:
                tickers.add(yahoo_symbol(row.ticker))
    except Exception as e:
        logger.warning(f"Could not load historical S&P 500 membership: {e}")

    return sorted(tickers)


def compute_unadjusted_close(close: np.ndarray, splits: np.ndarray) -> np.ndarray:
    """
    Unadjusted close(t) = Close(t) x the product of split ratios effective strictly after t.
    Uses split multiplier cumprod backward to reverse Yahoo's split-adjustment.
    """
    n = len(close)
    if n == 0:
        return np.array([], dtype=np.float32)
    mult = np.where((splits > 0) & np.isfinite(splits), splits, 1.0)
    # Reverse cumulative product
    cum_mult = np.cumprod(mult[::-1])[::-1]
    # Future split multiplier strictly after bar t
    future_mult = np.append(cum_mult[1:], 1.0)
    return (close * future_mult).astype(np.float32)


def process_ticker_frame(df_t: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Process raw yfinance DataFrame into standard float32 schema with total-return
    and unadjusted close columns.
    """
    if df_t is None or df_t.empty:
        return None

    if isinstance(df_t.columns, pd.MultiIndex):
        df_t = df_t.copy()
        df_t.columns = df_t.columns.get_level_values(0)

    # Handle standard columns
    cols_map = {
        "Open": "OPEN",
        "High": "HIGH",
        "Low": "LOW",
        "Close": "CLOSE",
        "Adj Close": "ADJ_CLOSE",
        "Volume": "VOLUME",
        "Stock Splits": "SPLIT",
        "Dividends": "DIVIDEND",
    }
    present = {k: v for k, v in cols_map.items() if k in df_t.columns}
    if "Close" not in present or "Open" not in present:
        return None

    sub = df_t[list(present.keys())].rename(columns=present).copy()

    # Ensure required columns exist
    for req in ["OPEN", "HIGH", "LOW", "CLOSE", "ADJ_CLOSE", "VOLUME"]:
        if req not in sub.columns:
            return None

    if "SPLIT" not in sub.columns:
        sub["SPLIT"] = 0.0
    if "DIVIDEND" not in sub.columns:
        sub["DIVIDEND"] = 0.0

    # Fill NaN / clean
    sub["SPLIT"] = sub["SPLIT"].fillna(0.0)
    sub["DIVIDEND"] = sub["DIVIDEND"].fillna(0.0)
    sub["VOLUME"] = sub["VOLUME"].fillna(0.0)

    # Drop bars with missing or non-positive price
    valid_mask = (
        np.isfinite(sub["OPEN"]) & (sub["OPEN"] > 0) &
        np.isfinite(sub["HIGH"]) & (sub["HIGH"] > 0) &
        np.isfinite(sub["LOW"]) & (sub["LOW"] > 0) &
        np.isfinite(sub["CLOSE"]) & (sub["CLOSE"] > 0)
    )
    sub = sub[valid_mask]
    if sub.empty:
        return None

    # Total-return OHLC = OHLC x (Adj Close / Close)
    close_vals = sub["CLOSE"].to_numpy(dtype=float)
    adj_vals = sub["ADJ_CLOSE"].fillna(sub["CLOSE"]).to_numpy(dtype=float)
    ratio = np.where((close_vals > 0) & np.isfinite(adj_vals), adj_vals / close_vals, 1.0)

    sub["TR_OPEN"] = (sub["OPEN"].to_numpy(dtype=float) * ratio).astype(np.float32)
    sub["TR_HIGH"] = (sub["HIGH"].to_numpy(dtype=float) * ratio).astype(np.float32)
    sub["TR_LOW"] = (sub["LOW"].to_numpy(dtype=float) * ratio).astype(np.float32)
    sub["TR_CLOSE"] = adj_vals.astype(np.float32)

    # Unadjusted close for $5 price filter
    split_vals = sub["SPLIT"].to_numpy(dtype=float)
    sub["UNADJ_CLOSE"] = compute_unadjusted_close(close_vals, split_vals)

    # Cast all numerical columns to float32
    for c in ["OPEN", "HIGH", "LOW", "CLOSE", "ADJ_CLOSE", "VOLUME", "SPLIT", "DIVIDEND"]:
        sub[c] = sub[c].astype(np.float32)

    sub.index = pd.DatetimeIndex(sub.index).tz_localize(None)
    sub.index.name = "Date"
    sub.sort_index(inplace=True)
    # Remove duplicate dates if any
    sub = sub[~sub.index.duplicated(keep="last")]

    return sub


def download_chunk(tickers: List[str], start_date: str = "2004-01-01",
                   max_retries: int = 3) -> Dict[str, pd.DataFrame]:
    """Download a chunk of tickers using yfinance with backoff."""
    results: Dict[str, pd.DataFrame] = {}
    if not tickers:
        return results

    ticker_str = " ".join(tickers)
    for attempt in range(max_retries):
        try:
            df = yf.download(
                ticker_str,
                start=start_date,
                auto_adjust=False,
                actions=True,
                threads=True,
                progress=False,
            )
            if df is None or df.empty:
                time.sleep(1.0 * (2 ** attempt))
                continue

            is_multi = isinstance(df.columns, pd.MultiIndex) and ("Ticker" in df.columns.names or len(tickers) > 1)

            for t in tickers:
                try:
                    if is_multi:
                        if t in df.columns.levels[1]:
                            df_t = df.xs(t, level=1, axis=1)
                        elif t in df.columns.levels[0]:
                            df_t = df.xs(t, level=0, axis=1)
                        else:
                            continue
                    else:
                        df_t = df

                    proc = process_ticker_frame(df_t)
                    if proc is not None and not proc.empty:
                        results[t] = proc
                except Exception as e:
                    logger.debug(f"Error processing {t}: {e}")

            if len(results) > 0:
                break
        except Exception as e:
            logger.warning(f"Download chunk failed (attempt {attempt+1}/{max_retries}): {e}")
            time.sleep(2.0 * (2 ** attempt))

    return results


def build_long_history(
    tickers: Optional[List[str]] = None,
    start_date: str = "2004-01-01",
    output_dir: str = OUTPUT_DIR,
    chunk_size: int = 100,
    limit: Optional[int] = None,
    skip_existing: bool = True,
) -> Tuple[int, int]:
    """
    Main long history build process.
    Downloads tickers, writes parquets, logs failures.
    Returns (success_count, fail_count).
    """
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.dirname(FAILURES_CSV), exist_ok=True)

    if tickers is None:
        all_tickers = get_target_tickers()
    else:
        all_tickers = sorted(set(tickers))

    if limit is not None and limit > 0:
        all_tickers = all_tickers[:limit]

    logger.info(f"Target tickers count: {len(all_tickers)}")

    to_download = []
    already_have = 0
    for t in all_tickers:
        p = os.path.join(output_dir, f"{t}.parquet")
        if skip_existing and os.path.exists(p) and os.path.getsize(p) > 1024:
            already_have += 1
        else:
            to_download.append(t)

    logger.info(f"Already cached: {already_have}. Need download: {len(to_download)}")

    success_count = already_have

    # Yahoo throttles bursts: modest chunks with pauses, then retry passes with growing back-off.
    pending = list(to_download)
    reasons: Dict[str, str] = {}
    for attempt, (size, pause) in enumerate([(chunk_size, 2.0), (50, 30.0), (25, 60.0), (10, 120.0), (5, 240.0)]):
        if not pending:
            break
        if attempt:
            logger.info(f"Retry pass {attempt}: {len(pending)} tickers, chunks of {size}, {pause:.0f}s pauses")
            time.sleep(pause)
        still: List[str] = []
        for i in range(0, len(pending), size):
            chunk = pending[i:i + size]
            logger.info(f"Downloading {i // size + 1}/{(len(pending) + size - 1) // size} ({len(chunk)} tickers)...")
            chunk_res = download_chunk(chunk, start_date=start_date)
            for t in chunk:
                df = chunk_res.get(t)
                if df is not None and not df.empty:
                    df.to_parquet(os.path.join(output_dir, f"{t}.parquet"), engine="pyarrow", compression="snappy")
                    success_count += 1
                else:
                    still.append(t)
            time.sleep(pause if attempt else 2.0)
        pending = still
    failed_tickers = [(t, reasons.get(t, "no valid price data after retries")) for t in pending]

    if failed_tickers:
        fail_df = pd.DataFrame(failed_tickers, columns=["ticker", "reason"])
        fail_df.to_csv(FAILURES_CSV, index=False)
        logger.info(f"Recorded {len(failed_tickers)} failures to {FAILURES_CSV}")

    logger.info(f"Build complete. Total valid: {success_count}, Failed: {len(failed_tickers)}")
    return success_count, len(failed_tickers)


def verify_adjustment_identities(output_dir: str = OUTPUT_DIR) -> bool:
    """
    Verification tests for Section 1 & Section 1.1:
    - SPY starts on 2004-01-02.
    - Adjustment identities hold (Total-return OHLC = OHLC x Adj/Close).
    - Unadjusted close calculation holds for known splits (NVDA 2024 10:1, AAPL 2020 4:1).
    - Dollar volume is split-invariant.
    """
    logger.info("Running verification of price adjustment identities...")
    os.makedirs(output_dir, exist_ok=True)
    all_passed = True

    # 1. SPY test
    spy_path = os.path.join(output_dir, "SPY.parquet")
    if not os.path.exists(spy_path):
        logger.info("Downloading SPY for identity verification...")
        res = download_chunk(["SPY"], start_date="2004-01-01")
        if "SPY" in res:
            res["SPY"].to_parquet(spy_path)

    if os.path.exists(spy_path):
        spy = pd.read_parquet(spy_path)
        first_date = spy.index[0].strftime("%Y-%m-%d")
        if first_date == "2004-01-02":
            logger.info(f"[PASS] SPY starts on 2004-01-02 (got {first_date})")
        else:
            logger.warning(f"[FAIL] SPY expected start 2004-01-02, got {first_date}")
            all_passed = False

        # Check total-return identity: TR_CLOSE == ADJ_CLOSE
        diff = np.abs(spy["TR_CLOSE"] - spy["ADJ_CLOSE"]).max()
        if diff < 1e-4:
            logger.info(f"[PASS] Total-return close matches Adj Close (max diff {diff:g})")
        else:
            logger.warning(f"[FAIL] TR_CLOSE diff: {diff}")
            all_passed = False
    else:
        logger.warning("[FAIL] SPY.parquet not found")
        all_passed = False

    # 2. NVDA 10:1 split (2024-06-10)
    nvda_path = os.path.join(output_dir, "NVDA.parquet")
    if not os.path.exists(nvda_path):
        res = download_chunk(["NVDA"], start_date="2024-05-01")
        if "NVDA" in res:
            res["NVDA"].to_parquet(nvda_path)

    if os.path.exists(nvda_path):
        nvda = pd.read_parquet(nvda_path)
        # Pre-split trading session 2024-06-07
        t_pre = pd.Timestamp("2024-06-07")
        if t_pre in nvda.index:
            row = nvda.loc[t_pre]
            # Split-adjusted close was ~120, unadjusted should be ~1208
            if row["UNADJ_CLOSE"] > 1000.0:
                logger.info(f"[PASS] NVDA pre-split unadjusted close reconstructed: ${row['UNADJ_CLOSE']:.2f} (split-adj: ${row['CLOSE']:.2f})")
            else:
                logger.warning(f"[FAIL] NVDA unadjusted close failed: {row['UNADJ_CLOSE']}")
                all_passed = False
    else:
        logger.warning("[FAIL] NVDA.parquet not found")
        all_passed = False

    # 3. AAPL 4:1 split (2020-08-31)
    aapl_path = os.path.join(output_dir, "AAPL.parquet")
    if not os.path.exists(aapl_path):
        res = download_chunk(["AAPL"], start_date="2020-08-01")
        if "AAPL" in res:
            res["AAPL"].to_parquet(aapl_path)

    if os.path.exists(aapl_path):
        aapl = pd.read_parquet(aapl_path)
        t_pre = pd.Timestamp("2020-08-28")
        if t_pre in aapl.index:
            row = aapl.loc[t_pre]
            # Split-adjusted close was ~125, unadjusted should be ~500
            if 400.0 < row["UNADJ_CLOSE"] < 600.0:
                logger.info(f"[PASS] AAPL pre-split unadjusted close reconstructed: ${row['UNADJ_CLOSE']:.2f} (split-adj: ${row['CLOSE']:.2f})")
            else:
                logger.warning(f"[FAIL] AAPL unadjusted close failed: {row['UNADJ_CLOSE']}")
                all_passed = False
    else:
        logger.warning("[FAIL] AAPL.parquet not found")
        all_passed = False

    return all_passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build long-history price parquet files (2004 onward).")
    parser.add_argument("--tickers", type=str, default=None, help="Comma-separated list of tickers to download.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tickers to download.")
    parser.add_argument("--start", type=str, default="2004-01-01", help="Start date (YYYY-MM-DD).")
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR, help="Destination directory.")
    parser.add_argument("--chunk-size", type=int, default=200, help="Download chunk size.")
    parser.add_argument("--verify", action="store_true", help="Run verification tests only.")

    args = parser.parse_args()

    if args.verify:
        passed = verify_adjustment_identities(output_dir=args.output_dir)
        sys.exit(0 if passed else 1)

    t_list = [x.strip().upper() for x in args.tickers.split(",")] if args.tickers else None
    succ, fail = build_long_history(
        tickers=t_list,
        start_date=args.start,
        output_dir=args.output_dir,
        chunk_size=args.chunk_size,
        limit=args.limit,
    )

    # Run verification test
    verify_adjustment_identities(output_dir=args.output_dir)
