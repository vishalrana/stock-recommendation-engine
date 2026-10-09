"""
Canonical Outcome Calculator Module
====================================
Master Architecture & Quantitative Specification v2.3+ Compliant

Provides single source of truth for historical signal outcome resolution,
scale-out path returns (50% T1, 30% T2, 20% T3), breakeven stop ratcheting,
and deterministic same-day ambiguity policy (SAME_DAY_AMBIGUITY_POLICY = "STOP_FIRST").

Example Scale-Out Return:
    Entry: $100.00
    Target 1: $110.00 (+10.0%) -> 50% scale-out = +5.0%
    Target 2: $120.00 (+20.0%) -> 30% scale-out = +6.0%
    Target 3: $130.00 (+30.0%) -> 20% scale-out = +6.0%
    Total Realized Return = +17.0% (NOT +30.0%)
"""

from typing import Dict, Any, Optional, Tuple
import datetime
import math
import pandas as pd
import numpy as np

# Canonical policy: when both stop and target are breached on the same trading day,
# risk management mandates executing stop first.
SAME_DAY_AMBIGUITY_POLICY: str = "STOP_FIRST"

from enum import Enum


class PositionState(str, Enum):
    OPEN = "open"
    T1_HIT = "hit_t1"
    T2_HIT = "hit_t2"
    T3_HIT = "hit_t3"
    STOPPED = "stopped"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


# Canonical scale-out weights matching quant_config.py
DEFAULT_SCALE_OUT_WEIGHTS = {
    "all_three": {"t1": 0.50, "t2": 0.30, "t3": 0.20, "runner": 0.0, "label": "50/30/20"},
    "t1_t2_only": {"t1": 0.60, "t2": 0.40, "t3": 0.0, "runner": 0.0, "label": "60/40/0"},
    "t1_only": {"t1": 0.70, "t2": 0.0, "t3": 0.0, "runner": 0.30, "label": "70/30/0"},
}


def get_scale_out_plan_weights(
    target_1: Optional[float],
    target_2: Optional[float],
    target_3: Optional[float],
) -> Tuple[float, float, float, float]:
    """
    Determine normalized scale-out weights based on available target levels.
    Returns (w1, w2, w3, w_runner) where w1 + w2 + w3 + w_runner == 1.0.
    Canonical mapping:
    - All 3 targets: 50% T1, 30% T2, 20% T3, 0% runner
    - T1 and T2: 60% T1, 40% T2, 0% T3, 0% runner
    - T1 only: 70% T1, 0% T2, 0% T3, 30% runner to breakeven
    """
    if target_1 and target_2 and target_3:
        return 0.50, 0.30, 0.20, 0.0
    elif target_1 and target_2:
        return 0.60, 0.40, 0.0, 0.0
    elif target_1:
        return 0.70, 0.0, 0.0, 0.30
    return 0.70, 0.0, 0.0, 0.30


def get_effective_scale_out_weights(
    target_1: Optional[float],
    target_2: Optional[float],
    target_3: Optional[float],
) -> Tuple[float, float, float]:
    """
    Determine normalized scale-out weights based on available target levels.
    Canonical mapping:
    - All 3 targets: 50% T1, 30% T2, 20% T3
    - T1 and T2: 60% T1, 40% T2, 0% T3
    - T1 only: 70% T1, 30% runner to breakeven
    """
    w1, w2, w3, _ = get_scale_out_plan_weights(target_1, target_2, target_3)
    return w1, w2, w3


class PositionScaleOutTracker:
    """
    Canonical deterministic position scale-out state machine (Section 16 & 17).
    Enforces the fundamental quantitative invariant:
        realized_weight + remaining_weight == 1.0
    at every state transition and lifecycle event.
    """
    def __init__(
        self,
        entry_price: float,
        stop_loss: float,
        target_1: float,
        target_2: Optional[float] = None,
        target_3: Optional[float] = None,
    ):
        self.entry_price = float(entry_price)
        self.initial_stop = float(stop_loss)
        self.current_stop = float(stop_loss)
        self.target_1 = float(target_1) if target_1 else None
        self.target_2 = float(target_2) if target_2 else None
        self.target_3 = float(target_3) if target_3 else None

        self.w1, self.w2, self.w3, self.w_runner = get_scale_out_plan_weights(
            self.target_1, self.target_2, self.target_3
        )
        assert abs(self.w1 + self.w2 + self.w3 + self.w_runner - 1.0) < 1e-6, "Weights must sum to 1.0"

        self.state = PositionState.OPEN
        self.realized_weight = 0.0
        self.remaining_weight = 1.0
        self.realized_return_pct = 0.0
        self.final_exit_price = self.current_stop
        self._check_invariant()

    @property
    def is_closed(self) -> bool:
        """True if position is completely closed (zero remaining weight)."""
        return self.remaining_weight <= 1e-6

    def _check_invariant(self):
        assert abs((self.realized_weight + self.remaining_weight) - 1.0) < 1e-6, (
            f"Weight leak invariant violated! Realized: {self.realized_weight}, Remaining: {self.remaining_weight}"
        )

    def on_stop_hit(self, exit_price: float) -> PositionState:
        """Handle stop loss breach (accounting for slippage / open gap)."""
        if self.is_closed:
            return self.state
        if self.remaining_weight > 0:
            r_stop = (exit_price - self.entry_price) / self.entry_price * 100.0
            self.realized_return_pct += self.remaining_weight * r_stop
            self.realized_weight = round(self.realized_weight + self.remaining_weight, 6)
            self.remaining_weight = 0.0
            self.final_exit_price = exit_price

        if self.state in (PositionState.T1_HIT, PositionState.T2_HIT):
            pass  # preserves milestone hit status while closed
        else:
            self.state = PositionState.STOPPED
        self._check_invariant()
        return self.state

    def on_t1_hit(self, exit_price: float) -> PositionState:
        """Handle Target 1 hit."""
        if self.is_closed or self.state != PositionState.OPEN:
            return self.state
        r1 = (exit_price - self.entry_price) / self.entry_price * 100.0
        self.realized_return_pct += self.w1 * r1
        self.realized_weight = round(self.realized_weight + self.w1, 6)
        self.remaining_weight = round(self.remaining_weight - self.w1, 6)
        self.final_exit_price = exit_price
        self.state = PositionState.T1_HIT
        # Ratchet stop to breakeven (entry price) on remaining portion
        self.current_stop = max(self.current_stop, self.entry_price)
        self._check_invariant()
        return self.state

    def on_t2_hit(self, exit_price: float) -> PositionState:
        """Handle Target 2 hit."""
        if self.is_closed or self.state not in (PositionState.OPEN, PositionState.T1_HIT) or self.w2 <= 0:
            return self.state
        if self.state == PositionState.OPEN:
            self.on_t1_hit(self.target_1)
        r2 = (exit_price - self.entry_price) / self.entry_price * 100.0
        self.realized_return_pct += self.w2 * r2
        self.realized_weight = round(self.realized_weight + self.w2, 6)
        self.remaining_weight = round(self.remaining_weight - self.w2, 6)
        self.final_exit_price = exit_price
        self.state = PositionState.T2_HIT
        # Trailing stop ratcheted to Target 1
        if self.target_1:
            self.current_stop = max(self.current_stop, self.target_1)
        self._check_invariant()
        return self.state

    def on_t3_hit(self, exit_price: float) -> PositionState:
        """Handle Target 3 hit."""
        if self.is_closed or self.state not in (PositionState.OPEN, PositionState.T1_HIT, PositionState.T2_HIT) or self.w3 <= 0:
            return self.state
        if self.state == PositionState.OPEN:
            self.on_t1_hit(self.target_1)
        if self.state == PositionState.T1_HIT and self.w2 > 0 and self.target_2:
            self.on_t2_hit(self.target_2)
        r3 = (exit_price - self.entry_price) / self.entry_price * 100.0
        self.realized_return_pct += self.w3 * r3
        self.realized_weight = round(self.realized_weight + self.w3, 6)
        self.remaining_weight = round(self.remaining_weight - self.w3, 6)
        self.final_exit_price = exit_price
        self.state = PositionState.T3_HIT
        self._check_invariant()
        return self.state

    def on_expired(self, close_price: float) -> PositionState:
        """Handle trade horizon expiry."""
        if self.is_closed:
            return self.state
        if self.remaining_weight > 0:
            r_close = (close_price - self.entry_price) / self.entry_price * 100.0
            self.realized_return_pct += self.remaining_weight * r_close
            self.realized_weight = round(self.realized_weight + self.remaining_weight, 6)
            self.remaining_weight = 0.0
            self.final_exit_price = close_price
            if self.state == PositionState.OPEN:
                self.state = PositionState.EXPIRED
        self._check_invariant()
        return self.state


def resolve_bar_event(
    open_price: Optional[float],
    high_price: float,
    low_price: float,
    close_price: float,
    stop_price: float,
    target_price: float,
    ambiguity_policy: str = SAME_DAY_AMBIGUITY_POLICY,
) -> Tuple[bool, bool, float]:
    """
    Single canonical event resolver for bar evaluation against target and stop levels.
    Evaluates open gaps and intraday high/low touch with deterministic STOP_FIRST policy.

    Execution precedence:
    1. Open Gap Check (if open price is provided):
       - Open <= stop_price => STOP_HIT at open_price
       - Open >= target_price => TARGET_HIT at open_price
    2. Intraday Bar Check:
       - Low <= stop_price and High >= target_price =>
         If ambiguity_policy == "STOP_FIRST", STOP_HIT at stop_price.
         Else TARGET_HIT at target_price.
       - Low <= stop_price => STOP_HIT at stop_price.
       - High >= target_price => TARGET_HIT at target_price.
    3. Neither touched:
       => Neither hit. Exit price defaults to close_price.

    Returns:
        (stop_hit: bool, target_hit: bool, exit_price: float)
    """
    h = float(high_price)
    l = float(low_price)
    c = float(close_price)
    s = float(stop_price)
    t = float(target_price)

    # 1. Open Gap Check (if open price is provided)
    if open_price is not None:
        o = float(open_price)
        if s > 0 and o <= s:
            return True, False, o
        if t > 0 and o >= t:
            return False, True, o

    # 2. Intraday Bar Check
    stop_touched = (s > 0 and l <= s)
    target_touched = (t > 0 and h >= t)

    if stop_touched and target_touched:
        if ambiguity_policy == "STOP_FIRST":
            return True, False, s
        else:
            return False, True, t

    if stop_touched:
        return True, False, s

    if target_touched:
        return False, True, t

    return False, False, c


def calculate_static_scale_out_return(
    entry_price: float,
    stop_loss: float,
    target_1: Optional[float],
    target_2: Optional[float] = None,
    target_3: Optional[float] = None,
    outcome: str = "hit_t3",
    exit_price: Optional[float] = None,
) -> float:
    """
    Calculate realized percentage return for a specified terminal outcome
    following canonical scale-out rules. Driven directly by PositionScaleOutTracker.
    """
    if entry_price <= 0:
        return 0.0
    if not target_1:
        px = exit_price if exit_price is not None else stop_loss
        r_exit = (px - entry_price) / entry_price * 100.0
        return float(round(r_exit, 4))

    tracker = PositionScaleOutTracker(
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        target_3=target_3,
    )

    out = str(outcome).lower()
    if out in ("stopped", "stop_loss"):
        tracker.on_stop_hit(exit_price if exit_price is not None else stop_loss)
    elif out in ("hit_t1", "take_profit_1"):
        tracker.on_t1_hit(target_1)
        # Remainder stopped at breakeven
        tracker.on_stop_hit(exit_price if exit_price is not None else tracker.current_stop)
    elif out in ("hit_t2", "take_profit_2"):
        tracker.on_t1_hit(target_1)
        if target_2:
            tracker.on_t2_hit(target_2)
        # Remainder stopped at trailing stop (T1)
        tracker.on_stop_hit(exit_price if exit_price is not None else tracker.current_stop)
    elif out in ("hit_t3", "take_profit_3"):
        tracker.on_t1_hit(target_1)
        if target_2:
            tracker.on_t2_hit(target_2)
        if target_3:
            tracker.on_t3_hit(target_3)
        if not tracker.is_closed:
            tracker.on_stop_hit(exit_price if exit_price is not None else tracker.current_stop)
    elif out in ("expired", "open", "invalidated", "manually_removed"):
        exp_px = exit_price if exit_price is not None else entry_price
        tracker.on_expired(exp_px)
    else:
        return 0.0

    return float(round(tracker.realized_return_pct, 4))


def evaluate_signal_outcome(
    df: pd.DataFrame,
    entry_price: float,
    stop_loss: float,
    target_1: float,
    target_2: Optional[float] = None,
    target_3: Optional[float] = None,
    max_holding_days: int = 20,
    ambiguity_policy: str = SAME_DAY_AMBIGUITY_POLICY,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate daily OHLC price bars against entry, stop loss, and canonical scale-out targets.
    Directly delegates position accounting and state transitions to PositionScaleOutTracker.
    """
    if df is None or df.empty or entry_price <= 0 or not target_1:
        return None

    # Column name normalization
    high_col = "High" if "High" in df.columns else ("HIGH" if "HIGH" in df.columns else None)
    low_col = "Low" if "Low" in df.columns else ("LOW" if "LOW" in df.columns else None)
    close_col = "Close" if "Close" in df.columns else ("CLOSE" if "CLOSE" in df.columns else None)
    open_col = "Open" if "Open" in df.columns else ("OPEN" if "OPEN" in df.columns else None)

    if not high_col or not low_col or not close_col:
        return None

    tracker = PositionScaleOutTracker(
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        target_3=target_3,
    )

    outcome_date_str = ""
    holding_days = 0

    for i, (idx, bar) in enumerate(df.iterrows()):
        try:
            day_open = float(bar[open_col]) if open_col is not None else None
            day_high = float(bar[high_col])
            day_low = float(bar[low_col])
            day_close = float(bar[close_col])
        except (ValueError, TypeError):
            continue

        if not (
            (day_open is None or (math.isfinite(day_open) and day_open > 0))
            and math.isfinite(day_high) and math.isfinite(day_low) and math.isfinite(day_close)
            and day_high > 0 and day_low > 0 and day_close > 0
        ):
            continue

        holding_days = i + 1

        if hasattr(idx, "date"):
            outcome_date_str = idx.date().isoformat()
        else:
            outcome_date_str = str(idx)[:10]

        # 1. CANONICAL EVENT RESOLUTION (Open Gap & STOP_FIRST Ambiguity)
        active_target = (
            tracker.target_3 if (tracker.state == PositionState.T2_HIT and tracker.target_3)
            else (tracker.target_2 if (tracker.state == PositionState.T1_HIT and tracker.target_2)
            else tracker.target_1)
        )
        stop_hit, target_hit, exit_p = resolve_bar_event(
            open_price=day_open,
            high_price=day_high,
            low_price=day_low,
            close_price=day_close,
            stop_price=tracker.current_stop,
            target_price=active_target,
            ambiguity_policy=ambiguity_policy,
        )

        if stop_hit:
            tracker.on_stop_hit(exit_p)
            return {
                "outcome": tracker.state.value,
                "outcome_return_pct": float(round(tracker.realized_return_pct, 4)),
                "outcome_date": outcome_date_str,
                "outcome_holding_days": holding_days,
                "exit_price": float(round(tracker.final_exit_price, 2)),
            }

        # 2. TARGET PROGRESSION
        if tracker.state == PositionState.OPEN and day_high >= tracker.target_1:
            tracker.on_t1_hit(tracker.target_1)

        if tracker.state == PositionState.T1_HIT and tracker.target_2 and day_high >= tracker.target_2:
            tracker.on_t2_hit(tracker.target_2)

        if tracker.state == PositionState.T2_HIT and tracker.target_3 and day_high >= tracker.target_3:
            tracker.on_t3_hit(tracker.target_3)

        if tracker.is_closed:
            return {
                "outcome": tracker.state.value,
                "outcome_return_pct": float(round(tracker.realized_return_pct, 4)),
                "outcome_date": outcome_date_str,
                "outcome_holding_days": holding_days,
                "exit_price": float(round(tracker.final_exit_price, 2)),
            }

        # 3. EXPIRY EVALUATION
        if holding_days >= max_holding_days:
            tracker.on_expired(day_close)
            return {
                "outcome": tracker.state.value,
                "outcome_return_pct": float(round(tracker.realized_return_pct, 4)),
                "outcome_date": outcome_date_str,
                "outcome_holding_days": holding_days,
                "exit_price": float(round(tracker.final_exit_price, 2)),
            }

    # If all available bars are exhausted and a milestone target was reached
    if tracker.state in (PositionState.T1_HIT, PositionState.T2_HIT):
        tracker.on_expired(day_close)
        return {
            "outcome": tracker.state.value,
            "outcome_return_pct": float(round(tracker.realized_return_pct, 4)),
            "outcome_date": outcome_date_str,
            "outcome_holding_days": holding_days,
            "exit_price": float(round(tracker.final_exit_price, 2)),
        }

    return None
