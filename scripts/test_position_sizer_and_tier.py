"""
Acceptance Test Script for Ranked Sequential Cash Allocation & Tier Logic
========================================================================
Validates:
- Scenario A: 3 Signals, $1,000 Cash
- Scenario B: Tier Logic (Strong Buy, Buy, Rejected)
- Scenario C: No Dilution across 10 signals
"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.position_sizer import allocate_capital, assign_tier, calculate_normalized_sizing


class SignalObj:
    """Mock signal object supporting attribute access."""
    def __init__(self, ticker, score, kelly_fraction, raw_dollar_demand, entry_price=100.0, weighted_rr_honest=2.0):
        self.ticker = ticker
        self.composite_score = score
        self.kelly_fraction = kelly_fraction
        self.raw_dollar_demand = raw_dollar_demand
        self.entry_price = entry_price
        self.weighted_rr_honest = weighted_rr_honest
        self.allocated_dollars = 0.0
        self.exact_shares = 0.0
        self.max_shares = 0
        self.position_sizing = ""
        self.rejection_reason = ""


def run_tests():
    print("=" * 80)
    print("  POSITION SIZING & TIER LOGIC ACCEPTANCE SUITE")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # Scenario A: 3 Signals, $1,000 Cash
    # --------------------------------------------------------------------------
    print("\n[Scenario A] 3 Signals, $1,000 Cash")
    # Portfolio value = $10,000, cash = $1,000
    # MIN_ALLOCATION_DOLLARS = 10000 * 0.01 = $100
    sig_a = SignalObj("SIG_A", score=85.0, kelly_fraction=0.05, raw_dollar_demand=500.0)
    sig_b = SignalObj("SIG_B", score=70.0, kelly_fraction=0.04, raw_dollar_demand=400.0)
    sig_c = SignalObj("SIG_C", score=60.0, kelly_fraction=0.03, raw_dollar_demand=300.0)

    signals_a = [sig_c, sig_a, sig_b]  # Intentionally unsorted
    funded_a, cash_constrained_a = allocate_capital(signals_a, portfolio_value=10000.0, cash_balance=1000.0)

    print(f"  * Funded count: {len(funded_a)}")
    for s in funded_a:
        print(f"    - {s.ticker}: Allocated = ${s.allocated_dollars:.2f} ({s.exact_shares:.2f} sh)")
    print(f"  * Cash-constrained count: {len(cash_constrained_a)}")
    for s in cash_constrained_a:
        print(f"    - {s.ticker}: Allocated = ${s.allocated_dollars:.2f}, Reason = {s.rejection_reason}")

    cash_used_a = sum(s.allocated_dollars for s in funded_a)
    print(f"  * Cash used: ${cash_used_a:.2f} / $1,000.00")

    assert len(funded_a) == 2, f"Expected 2 funded signals, got {len(funded_a)}"
    assert funded_a[0].ticker == "SIG_A" and funded_a[0].allocated_dollars == 500.0
    assert funded_a[1].ticker == "SIG_B" and funded_a[1].allocated_dollars == 400.0
    assert len(cash_constrained_a) == 1
    assert cash_constrained_a[0].ticker == "SIG_C" and cash_constrained_a[0].allocated_dollars == 0.0
    assert cash_constrained_a[0].rejection_reason == "Cash constrained"
    assert cash_used_a == 900.0, f"Expected $900.00 cash used, got ${cash_used_a}"
    print("  --> PASS Scenario A: 3 signals funding verified.")

    # --------------------------------------------------------------------------
    # Scenario B: Tier Logic
    # --------------------------------------------------------------------------
    print("\n[Scenario B] Tier Classification Logic")
    tier_1 = assign_tier(85.0, 2.0)
    tier_2 = assign_tier(70.0, 1.5)
    tier_3 = assign_tier(50.0, 1.0)
    tier_4 = assign_tier(46.9, 3.46)

    print(f"  * Score 85.0, R:R 2.0  --> {tier_1}")
    print(f"  * Score 70.0, R:R 1.5  --> {tier_2}")
    print(f"  * Score 50.0, R:R 1.0  --> {tier_3}")
    print(f"  * Score 46.9, R:R 3.46 --> {tier_4}")

    assert tier_1 == "Strong Buy", f"Expected Strong Buy, got {tier_1}"
    assert tier_2 == "Buy", f"Expected Buy, got {tier_2}"
    assert tier_3 == "Rejected", f"Expected Rejected, got {tier_3}"
    assert tier_4 == "Buy", f"Expected Buy, got {tier_4}"
    print("  --> PASS Scenario B: Tier logic verified.")

    # --------------------------------------------------------------------------
    # Scenario C: No Dilution (10 signals each want $500, cash = $2,000)
    # --------------------------------------------------------------------------
    print("\n[Scenario C] No Dilution (10 signals each want $500, cash = $2,000)")
    signals_c = [
        SignalObj(f"SIG_{i}", score=90.0 - i, kelly_fraction=0.05, raw_dollar_demand=500.0, entry_price=100.0)
        for i in range(10)
    ]
    funded_c, cash_constrained_c = allocate_capital(signals_c, portfolio_value=10000.0, cash_balance=2000.0)

    print(f"  * Funded count: {len(funded_c)}")
    for s in funded_c:
        print(f"    - {s.ticker}: Allocated = ${s.allocated_dollars:.2f} ({s.exact_shares} sh)")
    print(f"  * Cash-constrained count: {len(cash_constrained_c)}")
    for s in cash_constrained_c[:2]:
        print(f"    - {s.ticker}: Allocated = ${s.allocated_dollars:.2f}, Reason = {s.rejection_reason}")

    assert len(funded_c) == 4, f"Expected 4 funded signals, got {len(funded_c)}"
    for s in funded_c:
        assert s.allocated_dollars == 500.0, f"Expected $500.00, got ${s.allocated_dollars}"
        assert s.exact_shares == 5.0, f"Expected 5.0 shares, got {s.exact_shares}"

    assert len(cash_constrained_c) == 6, f"Expected 6 cash-constrained signals, got {len(cash_constrained_c)}"
    assert cash_constrained_c[0].ticker == "SIG_4"
    assert cash_constrained_c[0].rejection_reason == "Cash constrained"
    assert cash_constrained_c[0].allocated_dollars == 0.0

    for s in funded_c:
        assert s.allocated_dollars >= 100.0, "Funded signal must receive at least minimum $100"

    print("  --> PASS Scenario C: Dilution prevented; sequential funding validated.")

    # --------------------------------------------------------------------------
    # Scenario D: Dictionary input test (generate_signals compatibility)
    # --------------------------------------------------------------------------
    print("\n[Scenario D] Dict Input Compatibility")
    dict_signals = [
        {"ticker": "AAPL", "composite_score": 85.0, "half_kelly_fraction": 0.05, "entry_price": 200.0, "weighted_rr_honest": 2.0},
        {"ticker": "MSFT", "composite_score": 75.0, "half_kelly_fraction": 0.04, "entry_price": 400.0, "weighted_rr_honest": 1.8},
        {"ticker": "GOOG", "composite_score": 40.0, "half_kelly_fraction": -0.01, "entry_price": 150.0, "weighted_rr_honest": 0.8},
    ]
    funded_d, constrained_d = allocate_capital(dict_signals, portfolio_value=10000.0, cash_balance=1000.0)
    assert len(funded_d) == 2
    assert funded_d[0]["ticker"] == "AAPL" and funded_d[0]["allocated_dollars"] == 500.0
    assert funded_d[1]["ticker"] == "MSFT" and funded_d[1]["allocated_dollars"] == 400.0
    assert dict_signals[2]["allocated_dollars"] == 0.0
    assert "Kelly ≤ 0" in dict_signals[2]["rejection_reason"]
    print("  --> PASS Scenario D: Dict input and Kelly rejection verified.")

    print("\n" + "=" * 80)
    print("  ALL ACCEPTANCE SCENARIOS PASSED WITH ZERO ERRORS!")
    print("=" * 80)


if __name__ == "__main__":
    run_tests()
