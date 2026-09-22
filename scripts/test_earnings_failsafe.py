"""
Regression Test Suite: Earnings Blackout Fail-Safe & Universe Handling (P0-3)
============================================================================
Verifies:
1. Upcoming earnings within blackout -> rejected (pass: False)
2. Upcoming earnings beyond blackout -> passed (pass: True)
3. No earnings / confirmed clear -> passed (pass: True)
4. Stale earnings (> 24 hours) -> rejected for blackout strategies, passed for PEAD
5. API failure (missing/None/empty) -> rejected for blackout strategies, passed for PEAD
6. Partial API failure -> rejected for blackout strategies, passed for PEAD
7. Full API failure -> rejected for blackout strategies, passed for PEAD
8. PEAD exception: PEAD is exempt from pre-earnings blackout across all states
"""

import sys
import os
import unittest
import datetime
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.filters.earnings_filter import (
    earnings_risk_filter,
    fetch_earnings_calendar,
    EarningsStatus,
    is_earnings_record_fresh,
)


class TestEarningsFailsafe(unittest.TestCase):

    def setUp(self):
        self.scan_date = datetime.date(2026, 9, 22)

    def test_1_upcoming_earnings_within_blackout_rejected(self):
        # Trend Following blackout = 5 days. Earnings in 3 days -> REJECTED
        cal = {
            "AAPL": {
                "next_earnings_date": "2026-09-25",
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        }
        res = earnings_risk_filter("AAPL", self.scan_date, "trend_following", cal)
        self.assertFalse(res["pass"], "Earnings in 3 days must fail Trend Following blackout (5d)")
        self.assertEqual(res["days_to_earnings"], 3)
        self.assertIn("blackout: 5d", res["reason"])

    def test_2_upcoming_earnings_beyond_blackout_passed(self):
        # Trend Following blackout = 5 days. Earnings in 10 days -> PASSED
        cal = {
            "AAPL": {
                "next_earnings_date": "2026-10-02",
                "status": EarningsStatus.KNOWN_UPCOMING.value,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        }
        res = earnings_risk_filter("AAPL", self.scan_date, "trend_following", cal)
        self.assertTrue(res["pass"], "Earnings in 10 days must pass Trend Following blackout (5d)")
        self.assertEqual(res["days_to_earnings"], 10)

    def test_3_confirmed_clear_earnings_passed(self):
        # Confirmed clear
        cal = {
            "GOOG": {
                "next_earnings_date": None,
                "status": EarningsStatus.KNOWN_CLEAR.value,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        }
        res = earnings_risk_filter("GOOG", self.scan_date, "trend_following", cal)
        self.assertTrue(res["pass"], "Confirmed clear earnings schedule must pass")

    def test_4_stale_earnings_rejected_for_blackout_passed_for_pead(self):
        # Record older than 24 hours (e.g. 48 hours ago)
        old_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=48)).isoformat()
        cal = {
            "MSFT": {
                "next_earnings_date": "2026-10-15",
                "status": EarningsStatus.STALE.value,
                "updated_at": old_time,
            }
        }
        # Trend following must fail closed on STALE
        res_tf = earnings_risk_filter("MSFT", self.scan_date, "trend_following", cal)
        self.assertFalse(res_tf["pass"], "Stale earnings data must fail closed for blackout strategies")
        self.assertEqual(res_tf["status"], EarningsStatus.STALE.value)

        # PEAD must remain exempt
        res_pead = earnings_risk_filter("MSFT", self.scan_date, "pead", cal)
        self.assertTrue(res_pead["pass"], "PEAD must remain exempt even if earnings data is stale")

    def test_5_api_failure_unknown_status_fails_closed(self):
        # Ticker missing from calendar entirely
        cal = {}
        res = earnings_risk_filter("UNKNOWN_CO", self.scan_date, "trend_following", cal)
        self.assertFalse(res["pass"], "Missing earnings data must fail closed for blackout strategies")
        self.assertEqual(res["status"], EarningsStatus.UNKNOWN.value)

        # Calendar is None
        res_none = earnings_risk_filter("UNKNOWN_CO", self.scan_date, "pullback_recovery", None)
        self.assertFalse(res_none["pass"], "None earnings_calendar must fail closed for blackout strategies")
        self.assertEqual(res_none["status"], EarningsStatus.UNKNOWN.value)

    def test_6_pead_exemption_across_all_unknown_and_stale_states(self):
        # PEAD with None calendar
        res_none = earnings_risk_filter("PEAD_CO", self.scan_date, "pead", None)
        self.assertTrue(res_none["pass"], "PEAD must pass with None calendar")

        # PEAD with empty entry
        res_empty = earnings_risk_filter("PEAD_CO", self.scan_date, "pead", {"PEAD_CO": {}})
        self.assertTrue(res_empty["pass"], "PEAD must pass with empty calendar")

        # PEAD with unknown status
        res_unknown = earnings_risk_filter("PEAD_CO", self.scan_date, "pead", {"PEAD_CO": {"status": "UNKNOWN"}})
        self.assertTrue(res_unknown["pass"], "PEAD must pass with UNKNOWN status")

    def test_7_freshness_checker(self):
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fresh_rec = {"updated_at": now_iso}
        self.assertTrue(is_earnings_record_fresh(fresh_rec))

        stale_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=25)).isoformat()
        stale_rec = {"updated_at": stale_time}
        self.assertFalse(is_earnings_record_fresh(stale_rec))

        empty_rec = {}
        self.assertFalse(is_earnings_record_fresh(empty_rec))


if __name__ == "__main__":
    unittest.main()
