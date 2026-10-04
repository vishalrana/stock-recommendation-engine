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
import pandas as pd
import numpy as np

# Canonical policy: when both stop and target are breached on the same trading day,
# risk management mandates executing stop first.
SAME_DAY_AMBIGUITY_POLICY: str = "STOP_FIRST"

# Canonical scale-out weights matching quant_config.py
DEFAULT_SCALE_OUT_WEIGHTS = {
    "all_three": {"t1": 0.50, "t2": 0.30, "t3": 0.20},
    "t1_t2_only": {"t1": 0.60, "t2": 0.40, "t3": 0.0},
    "t1_only": {"t1": 1.00, "t2": 0.0, "t3": 0.0},
}


def get_effective_scale_out_weights(
    target_1: Optional[float],
    target_2: Optional[float],
    target_3: Optional[float],
) -> Tuple[float, float, float]:
    """
    Determine normalized scale-out weights based on available target levels.
    """
    if target_1 and target_2 and target_3:
        return 0.50, 0.30, 0.20
    elif target_1 and target_2:
        return 0.60, 0.40, 0.0
    elif target_1:
        return 1.00, 0.0, 0.0
    return 1.00, 0.0, 0.0


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
    following canonical scale-out rules.
    """
    if entry_price <= 0:
        return 0.0

    w1, w2, w3 = get_effective_scale_out_weights(target_1, target_2, target_3)
    r1 = ((target_1 - entry_price) / entry_price * 100.0) if target_1 else 0.0
    r2 = ((target_2 - entry_price) / entry_price * 100.0) if target_2 else 0.0
    r3 = ((target_3 - entry_price) / entry_price * 100.0) if target_3 else 0.0
    r_stop = (stop_loss - entry_price) / entry_price * 100.0

    out = str(outcome).lower()
    if out in ("stopped", "stop_loss"):
        return float(round(r_stop, 4))
    elif out in ("hit_t1", "take_profit_1"):
        # 50% at T1, remainder stopped at breakeven (0.0% return)
        return float(round(w1 * r1, 4))
    elif out in ("hit_t2", "take_profit_2"):
        # 50% at T1, 30% at T2, remaining 20% stopped at T1 (trailing stop)
        r_runner = r1 if w3 > 0 else 0.0
        return float(round(w1 * r1 + w2 * r2 + w3 * r_runner, 4))
    elif out in ("hit_t3", "take_profit_3"):
        # Full scale-out: 50% T1 + 30% T2 + 20% T3
        return float(round(w1 * r1 + w2 * r2 + w3 * r3, 4))
    elif out in ("expired", "open", "invalidated", "manually_removed") and exit_price is not None:
        return float(round((exit_price - entry_price) / entry_price * 100.0, 4))
    return 0.0


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

    Scale-out rules:
    - 50% closed at Target 1, stop moves to Breakeven for remaining 50%.
    - 30% closed at Target 2, stop for remaining 20% runner moves to Target 1.
    - 20% closed at Target 3.
    - If stopped out before T1: 100% loss at stop_loss.
    - If stopped out after T1: 50% booked at T1, 50% booked at Breakeven (0.0%). Total return = 0.50 * r1.
    - If stopped out after T2: 50% at T1, 30% at T2, 20% at T1 (trailing stop). Total = 0.50*r1 + 0.30*r2 + 0.20*r1.
    - If max_holding_days reached without complete exit: remaining position closed at final bar's Close.
    - If low <= current_stop and high >= current_target on same bar: stop evaluated first per STOP_FIRST policy.

    Returns:
        dict with {
            'outcome': 'stopped' | 'hit_t1' | 'hit_t2' | 'hit_t3' | 'expired',
            'outcome_return_pct': float,
            'outcome_date': str (YYYY-MM-DD),
            'outcome_holding_days': int,
            'exit_price': float
        } or None if trade remains open.
    """
    if df is None or df.empty or entry_price <= 0 or not target_1:
        return None

    # Column name normalization
    high_col = "High" if "High" in df.columns else ("HIGH" if "HIGH" in df.columns else None)
    low_col = "Low" if "Low" in df.columns else ("LOW" if "LOW" in df.columns else None)
    close_col = "Close" if "Close" in df.columns else ("CLOSE" if "CLOSE" in df.columns else None)

    if not high_col or not low_col or not close_col:
        return None

    w1, w2, w3 = get_effective_scale_out_weights(target_1, target_2, target_3)
    r1 = (target_1 - entry_price) / entry_price * 100.0
    r2 = ((target_2 - entry_price) / entry_price * 100.0) if target_2 else 0.0
    r3 = ((target_3 - entry_price) / entry_price * 100.0) if target_3 else 0.0

    remaining_weight = 1.0
    current_stop = float(stop_loss)
    realized_return_pct = 0.0

    hit_t1 = False
    hit_t2 = False
    hit_t3 = False

    outcome_date_str = ""
    holding_days = 0
    final_exit_price = current_stop

    for i, (idx, bar) in enumerate(df.iterrows()):
        day_high = float(bar[high_col])
        day_low = float(bar[low_col])
        day_close = float(bar[close_col])
        holding_days = i + 1

        if hasattr(idx, "date"):
            outcome_date_str = idx.date().isoformat()
        else:
            outcome_date_str = str(idx)[:10]

        # -------------------------------------------------------------
        # 1. AMBIGUITY POLICY: STOP FIRST EVALUATION
        # -------------------------------------------------------------
        if ambiguity_policy == "STOP_FIRST" and day_low <= current_stop:
            # Remaining weight stops out at current_stop
            r_stop_portion = (current_stop - entry_price) / entry_price * 100.0
            realized_return_pct += remaining_weight * r_stop_portion
            final_exit_price = current_stop
            remaining_weight = 0.0

            if hit_t2:
                outcome = "hit_t2"
            elif hit_t1:
                outcome = "hit_t1"
            else:
                outcome = "stopped"

            return {
                "outcome": outcome,
                "outcome_return_pct": float(round(realized_return_pct, 4)),
                "outcome_date": outcome_date_str,
                "outcome_holding_days": holding_days,
                "exit_price": float(round(final_exit_price, 2)),
            }

        # -------------------------------------------------------------
        # 2. TARGET PROGRESSION
        # -------------------------------------------------------------
        # Target 1
        if not hit_t1 and day_high >= target_1:
            hit_t1 = True
            realized_return_pct += w1 * r1
            remaining_weight -= w1
            final_exit_price = target_1
            # Breakeven stop ratchet on remainder
            current_stop = max(current_stop, float(entry_price))

        # Target 2 (can occur same bar as T1 if high was strong)
        if hit_t1 and not hit_t2 and target_2 and day_high >= target_2:
            hit_t2 = True
            realized_return_pct += w2 * r2
            remaining_weight -= w2
            final_exit_price = target_2
            # Trailing stop to Target 1
            current_stop = max(current_stop, float(target_1))

        # Target 3 (can occur same bar if breakout continues)
        if hit_t2 and not hit_t3 and target_3 and day_high >= target_3:
            hit_t3 = True
            realized_return_pct += w3 * r3
            remaining_weight -= w3
            final_exit_price = target_3

        # If all scale-out portions have exited
        if remaining_weight <= 0.0001:
            outcome = "hit_t3" if hit_t3 else ("hit_t2" if hit_t2 else "hit_t1")
            return {
                "outcome": outcome,
                "outcome_return_pct": float(round(realized_return_pct, 4)),
                "outcome_date": outcome_date_str,
                "outcome_holding_days": holding_days,
                "exit_price": float(round(final_exit_price, 2)),
            }

        # If STOP_FIRST was not triggered earlier and low breaches current_stop now
        # (e.g., if ambiguity_policy != STOP_FIRST or stop hit occurred after partial target)
        if day_low <= current_stop:
            r_stop_portion = (current_stop - entry_price) / entry_price * 100.0
            realized_return_pct += remaining_weight * r_stop_portion
            final_exit_price = current_stop
            remaining_weight = 0.0

            if hit_t2:
                outcome = "hit_t2"
            elif hit_t1:
                outcome = "hit_t1"
            else:
                outcome = "stopped"

            return {
                "outcome": outcome,
                "outcome_return_pct": float(round(realized_return_pct, 4)),
                "outcome_date": outcome_date_str,
                "outcome_holding_days": holding_days,
                "exit_price": float(round(final_exit_price, 2)),
            }

        # -------------------------------------------------------------
        # 3. EXPIRY EVALUATION
        # -------------------------------------------------------------
        if holding_days >= max_holding_days:
            # Position closes on expiry bar at Close
            r_close_portion = (day_close - entry_price) / entry_price * 100.0
            realized_return_pct += remaining_weight * r_close_portion
            final_exit_price = day_close
            remaining_weight = 0.0

            if hit_t2:
                outcome = "hit_t2"
            elif hit_t1:
                outcome = "hit_t1"
            else:
                outcome = "expired"

            return {
                "outcome": outcome,
                "outcome_return_pct": float(round(realized_return_pct, 4)),
                "outcome_date": outcome_date_str,
                "outcome_holding_days": holding_days,
                "exit_price": float(round(final_exit_price, 2)),
            }

    # Still open, holding_days < max_holding_days and neither stop nor complete exit reached
    return None
