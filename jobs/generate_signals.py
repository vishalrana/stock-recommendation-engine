"""
Generate Signals — Strategy 1.2
================================
Regime-aware, gated, percentile-normalized signal generator.

Flow:
  1. Detect market regime (SPY vs 200 DMA)
  2. Scan tickers with technical filters (trend, RSI, volume)
  3. Merge with historical backtest metrics from ticker_metrics
  4. Apply gated percentile-normalized ranking (SignalRanker)
  5. Archive previous signals to signals_history
  6. Clear and insert ranked signals
  7. Log results with regime metadata

Usage:
    python -m jobs.generate_signals
"""

import os
import sys
import glob
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
from regime import get_regime, should_trade
from ranker import SignalRanker
from jobs.supabase_client import get_client

# Ticker Blacklist
BLACKLIST = {"XYZ", "TEST", "PLACEHOLDER"}

# Strategy parameters (unchanged from 1.1 Beta)
RSI_PULLBACK_THRESHOLD = 45
RSI_RECOVERY_MIN = 45
RSI_RECOVERY_MAX = 65
VOLUME_MULTIPLIER = 1.0
TARGET_R_MULTIPLE = 3.0
LOOKBACK_RSI_DAYS = 10
SWING_LOW_LOOKBACK = 20

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
            headers={"User-Agent": "stock-recommendation-engine/1.2"},
            timeout=15,
        )
        response.raise_for_status()
        raw_table = pd.read_html(StringIO(response.text))[0]
        count = 0
        for _, row in raw_table.iterrows():
            ticker = str(row["Symbol"]).strip().upper().replace(".", "-")
            if ticker in BLACKLIST:
                continue
            tickers.append(ticker)
            company_names[ticker] = str(row["Security"]).strip()
            industries[ticker] = str(row["GICS Sub-Industry"]).strip()
            count += 1
            if count >= 100:
                break
        logger.info(f"Loaded {len(tickers)} tickers from Wikipedia (skipped blacklisted).")
    except Exception as e:
        logger.warning(f"Wikipedia fetch failed: {e}. Loading local fallback...")
        csv_path = os.path.join(PROJECT_ROOT, "outputs", "backtest_summary.csv")
        if os.path.exists(csv_path):
            try:
                summary_df = pd.read_csv(csv_path)
                for _, row in summary_df.iterrows():
                    ticker = str(row["ticker"]).strip().upper()
                    if ticker in BLACKLIST:
                        continue
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
) -> dict:
    """
    Evaluate Strategy 1.2 technical filters on the latest bar.

    Returns a dict with signal data if the ticker qualifies, or None.
    Score is NOT calculated here — that's done by SignalRanker after all
    signals are collected and metrics are merged.
    """
    n_bars = len(df)
    if n_bars < 201:
        return None

    t = n_bars - 1

    closes = df["CLOSE"].to_numpy(dtype=float)
    dma50s = df["DMA_50"].to_numpy(dtype=float)
    dma200s = df["DMA_200"].to_numpy(dtype=float)
    rsis = df["RSI_14"].to_numpy(dtype=float)
    volumes = df["VOLUME"].to_numpy(dtype=float)
    vol_mas = df["VOLUME_MA_20"].to_numpy(dtype=float)
    highs = df["HIGH"].to_numpy(dtype=float)
    dates = df.index

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
        "risk_reward": float(TARGET_R_MULTIPLE),
        "current_rsi": round(rsi_now, 2),
        "volume_ratio": volume_ratio,
    }


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
                "volume_ratio": sig.get("volume_ratio"),
                "score": sig.get("score"),
                "composite_score": sig.get("composite_score", sig.get("score", 0.0)),
                "tier_label": sig.get("tier_label", "Speculative"),
                "past_win_rate": m.get("win_rate", 0),
                "expectancy_pct": m.get("expectancy_pct", 0),
                "total_trades": m.get("total_trades", 0),
                "regime": regime_str,
            })

        supabase.table("signals_history").insert(history_rows).execute()
        logger.info("Archived %d signals to signals_history.", len(history_rows))

    except Exception as e:
        logger.error("Failed to archive signals to history: %s", e)


def main():
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("Strategy 1.2 — Regime-Aware Signal Generator")
    logger.info("=" * 60)

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

    # ── Step 2: Set gate thresholds based on regime ──────────
    if regime_str == "bull":
        min_expectancy = 0
        min_win_rate = 25
    else:
        min_expectancy = 2.0
        min_win_rate = 35
    min_trades = 5

    logger.info(
        "Gate thresholds: expectancy > %.1f, win_rate >= %.1f, trades >= %d",
        min_expectancy, min_win_rate, min_trades,
    )

    # ── Step 3: Load S&P 500 tickers ─────────────────────────
    tickers, company_names, industries = fetch_sp500_tickers_100()

    os.makedirs(os.path.join(PROJECT_ROOT, "data", "cache"), exist_ok=True)

    # ── Step 4: Download data if CI or cache empty ───────────
    is_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    force_download = os.environ.get("DOWNLOAD_DATA") == "true"
    cache_empty = len(glob.glob(os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet"))) == 0

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

    # ── Step 5: Scan tickers with technical filters ──────────
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
            "ticker, win_rate, expectancy_pct, total_signals"
        ).execute()
        for row in res.data:
            ticker = row["ticker"].upper()
            metrics_map[ticker] = {
                "win_rate": float(row["win_rate"] or 0),
                "expectancy_pct": float(row["expectancy_pct"] or 0),
                "total_trades": int(row["total_signals"] or 0),
            }
        logger.info(f"Loaded metrics for {len(metrics_map)} tickers.")
    except Exception as e:
        logger.warning(f"Could not load ticker metrics: {e}. Using fallback values.")

    # Technical scan
    raw_signals = []
    scanned_count = 0
    signal_date = None

    for idx, fpath in enumerate(parquet_files, 1):
        ticker = os.path.basename(fpath).replace(".parquet", "").upper()

        if tickers and ticker not in tickers:
            continue

        try:
            raw = pd.read_parquet(fpath, engine="pyarrow")
            df = calculate_indicators(raw).sort_index()
            scanned_count += 1

            if signal_date is None and len(df) > 0:
                latest_date = df.index[-1]
                if hasattr(latest_date, 'date'):
                    signal_date = latest_date.date().isoformat()
                else:
                    signal_date = str(latest_date)[:10]

            company_name = company_names.get(ticker, ticker)
            industry = industries.get(ticker, "Unknown")

            sig = check_latest_signal(ticker, df, company_name, industry)
            if sig is not None:
                raw_signals.append(sig)
                logger.info(
                    f"[QUALIFIED] {ticker} | Entry: ${sig['entry_price']:.2f} | "
                    f"Stop: ${sig['stop_loss']:.2f} | Exit: ${sig['exit_price']:.2f} | "
                    f"RSI: {sig['current_rsi']:.1f} | Vol: {sig['volume_ratio']:.2f}x"
                )

        except Exception as e:
            logger.error(f"Error scanning {ticker}: {e}")

    signals_qualified = len(raw_signals)
    logger.info(f"Technical scan complete. Scanned: {scanned_count}, Qualified: {signals_qualified}")

    if signal_date is None:
        logger.error("No valid scan date could be determined.")
        sys.exit(1)

    # ── Step 6: Merge with metrics and apply ranking ─────────
    signals_recommended = 0
    ranked_signals = []

    if raw_signals and trade_allowed:
        # Build DataFrame with metrics columns for the ranker
        signals_df = pd.DataFrame(raw_signals)
        signals_df["win_rate"] = signals_df["ticker"].apply(
            lambda t: metrics_map.get(t, {}).get("win_rate", 0)
        )
        signals_df["expectancy_pct"] = signals_df["ticker"].apply(
            lambda t: metrics_map.get(t, {}).get("expectancy_pct", 0)
        )
        signals_df["total_trades"] = signals_df["ticker"].apply(
            lambda t: metrics_map.get(t, {}).get("total_trades", 0)
        )

        logger.info("Applying composite ranking (Strategy 1.2 Rev B)...")
        ranker = SignalRanker()
        # Fetch all ranked candidates to log them
        scored_df = ranker.composite_rank(signals_df, regime_str, top_n=len(signals_df))

        if not scored_df.empty:
            # Log per-candidate
            for idx, row in scored_df.iterrows():
                breakdown = row["score_breakdown"]
                a = 0.40 * breakdown["momentum"]
                b = 0.30 * breakdown["expectancy"]
                c = 0.20 * breakdown["winrate"]
                d = 0.10 * breakdown["regime"]
                
                logger.info(
                    f"{row['ticker']} | Composite: {row['composite_score']:.1f} | Tier: {row['tier_label']} | "
                    f"Momentum: {a:.1f}/40, Expectancy: {b:.1f}/30, WinRate: {c:.1f}/20, Regime: {d:.1f}/10 | "
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
                    "volume_ratio": row["volume_ratio"],
                    "score": round(float(row["composite_score"]), 4),
                    "composite_score": round(float(row["composite_score"]), 4),
                    "tier_label": row["tier_label"],
                    "regime": regime_str,
                })
        else:
            logger.warning("No signals survived gates. 0 recommendations this scan.")
    elif not trade_allowed:
        logger.warning(
            "Bear market -- strategy inactive. No recommendations will be inserted."
        )
    else:
        logger.info("No technically qualified signals found.")

    # ── Step 7: Archive current signals before clearing ──────
    duration = round(time.time() - start_time, 2)
    status = "success"
    error_msg = None

    try:
        archive_current_signals(supabase, regime_str, metrics_map)

        # Clear signals
        logger.info("Clearing previous signals from Supabase...")
        supabase.table("signals").delete().neq("ticker", "").execute()
        logger.info("Previous signals cleared.")

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
        logger.error(f"Database update failed: {e}")

    # ── Step 8: Log to scan_log ──────────────────────────────
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
    }

    try:
        logger.info(f"Logging scan to scan_log: {scan_log_row}")
        supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
        logger.info("Scan log recorded successfully.")
    except Exception as e:
        logger.error(f"Failed to record scan log: {e}")
        sys.exit(1)

    if status == "failed":
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Strategy 1.2 signal generation complete.")
    logger.info(
        "Regime: %s | Scanned: %d | Qualified: %d | Recommended: %d | Duration: %.1fs",
        regime_str.upper(), scanned_count, signals_qualified, signals_recommended, duration,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
