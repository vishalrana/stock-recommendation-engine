"""
Swing Signals — Book Simulation & Cooldown Manager
===================================================
Manages position tracking, cooldowns, and combined book resolution:
- Standalone sleeve evaluation (PASS rules): each sleeve operates independently.
- Combined book: one open signal per ticker across all sleeves.
  Priority when multiple sleeves fire for the same ticker on date t: E > I > N > P > T.
- Cooldown: no new signal for the same (sleeve, ticker) until 10 sessions after exit execution.
- Capital: equal unit per signal. Track record is the sum of returns in units.
- Concurrent open signals per session: capital requirement distribution.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from src.swing.exits import SwingTrade
from src.swing.rules import SLEEVE_PRIORITY, SwingSignal
from src.trade_hygiene import SessionCalendar


class SwingBook:
    """Simulates book lifecycle for standalone sleeve or combined sleeves."""

    def __init__(self, mode: str = "standalone", cal: Optional[SessionCalendar] = None):
        """
        mode: "standalone" (independent per sleeve) or "combined" (E > I > N > P > T, 1 pos/ticker)
        """
        self.mode = mode
        self.cal = cal or SessionCalendar()
        self.open_trades: Dict[Tuple[str, str], SwingTrade] = {}  # (sleeve, ticker) -> trade
        self.cooldown_until: Dict[Tuple[str, str], date] = {}  # (sleeve, ticker) -> allowed_date
        self.closed_trades: List[SwingTrade] = []
        self.daily_open_counts: Dict[date, int] = {}

    def is_in_cooldown(self, sleeve: str, ticker: str, current_date: date) -> bool:
        """Check if (sleeve, ticker) is in cooldown on current_date."""
        allowed_date = self.cooldown_until.get((sleeve, ticker))
        if allowed_date is None:
            return False
        return current_date < allowed_date

    def has_open_position(self, sleeve: str, ticker: str) -> bool:
        """Check if ticker has open position (in sleeve for standalone, or any sleeve for combined)."""
        if self.mode == "combined":
            return any(t == ticker for (_, t) in self.open_trades.keys())
        return (sleeve, ticker) in self.open_trades

    def resolve_candidate_signals(self, candidates: List[SwingSignal]) -> List[SwingSignal]:
        """
        Filter candidate signals based on open positions, cooldowns, and sleeve priority.
        """
        valid: List[SwingSignal] = []

        if self.mode == "standalone":
            for sig in candidates:
                key = (sig.sleeve, sig.ticker)
                if key in self.open_trades:
                    continue
                if self.is_in_cooldown(sig.sleeve, sig.ticker, sig.signal_date):
                    continue
                valid.append(sig)
            return valid

        # Combined mode: group by ticker and apply priority E > I > N > P > T
        by_ticker: Dict[str, List[SwingSignal]] = defaultdict(list)
        for sig in candidates:
            # Check if ticker already open in any sleeve
            if any(t == sig.ticker for (_, t) in self.open_trades.keys()):
                continue
            if self.is_in_cooldown(sig.sleeve, sig.ticker, sig.signal_date):
                continue
            by_ticker[sig.ticker].append(sig)

        priority_rank = {sleeve: i for i, sleeve in enumerate(SLEEVE_PRIORITY)}

        for ticker, sigs in by_ticker.items():
            # Sort by priority rank
            sigs.sort(key=lambda s: priority_rank.get(s.sleeve, 99))
            valid.append(sigs[0])

        return valid

    def add_trade(self, trade: SwingTrade) -> None:
        """Register newly filled trade."""
        key = (trade.sleeve, trade.ticker)
        self.open_trades[key] = trade

    def close_trade(self, trade: SwingTrade) -> None:
        """Close trade, record result, and start 10-session cooldown."""
        key = (trade.sleeve, trade.ticker)
        if key in self.open_trades:
            del self.open_trades[key]

        self.closed_trades.append(trade)

        # Cooldown: 10 sessions after exit execution
        if trade.exit_date is not None:
            # New signal permitted after 10 sessions
            allowed_date = self.cal.shift(trade.exit_date, 11)
            self.cooldown_until[key] = allowed_date

    def record_daily_occupancy(self, session_date: date) -> None:
        """Record count of currently active open trades."""
        self.daily_open_counts[session_date] = len(self.open_trades)

    def summary_statistics(self) -> Dict[str, Any]:
        """Calculate unit return sum, concurrent open signals distribution, etc."""
        closed = self.closed_trades
        n_closed = len(closed)
        if n_closed == 0:
            return {
                "n_trades": 0,
                "total_units": 0.0,
                "mean_beta_adj": None,
                "concurrent_median": 0,
                "concurrent_p95": 0,
                "concurrent_max": 0,
            }

        beta_adjs = [t.beta_adj_pct for t in closed if t.beta_adj_pct is not None and np.isfinite(t.beta_adj_pct)]
        net_rets = [t.net_return_pct for t in closed if t.net_return_pct is not None and np.isfinite(t.net_return_pct)]

        occupancies = list(self.daily_open_counts.values()) if self.daily_open_counts else [0]

        return {
            "n_trades": n_closed,
            "total_beta_adj_units": round(float(sum(beta_adjs)), 2) if beta_adjs else 0.0,
            "total_net_units": round(float(sum(net_rets)), 2) if net_rets else 0.0,
            "mean_beta_adj_pct": round(float(np.mean(beta_adjs)), 4) if beta_adjs else None,
            "mean_net_return_pct": round(float(np.mean(net_rets)), 4) if net_rets else None,
            "concurrent_median": int(np.median(occupancies)) if occupancies else 0,
            "concurrent_p95": int(np.percentile(occupancies, 95)) if occupancies else 0,
            "concurrent_max": int(np.max(occupancies)) if occupancies else 0,
        }
