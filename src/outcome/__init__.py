"""
Outcome Calculation Package
"""

from src.outcome.outcome_calculator import (
    evaluate_signal_outcome,
    calculate_static_scale_out_return,
    get_effective_scale_out_weights,
    SAME_DAY_AMBIGUITY_POLICY,
)

__all__ = [
    "evaluate_signal_outcome",
    "calculate_static_scale_out_return",
    "get_effective_scale_out_weights",
    "SAME_DAY_AMBIGUITY_POLICY",
]
