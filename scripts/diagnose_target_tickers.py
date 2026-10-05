"""
Phase 20 Diagnostics on 9 Target Tickers
========================================
Detailed evaluation for:
NVDA, ORCL, AME, FIVN, OGN, ANF, IFF, LQDT, BFLY

Records:
- Strategy
- Setup qualification
- Momentum
- Expectancy
- Win rate (raw & Bayesian shrunk)
- Regime
- Context
- Score
- Tier
- Earnings result
- Entry Location
- Stop
- Targets
- R:R
- Reach probabilities
- Final recommendation (Yes/No + exact reason)
"""

import os
import sys
import datetime
import json
import pandas as pd
import yfinance as yf

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
src_dir = os.path.join(PROJECT_ROOT, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
jobs_dir = os.path.join(PROJECT_ROOT, "jobs")
if jobs_dir not in sys.path:
    sys.path.insert(0, jobs_dir)

from indicators import calculate_indicators
from regime import get_regime
from jobs.strategies import STRATEGIES
from src.ranker import (
    SignalRanker,
    compute_momentum_score,
    compute_expectancy_score,
    compute_regime_alignment,
    compute_context_score,
    assign_tier,
    normalize_strategy_key,
)
from src.quant_config import STRATEGY_WEIGHT_VECTORS, REGIME_SCORE_MATRIX
from src.strategies.target_calculator import calculate_targets
from src.entry_location import evaluate_entry_location
from src.filters.earnings_filter import (
    earnings_risk_filter,
    resolve_ticker_earnings,
    fetch_earnings_calendar,
    EarningsStatus,
)
from src.utils.metrics_pipeline import build_hardened_metrics
from src.data.cache_manager import get_cache_manager

TARGET_TICKERS = ["NVDA", "ORCL", "AME", "FIVN", "OGN", "ANF", "IFF", "LQDT", "BFLY"]


def run_diagnostics():
    today = datetime.date.today()
    print(f"Running Phase 20 Diagnostics as of {today} on {len(TARGET_TICKERS)} tickers...")

    reg_data = get_regime()
    regime_str = reg_data.get("regime", "bull") if isinstance(reg_data, dict) else str(reg_data)
    print(f"Current Market Regime: {regime_str.upper()}")

    cache_mgr = get_cache_manager()
    ranker = SignalRanker()

    results = []

    for ticker in TARGET_TICKERS:
        print(f"\n--- Evaluating {ticker} ---")
        # 1. Fetch OHLCV data
        raw_df = cache_mgr.get_ticker_history(ticker, (today - datetime.timedelta(days=400)).isoformat(), today.isoformat())
        if raw_df is None or len(raw_df) < 50:
            print(f"Downloading from yfinance for {ticker}...")
            raw_df = yf.download(ticker, period="1y", auto_adjust=True, progress=False)
            if isinstance(raw_df.columns, pd.MultiIndex):
                raw_df.columns = raw_df.columns.get_level_values(0)
            raw_df.columns = [c.upper() for c in raw_df.columns]

        if raw_df is None or len(raw_df) < 50:
            print(f"Insufficient data for {ticker}")
            results.append({
                "ticker": ticker,
                "strategy_status": "NO_SETUP",
                "reason": "Insufficient historical data",
                "composite_score": "N/A",
                "tier": "NO_SETUP",
                "final_recommendation": "NO (Insufficient historical data)",
            })
            continue

        df = calculate_indicators(raw_df.copy()).sort_index()

        # 2. Check each strategy for qualification
        qualified_setups = []
        for strat in STRATEGIES:
            try:
                sig = strat.scan(ticker, df, regime_str, {})
                if sig is not None:
                    sig["strategy_instance"] = strat
                    qualified_setups.append(sig)
            except Exception as e:
                pass

        if not qualified_setups:
            print(f"[-] {ticker}: No strategy setup qualified.")
            results.append({
                "ticker": ticker,
                "strategy": "N/A",
                "strategy_status": "NO_SETUP",
                "setup_qualification": False,
                "momentum": "N/A",
                "expectancy": "N/A",
                "win_rate_raw": "N/A",
                "win_rate_shrunk": "N/A",
                "regime": regime_str,
                "context": "N/A",
                "composite_score": "N/A",
                "tier": "NO_SETUP",
                "earnings_result": "N/A",
                "entry_location": "N/A",
                "stop": "N/A",
                "targets": "N/A",
                "rr": "N/A",
                "reach_prob": "N/A",
                "final_recommendation": "NO (No valid strategy setup qualified)",
            })
            continue

        # For qualified setups, evaluate each
        for sig in qualified_setups:
            strat_name = sig["strategy"]
            print(f"[+] {ticker}: Setup qualified for '{strat_name}'")

            # Momentum
            try:
                mom_score = compute_momentum_score(sig)
            except Exception:
                mom_score = 50.0

            # Bayesian shrunk win rate & expectancy
            hardened = build_hardened_metrics(
                ticker=ticker,
                raw_record=None,
                strategy_name=strat_name,
                strategy_win_rate=sig.get("strategy_win_rate"),
                past_win_rate=sig.get("past_win_rate"),
            )
            shrunk_wr = hardened["shrunk_win_rate"]
            raw_wr = hardened["raw_win_rate"]
            shrunk_exp = hardened["shrunk_expectancy"]
            raw_exp = hardened["raw_expectancy"]

            exp_score = compute_expectancy_score(strat_name, adjusted_expectancy_pct=shrunk_exp)
            reg_score = compute_regime_alignment(strat_name, regime_str)
            ctx_score = 0.0  # earnings decoupled base context

            # Canonical composite score
            row_for_ranker = {
                "ticker": ticker,
                "strategy": strat_name,
                "momentum_score": mom_score,
                "winrate_score": shrunk_wr,
                "win_rate": shrunk_wr,
                "expectancy_score": exp_score,
                "expectancy_pct": shrunk_exp,
                "context_score": ctx_score,
            }
            comp_res = ranker.compute_composite_score(row_for_ranker, regime_str)
            comp_score = comp_res["total"]
            initial_tier = assign_tier(comp_score, has_strategy_setup=True)

            # Earnings gate
            cal_map = {}
            resolve_ticker_earnings(ticker, cal_map, allow_network=True)
            er_res = earnings_risk_filter(
                ticker=ticker,
                scan_date=today,
                strategy=strat_name,
                earnings_calendar=cal_map,
                allow_unknown_date=True,
            )

            # Stop & targets
            entry_p = float(sig["entry_price"])
            raw_stop = float(sig["stop_loss"])
            min_stop = round(entry_p * 0.93, 2)
            stop_p = max(raw_stop, min_stop)
            atr_val = float(sig.get("atr_14", entry_p * 0.02))

            t_res = calculate_targets(
                ticker=ticker,
                entry_price=entry_p,
                atr_14=atr_val,
                stop_loss=stop_p,
                strategy_name=strat_name,
                price_df=df,
            )

            # Entry location
            loc_res = evaluate_entry_location(sig, df, strat_name)

            # Final recommendation decision
            rec_yes = False
            rec_reason = ""

            if comp_score < 65.0:
                rec_yes = False
                rec_reason = f"Rejected: Score {comp_score:.1f} < 65.0 threshold"
            elif not er_res.get("pass", True):
                rec_yes = False
                rec_reason = f"Rejected: Earnings gate failed ({er_res.get('reason')})"
            elif loc_res.state == "WAIT":
                rec_yes = False
                rec_reason = f"Rejected: Entry Location WAIT ({loc_res.reason})"
            elif loc_res.state == "REJECT":
                rec_yes = False
                rec_reason = f"Rejected: Entry Location REJECT ({loc_res.reason})"
            else:
                rec_yes = True
                rec_reason = f"QUALIFIED: {initial_tier} (Score {comp_score:.1f}, Entry BUY)"

            results.append({
                "ticker": ticker,
                "strategy": strat_name,
                "strategy_status": "QUALIFIED",
                "setup_qualification": True,
                "momentum": f"{mom_score:.1f}",
                "expectancy": f"raw={f'{raw_exp:.2f}%' if raw_exp is not None else 'N/A'}, shrunk={shrunk_exp:.2f}% (score={exp_score:.1f})",
                "win_rate_raw": f"{raw_wr:.1f}%" if raw_wr is not None else "N/A",
                "win_rate_shrunk": f"{shrunk_wr:.1f}%",
                "regime": f"{regime_str} (score={reg_score:.1f})",
                "context": f"{ctx_score:.1f}",
                "composite_score": f"{comp_score:.1f}",
                "tier": initial_tier,
                "earnings_result": "PASS" if er_res.get("pass") else f"FAIL ({er_res.get('reason_code')})",
                "entry_location": f"{loc_res.state} ({loc_res.reason})",
                "stop": f"${stop_p:.2f}",
                "targets": f"T1={f'${t_res.target_1:.2f}' if t_res.target_1 is not None else 'None (Trailing)'}, T2={f'${t_res.target_2:.2f}' if t_res.target_2 is not None else 'None'}, T3={f'${t_res.target_3:.2f}' if t_res.target_3 is not None else 'None'}",
                "rr": f"{t_res.weighted_rr_honest:.2f}" if t_res.weighted_rr_honest is not None else "N/A",
                "reach_prob": f"P(T1)={f'{t_res.reach_prob_t1:.2f}' if t_res.reach_prob_t1 is not None else 'N/A'}, P(T2)={f'{t_res.reach_prob_t2:.2f}' if t_res.reach_prob_t2 is not None else 'N/A'}, P(T3)={f'{t_res.reach_prob_t3:.2f}' if t_res.reach_prob_t3 is not None else 'N/A'}",
                "final_recommendation": f"{'YES' if rec_yes else 'NO'} - {rec_reason}",
            })

    # Print summary table
    print("\n" + "=" * 90)
    print("PHASE 20: TARGET TICKER DIAGNOSTICS SUMMARY")
    print("=" * 90)
    for r in results:
        print(f"\nTicker: {r['ticker']}")
        for k, v in r.items():
            if k != "ticker":
                print(f"  {k}: {v}")

    # Save to JSON for report integration
    out_path = os.path.join(PROJECT_ROOT, "docs", "TARGET_TICKERS_DIAGNOSTICS.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved full diagnostics report to {out_path}")


if __name__ == "__main__":
    run_diagnostics()
