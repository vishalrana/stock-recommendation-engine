"""
Risk Module
===========
Purpose: Construct trade management metrics for qualified signals.

Single Responsibility: Stop loss, resistance, upside, and risk-reward calculation.
"""

from typing import Optional
import pandas as pd

from config import TARGET_R_MULTIPLE

MIN_SWING_LOOKBACK = 20


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of the DataFrame with uppercase column names."""
    normalized = df.copy()
    normalized.columns = normalized.columns.str.upper()
    return normalized


def find_swing_low(df: pd.DataFrame) -> Optional[float]:
    """
    Find the most recent valid swing low in the last 20 trading days.

    A swing low is a candle where Low[i] is lower than lows at i-1, i-2, i+1, and i+2.
    The function returns the nearest valid swing low below the current close price.
    """
    df = _normalize_columns(df)
    lookback = df.tail(MIN_SWING_LOOKBACK)

    if len(lookback) < 5 or "LOW" not in lookback.columns or "CLOSE" not in lookback.columns:
        return None

    lows = lookback["LOW"].to_numpy()
    current_price = float(lookback["CLOSE"].iloc[-1])

    # Search from most recent eligible candle backwards
    for index in range(len(lows) - 3, 1, -1):
        candidate_low = float(lows[index])
        if candidate_low >= current_price:
            continue

        prior2 = float(lows[index - 2])
        prior1 = float(lows[index - 1])
        next1 = float(lows[index + 1])
        next2 = float(lows[index + 2])

        if (
            candidate_low < prior2
            and candidate_low < prior1
            and candidate_low < next1
            and candidate_low < next2
        ):
            return candidate_low

    return None


def calculate_exit_price(entry_price: float, stop_loss: float) -> Optional[float]:
    """
    Calculate exit price using a fixed R-multiple target.

    exit_price = entry_price + (risk * TARGET_R_MULTIPLE)
    where risk = entry_price - stop_loss
    """
    risk = entry_price - stop_loss
    if risk <= 0:
        return None
    return float(round(entry_price + (risk * TARGET_R_MULTIPLE), 2))


def calculate_upside_pct(entry_price: float, exit_price: float) -> float:
    """Return upside potential as a percentage."""
    if entry_price <= 0:
        return 0.0
    return round(((exit_price - entry_price) / entry_price) * 100.0, 2)


def calculate_risk_reward(entry_price: float, stop_loss: float, exit_price: float) -> Optional[float]:
    """Return the risk/reward ratio."""
    risk = entry_price - stop_loss
    reward = exit_price - entry_price
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 2)


def evaluate_trade(signal: dict, df: pd.DataFrame) -> tuple[Optional[dict], dict]:
    """
    Evaluate a candidate signal and return both the constructed trade and debug data.

    The returned debug dictionary is always populated for visibility.
    """
    debug = {
        "ticker": signal.get("ticker"),
        "pattern": signal.get("pattern"),
        "entry_price": round(float(signal.get("entry_price", 0.0)), 2),
        "stop_loss": None,
        "exit_price": None,
        "upside_pct": None,
        "risk_reward": None,
        "target_r_multiple": TARGET_R_MULTIPLE,
        "removal_reason": None,
    }

    if df is None or df.empty:
        debug["removal_reason"] = "INVALID_ENTRY"
        return None, debug

    entry_price = float(signal.get("entry_price", 0.0))
    if entry_price <= 0:
        debug["removal_reason"] = "INVALID_ENTRY"
        return None, debug

    stop_loss = find_swing_low(df)
    debug["stop_loss"] = round(stop_loss, 2) if stop_loss is not None else None
    if stop_loss is None:
        debug["removal_reason"] = "NO_SWING_LOW"
        return None, debug

    if stop_loss >= entry_price:
        debug["removal_reason"] = "INVALID_STOP"
        return None, debug

    exit_price = calculate_exit_price(entry_price, stop_loss)
    debug["exit_price"] = round(exit_price, 2) if exit_price is not None else None
    if exit_price is None or exit_price <= entry_price:
        debug["removal_reason"] = "INVALID_STOP"
        return None, debug

    upside_pct = calculate_upside_pct(entry_price, exit_price)
    debug["upside_pct"] = upside_pct

    risk_reward = calculate_risk_reward(entry_price, stop_loss, exit_price)
    debug["risk_reward"] = risk_reward
    if risk_reward is None or risk_reward < 2.0:
        debug["removal_reason"] = "RISK_REWARD_BELOW_2"
        return None, debug

    debug["removal_reason"] = "PASSED"

    updated = signal.copy()
    updated["stop_loss"] = round(stop_loss, 2)
    updated["exit_price"] = round(exit_price, 2)
    updated["upside_pct"] = upside_pct
    updated["risk_reward"] = risk_reward
    updated["target_r_multiple"] = TARGET_R_MULTIPLE
    return updated, debug


def construct_trade(signal: dict, df: pd.DataFrame) -> Optional[dict]:
    """
    Enrich a qualified signal with stop loss, exit price, upside, and risk reward.

    Returns an updated signal dictionary or None if the trade does not meet risk criteria.
    """
    trade, _ = evaluate_trade(signal, df)
    return trade
