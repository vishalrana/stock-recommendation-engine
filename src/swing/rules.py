"""
Swing Signals — Strategy Rules & Signal Generation
===================================================
Pre-registered sleeves and rule logic for the Swing Signals Program:
- Sleeves:
    T: Trend (C6pct >= 0.90, Close > MA200 > MA200_lag20, stop = C - 3A, target = C + 6A)
    E: Earnings Reaction (EARpct >= 0.80, AVOLpct >= 0.80, stop = C - 3A, no target)
    N: Catalyst Gap (5% gap on 3x vol, 8-K/6-K filing, stop = C - 3 * ATR_pre, no target)
    P: Large-Cap Pullback (U1-top500 / U2, Close > MA200 > MA200_lag20, RSI2 < 10, Close < SMA5)
    I: Insider Cluster (>= 2 directors/officers, >= $100k within 30d, non-routine, stop = C - 3A)

Common Gate G0 for every BUY at session t:
- In evaluated universe and Liquid(t)
- Not Event-locked(t)
- No open signal and no cooldown for (sleeve, ticker)
- No earnings within 5 sessions (t+1..t+5)
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from src.indicators import calculate_rsi
from src.patterns import wilder_atr
from src.research.stats import percentile_of
from src.swing.events import (
    check_catalyst_gap_8k,
    has_earnings_within_5_sessions,
    has_recent_neg8k,
    is_event_locked,
)
from src.swing.universe import check_liquid
from src.trade_hygiene import SessionCalendar

SLEEVES = ("T", "E", "N", "P", "I")
SLEEVE_PRIORITY = ("E", "I", "N", "P", "T")


@dataclass
class SwingSignal:
    ticker: str
    sleeve: str  # "T", "E", "N", "P", "I"
    signal_date: date  # session t
    entry_signal_close: float  # Close(t)
    stop_loss: float  # Stop price
    target: Optional[float]  # Target price (T only)
    atr14: float  # ATR14(t)
    c6_pct: Optional[float] = None
    ear_pct: Optional[float] = None
    avol_pct: Optional[float] = None
    cluster_value: Optional[float] = None
    gap_pct: Optional[float] = None
    rsi2: Optional[float] = None
    unadjusted_close: Optional[float] = None
    adv20: Optional[float] = None
    beta: Optional[float] = None
    earnings_date: Optional[date] = None
    earnings_unknown: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.sleeve, self.ticker)


def check_g0(
    ticker: str,
    sleeve: str,
    t_session: date,
    is_liquid: bool,
    event_locked: bool,
    open_signals: Set[Tuple[str, str]],
    cooldowns: Dict[Tuple[str, str], date],
    earnings_reaction_sessions: Sequence[date],
    cal: SessionCalendar,
) -> Tuple[bool, Optional[str]]:
    """
    Common gate G0 for every BUY at session t:
    - ticker is in evaluated universe and Liquid(t)
    - not Event-locked(t)
    - no open signal and no cooldown for (sleeve, ticker)
    - no earnings within 5 sessions (t+1..t+5)
    """
    if not is_liquid:
        return False, "Not liquid"

    if event_locked:
        return False, "Event locked"

    key = (sleeve, ticker)
    if key in open_signals:
        return False, "Open signal already active"

    cd_until = cooldowns.get(key)
    if cd_until is not None and t_session < cd_until:
        return False, f"In cooldown until {cd_until}"

    if has_earnings_within_5_sessions(t_session, earnings_reaction_sessions, cal):
        return False, "Earnings within 5 sessions"

    return True, None


def generate_sleeve_t_signal(
    ticker: str,
    t_session: date,
    close: float,
    ma200: float,
    ma200_lag20: float,
    c6_pct: float,
    atr14: float,
    is_g0: bool,
    beta: Optional[float] = None,
    unadj_close: Optional[float] = None,
    adv20: Optional[float] = None,
    earnings_date: Optional[date] = None,
    earnings_unknown: bool = False,
) -> Optional[SwingSignal]:
    """
    Sleeve T - Trend:
    BUY at close of t if:
    - G0
    - C6pct(t) >= 0.90
    - Close(t) > MA200(t)
    - MA200(t) > MA200(t-20)
    Levels:
    - stop = Close(t) - 3 * ATR14(t)
    - target = Close(t) + 6 * ATR14(t) (2R)
    """
    if not is_g0:
        return None
    if not (c6_pct >= 0.90):
        return None
    if not (close > ma200):
        return None
    if not (ma200 > ma200_lag20):
        return None
    if not (atr14 > 0):
        return None

    stop = round(close - 3.0 * atr14, 4)
    target = round(close + 6.0 * atr14, 4)

    return SwingSignal(
        ticker=ticker,
        sleeve="T",
        signal_date=t_session,
        entry_signal_close=close,
        stop_loss=stop,
        target=target,
        atr14=atr14,
        c6_pct=c6_pct,
        beta=beta,
        unadjusted_close=unadj_close,
        adv20=adv20,
        earnings_date=earnings_date,
        earnings_unknown=earnings_unknown,
        details={"ma200": ma200, "ma200_lag20": ma200_lag20},
    )


def compute_ear_and_avol(
    ticker_close: pd.Series,
    ticker_vol: pd.Series,
    benchmark_close: pd.Series,
    d0_session: date,
    cal: SessionCalendar,
) -> Optional[Tuple[float, float]]:
    """
    Calculate EAR and AVOL around reaction session d0:
    r = total-return close-to-close return
    b = sector ETF or SPY
    EAR = sum over sessions d0-1..d0+1 of (r_stock - r_b)
    AVOL = mean volume(d0-1..d0+1) / mean volume(d0-63..d0-8) - 1
    """
    try:
        d_minus_1 = cal.shift(d0_session, -1)
        d_plus_1 = cal.shift(d0_session, 1)
        d_minus_8 = cal.shift(d0_session, -8)
        d_minus_63 = cal.shift(d0_session, -63)
    except Exception:
        return None

    # Reaction window: d0-1, d0, d0+1
    reaction_days = [d_minus_1, d0_session, d_plus_1]
    ret_stock = []
    ret_bench = []

    for s in reaction_days:
        s_prev = cal.shift(s, -1)
        ts_s = pd.Timestamp(s)
        ts_prev = pd.Timestamp(s_prev)
        if ts_s not in ticker_close.index or ts_prev not in ticker_close.index:
            return None
        if ts_s not in benchmark_close.index or ts_prev not in benchmark_close.index:
            return None

        p_curr = float(ticker_close.loc[ts_s])
        p_prev = float(ticker_close.loc[ts_prev])
        b_curr = float(benchmark_close.loc[ts_s])
        b_prev = float(benchmark_close.loc[ts_prev])
        if p_prev <= 0 or b_prev <= 0:
            return None

        ret_stock.append(p_curr / p_prev - 1.0)
        ret_bench.append(b_curr / b_prev - 1.0)

    ear = float(sum(rs - rb for rs, rb in zip(ret_stock, ret_bench)))

    # AVOL calculation:
    react_vol = []
    for s in reaction_days:
        ts_s = pd.Timestamp(s)
        if ts_s in ticker_vol.index:
            react_vol.append(float(ticker_vol.loc[ts_s]))
    if len(react_vol) < 3:
        return None

    mean_react_vol = float(np.mean(react_vol))

    # Baseline volume: d0-63..d0-8
    base_vols = []
    curr = d_minus_63
    while curr <= d_minus_8:
        ts_c = pd.Timestamp(curr)
        if ts_c in ticker_vol.index:
            base_vols.append(float(ticker_vol.loc[ts_c]))
        curr = cal.shift(curr, 1)

    if len(base_vols) < 30:  # Need adequate baseline
        return None

    mean_base_vol = float(np.mean(base_vols))
    if mean_base_vol <= 0:
        return None

    avol = float(mean_react_vol / mean_base_vol - 1.0)
    return ear, avol


def generate_sleeve_e_signal(
    ticker: str,
    t_session: date,  # t = d0 + 1
    close: float,
    atr14: float,
    ear: float,
    ear_pct: float,
    avol_pct: float,
    is_g0: bool,
    beta: Optional[float] = None,
    unadj_close: Optional[float] = None,
    adv20: Optional[float] = None,
    next_earnings_date: Optional[date] = None,
    earnings_unknown: bool = False,
) -> Optional[SwingSignal]:
    """
    Sleeve E - Earnings Reaction:
    BUY at close of t = d0 + 1 if:
    - G0
    - EAR > 0
    - EARpct >= 0.80
    - AVOLpct >= 0.80
    Levels:
    - stop = Close(t) - 3 * ATR14(t)
    - no target
    """
    if not is_g0:
        return None
    if not (ear > 0):
        return None
    if not (ear_pct >= 0.80):
        return None
    if not (avol_pct >= 0.80):
        return None
    if not (atr14 > 0):
        return None

    stop = round(close - 3.0 * atr14, 4)

    return SwingSignal(
        ticker=ticker,
        sleeve="E",
        signal_date=t_session,
        entry_signal_close=close,
        stop_loss=stop,
        target=None,
        atr14=atr14,
        ear_pct=ear_pct,
        avol_pct=avol_pct,
        beta=beta,
        unadjusted_close=unadj_close,
        adv20=adv20,
        earnings_date=next_earnings_date,
        earnings_unknown=earnings_unknown,
        details={"ear": ear},
    )


def generate_sleeve_n_signal(
    ticker: str,
    t_session: date,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    prev_close: float,
    vol: float,
    median_vol_50: float,
    pre_gap_atr14: float,
    has_8k_or_6k: bool,
    has_neg8k: bool,
    is_g0: bool,
    beta: Optional[float] = None,
    unadj_close: Optional[float] = None,
    adv20: Optional[float] = None,
    earnings_date: Optional[date] = None,
    earnings_unknown: bool = False,
) -> Optional[SwingSignal]:
    """
    Sleeve N - Catalyst Gap:
    BUY at close of gap session t if:
    - G0
    - Open(t) / Close(t-1) - 1 >= 0.05
    - Volume(t) >= 3 * median Volume(t-50..t-1)
    - Close(t) >= Open(t)
    - Close(t) - Low(t) >= 0.5 * (High(t) - Low(t))
    - at least one 8-K or 6-K accepted in (16:00 ET on t-1, 20:00 ET on t]
    - none of those 8-Ks carries a NEG8K item
    Levels:
    - stop = Close(t) - 3 * ATR14(t-1) (pre-gap ATR)
    - no target
    """
    if not is_g0:
        return None
    if prev_close <= 0 or (open_p / prev_close - 1.0 < 0.05):
        return None
    if median_vol_50 <= 0 or (vol < 3.0 * median_vol_50):
        return None
    if close_p < open_p:
        return None
    rg = high_p - low_p
    if rg > 0 and (close_p - low_p < 0.5 * rg):
        return None
    if not has_8k_or_6k:
        return None
    if has_neg8k:
        return None
    if not (pre_gap_atr14 > 0):
        return None

    stop = round(close_p - 3.0 * pre_gap_atr14, 4)
    gap_pct = round((open_p / prev_close - 1.0) * 100.0, 2)

    return SwingSignal(
        ticker=ticker,
        sleeve="N",
        signal_date=t_session,
        entry_signal_close=close_p,
        stop_loss=stop,
        target=None,
        atr14=pre_gap_atr14,
        gap_pct=gap_pct,
        beta=beta,
        unadjusted_close=unadj_close,
        adv20=adv20,
        earnings_date=earnings_date,
        earnings_unknown=earnings_unknown,
        details={"vol_ratio": round(vol / median_vol_50, 2)},
    )


def generate_sleeve_p_signal(
    ticker: str,
    t_session: date,
    close: float,
    ma200: float,
    ma200_lag20: float,
    rsi2: float,
    sma5: float,
    atr14: float,
    is_g0: bool,
    beta: Optional[float] = None,
    unadj_close: Optional[float] = None,
    adv20: Optional[float] = None,
    earnings_date: Optional[date] = None,
    earnings_unknown: bool = False,
) -> Optional[SwingSignal]:
    """
    Sleeve P - Large-Cap Pullback:
    Universe: U1-top500 (or U2)
    BUY at close of t if:
    - G0
    - Close(t) > MA200(t)
    - MA200(t) > MA200(t-20)
    - RSI2(t) < 10
    - Close(t) < SMA5(t)
    Levels:
    - stop = Close(t) - 3 * ATR14(t)
    - no target
    """
    if not is_g0:
        return None
    if not (close > ma200):
        return None
    if not (ma200 > ma200_lag20):
        return None
    if not (rsi2 < 10.0):
        return None
    if not (close < sma5):
        return None
    if not (atr14 > 0):
        return None

    stop = round(close - 3.0 * atr14, 4)

    return SwingSignal(
        ticker=ticker,
        sleeve="P",
        signal_date=t_session,
        entry_signal_close=close,
        stop_loss=stop,
        target=None,
        atr14=atr14,
        rsi2=rsi2,
        beta=beta,
        unadjusted_close=unadj_close,
        adv20=adv20,
        earnings_date=earnings_date,
        earnings_unknown=earnings_unknown,
        details={"sma5": sma5, "ma200": ma200},
    )


def generate_sleeve_i_signal(
    ticker: str,
    t_session: date,
    close: float,
    atr14: float,
    cluster_info: Dict[str, Any],
    is_g0: bool,
    beta: Optional[float] = None,
    unadj_close: Optional[float] = None,
    adv20: Optional[float] = None,
    earnings_date: Optional[date] = None,
    earnings_unknown: bool = False,
) -> Optional[SwingSignal]:
    """
    Sleeve I - Insider Cluster Buying:
    Cluster completed on filing date D:
    - >= 2 distinct reporting owners (officer/director) made qualifying code-P purchases
    - filing dates within [D - 30 calendar days, D]
    - total value >= $100,000
    - at least one qualifying filing came after last fired I signal
    Timing: t = first NYSE session strictly after D.
    BUY at close of t if:
    - G0
    - Cluster completed on D
    Levels:
    - stop = Close(t) - 3 * ATR14(t)
    - no target
    """
    if not is_g0:
        return None
    if not (atr14 > 0):
        return None

    stop = round(close - 3.0 * atr14, 4)
    cluster_val = float(cluster_info.get("total_value", 0.0))

    return SwingSignal(
        ticker=ticker,
        sleeve="I",
        signal_date=t_session,
        entry_signal_close=close,
        stop_loss=stop,
        target=None,
        atr14=atr14,
        cluster_value=cluster_val,
        beta=beta,
        unadjusted_close=unadj_close,
        adv20=adv20,
        earnings_date=earnings_date,
        earnings_unknown=earnings_unknown,
        details=cluster_info,
    )
