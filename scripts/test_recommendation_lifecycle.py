"""
Recommendation Lifecycle & Manual Removal Test Suite
====================================================
Verifies:
1. Test 1: Automatic Invalidation: Active recommendation no longer qualifying in a new scan
   transitions to invalidated outcome. Ticker remains eligible for future scans.
2. Test 2: Stop Loss vs. Invalidation Distinction: SL reached -> stopped; setup disqualified -> invalidated.
   Never interchanged.
3. Test 3: Manual Removal Exact ID Targeting: Removing recommendation instance X (ID 205) never modifies
   another historical instance for the same ticker (ID 101).
4. Test 4: No False Invalidation: Failed/incomplete scans or missing quote data do NOT invalidate
   active recommendations.
5. Continued Active Qualification: Qualifying setup without stop breach remains active with refreshed price.
6. Non-Blacklisting Policy: Stopped, invalidated, and manually removed stocks are NEVER blacklisted.
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

    def test_automatic_invalidation_and_future_eligibility(self):
        """Test 1: Active recommendation no longer qualifying in new scan transitions to invalidated.
        Also verify ticker remains eligible for future scans."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle, BLACKLIST
        
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "uuid-205",
                "ticker": "ABC",
                "status": "open",
                "entry_price": 100.0,
                "stop_loss": 92.0,
                "price": 103.0,
                "target_1": 115.0,
                "target_2": 125.0,
                "target_3": 135.0,
                "strategy": "Pullback Recovery",
                "scan_date": "2026-09-01",
            }
        ]

        with patch("jobs.supabase_client.get_latest_bar") as mock_bar, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            # Price at 103.0 (well above stop loss 92.0), but ABC no longer qualifies tonight
            mock_bar.return_value = {"close": 103.00, "low": 101.50, "high": 104.50}

            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"NVDA", "AAPL"},
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
                disqualification_reasons={"ABC": "ReachProb(T1) 22.0% < StrategyMin.T1 (40.0%)"},
            )

            # 1. Verify status transitioned to 'invalidated' with exact signal_id
            mock_update_sig.assert_called_once()
            sig_args, sig_kwargs = mock_update_sig.call_args
            self.assertEqual(sig_args[0], "ABC")
            self.assertEqual(sig_args[1], "invalidated")
            self.assertEqual(sig_args[2], 103.00)
            self.assertTrue(sig_args[3])
            self.assertIn("ReachProb", sig_args[4])
            self.assertEqual(sig_kwargs.get("signal_id"), "uuid-205")

            # 2. Verify history transitioned to 'invalidated' with exact signal_id and scan_date
            mock_update_hist.assert_called_once()
            hist_args, hist_kwargs = mock_update_hist.call_args
            self.assertEqual(hist_args[0], "ABC")
            self.assertEqual(hist_args[1], "invalidated")
            self.assertEqual(hist_args[2], 103.00)
            self.assertEqual(hist_kwargs.get("signal_id"), "uuid-205")
            self.assertEqual(hist_kwargs.get("scan_date"), "2026-09-01")

            # 3. Verify ABC is NOT blacklisted and remains eligible for future scans
            self.assertNotIn("ABC", BLACKLIST)

    def test_stop_loss_vs_invalidation_strict_distinction(self):
        """Test 2: Verify SL reached produces 'stopped', while setup disqualification produces 'invalidated'.
        The two are never interchanged."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle
        
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "uuid-sl",
                "ticker": "STOPPED_STOCK",
                "status": "open",
                "entry_price": 100.0,
                "stop_loss": 92.0,
                "price": 95.0,
                "strategy": "Pullback Recovery",
                "scan_date": "2026-09-01",
            },
            {
                "id": "uuid-inv",
                "ticker": "INVALID_STOCK",
                "status": "open",
                "entry_price": 100.0,
                "stop_loss": 92.0,
                "price": 103.0,
                "strategy": "Pullback Recovery",
                "scan_date": "2026-09-01",
            },
        ]

        def fake_get_latest_bar(ticker):
            if ticker == "STOPPED_STOCK":
                # Low breached stop 92.0 -> STOPPED
                return {"close": 91.50, "low": 91.00, "high": 95.00}
            if ticker == "INVALID_STOCK":
                # Low intact at 101.0 > stop 92.0, but disqualified in scan -> INVALIDATED
                return {"close": 103.00, "low": 101.00, "high": 104.00}
            return None

        with patch("jobs.supabase_client.get_latest_bar", side_effect=fake_get_latest_bar), \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"OTHER"},
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
            )

            calls = {call[0][0]: call[0][1] for call in mock_update_sig.call_args_list}
            self.assertEqual(calls.get("STOPPED_STOCK"), "stopped")
            self.assertNotEqual(calls.get("STOPPED_STOCK"), "invalidated")

            self.assertEqual(calls.get("INVALID_STOCK"), "invalidated")
            self.assertNotEqual(calls.get("INVALID_STOCK"), "stopped")

    def test_manual_removal_exact_id_targeting(self):
        """Test 3 (Mandatory): Create two historical recommendations for the same ticker:
        ABC / ID 101 (scan_date 2026-08-01)
        ABC / ID 205 (scan_date 2026-09-01)
        Make ID 205 active recommendation.
        Manually remove ID 205.
        Verify:
        ID 205 -> manually_removed
        ID 101 -> completely unchanged."""
        from jobs.supabase_client import update_signals_status, update_history_outcome
        import jobs.supabase_client as sc

        # Mock database rows for ABC
        history_rows = {
            101: {"id": 101, "ticker": "ABC", "scan_date": "2026-08-01", "outcome": "stopped", "entry_price": 80.0},
            205: {"id": 205, "ticker": "ABC", "scan_date": "2026-09-01", "outcome": "open", "entry_price": 100.0},
        }
        signals_rows = {
            "sig-101": {"id": "sig-101", "ticker": "ABC", "scan_date": "2026-08-01", "status": "stopped"},
            "sig-205": {"id": "sig-205", "ticker": "ABC", "scan_date": "2026-09-01", "status": "open"},
        }

        mock_supabase = MagicMock()

        # Mock signals table update
        def signals_update(data):
            mock_query = MagicMock()
            def eq_id(col, val):
                mock_exec = MagicMock()
                def execute():
                    if col == "id" and val in signals_rows:
                        signals_rows[val].update(data)
                mock_exec.execute = execute
                return mock_exec
            mock_query.eq.side_effect = eq_id
            return mock_query

        # Mock signals_history table select & update
        def history_table():
            mock_table = MagicMock()
            def select_fn(cols):
                mock_s = MagicMock()
                def eq_fn(col, val):
                    mock_s2 = MagicMock()
                    def second_eq(col2, val2):
                        mock_ex = MagicMock()
                        data = [r for r in history_rows.values() if r.get(col) == val and r.get(col2) == val2]
                        mock_ex.execute.return_value = MagicMock(data=data)
                        return mock_ex
                    def execute_single():
                        data = [r for r in history_rows.values() if r.get(col) == val]
                        return MagicMock(data=data)
                    mock_s2.eq.side_effect = second_eq
                    mock_s2.execute = execute_single
                    return mock_s2
                mock_s.eq.side_effect = eq_fn
                return mock_s

            def update_fn(data):
                mock_u = MagicMock()
                def eq_fn(col, val):
                    mock_u2 = MagicMock()
                    def second_eq(col2, val2):
                        mock_ex = MagicMock()
                        def execute():
                            for r in history_rows.values():
                                if r.get(col) == val and r.get(col2) == val2:
                                    r.update(data)
                        mock_ex.execute = execute
                        return mock_ex
                    def execute_single():
                        for r in history_rows.values():
                            if r.get(col) == val:
                                r.update(data)
                    mock_u2.eq.side_effect = second_eq
                    mock_u2.execute = execute_single
                    return mock_u2
                mock_u.eq.side_effect = eq_fn
                return mock_u

            mock_table.select.side_effect = select_fn
            mock_table.update.side_effect = update_fn
            return mock_table

        mock_signals_t = MagicMock()
        mock_signals_t.update.side_effect = signals_update
        mock_history_t = history_table()

        def table_router(name):
            if name == "signals":
                return mock_signals_t
            if name == "signals_history":
                return mock_history_t
            return MagicMock()

        mock_supabase.table.side_effect = table_router

        orig_client = sc.supabase
        try:
            sc.supabase = mock_supabase

            # Manually remove recommendation ID 205 (scan_date 2026-09-01)
            update_signals_status(
                ticker="ABC",
                status="manually_removed",
                exit_price=105.0,
                sell_signal=True,
                signal_id="sig-205",
                removal_reason="Technical structure changed",
                removal_note="Manual test removal",
            )
            update_history_outcome(
                ticker="ABC",
                status="manually_removed",
                exit_price=105.0,
                sell_signal=True,
                history_id=205,
                scan_date="2026-09-01",
                removal_reason="Technical structure changed",
                removal_note="Manual test removal",
            )

            # Verification:
            # 1. ID 205 must be updated to manually_removed
            self.assertEqual(signals_rows["sig-205"]["status"], "manually_removed")
            self.assertEqual(history_rows[205]["outcome"], "manually_removed")
            self.assertEqual(history_rows[205]["removal_reason"], "Technical structure changed")

            # 2. ID 101 must be COMPLETELY UNCHANGED
            self.assertEqual(signals_rows["sig-101"]["status"], "stopped")
            self.assertEqual(history_rows[101]["outcome"], "stopped")
            self.assertNotIn("removal_reason", history_rows[101])
            self.assertEqual(history_rows[101]["entry_price"], 80.0)

        finally:
            sc.supabase = orig_client

    def test_no_false_invalidation_on_incomplete_or_failed_scan(self):
        """Test 4: Verify existing active recommendations are NOT automatically invalidated
        merely because the scan produced no results, failed, was incomplete, or market data was missing."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle
        
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "uuid-safe",
                "ticker": "SAFE_STOCK",
                "status": "open",
                "entry_price": 50.0,
                "stop_loss": 45.0,
                "price": 52.0,
                "scan_date": "2026-09-01",
            }
        ]

        with patch("jobs.supabase_client.get_latest_bar") as mock_bar, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            mock_bar.return_value = {"close": 52.00, "low": 51.00, "high": 53.00}

            # Scenario A: scan_successful = False (e.g. unhandled error during scan)
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),
                scan_successful=False,
                scanned_count=500,
                min_required_scanned=50,
            )
            mock_update_sig.assert_not_called()
            mock_update_hist.assert_not_called()

            # Scenario B: scanned_count = 0 or < min_required_scanned (incomplete scan / network down)
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),
                scan_successful=True,
                scanned_count=10,  # only 10 scanned < 50 minimum required
                min_required_scanned=50,
            )
            mock_update_sig.assert_not_called()
            mock_update_hist.assert_not_called()

            # Scenario C: quote data unavailable for ticker
            mock_bar.return_value = None  # Yahoo quote failed
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
            )
            mock_update_sig.assert_not_called()
            mock_update_hist.assert_not_called()

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
                "scan_date": "2026-09-01",
            }
        ]

        with patch("jobs.supabase_client.get_latest_bar") as mock_bar, \
             patch("jobs.supabase_client.update_signals_price") as mock_update_price, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig:

            # NVDA is still qualified, low is 123.00 > stop 112.00
            mock_bar.return_value = {"close": 126.50, "low": 123.00, "high": 127.00}

            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"NVDA"},
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
            )

            mock_update_price.assert_called_once_with("NVDA", 126.50)
            mock_update_sig.assert_not_called()


if __name__ == "__main__":
    unittest.main()

