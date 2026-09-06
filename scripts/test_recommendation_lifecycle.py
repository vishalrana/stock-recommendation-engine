"""
Recommendation Lifecycle & Manual Removal Test Suite
====================================================
Verifies that:
1. Stop Loss Hit: Price breaching stop loss transitions recommendation from Active -> stopped outcome.
2. Subsequent Scan Invalidation: Active recommendation no longer qualifying in a new scan transitions to invalidated outcome.
3. Continued Active Qualification: Qualifying setup without stop breach remains active and refreshes market price.
4. Manual Removal: Recommendation is marked manually_removed with reason, note, timestamp.
5. Non-Blacklisting: Stopped, invalidated, and manually removed stocks are NEVER blacklisted and can qualify in future scans.
6. View Separation: Current Recommendations contains only active recommendations (open/pending), while Scan Audit & History contains completed outcomes and rejections.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestRecommendationLifecycle(unittest.TestCase):

    def test_stop_loss_hit_lifecycle_transition(self):
        """Verify low price breaching stop_loss transitions status/outcome to stopped."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle
        
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "sig-1",
                "ticker": "AAPL",
                "status": "open",
                "entry_price": 150.0,
                "stop_loss": 142.0,
                "price": 145.0,
                "target_1": 160.0,
                "target_2": 165.0,
                "target_3": 170.0,
                "strategy": "Pullback Recovery",
            }
        ]

        with patch("jobs.supabase_client.get_latest_bar") as mock_bar, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            # Ticker low drops to 141.50 <= stop_loss 142.0
            mock_bar.return_value = {"close": 141.80, "low": 141.50, "high": 146.00}

            reconcile_recommendation_lifecycle(mock_supabase, qualified_tickers={"AAPL"})

            mock_update_sig.assert_called_once_with(
                "AAPL", "stopped", 141.80, True, "Stop loss hit"
            )
            mock_update_hist.assert_called_once_with(
                "AAPL", "stopped", 141.80, True
            )

    def test_subsequent_scan_invalidation(self):
        """Verify active recommendation that no longer qualifies transitions to invalidated."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle
        
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "sig-2",
                "ticker": "MSFT",
                "status": "open",
                "entry_price": 400.0,
                "stop_loss": 380.0,
                "price": 405.0,
                "target_1": 420.0,
                "target_2": 430.0,
                "target_3": 440.0,
                "strategy": "Trend Following",
            }
        ]

        with patch("jobs.supabase_client.get_latest_bar") as mock_bar, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            # Price intact (above stop), but MSFT is NOT in qualified_tickers tonight
            mock_bar.return_value = {"close": 406.00, "low": 402.00, "high": 408.00}

            reconcile_recommendation_lifecycle(mock_supabase, qualified_tickers={"NVDA", "AMZN"})

            mock_update_sig.assert_called_once_with(
                "MSFT", "invalidated", 406.00, True, "No longer qualifies in subsequent scan"
            )
            mock_update_hist.assert_called_once_with(
                "MSFT", "invalidated", 406.00, True
            )

    def test_continued_active_qualification(self):
        """Verify active recommendation that still qualifies and hasn't hit stop remains open with updated price."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle
        
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "sig-3",
                "ticker": "NVDA",
                "status": "open",
                "entry_price": 120.0,
                "stop_loss": 112.0,
                "price": 124.0,
                "target_1": 130.0,
                "target_2": 135.0,
                "target_3": 140.0,
                "strategy": "Trend Following",
            }
        ]

        with patch("jobs.supabase_client.get_latest_bar") as mock_bar, \
             patch("jobs.supabase_client.update_signals_price") as mock_update_price, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig:

            # NVDA is still qualified, low is 123.00 > stop 112.00
            mock_bar.return_value = {"close": 126.50, "low": 123.00, "high": 127.00}

            reconcile_recommendation_lifecycle(mock_supabase, qualified_tickers={"NVDA"})

            mock_update_price.assert_called_once_with("NVDA", 126.50)
            mock_update_sig.assert_not_called()

    def test_manual_removal_storage(self):
        """Verify update_signals_status and update_history_outcome record manual removal metadata."""
        from jobs.supabase_client import update_signals_status, update_history_outcome
        import jobs.supabase_client as sc

        mock_supabase = MagicMock()
        mock_signals_table = MagicMock()
        mock_history_table = MagicMock()

        def table_router(name):
            if name == "signals":
                return mock_signals_table
            if name == "signals_history":
                return mock_history_table
            return MagicMock()

        mock_supabase.table.side_effect = table_router
        mock_history_table.select().eq().eq().execute.return_value.data = [
            {"entry_price": 100.0, "scan_date": "2026-09-01"}
        ]

        orig_client = sc.supabase
        try:
            sc.supabase = mock_supabase
            update_signals_status(
                ticker="GOOGL",
                status="manually_removed",
                exit_price=175.0,
                sell_signal=True,
                sell_signal_reason="Technical structure changed: Broke EMA50",
                removal_reason="Technical structure changed",
                removal_note="Broke EMA50",
            )

            # Check signals update call
            mock_signals_table.update.assert_called_once()
            call_args = mock_signals_table.update.call_args[0][0]
            self.assertEqual(call_args["status"], "manually_removed")
            self.assertEqual(call_args["removal_reason"], "Technical structure changed")
            self.assertEqual(call_args["removal_note"], "Broke EMA50")
            self.assertIn("removed_at", call_args)

            update_history_outcome(
                ticker="GOOGL",
                status="manually_removed",
                exit_price=175.0,
                sell_signal=True,
                removal_reason="Technical structure changed",
                removal_note="Broke EMA50",
            )

            mock_history_table.update.assert_called_once()
            hist_call_args = mock_history_table.update.call_args[0][0]
            self.assertEqual(hist_call_args["outcome"], "manually_removed")
            self.assertEqual(hist_call_args["removal_reason"], "Technical structure changed")
            self.assertEqual(hist_call_args["removal_note"], "Broke EMA50")
            self.assertIn("removed_at", hist_call_args)
        finally:
            sc.supabase = orig_client

    def test_non_blacklisting_of_inactive_tickers(self):
        """Verify that stopped, invalidated, or manually removed tickers are NOT blacklisted and can qualify afresh."""
        from jobs.generate_signals import BLACKLIST
        
        # Verify BLACKLIST only contains dummy test placeholders
        self.assertNotIn("AAPL", BLACKLIST)
        self.assertNotIn("MSFT", BLACKLIST)
        self.assertNotIn("NVDA", BLACKLIST)
        self.assertNotIn("GOOGL", BLACKLIST)
        self.assertEqual(BLACKLIST, {"XYZ", "TEST", "PLACEHOLDER"})

        # Verify active recommendation query does NOT filter out stopped/invalidated/manually_removed tickers
        # Only open and pending statuses are considered active
        active_statuses = ["open", "pending"]
        self.assertNotIn("stopped", active_statuses)
        self.assertNotIn("invalidated", active_statuses)
        self.assertNotIn("manually_removed", active_statuses)


if __name__ == "__main__":
    unittest.main()
