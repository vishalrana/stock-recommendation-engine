"""
Swing Signals — Exit Engine & Position Lifecycle
=================================================
Exact implementation of pre-registered exit rules:

Resting exits (GTC orders placed at fill):
- Checked on each holding session s in this order:
  1. stop: if open <= stop -> exit at open; else if low <= stop -> exit at stop
  2. target (T only): if open >= target -> exit at open; else if high >= target -> exit at target
  3. Collision: if both stop and target touched on same bar -> STOP_FIRST

Rule exits:
- Decided at close s, executed at open s+1:
  - Time limits:
      T: holding session 60
      E: holding session 75
      N: holding session 40
      P: holding session 10
      I: holding session 40
  - Next earnings (E only): close of next reaction session d0' + 1
  - SMA5 exit (P only): first close s on or after fill with Close(s) > SMA5(s)
  - Event lock: any close s where Event-locked(s) is true
  - Pre-earnings exit X1 (T only overlay): close of d0 - 2 (d0 is next reaction session)

Sensitivities:
- close_checked_stops: stops evaluated at close and executed at next open.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


@dataclass
class SwingTrade:
    ticker: str
    sleeve: str
    signal_date: date
    fill_date: date
    fill_price: float
    stop_loss: float
    target: Optional[float]
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "stop", "target", "time_exit", "next_earnings", "sma5_exit", "event_lock", "pre_earnings_x1", "censored"
    holding_sessions: int = 0
    beta: Optional[float] = None
    net_return_pct: Optional[float] = None
    spy_return_pct: Optional[float] = None
    excess_pct: Optional[float] = None
    beta_adj_pct: Optional[float] = None
    half_spread_pct: Optional[float] = None
    details: Optional[Dict[str, Any]] = None


def max_holding_sessions_for_sleeve(sleeve: str) -> int:
    """Holding session limit at which time exit is decided at close."""
    if sleeve == "T":
        return 60
    if sleeve == "E":
        return 75
    if sleeve == "N":
        return 40
    if sleeve == "P":
        return 10
    if sleeve == "I":
        return 40
    return 40


def simulate_swing_trade(
    ticker: str,
    sleeve: str,
    signal_date: date,
    stop_loss: float,
    target: Optional[float],
    bars: pd.DataFrame,  # columns: TR_OPEN, TR_HIGH, TR_LOW, TR_CLOSE, SMA5, etc. Indexed by session date
    spy_bars: pd.DataFrame,  # TR_OPEN, TR_CLOSE indexed by session date
    next_reaction_sessions: Sequence[date] = (),
    event_locked_sessions: Sequence[date] = (),
    beta: Optional[float] = None,
    half_spread: Optional[float] = None,
    cost_per_side: float = 0.10,
    overlay_x1: bool = False,
    close_checked_stops: bool = False,
) -> Optional[SwingTrade]:
    """
    Simulate single swing signal from signal_date through its exit.
    Fill is Open on session t+1.
    Returns completed SwingTrade, or None if signal cannot be filled or data runs out before exit.
    """
    dates = list(bars.index)
    if not dates:
        return None

    # Locate signal_date
    ts_sig = pd.Timestamp(signal_date)
    if ts_sig not in bars.index:
        # If signal_date is not in index, find position
        matching_idx = [i for i, d in enumerate(dates) if d == ts_sig or d.date() == signal_date]
        if not matching_idx:
            return None
        sig_i = matching_idx[0]
    else:
        sig_i = bars.index.get_loc(ts_sig)
        if isinstance(sig_i, slice):
            sig_i = sig_i.start

    # Fill is next session (sig_i + 1)
    fill_i = sig_i + 1
    if fill_i >= len(bars):
        return None  # No bar to fill

    fill_session = dates[fill_i]
    fill_date = fill_session.date() if isinstance(fill_session, pd.Timestamp) else fill_session

    fill_price = float(bars["TR_OPEN"].iloc[fill_i])
    if not (np.isfinite(fill_price) and fill_price > 0):
        return None

    trade = SwingTrade(
        ticker=ticker,
        sleeve=sleeve,
        signal_date=signal_date,
        fill_date=fill_date,
        fill_price=fill_price,
        stop_loss=stop_loss,
        target=target,
        beta=beta,
        half_spread_pct=half_spread,
    )

    max_hold = max_holding_sessions_for_sleeve(sleeve)
    event_lock_set = set(event_locked_sessions)

    # Next confirmed reaction session for Sleeve E or X1
    future_reaction = [r for r in next_reaction_sessions if r > signal_date]
    next_reaction = future_reaction[0] if future_reaction else None

    # Pre-extract numpy arrays for fast holding session evaluation
    o_arr = bars["TR_OPEN"].to_numpy(dtype=float)
    h_arr = bars["TR_HIGH"].to_numpy(dtype=float)
    l_arr = bars["TR_LOW"].to_numpy(dtype=float)
    c_arr = bars["TR_CLOSE"].to_numpy(dtype=float)
    sma5_arr = bars["SMA5"].to_numpy(dtype=float) if "SMA5" in bars.columns else np.full(len(bars), np.nan)

    # Loop through holding sessions starting at fill_i
    # Holding session count h_idx starts at 1
    h_idx = 0
    cur_i = fill_i

    while cur_i < len(bars):
        h_idx += 1
        s_date = dates[cur_i]
        s_day = s_date.date() if isinstance(s_date, pd.Timestamp) else s_date

        oi = o_arr[cur_i]
        hi = h_arr[cur_i]
        li = l_arr[cur_i]
        ci = c_arr[cur_i]

        # ---------------------------------------------------------
        # 1. Resting Exits (evaluated during bar s)
        # ---------------------------------------------------------
        if not close_checked_stops:
            stop_hit = False
            stop_px = oi
            if stop_loss > 0:
                if oi <= stop_loss:
                    stop_hit = True
                    stop_px = oi
                elif li <= stop_loss:
                    stop_hit = True
                    stop_px = stop_loss

            target_hit = False
            target_px = oi
            if target is not None and target > 0:
                if oi >= target:
                    target_hit = True
                    target_px = oi
                elif hi >= target:
                    target_hit = True
                    target_px = target

            # Same-bar collision: STOP_FIRST
            if stop_hit and target_hit:
                trade.exit_date = s_day
                trade.exit_price = stop_px
                trade.exit_reason = "stop"
                trade.holding_sessions = h_idx
                return _finalize_returns(trade, spy_bars, cost_per_side)

            if stop_hit:
                trade.exit_date = s_day
                trade.exit_price = stop_px
                trade.exit_reason = "stop"
                trade.holding_sessions = h_idx
                return _finalize_returns(trade, spy_bars, cost_per_side)

            if target_hit:
                trade.exit_date = s_day
                trade.exit_price = target_px
                trade.exit_reason = "target"
                trade.holding_sessions = h_idx
                return _finalize_returns(trade, spy_bars, cost_per_side)

        # ---------------------------------------------------------
        # 2. Rule Exits (decided at close of s, executed at open of s+1)
        # ---------------------------------------------------------
        rule_exit_reason = None

        # Sensitivity: close_checked_stops
        if close_checked_stops and ci <= stop_loss:
            rule_exit_reason = "stop"

        # Event-lock exit
        if rule_exit_reason is None and s_day in event_lock_set:
            rule_exit_reason = "event_lock"

        # Overlay X1 pre-earnings exit (T only): decided at the close of d0 - 2, executed at the
        # open of d0 - 1, where d0 is the next confirmed reaction session inside the hold.
        if rule_exit_reason is None and sleeve == "T" and overlay_x1 and next_reaction:
            if cur_i + 2 < len(dates):
                d_plus2 = dates[cur_i + 2].date() if isinstance(dates[cur_i + 2], pd.Timestamp) else dates[cur_i + 2]
                if d_plus2 == next_reaction:
                    rule_exit_reason = "pre_earnings_x1"

        # Sleeve E next-earnings exit: decided at close of next reaction session d0' + 1
        if rule_exit_reason is None and sleeve == "E" and next_reaction:
            # Reaction day d0' is in future_reaction; if s_day is session after d0'
            # (i.e. reaction session was at cur_i - 1), close of d0' + 1 is today
            if cur_i >= 1:
                prev_day = dates[cur_i - 1].date() if isinstance(dates[cur_i - 1], pd.Timestamp) else dates[cur_i - 1]
                if prev_day == next_reaction:
                    rule_exit_reason = "next_earnings"

        # Sleeve P SMA5 exit: first close s on or after fill with Close(s) > SMA5(s)
        if rule_exit_reason is None and sleeve == "P":
            sma5_val = sma5_arr[cur_i]
            if np.isfinite(sma5_val) and ci > sma5_val:
                rule_exit_reason = "sma5_exit"

        # Time limits: decided at close of max_hold session
        if rule_exit_reason is None and h_idx >= max_hold:
            rule_exit_reason = "time_exit"

        # If a rule exit is decided at close s, execute at open s+1
        if rule_exit_reason is not None:
            if cur_i + 1 < len(bars):
                exec_session = dates[cur_i + 1]
                exec_day = exec_session.date() if isinstance(exec_session, pd.Timestamp) else exec_session
                exec_px = o_arr[cur_i + 1]
                if np.isfinite(exec_px) and exec_px > 0:
                    trade.exit_date = exec_day
                    trade.exit_price = exec_px
                    trade.exit_reason = rule_exit_reason
                    trade.holding_sessions = h_idx
                    return _finalize_returns(trade, spy_bars, cost_per_side)
            # Decided at the last available close: the exit executes at the next open, which has no
            # bar yet. Unresolved for backtests (exit_date None); live announces the pending SELL.
            trade.exit_reason = rule_exit_reason
            trade.holding_sessions = h_idx
            trade.details = dict(trade.details or {}, pending_exit=True, decided_on=s_day.isoformat())
            return trade

        cur_i += 1

    # End of available bars without exit -> Censored (left unresolved, never force-closed)
    return None


def _finalize_returns(
    trade: SwingTrade,
    spy_bars: Any,
    cost_per_side: float,
) -> Optional[SwingTrade]:
    """Calculate net_pct, spy_pct, excess_pct, beta_adj_pct."""
    if trade.exit_price is None or trade.fill_price is None or trade.fill_price <= 0:
        return None

    # net_pct = (exit - fill) / fill x 100 - 2 x cost_per_side
    raw_ret = (trade.exit_price - trade.fill_price) / trade.fill_price * 100.0
    net_pct = raw_ret - 2.0 * cost_per_side
    trade.net_return_pct = round(net_pct, 4)

    # SPY return between the same execution points
    if isinstance(spy_bars, tuple):
        spy_open_map, spy_close_map = spy_bars
        spy_fill_px = spy_open_map.get(trade.fill_date)
        if trade.exit_reason in ("time_exit", "next_earnings", "sma5_exit", "event_lock", "pre_earnings_x1"):
            spy_exit_px = spy_open_map.get(trade.exit_date)
        else:
            spy_exit_px = spy_close_map.get(trade.exit_date)
    else:
        ts_fill = pd.Timestamp(trade.fill_date)
        ts_exit = pd.Timestamp(trade.exit_date)
        if ts_fill not in spy_bars.index or ts_exit not in spy_bars.index:
            return None
        spy_fill_px = float(spy_bars["TR_OPEN"].loc[ts_fill])
        if trade.exit_reason in ("stop", "target") and trade.exit_date == trade.fill_date:
            spy_exit_px = float(spy_bars["TR_CLOSE"].loc[ts_exit])
        elif trade.exit_reason in ("time_exit", "next_earnings", "sma5_exit", "event_lock", "pre_earnings_x1"):
            spy_exit_px = float(spy_bars["TR_OPEN"].loc[ts_exit])
        else:
            spy_exit_px = float(spy_bars["TR_CLOSE"].loc[ts_exit])

    if spy_fill_px is None or spy_exit_px is None or spy_fill_px <= 0 or spy_exit_px <= 0:
        return None

    spy_pct = (spy_exit_px - spy_fill_px) / spy_fill_px * 100.0
    trade.spy_return_pct = round(spy_pct, 4)

    excess_pct = net_pct - spy_pct
    trade.excess_pct = round(excess_pct, 4)

    if trade.beta is not None and np.isfinite(trade.beta):
        beta_adj_pct = net_pct - float(trade.beta) * spy_pct
        trade.beta_adj_pct = round(beta_adj_pct, 4)
    else:
        trade.beta_adj_pct = None

    return trade
