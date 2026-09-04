"""
Risk Controls Module
====================
Enforces drawdown protection and market volatility risk multipliers.
Master Specification v2.3+ compliant.
"""

from typing import Tuple, Dict, Any


def get_drawdown_multiplier(portfolio_value: float, peak_value: float) -> Tuple[float, str]:
    """
    Calculate portfolio drawdown multiplier and status string.
    Thresholds:
    - dd < 5.0%: 1.0, "normal"
    - 5.0% <= dd < 10.0%: 0.75, "warning"
    - 10.0% <= dd < 15.0%: 0.50, "severe"
    - dd >= 15.0%: 0.0, "halt"
    """
    pv = float(portfolio_value)
    peak = float(peak_value)
    if not peak or peak <= 0 or pv <= 0:
        return 1.0, "normal"

    dd = (peak - pv) / peak * 100.0
    if dd < 5.0:
        return 1.0, "normal"
    if dd < 10.0:
        return 0.75, "warning"
    if dd < 15.0:
        return 0.50, "severe"
    return 0.0, "halt"


def enforce_risk_controls(
    signal: Dict[str, Any],
    portfolio_value: float,
    peak_value: float,
    vix_size_mult: float = 1.0,
) -> Tuple[bool, float, str]:
    """
    P0-5: Enforce portfolio drawdown and VIX risk controls.
    
    Returns:
        (is_allowed: bool, combined_multiplier: float, reason: str)
    """
    dd_mult, dd_status = get_drawdown_multiplier(portfolio_value, peak_value)

    # Severe drawdown (>=15%): complete halt on all new allocations
    if dd_mult <= 0.0 or dd_status == "halt":
        return False, 0.0, "Trading halted: drawdown >= 15%"

    # 10-15% drawdown: only candidates with composite_score >= 80 allowed
    score = float(signal.get("composite_score", signal.get("score", 0.0)) or 0.0)
    if dd_status == "severe" and score < 80.0:
        return False, 0.0, f"Drawdown severe (10-15%): score {score:.1f} < 80 blocked"

    # VIX multiplier check
    vix_mult = float(vix_size_mult)
    if vix_mult <= 0.0:
        return False, 0.0, "VIX emergency halt"

    combined_mult = round(dd_mult * vix_mult, 4)
    if combined_mult <= 0.0:
        return False, 0.0, "Risk multiplier reduced allocation to 0"

    return True, combined_mult, f"Drawdown: {dd_status} ({dd_mult:.2f}), VIX mult: {vix_mult:.2f}"


__all__ = [
    "get_drawdown_multiplier",
    "enforce_risk_controls",
]
