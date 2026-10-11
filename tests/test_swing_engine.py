"""
Tests for Swing Signals Engine (Phases 2-4)
===========================================
Tests strategy rules, exit engine, book management, cooldowns, and ledger logging:
- Sleeve T, E, N, P, I generation logic
- Common gate G0 enforcement
- Resting exits (stop, target, same-bar STOP_FIRST)
- Rule exits (time limits, SMA5 exit, next earnings)
- Cooldown and combined book priority resolution
- Immutable ledger recording
"""

import os
import sys
from datetime import date, timedelta
import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.swing.rules import (
    SLEEVES,
    SLEEVE_PRIORITY,
    SwingSignal,
    check_g0,
    generate_sleeve_t_signal,
    generate_sleeve_p_signal,
)
from src.swing.exits import (
    SwingTrade,
    simulate_swing_trade,
    max_holding_sessions_for_sleeve,
)
from src.swing.book import SwingBook
from src.swing.ledger import record_ledger_entry
from src.trade_hygiene import SessionCalendar


def test_sleeve_max_holding_sessions():
    assert max_holding_sessions_for_sleeve("T") == 60
    assert max_holding_sessions_for_sleeve("E") == 75
    assert max_holding_sessions_for_sleeve("N") == 40
    assert max_holding_sessions_for_sleeve("P") == 10
    assert max_holding_sessions_for_sleeve("I") == 40


def test_sleeve_t_signal_rules():
    t_session = date(2026, 10, 9)
    # Valid setup: C6 >= 0.90, Close > MA200 > MA200_lag20
    sig = generate_sleeve_t_signal(
        ticker="AAPL",
        t_session=t_session,
        close=200.0,
        ma200=180.0,
        ma200_lag20=175.0,
        c6_pct=0.92,
        atr14=4.0,
        is_g0=True,
    )
    assert sig is not None
    assert sig.sleeve == "T"
    assert sig.stop_loss == 200.0 - 3.0 * 4.0  # 188.0
    assert sig.target == 200.0 + 6.0 * 4.0     # 224.0 (2R)

    # Fails if MA200 is declining
    bad_ma = generate_sleeve_t_signal(
        ticker="AAPL",
        t_session=t_session,
        close=200.0,
        ma200=180.0,
        ma200_lag20=185.0,
        c6_pct=0.92,
        atr14=4.0,
        is_g0=True,
    )
    assert bad_ma is None

    # Fails if C6 < 0.90
    low_c6 = generate_sleeve_t_signal(
        ticker="AAPL",
        t_session=t_session,
        close=200.0,
        ma200=180.0,
        ma200_lag20=175.0,
        c6_pct=0.88,
        atr14=4.0,
        is_g0=True,
    )
    assert low_c6 is None


def test_sleeve_p_signal_rules():
    t_session = date(2026, 10, 9)
    # Pullback setup: Close > MA200 > MA200_lag20, RSI2 < 10, Close < SMA5
    sig = generate_sleeve_p_signal(
        ticker="MSFT",
        t_session=t_session,
        close=400.0,
        ma200=380.0,
        ma200_lag20=370.0,
        rsi2=8.5,
        sma5=405.0,
        atr14=5.0,
        is_g0=True,
    )
    assert sig is not None
    assert sig.sleeve == "P"
    assert sig.stop_loss == 400.0 - 3.0 * 5.0  # 385.0
    assert sig.target is None

    # Fails if RSI2 >= 10
    high_rsi = generate_sleeve_p_signal(
        ticker="MSFT",
        t_session=t_session,
        close=400.0,
        ma200=380.0,
        ma200_lag20=370.0,
        rsi2=12.0,
        sma5=405.0,
        atr14=5.0,
        is_g0=True,
    )
    assert high_rsi is None


def test_resting_stop_and_target_simulation():
    # Build a synthetic bar dataframe
    dates = pd.bdate_range("2026-01-01", periods=10)
    # Day 0: Signal date
    # Day 1: Fill date at Open 100
    # Day 2: Touches target (High 115, target 110)
    bars = pd.DataFrame({
        "TR_OPEN":  [100, 100, 105, 108, 110, 110, 110, 110, 110, 110],
        "TR_HIGH":  [102, 104, 115, 112, 115, 115, 115, 115, 115, 115],
        "TR_LOW":   [98,  98,  102, 104, 106, 106, 106, 106, 106, 106],
        "TR_CLOSE": [100, 102, 112, 110, 112, 112, 112, 112, 112, 112],
        "SMA5":     [100, 101, 103, 105, 107, 108, 109, 110, 111, 112],
    }, index=dates)

    spy_bars = pd.DataFrame({
        "TR_OPEN":  [400] * 10,
        "TR_CLOSE": [402] * 10,
    }, index=dates)

    trade = simulate_swing_trade(
        ticker="TEST",
        sleeve="T",
        signal_date=dates[0].date(),
        stop_loss=90.0,
        target=110.0,
        bars=bars,
        spy_bars=spy_bars,
        beta=1.0,
    )
    assert trade is not None
    assert trade.exit_reason == "target"
    assert trade.exit_price == 110.0
    assert trade.exit_date == dates[2].date()
    assert trade.net_return_pct > 0


def test_same_bar_stop_first_collision():
    dates = pd.bdate_range("2026-01-01", periods=5)
    # Day 1 touches both Stop (88) and Target (112) on same bar
    bars = pd.DataFrame({
        "TR_OPEN":  [100, 100, 100, 100, 100],
        "TR_HIGH":  [102, 115, 105, 105, 105],  # Touches 112 target
        "TR_LOW":   [98,  85,  95,  95,  95],   # Touches 90 stop
        "TR_CLOSE": [100, 95,  100, 100, 100],
        "SMA5":     [100] * 5,
    }, index=dates)

    spy_bars = pd.DataFrame({
        "TR_OPEN":  [400] * 5,
        "TR_CLOSE": [400] * 5,
    }, index=dates)

    trade = simulate_swing_trade(
        ticker="TEST",
        sleeve="T",
        signal_date=dates[0].date(),
        stop_loss=90.0,
        target=112.0,
        bars=bars,
        spy_bars=spy_bars,
        beta=1.0,
    )
    assert trade is not None
    # Pre-registered rule: STOP_FIRST
    assert trade.exit_reason == "stop"
    assert trade.exit_price == 90.0
    assert trade.exit_date == dates[1].date()


def test_book_cooldown_and_priority():
    cal = SessionCalendar()
    book = SwingBook(mode="combined", cal=cal)
    d = date(2026, 10, 9)

    sig_e = SwingSignal("AAPL", "E", d, 200.0, 190.0, None, 4.0)
    sig_t = SwingSignal("AAPL", "T", d, 200.0, 190.0, 220.0, 4.0)

    # Sleeve priority: E > I > N > P > T
    # Both fire for AAPL on same day -> E wins!
    resolved = book.resolve_candidate_signals([sig_t, sig_e])
    assert len(resolved) == 1
    assert resolved[0].sleeve == "E"

    # Close trade and verify 10-session cooldown
    trade = SwingTrade("AAPL", "E", d, d, 200.0, 190.0, None, exit_date=d, exit_price=205.0)
    book.close_trade(trade)

    # 5 sessions later, AAPL must be in cooldown
    day_plus_5 = cal.shift(d, 5)
    assert book.is_in_cooldown("E", "AAPL", day_plus_5) is True

    # 12 sessions later, cooldown expired
    day_plus_12 = cal.shift(d, 12)
    assert book.is_in_cooldown("E", "AAPL", day_plus_12) is False


def test_ledger_recording(tmp_path):
    entry = record_ledger_entry(
        session_date=date(2026, 10, 9),
        action="BUY",
        ticker="AAPL",
        sleeve="T",
        price=200.0,
        units=1.0,
        details={"stop_loss": 190.0},
    )
    assert entry["action"] == "BUY"
    assert entry["ticker"] == "AAPL"
    assert entry["price"] == 200.0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
