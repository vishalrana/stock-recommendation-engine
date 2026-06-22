"""
Patterns Module
===============
Purpose: Detect price action patterns using pure pandas.

Single Responsibility: Pattern recognition and entry-price calculation only.
"""

from typing import Optional, Tuple
import pandas as pd


def is_bullish_engulfing(latest: pd.Series, prior: pd.Series) -> bool:
    """Detect bull engulfing using the last two candles."""
    prior_open = float(prior["OPEN"])
    prior_close = float(prior["CLOSE"])
    current_open = float(latest["OPEN"])
    current_close = float(latest["CLOSE"])

    prior_bearish = prior_close < prior_open
    current_bullish = current_close > current_open
    body_engulf = current_open < prior_close and current_close > prior_open
    larger_body = abs(current_close - current_open) > abs(prior_close - prior_open)

    return prior_bearish and current_bullish and body_engulf and larger_body


def is_hammer(latest: pd.Series) -> bool:
    """Detect hammer with quality filters."""
    open_price = float(latest["OPEN"])
    close_price = float(latest["CLOSE"])
    high_price = float(latest["HIGH"])
    low_price = float(latest["LOW"])

    body = abs(close_price - open_price)
    lower_wick = min(open_price, close_price) - low_price
    upper_wick = high_price - max(open_price, close_price)
    candle_range = high_price - low_price

    if candle_range <= 0 or body <= 0:
        return False

    body_position = (max(open_price, close_price) - low_price) / candle_range
    lower_wick_ratio = lower_wick / body if body > 0 else 0
    upper_wick_ratio = upper_wick / body if body > 0 else 0

    return (
        close_price > open_price
        and lower_wick_ratio >= 2.0
        and upper_wick_ratio <= 1.0
        and body_position >= 0.66
    )


def is_inside_bar(latest: pd.Series, prior: pd.Series) -> bool:
    """Detect inside bar using the previous candle."""
    return (
        float(latest["HIGH"]) < float(prior["HIGH"])
        and float(latest["LOW"]) > float(prior["LOW"])
    )


def calculate_entry_price(pattern: str, latest: pd.Series) -> float:
    """Calculate entry price based on the detected pattern."""
    high_price = float(latest["HIGH"])
    close_price = float(latest["CLOSE"])

    if pattern == "Bullish Engulfing":
        return round(high_price * 1.001, 2)
    if pattern == "Hammer":
        return round(high_price * 1.001, 2)
    if pattern == "Inside Bar":
        return round(high_price * 1.001, 2)

    return round(close_price, 2)


def detect_pattern(df: pd.DataFrame) -> Tuple[Optional[str], Optional[float]]:
    """Identify the latest price action pattern and calculate entry price."""
    if df is None or len(df) < 2:
        return None, None

    latest = df.iloc[-1]
    prior = df.iloc[-2]

    if is_bullish_engulfing(latest, prior):
        pattern = "Bullish Engulfing"
    elif is_hammer(latest):
        pattern = "Hammer"
    elif is_inside_bar(latest, prior):
        pattern = "Inside Bar"
    else:
        return None, None

    entry_price = calculate_entry_price(pattern, latest)
    return pattern, entry_price
