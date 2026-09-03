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


def assign_tier(composite_score: float, honest_rr: float) -> str:
    """
    Assign signal tier based on composite score and honest risk-to-reward ratio:
    - Strong Buy: composite_score >= 80.0 and honest_rr >= 1.50
    - Buy: (composite_score >= 65.0 and honest_rr >= 1.20) or (composite_score >= 45.0 and honest_rr >= 3.0)
    - Rejected: all others
    """
    score = float(composite_score)
    rr = float(honest_rr or 0.0)

    if score >= 80.0 and rr >= 1.50:
        return "Strong Buy"
    elif (score >= 65.0 and rr >= 1.20) or (score >= 45.0 and rr >= 3.0):
        return "Buy"
    else:
        return "Rejected"


def allocate_capital(
    signals: List[Any],
    portfolio_value: float,
    cash_balance: float,
) -> tuple[List[Any], List[Any]]:
    """
    Rank signals by composite score DESC, then fund sequentially.
    Stop when cash runs out or Kelly turns negative.

    Hard-caps every single-stock capital allocation at 5.0% of portfolio value.
    Minimum allocation = 1.0% of portfolio ($100 on a $10,000 portfolio).
    Any signal that cannot be allocated at least the minimum gets marked 'Cash constrained'.

    Returns:
        tuple (funded_signals, cash_constrained_signals)
    """
    pv = max(0.0, float(portfolio_value))
    remaining_cash = max(0.0, float(cash_balance))

    MIN_ALLOCATION_PCT = 0.01  # 1% of portfolio = $100 minimum
    MIN_ALLOCATION_DOLLARS = pv * MIN_ALLOCATION_PCT  # $100
    MAX_SINGLE_STOCK_PCT = 0.05  # 5% cap invariant
    MAX_SINGLE_STOCK_DOLLARS = pv * MAX_SINGLE_STOCK_PCT

    def _get(sig, key, default=None):
        if isinstance(sig, dict):
            return sig.get(key, default)
        return getattr(sig, key, default)

    def _set(sig, key, val):
        if isinstance(sig, dict):
            sig[key] = val
        else:
            setattr(sig, key, val)

    # Sort by composite score (best first)
    ranked = sorted(
        signals,
        key=lambda s: float(_get(s, "composite_score", _get(s, "score", 0.0)) or 0.0),
        reverse=True,
    )

    funded = []
    cash_constrained = []

    for signal in ranked:
        score = float(_get(signal, "composite_score", _get(signal, "score", 50.0)) or 50.0)
        rr = float(_get(signal, "weighted_rr_honest", _get(signal, "weighted_rr", 2.0)) or 2.0)

        # Determine half_kelly_fraction
        hk_frac = _get(signal, "kelly_fraction")
        if hk_frac is None:
            hk_frac = _get(signal, "half_kelly_fraction")
        if hk_frac is None:
            hk_frac = calculate_half_kelly(score, rr)
        else:
            hk_frac = float(hk_frac)

        _set(signal, "kelly_fraction", hk_frac)
        _set(signal, "half_kelly_fraction", hk_frac)

        # Skip if Kelly is negative or zero
        if hk_frac <= 0:
            _set(signal, "allocated_dollars", 0.0)
            _set(signal, "exact_shares", 0.0)
            _set(signal, "max_shares", 0)
            _set(signal, "position_sizing", "K: 0.0%")
            _set(signal, "rejection_reason", f"Kelly ≤ 0 (Honest R:R = {rr:.2f})")
            continue

        # Raw demand from Half-Kelly (already capped at 5%)
        raw_demand_val = _get(signal, "raw_dollar_demand")
        if raw_demand_val is not None and float(raw_demand_val) > 0:
            raw_demand = min(float(raw_demand_val), MAX_SINGLE_STOCK_DOLLARS)
        else:
            raw_demand = min(pv * hk_frac, MAX_SINGLE_STOCK_DOLLARS)

        raw_demand = round(raw_demand, 2)
        _set(signal, "raw_dollar_demand", raw_demand)

        # If we can't fund at least the minimum, stop
        if remaining_cash < MIN_ALLOCATION_DOLLARS or (remaining_cash <= MIN_ALLOCATION_DOLLARS and raw_demand > remaining_cash):
            _set(signal, "allocated_dollars", 0.0)
            _set(signal, "exact_shares", 0.0)
            _set(signal, "max_shares", 0)
            _set(signal, "position_sizing", "K: 0.0%")
            _set(signal, "rejection_reason", "Cash constrained")
            cash_constrained.append(signal)
            continue

        # Fund the signal
        allocation = min(raw_demand, remaining_cash)
        allocation = round(allocation, 2)

        entry = float(_get(signal, "entry_price", _get(signal, "price", 0.0)) or 0.0)
        if entry > 0.0 and allocation > 0.0:
            exact_shares = round(allocation / entry, 4)
            int_shares = int(exact_shares)
        else:
            exact_shares = 0.0
            int_shares = 0

        _set(signal, "allocated_dollars", allocation)
        _set(signal, "exact_shares", exact_shares)
        _set(signal, "max_shares", int_shares)

        alloc_pct = (allocation / pv * 100.0) if pv > 0 else 0.0
        if exact_shares > 0:
            if exact_shares < 1.0:
                _set(signal, "position_sizing", f"K: {alloc_pct:.1f}% ({exact_shares:.2f} sh)")
            else:
                _set(signal, "position_sizing", f"K: {alloc_pct:.1f}% ({int_shares} sh)")
        else:
            _set(signal, "position_sizing", "K: 0.0%")

        remaining_cash -= allocation
        funded.append(signal)

    return funded, cash_constrained


def calculate_normalized_sizing(
    signals: List[Dict[str, Any]],
    portfolio_value: float,
    available_cash: float,
) -> List[Dict[str, Any]]:
    """
    Backwards-compatible wrapper calling allocate_capital.
    Returns all signals with updated allocated_dollars, exact_shares, max_shares,
    position_sizing, and rejection_reason.
    """
    funded, cash_constrained = allocate_capital(signals, portfolio_value, available_cash)
    all_sized = funded + cash_constrained
    funded_ids = {id(s) for s in all_sized}
    for s in signals:
        if id(s) not in funded_ids:
            all_sized.append(s)
    return all_sized


__all__ = [
    "calculate_p_win",
    "calculate_half_kelly",
    "allocate_capital",
    "calculate_normalized_sizing",
    "assign_tier",
]
