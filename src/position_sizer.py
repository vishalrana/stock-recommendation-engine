"""
Position Sizer Module
=====================
Cash-constrained Half-Kelly position sizing utilizing sigmoid win-probability
interpolation and honest weighted risk-to-reward ratios.
"""

import math
from typing import List, Dict, Any


def calculate_p_win(composite_score: float) -> float:
    """
    Fix 1: Replace coarse win-probability buckets with smooth sigmoid mapping:
    p_win = 0.35 + 0.40 / (1 + e^(-0.15 * (S_composite - 65)))
    Clamped to [0.35, 0.75], rounded to 4 decimal places.
    """
    s = float(composite_score)
    z = -0.15 * (s - 65.0)
    if z > 50.0:
        sigmoid_val = 0.0
    elif z < -50.0:
        sigmoid_val = 1.0
    else:
        sigmoid_val = 1.0 / (1.0 + math.exp(z))

    p = 0.35 + 0.40 * sigmoid_val
    return max(0.35, min(0.75, round(p, 4)))


def calculate_half_kelly(composite_score: float, honest_rr: float) -> float:
    """
    Calculate Half-Kelly fraction using sigmoid p_win and honest R:R.
    R_honest is used ONLY here, not in composite score.
    """
    p_win = calculate_p_win(composite_score)
    r = float(honest_rr) if float(honest_rr) > 0 else 1.0
    full_kelly = p_win - (1.0 - p_win) / r
    half_kelly = max(0.0, full_kelly / 2.0)
    return round(half_kelly, 4)


def calculate_normalized_sizing(signals: List[Dict[str, Any]], portfolio_value: float, available_cash: float) -> List[Dict[str, Any]]:
    """
    Apply cash-constrained normalization to position sizing.
    Hard-caps every single-stock capital allocation at 5.0% of portfolio value.
    Computes exact_shares (NUMERIC) and max_shares (INTEGER).
    """
    safe_cash = max(0.0, float(available_cash))
    pv = max(0.0, float(portfolio_value))
    max_single_stock_dollars = 0.05 * pv

    raw_allocs = []
    for sig in signals:
        score = float(sig.get("composite_score", sig.get("score", 50.0)))
        rr = float(sig.get("weighted_rr_honest", sig.get("weighted_rr", 2.0)))

        if "half_kelly_fraction" in sig and sig["half_kelly_fraction"] is not None:
            hk_frac = max(0.0, float(sig["half_kelly_fraction"]))
        else:
            hk_frac = calculate_half_kelly(score, rr)

        raw_dollars = min(pv * hk_frac, max_single_stock_dollars)
        raw_allocs.append(raw_dollars)

    total_needed = sum(raw_allocs)

    if safe_cash == 0.0:
        multiplier = 0.0
    elif total_needed > safe_cash and total_needed > 0.0:
        multiplier = safe_cash / total_needed
    else:
        multiplier = 1.0

    result = []
    for i, sig in enumerate(signals):
        sig_copy = sig.copy()
        final_dollar = min(raw_allocs[i] * multiplier, max_single_stock_dollars)

        entry = float(sig_copy.get("entry_price", 0.0))

        if entry > 0.0 and final_dollar > 0.0:
            exact_shares = round(final_dollar / entry, 4)
            int_shares = int(math.floor(exact_shares))
        else:
            exact_shares = 0.0
            int_shares = 0
            final_dollar = 0.0

        sig_copy["allocated_dollars"] = round(final_dollar, 2)
        sig_copy["exact_shares"] = exact_shares
        sig_copy["max_shares"] = int_shares

        alloc_pct = (final_dollar / portfolio_value * 100.0) if portfolio_value > 0 else 0.0
        if exact_shares > 0:
            if exact_shares < 1.0:
                sig_copy["position_sizing"] = f"K: {alloc_pct:.1f}% ({exact_shares:.2f} sh)"
            else:
                sig_copy["position_sizing"] = f"K: {alloc_pct:.1f}% ({int_shares} sh)"
        else:
            sig_copy["position_sizing"] = "K: 0.0%"

        result.append(sig_copy)

    return result


__all__ = [
    "calculate_p_win",
    "calculate_half_kelly",
    "calculate_normalized_sizing",
]
