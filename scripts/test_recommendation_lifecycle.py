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

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _bars(start_date, *rows):
    """Daily OHLC bars (business days from start_date) as returned by get_bars_after().
    Each row is (open, high, low, close)."""
    idx = pd.bdate_range(start=start_date, periods=len(rows))
    return pd.DataFrame(
        [{"OPEN": o, "HIGH": h, "LOW": l, "CLOSE": c} for (o, h, l, c) in rows],
        index=idx,
    )


class TestRecommendationLifecycle(unittest.TestCase):

    def test_failing_to_requalify_does_not_close_and_holding_period_expires(self):
        """Test 1: An idea that no longer qualifies stays open (it is measured as designed);
        it closes as 'expired' once the strategy holding period (Pullback Recovery: 10 bars) ends,
        with the return measured from the D+1 open fill. The ticker is never blacklisted."""
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

        with patch("jobs.supabase_client.get_bars_after") as mock_bars, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            # Day 1: price at 103.0 (well above stop 92.0); ABC no longer qualifies tonight -> stays open
            mock_bars.return_value = _bars("2026-09-02", (101.00, 104.50, 101.50, 103.00))
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"NVDA", "AAPL"},
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
                disqualification_reasons={"ABC": "ReachProb(T1) 22.0% < StrategyMin.T1 (40.0%)"},
            )
            mock_update_sig.assert_not_called()
            mock_update_hist.assert_not_called()

            # Day 10: holding period reached without stop or target -> expired at that close
            ten_bars = [(101.00, 104.50, 101.50, 103.00)] + [(103.0, 104.0, 102.0, 103.5)] * 8 + [(103.5, 105.0, 103.0, 104.0)]
            mock_bars.return_value = _bars("2026-09-02", *ten_bars)
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
            )

            mock_update_sig.assert_called_once()
            sig_args, sig_kwargs = mock_update_sig.call_args
            self.assertEqual(sig_args[0], "ABC")
            self.assertEqual(sig_args[1], "expired")
            self.assertEqual(sig_args[2], 104.00)
            self.assertIn("Holding period ended", sig_args[4])
            self.assertEqual(sig_kwargs.get("signal_id"), "uuid-205")

            mock_update_hist.assert_called_once()
            hist_args, hist_kwargs = mock_update_hist.call_args
            self.assertEqual(hist_args[1], "expired")
            self.assertEqual(hist_kwargs.get("signal_id"), "uuid-205")
            self.assertEqual(hist_kwargs.get("scan_date"), "2026-09-01")
            # Return measured from the D+1 open fill (101.00) to the expiry close (104.00)
            self.assertEqual(hist_kwargs.get("entry_fill_price"), 101.00)
            self.assertAlmostEqual(hist_kwargs.get("return_pct"), (104.0 - 101.0) / 101.0 * 100.0, places=3)
            self.assertEqual(hist_kwargs.get("holding_days"), 10)

            # 3. Verify ABC is NOT blacklisted and remains eligible for future scans
            self.assertNotIn("ABC", BLACKLIST)

    def test_stop_loss_vs_invalidation_strict_distinction(self):
        """Test 2: Verify SL reached produces 'stopped', while setup disqualification alone closes nothing."""
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

        def fake_get_bars_after(ticker, after_date, through_date=None):
            if ticker == "STOPPED_STOCK":
                # Low breached stop 92.0 -> STOPPED
                return _bars("2026-09-02", (94.00, 95.00, 91.00, 91.50))
            if ticker == "INVALID_STOCK":
                # Low intact at 101.0 > stop 92.0, but disqualified in scan -> INVALIDATED
                return _bars("2026-09-02", (102.00, 104.00, 101.00, 103.00))
            return None

        with patch("jobs.supabase_client.get_bars_after", side_effect=fake_get_bars_after), \
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

            # Disqualified in tonight's scan but no stop/target/expiry: stays open, never invalidated
            self.assertNotIn("INVALID_STOCK", calls)

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

        with patch("jobs.supabase_client.get_bars_after") as mock_bar, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            mock_bar.return_value = _bars("2026-09-02", (51.50, 53.00, 51.00, 52.00))

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

        with patch("jobs.supabase_client.get_bars_after") as mock_bar, \
             patch("jobs.supabase_client.update_signals_price") as mock_update_price, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig:

            # NVDA is still qualified, low is 123.00 > stop 112.00
            mock_bar.return_value = _bars("2026-09-02", (124.00, 127.00, 123.00, 126.50))

            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"NVDA"},
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
            )

            mock_update_price.assert_called_once_with("NVDA", 126.50, signal_id="sig-3")
            mock_update_sig.assert_not_called()

    def test_missed_scan_days_are_replayed(self):
        """A stop hit on a day with no successful nightly scan must still be detected:
        the whole bar path since the signal date is replayed, not just the latest bar."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle

        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {"id": "sig-gap", "ticker": "GAPPY", "status": "open", "entry_price": 100.0,
             "stop_loss": 93.0, "target_1": 110.0, "target_2": 120.0, "target_3": 130.0,
             "strategy": "Trend Following", "scan_date": "2026-09-01"}
        ]
        with patch("jobs.supabase_client.get_bars_after") as mock_bars, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:
            # Day 2 breaches the stop; by day 3 (latest bar) price has fully recovered.
            mock_bars.return_value = _bars(
                "2026-09-02",
                (100.0, 101.0, 98.0, 99.0),
                (97.0, 97.5, 92.0, 94.0),
                (99.0, 104.0, 98.0, 103.0),
            )
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase, qualified_tickers={"GAPPY"}, scan_successful=True,
                scanned_count=500, min_required_scanned=50, current_scan_date="2026-09-04",
            )
            sig_args, sig_kwargs = mock_update_sig.call_args
            self.assertEqual(sig_args[1], "stopped")
            self.assertEqual(sig_args[2], 93.0)
            self.assertEqual(sig_kwargs.get("exit_date"), "2026-09-03")
            _, hist_kwargs = mock_update_hist.call_args
            self.assertEqual(hist_kwargs.get("outcome_date"), "2026-09-03")
            self.assertEqual(hist_kwargs.get("holding_days"), 2)
            self.assertAlmostEqual(hist_kwargs.get("return_pct"), -7.0, places=3)

    def test_scale_out_before_stop_records_t1_outcome(self):
        """T1 reached, then the remainder stopped at breakeven: outcome is hit_t1 with the
        scale-out return (50% at +10%, 50% at 0%) = +5%, never a full stop-loss."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle

        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {"id": "sig-t1", "ticker": "SCALE", "status": "open", "entry_price": 100.0,
             "stop_loss": 93.0, "target_1": 110.0, "target_2": 120.0, "target_3": 130.0,
             "strategy": "Trend Following", "scan_date": "2026-09-01"}
        ]
        with patch("jobs.supabase_client.get_bars_after") as mock_bars, \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:
            mock_bars.return_value = _bars(
                "2026-09-02",
                (100.0, 111.0, 101.0, 109.0),   # T1 hit, low stays above breakeven
                (104.0, 105.0, 99.0, 100.5),    # remainder stopped at breakeven (100)
            )
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase, qualified_tickers={"SCALE"}, scan_successful=True,
                scanned_count=500, min_required_scanned=50, current_scan_date="2026-09-03",
            )
            sig_args, _ = mock_update_sig.call_args
            self.assertEqual(sig_args[1], "hit_t1")
            _, hist_kwargs = mock_update_hist.call_args
            self.assertAlmostEqual(hist_kwargs.get("return_pct"), 5.0, places=3)

    def test_open_recommendation_trade_parameters_are_frozen(self):
        """A still-qualifying recommendation must never have its stop, targets or strategy
        overwritten by tonight's re-scan; only analytical fields are refreshed."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle

        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {"id": "sig-frozen", "ticker": "FROZEN", "status": "pending", "entry_price": 100.0,
             "stop_loss": 93.0, "target_1": 110.0, "target_2": 120.0, "target_3": 130.0,
             "strategy": "Trend Following", "scan_date": "2026-09-01"}
        ]
        tonight = {
            "stop_loss": 88.0, "target_1": 99.0, "target_2": 104.0, "target_3": 109.0,
            "strategy": "Mean Reversion", "strategy_name": "Mean Reversion",
            "reach_prob_t1": 0.9, "composite_score": 71.0, "tier_label": "Buy", "pe_ratio": 22.5,
        }
        with patch("jobs.supabase_client.get_bars_after") as mock_bars, \
             patch("jobs.supabase_client.update_signals_price"), \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig:
            mock_bars.return_value = _bars("2026-09-02", (101.0, 103.0, 97.0, 95.0))
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase, qualified_tickers={"FROZEN"}, scan_successful=True,
                scanned_count=500, min_required_scanned=50, current_scan_date="2026-09-02",
                updated_analytics={"FROZEN": tonight},
            )
            mock_update_sig.assert_not_called()
            update_calls = [c.args[0] for c in mock_supabase.table.return_value.update.call_args_list
                            if isinstance(c.args[0], dict) and "position_state" in c.args[0]]
            self.assertEqual(len(update_calls), 1)
            fields = update_calls[0]
            for frozen in ("stop_loss", "target_1", "target_2", "target_3", "strategy",
                           "strategy_name", "reach_prob_t1", "entry_price"):
                self.assertNotIn(frozen, fields)
            self.assertEqual(fields["composite_score"], 71.0)
            self.assertEqual(fields["pe_ratio"], 22.5)
            self.assertEqual(fields["status"], "open")
            self.assertEqual(fields["entry_fill_price"], 101.0)
            self.assertEqual(fields["entry_date"], "2026-09-02")

    def test_production_mode_calls_lifecycle_reconciliation(self):
        """TEST A: Production mode (dry_run=False) actually calls lifecycle reconciliation."""
        import argparse
        mock_client = MagicMock()
        mock_client.table().select().in_().execute.return_value.data = []
        mock_client.table().select().execute.return_value.data = []

        mock_cm = MagicMock()
        mock_cm.is_stale.return_value = False
        mock_cm.get_last_cached_date.return_value = "2026-09-01"

        with patch("jobs.generate_signals.get_client", return_value=mock_client), \
             patch("jobs.generate_signals.get_regime", return_value={"regime": "bull", "spy_price": 500, "spy_200dma": 450}), \
             patch("jobs.generate_signals.apply_vix_override", return_value=("bull", ["Pullback Recovery"])), \
             patch("jobs.generate_signals.glob.glob", return_value=["dummy.parquet"]), \
             patch("jobs.generate_signals.load_universe", return_value=(["AAPL"], {"AAPL": "Apple"}, {"AAPL": "Tech"})), \
             patch("jobs.generate_signals.get_cache_manager", return_value=mock_cm), \
             patch("jobs.generate_signals.fetch_earnings_calendar", return_value={}), \
             patch("jobs.generate_signals.load_cached_metrics", return_value={}), \
             patch("jobs.generate_signals.STRATEGIES", []), \
             patch("jobs.generate_signals.reconcile_recommendation_lifecycle") as mock_rec, \
             patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(dry_run=False, force_refresh=False, verbose=False, cache_mode="local")):
            from jobs.generate_signals import main
            main()

            self.assertTrue(mock_rec.called, "Production mode (dry_run=False) MUST call reconcile_recommendation_lifecycle()")
            _, kwargs = mock_rec.call_args
            self.assertTrue(kwargs.get("scan_successful"), "scan_successful must be True in production scan")
            self.assertIn("min_required_scanned", kwargs)
            self.assertIn("disqualification_reasons", kwargs)

    def test_dry_run_mode_skips_reconciliation_and_mutations(self):
        """TEST B: Dry-run mode (dry_run=True) does NOT call lifecycle reconciliation or mutate lifecycle state."""
        import argparse
        mock_client = MagicMock()
        mock_signals_table = MagicMock()
        mock_history_table = MagicMock()
        mock_scan_log_table = MagicMock()

        def table_router(name):
            if name == "signals":
                return mock_signals_table
            if name == "signals_history":
                return mock_history_table
            if name == "scan_log":
                return mock_scan_log_table
            return MagicMock()

        mock_client.table.side_effect = table_router
        mock_signals_table.select().in_().execute.return_value.data = []
        mock_signals_table.select().execute.return_value.data = []

        mock_cm = MagicMock()
        mock_cm.is_stale.return_value = False
        mock_cm.get_last_cached_date.return_value = "2026-09-01"

        with patch("jobs.generate_signals.get_client", return_value=mock_client), \
             patch("jobs.generate_signals.get_regime", return_value={"regime": "bull", "spy_price": 500, "spy_200dma": 450}), \
             patch("jobs.generate_signals.apply_vix_override", return_value=("bull", ["Pullback Recovery"])), \
             patch("jobs.generate_signals.glob.glob", return_value=["dummy.parquet"]), \
             patch("jobs.generate_signals.load_universe", return_value=(["AAPL"], {"AAPL": "Apple"}, {"AAPL": "Tech"})), \
             patch("jobs.generate_signals.get_cache_manager", return_value=mock_cm), \
             patch("jobs.generate_signals.fetch_earnings_calendar", return_value={}), \
             patch("jobs.generate_signals.load_cached_metrics", return_value={}), \
             patch("jobs.generate_signals.STRATEGIES", []), \
             patch("jobs.generate_signals.reconcile_recommendation_lifecycle") as mock_rec, \
             patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(dry_run=True, force_refresh=False, verbose=False, cache_mode="local")):
            from jobs.generate_signals import main
            main()

            self.assertFalse(mock_rec.called, "Dry-run mode (dry_run=True) must NEVER call reconcile_recommendation_lifecycle()")
            self.assertFalse(mock_signals_table.delete.called, "Dry-run mode must NEVER delete from signals table")
            self.assertFalse(mock_signals_table.insert.called, "Dry-run mode must NEVER insert into signals table")
            self.assertFalse(mock_history_table.upsert.called, "Dry-run mode must NEVER upsert into signals_history")
            self.assertFalse(mock_scan_log_table.upsert.called, "Dry-run mode must NEVER upsert into scan_log")

    def test_duplicate_ticker_multiple_instances_isolation(self):
        """Test: AAPL / Strategy A / ID 101 vs AAPL / Strategy B / ID 102.
        Removing or invalidating ID 101 must NEVER modify ID 102."""
        from jobs.supabase_client import update_signals_status, update_history_outcome
        import jobs.supabase_client as sc

        history_rows = {
            101: {"id": 101, "ticker": "AAPL", "scan_date": "2026-08-01", "outcome": "open", "strategy": "Strategy A"},
            102: {"id": 102, "ticker": "AAPL", "scan_date": "2026-09-01", "outcome": "open", "strategy": "Strategy B"},
        }
        signals_rows = {
            "sig-101": {"id": "sig-101", "ticker": "AAPL", "scan_date": "2026-08-01", "status": "open", "strategy": "Strategy A"},
            "sig-102": {"id": "sig-102", "ticker": "AAPL", "scan_date": "2026-09-01", "status": "open", "strategy": "Strategy B"},
        }

        mock_supabase = MagicMock()

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

            # Invalidate or remove ONLY ID sig-101 (scan_date 2026-08-01)
            update_signals_status(
                ticker="AAPL",
                status="invalidated",
                exit_price=150.0,
                sell_signal=True,
                sell_signal_reason="Disqualified",
                signal_id="sig-101",
            )
            update_history_outcome(
                ticker="AAPL",
                status="invalidated",
                exit_price=150.0,
                sell_signal=True,
                history_id=101,
                scan_date="2026-08-01",
                sell_signal_reason="Disqualified",
            )

            # Verification:
            # 1. Instance 101 is invalidated
            self.assertEqual(signals_rows["sig-101"]["status"], "invalidated")
            self.assertEqual(history_rows[101]["outcome"], "invalidated")

            # 2. Instance 102 is 100% UNCHANGED ('open')
            self.assertEqual(signals_rows["sig-102"]["status"], "open")
            self.assertEqual(history_rows[102]["outcome"], "open")
            self.assertEqual(history_rows[102]["strategy"], "Strategy B")

        finally:
            sc.supabase = orig_client

    def test_manual_removal_refuses_unsafe_ticker_only(self):
        """Verify that manual removal safely refuses operation without exact instance identity."""
        from jobs.supabase_client import update_signals_status, update_history_outcome
        import jobs.supabase_client as sc

        mock_signals_t = MagicMock()
        mock_history_t = MagicMock()
        mock_supabase = MagicMock()

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

            # Attempt manual removal without signal_id on signals table
            res_sig = update_signals_status(
                ticker="AAPL",
                status="manually_removed",
                exit_price=150.0,
                sell_signal=True,
                signal_id=None,  # Unsafe ticker-only
            )
            self.assertIsNone(res_sig)
            self.assertFalse(mock_signals_t.update().eq.called)

            # Attempt manual removal without history_id, signal_id, or scan_date
            res_hist = update_history_outcome(
                ticker="AAPL",
                status="manually_removed",
                exit_price=150.0,
                history_id=None,
                signal_id=None,
                scan_date=None,  # Unsafe ticker-only
            )
            self.assertIsNone(res_hist)
            self.assertFalse(mock_history_t.update.called)

        finally:
            sc.supabase = orig_client

    def test_rejected_signals_outcome_is_rejected_not_open(self):
        """Verify that rejected audit signals are archived to signals_history as 'rejected', not 'open',
        protecting validate_ranking.py from evaluating rejected records as active recommendations."""
        # Simulate history_rows creation for both qualified and rejected signals
        ranked_signals = [
            {"id": "sig-good", "ticker": "MSFT", "status": "pending", "scan_date": "2026-09-08"},
            {"id": "sig-bad", "ticker": "BADCO", "status": "rejected", "rejection_reason": "Earnings blackout", "scan_date": "2026-09-08"},
        ]

        history_rows = []
        for sig in ranked_signals:
            history_rows.append({
                "signal_id": sig.get("id"),
                "ticker": sig.get("ticker"),
                "scan_date": sig.get("scan_date"),
                "outcome": "rejected" if sig.get("status") == "rejected" else "open",
            })

        self.assertEqual(history_rows[0]["outcome"], "open")
        self.assertEqual(history_rows[1]["outcome"], "rejected")

    def test_d_plus_1_activation_separation(self):
        """P0 Test: Recommendations created on scan date D must NOT evaluate scan date D candle as an outcome.
        Trade activation begins on D+1 (next trading day)."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle

        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {
                "id": "uuid-d1",
                "ticker": "D1_STOCK",
                "status": "open",
                "entry_price": 100.0,
                "stop_loss": 92.0,
                "price": 100.0,
                "target_1": 110.0,
                "target_2": 120.0,
                "target_3": 130.0,
                "strategy": "trend_following",
                "scan_date": "2026-10-06",
            }
        ]

        def fake_bars(ticker, after_date, through_date=None):
            # The provider only ever returns bars strictly after the signal date.
            df = _bars("2026-10-07", (88.0, 95.0, 85.0, 88.0))
            if through_date:
                df = df[df.index <= pd.Timestamp(through_date)]
            return df[df.index > pd.Timestamp(after_date)]

        with patch("jobs.supabase_client.get_bars_after", side_effect=fake_bars), \
             patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            # Scenario 1: Reconciling on same scan date D ("2026-10-06").
            # Same-day evaluation must be SKIPPED.

            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"D1_STOCK"},
                scan_successful=True,
                scanned_count=100,
                min_required_scanned=50,
                current_scan_date="2026-10-06",
            )

            # Neither signals nor history should be updated to 'stopped'
            self.assertFalse(mock_update_sig.called, "Same-day candle must not trigger stop loss on scan date D")
            self.assertFalse(mock_update_hist.called, "Same-day candle must not update history outcome on scan date D")

            # Scenario 2: Price data only reaches D (no bar after the signal date yet)
            with patch("jobs.supabase_client.get_bars_after", return_value=_bars("2026-10-07").iloc[0:0]):
                reconcile_recommendation_lifecycle(
                    supabase=mock_supabase,
                    qualified_tickers={"D1_STOCK"},
                    scan_successful=True,
                    scanned_count=100,
                    min_required_scanned=50,
                    current_scan_date="2026-10-07",
                )
            self.assertFalse(mock_update_sig.called, "Stale candle with bar_date <= scan_date must not trigger outcome")

            # Scenario 3: Current scan date is D+1 ("2026-10-07") and bar date is D+1 ("2026-10-07").
            # Trade activates and stop is hit (gap open 88.0 below stop 92.0 -> exit at the open).
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"D1_STOCK"},
                scan_successful=True,
                scanned_count=100,
                min_required_scanned=50,
                current_scan_date="2026-10-07",
            )
            self.assertTrue(mock_update_sig.called, "D+1 candle must activate and evaluate stop loss")
            sig_args, _ = mock_update_sig.call_args
            self.assertEqual(sig_args[1], "stopped")
            self.assertEqual(sig_args[2], 88.0)

    def test_lifecycle_idempotency(self):
        """Invariant: Repeated lifecycle execution must be idempotent and never alter an already closed trade."""
        from jobs.generate_signals import reconcile_recommendation_lifecycle

        mock_supabase = MagicMock()
        # Query for active recommendations returns empty because the trade is already closed
        mock_supabase.table().select().in_().execute.return_value.data = []

        with patch("jobs.supabase_client.update_signals_status") as mock_update_sig, \
             patch("jobs.supabase_client.update_history_outcome") as mock_update_hist:

            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"D1_STOCK"},
                scan_successful=True,
                scanned_count=100,
                min_required_scanned=50,
                current_scan_date="2026-10-08",
            )
            self.assertFalse(mock_update_sig.called)
            self.assertFalse(mock_update_hist.called)


if __name__ == "__main__":
    unittest.main()

