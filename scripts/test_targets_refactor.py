"""
Acceptance Test Script for Strategy-Specific ATR Targets and Reach Probability Filtering
"""

import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.strategies.target_calculator import calculate_targets


def run_acceptance_tests():
    print("=" * 80)
    print("  TARGET CALCULATOR ACCEPTANCE TESTS — 4 SCENARIOS")
    print("=" * 80)    # -------------------------------------------------------------
    # Scenario A: PLTR, Trend Following
    # Entry: $186.32, ATR: $6.42, Stop: $173.30
    # Override/computed targets: T1: $208.24 (fixed), T2: $226.83 (fixed), T3: $231.26 (ATR)
    # ReachProbs: T1=38%, T2=16%, T3=14%
    # Expected: T3 reach prob 14% < 15% threshold -> T3 pruned.
    # T1 + T2 survive -> Scale: 60/40/0, Honest R:R ≈ 2.25
    # -------------------------------------------------------------
    res_a = calculate_targets(
        ticker="PLTR",
        entry_price=186.32,
        atr_14=6.42,
        stop_loss=173.30,
        strategy_name="Trend Following",
        override_targets=(208.24, 226.83, 231.26),
        mock_reach_probs=(0.38, 0.16, 0.14),
    )

    print("\n[Scenario A] PLTR — Trend Following")
    print(f"  Entry: $186.32, Stop: $173.30, ATR: $6.42")
    print(f"  Reach Probs: T1={res_a.reach_prob_t1:.0%}, T2={res_a.reach_prob_t2:.0%}, T3={res_a.reach_prob_t3:.0%}")
    print(f"  Targets: T1=${res_a.target_1}, T2=${res_a.target_2}, T3=${res_a.target_3}")
    print(f"  Scale-out Weights: {res_a.scale_out_weights}")
    print(f"  Honest Weighted R:R: {res_a.weighted_rr_honest}")
    print(f"  Is Valid: {res_a.is_valid}")

    assert res_a.is_valid is True, "Scenario A should be valid"
    assert res_a.scale_out_weights == "60/40/0", f"Expected 60/40/0, got {res_a.scale_out_weights}"
    assert abs(res_a.weighted_rr_honest - 2.25) <= 0.02, f"Expected R:R ~2.25, got {res_a.weighted_rr_honest}"
    assert res_a.target_1 == 208.24 and res_a.target_2 == 226.83 and res_a.target_3 is None
    print("  --> PASS Scenario A")

    # -------------------------------------------------------------
    # Scenario B: DASH, Trend Following
    # Entry: $233.18, ATR: $6.49, Stop: $216.95
    # T1: $261.16, T2: $284.48, T3: $278.61 (ATR wins)
    # ReachProbs: T1=42%, T2=19%, T3=22%
    # Expected: All 3 kept, Scale: 50/30/20, Honest R:R ≈ 2.37
    # -------------------------------------------------------------
    res_b = calculate_targets(
        ticker="DASH",
        entry_price=233.18,
        atr_14=6.49,
        stop_loss=216.95,
        strategy_name="Trend Following",
        override_targets=(261.16, 284.48, 278.61),
        mock_reach_probs=(0.42, 0.19, 0.22),
    )

    print("\n[Scenario B] DASH — Trend Following")
    print(f"  Entry: $233.18, Stop: $216.95, ATR: $6.49")
    print(f"  Reach Probs: T1={res_b.reach_prob_t1:.0%}, T2={res_b.reach_prob_t2:.0%}, T3={res_b.reach_prob_t3:.0%}")
    print(f"  Targets: T1=${res_b.target_1}, T2=${res_b.target_2}, T3=${res_b.target_3}")
    print(f"  Scale-out Weights: {res_b.scale_out_weights}")
    print(f"  Honest Weighted R:R: {res_b.weighted_rr_honest}")
    print(f"  Is Valid: {res_b.is_valid}")

    assert res_b.is_valid is True, "Scenario B should be valid"
    assert res_b.scale_out_weights == "50/30/20", f"Expected 50/30/20, got {res_b.scale_out_weights}"
    assert abs(res_b.weighted_rr_honest - 2.37) <= 0.02, f"Expected R:R ~2.37, got {res_b.weighted_rr_honest}"
    assert res_b.target_1 == 261.16 and res_b.target_2 == 284.48 and res_b.target_3 == 278.61
    print("  --> PASS Scenario B")

    # -------------------------------------------------------------
    # Scenario C: AAPL, Pullback Recovery
    # Entry: $220.00, ATR: $3.96, Stop: $212.30
    # Pullback 4% floor: T1 = $228.80
    # ReachProbs: T1=45%, T2=16%, T3=7% (T2 and T3 fail minimums)
    # Expected: T2/T3 removed, Scale: 70/30/0, Honest R:R ≈ 0.80
    # -------------------------------------------------------------
    res_c = calculate_targets(
        ticker="AAPL",
        entry_price=220.00,
        atr_14=3.96,
        stop_loss=212.30,
        strategy_name="Pullback Recovery",
        mock_reach_probs=(0.45, 0.16, 0.07),
    )

    print("\n[Scenario C] AAPL — Pullback Recovery")
    print(f"  Entry: $220.00, Stop: $212.30, ATR: $3.96")
    print(f"  Reach Probs: T1={res_c.reach_prob_t1:.0%}, T2={res_c.reach_prob_t2:.0%}, T3={res_c.reach_prob_t3:.0%}")
    print(f"  Targets: T1=${res_c.target_1}, T2=${res_c.target_2}, T3=${res_c.target_3}")
    print(f"  Scale-out Weights: {res_c.scale_out_weights}")
    print(f"  Honest Weighted R:R: {res_c.weighted_rr_honest}")
    print(f"  Is Valid: {res_c.is_valid}")

    assert res_c.is_valid is True, "Scenario C should be valid"
    assert res_c.target_1 == 228.80, f"Expected T1=228.80, got {res_c.target_1}"
    assert res_c.target_2 is None, f"Expected T2=None, got {res_c.target_2}"
    assert res_c.target_3 is None, f"Expected T3=None, got {res_c.target_3}"
    assert res_c.scale_out_weights == "70/30/0", f"Expected 70/30/0, got {res_c.scale_out_weights}"
    assert abs(res_c.weighted_rr_honest - 0.80) <= 0.02, f"Expected R:R ~0.80, got {res_c.weighted_rr_honest}"
    print("  --> PASS Scenario C")

    # -------------------------------------------------------------
    # Scenario D: JNJ, Pullback Recovery
    # Entry: $165.00, ATR: $1.98, Stop: $159.23
    # T1: $178.20 (fixed wins)
    # ReachProb(T1) = 12%
    # Expected: Signal rejected. is_valid = False. No row emitted.
    # -------------------------------------------------------------
    res_d = calculate_targets(
        ticker="JNJ",
        entry_price=165.00,
        atr_14=1.98,
        stop_loss=159.23,
        strategy_name="Pullback Recovery",
        mock_reach_probs=(0.12, 0.05, 0.02),
    )

    print("\n[Scenario D] JNJ — Pullback Recovery")
    print(f"  Entry: $165.00, Stop: $159.23, ATR: $1.98")
    print(f"  Reach Probs: T1={res_d.reach_prob_t1:.0%}, T2={res_d.reach_prob_t2:.0%}, T3={res_d.reach_prob_t3:.0%}")
    print(f"  Is Valid: {res_d.is_valid}")
    print(f"  Rejection Reason: {res_d.rejection_reason}")

    assert res_d.is_valid is False, "Scenario D should be rejected"
    print("  --> PASS Scenario D")

    print("\n" + "=" * 80)
    print("  ALL 4 ACCEPTANCE CRITERIA SCENARIOS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_acceptance_tests()
