"""
Independent Reference Quantitative Models
==========================================
Clean-room, textbook reference implementations written from scratch.
DO NOT IMPORT OR CALL ANY PRODUCTION MODULES IN THIS FILE.
Used exclusively to verify production calculations over deterministic golden datasets.
"""

import math
from typing import List, Optional, Tuple
import numpy as np


def ref_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Textbook Welles Wilder RMA Relative Strength Index.
    Seed: simple average of first period price changes (bars 1..period).
    Recursion: avg_gain_t = (avg_gain_{t-1} * (N-1) + gain_t) / N.
    """
    n = len(closes)
    result: List[Optional[float]] = [None] * n
    if n <= period:
        return result

    gains = [0.0] * n
    losses = [0.0] * n
    for t in range(1, n):
        delta = closes[t] - closes[t - 1]
        if delta > 0.0:
            gains[t] = delta
        else:
            losses[t] = -delta

    # First period changes span indices 1 .. period
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    if avg_loss == 0.0:
        result[period] = 50.0 if avg_gain == 0.0 else 100.0
    elif avg_gain == 0.0:
        result[period] = 0.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    for t in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[t]) / period
        avg_loss = (avg_loss * (period - 1) + losses[t]) / period

        if avg_loss == 0.0:
            result[t] = 50.0 if avg_gain == 0.0 else 100.0
        elif avg_gain == 0.0:
            result[t] = 0.0
        else:
            rs = avg_gain / avg_loss
            result[t] = 100.0 - (100.0 / (1.0 + rs))

    return result


def ref_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Textbook Welles Wilder RMA Average True Range.
    TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|).
    Seed ATR at index period-1: mean(TR[0:period]).
    Recursion: ATR_t = (ATR_{t-1} * (N-1) + TR_t) / N.
    """
    n = len(closes)
    result: List[Optional[float]] = [None] * n
    if n < period:
        return result

    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for t in range(1, n):
        tr[t] = max(
            highs[t] - lows[t],
            abs(highs[t] - closes[t - 1]),
            abs(lows[t] - closes[t - 1]),
        )

    seed_atr = sum(tr[:period]) / period
    result[period - 1] = seed_atr

    curr_atr = seed_atr
    for t in range(period, n):
        curr_atr = (curr_atr * (period - 1) + tr[t]) / period
        result[t] = curr_atr

    return result


def ref_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Textbook Welles Wilder Average Directional Index (ADX).
    Seed smoothed TR, +DM, -DM at index period: sum over bars 1..period.
    First valid DX at index period.
    Seed ADX at index 2*period - 1: mean of DX[period : 2*period].
    Recursion thereafter: ADX_t = (ADX_{t-1} * (N-1) + DX_t) / N.
    """
    n = len(closes)
    result: List[Optional[float]] = [None] * n
    if n < 2 * period:
        return result

    tr = [0.0] * n
    dm_p = [0.0] * n
    dm_m = [0.0] * n

    for t in range(1, n):
        tr[t] = max(
            highs[t] - lows[t],
            abs(highs[t] - closes[t - 1]),
            abs(lows[t] - closes[t - 1]),
        )
        up = highs[t] - highs[t - 1]
        down = lows[t - 1] - lows[t]
        dm_p[t] = up if (up > down and up > 0.0) else 0.0
        dm_m[t] = down if (down > up and down > 0.0) else 0.0

    tr_s = sum(tr[1:period + 1])
    dm_p_s = sum(dm_p[1:period + 1])
    dm_m_s = sum(dm_m[1:period + 1])

    dx_list: List[Optional[float]] = [None] * n

    def calc_dx(trs, dmp, dmm):
        if trs == 0.0:
            return 0.0
        dip = 100.0 * (dmp / trs)
        dim = 100.0 * (dmm / trs)
        disum = dip + dim
        if disum == 0.0:
            return 0.0
        return 100.0 * (abs(dip - dim) / disum)

    dx_list[period] = calc_dx(tr_s, dm_p_s, dm_m_s)

    for t in range(period + 1, n):
        tr_s = tr_s - (tr_s / period) + tr[t]
        dm_p_s = dm_p_s - (dm_p_s / period) + dm_p[t]
        dm_m_s = dm_m_s - (dm_m_s / period) + dm_m[t]
        dx_list[t] = calc_dx(tr_s, dm_p_s, dm_m_s)

    seed_idx = 2 * period - 1
    valid_dx = [dx_list[k] for k in range(period, seed_idx + 1) if dx_list[k] is not None]
    seed_adx = sum(valid_dx) / period
    result[seed_idx] = seed_adx

    curr_adx = seed_adx
    for t in range(seed_idx + 1, n):
        curr_adx = (curr_adx * (period - 1) + dx_list[t]) / period
        result[t] = curr_adx

    return result


def ref_ema(closes: List[float], period: int) -> List[float]:
    """Exponential Moving Average: alpha = 2 / (period + 1), adjust=False."""
    n = len(closes)
    if n == 0:
        return []
    alpha = 2.0 / (period + 1)
    result = [closes[0]]
    for t in range(1, n):
        val = alpha * closes[t] + (1.0 - alpha) * result[-1]
        result.append(val)
    return result


def ref_bayesian_win_rate(wins: int, losses: int, alpha: float = 5.0, prior: float = 50.0) -> float:
    """Beta-Binomial posterior win rate: (wins * 100 + alpha * prior) / (total + alpha)."""
    total = max(0, wins) + max(0, losses)
    if total == 0:
        return float(prior)
    return round((wins * 100.0 + alpha * prior) / (total + alpha), 2)


def ref_expectancy(expectancy_pct: float, base: float = 30.0, slope: float = 20.0) -> float:
    """Clamped expectancy score: clamp(base + slope * expectancy_pct, 0, 100)."""
    if expectancy_pct is None or math.isnan(expectancy_pct):
        return 50.0
    val = base + slope * float(expectancy_pct)
    return round(max(0.0, min(100.0, val)), 4)


def ref_reach_target_before_stop(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    opens: List[float],
    t_pct: float,
    s_pct: float,
    hold_days: int,
    lookback_days: int = 504,
) -> float:
    """
    Joint OHLC forward simulation of reach probability:
    Conservative same-bar STOP_FIRST policy.
    """
    n = len(closes)
    total_windows = n - hold_days
    if total_windows <= 0:
        return 0.0

    num_windows = min(lookback_days, total_windows)
    start_idx = total_windows - num_windows

    successes = 0
    valid_count = 0

    for d in range(start_idx, total_windows):
        p0 = closes[d]
        if p0 <= 0:
            continue
        valid_count += 1
        target_p = p0 * (1.0 + t_pct)
        stop_p = p0 * (1.0 - s_pct)
        reached = False

        for t in range(d + 1, min(d + hold_days + 1, n)):
            o_t = opens[t]
            h_t = highs[t]
            l_t = lows[t]

            # 1. Open gap
            if o_t <= stop_p:
                reached = False
                break
            if o_t >= target_p:
                reached = True
                break

            # 2. Intrabar (STOP FIRST on touch ambiguity)
            if l_t <= stop_p and h_t >= target_p:
                reached = False
                break
            if l_t <= stop_p:
                reached = False
                break
            if h_t >= target_p:
                reached = True
                break

        if reached:
            successes += 1

    return float(successes / valid_count) if valid_count > 0 else 0.0
