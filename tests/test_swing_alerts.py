"""
Unit Tests for Swing Alerts Dispatcher (src/alerts.py)
======================================================
Tests:
- Ordering: SELL first, then BUY, then WARN
- Character chunk splitting at 3,500 characters
- Shadow sleeves do not generate BUY notifications
- Graceful handling when APPRISE_URLS is unset
"""

import datetime as dt
from src.alerts import build_alert_message, send_alerts


def test_alert_message_ordering():
    sells = [
        {"ticker": "AAPL", "sleeve": "T", "reason": "Target 2R reached", "fill": 160.0, "net_return_pct": 5.2, "holding_sessions": 12}
    ]
    buys = [
        {"ticker": "NVDA", "sleeve": "E", "is_live": True, "entry_signal_close": 125.0, "stop_loss": 115.0, "target": None, "why_summary": "Earnings beat"}
    ]
    warns = [
        {"ticker": "MSFT", "warning": "Earnings release in 3 sessions"}
    ]

    chunks = build_alert_message(sells=sells, buys=buys, warns=warns)
    assert len(chunks) == 1
    msg = chunks[0]

    sell_idx = msg.find("SELL")
    buy_idx = msg.find("BUY")
    warn_idx = msg.find("WARNINGS")

    assert sell_idx != -1
    assert buy_idx != -1
    assert warn_idx != -1
    assert sell_idx < buy_idx < warn_idx


def test_shadow_sleeves_never_alert_buy():
    buys = [
        {"ticker": "XYZ", "sleeve": "E", "is_live": False, "entry_signal_close": 50.0, "stop_loss": 45.0, "why_summary": "Shadow setup"}
    ]
    chunks = build_alert_message(sells=[], buys=buys, warns=[])
    # When all buys are shadow and there are no sells or warns, message list should be empty
    assert len(chunks) == 0


def test_message_splitting_at_3500_chars():
    # Generate large number of sell items
    sells = [
        {
            "ticker": f"TICK{i:04d}",
            "sleeve": "T",
            "reason": "Holding limit reached after extended consolidation period",
            "fill": 100.0 + i,
            "net_return_pct": 1.25,
            "holding_sessions": 60,
        }
        for i in range(100)
    ]

    chunks = build_alert_message(sells=sells, buys=[], warns=[])
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 3500


def test_send_alerts_no_alerts_flag():
    res = send_alerts(sells=[], buys=[], warns=[], no_alerts=True)
    assert res is True
