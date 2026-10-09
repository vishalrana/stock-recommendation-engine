"""
Production Backtest Validation Engine
======================================
Master Quantitative Specification v2.3+ Compliant

Executes an independent, realistic, zero-lookahead backtest using the actual production
signal generation, ranking, entry location, target/risk, and outcome resolution pipeline.

Key Design Elements:
1. Chronological Partitioning: In-Sample (Train) vs Out-of-Sample (Test) with 20-bar Embargo.
2. Execution Realism: D+1 Open entry price, 10 bps transaction cost / slippage per trade.
3. Canonical Outcome Resolver: PositionScaleOutTracker via evaluate_signal_outcome.
4. Statistical Validity: Wilson 95% CI on Win Rate, standard error on expectancy, benchmark comparison (SPY).
5. Survivorship Caveat: Acknowledges 2025-2026 cache coverage and documents limitations honestly.
"""

import os
import sys
import json
import math
import time
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.data.cache_manager import get_cache_manager
from src.indicators import calculate_indicators
from src.entry_location import evaluate_entry_location
from src.ranker import (
    SignalRanker,
    assign_tier,
    compute_momentum_score,
    compute_expectancy_score,
    compute_regime_alignment,
    validate_candidate_features,
    normalize_strategy_key,
)
from src.strategies.target_calculator import calculate_targets
from src.quant_config import (
    STRATEGY_STOP_CONFIG,
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
)
from src.outcome.outcome_calculator import (
    evaluate_signal_outcome,
    SAME_DAY_AMBIGUITY_POLICY,
)

# Active production strategies & helpers
from jobs.generate_signals import STRATEGIES, load_metrics
from src.utils.metrics_pipeline import build_hardened_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("validate_backtest_pipeline")

# Representative liquid benchmark universe across sectors
BENCHMARK_UNIVERSE = [
    # Mega-cap Tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    # Semiconductors & Tech Momentum
    "AVGO", "AMD", "QCOM", "TXN", "NOW", "PANW",
    # Financials
    "JPM", "BAC", "GS", "MS", "BLK", "SCHW",
    # Healthcare & Pharma
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO",
    # Industrials & Defense
    "CAT", "GE", "LMT", "RTX", "HON", "BA",
    # Energy
    "XOM", "CVX", "SLB", "EOG", "COP",
    # Consumer & Retail
    "WMT", "COST", "HD", "PG", "KO", "MCD",
    # High-Beta / Growth
    "PLTR", "UBER", "CRWD", "COIN",
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLI", "XLP", "XLU", "XLV", "XLY", "XLB", "SMH", "SOXX", "XBI", "KRE",
]

TRANSACTION_COST_PCT = 0.10  # 10 bps per trade (0.20% round trip)
MAX_HOLDING_DAYS = 20
EMBARGO_DAYS = 20
MAX_CONCURRENT_POSITIONS = 10


def calculate_wilson_ci(wins: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Calculate Wilson score confidence interval for a proportion."""
    if n == 0:
        return 0.0, 0.0
    z = 1.959964  # 95% confidence z-score
    p = wins / n
    denominator = 1 + z**2 / n
    centre_adjusted_probability = p + z**2 / (2 * n)
    adjusted_standard_deviation = math.sqrt((p * (1 - p) + z**2 / (4 * n)) / n)
    lower = (centre_adjusted_probability - z * adjusted_standard_deviation) / denominator
    upper = (centre_adjusted_probability + z * adjusted_standard_deviation) / denominator
    return max(0.0, lower * 100.0), min(100.0, upper * 100.0)


def run_pipeline_backtest(
    start_date: str = "2025-02-13",
    end_date: str = "2026-10-02",
    train_split_ratio: float = 0.60,
) -> Dict[str, Any]:
    """
    Run chronological walk-forward / in-sample vs out-of-sample backtest
    using actual production signal generator, ranker, entry location, and outcome calculator.
    """
    t_start = time.time()
    cm = get_cache_manager()
    logger.info("Preloading price data from date-partitioned cache...")
    cm.preload_history(start_date, end_date)

    spy_df = cm._history_cache.get("SPY")
    if spy_df is None or len(spy_df) < 200:
        spy_df = cm._history_cache.get("XLK")

    # Compute indicators for universe
    ticker_dfs: Dict[str, pd.DataFrame] = {}
    valid_universe = []
    for t in BENCHMARK_UNIVERSE:
        raw = cm._history_cache.get(t)
        if raw is not None and len(raw) >= 120:
            df_ind = calculate_indicators(raw).sort_index()
            ticker_dfs[t] = df_ind
            valid_universe.append(t)

    logger.info(f"Loaded {len(valid_universe)} valid instruments with indicators.")
    if not valid_universe:
        raise RuntimeError("No instruments found in cache for backtest.")

    # Harmonize common trading dates
    sample_df = next(iter(ticker_dfs.values()))
    all_dates = list(sample_df.index)
    # Require at least 60 bars warm-up and 20 bars forward
    eval_dates = [d for d in all_dates if all_dates.index(d) >= 60 and len(all_dates) - all_dates.index(d) > MAX_HOLDING_DAYS]
    n_eval = len(eval_dates)
    logger.info(f"Total evaluation trading days: {n_eval} ({eval_dates[0].strftime('%Y-%m-%d')} to {eval_dates[-1].strftime('%Y-%m-%d')})")

    # Chronological partition
    train_cutoff_idx = int(n_eval * train_split_ratio)
    train_dates = set(eval_dates[:train_cutoff_idx])
    embargo_dates = set(eval_dates[train_cutoff_idx : train_cutoff_idx + EMBARGO_DAYS])
    test_dates = set(eval_dates[train_cutoff_idx + EMBARGO_DAYS :])

    logger.info(f"Train dates: {len(train_dates)}, Embargo dates: {len(embargo_dates)}, Test (OOS) dates: {len(test_dates)}")

    executed_trades: List[Dict[str, Any]] = []

    for d_idx, eval_date in enumerate(eval_dates):
        is_train = eval_date in train_dates
        is_test = eval_date in test_dates
        if not is_train and not is_test:
            continue  # Inside embargo gap

        partition = "TRAIN" if is_train else "TEST"

        # Determine regime on eval_date
        regime = "bull"
        if spy_df is not None and eval_date in spy_df.index:
            s_loc = spy_df.index.get_loc(eval_date)
            if s_loc >= 50:
                p_spy = float(spy_df["CLOSE"].iloc[s_loc])
                dma50_spy = float(spy_df["CLOSE"].iloc[max(0, s_loc - 49):s_loc + 1].mean())
                regime = "bull" if p_spy >= dma50_spy else "bear"

        day_candidates = []

        for ticker in valid_universe:
            df_full = ticker_dfs[ticker]
            if eval_date not in df_full.index:
                continue

            loc = df_full.index.get_loc(eval_date)
            if loc < 60:
                continue

            # Strict zero look-ahead slice up to and including eval_date
            df_pit = df_full.iloc[: loc + 1].copy()
            curr_bar = df_pit.iloc[-1]
            atr_v = float(curr_bar.get("ATR_14", 0.0))
            if atr_v <= 0 or math.isnan(atr_v):
                continue

            metrics = load_metrics(ticker, {}, {}, {})

            # Scan production strategies
            for strat in STRATEGIES:
                strat_name = strat.name
                strat_key = normalize_strategy_key(strat_name)

                # Regime filter
                if regime == "bear" and strat_key not in ("pead", "cross_sectional", "pullback"):
                    continue

                try:
                    sig = strat.scan(ticker, df_pit, regime, metrics)
                except Exception:
                    sig = None

                if not sig or not sig.get("entry_price") or not sig.get("stop_loss"):
                    continue

                entry_p = float(sig["entry_price"])
                raw_stop = float(sig["stop_loss"])

                # Enforce stop bounds (7% ceiling, canonical strategy floor)
                stop_cfg = STRATEGY_STOP_CONFIG.get(strat_key, {})
                stop_floor_pct = float(stop_cfg.get("stop_floor", 0.04))
                stop_loss = max(raw_stop, round(entry_p * 0.93, 2))
                stop_loss = min(stop_loss, round(entry_p * (1.0 - stop_floor_pct), 2))
                sig["stop_loss"] = stop_loss

                # Targets calculation
                calc_res = calculate_targets(
                    ticker=ticker,
                    entry_price=entry_p,
                    atr_14=atr_v,
                    stop_loss=stop_loss,
                    strategy_name=strat_name,
                    mock_reach_probs=(0.70, 0.50, 0.35),
                )
                sig["target_1"] = calc_res.target_1
                sig["target_2"] = calc_res.target_2
                sig["target_3"] = calc_res.target_3
                sig["weighted_rr"] = calc_res.weighted_rr_honest

                # Tier check
                comp_score = float(sig.get("composite_score", 70.0))
                tier = assign_tier(comp_score, calc_res.weighted_rr_honest)
                if tier not in ("Strong Buy", "Buy"):
                    continue

                # Entry Location qualification
                loc_eval = evaluate_entry_location(sig, df_pit, strat_name)
                if loc_eval.state in ("REJECT", "WAIT"):
                    continue

                day_candidates.append({
                    "ticker": ticker,
                    "strategy": strat_name,
                    "eval_date": eval_date,
                    "entry_price": entry_p,
                    "stop_loss": stop_loss,
                    "target_1": calc_res.target_1,
                    "target_2": calc_res.target_2,
                    "target_3": calc_res.target_3,
                    "composite_score": comp_score,
                    "tier": tier,
                    "partition": partition,
                    "regime": regime,
                    "eval_loc": loc,
                })

        if not day_candidates:
            continue

        # Sort and deduplicate top candidates per date (max 5)
        day_candidates.sort(key=lambda x: x["composite_score"], reverse=True)
        seen_tickers = set()
        selected_ideas = []
        for c in day_candidates:
            if c["ticker"] not in seen_tickers:
                seen_tickers.add(c["ticker"])
                selected_ideas.append(c)
                if len(selected_ideas) >= 5:
                    break

        # Simulate D+1 Execution and Canonical Outcome Resolution
        for cand in selected_ideas:
            ticker = cand["ticker"]
            df_full = ticker_dfs[ticker]
            eval_loc = cand["eval_loc"]

            if eval_loc + 1 >= len(df_full):
                continue

            d_plus_1_bar = df_full.iloc[eval_loc + 1]
            open_px = float(d_plus_1_bar.get("OPEN", d_plus_1_bar["CLOSE"]))
            # If D+1 Open gaps up > 3% above signal entry price, skip execution due to slippage
            if open_px > cand["entry_price"] * 1.03:
                continue

            # Forward window from D+1 onwards
            fwd_df = df_full.iloc[eval_loc + 1 : eval_loc + 1 + MAX_HOLDING_DAYS].copy()
            if fwd_df.empty:
                continue

            # Resolve using production outcome state machine
            res = evaluate_signal_outcome(
                df=fwd_df,
                entry_price=open_px,
                stop_loss=cand["stop_loss"],
                target_1=cand["target_1"],
                target_2=cand["target_2"],
                target_3=cand["target_3"],
                max_holding_days=MAX_HOLDING_DAYS,
            )

            if res is None:
                continue

            raw_return = float(res.get("outcome_return_pct", res.get("realized_return_pct", 0.0)))
            net_return = raw_return - (TRANSACTION_COST_PCT * 2.0)  # Round-trip 20 bps cost
            outcome = res.get("outcome", "unknown")
            holding_days = int(res.get("outcome_holding_days", res.get("holding_days", 0)))

            executed_trades.append({
                "date": cand["eval_date"].strftime("%Y-%m-%d"),
                "ticker": ticker,
                "strategy": cand["strategy"],
                "partition": cand["partition"],
                "regime": cand["regime"],
                "entry_price": round(open_px, 2),
                "stop_loss": round(cand["stop_loss"], 2),
                "target_1": round(cand["target_1"], 2),
                "target_2": round(cand["target_2"], 2) if cand["target_2"] else None,
                "target_3": round(cand["target_3"], 2) if cand["target_3"] else None,
                "composite_score": round(cand["composite_score"], 2),
                "tier": cand["tier"],
                "outcome": outcome,
                "holding_days": holding_days,
                "raw_return_pct": round(raw_return, 4),
                "net_return_pct": round(net_return, 4),
                "is_win": net_return > 0.0,
            })

    elapsed = time.time() - t_start
    logger.info(f"Backtest completed in {elapsed:.2f}s. Total simulated trades: {len(executed_trades)}")

    # Compile Partition Analytics
    def compile_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(trades)
        if n == 0:
            return {
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "win_rate_ci_95": [0.0, 0.0],
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "profit_factor": 0.0,
                "raw_expectancy_pct": 0.0,
                "net_expectancy_pct": 0.0,
                "expectancy_std_err": 0.0,
                "median_holding_days": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
            }

        wins = [t for t in trades if t["net_return_pct"] > 0]
        losses = [t for t in trades if t["net_return_pct"] <= 0]
        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = (n_wins / n) * 100.0
        ci_lower, ci_upper = calculate_wilson_ci(n_wins, n)

        avg_win = float(np.mean([t["net_return_pct"] for t in wins])) if wins else 0.0
        avg_loss = float(np.mean([t["net_return_pct"] for t in losses])) if losses else 0.0

        gross_gains = sum(t["net_return_pct"] for t in wins)
        gross_losses = abs(sum(t["net_return_pct"] for t in losses))
        profit_factor = round(gross_gains / gross_losses, 2) if gross_losses > 0 else (99.0 if gross_gains > 0 else 0.0)

        net_returns = [t["net_return_pct"] for t in trades]
        raw_returns = [t["raw_return_pct"] for t in trades]
        net_exp = float(np.mean(net_returns))
        raw_exp = float(np.mean(raw_returns))
        std_err = float(np.std(net_returns) / math.sqrt(n)) if n > 1 else 0.0
        med_holding = float(np.median([t["holding_days"] for t in trades]))

        # Cumulative drawdown calculation
        cum = np.cumsum(net_returns)
        running_max = np.maximum.accumulate(cum)
        dd = cum - running_max
        max_dd = float(abs(np.min(dd))) if len(dd) > 0 else 0.0

        # Annualized Sharpe (assuming med_holding turns/year)
        ret_std = float(np.std(net_returns))
        sharpe = round((net_exp / ret_std) * math.sqrt(252 / max(1.0, med_holding)), 2) if ret_std > 0 else 0.0

        return {
            "trade_count": n,
            "wins": n_wins,
            "losses": n_losses,
            "win_rate_pct": round(win_rate, 2),
            "win_rate_ci_95": [round(ci_lower, 2), round(ci_upper, 2)],
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": profit_factor,
            "raw_expectancy_pct": round(raw_exp, 2),
            "net_expectancy_pct": round(net_exp, 2),
            "expectancy_std_err": round(std_err, 2),
            "median_holding_days": round(med_holding, 1),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": sharpe,
        }

    train_trades = [t for t in executed_trades if t["partition"] == "TRAIN"]
    test_trades = [t for t in executed_trades if t["partition"] == "TEST"]

    train_stats = compile_stats(train_trades)
    test_stats = compile_stats(test_trades)
    all_stats = compile_stats(executed_trades)

    # Strategy breakdown (overall)
    strat_breakdown = {}
    for s_name in sorted({t["strategy"] for t in executed_trades}):
        s_trades = [t for t in executed_trades if t["strategy"] == s_name]
        strat_breakdown[s_name] = compile_stats(s_trades)

    # Regime breakdown (overall)
    regime_breakdown = {}
    for r_name in sorted({t["regime"] for t in executed_trades}):
        r_trades = [t for t in executed_trades if t["regime"] == r_name]
        regime_breakdown[r_name] = compile_stats(r_trades)

    # SPY Benchmark comparison over eval period
    spy_eval_return = 0.0
    if spy_df is not None and len(eval_dates) >= 2:
        try:
            start_spy = float(spy_df["CLOSE"].asof(eval_dates[0]))
            end_spy = float(spy_df["CLOSE"].asof(eval_dates[-1]))
            spy_eval_return = round(((end_spy - start_spy) / start_spy) * 100.0, 2)
        except Exception:
            spy_eval_return = 0.0

    # Determine Edge Status
    oos_count = test_stats["trade_count"]
    oos_exp = test_stats["net_expectancy_pct"]
    oos_ci = test_stats["win_rate_ci_95"]
    if oos_count >= 30 and oos_exp > 0.0 and oos_ci[0] > 40.0:
        edge_status = "VERIFIED_STATISTICALLY_DEFENSIBLE"
    elif oos_count > 0 and oos_exp > 0.0:
        edge_status = "PROVISIONAL_POSITIVE_EXPECTANCY_LIMITED_SAMPLE"
    else:
        edge_status = "UNVERIFIED_INSUFFICIENT_OUT_OF_SAMPLE_EDGE"

    summary = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "execution_duration_seconds": round(elapsed, 2),
            "instruments_evaluated": len(valid_universe),
            "date_range": [start_date, end_date],
            "evaluation_sessions": n_eval,
            "train_sessions": len(train_dates),
            "embargo_sessions": len(embargo_dates),
            "test_oos_sessions": len(test_dates),
            "transaction_cost_pct_roundtrip": round(TRANSACTION_COST_PCT * 2.0, 3),
            "max_holding_days": MAX_HOLDING_DAYS,
            "ambiguity_policy": SAME_DAY_AMBIGUITY_POLICY,
            "edge_status": edge_status,
        },
        "in_sample_train_stats": train_stats,
        "out_of_sample_test_stats": test_stats,
        "combined_all_stats": all_stats,
        "strategy_breakdown": strat_breakdown,
        "regime_breakdown": regime_breakdown,
        "benchmark_comparison": {
            "benchmark_symbol": "SPY",
            "benchmark_period_return_pct": spy_eval_return,
            "pipeline_total_trades": all_stats["trade_count"],
            "pipeline_net_expectancy_pct": all_stats["net_expectancy_pct"],
        },
        "survivorship_and_limitations": {
            "survivorship_caveat": "Universe cached represents surviving/active benchmark equities from 2025 to 2026. Point-in-time delisted coverage is limited to local registry.",
            "execution_model": "D+1 Open entry price; 20 bps round-trip slippage/commissions deducted; PositionScaleOutTracker conservative stop-first resolution.",
            "data_provenance": "Local date-partitioned parquet cache in data/cache/by_date.",
        }
    }

    # Save artifacts
    outputs_dir = os.path.join(PROJECT_ROOT, "outputs")
    os.makedirs(outputs_dir, exist_ok=True)

    json_path = os.path.join(outputs_dir, "production_backtest_summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logger.info(f"Saved production backtest summary to {json_path}")

    csv_path = os.path.join(outputs_dir, "production_backtest_trades.csv")
    if executed_trades:
        df_trades = pd.DataFrame(executed_trades)
        df_trades.to_csv(csv_path, index=False)
        logger.info(f"Saved {len(executed_trades)} executed backtest trades to {csv_path}")

    return summary


if __name__ == "__main__":
    res = run_pipeline_backtest()
    print("\n=======================================================")
    print("BACKTEST VALIDATION RUN COMPLETE")
    print("=======================================================")
    print(f"Edge Status:      {res['metadata']['edge_status']}")
    print(f"Total Trades:     {res['combined_all_stats']['trade_count']}")
    print(f"In-Sample Trades: {res['in_sample_train_stats']['trade_count']} | Win Rate: {res['in_sample_train_stats']['win_rate_pct']}% | Net Exp: {res['in_sample_train_stats']['net_expectancy_pct']}%")
    print(f"Out-of-Sample:    {res['out_of_sample_test_stats']['trade_count']} | Win Rate: {res['out_of_sample_test_stats']['win_rate_pct']}% | Net Exp: {res['out_of_sample_test_stats']['net_expectancy_pct']}% (95% CI: {res['out_of_sample_test_stats']['win_rate_ci_95']})")
    print("=======================================================")
