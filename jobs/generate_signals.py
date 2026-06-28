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
from regime import get_regime
from jobs.supabase_client import get_client
from jobs.strategies import STRATEGIES

# Strategy activation by market regime
REGIME_STRATEGY_MAP = {
    "bull": [
        "Pullback Recovery",
        "Trend Following",
        "Sector Rotation",
        "Post-Earnings Drift",
        "52-Week High",
        "Cross-Sectional Momentum",
    ],
    "sideways": [
        "Pullback Recovery",
        "Mean Reversion",
        "Sector Rotation",
        "Post-Earnings Drift",
        "Cross-Sectional Momentum",
    ],
    "bear": [
        "Mean Reversion",
        "Post-Earnings Drift",
        # Defensive only: no trend following, no 52-week high, no cross-sectional
    ],
}

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


def load_etf_universe() -> list[str]:
    """Load sector ETF universe."""
    from jobs.strategies.sector_rotation import SECTOR_ETFS
    return list(SECTOR_ETFS.keys())


def run_cross_sectional_screen(universe: list[str], parquet_files: list[str]) -> list[tuple]:
    """Pre-screen: calculate 3-month returns for all tickers in universe, keep top 15%."""
    returns = []
    
    ticker_to_fpath = {}
    for fpath in parquet_files:
        t = os.path.basename(fpath).replace(".parquet", "").upper()
        ticker_to_fpath[t] = fpath

    for ticker in universe:
        fpath = ticker_to_fpath.get(ticker)
        if not fpath:
            continue
        try:
            raw = pd.read_parquet(fpath, engine="pyarrow")
            if len(raw) < 63:
                continue
            close_col = "CLOSE" if "CLOSE" in raw.columns else "Close"
            price = raw[close_col].iloc[-1]
            price_63d = raw[close_col].iloc[-63]
            ret = (price / price_63d - 1) * 100 if price_63d > 0 else 0
            returns.append((ticker, ret))
        except Exception:
            continue
    
    returns.sort(key=lambda x: x[1], reverse=True)
    top_15pct = max(1, int(len(returns) * 0.15))
    return returns[:top_15pct]


def load_metrics(ticker: str, metrics_map: dict, company_names: dict, industries: dict) -> dict:
    """Build per-ticker metrics dict for strategy scan."""
    m = metrics_map.get(ticker.upper(), {})
    wins = m.get("wins", 0)
    losses = m.get("losses", 0)
    return {
        "win_rate": m.get("win_rate", 0.0),
        "expectancy_pct": m.get("expectancy_pct", 0.0),
        "total_trades": m.get("total_signals", 0),
        "wins": wins,
        "losses": losses,
        "completed_trades": wins + losses,
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
                    "strategy_name": sig.get("strategy_name"),
                    "outcome": "open",
                    "context_score": sig.get("context_score", 0.0),
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
        "--force",
        dest="force_refresh",
        action="store_true",
        help="Delete all cached parquet files and re-download fresh data from yfinance for the full universe",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
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
    allowed_strategies = REGIME_STRATEGY_MAP.get(regime_str, ["Pullback Recovery"])

    logger.info(
        "REGIME: %s | SPY: $%.2f | 200 DMA: $%.2f",
        regime_str.upper(),
        regime_info["spy_price"],
        regime_info["spy_200dma"],
    )
    logger.info("Regime detected: %s", regime_str)
    logger.info("Active strategies: %s", ", ".join(allowed_strategies))

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

    from jobs.strategies.sector_rotation import SECTOR_ETFS
    etf_tickers = list(SECTOR_ETFS.keys())

    if is_ci or force_download or cache_empty:
        logger.info("Fetching fresh data from yfinance...")
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=500)

        all_download_tickers = list(dict.fromkeys(tickers + etf_tickers))

        for i, ticker in enumerate(all_download_tickers, 1):
            if ticker in BLACKLIST:
                continue
            logger.info("[%d/%d] Downloading %s...", i, len(all_download_tickers), ticker)
            df = fetch_ohlcv_data(ticker, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                df.to_parquet(os.path.join(cache_dir, f"{ticker}.parquet"), engine="pyarrow", index=True)
        logger.info("Finished downloading daily data.")

    # Ensure all Sector ETFs are present in the cache
    missing_etfs = [t for t in etf_tickers if not os.path.exists(os.path.join(cache_dir, f"{t}.parquet"))]
    if missing_etfs:
        logger.info("Downloading missing Sector ETFs: %s", missing_etfs)
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=500)
        for t in missing_etfs:
            df = fetch_ohlcv_data(t, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                df.to_parquet(os.path.join(cache_dir, f"{t}.parquet"), engine="pyarrow", index=True)

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
            "ticker, win_rate, expectancy_pct, total_signals, wins, losses, median_win_return"
        ).execute()
        for row in res.data:
            ticker = row["ticker"].upper()
            metrics_map[ticker] = {
                "win_rate": float(row["win_rate"] or 0),
                "expectancy_pct": float(row["expectancy_pct"] or 0),
                "total_signals": int(row["total_signals"] or 0),
                "wins": int(row.get("wins") or 0),
                "losses": int(row.get("losses") or 0),
                "median_win_return": float(row.get("median_win_return") or 0.0),
            }
        logger.info("Loaded metrics for %d tickers.", len(metrics_map))
    except Exception as e:
        logger.warning("Could not load ticker metrics: %s. Using fallback values.", e)

    all_signals: list[dict] = []
    strategy_counts: dict[str, int] = {}
    skipped_strategies: dict[str, str] = {}
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
    signals_blocked = 0

    for strategy in STRATEGIES:
        if strategy.name not in allowed_strategies:
            skipped_strategies[strategy.name] = regime_str
            strategy_counts[strategy.name] = 0
            logger.info(
                "[REGIME] Skipping %s — not active in %s regime",
                strategy.name,
                regime_str,
            )
            continue

        if hasattr(strategy, "reset_scan_stats"):
            strategy.reset_scan_stats()

        strategy_signals: list[dict] = []

        # Determine universe based on strategy type
        if strategy.name == 'Sector Rotation':
            current_universe = load_etf_universe()
        elif strategy.name == 'Cross-Sectional Momentum':
            screened_info = run_cross_sectional_screen(tickers, parquet_files)
            current_universe = [x[0] for x in screened_info]
        else:
            current_universe = tickers

        for idx, fpath in enumerate(parquet_files, 1):
            ticker = os.path.basename(fpath).replace(".parquet", "").upper()

            if current_universe and ticker not in current_universe:
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

        if strategy_signals:
            ranked = strategy.rank_candidates(strategy_signals, regime_str)
            buy_ranked = [s for s in ranked if s["tier_label"] in ("Strong Buy", "Buy")]
            strategy_counts[strategy.name] = len(buy_ranked)
            all_signals.extend(buy_ranked)

            if hasattr(strategy, "signals_blocked"):
                signals_blocked += strategy.signals_blocked
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

    if all_signals:
        final_signals = deduplicate_by_ticker(all_signals)
        final_signals.sort(key=lambda x: x.get('quality_score', x['composite_score']), reverse=True)
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

        # === Post-Ranking Context Safety Net ===
        # Ensure no final signals slip through without context_score
        from src.providers.context.aggregator import ContextAggregator
        from src.scorers.context_scorer import ContextScorer
        from src.ranker import SignalRanker
        
        context_aggregator = ContextAggregator()
        context_scorer = ContextScorer()
        ranker = SignalRanker()
        
        for sig in final_signals:
            if sig.get("context_score", 0.0) == 0.0 or sig.get("context_score") is None:
                logger.info(f"[CONTEXT FALLBACK] Computing context on-the-fly for {sig['ticker']}")
                try:
                    price_df = ranker._fetch_price_history(sig["ticker"])
                    if price_df is not None and not price_df.empty:
                        ctx = context_aggregator.get_aggregated(sig["ticker"], price_df)
                        tech_data = {
                            'rsi': sig.get("current_rsi", 50),
                            'adx': sig.get("adx_value", 20),
                            'volume_ratio': sig.get("volume_ratio", 1.0),
                        }
                        sig["context_score"] = context_scorer.calculate(ctx, float(sig["entry_price"]), tech_data)
                        logger.info(f"[CONTEXT FALLBACK] Computed context_score {sig['context_score']:.2f} for {sig['ticker']}")
                except Exception as e:
                    logger.warning(f"[CONTEXT FALLBACK] Failed for {sig['ticker']}: {e}")
                    sig["context_score"] = 0.0

        for sig in final_signals:
            entry_price = float(sig["entry_price"])
            t1_pct = float(sig.get("target_1_pct") or 0.0)
            t2_pct = float(sig.get("target_2_pct") or 0.0)
            t3_pct = float(sig.get("target_3_pct") or 0.0)

            target_1 = round(entry_price * (1 + t1_pct / 100), 2) if t1_pct else None
            target_2 = round(entry_price * (1 + t2_pct / 100), 2) if t2_pct else None
            target_3 = round(entry_price * (1 + t3_pct / 100), 2) if t3_pct else None

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
                    "target_1": target_1,
                    "target_2": target_2,
                    "target_3": target_3,
                    "target_1_pct": sig.get("target_1_pct"),
                    "target_2_pct": sig.get("target_2_pct"),
                    "target_3_pct": sig.get("target_3_pct"),
                    "weighted_rr": sig.get("weighted_rr"),
                    "position_sizing": sig.get("position_sizing", "50/30/20"),
                    "narrative": sig.get("narrative"),
                    "strategy_name": sig["strategy"],
                    "context_score": sig.get("context_score", 0.0),
                }
            )
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
        "signals_blocked": signals_blocked,
        "strategy_breakdown": strategy_counts,
        "active_strategies": len(allowed_strategies),
        "skipped_strategies": skipped_strategies,
    }

    if not args.dry_run:
        try:
            logger.info("Logging scan to scan_log: %s", scan_log_row)
            supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
            logger.info("Scan log recorded successfully.")
        except Exception as e:
            if "strategy_breakdown" in scan_log_row:
                logger.warning("Failed to record scan log with strategy_breakdown: %s. Retrying without it.", e)
                del scan_log_row["strategy_breakdown"]
                try:
                    supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
                    logger.info("Scan log recorded successfully (without strategy_breakdown).")
                except Exception as retry_err:
                    logger.warning(
                        "Failed to record scan log with optional regime activation fields: %s. "
                        "Retrying without active_strategies/skipped_strategies.",
                        retry_err,
                    )
                    scan_log_row.pop("active_strategies", None)
                    scan_log_row.pop("skipped_strategies", None)
                    try:
                        supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
                        logger.info("Scan log recorded successfully (without optional activation fields).")
                    except Exception as final_retry_err:
                        logger.error("Failed to record scan log on retry: %s", final_retry_err)
                        sys.exit(1)
            else:
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
