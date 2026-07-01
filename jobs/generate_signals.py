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


def archive_current_signals(supabase, regime_str: str, metrics_map: dict):
    """Evaluate existing open positions, update their statuses, and archive outcomes."""
    try:
        from jobs.supabase_client import get_latest_price, update_signals_status, update_signals_price, update_history_outcome
        
        # 1. Fetch current signals
        res = supabase.table("signals").select("*").execute()
        current_signals = res.data or []
        
        if not current_signals:
            logger.info("No existing signals in the database to process.")
            return []
            
        logger.info("Evaluating %d existing positions...", len(current_signals))
        
        for existing in current_signals:
            # Only evaluate open positions
            if existing.get('status', 'open') != 'open':
                continue
                
            ticker = existing['ticker']
            entry_price = float(existing['entry_price'])
            stop_loss = float(existing['stop_loss'])
            # ponytail: targets can be None for trend/momentum strategies
            target_1 = float(existing['target_1']) if existing.get('target_1') is not None else None
            target_2 = float(existing['target_2']) if existing.get('target_2') is not None else None
            target_3 = float(existing['target_3']) if existing.get('target_3') is not None else None
            has_targets = target_1 is not None
            
            current_price = get_latest_price(ticker)
            if current_price is None:
                logger.warning(f"Could not fetch latest price for {ticker}. Skipping evaluation.")
                continue
                
            # Determine status
            # Determine status
            status = 'open'
            exit_price = None
            sell_signal = None
            is_partial_exit = False
            partial_fraction = 0.0
            partial_reason = ''
            exit_outcome = ''
            
            if current_price <= stop_loss:
                status = 'closed'
                exit_price = stop_loss
                sell_signal = 'Stop loss hit'
                exit_outcome = 'stopped'
            elif has_targets and current_price >= target_3:
                status = 'closed'
                exit_price = target_3
                sell_signal = 'Target 3 hit – full exit'
                exit_outcome = 'hit_t3'
            elif has_targets and current_price >= target_2:
                # Check if T2 was already processed to avoid repeat triggers
                if not (existing.get('sell_signal_reason') and 'Target 2' in existing['sell_signal_reason']):
                    status = 'open'  # Partial exit — keep open
                    exit_price = target_2
                    sell_signal = 'Target 2 hit – sell 30%'
                    is_partial_exit = True
                    partial_fraction = 0.30
                    partial_reason = 'Target 2 hit (Partial)'
                    exit_outcome = 'hit_t2'
            elif has_targets and current_price >= target_1:
                # Check if T1 was already processed to avoid repeat triggers
                if not (existing.get('sell_signal_reason') and 'Target 1' in existing['sell_signal_reason']):
                    status = 'open'  # Partial exit — keep open
                    exit_price = target_1
                    sell_signal = 'Target 1 hit – sell 50%'
                    is_partial_exit = True
                    partial_fraction = 0.50
                    partial_reason = 'Target 1 hit (Partial)'
                    exit_outcome = 'hit_t1'
                
            if sell_signal is not None:
                logger.info(f"[SELL SIGNAL] {ticker} triggered {sell_signal} at {exit_price}")
                
                if is_partial_exit:
                    # ponytail: Position Lot Splitting logic
                    import math
                    original_max_shares = int(existing.get("max_shares") or 0)
                    original_allocated = float(existing.get("allocated_dollars") or 0.0)
                    
                    shares_sold = int(math.floor(original_max_shares * partial_fraction))
                    dollars_sold = original_allocated * partial_fraction
                    
                    if shares_sold > 0 and dollars_sold > 0:
                        # 1. Insert CLOSED portion into signals_history with dynamic outcome
                        return_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
                        holding_days = 0
                        scan_date_str = existing.get("scan_date")
                        if scan_date_str:
                            try:
                                scan_date = datetime.strptime(scan_date_str, '%Y-%m-%d').date()
                                holding_days = (datetime.now().date() - scan_date).days
                            except Exception:
                                pass
                                
                        hist_row = {
                            "scan_date": existing.get("scan_date"),
                            "ticker": f"{ticker} (Partial)",
                            "company_name": existing.get("company_name"),
                            "industry": existing.get("industry"),
                            "price": exit_price,
                            "entry_price": entry_price,
                            "stop_loss": stop_loss,
                            "exit_price": exit_price,
                            "outcome": exit_outcome,
                            "outcome_date": datetime.now().date().isoformat(),
                            "outcome_return_pct": return_pct,
                            "outcome_holding_days": holding_days,
                            "allocated_dollars": dollars_sold,
                            "max_shares": shares_sold,
                            "strategy_name": existing.get("strategy_name") or existing.get("strategy")
                        }
                        
                        logger.info(f"[ROTATION/MONITOR] Inserting partial closed history lot for {ticker}: {shares_sold} shares, ${dollars_sold:.2f}")
                        supabase.table('signals_history').insert(hist_row).execute()
                        
                        # 2. UPDATE existing OPEN row in signals table
                        remaining_shares = original_max_shares - shares_sold
                        remaining_dollars = original_allocated - dollars_sold
                        
                        update_payload = {
                            "max_shares": remaining_shares,
                            "allocated_dollars": remaining_dollars,
                            "sell_signal_reason": sell_signal,
                            "sell_signal": True,
                            "sell_price": exit_price,
                            "price": current_price
                        }
                        
                        if exit_outcome == 'hit_t1':
                            logger.info(f"[MONITOR] Setting stop loss to breakeven: {entry_price}")
                            update_payload["stop_loss"] = entry_price
                            
                        supabase.table('signals').update(update_payload).eq('id', existing['id']).execute()
                else:
                    if status == 'closed':
                        # Full exit — update and archive
                        update_signals_status(ticker, 'closed', exit_price, sell_signal, sell_signal_reason=sell_signal)
                        update_history_outcome(ticker, status, exit_price, sell_signal)
                    else:
                        # Fallback standard update
                        supabase.table('signals').update({
                            'sell_signal': True,
                            'sell_signal_reason': sell_signal,
                            'sell_price': exit_price,
                            'price': current_price,
                        }).eq('ticker', ticker).eq('status', 'open').execute()
            else:
                logger.info(f"[POSITION HOLD] {ticker} remains open. Current price: {current_price:.2f}")
                # Update current price in signals table
                update_signals_price(ticker, current_price)
                
        # Return list of active open tickers to skip in daily scan insertion
        res_updated = supabase.table("signals").select("ticker").eq("status", "open").execute()
        open_tickers = [row['ticker'] for row in (res_updated.data or [])]
        return open_tickers
        
    except Exception as e:
        logger.error("Failed to archive/evaluate current signals: %s", e)
        return []


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

    # TASK 3: Drawdown Circuit Breaker
    portfolio_value = 10000.0
    peak_value = 10000.0
    try:
        # Fetch the latest record in portfolio_state
        res_state = supabase.table("portfolio_state").select("*").order("created_at", desc=True).limit(1).execute()
        if res_state.data:
            state = res_state.data[0]
            portfolio_value = float(state["portfolio_value"])
            peak_value = float(state["peak_value"])
            logger.info(f"[PORTFOLIO] Found state: Value=${portfolio_value:.2f}, Peak=${peak_value:.2f}")
        else:
            logger.info("[PORTFOLIO] No state entries found in portfolio_state. Using default 10k values.")
    except Exception as e:
        logger.warning(f"Failed to fetch portfolio state: {e}. Using default 10k values.")

    from src.risk_controls import get_drawdown_multiplier
    risk_multiplier, risk_status = get_drawdown_multiplier(portfolio_value, peak_value)
    logger.info(f"[RISK CONTROL] Drawdown Multiplier={risk_multiplier:.2f} ({risk_status})")

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
        # TASK 1: Log the applied weight vector on each run
        REGIME_WEIGHTS = {
            "bull":     {"mom": 0.30, "exp": 0.30, "wr": 0.15, "reg": 0.10, "ctx": 0.15},
            "sideways": {"mom": 0.25, "exp": 0.35, "wr": 0.15, "reg": 0.10, "ctx": 0.15},
            "bear":     {"mom": 0.15, "exp": 0.35, "wr": 0.10, "reg": 0.10, "ctx": 0.30},
        }
        w = REGIME_WEIGHTS.get(regime_str.lower(), REGIME_WEIGHTS["sideways"])
        logger.info(f"[WEIGHTS] Regime={regime_str}, weights={w}")

        final_signals = deduplicate_by_ticker(all_signals)
        
        # TASK 3: Filter candidates if drawdown is >= 10% (requires composite_score >= 80)
        dd_pct = (peak_value - portfolio_value) / peak_value * 100 if peak_value > 0 else 0.0
        if dd_pct >= 10.0:
            logger.info(f"[RISK CONTROL] Drawdown is {dd_pct:.1f}% >= 10%. Filtering candidates to require composite_score >= 80.")
            final_signals = [s for s in final_signals if float(s.get("composite_score", 0.0)) >= 80.0]

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
        # Ensure no final signals slip through without context_score (skip if SKIP_NLP=true)
        skip_nlp = os.getenv("SKIP_NLP", "false").lower() == "true"
        if not skip_nlp:
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
                            sig["context_analyst"] = getattr(ctx, "context_analyst", 0.0)
                            sig["context_earnings"] = getattr(ctx, "context_earnings", 0.0)
                            sig["context_fundamental"] = getattr(ctx, "context_fundamental", 0.0)
                            sig["context_news"] = getattr(ctx, "context_news", 0.0)
                            
                            # Save to context_cache table on cache miss
                            if ctx.cached_score is None:
                                from src.providers.context.aggregator import save_context_to_cache
                                save_context_to_cache(sig["ticker"], sig["context_score"], ctx)
                                
                            # Adjust composite score by adding the context score contribution
                            old_score = float(sig.get("composite_score", 50.0))
                            context_score = float(sig["context_score"])
                            new_score = old_score + context_score * 0.15
                            sig["composite_score"] = round(new_score, 4)
                            sig["quality_score"] = round(new_score, 4)
                            logger.info(f"[CONTEXT FALLBACK] Computed context_score {sig['context_score']:.2f} for {sig['ticker']} (new composite: {sig['composite_score']:.1f})")
                    except Exception as e:
                        logger.warning(f"[CONTEXT FALLBACK] Failed for {sig['ticker']}: {e}")
                        sig["context_score"] = 0.0

        # ponytail: Hybrid exit architecture — short-term keeps ATR-scaled T1/T2/T3,
        # trend/momentum strategies get None targets + trailing stop
        SHORT_TERM_STRATEGIES = {'Pullback Recovery', 'Mean Reversion', 'Post-Earnings Drift'}
        TREND_STRATEGIES = {'Trend Following', 'Sector Rotation', '52-Week High', '52-Week High Breakout', 'Cross-Sectional Momentum'}

        # Fetch active open positions from Supabase to prevent duplicates and calculate current portfolio allocation
        open_positions = []
        open_tickers = []
        open_positions_allocated_sum = 0.0
        try:
            res_open = supabase.table("signals").select("*").eq("status", "open").execute()
            open_positions = res_open.data or []
            open_tickers = [row['ticker'].upper() for row in open_positions]
            
            # Sum up allocated_dollars of all currently 'open' positions
            for pos in open_positions:
                allocated = pos.get("allocated_dollars")
                if allocated is not None and float(allocated) > 0:
                    pos_val = float(allocated)
                else:
                    # Fallback to estimate if allocated_dollars is missing/NULL
                    ps_str = pos.get("position_sizing") or ""
                    pct = 0.05
                    # Do not parse slash formats (legacy)
                    clean = ps_str.replace("Kelly:", "").replace("K:", "").replace("%", "").strip()
                    try:
                        if clean and '/' not in clean:
                            pct = float(clean) / 100.0
                    except ValueError:
                        pass
                    pos_val = pct * portfolio_value
                open_positions_allocated_sum += pos_val
                
            logger.info(f"[PORTFOLIO] Allocated capital in open positions: ${open_positions_allocated_sum:.2f}")
        except Exception as e:
            logger.warning("Failed to fetch active open positions/allocated capital from Supabase: %s", e)

        # ── Capital Rotation Protocol ──────────────────────────────────────
        try:
            # 1. Identify Tier-1 Candidates (Score >= 85)
            tier1_candidates = [sig for sig in final_signals if float(sig.get("composite_score") or 0.0) >= 85.0]
            # 2. Identify Weak Holdings (Score <= 55)
            weak_holdings = [pos for pos in open_positions if float(pos.get("composite_score") or 0.0) <= 55.0]
            
            if tier1_candidates and weak_holdings:
                # Estimate raw Kelly weights & total raw dollars needed for new signals
                total_raw_dollars_needed = 0.0
                for sig in final_signals:
                    score = float(sig.get("composite_score", 0.0))
                    win_p = 0.35
                    if score >= 90.0: win_p = 0.75
                    elif score >= 80.0: win_p = 0.68
                    elif score >= 70.0: win_p = 0.60
                    elif score >= 60.0: win_p = 0.52
                    elif score >= 50.0: win_p = 0.45
                    
                    rr_val = sig.get('weighted_rr') if sig.get('weighted_rr', 0) > 0 else 2.0
                    kelly_f = win_p - (1.0 - win_p) / rr_val
                    half_kelly = max(0.0, kelly_f / 2.0)
                    
                    half_kelly_fraction = half_kelly * risk_multiplier * size_mult
                    raw_dollar_sizing = portfolio_value * half_kelly_fraction
                    total_raw_dollars_needed += raw_dollar_sizing
                
                # Sort weak holdings lowest score first to rotate the weakest ones first
                weak_holdings = sorted(weak_holdings, key=lambda x: float(x.get("composite_score") or 0.0))
                
                # Pair them up and trigger rotation if cash-constrained
                from jobs.supabase_client import update_signals_status, update_history_outcome
                
                for t1_cand in tier1_candidates:
                    # Check cash constraint
                    unallocated_cash = portfolio_value - open_positions_allocated_sum
                    if total_raw_dollars_needed <= unallocated_cash:
                        break # No longer cash-constrained, stop rotating
                        
                    if not weak_holdings:
                        break # No more weak holdings to rotate
                        
                    weak_pos = weak_holdings[0]
                    spread = float(t1_cand.get("composite_score") or 0.0) - float(weak_pos.get("composite_score") or 0.0)
                    
                    if spread >= 30.0:
                        exit_price = float(weak_pos.get("price") or weak_pos.get("entry_price") or 0.0)
                        reason = f"Capital Rotation Triggered (Reallocating to {t1_cand['ticker']})"
                        
                        logger.info(f"[ROTATION] Rotating out of weak holding {weak_pos['ticker']} (Score: {weak_pos['composite_score']}) "
                                    f"to fund {t1_cand['ticker']} (Score: {t1_cand['composite_score']}) | Spread: {spread:.2f}")
                        
                        # Execute database updates
                        update_signals_status(weak_pos['ticker'], 'closed', exit_price, True, reason)
                        update_history_outcome(weak_pos['ticker'], 'closed', exit_price, True)
                        
                        # Deduct weak position allocated dollars from Occupied pool
                        allocated = weak_pos.get("allocated_dollars")
                        if allocated is not None and float(allocated) > 0:
                            pos_val = float(allocated)
                        else:
                            ps_str = weak_pos.get("position_sizing") or ""
                            pct = 0.05
                            clean = ps_str.replace("Kelly:", "").replace("K:", "").replace("%", "").strip()
                            try:
                                if clean and '/' not in clean:
                                    pct = float(clean) / 100.0
                            except ValueError:
                                pass
                            pos_val = pct * portfolio_value
                        
                        open_positions_allocated_sum -= pos_val
                        
                        # Remove from tracking lists
                        weak_holdings.pop(0)
                        if weak_pos['ticker'].upper() in open_tickers:
                            open_tickers.remove(weak_pos['ticker'].upper())
                        open_positions = [p for p in open_positions if p['ticker'].upper() != weak_pos['ticker'].upper()]
        except Exception as rotation_err:
            logger.error(f"[ROTATION ERROR] Failed during rotation evaluation: {rotation_err}")

        # Compute available cash constraints: Portfolio Value - Sum(Allocated Dollars of Open Positions) (Floored at 0.0)
        available_cash = max(0.0, portfolio_value - open_positions_allocated_sum)
        logger.info(f"[PORTFOLIO] Available cash for new setups: ${available_cash:.2f}")

        # Phase 1: Pre-calculate raw Kelly weights and metrics for potential new setups
        candidates_to_size = []
        for sig in final_signals:
            ticker = sig["ticker"]
            if ticker.upper() in open_tickers:
                logger.info(f"Ticker {ticker} is already an active open position. Skipping recommendation insertion.")
                continue

            entry_price = float(sig["entry_price"])
            stop_loss = float(sig["stop_loss"])
            strategy_name = sig["strategy"]

            # Re-evaluate tier label based on the actual final composite score and metrics
            score = float(sig.get("composite_score", 0.0))
            # TASK 4: Regime-Aware Tier 1 Threshold
            TIER1_THRESHOLDS = {
                "bull":     80,
                "sideways": 75,
                "bear":     75,   # also require ctx_score > 50
            }
            current_regime = regime_str.lower()
            threshold = TIER1_THRESHOLDS.get(current_regime, 75)
            ctx_score = float(sig.get("context_score", 0.0))
            
            if current_regime == "bear":
                tier1_pass = (score >= threshold) and (ctx_score > 50.0)
            else:
                tier1_pass = score >= threshold

            exp = float(sig.get("expectancy_pct") or 0.0)
            wr = float(sig.get("past_win_rate") or sig.get("win_rate") or 0.0)
            trades = int(sig.get("total_trades") or 0)

            is_t1 = tier1_pass and (exp > 0.0) and (wr >= 35.0) and (trades >= 10)
            is_t2 = (score >= 50.0) and (exp >= 0.0) and (wr >= 25.0) and (trades >= 10)
            is_t3 = (score >= 40.0) and (exp >= -2.0)

            if is_t1:
                sig["tier_label"] = "Strong Buy"
            elif is_t2:
                sig["tier_label"] = "Buy"
            elif is_t3:
                sig["tier_label"] = "Watch"
            else:
                sig["tier_label"] = "Speculative"

            atr = float(sig.get("atr_14", 0.0))

            if strategy_name in SHORT_TERM_STRATEGIES:
                # ponytail: ATR-scaled T1/T2/T3 for cash-flow strategies
                target_1 = round(entry_price + 1.5 * atr, 2) if atr > 0 else round(entry_price * 1.07, 2)
                target_2 = round(entry_price + 2.5 * atr, 2) if atr > 0 else round(entry_price * 1.12, 2)
                target_3 = round(entry_price + 3.5 * atr, 2) if atr > 0 else round(entry_price * 1.18, 2)
                sig["target_1_pct"] = round((target_1 / entry_price - 1) * 100, 1)
                sig["target_2_pct"] = round((target_2 / entry_price - 1) * 100, 1)
                sig["target_3_pct"] = round((target_3 / entry_price - 1) * 100, 1)
                risk = entry_price - stop_loss
                reward = (target_1 - entry_price) * 0.5 + (target_2 - entry_price) * 0.3 + (target_3 - entry_price) * 0.2
                sig["weighted_rr"] = round(reward / risk, 2) if risk > 0 else 0.0
            else:
                # ponytail: Trend/momentum — no profit caps, 3-ATR trailing stop
                target_1 = None
                target_2 = None
                target_3 = None
                sig["target_1_pct"] = None
                sig["target_2_pct"] = None
                sig["target_3_pct"] = None
                # Override stop_loss to 3*ATR trailing stop
                if atr > 0:
                    stop_loss = round(entry_price - 3.0 * atr, 2)
                    sig["stop_loss"] = stop_loss
                risk = entry_price - stop_loss
                # R:R approximation for trend: use 3*ATR as expected reward (conservative)
                sig["weighted_rr"] = round((3.0 * atr) / risk, 2) if risk > 0 else 0.0

            # Explicit target nullification for all Trend/Momentum strategies
            if strategy_name in TREND_STRATEGIES:
                target_1 = None
                target_2 = None
                target_3 = None
                sig["target_1_pct"] = None
                sig["target_2_pct"] = None
                sig["target_3_pct"] = None

            # TASK 2 & 3: Sizing adjustments based on VIX override and drawdown controls
            win_p = 0.35
            if score >= 90.0: win_p = 0.75
            elif score >= 80.0: win_p = 0.68
            elif score >= 70.0: win_p = 0.60
            elif score >= 60.0: win_p = 0.52
            elif score >= 50.0: win_p = 0.45
            
            rr_val = sig.get('weighted_rr') if sig.get('weighted_rr', 0) > 0 else 2.0
            kelly_f = win_p - (1.0 - win_p) / rr_val
            half_kelly = max(0.0, kelly_f / 2.0)
            
            # Pre-calculate half kelly fraction
            sig["half_kelly_fraction"] = half_kelly * risk_multiplier * size_mult
            
            # Store temporary attributes needed for final build
            sig["target_1"] = target_1
            sig["target_2"] = target_2
            sig["target_3"] = target_3
            
            candidates_to_size.append(sig)

        # Phase 2: Apply Cash-Constrained Cross-Sectional Normalization
        from src.ranker import calculate_normalized_sizing
        sized_signals = calculate_normalized_sizing(candidates_to_size, portfolio_value, available_cash)

        # Phase 3: Construct final ranked signals list for database insertion
        for sig in sized_signals:
            # We display the final allocated percentage in position_sizing string
            final_alloc_pct = 0.0
            if portfolio_value > 0:
                final_alloc_pct = (sig["allocated_dollars"] / portfolio_value) * 100.0
            position_sizing_str = f"K: {final_alloc_pct:.1f}%"
            if final_alloc_pct == 0.0 or available_cash <= 0:
                position_sizing_str = "Allocation: 0.0% (No Cash Available)"

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
                    "target_1": sig["target_1"],
                    "target_2": sig["target_2"],
                    "target_3": sig["target_3"],
                    "target_1_pct": sig.get("target_1_pct"),
                    "target_2_pct": sig.get("target_2_pct"),
                    "target_3_pct": sig.get("target_3_pct"),
                    "weighted_rr": sig.get("weighted_rr"),
                    "position_sizing": position_sizing_str,
                    "narrative": sig.get("narrative"),
                    "strategy_name": sig["strategy"],
                    "context_score": sig.get("context_score", 0.0),
                    # GTM persistence columns (Task 2)
                    "entry_date": get_next_trading_day(datetime.strptime(sig["scan_date"], "%Y-%m-%d").date()).isoformat(),
                    "status": "pending",
                    "sell_signal": False,
                    "sell_signal_reason": None,
                    "sell_price": None,
                    # Context breakdown columns (Task 7)
                    "context_analyst": float(sig.get("context_analyst") or 0.0),
                    "context_earnings": float(sig.get("context_earnings") or 0.0),
                    "context_fundamental": float(sig.get("context_fundamental") or 0.0),
                    "context_news": float(sig.get("context_news") or 0.0),
                    # New position sizing columns
                    "allocated_dollars": sig["allocated_dollars"],
                    "max_shares": sig["max_shares"],
                }
            )
    else:
        logger.info("No technically qualified signals found.")

    rsi_breadth_pct = round(100.0 * rsi_passed_count / scanned_count, 1) if scanned_count > 0 else 0.0
    signals_recommended = len(ranked_signals)
    
    if ranked_signals:
        logger.info("=== FINAL RECOMMENDED SIGNALS ===")
        for s in ranked_signals:
            logger.info(f"Ticker: {s['ticker']:<5} | Strategy: {s['strategy']:<25} | Composite Score: {s['composite_score']:.2f} | Tier: {s['tier_label']}")
        logger.info("=================================")
    else:
        logger.info("No high-confidence setups tonight. Cash is a position.")

    duration = round(time.time() - start_time, 2)
    status = "success"

    if not args.dry_run:
        try:
            archive_current_signals(supabase, regime_str, metrics_map)
            logger.info("Clearing closed signals from Supabase (keeping open)...")
            supabase.table("signals").delete().neq("status", "open").execute()
            logger.info("Closed signals cleared.")
        except Exception as e:
            logger.error("Failed to clear/archive signals: %s", e)
            error_msg = f"Archive/Clear failed: {e}"

        try:
            if ranked_signals:
                logger.info("Inserting %d ranked signals...", len(ranked_signals))
                supabase.table("signals").insert(ranked_signals).execute()
                
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
                        "allocated_dollars": sig.get("allocated_dollars"),
                        "max_shares": sig.get("max_shares"),
                    })
                
                supabase.table("signals_history").upsert(
                    history_rows,
                    on_conflict="scan_date,ticker"
                ).execute()
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
