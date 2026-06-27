"""
Generate Signals — Strategy 1.3 Rev B (Modular Architecture)
=============================================================
Regime-aware, gated, percentile-normalized signal generator.

Flow:
  1. Detect market regime (SPY vs 200 DMA)
  2. Fetch S&P 500 + Nasdaq-100 universe (deduplicated)
  3. Scan tickers via registered strategies (Pullback Recovery)
  4. Merge with historical backtest metrics from ticker_metrics
  5. Apply gated percentile-normalized ranking (SignalRanker per strategy)
  6. Archive previous signals to signals_history via upsert (duplicate-safe)
  7. Clear and insert ranked signals
  8. Log results with regime metadata and gate rejection breakdown

Usage:
    python -m jobs.generate_signals [--dry-run]
"""

import os
import sys
import glob
import time
import logging
import argparse
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from indicators import calculate_indicators
from downloader import fetch_ohlcv_data
from regime import get_regime, should_trade
from jobs.supabase_client import get_client
from jobs.strategies import STRATEGIES

BLACKLIST = {"XYZ", "TEST", "PLACEHOLDER"}
TOP_N = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def load_universe() -> tuple[list, dict, dict]:
    """Load S&P 500 + Nasdaq-100 universe from Wikipedia (deduplicated)."""
    tickers: list[str] = []
    company_names: dict[str, str] = {}
    industries: dict[str, str] = {}
    sp500_set: set[str] = set()

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
        logger.info("S&P 500: loaded %d tickers from Wikipedia.", len(sp500_set))
    except Exception as e:
        logger.warning("S&P 500 Wikipedia fetch failed: %s. Loading local fallback...", e)
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
                logger.info("Loaded %d fallback tickers from local CSV.", len(tickers))
            except Exception as csv_err:
                logger.error("Could not load fallback CSV: %s", csv_err)

    sp500_count = len(sp500_set)
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
            logger.info("Nasdaq-100: added %d non-overlapping tickers.", ndx_unique_count)
        else:
            logger.warning("Nasdaq-100 Wikipedia table not found. Skipping NDX expansion.")
    except Exception as e:
        logger.warning("Nasdaq-100 Wikipedia fetch failed: %s. Skipping NDX expansion.", e)

    logger.info(
        "Universe: %d S&P 500 + %d Nasdaq-100 non-overlapping = %d total tickers",
        sp500_count,
        ndx_unique_count,
        len(tickers),
    )
    return tickers, company_names, industries


def load_metrics(ticker: str, metrics_map: dict, company_names: dict, industries: dict) -> dict:
    """Build per-ticker metrics dict for strategy scan."""
    m = metrics_map.get(ticker.upper(), {})
    return {
        "win_rate": m.get("win_rate", 0.0),
        "expectancy_pct": m.get("expectancy_pct", 0.0),
        "total_trades": m.get("total_trades", 0),
        "median_win_return": m.get("median_win_return", 0.0),
        "company_name": company_names.get(ticker, ticker),
        "industry": industries.get(ticker, "Unknown"),
    }


def deduplicate_by_ticker(signals: list[dict]) -> list[dict]:
    """Keep highest quality_score per ticker."""
    best: dict[str, dict] = {}
    for sig in signals:
        ticker = sig["ticker"]
        if ticker not in best or sig["quality_score"] > best[ticker]["quality_score"]:
            best[ticker] = sig
    return list(best.values())


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
            history_rows.append(
                {
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
                    "quality_score": sig.get("quality_score", sig.get("composite_score", 0.0)),
                    "tier_label": sig.get("tier_label", "Speculative"),
                    "strategy": sig.get("strategy"),
                    "past_win_rate": m.get("win_rate", 0),
                    "expectancy_pct": m.get("expectancy_pct", 0),
                    "total_trades": m.get("total_trades", 0),
                    "regime": regime_str,
                    "earnings_date": sig.get("earnings_date"),
                    "is_momentum_exception": sig.get("is_momentum_exception", False),
                    "distance_from_high_pct": sig.get("distance_from_high_pct"),
                    "target_1": sig.get("target_1"),
                    "target_2": sig.get("target_2"),
                    "target_3": sig.get("target_3"),
                    "target_1_pct": sig.get("target_1_pct"),
                    "target_2_pct": sig.get("target_2_pct"),
                    "target_3_pct": sig.get("target_3_pct"),
                    "weighted_rr": sig.get("weighted_rr"),
                    "position_sizing": sig.get("position_sizing", "50/30/20"),
                    "narrative": sig.get("narrative"),
                }
            )

        supabase.table("signals_history").upsert(
            history_rows,
            on_conflict="scan_date,ticker",
        ).execute()
        logger.info("Archived %d signals to signals_history (upsert, duplicates skipped).", len(history_rows))

    except Exception as e:
        logger.error("Failed to archive signals to history: %s", e)


def main():
    start_time = time.time()

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

    scan_date_today = datetime.now().date().isoformat()
    signal_date = scan_date_today

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

    tickers, company_names, industries = load_universe()

    os.makedirs(os.path.join(PROJECT_ROOT, "data", "cache"), exist_ok=True)

    is_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    force_download = os.environ.get("DOWNLOAD_DATA") == "true"
    cache_dir = os.path.join(PROJECT_ROOT, "data", "cache")
    cache_empty = len(glob.glob(os.path.join(cache_dir, "*.parquet"))) == 0

    if args.force_refresh:
        stale_files = glob.glob(os.path.join(cache_dir, "*.parquet"))
        logger.info("FORCE REFRESH: deleting %d stale parquet files...", len(stale_files))
        for sf in stale_files:
            try:
                os.remove(sf)
            except OSError as rm_err:
                logger.warning("Could not delete %s: %s", sf, rm_err)
        cache_empty = True

    if is_ci or force_download or cache_empty:
        logger.info("Fetching fresh data from yfinance...")
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=500)

        for i, ticker in enumerate(tickers, 1):
            if ticker in BLACKLIST:
                continue
            logger.info("[%d/%d] Downloading %s...", i, len(tickers), ticker)
            fetch_ohlcv_data(ticker, start_date=start_date, end_date=end_date)
        logger.info("Finished downloading daily data.")

    cache_path = os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet")
    parquet_files = sorted(glob.glob(cache_path))
    total_files = len(parquet_files)
    logger.info("Found %d cached files in data/cache/", total_files)

    if total_files == 0:
        logger.error("No cached files found. Cannot generate signals.")
        sys.exit(1)

    try:
        supabase = get_client()
    except Exception as e:
        logger.error("Failed to initialize Supabase client: %s", e)
        sys.exit(1)

    metrics_map: dict = {}
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
        logger.info("Loaded metrics for %d tickers.", len(metrics_map))
    except Exception as e:
        logger.warning("Could not load ticker metrics: %s. Using fallback values.", e)

    all_signals: list[dict] = []
    strategy_counts: dict[str, int] = {}
    scanned_count = 0
    signals_qualified = 0
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
        "momentum_exceptions": 0,
    }
    rsi_passed_count = 0
    signals_strong_buy = 0
    signals_buy = 0

    for strategy in STRATEGIES:
        if hasattr(strategy, "reset_scan_stats"):
            strategy.reset_scan_stats()

        strategy_signals: list[dict] = []

        for idx, fpath in enumerate(parquet_files, 1):
            ticker = os.path.basename(fpath).replace(".parquet", "").upper()

            if tickers and ticker not in tickers:
                continue

            try:
                raw = pd.read_parquet(fpath, engine="pyarrow")

                if len(raw) < 60:
                    logger.warning(
                        "%s: not enough history (%d bars) for stable ADX. Skipping.",
                        ticker,
                        len(raw),
                    )
                    gate_rejections["failed_adx_gate"] += 1
                    continue

                df = calculate_indicators(raw).sort_index()
                scanned_count += 1

                metrics = load_metrics(ticker, metrics_map, company_names, industries)
                signal = strategy.scan(ticker, df, regime_str, metrics)

                if signal is not None:
                    signals_qualified += 1
                    strategy_signals.append(signal)
                    logger.info(
                        "[QUALIFIED] %s | Entry: $%.2f | Stop: $%.2f | Exit: $%.2f | "
                        "RSI: %.1f | Vol: %.2fx | ADX: %.1f | MACD Hist: %.4f",
                        ticker,
                        signal["entry_price"],
                        signal["stop_loss"],
                        signal["exit_price"],
                        signal["current_rsi"],
                        signal["volume_ratio"],
                        signal["adx_value"],
                        signal["macd_histogram"],
                    )

            except Exception as e:
                logger.error("Error scanning %s: %s", ticker, e)

        if hasattr(strategy, "gate_rejections"):
            for key, count in strategy.gate_rejections.items():
                gate_rejections[key] = gate_rejections.get(key, 0) + count
        if hasattr(strategy, "rsi_passed_count"):
            rsi_passed_count += strategy.rsi_passed_count

        if trade_allowed and strategy_signals:
            ranked = strategy.rank_candidates(strategy_signals, regime_str)
            buy_ranked = [s for s in ranked if s["tier_label"] in ("Strong Buy", "Buy")]
            strategy_counts[strategy.name] = len(buy_ranked)
            all_signals.extend(buy_ranked)

            if hasattr(strategy, "signals_strong_buy"):
                signals_strong_buy += strategy.signals_strong_buy
            if hasattr(strategy, "signals_buy"):
                signals_buy += strategy.signals_buy
        else:
            strategy_counts[strategy.name] = 0

    logger.info(
        "Technical scan complete. Scanned: %d, Qualified: %d",
        scanned_count,
        signals_qualified,
    )

    ranked_signals: list[dict] = []
    error_msg = None

    if all_signals and trade_allowed:
        final_signals = deduplicate_by_ticker(all_signals)
        final_signals.sort(key=lambda x: x["quality_score"], reverse=True)
        final_signals = final_signals[:TOP_N]

        t1 = sum(1 for s in final_signals if s["tier_label"] == "Strong Buy")
        t2 = sum(1 for s in final_signals if s["tier_label"] == "Buy")
        t3 = sum(1 for s in final_signals if s["tier_label"] == "Watch")
        t4 = sum(1 for s in final_signals if s["tier_label"] == "Speculative")
        logger.info(
            "Final recommendations: %d signals (T1: %d, T2: %d, T3: %d, Speculative: %d)",
            len(final_signals),
            t1,
            t2,
            t3,
            t4,
        )

        for sig in final_signals:
            ranked_signals.append(
                {
                    "scan_date": sig["scan_date"],
                    "ticker": sig["ticker"],
                    "company_name": sig["company_name"],
                    "industry": sig["industry"],
                    "price": sig["price"],
                    "entry_price": sig["entry_price"],
                    "stop_loss": sig["stop_loss"],
                    "exit_price": sig["exit_price"],
                    "upside_pct": sig["upside_pct"],
                    "risk_reward": sig["risk_reward"],
                    "current_rsi": sig["current_rsi"],
                    "rsi_min_10d": sig.get("rsi_min_10d"),
                    "volume_ratio": sig["volume_ratio"],
                    "adx_value": sig["adx_value"],
                    "macd_histogram": sig["macd_histogram"],
                    "ema20": sig["ema20"],
                    "score": sig["composite_score"],
                    "composite_score": sig["composite_score"],
                    "quality_score": sig["quality_score"],
                    "tier_label": sig["tier_label"],
                    "strategy": sig["strategy"],
                    "regime": regime_str,
                    "is_fallback": bool(sig.get("is_fallback", False)),
                    "target_1": sig.get("target_1"),
                    "target_2": sig.get("target_2"),
                    "target_3": sig.get("target_3"),
                    "target_1_pct": sig.get("target_1_pct"),
                    "target_2_pct": sig.get("target_2_pct"),
                    "target_3_pct": sig.get("target_3_pct"),
                    "weighted_rr": sig.get("weighted_rr"),
                    "position_sizing": sig.get("position_sizing", "50/30/20"),
                    "narrative": sig.get("narrative"),
                }
            )
    elif not trade_allowed:
        logger.warning("Bear market -- strategy inactive. No recommendations will be inserted.")
    else:
        logger.info("No technically qualified signals found.")

    rsi_breadth_pct = round(100.0 * rsi_passed_count / scanned_count, 1) if scanned_count > 0 else 0.0
    signals_recommended = len(ranked_signals)
    if signals_recommended == 0:
        logger.info("No high-confidence setups tonight. Cash is a position.")

    duration = round(time.time() - start_time, 2)
    status = "success"

    if not args.dry_run:
        try:
            archive_current_signals(supabase, regime_str, metrics_map)
            logger.info("Clearing previous signals from Supabase...")
            supabase.table("signals").delete().neq("ticker", "").execute()
            logger.info("Previous signals cleared.")
        except Exception as e:
            logger.error("Failed to clear/archive signals: %s", e)
            error_msg = f"Archive/Clear failed: {e}"

        try:
            if ranked_signals:
                logger.info("Inserting %d ranked signals...", len(ranked_signals))
                supabase.table("signals").insert(ranked_signals).execute()
                logger.info("Signals inserted successfully.")
            else:
                logger.info("No signals to insert.")
        except Exception as e:
            status = "failed"
            error_msg = str(e)
            logger.error("Database insertion failed: %s", e)
    else:
        logger.info("[DRY RUN] Skipped archiving, clearing, and inserting signals.")

    logger.info(
        "RSI breadth: %d/%d tickers passed RSI gate (%.1f%%)",
        rsi_passed_count,
        scanned_count,
        rsi_breadth_pct,
    )

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
        "momentum_exceptions": gate_rejections["momentum_exceptions"],
        "rsi_breadth_pct": rsi_breadth_pct,
        "signals_strong_buy": signals_strong_buy,
        "signals_buy": signals_buy,
        "strategy_breakdown": strategy_counts,
    }

    if not args.dry_run:
        try:
            logger.info("Logging scan to scan_log: %s", scan_log_row)
            supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
            logger.info("Scan log recorded successfully.")
        except Exception as e:
            logger.error("Failed to record scan log: %s", e)
            sys.exit(1)
    else:
        logger.info("[DRY RUN] Skipped logging scan to scan_log. Row: %s", scan_log_row)

    if status == "failed":
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Strategy 1.3 Rev B signal generation complete.")
    logger.info(
        "Regime: %s | Scanned: %d | Qualified: %d | Recommended: %d | Duration: %.1fs",
        regime_str.upper(),
        scanned_count,
        signals_qualified,
        signals_recommended,
        duration,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
