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
from src.data.cache_manager import get_cache_manager
from src.utils.metrics_cache import load_cached_metrics, save_cached_metrics
from src.strategies.target_calculator import calculate_targets
from src.filters.earnings_filter import fetch_earnings_calendar, earnings_risk_filter
from src.filters.survivorship_bias import compute_reach_prob_with_survivorship


def get_cache_mode(args) -> str:
    """Determine cache refresh mode based on CLI flags, env vars, and environment.

    Priority chain:
      1. --force-refresh flag  → "force"
      2. --cache-mode CLI arg  → whatever the user typed
      3. CACHE_MODE env var    → whatever is set
      4. GITHUB_ACTIONS=true   → "incremental"
      5. default               → "local"
    """
    if args.force_refresh:
        return "force"
    if hasattr(args, "cache_mode") and args.cache_mode:
        return args.cache_mode
    env_mode = os.environ.get("CACHE_MODE", "").lower()
    if env_mode in ("local", "incremental", "force"):
        return env_mode
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "incremental"
    return "local"

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
TOP_N = 3

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


def run_cross_sectional_screen(universe: list[str], cache_manager) -> list[tuple]:
    """Pre-screen: calculate 3-month returns for all tickers in universe, keep top 15%."""
    returns = []
    
    # Calculate returns over last 120 days to ensure 63 trading days are covered
    end_date_str = datetime.now().date().isoformat()
    start_date_str = (datetime.now().date() - timedelta(days=120)).isoformat()

    for ticker in universe:
        try:
            raw = cache_manager.get_ticker_history(ticker, start_date_str, end_date_str)
            if raw is None or len(raw) < 63:
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


def refresh_active_signals_prices(supabase):
    """
    Refresh current market prices on active recommendations.
    Pure recommendation engine behavior: updates market quotes without simulated trades,
    selling shares, or updating portfolio P&L.
    """
    try:
        from jobs.supabase_client import get_latest_bar, update_signals_price
        
        res = supabase.table("signals").select("id, ticker, status").in_("status", ["open", "pending"]).execute()
        current_signals = res.data or []
        
        if not current_signals:
            logger.info("No active recommendations in database to refresh.")
            return
            
        logger.info("Refreshing market prices for %d active recommendations...", len(current_signals))
        for existing in current_signals:
            ticker = existing["ticker"]
            bar = get_latest_bar(ticker)
            if bar and "close" in bar:
                update_signals_price(ticker, float(bar["close"]))
                logger.info(f"[PRICE REFRESH] {ticker}: updated to ${float(bar['close']):.2f}")
    except Exception as e:
        logger.warning("Could not refresh active signals prices: %s", e)


def get_next_trading_day(date_obj):
    """Return the next trading day (skip weekends)."""
    next_day = date_obj + timedelta(days=1)
    while next_day.weekday() >= 5:  # Saturday=5, Sunday=6
        next_day += timedelta(days=1)
    return next_day


VIX_EMERGENCY_THRESHOLD = 40

def apply_vix_override(regime, strategies, size_mult):
    import yfinance as yf
    try:
        vix_ticker = yf.Ticker("^VIX")
        vix_history = vix_ticker.history(period="1d")
        if not vix_history.empty:
            vix = float(vix_history["Close"].iloc[-1])
            if vix > VIX_EMERGENCY_THRESHOLD:
                regime = "bear"
                strategies = ["Mean Reversion", "Post-Earnings Drift"]
                size_mult = 0.5
                logger.info(f"[VIX OVERRIDE] VIX={vix:.1f} > 40 — forced bear, half sizing")
            else:
                logger.info(f"[VIX] VIX level: {vix:.1f} (Normal)")
        else:
            logger.warning("VIX history empty, skipping VIX override check.")
    except Exception as e:
        logger.warning(f"Failed to fetch VIX info: {e}")
    return regime, strategies, size_mult


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
    parser.add_argument(
        "--cache-mode",
        choices=["local", "incremental", "force"],
        default=None,
        help="Override automatic cache mode detection (local=no downloads, incremental=missing days only, force=full re-download)",
    )
    args = parser.parse_args()
    
    # If dry-run is requested, set SKIP_NLP=true to bypass model loading entirely
    if args.dry_run:
        os.environ["SKIP_NLP"] = "true"

    cache_mode = get_cache_mode(args)

    logger.info("=" * 60)
    logger.info("Strategy 1.3 Rev B — Regime-Aware Signal Generator")
    if args.dry_run:
        logger.info("DRY RUN ACTIVE — database writes will be skipped")
    logger.info("Cache mode: %s", cache_mode.upper())
    logger.info("=" * 60)

    scan_date_today = datetime.now().date().isoformat()
    signal_date = scan_date_today

    regime_info = get_regime()
    sma_regime = regime_info["regime"]
    
    use_hmm = os.environ.get("USE_HMM", "false").lower() == "true"
    if use_hmm:
        try:
            from src.hmm_regime import RollingHMM
            hmm = RollingHMM()
            hmm_regime = hmm.get_regime()
            logger.info(f"[REGIME COMPARISON] HMM={hmm_regime}, SMA={sma_regime}")
            regime_str = hmm_regime
        except Exception as e:
            logger.warning(f"Failed to calculate HMM regime: {e}. Falling back to SMA.")
            regime_str = sma_regime
    else:
        regime_str = sma_regime

    allowed_strategies = REGIME_STRATEGY_MAP.get(regime_str, ["Pullback Recovery"])

    # TASK 2: VIX Emergency Override
    size_mult = 1.0
    regime_str, allowed_strategies, size_mult = apply_vix_override(regime_str, allowed_strategies, size_mult)

    logger.info(
        "REGIME: %s | SPY: $%.2f | 200 DMA: $%.2f",
        regime_str.upper(),
        regime_info["spy_price"],
        regime_info["spy_200dma"],
    )
    logger.info("Regime detected: %s", regime_str)
    logger.info("Active strategies: %s", ", ".join(allowed_strategies))

    tickers, company_names, industries = load_universe()

    # ── Cache Mode Detection ──────────────────────────────────────────
    cache_manager = get_cache_manager()
    by_date_dir = os.path.join(PROJECT_ROOT, "data", "cache", "by_date")

    from jobs.strategies.sector_rotation import SECTOR_ETFS
    etf_tickers = list(SECTOR_ETFS.keys())
    all_download_tickers = list(dict.fromkeys(tickers + etf_tickers))
    all_download_tickers = [t for t in all_download_tickers if t not in BLACKLIST]

    # ── Cache Refresh (mode-aware) ────────────────────────────────────
    t_download_start = time.time()
    end_date_dt = datetime.now().date()

    if cache_mode == "force":
        logger.info("FORCE: Clearing all cache and re-downloading full history...")
        cache_manager.clear_all()
        # Also clean up any legacy per-ticker files
        for lf in glob.glob(os.path.join(PROJECT_ROOT, "data", "cache", "*.parquet")):
            try:
                os.remove(lf)
            except Exception:
                pass
        start_date_dt = end_date_dt - timedelta(days=500)
        cache_manager.refresh_cache(all_download_tickers, start_date_dt.isoformat(), end_date_dt.isoformat())
        logger.info(f"Force refresh completed in {time.time() - t_download_start:.1f}s")

    elif cache_mode == "incremental":
        last_cached = cache_manager.get_last_cached_date()
        if last_cached:
            start_date_dt = last_cached + timedelta(days=1)
            logger.info(f"INCREMENTAL: Downloading from {start_date_dt} to {end_date_dt} (last cached: {last_cached})")
        else:
            start_date_dt = end_date_dt - timedelta(days=500)
            logger.info(f"INCREMENTAL: No cache found. Full download from {start_date_dt} to {end_date_dt}")
        if start_date_dt <= end_date_dt:
            cache_manager.refresh_cache(all_download_tickers, start_date_dt.isoformat(), end_date_dt.isoformat())
        else:
            logger.info("INCREMENTAL: Cache already covers today. No download needed.")
        logger.info(f"Incremental refresh completed in {time.time() - t_download_start:.1f}s")

    else:  # local
        if cache_manager.is_stale(max_age_trading_days=2):
            last_cached = cache_manager.get_last_cached_date()
            if last_cached:
                start_date_dt = last_cached + timedelta(days=1)
                logger.info(f"LOCAL: Cache stale (last: {last_cached}). Downloading {start_date_dt} to {end_date_dt}...")
            else:
                start_date_dt = end_date_dt - timedelta(days=500)
                logger.info(f"LOCAL: No cache found. Full download from {start_date_dt}...")
            cache_manager.refresh_cache(all_download_tickers, start_date_dt.isoformat(), end_date_dt.isoformat())
            logger.info(f"Local refresh completed in {time.time() - t_download_start:.1f}s")
        else:
            logger.info("LOCAL: Cache is fresh (last: %s). Skipping downloads.", cache_manager.get_last_cached_date())

    daily_files = glob.glob(os.path.join(by_date_dir, "*.parquet"))
    total_files = len(daily_files)
    logger.info("Found %d date-partitioned daily files in data/cache/by_date", total_files)

    if total_files == 0:
        logger.error("No cached daily files found. Cannot generate signals.")
        sys.exit(1)

    # ── Supabase Client ───────────────────────────────────────────────
    try:
        supabase = get_client()
    except Exception as e:
        logger.error("Failed to initialize Supabase client: %s", e)
        sys.exit(1)

    # ── Earnings Calendar Preload (Cached Daily) ─────────────────────
    earnings_calendar_cache = {}
    try:
        earnings_calendar_cache = fetch_earnings_calendar(tickers, supabase=supabase)
        logger.info(f"[EARNINGS CALENDAR] Loaded {len(earnings_calendar_cache)} ticker schedules")
    except Exception as ec_err:
        logger.warning(f"Could not load earnings calendar: {ec_err}")

    # Recommendation engine operates independently of portfolio equity / simulated cash state
    risk_multiplier = 1.0

    # ── Ticker Metrics (with local cache for zero-network local mode) ─
    metrics_map: dict = {}
    if cache_mode == "local":
        metrics_map = load_cached_metrics() or {}

    if not metrics_map:
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
            save_cached_metrics(metrics_map)
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

    # Preload the entire daily cache history into memory once for all tickers to maximize speed!
    preload_end_str = datetime.now().date().isoformat()
    preload_start_str = (datetime.now().date() - timedelta(days=500)).isoformat()
    cache_manager.preload_history(preload_start_str, preload_end_str)

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
            screened_info = run_cross_sectional_screen(tickers, cache_manager)
            current_universe = [x[0] for x in screened_info]
        else:
            current_universe = tickers

        t_strat_start = time.time()
        for idx, ticker in enumerate(current_universe, 1):
            ticker = ticker.upper()
            if ticker in BLACKLIST:
                continue

            try:
                raw = cache_manager.get_ticker_history(ticker, preload_start_str, preload_end_str)
                if raw is None or raw.empty:
                    continue

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
                    # ponytail: carry ATR into signal for hybrid exit calc later
                    signal["atr_14"] = float(df["ATR_14"].iloc[-1]) if "ATR_14" in df.columns else 0.0
                    signals_qualified += 1
                    strategy_signals.append(signal)
                    logger.debug(
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

        logger.info(
            "%s: Scanned %d tickers, found %d qualified signals in %.2fs",
            strategy.name,
            len(current_universe),
            len(strategy_signals),
            time.time() - t_strat_start
        )

        if hasattr(strategy, "gate_rejections"):
            for key, count in strategy.gate_rejections.items():
                gate_rejections[key] = gate_rejections.get(key, 0) + count
        if hasattr(strategy, "rsi_passed_count"):
            rsi_passed_count += strategy.rsi_passed_count

        if strategy_signals:
            strategy_counts[strategy.name] = len(strategy_signals)
            all_signals.extend(strategy_signals)
            if hasattr(strategy, "signals_blocked"):
                signals_blocked += strategy.signals_blocked
        else:
            strategy_counts[strategy.name] = 0

    logger.info(
        "Technical scan complete. Scanned: %d, Qualified: %d",
        scanned_count,
        signals_qualified,
    )

    ranked_signals: list[dict] = []
    error_msg = None
    rejected_signals_to_insert = []

    if all_signals:
        # P0-1 & P0-2: Central SignalRanker is single source of truth
        candidates = deduplicate_by_ticker(all_signals)
        logger.info(f"[CENTRAL RANKER] Processing {len(candidates)} candidates from all strategies...")

        from src.ranker import (
            SignalRanker,
            compute_momentum_score,
            compute_expectancy_score,
            compute_regime_alignment,
            validate_candidate_features,
        )
        from src.providers.context.aggregator import ContextAggregator
        from src.scorers.context_scorer import ContextScorer

        ranker = SignalRanker()
        context_aggregator = ContextAggregator()
        context_scorer = ContextScorer()
        skip_nlp = os.getenv("SKIP_NLP", "false").lower() == "true"

        # P0-2: Calculate technical momentum, win rate, expectancy, and regime scores
        for sig in candidates:
            # 1. Technical Momentum Score (from real indicator values)
            try:
                sig["momentum_score"] = compute_momentum_score(sig)
            except Exception as m_err:
                logger.warning(f"Could not compute momentum score for {sig.get('ticker')}: {m_err}")
                sig["momentum_score"] = None

            # 2. Historical Win Rate Score (from ticker metrics)
            t_upper = sig["ticker"].upper()
            w_val = sig.get("past_win_rate")
            if w_val is None and t_upper in metrics_map:
                w_val = metrics_map[t_upper].get("win_rate")
            if w_val is not None:
                sig["winrate_score"] = float(w_val)
                sig["win_rate"] = float(w_val)
                sig["past_win_rate"] = float(w_val)
            else:
                sig["winrate_score"] = None

            # 3. Strategy Expectancy Score
            strat_name = sig.get("strategy", "Trend Following")
            sig["expectancy_score"] = compute_expectancy_score(strat_name)

            # 4. Continuous Regime Score
            sig["regime_score"] = compute_regime_alignment(strat_name, regime_str)

        # Context Scoring with breakdown (P0-2)
        if not skip_nlp and candidates:
            logger.info(f"Computing context scoring for {len(candidates)} candidates in parallel...")
            def _score_ctx(cand):
                t = cand["ticker"]
                try:
                    price_df = ranker._fetch_price_history(t)
                    if price_df is not None and not price_df.empty:
                        ctx = context_aggregator.get_aggregated(t, price_df)
                        tech_data = {
                            'rsi': cand.get("current_rsi", 50),
                            'adx': cand.get("adx_value", 20),
                            'volume_ratio': cand.get("volume_ratio", 1.0),
                        }
                        c_score, c_analyst, c_earnings, c_fundamental, c_news = context_scorer.calculate_with_breakdown(
                            ctx, float(cand.get("entry_price") or cand.get("price", 1.0)), tech_data
                        )
                        if ctx.cached_score is None:
                            from src.providers.context.aggregator import save_context_to_cache
                            save_context_to_cache(t, c_score, ctx)
                        return (t, c_score, c_analyst, c_earnings, c_fundamental, c_news, getattr(ctx, "de_ratio", None), getattr(ctx, "current_ratio", None), getattr(ctx, "earnings_surprise_pct", None), getattr(ctx, "finbert_sentiment", None), getattr(ctx, "target_consensus", None))
                except Exception as ctx_err:
                    logger.warning(f"Context scoring failed for {t}: {ctx_err}")
                return (t, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, None, None, None)

            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(_score_ctx, c): c for c in candidates}
                ctx_map = {}
                try:
                    for future in as_completed(futures, timeout=60.0):
                        res_data = future.result(timeout=10.0)
                        ctx_map[res_data[0]] = res_data[1:]
                except Exception as batch_err:
                    logger.warning(f"Parallel context batch timeout: {batch_err}")

            for c in candidates:
                t = c["ticker"]
                if t in ctx_map:
                    c["context_score"], c["context_analyst"], c["context_earnings"], c["context_fundamental"], c["context_news"], c["de_ratio"], c["current_ratio"], c["earnings_surprise_pct"], c["finbert_sentiment"], c["target_consensus"] = ctx_map[t]
                else:
                    c["context_score"] = 0.0
                    c["context_analyst"] = 0.0
                    c["context_earnings"] = 0.0
                    c["context_fundamental"] = 0.0
                    c["context_news"] = 0.0
        else:
            for c in candidates:
                c["context_score"] = 0.0
                c["context_analyst"] = 0.0
                c["context_earnings"] = 0.0
                c["context_fundamental"] = 0.0
                c["context_news"] = 0.0

        # P0-2: Central Composite Scoring with strict validation
        scored_candidates = []
        for sig in candidates:
            is_valid_feat, feat_msg = validate_candidate_features(sig)
            if not is_valid_feat:
                logger.warning(f"[FEATURE VALIDATION FAIL] Dropping {sig.get('ticker')}: {feat_msg}")
                sig["status"] = "rejected"
                sig["rejection_reason"] = f"Validation failed: {feat_msg}"
                sig["allocated_dollars"] = 0.0
                sig["exact_shares"] = 0.0
                sig["max_shares"] = 0
                sig["position_sizing"] = "K: 0.0%"
                rejected_signals_to_insert.append(sig)
                continue

            try:
                res = ranker.compute_composite_score(sig, regime_str)
                sig["composite_score"] = round(res["total"], 4)
                sig["quality_score"] = round(res["total"], 4)
                sig["score_breakdown"] = res["breakdown"]
                scored_candidates.append(sig)
            except Exception as score_err:
                logger.warning(f"Scoring error for {sig.get('ticker')}: {score_err}")
                sig["status"] = "rejected"
                sig["rejection_reason"] = f"Scoring error: {score_err}"
                sig["allocated_dollars"] = 0.0
                sig["exact_shares"] = 0.0
                sig["max_shares"] = 0
                sig["position_sizing"] = "K: 0.0%"
                rejected_signals_to_insert.append(sig)

        # Sort ALL valid candidates by composite score DESC
        # P0-3: DO NOT TRUNCATE TO TOP_N HERE!
        scored_candidates.sort(key=lambda x: float(x.get('composite_score', 0.0)), reverse=True)
        final_signals = scored_candidates
        logger.info(f"Central ranking complete: {len(final_signals)} candidates scored.")

        # ponytail: Hybrid exit architecture — short-term keeps ATR-scaled T1/T2/T3,
        # trend/momentum strategies get None targets + trailing stop
        SHORT_TERM_STRATEGIES = {'Pullback Recovery', 'Mean Reversion', 'Post-Earnings Drift'}
        TREND_STRATEGIES = {'Trend Following', 'Sector Rotation', '52-Week High', '52-Week High Breakout', 'Cross-Sectional Momentum'}

        # Fetch active open recommendations from Supabase to prevent duplicate active recommendations
        open_positions = []
        open_tickers = []
        try:
            res_open = supabase.table("signals").select("ticker, status").in_("status", ["open", "pending"]).execute()
            open_positions = res_open.data or []
            open_tickers = [row['ticker'].upper() for row in open_positions]
            logger.info(f"[RECOMMENDATIONS] Found {len(open_tickers)} existing active recommendations in database.")
        except Exception as e:
            logger.warning("Failed to fetch active recommendations from Supabase: %s", e)

        # Pre-calculate trade setups, risk analytics, and tier qualification
        earnings_rejected_count = 0
        reach_rejected_count = 0
        rejected_signals_to_insert = []
        qualified_recommendations = []

        for sig in final_signals:
            ticker = sig["ticker"]
            if ticker.upper() in open_tickers:
                logger.info(f"Ticker {ticker} is already an active recommendation. Skipping duplicate insertion.")
                continue

            entry_price = float(sig["entry_price"])
            stop_loss = float(sig["stop_loss"])
            strategy_name = sig["strategy"]

            score = float(sig.get("composite_score", sig.get("score", 0.0)))

            # 0. Earnings Date Risk Filter
            from datetime import datetime as dt_cls
            scan_dt = dt_cls.strptime(sig["scan_date"], "%Y-%m-%d").date() if isinstance(sig["scan_date"], str) else sig["scan_date"]
            er_res = earnings_risk_filter(
                ticker=ticker,
                scan_date=scan_dt,
                strategy=strategy_name,
                earnings_calendar=earnings_calendar_cache,
            )
            sig["next_earnings_date"] = er_res.get("next_earnings_date")
            sig["days_to_earnings"] = er_res.get("days_to_earnings")

            if not er_res.get("pass", True):
                sig["status"] = "rejected"
                sig["earnings_rejected"] = True
                sig["rejection_reason"] = er_res.get("reason", "Earnings blackout")
                sig["allocated_dollars"] = 0.0
                sig["exact_shares"] = 0.0
                sig["max_shares"] = 0
                sig["position_sizing"] = "N/A - Earnings Risk"
                earnings_rejected_count += 1
                logger.info(f"[EARNINGS RISK GATE] Dropping {ticker} ({strategy_name}): {sig['rejection_reason']}")
                rejected_signals_to_insert.append(sig)
                continue
            else:
                sig["earnings_rejected"] = False

            atr = float(sig.get("atr_14", 0.0))

            # 1. Enforce Hard Stop-Loss Risk Ceiling (Max 7.0% Max Loss)
            min_stop = round(entry_price * 0.93, 2)
            if stop_loss < min_stop:
                stop_loss = min_stop
                sig["stop_loss"] = stop_loss

            # Minimum stop distance floor: ensure at least 4.0% buffer
            max_tight_stop = round(entry_price * 0.96, 2)
            if stop_loss > max_tight_stop:
                logger.info(f"[STOP FLOOR] {sig['ticker']}: widening tight stop from ${stop_loss} to ${max_tight_stop} (4.0% minimum)")
                stop_loss = max_tight_stop
                sig['stop_loss'] = stop_loss

            # 2. Strategy-Specific ATR Targets with Survivorship Bias Mitigation
            ticker_df = None
            if cache_manager:
                try:
                    ticker_df = cache_manager.get_ticker_history(ticker, preload_start_str, preload_end_str)
                except Exception as e:
                    logger.debug("Could not fetch ticker history for %s: %s", ticker, e)

            calc_res = calculate_targets(
                ticker=ticker,
                entry_price=entry_price,
                atr_14=atr,
                stop_loss=stop_loss,
                strategy_name=strategy_name,
                price_df=ticker_df,
                sector=sig.get("sector") or sig.get("industry"),
            )

            if not calc_res.is_valid:
                logger.info(f"[REACH PROB FILTER] Dropping {ticker} ({strategy_name}): {calc_res.rejection_reason}")
                reach_rejected_count += 1
                sig["status"] = "rejected"
                sig["rejection_reason"] = calc_res.rejection_reason
                sig["allocated_dollars"] = 0.0
                sig["exact_shares"] = 0.0
                sig["max_shares"] = 0
                sig["position_sizing"] = "N/A - Invalid Setup"
                sig["reach_prob_raw"] = calc_res.reach_prob_raw
                sig["reach_prob_adjusted"] = calc_res.reach_prob_adjusted
                rejected_signals_to_insert.append(sig)
                continue

            sig["target_1"] = calc_res.target_1
            sig["target_2"] = calc_res.target_2
            sig["target_3"] = calc_res.target_3
            sig["target_1_atr"] = calc_res.target_1_atr
            sig["target_2_atr"] = calc_res.target_2_atr
            sig["target_3_atr"] = calc_res.target_3_atr
            sig["target_1_pct"] = calc_res.target_1_pct
            sig["target_2_pct"] = calc_res.target_2_pct
            sig["target_3_pct"] = calc_res.target_3_pct
            sig["reach_prob_t1"] = calc_res.reach_prob_t1
            sig["reach_prob_t2"] = calc_res.reach_prob_t2
            sig["reach_prob_t3"] = calc_res.reach_prob_t3
            sig["reach_prob_raw"] = calc_res.reach_prob_raw
            sig["reach_prob_adjusted"] = calc_res.reach_prob_adjusted
            sig["scale_out_weights"] = calc_res.scale_out_weights
            sig["weighted_rr"] = calc_res.weighted_rr_honest
            sig["weighted_rr_honest"] = calc_res.weighted_rr_honest

            # Assign tier based on composite score & honest R:R
            from src.position_sizer import assign_tier
            sig["tier_label"] = assign_tier(score, calc_res.weighted_rr_honest)
            if sig["tier_label"] not in ("Strong Buy", "Buy"):
                logger.info(f"[TIER FILTER] Candidate {ticker} ({strategy_name}) not in buy tier: {sig['tier_label']} (Score={score:.2f}, Honest R:R={calc_res.weighted_rr_honest:.2f})")
                sig["status"] = "rejected"
                sig["rejection_reason"] = f"Tier {sig['tier_label']} (Score {score:.1f}, R:R {calc_res.weighted_rr_honest:.2f})"
                sig["allocated_dollars"] = 0.0
                sig["exact_shares"] = 0.0
                sig["max_shares"] = 0
                sig["position_sizing"] = f"R:R {calc_res.weighted_rr_honest:.2f} ({sig['scale_out_weights']})"
                rejected_signals_to_insert.append(sig)
                continue

            # Diagnostic win probability and Kelly fraction (informational opportunity analytics only, NEVER gates recommendation)
            from src.position_sizer import calculate_p_win
            win_p = calculate_p_win(score)
            rr_val = calc_res.weighted_rr_honest if calc_res.weighted_rr_honest > 0 else 2.0
            diagnostic_raw_kelly = win_p - (1.0 - win_p) / rr_val
            sig["diagnostic_raw_kelly"] = round(diagnostic_raw_kelly, 4)
            sig["kelly_fraction"] = round(diagnostic_raw_kelly, 4)
            sig["final_adjusted_half_kelly"] = round(max(0.0, diagnostic_raw_kelly / 2.0), 4)
            sig["half_kelly_fraction"] = sig["final_adjusted_half_kelly"]

            # Qualified Recommendation Setup!
            sig["status"] = "pending"
            sig["rejection_reason"] = None
            sig["allocated_dollars"] = 0.0
            sig["exact_shares"] = 0.0
            sig["max_shares"] = 0
            sig["position_sizing"] = f"R:R {calc_res.weighted_rr_honest:.2f} ({sig['scale_out_weights']})"

            qualified_recommendations.append(sig)

        logger.info(
            f"Scan Summary: {scanned_count} scanned | "
            f"{len(qualified_recommendations)} qualified recommendations | "
            f"{earnings_rejected_count} earnings-rejected | "
            f"{reach_rejected_count} reach-prob-rejected | "
            f"{len(rejected_signals_to_insert)} total rejected/audit logged"
        )

        # Phase 3: Construct final ranked signals list for database insertion
        all_signals_to_save = qualified_recommendations + rejected_signals_to_insert
        for sig in all_signals_to_save:
            is_rejected = sig.get("status") == "rejected"
            position_sizing_str = sig.get("position_sizing") or "N/A"

            ranked_signals.append(
                {
                    "scan_date": sig["scan_date"],
                    "ticker": sig["ticker"],
                    "company_name": sig["company_name"],
                    "industry": sig["industry"],
                    "price": sig["price"],
                    "entry_price": sig["entry_price"],
                    "stop_loss": sig["stop_loss"],
                    "exit_price": sig.get("exit_price"),
                    "upside_pct": sig.get("upside_pct"),
                    "risk_reward": sig.get("risk_reward"),
                    "current_rsi": sig.get("current_rsi"),
                    "rsi_min_10d": sig.get("rsi_min_10d"),
                    "volume_ratio": sig.get("volume_ratio"),
                    "adx_value": sig.get("adx_value"),
                    "macd_histogram": sig.get("macd_histogram"),
                    "ema20": sig.get("ema20"),
                    "score": sig.get("composite_score", sig.get("score", 0.0)),
                    "composite_score": sig.get("composite_score", sig.get("score", 0.0)),
                    "quality_score": sig.get("quality_score", sig.get("composite_score", 0.0)),
                    "tier_label": sig.get("tier_label", "Rejected"),
                    "strategy": sig["strategy"],
                    "regime": regime_str,
                    "is_fallback": bool(sig.get("is_fallback", False)),
                    "target_1": sig.get("target_1"),
                    "target_2": sig.get("target_2"),
                    "target_3": sig.get("target_3"),
                    "target_1_atr": sig.get("target_1_atr"),
                    "target_2_atr": sig.get("target_2_atr"),
                    "target_3_atr": sig.get("target_3_atr"),
                    "target_1_pct": sig.get("target_1_pct"),
                    "target_2_pct": sig.get("target_2_pct"),
                    "target_3_pct": sig.get("target_3_pct"),
                    "reach_prob_t1": sig.get("reach_prob_t1"),
                    "reach_prob_t2": sig.get("reach_prob_t2"),
                    "reach_prob_t3": sig.get("reach_prob_t3"),
                    "scale_out_weights": sig.get("scale_out_weights", "50/30/20"),
                    "weighted_rr": sig.get("weighted_rr"),
                    "weighted_rr_honest": sig.get("weighted_rr_honest"),
                    "position_sizing": position_sizing_str,
                    "narrative": sig.get("narrative"),
                    "strategy_name": sig["strategy"],
                    "context_score": sig.get("context_score", 0.0),
                    # GTM persistence columns
                    "entry_date": get_next_trading_day(datetime.strptime(sig["scan_date"], "%Y-%m-%d").date()).isoformat(),
                    "status": "rejected" if is_rejected else "pending",
                    "sell_signal": False,
                    "sell_signal_reason": sig.get("rejection_reason") if is_rejected else None,
                    "rejection_reason": sig.get("rejection_reason") if is_rejected else None,
                    "sell_price": None,
                    # Context breakdown columns
                    "context_analyst": float(sig.get("context_analyst") or 0.0),
                    "context_earnings": float(sig.get("context_earnings") or 0.0),
                    "context_fundamental": float(sig.get("context_fundamental") or 0.0),
                    "context_news": float(sig.get("context_news") or 0.0),
                    # Veto and ratios
                    "de_ratio": sig.get("de_ratio"),
                    "current_ratio": sig.get("current_ratio"),
                    "earnings_surprise_pct": sig.get("earnings_surprise_pct"),
                    "finbert_sentiment": sig.get("finbert_sentiment"),
                    # New position sizing columns
                    "allocated_dollars": sig.get("allocated_dollars", 0.0),
                    "exact_shares": sig.get("exact_shares", 0.0),
                    "max_shares": sig.get("max_shares", 0),
                    # Earnings and Survivorship Risk columns
                    "next_earnings_date": sig.get("next_earnings_date"),
                    "days_to_earnings": sig.get("days_to_earnings"),
                    "earnings_rejected": bool(sig.get("earnings_rejected", False)),
                    "reach_prob_raw": sig.get("reach_prob_raw"),
                    "reach_prob_adjusted": sig.get("reach_prob_adjusted"),
                }
            )
    else:
        logger.info("No technically qualified signals found.")

    rsi_breadth_pct = round(100.0 * rsi_passed_count / scanned_count, 1) if scanned_count > 0 else 0.0
    signals_recommended = len([s for s in ranked_signals if s.get("status") != "rejected"])
    
    if ranked_signals:
        logger.info("=== FINAL RECOMMENDED SIGNALS ===")
        for s in ranked_signals:
            logger.info(f"Ticker: {s['ticker']:<5} | Strategy: {s['strategy']:<25} | Composite Score: {s['composite_score']:.2f} | Status: {s['status']} | Tier: {s['tier_label']}")
        logger.info("=================================")
    else:
        logger.info("No high-confidence setups tonight. Cash is a position.")

    duration = round(time.time() - start_time, 2)
    status = "success"

    if not args.dry_run:
        try:
            refresh_active_signals_prices(supabase)
            logger.info("Clearing previous rejected audit entries from Supabase...")
            supabase.table("signals").delete().eq("status", "rejected").execute()
            logger.info("Previous audit entries cleared.")
        except Exception as e:
            logger.error("Failed to refresh/clear signals: %s", e)
            error_msg = f"Refresh/Clear failed: {e}"

        try:
            if ranked_signals:
                logger.info("Inserting %d ranked signals...", len(ranked_signals))
                
                # Also archive new signals to signals_history as open outcomes
                history_rows = []
                for sig in ranked_signals:
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
                        "score": sig.get("composite_score", sig.get("score", 0.0)),
                        "composite_score": sig.get("composite_score", sig.get("score", 0.0)),
                        "quality_score": sig.get("quality_score", sig.get("composite_score", 0.0)),
                        "tier_label": sig.get("tier_label", "Rejected"),
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
                        "target_1_atr": sig.get("target_1_atr"),
                        "target_2_atr": sig.get("target_2_atr"),
                        "target_3_atr": sig.get("target_3_atr"),
                        "target_1_pct": sig.get("target_1_pct"),
                        "target_2_pct": sig.get("target_2_pct"),
                        "target_3_pct": sig.get("target_3_pct"),
                        "reach_prob_t1": sig.get("reach_prob_t1"),
                        "reach_prob_t2": sig.get("reach_prob_t2"),
                        "reach_prob_t3": sig.get("reach_prob_t3"),
                        "scale_out_weights": sig.get("scale_out_weights", "50/30/20"),
                        "weighted_rr": sig.get("weighted_rr"),
                        "weighted_rr_honest": sig.get("weighted_rr_honest"),
                        "position_sizing": sig.get("position_sizing", "50/30/20"),
                        "narrative": sig.get("narrative"),
                        "strategy_name": sig.get("strategy_name"),
                        "outcome": "open",
                        "sell_signal_reason": sig.get("rejection_reason"),
                        "rejection_reason": sig.get("rejection_reason"),
                        "context_score": sig.get("context_score", 0.0),
                        "context_analyst": float(sig.get("context_analyst") or 0.0),
                        "context_earnings": float(sig.get("context_earnings") or 0.0),
                        "context_fundamental": float(sig.get("context_fundamental") or 0.0),
                        "context_news": float(sig.get("context_news") or 0.0),
                        "de_ratio": sig.get("de_ratio"),
                        "current_ratio": sig.get("current_ratio"),
                        "earnings_surprise_pct": sig.get("earnings_surprise_pct"),
                        "finbert_sentiment": sig.get("finbert_sentiment"),
                        "allocated_dollars": sig.get("allocated_dollars"),
                        "exact_shares": sig.get("exact_shares"),
                        "max_shares": int(sig.get("max_shares", 0)) if sig.get("max_shares") is not None else None,
                        "next_earnings_date": sig.get("next_earnings_date"),
                        "days_to_earnings": sig.get("days_to_earnings"),
                        "earnings_rejected": bool(sig.get("earnings_rejected", False)),
                        "reach_prob_raw": sig.get("reach_prob_raw"),
                        "reach_prob_adjusted": sig.get("reach_prob_adjusted"),
                    })
                
                # Attempt insertion with full schema; fall back to base columns if DB migration is pending
                new_cols = (
                    "target_1_atr", "target_2_atr", "target_3_atr",
                    "reach_prob_t1", "reach_prob_t2", "reach_prob_t3",
                    "scale_out_weights", "weighted_rr_honest", "exact_shares",
                    "de_ratio", "current_ratio", "earnings_surprise_pct", "finbert_sentiment",
                    "next_earnings_date", "days_to_earnings", "earnings_rejected",
                    "reach_prob_adjusted", "reach_prob_raw", "rejection_reason"
                )
                try:
                    supabase.table("signals").insert(ranked_signals).execute()
                except Exception as sig_err:
                    if any(col in str(sig_err) for col in new_cols) or "42703" in str(sig_err) or "PGRST204" in str(sig_err):
                        logger.warning("Pending DB schema migration detected for 'signals'. Stripping new columns for insertion.")
                        stripped_signals = [{k: (int(v) if k == "max_shares" and v is not None else v) for k, v in row.items() if k not in new_cols} for row in ranked_signals]
                        supabase.table("signals").insert(stripped_signals).execute()
                    else:
                        raise sig_err

                try:
                    supabase.table("signals_history").upsert(history_rows, on_conflict="scan_date,ticker").execute()
                except Exception as hist_err:
                    if any(col in str(hist_err) for col in new_cols) or "42703" in str(hist_err) or "PGRST204" in str(hist_err):
                        logger.warning("Pending DB schema migration detected for 'signals_history'. Stripping new columns for upsert.")
                        stripped_history = [{k: (int(v) if k == "max_shares" and v is not None else v) for k, v in row.items() if k not in new_cols} for row in history_rows]
                        supabase.table("signals_history").upsert(stripped_history, on_conflict="scan_date,ticker").execute()
                    else:
                        raise hist_err

                logger.info("Signals inserted and archived successfully.")
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
