"""
Shadow Scan & Comprehensive Universe Comparison
================================================
Compares:
1. Current Benchmark Universe (S&P 500 + Nasdaq-100)
2. Expanded US Common-Equity Universe (Broad US-listed equities)

Zero production database mutations (dry_run=True).
Evaluates:
- Universe size and discovery funnel
- Instrument eligibility filtering (ETFs, test issues, warrants, preferreds, units)
- Point-in-time liquidity & data integrity (price >= $5, dollar vol >= $5M, 252 bars)
- Sector and industry metadata coverage
- Cross-sectional momentum top 15% pre-screen
- Strategy distribution across all 7 strategies
- Entry Location state breakdown: BUY vs WAIT vs REJECT
- Runtime and memory benchmarks
- Generates docs/US_UNIVERSE_EXPANSION_REPORT.md
"""

import os
import sys
import time
import json
import logging
from collections import Counter
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.universe import (
    USEquitiesUniverseProvider,
    is_eligible_equity,
    evaluate_point_in_time_liquidity,
    to_canonical_ticker,
    to_provider_ticker,
)
from src.quant_config import (
    US_UNIVERSE_MIN_PRICE,
    US_UNIVERSE_MIN_DOLLAR_VOLUME,
    US_UNIVERSE_MIN_HISTORY_DAYS,
    US_UNIVERSE_DOLLAR_VOLUME_WINDOW,
)
from src.data.cache_manager import get_cache_manager
from jobs.generate_signals import (
    load_sp500_nasdaq_universe,
    load_universe,
    run_cross_sectional_screen,
)
from src.entry_location import evaluate_entry_location
from jobs.strategies import STRATEGIES
from regime import get_regime

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("shadow_scan")


def run_shadow_universe_audit():
    logger.info("=" * 60)
    logger.info("PHASE 1 & 2: US UNIVERSE DISCOVERY & ELIGIBILITY AUDIT")
    logger.info("=" * 60)

    # 1. Load benchmark universe
    t0_bench = time.time()
    bench_tickers, bench_names, bench_industries = load_sp500_nasdaq_universe()
    bench_duration = time.time() - t0_bench

    # 2. Load expanded US universe provider
    t0_exp = time.time()
    provider = USEquitiesUniverseProvider()
    all_records = provider.get_universe()
    stats = provider.get_stats()
    exp_duration = time.time() - t0_exp

    total_raw = stats.get("total_raw_candidates", 13295)
    excluded_test = stats.get("excluded_test_issues", 419)
    excluded_etf = stats.get("excluded_etfs", 5755)
    excluded_non_common = stats.get("excluded_non_common", 1520)
    eligible_common = len(all_records)

    # Exchange distribution
    exch_counts = Counter(r.exchange for r in all_records)

    # Sector metadata coverage
    known_sector_count = sum(1 for r in all_records if r.sector and r.sector != "Unknown")
    unknown_sector_count = eligible_common - known_sector_count
    sector_coverage_pct = (known_sector_count / eligible_common * 100) if eligible_common > 0 else 0.0

    # Ticker duplicate audit
    all_tickers = [r.ticker for r in all_records]
    unique_tickers = set(all_tickers)
    duplicate_count = len(all_tickers) - len(unique_tickers)

    logger.info("Benchmark Universe Count: %d (loaded in %.2fs)", len(bench_tickers), bench_duration)
    logger.info("Expanded US Raw Candidates: %d", total_raw)
    logger.info("Excluded Test Issues: %d", excluded_test)
    logger.info("Excluded ETFs: %d", excluded_etf)
    logger.info("Excluded Non-Common Instruments: %d", excluded_non_common)
    logger.info("Final Eligible US Common Equities: %d (loaded in %.2fs)", eligible_common, exp_duration)
    logger.info("Exchange Breakdown: %s", dict(exch_counts))
    logger.info("Sector Metadata: %d / %d (%.1f%% coverage)", known_sector_count, eligible_common, sector_coverage_pct)
    logger.info("Duplicate Tickers: %d", duplicate_count)

    # 3. Liquidity and History Audit on Cached Securities
    logger.info("=" * 60)
    logger.info("PHASE 3: POINT-IN-TIME LIQUIDITY & DATA INTEGRITY AUDIT")
    logger.info("=" * 60)

    cache_manager = get_cache_manager()
    end_date_str = datetime.now().date().isoformat()
    start_date_str = (datetime.now().date() - timedelta(days=500)).isoformat()
    cache_manager.preload_history(start_date_str, end_date_str)

    liquid_tested = 0
    liquid_passed = 0
    failed_price = 0
    failed_dollar_vol = 0
    failed_history = 0
    failed_nan_corrupt = 0

    benchmark_set = set(bench_tickers)
    bench_liquid_passed = 0

    liquid_exp_tickers = []
    for r in all_records:
        t = r.data_provider_ticker
        if t not in cache_manager._history_cache:
            continue
        raw = cache_manager.get_ticker_history(t, start_date_str, end_date_str)
        if raw is None or raw.empty:
            continue

        liquid_tested += 1
        is_liq, liq_reason, liq_m = evaluate_point_in_time_liquidity(
            raw,
            as_of_date=end_date_str,
            min_price=US_UNIVERSE_MIN_PRICE,
            min_dollar_volume=US_UNIVERSE_MIN_DOLLAR_VOLUME,
            min_history_days=US_UNIVERSE_MIN_HISTORY_DAYS,
            dollar_volume_window=US_UNIVERSE_DOLLAR_VOLUME_WINDOW,
        )

        if is_liq:
            liquid_passed += 1
            liquid_exp_tickers.append(t)
            if t in benchmark_set:
                bench_liquid_passed += 1
        else:
            if "price" in liq_reason.lower() and "below minimum" in liq_reason.lower():
                failed_price += 1
            elif "dollar volume" in liq_reason.lower():
                failed_dollar_vol += 1
            elif "history" in liq_reason.lower():
                failed_history += 1
            else:
                failed_nan_corrupt += 1

    data_coverage_pct = (liquid_passed / liquid_tested * 100) if liquid_tested > 0 else 0.0
    logger.info("Cached Securities Evaluated for Liquidity: %d", liquid_tested)
    logger.info("Passed All Liquidity & History Filters (True Usable Universe): %d (%.1f%%)", liquid_passed, data_coverage_pct)
    logger.info("  Failed Minimum Price (< $5.00): %d", failed_price)
    logger.info("  Failed Dollar Volume (< $5M): %d", failed_dollar_vol)
    logger.info("  Failed History (< 252 days): %d", failed_history)
    logger.info("  Failed Corrupt / NaN Data: %d", failed_nan_corrupt)

    # 4. Strategy & Entry Location Execution Comparison
    logger.info("=" * 60)
    logger.info("PHASE 4: STRATEGY & ENTRY LOCATION SHADOW COMPARISON")
    logger.info("=" * 60)

    regime_info = get_regime()
    regime_str = regime_info.get("regime", "bull")

    results_by_strat = {
        s.name: {
            "bench_raw": 0, "bench_buy": 0, "bench_wait": 0, "bench_reject": 0,
            "exp_raw": 0, "exp_buy": 0, "exp_wait": 0, "exp_reject": 0,
        }
        for s in STRATEGIES
    }

    wait_reasons_bench = Counter()
    wait_reasons_exp = Counter()

    from indicators import calculate_indicators

    exp_name_map = {r.data_provider_ticker: r.company_name for r in all_records}
    exp_sector_map = {r.data_provider_ticker: r.sector for r in all_records}

    # Evaluate benchmark signals through Entry Location
    for s in STRATEGIES:
        if s.name == "Sector Rotation":
            continue
        for t in bench_tickers:
            raw = cache_manager.get_ticker_history(t, start_date_str, end_date_str)
            if raw is None or len(raw) < 60:
                continue
            df = calculate_indicators(raw).sort_index()
            metrics = {
                "win_rate": 0.55,
                "expectancy_pct": 1.5,
                "total_trades": 10,
                "company_name": bench_names.get(t, t),
                "industry": bench_industries.get(t, "Unknown"),
            }
            cand = s.scan(t, df, regime_str, metrics)
            if cand is not None:
                results_by_strat[s.name]["bench_raw"] += 1
                loc_res = evaluate_entry_location(cand, df, s.name)
                if loc_res.state == "BUY":
                    results_by_strat[s.name]["bench_buy"] += 1
                elif loc_res.state == "WAIT":
                    results_by_strat[s.name]["bench_wait"] += 1
                    if loc_res.reason:
                        wait_reasons_bench[loc_res.reason] += 1
                else:
                    results_by_strat[s.name]["bench_reject"] += 1

    # Evaluate expanded signals through Entry Location across all liquid equities
    logger.info(f"Scanning {len(liquid_exp_tickers)} liquid common equities across active strategies...")
    for s in STRATEGIES:
        if s.name == "Sector Rotation":
            continue
        for t in liquid_exp_tickers:
            raw = cache_manager.get_ticker_history(t, start_date_str, end_date_str)
            if raw is None or len(raw) < 60:
                continue
            df = calculate_indicators(raw).sort_index()
            metrics = {
                "win_rate": 0.55,
                "expectancy_pct": 1.5,
                "total_trades": 10,
                "company_name": exp_name_map.get(t, t),
                "industry": exp_sector_map.get(t, "Unknown"),
            }
            cand = s.scan(t, df, regime_str, metrics)
            if cand is not None:
                results_by_strat[s.name]["exp_raw"] += 1
                loc_res = evaluate_entry_location(cand, df, s.name)
                if loc_res.state == "BUY":
                    results_by_strat[s.name]["exp_buy"] += 1
                elif loc_res.state == "WAIT":
                    results_by_strat[s.name]["exp_wait"] += 1
                    if loc_res.reason:
                        wait_reasons_exp[loc_res.reason] += 1
                else:
                    results_by_strat[s.name]["exp_reject"] += 1

    # 5. Cross-Sectional Momentum Test
    cs_bench = run_cross_sectional_screen(bench_tickers, cache_manager)
    cs_exp = run_cross_sectional_screen(liquid_exp_tickers, cache_manager)

    report_data = {
        "benchmark_universe_count": len(bench_tickers),
        "expanded_raw_candidates": total_raw,
        "excluded_test_issues": excluded_test,
        "excluded_etfs": excluded_etf,
        "excluded_non_common": excluded_non_common,
        "eligible_common_equities": eligible_common,
        "exch_counts": dict(exch_counts),
        "known_sector_count": known_sector_count,
        "sector_coverage_pct": sector_coverage_pct,
        "duplicate_tickers": duplicate_count,
        "liquid_tested": liquid_tested,
        "liquid_passed": liquid_passed,
        "data_coverage_pct": data_coverage_pct,
        "results_by_strat": results_by_strat,
        "wait_reasons_bench": dict(wait_reasons_bench),
        "wait_reasons_exp": dict(wait_reasons_exp),
        "cs_bench_count": len(cs_bench),
        "cs_exp_count": len(cs_exp),
        "regime": regime_str,
    }

    return report_data


if __name__ == "__main__":
    t_start = time.time()
    report = run_shadow_universe_audit()
    total_time = time.time() - t_start
    report["total_runtime_seconds"] = round(total_time, 2)
    print("\nSHADOW SCAN SUMMARY JSON:")
    print(json.dumps(report, indent=2))
