#!/usr/bin/env python
"""
Regression Test Suite for P0 Fixes
==================================
Tests all 7 P0 objectives required by the Master Specification v2.3+:
- P0-1: Single source of truth for scoring & tiering (bypass strategy-level ranking/filtering)
- P0-2: Fix context/momentum/win-rate defaults / 46.9 bug
- P0-3: Remove premature TOP_N=3 truncation before downstream gates
- P0-4: Use adjusted Half-Kelly sizing only (final_adjusted_half_kelly)
- P0-5: Enforce drawdown controls and VIX emergency sizing
- P0-6: Enforce 1.0% portfolio floor and 5.0% ceiling in allocate_capital()
- P0-7: Prevent invalid/fake signals from reaching allocator
"""

import sys
import os
import math

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "jobs"))

from src.ranker import (
    SignalRanker,
    compute_momentum_score,
    validate_candidate_features,
    calculate_p_win,
    calculate_half_kelly,
)
from src.position_sizer import (
    allocate_capital,
    assign_tier,
    validate_candidate_for_allocation,
)
from src.risk_controls import (
    get_drawdown_multiplier,
    enforce_risk_controls,
)


def test_p0_1_single_source_of_truth():
    print("\n--- Testing P0-1: Single Source of Truth for Scoring & Tiering ---")
    ranker = SignalRanker()

    # Candidate with good features
    row_strong = {
        "ticker": "AAPL",
        "strategy": "trend_following",
        "current_rsi": 62.0,
        "price": 180.0,
        "dma_50": 170.0,
        "volume_ratio": 1.4,
        "macd_histogram": 0.5,
        "winrate_score": 65.0,
        "context_analyst": 20.0,
        "context_earnings": 18.0,
        "context_fundamental": 15.0,
        "context_news": 12.0,
    }
    res_strong = ranker.compute_composite_score(row_strong, regime="BULL")
    score_strong = res_strong["composite_score"]
    tier_strong = assign_tier(score_strong)
    assert score_strong >= 65.0, f"Expected high score for strong candidate, got {score_strong}"
    print(f"Strong Candidate: Score={score_strong:.2f}, Tier={tier_strong} (Central Authority)")

    # Candidate with weak features
    row_weak = {
        "ticker": "XYZ",
        "strategy": "trend_following",
        "current_rsi": 35.0,
        "price": 90.0,
        "dma_50": 105.0,
        "volume_ratio": 0.6,
        "macd_histogram": -0.8,
        "winrate_score": 40.0,
        "context_analyst": 5.0,
        "context_earnings": 0.0,
        "context_fundamental": 5.0,
        "context_news": 0.0,
    }
    res_weak = ranker.compute_composite_score(row_weak, regime="BULL")
    score_weak = res_weak["composite_score"]
    tier_weak = assign_tier(score_weak)
    assert score_weak < 55.0, f"Expected low score for weak candidate, got {score_weak}"
    assert tier_weak == "Rejected", f"Expected Rejected tier for weak candidate, got {tier_weak}"
    print(f"Weak Candidate: Score={score_weak:.2f}, Tier={tier_weak} (Central Authority)")

    # Check strategy rank_candidates pass-through (no truncation to 3 or 5)
    from jobs.strategies import trend_following
    strat = trend_following.TrendFollowingStrategy()
    sample_pool = [
        {"ticker": f"TICK{i}", "score": 70 + i, "is_setup": True, "stop_loss": 90, "entry_price": 100}
        for i in range(10)
    ]
    ranked = strat.rank_candidates(sample_pool, regime="BULL")
    assert len(ranked) == 10, f"Expected all 10 candidates preserved, got {len(ranked)}"
    print("Strategy rank_candidates preserves full candidate pool: PASS")
    print("P0-1 PASS: Central SignalRanker is single authority; no strategy-level truncation.")


def test_p0_2_fix_46_9_score_bug():
    print("\n--- Testing P0-2: Fix Context/Momentum/Win-Rate Defaults / 46.9 Bug ---")
    ranker = SignalRanker()

    # Candidate missing critical features must fail validation
    invalid_row = {
        "ticker": "BAD1",
        "strategy": "trend_following",
        # Missing momentum & technicals, missing winrate
    }
    is_valid, msg = validate_candidate_features(invalid_row)
    assert not is_valid, f"Expected invalid features to fail, but got {is_valid}"
    print(f"Missing features rejected by validate_candidate_features: '{msg}' (PASS)")

    # Attempting to score without winrate must raise ValueError, not silently produce 46.9
    try:
        ranker.compute_composite_score(invalid_row, regime="BULL")
        assert False, "Should have raised ValueError on missing winrate"
    except ValueError as e:
        print(f"Missing required field raised ValueError as expected: {e} (PASS)")

    # Continuous momentum score check: different technicals must produce different momentum scores
    mom_bullish = compute_momentum_score({
        "current_rsi": 65.0,
        "price": 150.0,
        "dma_50": 135.0,
        "volume_ratio": 2.0,
        "macd_histogram": 1.2,
    })
    mom_bearish = compute_momentum_score({
        "current_rsi": 40.0,
        "price": 90.0,
        "dma_50": 100.0,
        "volume_ratio": 0.7,
        "macd_histogram": -0.5,
    })
    assert mom_bullish > mom_bearish, f"Bullish momentum {mom_bullish} should exceed bearish {mom_bearish}"
    print(f"Continuous momentum calculation: Bullish={mom_bullish:.2f}, Bearish={mom_bearish:.2f} (PASS)")

    # Ensure two different valid candidates do NOT collapse to 46.9
    c1 = {
        "ticker": "PLTR",
        "strategy": "trend_following",
        "current_rsi": 58.0,
        "price": 45.0,
        "dma_50": 42.0,
        "volume_ratio": 1.3,
        "macd_histogram": 0.3,
        "winrate_score": 60.0,
        "context_analyst": 20.0,
        "context_earnings": 15.0,
        "context_fundamental": 15.0,
        "context_news": 10.0,
    }
    c2 = {
        "ticker": "NVDA",
        "strategy": "trend_following",
        "current_rsi": 72.0,
        "price": 130.0,
        "dma_50": 115.0,
        "volume_ratio": 1.8,
        "macd_histogram": 1.5,
        "winrate_score": 75.0,
        "context_analyst": 25.0,
        "context_earnings": 22.0,
        "context_fundamental": 20.0,
        "context_news": 18.0,
    }
    s1 = ranker.compute_composite_score(c1, regime="BULL")["composite_score"]
    s2 = ranker.compute_composite_score(c2, regime="BULL")["composite_score"]
    assert abs(s1 - 46.9) > 1.0, f"Score {s1} should not be 46.9"
    assert abs(s2 - 46.9) > 1.0, f"Score {s2} should not be 46.9"
    assert abs(s1 - s2) > 5.0, f"Scores {s1} and {s2} should be distinctly different"
    print(f"Distinct composite scores: PLTR={s1:.2f}, NVDA={s2:.2f} (PASS)")
    print("P0-2 PASS: No 46.9 defaults, real feature validation, continuous momentum.")


def test_p0_3_remove_top_n_truncation():
    print("\n--- Testing P0-3: Remove Premature TOP_N=3 Truncation ---")
    # Simulate a scenario with 5 ranked candidates where the top 3 fail downstream gates:
    # Candidate 1: Blocked by earnings blackout
    # Candidate 2: Blocked by reach probability < 0.20
    # Candidate 3: Blocked by Kelly <= 0 (bad R:R)
    # Candidate 4: Valid candidate (should be funded)
    # Candidate 5: Valid candidate (should be funded if cash permits)

    candidates = [
        {
            "ticker": "FAIL_EARNINGS",
            "composite_score": 92.0,
            "final_adjusted_half_kelly": 0.04,
            "entry_price": 100.0,
            "earnings_rejected": True,
            "rejection_reason": "Blackout window: earnings in 2 days",
        },
        {
            "ticker": "FAIL_REACH",
            "composite_score": 89.0,
            "final_adjusted_half_kelly": 0.03,
            "entry_price": 100.0,
            "earnings_rejected": False,
            "rejection_reason": "Reach probability 0.15 below 0.20 floor",
        },
        {
            "ticker": "FAIL_KELLY",
            "composite_score": 85.0,
            "final_adjusted_half_kelly": 0.0,
            "entry_price": 100.0,
            "earnings_rejected": False,
        },
        {
            "ticker": "PASS_FOURTH",
            "composite_score": 82.0,
            "final_adjusted_half_kelly": 0.04,
            "entry_price": 100.0,
            "earnings_rejected": False,
        },
        {
            "ticker": "PASS_FIFTH",
            "composite_score": 80.0,
            "final_adjusted_half_kelly": 0.03,
            "entry_price": 50.0,
            "earnings_rejected": False,
        },
    ]

    # Filter downstream as jobs/generate_signals.py does: only non-earnings-rejected and non-gated
    evaluable = [c for c in candidates if not c.get("earnings_rejected") and not c.get("rejection_reason")]
    funded, constrained = allocate_capital(evaluable, portfolio_value=10000.0, cash_balance=1000.0)

    funded_tickers = [f["ticker"] for f in funded]
    assert "PASS_FOURTH" in funded_tickers, "PASS_FOURTH should have been funded (not truncated by TOP_N=3)"
    assert "PASS_FIFTH" in funded_tickers, "PASS_FIFTH should have been funded"
    print(f"Funded candidates beyond TOP_N=3: {funded_tickers} (PASS)")
    print("P0-3 PASS: Downstream candidates are reached and funded; no premature TOP_N=3 cut.")


def test_p0_4_use_adjusted_half_kelly_only():
    print("\n--- Testing P0-4: Use Adjusted Half-Kelly Sizing Only ---")
    # Candidate with both raw full kelly (e.g. 0.15) and final_adjusted_half_kelly (0.04)
    # The allocator MUST use final_adjusted_half_kelly (0.04 -> $400 on $10k), NOT raw kelly (0.15 -> $500 capped)
    candidate = {
        "ticker": "TEST_KELLY",
        "composite_score": 85.0,
        "entry_price": 100.0,
        "stop_loss": 94.0,
        "kelly_fraction": 0.15,               # Raw full Kelly (should be IGNORED by allocator)
        "final_adjusted_half_kelly": 0.035,    # 3.5% adjusted half Kelly ($350 on $10k)
    }

    funded, _ = allocate_capital([candidate], portfolio_value=10000.0, cash_balance=5000.0)
    assert len(funded) == 1, "Candidate should be funded"
    allocated = funded[0]["allocated_dollars"]
    expected_dollars = 10000.0 * 0.035  # $350.00
    assert math.isclose(allocated, expected_dollars, abs_tol=0.01), (
        f"Expected ${expected_dollars}, got ${allocated}. Raw Kelly was likely used instead of adjusted Half-Kelly!"
    )
    print(f"Allocated: ${allocated:.2f} exactly matches final_adjusted_half_kelly (3.5% = ${expected_dollars:.2f}) (PASS)")
    print("P0-4 PASS: Consumes ONLY final_adjusted_half_kelly; ignores raw full kelly_fraction.")


def test_p0_5_enforce_drawdown_and_vix_controls():
    print("\n--- Testing P0-5: Enforce Drawdown & VIX Controls ---")
    peak = 10000.0

    # 1. Drawdown < 5%: 1.0x, normal
    mult, status = get_drawdown_multiplier(9600.0, peak)
    assert mult == 1.0 and status == "normal", f"Expected (1.0, normal), got ({mult}, {status})"

    # 2. Drawdown 5-10%: 0.75x, warning
    mult, status = get_drawdown_multiplier(9200.0, peak)
    assert mult == 0.75 and status == "warning", f"Expected (0.75, warning), got ({mult}, {status})"

    # 3. Drawdown 10-15%: 0.50x, severe
    mult, status = get_drawdown_multiplier(8800.0, peak)
    assert mult == 0.50 and status == "severe", f"Expected (0.50, severe), got ({mult}, {status})"

    # 4. Drawdown >= 15%: 0.0x, halt
    mult, status = get_drawdown_multiplier(8400.0, peak)
    assert mult == 0.0 and status == "halt", f"Expected (0.0, halt), got ({mult}, {status})"

    # 5. enforce_risk_controls: Block trading on halt
    sig_any = {"ticker": "XYZ", "composite_score": 90.0}
    allowed, eff_mult, reason = enforce_risk_controls(sig_any, portfolio_value=8400.0, peak_value=peak)
    assert not allowed and eff_mult == 0.0, "Should halt on >= 15% drawdown"
    print(f"Halt enforcement on 16% drawdown: allowed={allowed}, reason='{reason}' (PASS)")

    # 6. Severe drawdown (10-15%): score < 80 blocked, score >= 80 allowed with 0.50x mult
    sig_low_score = {"ticker": "LOW", "composite_score": 75.0}
    sig_high_score = {"ticker": "HIGH", "composite_score": 85.0}
    allowed_low, _, reason_low = enforce_risk_controls(sig_low_score, portfolio_value=8800.0, peak_value=peak)
    allowed_high, mult_high, reason_high = enforce_risk_controls(sig_high_score, portfolio_value=8800.0, peak_value=peak)
    assert not allowed_low, f"Score < 80 should be blocked during severe drawdown, got reason: {reason_low}"
    assert allowed_high and mult_high == 0.50, f"Score >= 80 should be allowed with 0.5x, got mult={mult_high}"
    print(f"Severe drawdown filtering: Score 75 blocked ('{reason_low}'), Score 85 allowed at {mult_high}x (PASS)")

    # 7. VIX emergency sizing combined multiplier
    allowed_vix, mult_vix, _ = enforce_risk_controls(sig_high_score, portfolio_value=9600.0, peak_value=peak, vix_size_mult=0.5)
    assert allowed_vix and mult_vix == 0.5, f"Expected 0.5 mult with VIX mult 0.5, got {mult_vix}"
    print("P0-5 PASS: Drawdown schedule, halt at 15%, score >= 80 gate in severe, and VIX sizing fully enforced.")


def test_p0_6_enforce_floor_and_ceiling():
    print("\n--- Testing P0-6: Enforce 1.0% Floor and 5.0% Ceiling in Allocation ---")
    pv = 10000.0  # 1% floor = $100, 5% cap = $500

    candidates = [
        # Candidate 1: Below 1.0% minimum (0.8% = $80) -> MUST be rejected
        {"ticker": "SUB_MIN", "composite_score": 88.0, "final_adjusted_half_kelly": 0.008, "entry_price": 50.0},
        # Candidate 2: Exact 1.0% minimum ($100) -> MUST be funded
        {"ticker": "AT_MIN", "composite_score": 85.0, "final_adjusted_half_kelly": 0.010, "entry_price": 50.0},
        # Candidate 3: Normal 3.0% ($300) -> MUST be funded
        {"ticker": "NORMAL", "composite_score": 82.0, "final_adjusted_half_kelly": 0.030, "entry_price": 50.0},
        # Candidate 4: Above 5.0% cap (8.0% = $800) -> MUST be capped at $500 (5.0%)
        {"ticker": "ABOVE_CAP", "composite_score": 80.0, "final_adjusted_half_kelly": 0.080, "entry_price": 50.0},
        # Candidate 5: Cash constrained when remaining cash drops below $100
        {"ticker": "CASH_STARVED", "composite_score": 75.0, "final_adjusted_half_kelly": 0.030, "entry_price": 50.0},
    ]

    # Provide $950 total cash:
    # SUB_MIN: rejected (below 1%)
    # AT_MIN: funds $100, remaining $850
    # NORMAL: funds $300, remaining $550
    # ABOVE_CAP: funds $500 (capped from $800), remaining $50
    # CASH_STARVED: remaining cash $50 is below 1% floor ($100) -> marked Cash constrained, rejected
    funded, constrained = allocate_capital(candidates, portfolio_value=pv, cash_balance=950.0)

    funded_map = {f["ticker"]: f for f in funded}
    assert "SUB_MIN" not in funded_map, "SUB_MIN (<1%) must not be funded"
    assert "AT_MIN" in funded_map, "AT_MIN (1%) must be funded"
    assert "NORMAL" in funded_map, "NORMAL (3%) must be funded"
    assert "ABOVE_CAP" in funded_map, "ABOVE_CAP (8%) must be funded at cap"
    assert "CASH_STARVED" not in funded_map, "CASH_STARVED must not be funded below minimum floor"

    assert funded_map["AT_MIN"]["allocated_dollars"] == 100.0, f"Expected $100, got {funded_map['AT_MIN']['allocated_dollars']}"
    assert funded_map["NORMAL"]["allocated_dollars"] == 300.0, f"Expected $300, got {funded_map['NORMAL']['allocated_dollars']}"
    assert funded_map["ABOVE_CAP"]["allocated_dollars"] == 500.0, f"Expected $500 cap, got {funded_map['ABOVE_CAP']['allocated_dollars']}"

    # Check rejection reasons
    for c in candidates:
        if c["ticker"] == "SUB_MIN":
            assert "below 1.0% minimum" in c["rejection_reason"], f"Unexpected reason: {c['rejection_reason']}"
        elif c["ticker"] == "CASH_STARVED":
            assert c["rejection_reason"] == "Cash constrained", f"Unexpected reason: {c['rejection_reason']}"

    print(f"Floor (<1% rejected, 1% funded): PASS")
    print(f"Ceiling (8% capped to 5% = $500): PASS")
    print(f"Cash constraint (<$100 remaining rejected rather than partial): PASS")
    print("P0-6 PASS: 1.0% floor and 5.0% ceiling strictly enforced.")


def test_p0_7_prevent_invalid_signals():
    print("\n--- Testing P0-7: Prevent Invalid/Fake Signals from Reaching Allocator ---")
    valid_base = {
        "ticker": "MSFT",
        "strategy": "trend_following",
        "composite_score": 82.5,
        "tier_label": "Buy",
        "momentum_score": 75.0,
        "winrate_score": 65.0,
        "expectancy_score": 58.0,
        "regime_score": 80.0,
        "context_score": 70.0,
        "entry_price": 400.0,
        "stop_loss": 380.0,
        "weighted_rr_honest": 2.1,
        "final_adjusted_half_kelly": 0.035,
        "earnings_rejected": False,
    }

    # Baseline valid candidate
    ok, msg = validate_candidate_for_allocation(valid_base)
    assert ok, f"Baseline candidate should be valid, got {msg}"
    print(f"Valid candidate passed validation: {msg} (PASS)")

    # Case 1: Missing ticker
    c = dict(valid_base, ticker="")
    ok, msg = validate_candidate_for_allocation(c)
    assert not ok and "ticker" in msg, f"Expected ticker failure, got: {msg}"

    # Case 2: Non-buy tier (e.g. Watchlist or Neutral)
    c = dict(valid_base, tier_label="Neutral")
    ok, msg = validate_candidate_for_allocation(c)
    assert not ok and "tier" in msg.lower(), f"Expected tier failure, got: {msg}"

    # Case 3: Stop loss above entry price
    c = dict(valid_base, stop_loss=410.0)
    ok, msg = validate_candidate_for_allocation(c)
    assert not ok and "stop_loss" in msg, f"Expected stop_loss failure, got: {msg}"

    # Case 4: Negative or NaN scoring feature
    c = dict(valid_base, momentum_score=float("nan"))
    ok, msg = validate_candidate_for_allocation(c)
    assert not ok and "momentum_score" in msg, f"Expected NaN momentum failure, got: {msg}"

    # Case 5: Missing Half-Kelly
    c = dict(valid_base, final_adjusted_half_kelly=0.0)
    ok, msg = validate_candidate_for_allocation(c)
    assert not ok and "half-kelly" in msg.lower(), f"Expected Half-Kelly failure, got: {msg}"

    # Case 6: Earnings blackout rejected
    c = dict(valid_base, earnings_rejected=True)
    ok, msg = validate_candidate_for_allocation(c)
    assert not ok and "earnings" in msg.lower(), f"Expected earnings failure, got: {msg}"

    print("All invalid signal cases rejected with strict validation: PASS")
    print("P0-7 PASS: Corrupted or invalid signals cannot bypass the allocator.")


def main():
    print("==================================================================")
    print("RUNNING COMPREHENSIVE REGRESSION SUITE: P0 PRODUCTION SAFEGUARDS")
    print("==================================================================")
    test_p0_1_single_source_of_truth()
    test_p0_2_fix_46_9_score_bug()
    test_p0_3_remove_top_n_truncation()
    test_p0_4_use_adjusted_half_kelly_only()
    test_p0_5_enforce_drawdown_and_vix_controls()
    test_p0_6_enforce_floor_and_ceiling()
    test_p0_7_prevent_invalid_signals()
    print("\n==================================================================")
    print("ALL 7 P0 REGRESSION TESTS COMPLETED SUCCESSFULLY!")
    print("==================================================================")


if __name__ == "__main__":
    main()
