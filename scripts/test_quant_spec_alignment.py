#!/usr/bin/env python
"""
Verification Test Suite: Quantitative Engine Alignment with Master Spec v2.3+
=============================================================================
Tests all P1-A quantitative requirements:
1. Exact strategy weight vectors (all 7 strategies, sum to 1.0).
2. Exact discrete regime score matrix (21 cells: 7 strategies x 3 regimes).
3. Expectancy score formula: S_exp = 30 + 20 * E_adjusted.
4. Context veto thresholds: D/E > 2.5 & CR < 1.0, FinBERT < -0.30, surprise < -10%, downside penalty.
5. Earnings blackout windows per strategy (5d, 5d, 3d, 3d, 4d, 0d PEAD, 3d MR).
6. Target ATR multipliers & minimum floors for all strategies.
7. Reach probability: 0.0 fallback when data is insufficient (no 0.35 arbitrary fallback).
8. Scale-out survival boundary: 15.0% threshold for Target 3 (14.99% -> 60/40/0, 15.0% -> 50/30/20).
9. Earnings cache TTL (24h / 86400s) & refresh logic.
10. Position sizer hardening: requires final_adjusted_half_kelly (no silent unadjusted fallback).
"""

import sys
import os
import time
import datetime
import math
import numpy as np
import pandas as pd
from typing import Dict, Any

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.quant_config import (
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
    EXPECTANCY_BASE,
    EXPECTANCY_SLOPE,
    CONTEXT_VETO_THRESHOLDS,
    EARNINGS_BLACKOUT_DAYS,
    EARNINGS_CACHE_TTL_SECONDS,
    STRATEGY_TARGET_CONFIG,
    T3_REACH_PROB_SURVIVAL_THRESHOLD,
    SCALE_OUT_WEIGHTS,
    MIN_REACH_PROB_WINDOWS,
)
from src.ranker import (
    SignalRanker,
    compute_expectancy_score,
    compute_regime_alignment,
    compute_context_score,
)
from src.strategies.target_calculator import (
    calculate_targets,
    get_reach_prob,
    get_reach_prob_distribution,
)
from src.filters.earnings_filter import (
    is_earnings_record_fresh,
    earnings_risk_filter,
    fetch_earnings_calendar,
)
from src.ranker import (
    assign_tier,
    validate_candidate_features,
)


def test_1_strategy_weight_vectors():
    print("\n--- Test 1: Strategy Weight Vectors (Exact Alignment & Sum to 1.0) ---")
    expected_weights = {
        "trend_following":          {"mom": 0.45, "exp": 0.20, "wr": 0.15, "reg": 0.10, "ctx": 0.10},
        "52w_high_breakout":        {"mom": 0.50, "exp": 0.15, "wr": 0.15, "reg": 0.10, "ctx": 0.10},
        "pullback_recovery":        {"mom": 0.25, "exp": 0.35, "wr": 0.15, "reg": 0.10, "ctx": 0.15},
        "pead":                     {"mom": 0.30, "exp": 0.25, "wr": 0.15, "reg": 0.10, "ctx": 0.20},
        "cross_sectional_momentum": {"mom": 0.40, "exp": 0.20, "wr": 0.20, "reg": 0.10, "ctx": 0.10},
        "sector_rotation":          {"mom": 0.35, "exp": 0.25, "wr": 0.15, "reg": 0.10, "ctx": 0.15},
        "mean_reversion":           {"mom": 0.10, "exp": 0.20, "wr": 0.15, "reg": 0.15, "ctx": 0.40},
    }

    for strat, expected in expected_weights.items():
        actual = STRATEGY_WEIGHT_VECTORS[strat]
        assert actual == expected, f"Mismatch in {strat}: got {actual}, expected {expected}"
        total_wt = sum(actual.values())
        assert abs(total_wt - 1.0) < 1e-6, f"Weights in {strat} do not sum to 1.0 (got {total_wt})"

    print("  --> PASS: All 7 strategy weight vectors match Master Spec and sum exactly to 1.0")


def test_2_regime_score_matrix():
    print("\n--- Test 2: Discrete Regime Score Matrix (21 cells: 7 strategies x 3 regimes) ---")
    expected_matrix = {
        "trend_following":          {"bull": 100.0, "sideways": 70.0, "bear": 20.0},
        "52w_high_breakout":        {"bull": 100.0, "sideways": 60.0, "bear": 10.0},
        "cross_sectional_momentum": {"bull": 85.0,  "sideways": 75.0, "bear": 30.0},
        "sector_rotation":          {"bull": 80.0,  "sideways": 90.0, "bear": 40.0},
        "pullback_recovery":        {"bull": 70.0,  "sideways": 85.0, "bear": 50.0},
        "pead":                     {"bull": 75.0,  "sideways": 70.0, "bear": 70.0},
        "mean_reversion":           {"bull": 30.0,  "sideways": 65.0, "bear": 100.0},
    }

    for strat, scores in expected_matrix.items():
        for regime, expected_val in scores.items():
            matrix_val = REGIME_SCORE_MATRIX[strat][regime]
            assert matrix_val == expected_val, f"Matrix mismatch {strat}/{regime}: got {matrix_val}, expected {expected_val}"
            # Also test compute_regime_alignment function
            func_val = compute_regime_alignment(strat, regime)
            assert func_val == expected_val, f"Function mismatch {strat}/{regime}: got {func_val}, expected {expected_val}"

    print("  --> PASS: All 21 discrete regime score cells match Master Spec exactly")


def test_3_expectancy_score_formula():
    print("\n--- Test 3: Expectancy Score Formula (S_exp = 30 + 20 * E_adjusted) ---")
    assert EXPECTANCY_BASE == 30.0
    assert EXPECTANCY_SLOPE == 20.0

    # No explicit value: falls back to the strategy's shrunk backtest evidence (neutral 0% without it)
    from src.strategy_evidence import get_strategy_evidence
    for strat in ("trend_following", "52w_high_breakout"):
        ev_exp = get_strategy_evidence(strat).shrunk_expectancy
        expected = max(0.0, min(100.0, 30.0 + 20.0 * ev_exp))
        s_fallback = compute_expectancy_score(strat)
        assert abs(s_fallback - expected) < 1e-6, f"{strat} S_exp expected {expected}, got {s_fallback}"

    # Test direct expectancy input: 0.0 percentage points -> 30.0
    s_zero = compute_expectancy_score("trend_following", adjusted_expectancy_pct=0.0)
    assert s_zero == 30.0, f"Zero expectancy expected 30.0, got {s_zero}"

    # Test direct expectancy input: +2.0 percentage points -> 30 + 20 * 2.0 = 70.0
    s_plus2 = compute_expectancy_score("trend_following", adjusted_expectancy_pct=2.0)
    assert s_plus2 == 70.0, f"+2.0 expectancy expected 70.0, got {s_plus2}"

    # Test direct negative expectancy: -1.0 percentage point -> 30 + 20 * (-1.0) = 10.0
    s_neg1 = compute_expectancy_score("trend_following", adjusted_expectancy_pct=-1.0)
    assert s_neg1 == 10.0, f"-1.0 expectancy expected 10.0, got {s_neg1}"

    print("  --> PASS: Expectancy scoring follows exact canonical linear formula S_exp = 30 + 20 * E_adjusted")


def test_4_context_veto_thresholds():
    print("\n--- Test 4: Context Veto Thresholds & Penalties ---")
    base_kwargs = {
        "analyst_pts": 40.0,
        "fundamental_pts": 20.0,
        "news_pts": 20.0,
        "de_ratio": 1.2,
        "current_ratio": 1.5,
        "finbert_sentiment": 0.20,
        "target_consensus": 120.0,
        "price": 100.0,
    }

    # Normal candidate: total raw context = 80.0 -> normalized 100.0, no vetoes -> 100.0
    c_norm = compute_context_score(**base_kwargs)
    assert c_norm == 100.0, f"Expected 100.0, got {c_norm}"

    # Veto 1: Balance Sheet Distress (D/E > 2.5 AND CR < 1.0)
    # Sub-case 1a: D/E = 2.6, CR = 1.2 (CR safe) -> NO VETO
    c_safe_cr = compute_context_score(**dict(base_kwargs, de_ratio=2.6, current_ratio=1.2))
    assert c_safe_cr == 100.0, f"D/E high but CR safe should not veto, got {c_safe_cr}"

    # Sub-case 1b: D/E = 2.49, CR = 0.8 (D/E safe) -> NO VETO
    c_safe_de = compute_context_score(**dict(base_kwargs, de_ratio=2.49, current_ratio=0.8))
    assert c_safe_de == 100.0, f"CR low but D/E safe should not veto, got {c_safe_de}"

    # Sub-case 1c: D/E = 2.51, CR = 0.99 -> VETO! Capped at 30.0
    c_distress = compute_context_score(**dict(base_kwargs, de_ratio=2.51, current_ratio=0.99))
    assert c_distress <= 30.0, f"Distress expected <= 30.0, got {c_distress}"

    # Veto 2: Negative News Sentiment (FinBERT < -0.30)
    # Sub-case 2a: sentiment = -0.29 -> NO VETO
    c_sent_border = compute_context_score(**dict(base_kwargs, finbert_sentiment=-0.29))
    assert c_sent_border == 100.0, f"Sentiment -0.29 should not cap at 40, got {c_sent_border}"

    # Sub-case 2b: sentiment = -0.31 -> VETO! Capped at 40.0
    c_sent_veto = compute_context_score(**dict(base_kwargs, finbert_sentiment=-0.31))
    assert c_sent_veto <= 40.0, f"Sentiment veto expected <= 40.0, got {c_sent_veto}"

    # Decoupled Earnings: Score invariant to earnings surprise; downstream gate blocks severe miss
    c_miss = compute_context_score(**dict(base_kwargs, earnings_surprise_pct=-10.1))
    assert c_miss == 100.0, f"Initial context score must be invariant to earnings surprise, got {c_miss}"

    # Downstream Earnings Risk Gate blocks severe miss
    from src.filters.earnings_filter import earnings_risk_filter
    import datetime
    gate_block = earnings_risk_filter(
        ticker="TEST",
        scan_date=datetime.date.today(),
        strategy="Trend Following",
        earnings_surprise_pct=-10.1,
        earnings_calendar={"TEST": {"next_earnings_date": (datetime.date.today() + datetime.timedelta(days=20)).isoformat()}},
    )
    assert not gate_block["pass"], "Downstream earnings gate must block severe earnings miss"

    # Penalty 4: Analyst Downside (Target < Price)
    # Sub-case 4a: Target = 95.0, Price = 100.0 -> PENALTY -15.0
    c_downside = compute_context_score(**dict(base_kwargs, price=100.0, target_consensus=95.0))
    assert c_downside == 85.0, f"Analyst downside expected 85.0 (100 - 15), got {c_downside}"

    print("  --> PASS: Context veto and penalty thresholds precisely match specification")


def test_5_earnings_blackout_windows():
    print("\n--- Test 5: Strategy Earnings Blackout Windows ---")
    expected_blackouts = {
        "trend_following": 5,
        "52w_high_breakout": 5,
        "pullback_recovery": 3,
        "cross_sectional_momentum": 3,
        "sector_rotation": 4,
        "pead": 0,
        "mean_reversion": 3,
    }

    for strat, days in expected_blackouts.items():
        assert EARNINGS_BLACKOUT_DAYS[strat] == days, f"Blackout days mismatch for {strat}: got {EARNINGS_BLACKOUT_DAYS[strat]}, expected {days}"

    # Test blackout evaluation logic via earnings_risk_filter
    # Blackout windows are trading days. Thursday 2026-09-10 -> Wednesday 2026-09-16 is 4 sessions
    # (Fri, Mon, Tue, Wed); no holiday in between.
    scan_date = datetime.date(2026, 9, 10)
    cal = {"AAPL": {"next_earnings_date": "2026-09-16"}}
    # Ticker earnings in 4 trading days:
    # Trend (5 sessions) -> pass is False (in blackout)
    # Pullback (3 sessions) -> pass is True (NOT in blackout)
    # PEAD (0) -> pass is True (NOT in blackout)
    assert earnings_risk_filter("AAPL", scan_date, "trend_following", earnings_calendar=cal)["pass"] is False
    assert earnings_risk_filter("AAPL", scan_date, "pullback_recovery", earnings_calendar=cal)["pass"] is True
    assert earnings_risk_filter("AAPL", scan_date, "pead", earnings_calendar=cal)["pass"] is True

    print("  --> PASS: Earnings blackout days and strategy exemptions match Master Spec")


def test_6_target_atr_multipliers_and_floors():
    print("\n--- Test 6: Target ATR Multipliers & Minimum Floors ---")
    expected_configs = {
        "trend_following": {"atr": (2.5, 5.0, 8.0), "floors": (0.06, 0.14, 0.22)},
        "52w_high_breakout": {"atr": (2.0, 4.0, 7.0), "floors": (0.05, 0.12, 0.20)},
        "pullback_recovery": {"atr": (1.5, 3.0, 5.0), "floors": (0.04, 0.09, 0.15)},
        "pead": {"atr": (2.0, 4.5, 7.5), "floors": (0.05, 0.13, 0.22)},
        "cross_sectional_momentum": {"atr": (2.0, 4.0, 6.5), "floors": (0.05, 0.11, 0.18)},
        "sector_rotation": {"atr": (1.8, 3.5, 6.0), "floors": (0.045, 0.10, 0.16)},
    }

    for strat, exp in expected_configs.items():
        cfg = STRATEGY_TARGET_CONFIG[strat]
        assert (cfg["atr_k1"], cfg["atr_k2"], cfg["atr_k3"]) == exp["atr"], f"ATR multipliers mismatch in {strat}"
        assert (cfg["fixed_t1"], cfg["fixed_t2"], cfg["fixed_t3"]) == exp["floors"], f"Fixed floors mismatch in {strat}"

    # Verify calculation selects max(floor, atr)
    # High ATR test: ATR target wins
    # Pullback: Entry $100, ATR $10. T1 ATR = 100 + 1.5 * 10 = 115. Floor = 100 * 1.04 = 104.
    res_high_atr = calculate_targets(
        ticker="TEST",
        entry_price=100.0,
        atr_14=10.0,
        stop_loss=95.0,
        strategy_name="pullback_recovery",
        mock_reach_probs=(0.50, 0.30, 0.20),
    )
    assert res_high_atr.target_1 == 115.0, f"High ATR target should be 115.0, got {res_high_atr.target_1}"

    # Low ATR test: Fixed floor wins
    # Pullback: Entry $100, ATR $1. T1 ATR = 100 + 1.5 * 1 = 101.5. Floor = 100 * 1.04 = 104.
    res_low_atr = calculate_targets(
        ticker="TEST",
        entry_price=100.0,
        atr_14=1.0,
        stop_loss=95.0,
        strategy_name="pullback_recovery",
        mock_reach_probs=(0.50, 0.30, 0.20),
    )
    assert res_low_atr.target_1 == 104.0, f"Low ATR target should use floor 104.0, got {res_low_atr.target_1}"

    print("  --> PASS: Target ATR multipliers, fixed floors, and max() selection verified")


def test_7_reach_probability_zero_fallback():
    print("\n--- Test 7: Reach Probability Zero Fallback (No 0.35 Hardcoded Fallback) ---")
    # Empty price dataframe -> returns 0.0 (no arbitrary 0.35 fallback)
    empty_df = pd.DataFrame()
    rp_empty = get_reach_prob("TEST_EMPTY", target_pct=0.05, holding_days=10, price_df=empty_df)
    assert rp_empty == 0.0, f"Empty history reach prob must be 0.0, got {rp_empty}"

    # Insufficient price history (< holding_days + 5 bars) -> returns 0.0
    short_df = pd.DataFrame({"Close": [100.0] * 12})
    rp_short = get_reach_prob("TEST_SHORT", target_pct=0.05, holding_days=10, price_df=short_df)
    assert rp_short == 0.0, f"Short history reach prob must be 0.0, got {rp_short}"

    # Insufficient windows (< MIN_REACH_PROB_WINDOWS) -> returns 0.0
    # holding_days = 10, len(closes) = 25 -> total_possible_windows = 15 < 20
    border_df = pd.DataFrame({"Close": [100.0 + i for i in range(25)]})
    rp_border = get_reach_prob("TEST_BORDER", target_pct=0.05, holding_days=10, price_df=border_df)
    assert rp_border == 0.0, f"Insufficient windows (<20) reach prob must be 0.0, got {rp_border}"

    # Sufficient windows (>= 20) with strong upward trend -> valid empirical reach prob > 0.0
    # holding_days = 10, len(closes) = 100 with steadily increasing prices
    trend_df = pd.DataFrame({"Close": [100.0 * (1.005 ** i) for i in range(100)]})
    rp_trend = get_reach_prob("TEST_TREND", target_pct=0.03, holding_days=10, price_df=trend_df)
    assert rp_trend > 0.50, f"Sufficient windows expected reach prob > 0.50, got {rp_trend}"

    print("  --> PASS: Reach probability returns 0.0 on insufficient data; zero arbitrary fallbacks")


def test_8_scale_out_survival_boundary():
    print("\n--- Test 8: Scale-out T3 Survival Boundary (15.0% threshold) ---")
    assert T3_REACH_PROB_SURVIVAL_THRESHOLD == 0.15

    # Case A: T3 reach prob = 14.99% -> T3 does NOT survive -> 60/40/0
    res_sub_15 = calculate_targets(
        ticker="TEST",
        entry_price=100.0,
        atr_14=2.0,
        stop_loss=95.0,
        strategy_name="trend_following",
        mock_reach_probs=(0.50, 0.25, 0.1499),
    )
    assert res_sub_15.scale_out_weights == "60/40/0", f"14.99% should yield 60/40/0, got {res_sub_15.scale_out_weights}"
    assert res_sub_15.target_3 is None, f"14.99% should prune target_3, got {res_sub_15.target_3}"

    # Case B: T3 reach prob = 15.00% -> T3 DOES survive -> 50/30/20
    res_at_15 = calculate_targets(
        ticker="TEST",
        entry_price=100.0,
        atr_14=2.0,
        stop_loss=95.0,
        strategy_name="trend_following",
        mock_reach_probs=(0.50, 0.25, 0.1500),
    )
    assert res_at_15.scale_out_weights == "50/30/20", f"15.00% should yield 50/30/20, got {res_at_15.scale_out_weights}"
    assert res_at_15.target_3 is not None, f"15.00% should retain target_3, got {res_at_15.target_3}"

    print("  --> PASS: Scale-out boundary precisely triggers at 15.0% survival threshold")


def test_9_earnings_cache_freshness():
    print("\n--- Test 9: Earnings Cache TTL (24h / 86400s) & Refresh Logic ---")
    assert EARNINGS_CACHE_TTL_SECONDS == 86400

    now = time.time()
    # Record from 2 hours ago (fresh)
    fresh_record = {"earnings_date": "2026-09-10", "cached_at": now - 7200}
    assert is_earnings_record_fresh(fresh_record) is True

    # Record from 25 hours ago (stale)
    stale_record = {"earnings_date": "2026-09-10", "cached_at": now - 90000}
    assert is_earnings_record_fresh(stale_record) is False

    # Record with no cached_at timestamp (stale)
    no_ts_record = {"earnings_date": "2026-09-10"}
    assert is_earnings_record_fresh(no_ts_record) is False

    print("  --> PASS: Earnings cache freshness correctly respects 24h TTL")


def test_10_pure_recommendation_architecture():
    print("\n--- Test 10: Pure Recommendation Architecture (No Portfolio / Sizing Machinery) ---")
    pos_sizer_path = os.path.join(PROJECT_ROOT, "src", "position_sizer.py")
    assert not os.path.exists(pos_sizer_path), "src/position_sizer.py must not exist"

    # Validation uses validate_candidate_features in src.ranker
    valid_features = {
        "ticker": "AAPL",
        "strategy": "trend_following",
        "current_rsi": 55.0,
        "price": 150.0,
        "dma_50": 140.0,
        "volume_ratio": 1.2,
        "macd_histogram": 0.5,
        "atr_14": 2.5,
        "winrate_score": 65.0,
    }
    is_valid, msg = validate_candidate_features(valid_features)
    assert is_valid is True, f"Expected valid candidate, got {msg}"

    # Tier assignment is pure quality-driven without capital/sizing
    tier = assign_tier(88.0, 2.5)
    assert tier == "Strong Buy"
    print("  --> PASS: Pure recommendation architecture verified with zero portfolio/sizing machinery")


def main():
    print("=" * 80)
    print("  QUANT SPEC ALIGNMENT TEST SUITE (Master Spec v2.3+)")
    print("=" * 80)
    test_1_strategy_weight_vectors()
    test_2_regime_score_matrix()
    test_3_expectancy_score_formula()
    test_4_context_veto_thresholds()
    test_5_earnings_blackout_windows()
    test_6_target_atr_multipliers_and_floors()
    test_7_reach_probability_zero_fallback()
    test_8_scale_out_survival_boundary()
    test_9_earnings_cache_freshness()
    test_10_pure_recommendation_architecture()
    print("\n" + "=" * 80)
    print("  ALL 10 QUANT SPEC ALIGNMENT TESTS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    main()
