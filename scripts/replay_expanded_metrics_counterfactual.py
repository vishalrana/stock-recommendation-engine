"""
Fast Controlled Counterfactual Replay Experiment
===============================================
Compares Run A (Old 515 Metrics) vs Run B (Expanded 2,726 Genuine Metrics)
on the exact Point-in-Time market state: 2026-10-02 EOD.

Zero production writes. Pure analytical audit.
"""

import os
import sys
import time
import json
import logging
from typing import Dict, List, Set, Tuple
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.data.cache_manager import get_cache_manager
from src.universe.us_equities import USEquitiesUniverseProvider
from src.universe.filters import evaluate_point_in_time_liquidity
from src.indicators import calculate_indicators
from src.entry_location import evaluate_entry_location
from src.ranker import (
    SignalRanker,
    assign_tier,
    compute_momentum_score,
    compute_expectancy_score,
    compute_regime_alignment,
    compute_context_score,
    validate_candidate_features,
    normalize_strategy_key,
)
from src.strategies.target_calculator import calculate_targets
from src.quant_config import (
    STRATEGY_STOP_CONFIG,
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
)

# Active strategies in Bull regime
from jobs.strategies.pullback import PullbackRecoveryStrategy
from jobs.strategies.trend_following import TrendFollowingStrategy
from jobs.strategies.week_52_high import Week52HighStrategy
from jobs.strategies.pead import PEADStrategy
from jobs.strategies.cross_sectional import CrossSectionalMomentumStrategy
from jobs.strategies.sector_rotation import SectorRotationStrategy, SECTOR_ETFS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


def evaluate_candidate_pool(
    signals: List[dict],
    metrics_map: dict,
    cache_manager,
    regime_str: str = "bull",
) -> Tuple[List[dict], dict]:
    """Score candidates under given metrics_map and evaluate Entry Location."""
    ranker = SignalRanker()
    scored = []

    for raw_sig in signals:
        sig = dict(raw_sig)
        t = sig["ticker"].upper()
        strat_name = sig.get("strategy", "Trend Following")
        strat_key = normalize_strategy_key(strat_name)

        # 1. Momentum Score
        if "momentum_score" not in sig or sig["momentum_score"] is None:
            sig["momentum_score"] = compute_momentum_score(sig)

        # 2. Historical Win Rate Score
        # Check if candidate has metrics in map
        m_entry = metrics_map.get(t, {})
        w_val = m_entry.get("win_rate")
        if w_val is not None:
            sig["winrate_score"] = float(w_val)
            sig["win_rate"] = float(w_val)
        else:
            sig["winrate_score"] = 0.0
            sig["win_rate"] = 0.0

        # 3. Strategy Expectancy Score
        sig["expectancy_score"] = compute_expectancy_score(strat_key)

        # 4. Regime Score
        sig["regime_score"] = compute_regime_alignment(strat_key, regime_str)

        # 5. Context Score
        # Known context for AME (65.0) and VTRS (55.0); neutral 50.0 for others
        c_score = 50.0
        if t == "AME":
            c_score = 65.0
        elif t == "VTRS":
            c_score = 55.0
        sig["context_score"] = c_score

        # Composite score calculation
        w = STRATEGY_WEIGHT_VECTORS.get(strat_key, STRATEGY_WEIGHT_VECTORS["trend_following"])
        composite_score = (
            w["mom"] * sig["momentum_score"]
            + w["exp"] * sig["expectancy_score"]
            + w["wr"] * sig["winrate_score"]
            + w["reg"] * sig["regime_score"]
            + w["ctx"] * sig["context_score"]
        )
        sig["composite_score"] = round(float(composite_score), 4)

        # Target and Stop calculation
        entry_price = float(sig.get("entry_price") or sig.get("price", 1.0))
        atr = float(sig.get("atr_14", 1.0))
        stop_loss = float(sig.get("stop_loss", entry_price * 0.95))

        if composite_score >= 65.0:
            calc_res = calculate_targets(
                ticker=t,
                entry_price=entry_price,
                atr_14=atr,
                stop_loss=stop_loss,
                strategy_name=strat_name,
                price_df=cache_manager.get_ticker_history(t, "2024-01-01", "2026-10-02"),
                sector=sig.get("industry", "Unknown"),
            )
            sig["weighted_rr_honest"] = calc_res.weighted_rr_honest
            sig["tier_label"] = assign_tier(sig["composite_score"], calc_res.weighted_rr_honest)
        else:
            sig["weighted_rr_honest"] = 1.4
            sig["tier_label"] = "Rejected"

        scored.append(sig)

    scored.sort(key=lambda x: x["composite_score"], reverse=True)

    # Brackets
    strong_buy = [c for c in scored if c["composite_score"] >= 80.0]
    buy = [c for c in scored if 65.0 <= c["composite_score"] < 80.0]
    watch = [c for c in scored if 60.0 <= c["composite_score"] < 65.0]
    sub_60 = [c for c in scored if c["composite_score"] < 60.0]

    # Entry Location on all >= 65.0 candidates
    qualifying = [c for c in scored if c["composite_score"] >= 65.0]
    el_results = []
    active_recs = []

    seen = set()
    for c in qualifying:
        t = c["ticker"].upper()
        if t in seen:
            continue
        seen.add(t)

        df = cache_manager.get_ticker_history(t, "2024-01-01", "2026-10-02")
        loc_res = evaluate_entry_location(c, df, c["strategy"])
        rec = {
            "ticker": t,
            "strategy": c["strategy"],
            "score": c["composite_score"],
            "tier": c["tier_label"],
            "entry_state": loc_res.state,
            "entry_reason": loc_res.reason,
            "price": c["price"],
            "support": loc_res.support_level,
            "resistance": loc_res.resistance_level,
            "win_rate": c["win_rate"],
        }
        el_results.append(rec)
        if loc_res.state == "BUY":
            active_recs.append(rec)

    stats = {
        "total_candidates": len(scored),
        "strong_buy_count": len(strong_buy),
        "buy_count": len(buy),
        "watch_count": len(watch),
        "sub_60_count": len(sub_60),
        "tier_qualifying_count": len(qualifying),
        "entry_location_total": len(el_results),
        "entry_location_buy_count": len([r for r in el_results if r["entry_state"] == "BUY"]),
        "entry_location_wait_count": len([r for r in el_results if r["entry_state"] == "WAIT"]),
        "entry_location_reject_count": len([r for r in el_results if r["entry_state"] == "REJECT"]),
        "final_recommendations_count": len(active_recs),
        "entry_location_results": el_results,
        "final_recommendations": active_recs,
        "top_15": [
            {
                "ticker": c["ticker"],
                "strategy": c["strategy"],
                "score": c["composite_score"],
                "tier": c["tier_label"],
                "win_rate": c["win_rate"],
                "momentum": c.get("momentum_score"),
            }
            for c in scored[:15]
        ],
    }
    return scored, stats


def main():
    t_start = time.time()
    logger.info("Initializing CacheManager and preloading history up to 2026-10-02...")
    cm = get_cache_manager()
    cm.preload_history("2024-01-01", "2026-10-02")

    provider = USEquitiesUniverseProvider()
    records = provider.get_universe()
    record_map = {r.data_provider_ticker: r for r in records}

    logger.info("Filtering operational discovery universe (>=60 bars PIT on 2026-10-02)...")
    operational_tickers = []
    for r in records:
        t = r.data_provider_ticker
        df = cm.get_ticker_history(t, "2024-01-01", "2026-10-02")
        if df is not None and not df.empty:
            is_op, _, _ = evaluate_point_in_time_liquidity(df, as_of_date="2026-10-02", min_history_days=60)
            if is_op:
                operational_tickers.append(t)

    logger.info(f"Operational discovery universe: {len(operational_tickers)} tickers.")

    # 1. Pre-screen Cross-Sectional Momentum top 15% with NaN guard
    cs_returns = []
    for t in operational_tickers:
        df = cm.get_ticker_history(t, "2026-05-01", "2026-10-02")
        if df is not None and len(df) >= 63:
            p_now = float(df["CLOSE"].iloc[-1])
            p_63 = float(df["CLOSE"].iloc[-63])
            if p_63 > 0:
                ret = (p_now / p_63 - 1.0) * 100.0
                if not np.isnan(ret):
                    cs_returns.append((t, ret))
    cs_returns.sort(key=lambda x: x[1], reverse=True)
    top_15pct_n = max(1, int(len(cs_returns) * 0.15))
    cs_universe = set([x[0] for x in cs_returns[:top_15pct_n]])
    logger.info(f"[CROSS-SECTIONAL] Top 15% pool size: {len(cs_universe)} (Cutoff return: {cs_returns[top_15pct_n-1][1]:.2f}%)")

    # 2. Fast pre-filter and indicator calculation
    logger.info("Running fast pre-filter and indicator calculation across operational universe...")
    strategies = [
        PullbackRecoveryStrategy(),
        TrendFollowingStrategy(),
        Week52HighStrategy(),
        PEADStrategy(),
        CrossSectionalMomentumStrategy(),
    ]

    all_raw_signals = []
    evaluated_count = 0
    t_calc = time.time()

    for idx, t in enumerate(operational_tickers, 1):
        raw = cm.get_ticker_history(t, "2024-01-01", "2026-10-02")
        if raw is None or len(raw) < 60:
            continue

        c = float(raw["CLOSE"].iloc[-1])
        h20 = float(raw["HIGH"].iloc[-20:].max())
        h52 = float(raw["HIGH"].iloc[-252:].max()) if len(raw) >= 252 else h20
        m50 = float(raw["CLOSE"].iloc[-50:].mean()) if len(raw) >= 50 else c
        m200 = float(raw["CLOSE"].iloc[-200:].mean()) if len(raw) >= 200 else m50

        # Fast pre-check: could this stock trigger any strategy?
        # - Pullback: c > m50 > m200
        # - Trend Following: c > m200 * 1.02 and (c/h20 - 1) >= -0.05
        # - 52W High: (c/h52 - 1) >= -0.05 and c > m50
        # - Cross-Sectional: t in cs_universe
        could_pullback = (c > m50 > m200)
        could_trend = (c > m200 * 1.02) and ((c / h20 - 1.0) >= -0.05)
        could_52w = ((c / h52 - 1.0) >= -0.05) and (c > m50)
        is_cs = (t in cs_universe)

        if not (could_pullback or could_trend or could_52w or is_cs):
            continue

        # Stock is a viable candidate; compute indicators
        df_ind = calculate_indicators(raw).sort_index()
        evaluated_count += 1

        rec = record_map.get(t)
        ind = rec.sector if rec and rec.sector and rec.sector != "Unknown" else (rec.industry if rec else "Unknown")
        dummy_metrics = {"win_rate": 50.0, "total_trades": 10, "expectancy_pct": 1.5, "industry": ind, "company_name": rec.company_name if rec else t}

        # Test strategies
        for strat in strategies:
            if strat.name == "Cross-Sectional Momentum" and not is_cs:
                continue
            try:
                sig = strat.scan(t, df_ind, "bull", dummy_metrics)
                if sig is not None:
                    sig["atr_14"] = float(df_ind["ATR_14"].iloc[-1]) if "ATR_14" in df_ind.columns else 0.0
                    all_raw_signals.append(sig)
            except Exception:
                pass

        if evaluated_count % 200 == 0:
            logger.info(f"Evaluated {evaluated_count} viable tickers (processed {idx}/{len(operational_tickers)})...")

    # Add Sector Rotation ETFs
    etf_strat = SectorRotationStrategy()
    for etf in SECTOR_ETFS.keys():
        raw = cm.get_ticker_history(etf, "2024-01-01", "2026-10-02")
        if raw is not None and len(raw) >= 60:
            df_ind = calculate_indicators(raw).sort_index()
            sig = etf_strat.scan(etf, df_ind, "bull", {"win_rate": 50.0, "total_trades": 10, "expectancy_pct": 1.5, "company_name": etf, "industry": "ETF"})
            if sig is not None:
                sig["atr_14"] = float(df_ind["ATR_14"].iloc[-1]) if "ATR_14" in df_ind.columns else 0.0
                all_raw_signals.append(sig)

    calc_time = time.time() - t_calc
    logger.info(f"Generated {len(all_raw_signals)} total candidate signals from {evaluated_count} viable tickers in {calc_time:.2f}s.")

    # 3. Load Metrics
    with open(os.path.join(PROJECT_ROOT, "data", "cache", "ticker_metrics_cache.json"), "r") as f:
        old_metrics_map = json.load(f)["metrics"]

    with open(os.path.join(PROJECT_ROOT, "data", "cache", "universe", "expanded_ticker_metrics.json"), "r") as f:
        expanded_metrics_map = json.load(f)["metrics"]

    # 4. Run Evaluation under Run A (Old Metrics)
    logger.info("\nEvaluating Run A (Old Metrics - 515 S&P 500 tickers)...")
    _, stats_a = evaluate_candidate_pool(all_raw_signals, old_metrics_map, cm, "bull")

    # 5. Run Evaluation under Run B (Expanded Genuine Metrics - 2,726 tickers)
    logger.info("\nEvaluating Run B (Expanded Genuine PIT Metrics - 2,726 tickers)...")
    _, stats_b = evaluate_candidate_pool(all_raw_signals, expanded_metrics_map, cm, "bull")

    # 6. Save Comparative Output
    summary_path = os.path.join(PROJECT_ROOT, "docs", "expanded_universe_metrics_summary.json")
    full_summary = {
        "as_of_date": "2026-10-02",
        "market_regime": "bull",
        "operational_discovery_universe": len(operational_tickers),
        "viable_tickers_evaluated": evaluated_count,
        "total_raw_strategy_signals": len(all_raw_signals),
        "run_a_old_metrics": stats_a,
        "run_b_expanded_metrics": stats_b,
        "delta": {
            "additional_qualifying_tier_ge_65": stats_b["tier_qualifying_count"] - stats_a["tier_qualifying_count"],
            "additional_strong_buy_ge_80": stats_b["strong_buy_count"] - stats_a["strong_buy_count"],
            "additional_buy_65_to_80": stats_b["buy_count"] - stats_a["buy_count"],
            "entry_location_buy_delta": stats_b["entry_location_buy_count"] - stats_a["entry_location_buy_count"],
            "entry_location_wait_delta": stats_b["entry_location_wait_count"] - stats_a["entry_location_wait_count"],
            "final_recommendations_delta": stats_b["final_recommendations_count"] - stats_a["final_recommendations_count"],
        },
        "total_elapsed_seconds": round(time.time() - t_start, 2),
    }

    with open(summary_path, "w") as f:
        json.dump(full_summary, f, indent=2)

    logger.info(f"Saved complete comparative analysis to {summary_path}")

    # Print Side-by-Side Table
    print("\n" + "=" * 90)
    print("COUNTERFACTUAL REPLAY RESULTS: 2026-10-02 POINT-IN-TIME MARKET STATE")
    print("=" * 90)
    print(f"{'Metric':<40} | {'Run A (Old Metrics)':<20} | {'Run B (Expanded Metrics)':<25}")
    print("-" * 90)
    print(f"{'Metric Records Available':<40} | {len(old_metrics_map):<20} | {len(expanded_metrics_map):<25}")
    print(f"{'Total Raw Strategy Candidates':<40} | {stats_a['total_candidates']:<20} | {stats_b['total_candidates']:<25}")
    print(f"{'Strong Buy (Score >= 80.0)':<40} | {stats_a['strong_buy_count']:<20} | {stats_b['strong_buy_count']:<25}")
    print(f"{'Buy (Score 65.0 - 79.99)':<40} | {stats_a['buy_count']:<20} | {stats_b['buy_count']:<25}")
    print(f"{'Watch (Score 60.0 - 64.99)':<40} | {stats_a['watch_count']:<20} | {stats_b['watch_count']:<25}")
    print(f"{'Sub-60 (Score < 60.0)':<40} | {stats_a['sub_60_count']:<20} | {stats_b['sub_60_count']:<25}")
    print(f"{'Candidates Reaching Entry Location':<40} | {stats_a['entry_location_total']:<20} | {stats_b['entry_location_total']:<25}")
    print(f"{'  -> Entry Location: BUY':<40} | {stats_a['entry_location_buy_count']:<20} | {stats_b['entry_location_buy_count']:<25}")
    print(f"{'  -> Entry Location: WAIT':<40} | {stats_a['entry_location_wait_count']:<20} | {stats_b['entry_location_wait_count']:<25}")
    print(f"{'  -> Entry Location: REJECT':<40} | {stats_a['entry_location_reject_count']:<20} | {stats_b['entry_location_reject_count']:<25}")
    print(f"{'Final Active Recommendations':<40} | {stats_a['final_recommendations_count']:<20} | {stats_b['final_recommendations_count']:<25}")
    print("=" * 90)

    if stats_b["entry_location_results"]:
        print("\nAll Qualifying Candidates in Run B (Score >= 65.0):")
        for r in stats_b["entry_location_results"]:
            print(f"- {r['ticker']} ({r['strategy']}): Score={r['score']:.2f}, WinRate={r['win_rate']:.1f}%, State={r['entry_state']} | {r['entry_reason']}")


if __name__ == "__main__":
    main()
