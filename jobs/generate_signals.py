"""
Generate Signals — Strategy 1.3 Rev B
======================================
Regime-aware, gated, percentile-normalized signal generator.

Flow:
  1. Detect market regime (SPY vs 200 DMA)
  2. Fetch S&P 500 + Nasdaq-100 universe (deduplicated)
  3. Scan tickers with technical filters (trend, RSI, ADX, MACD, volume, risk/reward, trades floor)
     - BULL regime: trend gate relaxed to price > 50 SMA only (Rev B)
  4. Merge with historical backtest metrics from ticker_metrics
  5. Apply gated percentile-normalized ranking (SignalRanker)
  6. Extended Bull Fallback: if signals_recommended < 3 and rsi_breadth < 25%, re-scan with looser RSI/ADX
  7. Archive previous signals to signals_history via upsert (duplicate-safe)
  8. Clear and insert ranked signals (is_fallback tagged where applicable)
  9. Log results with regime metadata and gate rejection breakdown

Usage:
    python -m jobs.generate_signals [--dry-run]

Database prerequisites (run once in Supabase SQL Editor):
  -- Unique constraint to prevent duplicate archive rows:
  ALTER TABLE signals_history
    ADD CONSTRAINT signals_history_scan_date_ticker_key UNIQUE (scan_date, ticker);

  -- is_fallback column on both tables:
  ALTER TABLE signals ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN DEFAULT FALSE;
  ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN DEFAULT FALSE;

  -- RSI breadth column on scan_log:
  ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS rsi_breadth_pct NUMERIC;

  -- Max Risk Gate column on scan_log:
  ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_maxrisk_gate INT DEFAULT 0;

  -- Min Risk Gate column on scan_log:
  ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_minrisk_gate INT DEFAULT 0;

  -- Max Gap Gate column on scan_log:
  ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_maxgap_gate INT DEFAULT 0;

  -- Earnings Gate column on scan_log:
  ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_earnings_gate INT DEFAULT 0;
"""

import os
import sys
import glob
import time
import logging
import argparse
from datetime import datetime, timedelta
from io import StringIO

import numpy as np
import pandas as pd
import requests

# Add project root and src to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from indicators import calculate_indicators, check_rsi_pullback_recovery
from downloader import fetch_ohlcv_data
from regime import get_regime, should_trade
from ranker import SignalRanker
from jobs.supabase_client import get_client

# Ticker Blacklist
BLACKLIST = {"XYZ", "TEST", "PLACEHOLDER"}

# Strategy parameters — Strategy 1.3 Rev B
RSI_PULLBACK_THRESHOLD = 52.0  # Raised: 45→48→50→52 (extended bull, deep RSI dips are rare)
RSI_RECOVERY_MIN = 45.0
RSI_RECOVERY_MAX = 67.0        # Captures extended momentum in bull regime
ADX_MIN = 18.0                 # Lowered from 20 — reduces ADX gate rejections
VOLUME_MULTIPLIER = 1.0
TARGET_R_MULTIPLE = 3.0
LOOKBACK_RSI_DAYS = 10
SWING_LOW_LOOKBACK = 20

# Extended Bull Fallback parameters (triggered when primary scan yields < 3 signals in bull)
FALLBACK_RSI_PULLBACK_THRESHOLD = 57.0
FALLBACK_ADX_MIN = 15.0
FALLBACK_TRIGGER_THRESHOLD = 3    # min recommended signals before fallback fires
FALLBACK_RSI_BREADTH_MAX = 25.0   # only trigger fallback if RSI breadth is also low

# Ranking
TOP_N = 5

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


def fetch_sp500_tickers_all() -> tuple[list, dict, dict]:
    """Fetch S&P 500 + Nasdaq-100 universe from Wikipedia (deduplicated).

    Returns:
        tickers: ordered list of unique ticker symbols
        company_names: ticker -> company name
        industries: ticker -> GICS sub-industry
    """
    tickers: list[str] = []
    company_names: dict[str, str] = {}
    industries: dict[str, str] = {}
    sp500_set: set[str] = set()

    # ── S&P 500 ──────────────────────────────────────────────
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
        logger.warning(f"S&P 500 Wikipedia fetch failed: {e}. Loading local fallback...")
        csv_path = os.path.join(PROJECT_ROOT, "outputs", "backtest_summary.csv")
        if os.path.exists(csv_path):
            try:
                summary_df = pd.read_csv(csv_path)
                for _, row in summary_df.iterrows():
                    ticker = str(row["ticker"]).strip().upper()
                    if ticker in BLACKLIST:
                        continue
                    tickers.append(ticker)
                    sp500_set.add(ticker)
                    industries[ticker] = str(row["industry"]).strip()
                    company_names[ticker] = ticker
                logger.info(f"Loaded {len(tickers)} fallback tickers from local CSV.")
            except Exception as csv_err:
                logger.error(f"Could not load fallback CSV: {csv_err}")

    sp500_count = len(sp500_set)

    # ── Nasdaq-100 (non-overlapping with S&P 500) ─────────────
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
        # The Nasdaq-100 Wikipedia page has multiple tables; the components table
        # contains a "Ticker" column. Try each table until one has that column.
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
                    continue  # skip duplicates already in S&P 500
                tickers.append(ticker)
                company_names[ticker] = str(row.get("Company", ticker)).strip()
                industries[ticker] = str(row.get("GICS Sector", "Unknown")).strip()
                ndx_unique_count += 1
            logger.info(f"Nasdaq-100: added {ndx_unique_count} non-overlapping tickers.")
        else:
            logger.warning("Nasdaq-100 Wikipedia table not found (no 'Ticker' column). Skipping NDX expansion.")
    except Exception as e:
        logger.warning(f"Nasdaq-100 Wikipedia fetch failed: {e}. Skipping NDX expansion.")

    total = len(tickers)
    logger.info(
        f"Universe: {sp500_count} S&P 500 + {ndx_unique_count} Nasdaq-100 non-overlapping = {total} total tickers"
    )
    return tickers, company_names, industries


def compute_atr14(df: pd.DataFrame) -> float:
    """Compute 14-day Average True Range."""
    high_low = df['HIGH'] - df['LOW']
    high_close = abs(df['HIGH'] - df['CLOSE'].shift())
    low_close = abs(df['LOW'] - df['CLOSE'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return float(tr.rolling(14).mean().iloc[-1])


def get_earnings_date(ticker: str) -> str | None:
    """Fetch next earnings date from yfinance for a ticker, returning ISO string or None."""
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        calendar = stock.calendar
        if calendar is None:
            return None
        
        # If it is a dictionary
        if isinstance(calendar, dict):
            dates = calendar.get("Earnings Date")
            if dates and isinstance(dates, list) and len(dates) > 0:
                import pandas as pd
                return pd.Timestamp(dates[0]).strftime("%Y-%m-%d")
            return None
            
        # If it is a pandas DataFrame
        if hasattr(calendar, "empty") and not calendar.empty:
            if hasattr(calendar, "index") and len(calendar.index) > 0:
                import pandas as pd
                return pd.Timestamp(calendar.index[0]).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def check_latest_signal(
    ticker: str,
    df: pd.DataFrame,
    company_name: str,
    industry: str,
    total_trades: int,
    regime_str: str = "neutral",
    rsi_threshold_override: float | None = None,
    adx_min_override: float | None = None,
    median_win_return: float = 0.0,
) -> tuple[dict, str]:
    """
    Evaluate Strategy 1.3 Rev B technical filters on the latest bar.

    Args:
        regime_str: current market regime ("bull", "bear", "sideways").
        rsi_threshold_override: if set, replaces RSI_PULLBACK_THRESHOLD (used in fallback scan).
        adx_min_override: if set, replaces ADX_MIN (used in fallback scan).

    Returns a tuple (signal_data, failed_gate_name).
    If the stock passes all gates, failed_gate_name is None.
    """
    effective_rsi_threshold = rsi_threshold_override if rsi_threshold_override is not None else RSI_PULLBACK_THRESHOLD
    
    if adx_min_override is not None:
        effective_adx_min = adx_min_override
    else:
        effective_adx_min = 15.0 if regime_str.lower() == "bull" else 18.0

    n_bars = len(df)
    if n_bars < 201:
        return None, "failed_trend_gate"

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
    macd_hists = df["MACD_HIST"].to_numpy(dtype=float)
    ema20s = df["EMA_20"].to_numpy(dtype=float)
    dates = df.index

    c = closes[t]
    d50 = dma50s[t]
    d200 = dma200s[t]
    rsi_now = rsis[t]
    vol = volumes[t]
    vma = vol_mas[t]
    adx_now = adxs[t]
    macd_line = macd_lines[t]
    macd_sig = macd_sigs[t]
    macd_hist = macd_hists[t]
    ema20 = ema20s[t]

    if any(np.isnan(x) for x in (c, d50, d200, rsi_now, vol, vma, adx_now, macd_line, macd_sig, macd_hist, ema20)):
        return None, "failed_trend_gate"

    # 1. Trend alignment
    # Strategy 1.3 Rev B: trend gate relaxed in BULL regime to 50 SMA only.
    # In bear/sideways regimes the full 50 DMA > 200 DMA stack is still required.
    if regime_str == "bull":
        if not (c > d50):
            return None, "failed_trend_gate"
    else:
        if not (c > d50 > d200):
            return None, "failed_trend_gate"

    # 2. RSI pullback-recovery
    rsi_res = check_rsi_pullback_recovery(
        df["RSI_14"],
        lookback=LOOKBACK_RSI_DAYS,
        dip_threshold=effective_rsi_threshold,
        recovery_min=RSI_RECOVERY_MIN,
        recovery_max=RSI_RECOVERY_MAX
    )
    if not rsi_res.get("passed"):
        return None, "failed_rsi_gate"
    rsi_min_10d = rsi_res.get("rsi_min_10d")

    # 3. ADX trend strength
    if np.isnan(adx_now) or not (adx_now >= effective_adx_min):
        return None, "failed_adx_gate"

    # 5. Volume confirmation: volume_ratio >= 1.0x
    volume_ratio = round(vol / vma, 2) if vma > 0 else 0.0
    if not (volume_ratio >= VOLUME_MULTIPLIER):
        return None, "failed_volume_gate"

    # 6. Swing Low Stop-loss & Max Risk % check (reject if stop-loss is missing/invalid or risk > 15% of entry price)
    stop_loss = find_swing_low(df)
    if stop_loss is None:
        return None, "failed_maxrisk_gate"

    entry_price = round(highs[t] * 1.001, 2)
    if stop_loss >= entry_price:
        return None, "failed_maxrisk_gate"

    risk = entry_price - stop_loss
    if risk <= 0:
        return None, "failed_maxrisk_gate"

    if (entry_price - stop_loss) / entry_price > 0.15:
        return None, "failed_maxrisk_gate"

    # Strategy 1.3 Rev B: Min Risk % gate (avoid noise-level stops)
    risk_pct = (entry_price - stop_loss) / entry_price * 100
    MIN_RISK_PCT = 2.5
    if risk_pct < MIN_RISK_PCT:
        return None, "failed_minrisk_gate"

    # Strategy 1.3 Rev B: Max Gap % gate (avoid flash crashes / dead cat bounces)
    MAX_GAP_PCT = 5.0
    daily_returns = df['CLOSE'].pct_change().iloc[-5:]
    max_drop = daily_returns.min() * 100
    if max_drop < -MAX_GAP_PCT:
        return None, "failed_maxgap_gate"

    if median_win_return and median_win_return > 0:
        # 15% buffer above historical median winning return, min 5%, max 20%
        target_pct = min(max(median_win_return * 1.15, 5.0), 20.0)
    else:
        # ATR fallback for stocks with no winning trade history
        atr14 = compute_atr14(df)
        target_pct = min((2.5 * atr14 / c) * 100, 20.0)

    exit_price = round(entry_price * (1 + target_pct / 100), 2)
    risk_reward = round(target_pct / ((entry_price - stop_loss) / entry_price * 100), 2)

    # 7. Historical data floor: total_trades >= 10 in ticker_metrics
    if total_trades < 10:
        return None, "failed_trades_gate"

    # Strategy 1.3 Rev B: Earnings Calendar filter (avoid reporting during expected hold)
    EARNINGS_BUFFER_DAYS = 7
    earnings_date = get_earnings_date(ticker)
    if earnings_date:
        ts_earnings = pd.Timestamp(earnings_date).normalize()
        ts_now = pd.Timestamp.now().normalize()
        days_to_earnings = (ts_earnings - ts_now).days
        if 0 < days_to_earnings <= EARNINGS_BUFFER_DAYS:
            return None, "failed_earnings_gate"

    latest_date = dates[t]
    if hasattr(latest_date, 'date'):
        signal_date = latest_date.date().isoformat()
    else:
        signal_date = str(latest_date)[:10]

    upside_pct = round(((exit_price - entry_price) / entry_price) * 100.0, 2)

    return {
        "scan_date": signal_date,
        "ticker": ticker,
        "company_name": company_name,
        "industry": industry,
        "price": round(c, 2),
        "dma_50": round(d50, 2),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "exit_price": exit_price,
        "upside_pct": upside_pct,
        "risk_reward": risk_reward,
        "current_rsi": round(rsi_now, 2),
        "rsi_min_10d": round(float(rsi_min_10d), 2),
        "volume_ratio": volume_ratio,
        "adx_value": round(float(adx_now), 2),
        "macd_histogram": round(float(macd_hist), 4),
        "ema20": round(float(ema20), 2),
        "earnings_date": earnings_date,
    }, None


def archive_current_signals(supabase, regime_str: str, metrics_map: dict):
    """Archive current signals to signals_history before clearing."""
    try:
        res = supabase.table("signals").select("*").execute()
        current_signals = res.data or []

        if not current_signals:
            logger.info("No existing signals to archive.")
            return

        history_rows = []
        for sig in current_signals:
            ticker = sig.get("ticker", "")
            m = metrics_map.get(ticker.upper(), {})
            history_rows.append({
                "scan_date": sig.get("scan_date"),
                "ticker": ticker,
                "company_name": sig.get("company_name"),
                "industry": sig.get("industry"),
                "price": sig.get("price"),
                "entry_price": sig.get("entry_price"),
                "stop_loss": sig.get("stop_loss"),
                "exit_price": sig.get("exit_price"),
                "upside_pct": sig.get("upside_pct"),
                "risk_reward": sig.get("risk_reward"),
                "current_rsi": sig.get("current_rsi"),
                "rsi_min_10d": sig.get("rsi_min_10d"),
                "volume_ratio": sig.get("volume_ratio"),
                "adx_value": sig.get("adx_value"),
                "macd_histogram": sig.get("macd_histogram"),
                "ema20": sig.get("ema20"),
                "score": sig.get("score"),
                "composite_score": sig.get("composite_score", sig.get("score", 0.0)),
                "tier_label": sig.get("tier_label", "Speculative"),
                "past_win_rate": m.get("win_rate", 0),
                "expectancy_pct": m.get("expectancy_pct", 0),
                "total_trades": m.get("total_trades", 0),
                "regime": regime_str,
                "earnings_date": sig.get("earnings_date"),
            })

        # Upsert instead of insert to safely handle retries / duplicate archive calls.
        # Requires: ALTER TABLE signals_history ADD CONSTRAINT signals_history_scan_date_ticker_key UNIQUE (scan_date, ticker);
        supabase.table("signals_history").upsert(
            history_rows,
            on_conflict="scan_date,ticker"
        ).execute()
        logger.info("Archived %d signals to signals_history (upsert, duplicates skipped).", len(history_rows))

    except Exception as e:
        logger.error("Failed to archive signals to history: %s", e)


def main():
    start_time = time.time()
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Generate Nightly Stock Signals")
    parser.add_argument("--dry-run", action="store_true", help="Run scan logic without writing to database")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Delete all cached parquet files and re-download fresh data from yfinance for the full universe",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Strategy 1.3 Rev B — Regime-Aware Signal Generator")
    if args.dry_run:
        logger.info("DRY RUN ACTIVE — database writes will be skipped")
    if args.force_refresh:
        logger.info("FORCE REFRESH ACTIVE — cache will be cleared and data re-downloaded")
    logger.info("=" * 60)

    # scan_date is always today's date (not derived from stale cache)
    scan_date_today = datetime.now().date().isoformat()

    # Initialize gate rejection counts
    gate_rejections = {
        "failed_rsi_gate": 0,
        "failed_adx_gate": 0,
        "failed_trend_gate": 0,
        "failed_volume_gate": 0,
        "failed_maxrisk_gate": 0,
        "failed_minrisk_gate": 0,
        "failed_maxgap_gate": 0,
        "failed_earnings_gate": 0,
        "failed_trades_gate": 0,
    }

    # ── Step 1: Detect market regime ─────────────────────────
    regime_info = get_regime()
    regime_str = regime_info["regime"]
    trade_allowed = should_trade(regime_str, "swing_momentum")

    logger.info(
        "REGIME: %s | SPY: $%.2f | 200 DMA: $%.2f | Trade allowed: %s",
        regime_str.upper(),
        regime_info["spy_price"],
        regime_info["spy_200dma"],
        trade_allowed,
    )

    # ── Step 2: Load S&P 500 + Nasdaq-100 universe ───────────
    tickers, company_names, industries = fetch_sp500_tickers_all()

    os.makedirs(os.path.join(PROJECT_ROOT, "data", "cache"), exist_ok=True)

    # ── Step 3: Download data if CI, force-refresh, env flag, or cache empty ─
    is_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    force_download = os.environ.get("DOWNLOAD_DATA") == "true"
    cache_dir = os.path.join(PROJECT_ROOT, "data", "cache")
    cache_empty = len(glob.glob(os.path.join(cache_dir, "*.parquet"))) == 0

    if args.force_refresh:
        # Delete all stale parquet files so the scan uses only fresh data
        stale_files = glob.glob(os.path.join(cache_dir, "*.parquet"))
        logger.info("FORCE REFRESH: deleting %d stale parquet files...", len(stale_files))
        for sf in stale_files:
            try:
                os.remove(sf)
            except OSError as rm_err:
                logger.warning("Could not delete %s: %s", sf, rm_err)
        cache_empty = True  # trigger download below

    if is_ci or force_download or cache_empty:
        logger.info("Fetching fresh data from yfinance...")
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=500)

        for i, ticker in enumerate(tickers, 1):
            if ticker in BLACKLIST:
                continue
            logger.info(f"[{i}/{len(tickers)}] Downloading {ticker}...")
            fetch_ohlcv_data(ticker, start_date=start_date, end_date=end_date)
        logger.info("Finished downloading daily data.")

    # ── Step 4: Scan tickers with technical filters ──────────
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

    # Fetch historical metrics
    metrics_map = {}
    try:
        logger.info("Fetching historical metrics from Supabase ticker_metrics...")
        res = supabase.table("ticker_metrics").select(
            "ticker, win_rate, expectancy_pct, total_signals, median_win_return"
        ).execute()
        for row in res.data:
            ticker = row["ticker"].upper()
            metrics_map[ticker] = {
                "win_rate": float(row["win_rate"] or 0),
                "expectancy_pct": float(row["expectancy_pct"] or 0),
                "total_trades": int(row["total_signals"] or 0),
                "median_win_return": float(row.get("median_win_return") or 0.0),
            }
        logger.info(f"Loaded metrics for {len(metrics_map)} tickers.")
    except Exception as e:
        logger.warning(f"Could not load ticker metrics: {e}. Using fallback values.")

    # Technical scan
    raw_signals = []
    scanned_count = 0
    rsi_passed_count = 0  # tracks how many tickers passed the RSI gate (for breadth monitoring)
    # scan_date is always today — not the latest parquet bar (which may be stale)
    signal_date = scan_date_today

    for idx, fpath in enumerate(parquet_files, 1):
        ticker = os.path.basename(fpath).replace(".parquet", "").upper()

        if tickers and ticker not in tickers:
            continue

        try:
            raw = pd.read_parquet(fpath, engine="pyarrow")
            
            # Guard for short history (minimum 60 bars for stable ADX)
            if len(raw) < 60:
                logger.warning(f"{ticker}: not enough history ({len(raw)} bars) for stable ADX. Skipping.")
                gate_rejections["failed_adx_gate"] += 1
                continue

            df = calculate_indicators(raw).sort_index()
            scanned_count += 1

            company_name = company_names.get(ticker, ticker)
            industry = industries.get(ticker, "Unknown")
            
            # Fetch total trades for this ticker (historical_data_floor gate)
            total_trades = metrics_map.get(ticker, {}).get("total_trades", 0)
            median_win_return = metrics_map.get(ticker, {}).get("median_win_return", 0.0)

            sig, failed_gate = check_latest_signal(
                ticker, df, company_name, industry, total_trades,
                regime_str=regime_str,
                median_win_return=median_win_return
            )
            if sig is not None:
                sig["is_fallback"] = False
                raw_signals.append(sig)
                rsi_passed_count += 1
                logger.info(
                    f"[QUALIFIED] {ticker} | Entry: ${sig['entry_price']:.2f} | "
                    f"Stop: ${sig['stop_loss']:.2f} | Exit: ${sig['exit_price']:.2f} | "
                    f"RSI: {sig['current_rsi']:.1f} | Vol: {sig['volume_ratio']:.2f}x | "
                    f"ADX: {sig['adx_value']:.1f} | MACD Hist: {sig['macd_histogram']:.4f}"
                )
            else:
                if failed_gate in gate_rejections:
                    gate_rejections[failed_gate] += 1
                # Count RSI passes (tickers that passed trend gate but may have failed later)
                if failed_gate not in ("failed_trend_gate", "failed_rsi_gate"):
                    rsi_passed_count += 1

        except Exception as e:
            logger.error(f"Error scanning {ticker}: {e}")

    signals_qualified = len(raw_signals)
    logger.info(f"Technical scan complete. Scanned: {scanned_count}, Qualified: {signals_qualified}")

    # signal_date is always set (scan_date_today), so this guard is a safety net only
    if signal_date is None:
        logger.error("No valid scan date could be determined.")
        sys.exit(1)

    # ── Step 5: Merge with metrics and apply ranking ─────────
    signals_recommended = 0
    ranked_signals = []
    error_msg = None

    if raw_signals and trade_allowed:
        # Build DataFrame with metrics columns for the ranker
        signals_df = pd.DataFrame(raw_signals)
        signals_df["win_rate"] = signals_df["ticker"].apply(
            lambda t: metrics_map.get(t, {}).get("win_rate", 0.0)
        )
        signals_df["expectancy_pct"] = signals_df["ticker"].apply(
            lambda t: metrics_map.get(t, {}).get("expectancy_pct", 0.0)
        )
        signals_df["total_trades"] = signals_df["ticker"].apply(
            lambda t: metrics_map.get(t, {}).get("total_trades", 0)
        )

        logger.info("Applying composite ranking (Strategy 1.3 Rev B)...")
        ranker = SignalRanker()
        scored_df = ranker.composite_rank(signals_df, regime_str, top_n=len(signals_df))

        if not scored_df.empty:
            # Log per-candidate
            for idx, row in scored_df.iterrows():
                breakdown = row["score_breakdown"]
                a = 0.30 * breakdown["momentum"]
                b = 0.40 * breakdown["expectancy"]
                c = 0.20 * breakdown["winrate"]
                d = 0.10 * breakdown["regime"]
                
                logger.info(
                    f"{row['ticker']} | Composite: {row['composite_score']:.1f} | Tier: {row['tier_label']} | "
                    f"Momentum: {a:.1f}/30, Expectancy: {b:.1f}/40, WinRate: {c:.1f}/20, Regime: {d:.1f}/10 | "
                    f"Raw: exp={row['expectancy_pct']:.2f}%, win={row['win_rate']:.1f}%, trades={row['total_trades']}"
                )

            # Select top recommendations
            ranked_df = scored_df.head(TOP_N)
            signals_recommended = len(ranked_df)

            # Log summary
            t1 = sum(ranked_df["tier_label"] == "Strong Buy")
            t2 = sum(ranked_df["tier_label"] == "Buy")
            t3 = sum(ranked_df["tier_label"] == "Watch")
            t4 = sum(ranked_df["tier_label"] == "Speculative")
            logger.info(
                f"Final recommendations: {signals_recommended} signals "
                f"(T1: {t1}, T2: {t2}, T3: {t3}, Speculative: {t4})"
            )

            for _, row in ranked_df.iterrows():
                ranked_signals.append({
                    "scan_date": row["scan_date"],
                    "ticker": row["ticker"],
                    "company_name": row["company_name"],
                    "industry": row["industry"],
                    "price": row["price"],
                    "entry_price": row["entry_price"],
                    "stop_loss": row["stop_loss"],
                    "exit_price": row["exit_price"],
                    "upside_pct": row["upside_pct"],
                    "risk_reward": row["risk_reward"],
                    "current_rsi": row["current_rsi"],
                    "rsi_min_10d": row["rsi_min_10d"],
                    "volume_ratio": row["volume_ratio"],
                    "adx_value": row["adx_value"],
                    "macd_histogram": row["macd_histogram"],
                    "ema20": row["ema20"],
                    "score": round(float(row["composite_score"]), 4),
                    "composite_score": round(float(row["composite_score"]), 4),
                    "tier_label": row["tier_label"],
                    "regime": regime_str,
                    "is_fallback": bool(row.get("is_fallback", False)),
                })
        else:
            logger.warning("No signals survived gates. 0 recommendations this scan.")
    elif not trade_allowed:
        logger.warning(
            "Bear market -- strategy inactive. No recommendations will be inserted."
        )
    else:
        logger.info("No technically qualified signals found.")

    # ── Step 6: Extended Bull Fallback ───────────────────────
    # Trigger when: bull regime + low primary signals + low RSI breadth.
    rsi_breadth_pct_primary = round(100.0 * rsi_passed_count / scanned_count, 1) if scanned_count > 0 else 0.0
    fallback_signals: list[dict] = []
    fallback_triggered = False

    if (
        trade_allowed
        and regime_str == "bull"
        and signals_recommended < FALLBACK_TRIGGER_THRESHOLD
        and rsi_breadth_pct_primary < FALLBACK_RSI_BREADTH_MAX
    ):
        fallback_triggered = True
        logger.warning(
            "Extended Bull Fallback triggered — primary scan yielded %d signals, "
            "re-running with relaxed RSI=%.1f/ADX=%.1f",
            signals_recommended, FALLBACK_RSI_PULLBACK_THRESHOLD, FALLBACK_ADX_MIN,
        )

        # Tickers already picked up by primary scan
        primary_tickers = {s["ticker"] for s in ranked_signals}

        for fpath in sorted(glob.glob(os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet"))):
            f_ticker = os.path.basename(fpath).replace(".parquet", "").upper()
            if tickers and f_ticker not in tickers:
                continue
            if f_ticker in primary_tickers:
                continue  # primary result takes priority
            try:
                raw = pd.read_parquet(fpath, engine="pyarrow")
                if len(raw) < 60:
                    continue
                df_f = calculate_indicators(raw).sort_index()
                company_name = company_names.get(f_ticker, f_ticker)
                industry = industries.get(f_ticker, "Unknown")
                total_trades = metrics_map.get(f_ticker, {}).get("total_trades", 0)
                median_win_return = metrics_map.get(f_ticker, {}).get("median_win_return", 0.0)
                sig, _ = check_latest_signal(
                    f_ticker, df_f, company_name, industry, total_trades,
                    regime_str=regime_str,
                    rsi_threshold_override=FALLBACK_RSI_PULLBACK_THRESHOLD,
                    adx_min_override=FALLBACK_ADX_MIN,
                    median_win_return=median_win_return,
                )
                if sig is not None:
                    sig["is_fallback"] = True
                    sig["tier_label"] = "Watch"  # fallback signals capped at Watch
                    fallback_signals.append(sig)
                    logger.info(
                        f"[FALLBACK QUALIFIED] {f_ticker} | RSI: {sig['current_rsi']:.1f} | "
                        f"ADX: {sig['adx_value']:.1f}"
                    )
            except Exception as e:
                logger.error(f"Error in fallback scan for {f_ticker}: {e}")

        logger.info("Fallback scan complete: %d additional candidates found.", len(fallback_signals))

        # Merge fallback candidates: rank them and fill up to TOP_N
        if fallback_signals:
            fb_df = pd.DataFrame(fallback_signals)
            fb_df["win_rate"] = fb_df["ticker"].apply(lambda t: metrics_map.get(t, {}).get("win_rate", 0.0))
            fb_df["expectancy_pct"] = fb_df["ticker"].apply(lambda t: metrics_map.get(t, {}).get("expectancy_pct", 0.0))
            fb_df["total_trades"] = fb_df["ticker"].apply(lambda t: metrics_map.get(t, {}).get("total_trades", 0))
            ranker_fb = SignalRanker()
            fb_scored = ranker_fb.composite_rank(fb_df, regime_str, top_n=len(fb_df))
            slots_remaining = TOP_N - signals_recommended
            for _, row in fb_scored.head(slots_remaining).iterrows():
                ranked_signals.append({
                    "scan_date": row["scan_date"],
                    "ticker": row["ticker"],
                    "company_name": row["company_name"],
                    "industry": row["industry"],
                    "price": row["price"],
                    "entry_price": row["entry_price"],
                    "stop_loss": row["stop_loss"],
                    "exit_price": row["exit_price"],
                    "upside_pct": row["upside_pct"],
                    "risk_reward": row["risk_reward"],
                    "current_rsi": row["current_rsi"],
                    "rsi_min_10d": row["rsi_min_10d"],
                    "volume_ratio": row["volume_ratio"],
                    "adx_value": row["adx_value"],
                    "macd_histogram": row["macd_histogram"],
                    "ema20": row["ema20"],
                    "score": round(float(row["composite_score"]), 4),
                    "composite_score": round(float(row["composite_score"]), 4),
                    "tier_label": "Watch",  # capped regardless of composite score
                    "regime": regime_str,
                    "is_fallback": True,
                })
            signals_recommended = len(ranked_signals)
            logger.info(
                "After fallback merge: %d total recommendations (%d primary + %d fallback).",
                signals_recommended,
                signals_recommended - len(fallback_signals[:slots_remaining]),
                min(len(fallback_signals), slots_remaining),
            )

    # Final recommendations check
    signals_recommended = len(ranked_signals)
    if signals_recommended == 0:
        logger.info("No high-confidence setups tonight. Cash is a position.")

    # Auto-relax warning check — dynamic message referencing current thresholds
    if signals_recommended < 3 and trade_allowed:
        next_threshold = RSI_PULLBACK_THRESHOLD + 2
        warn_parts = [
            f"WARN: Low signal count ({signals_recommended}) after",
            "fallback scan" if fallback_triggered else "primary scan",
            f"— RSI_PULLBACK_THRESHOLD={RSI_PULLBACK_THRESHOLD},",
            f"consider raising to {next_threshold};",
            f"ADX_MIN={15.0 if regime_str.lower() == 'bull' else 18.0}, RSI_RECOVERY_MAX={RSI_RECOVERY_MAX}",
        ]
        error_msg = " ".join(warn_parts)
        logger.warning(error_msg)

    # ── Step 6: Archive and update signals ──────────────────
    duration = round(time.time() - start_time, 2)
    status = "success"

    if not args.dry_run:
        # Separate archiving and clearing from insertion to prevent silent swallowing or orphaned deletes
        try:
            archive_current_signals(supabase, regime_str, metrics_map)
            logger.info("Clearing previous signals from Supabase...")
            supabase.table("signals").delete().neq("ticker", "").execute()
            logger.info("Previous signals cleared.")
        except Exception as e:
            logger.error(f"Failed to clear/archive signals: {e}")
            error_msg = f"Archive/Clear failed: {e}"

        try:
            # Insert ranked signals
            if ranked_signals:
                logger.info(f"Inserting {len(ranked_signals)} ranked signals...")
                supabase.table("signals").insert(ranked_signals).execute()
                logger.info("Signals inserted successfully.")
            else:
                logger.info("No signals to insert.")
        except Exception as e:
            status = "failed"
            error_msg = str(e)
            logger.error(f"Database insertion failed: {e}")
    else:
        logger.info("[DRY RUN] Skipped archiving, clearing, and inserting signals.")

    # ── Step 7: Log to scan_log ──────────────────────────────
    rsi_breadth_pct = rsi_breadth_pct_primary  # already computed above
    logger.info(f"RSI breadth: {rsi_passed_count}/{scanned_count} tickers passed RSI gate ({rsi_breadth_pct}%)")

    scan_log_row = {
        "scan_date": signal_date,
        "tickers_scanned": scanned_count,
        "signals_generated": signals_qualified,
        "signals_qualified": signals_qualified,
        "signals_recommended": signals_recommended,
        "scan_duration_secs": duration,
        "status": status,
        "error_message": error_msg,
        "regime": regime_str,
        "failed_rsi_gate": gate_rejections["failed_rsi_gate"],
        "failed_adx_gate": gate_rejections["failed_adx_gate"],
        "failed_trend_gate": gate_rejections["failed_trend_gate"],
        "failed_volume_gate": gate_rejections["failed_volume_gate"],
        "failed_maxrisk_gate": gate_rejections["failed_maxrisk_gate"],
        "failed_minrisk_gate": gate_rejections["failed_minrisk_gate"],
        "failed_maxgap_gate": gate_rejections["failed_maxgap_gate"],
        "failed_earnings_gate": gate_rejections["failed_earnings_gate"],
        "failed_trades_gate": gate_rejections["failed_trades_gate"],
        "rsi_breadth_pct": rsi_breadth_pct,
    }

    if not args.dry_run:
        try:
            logger.info(f"Logging scan to scan_log: {scan_log_row}")
            supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
            logger.info("Scan log recorded successfully.")
        except Exception as e:
            logger.error(f"Failed to record scan log: {e}")
            sys.exit(1)
    else:
        logger.info(f"[DRY RUN] Skipped logging scan to scan_log. Row: {scan_log_row}")

    if status == "failed":
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Strategy 1.3 Rev B signal generation complete.")
    logger.info(
        "Regime: %s | Scanned: %d | Qualified: %d | Recommended: %d | Fallback: %s | Duration: %.1fs",
        regime_str.upper(), scanned_count, signals_qualified, signals_recommended,
        "YES" if fallback_triggered else "NO", duration,
    )
    logger.info("="  * 60)


if __name__ == "__main__":
    main()
