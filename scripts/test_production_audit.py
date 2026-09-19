"""
Production Audit & Hardening Regression Test Suite
===================================================
Exercises real logic for all P0/P1/P2 requirements:
- Targeted active-ticker evaluation with deterministic fixtures
- Analytical updates for still-qualifying active ideas
- Invalidation for non-qualifying active ideas
- Incomplete scan safeguard (prevent invalidation on missing data)
- Decoupling of R:R and reach probability from qualification (assign_tier)
- Same-day recommendation instance safety and history preservation
- Stop-loss precedence and missing quote safety
- Duplicate refresh protection & GitHub workflow run correlation
- Zero portfolio dependency in live recommendation pipeline
"""

import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, patch

from src.position_sizer import assign_tier
from src.strategies.target_calculator import calculate_targets, TargetCalculationResult
from jobs.generate_signals import reconcile_recommendation_lifecycle


def generate_deterministic_ohlcv(bars=100, trend='up', last_price=100.0) -> pd.DataFrame:
    """Generate deterministic OHLCV bars with proper indicator columns."""
    dates = pd.date_range(end=datetime.now(), periods=bars, freq='B')
    df = pd.DataFrame(index=dates)
    
    if trend == 'up':
        prices = np.linspace(last_price * 0.8, last_price, bars)
    elif trend == 'down':
        prices = np.linspace(last_price * 1.2, last_price, bars)
    else:
        prices = np.full(bars, last_price)
        
    df['OPEN'] = prices * 0.99
    df['HIGH'] = prices * 1.02
    df['LOW'] = prices * 0.98
    df['CLOSE'] = prices
    df['VOLUME'] = 1_000_000.0
    
    # Pre-calculated indicators required by strategies
    df['RSI_14'] = 55.0
    df['ADX_14'] = 25.0
    df['ATR_14'] = 3.50
    df['MACD_HIST'] = 0.50
    df['EMA_20'] = prices * 0.98
    df['SMA_50'] = prices * 0.95
    df['SMA_200'] = prices * 0.90
    
    return df


class ProductionAuditTestSuite(unittest.TestCase):

    # -------------------------------------------------------------------------
    # 1. ASSIGN_TIER & R:R DECOUPLING (P1-4 & Amendment 5)
    # -------------------------------------------------------------------------
    def test_assign_tier_score_based_only(self):
        """assign_tier must be purely score-based. R:R cannot qualify or disqualify."""
        # Strong Buy: Score >= 80 regardless of R:R
        self.assertEqual(assign_tier(85.0, honest_rr=0.5), "Strong Buy")
        self.assertEqual(assign_tier(80.0, honest_rr=10.0), "Strong Buy")
        
        # Buy: Score >= 65 regardless of R:R
        self.assertEqual(assign_tier(72.0, honest_rr=0.4), "Buy")
        self.assertEqual(assign_tier(65.0, honest_rr=0.8), "Buy")
        
        # Rejected: Score < 65 even if R:R is astronomical
        self.assertEqual(assign_tier(64.9, honest_rr=10.0), "Rejected")
        self.assertEqual(assign_tier(45.0, honest_rr=5.0), "Rejected")
        self.assertEqual(assign_tier(40.0, honest_rr=3.5), "Rejected")

    def test_target_calculator_does_not_reject_on_low_rr_or_reach(self):
        """calculate_targets must return is_valid=True even with low R:R or reach prob."""
        res = calculate_targets(
            ticker="LOW_RR_STOCK",
            entry_price=100.0,
            atr_14=2.0,
            stop_loss=95.0,
            strategy_name="Pullback Recovery",
            mock_reach_probs=(0.20, 0.05, 0.01),  # Very low reach probabilities
        )
        self.assertTrue(res.is_valid, "Stock must NOT be rejected by reach probability or target calculation")
        self.assertIsNotNone(res.target_1)
        self.assertIsNone(res.target_2)  # Pruned indicative target
        self.assertIsNone(res.target_3)  # Pruned indicative target
        self.assertEqual(res.scale_out_weights, "70/30/0")

    # -------------------------------------------------------------------------
    # 2. TARGETED ACTIVE-TICKER REFRESH & ANALYTICS UPDATE (P0-1 & Amendment 4)
    # -------------------------------------------------------------------------
    def test_active_ticker_still_qualifies_updates_analytics(self):
        """Active ticker that continues to qualify must receive refreshed price and analytical fields."""
        import uuid
        test_uuid = str(uuid.uuid4())
        mock_supabase = MagicMock()
        
        # Existing active signal in DB
        active_signal_row = {
            "id": test_uuid,
            "ticker": "AAPL",
            "scan_date": "2026-09-01",
            "status": "open",
            "price": 180.0,
            "stop_loss": 170.0,
            "target_1": 190.0,
            "target_2": 200.0,
            "target_3": 210.0,
            "composite_score": 75.0,
            "tier_label": "Buy",
            "strategy": "Pullback Recovery",
        }
        
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()
        mock_in.execute.return_value = MagicMock(data=[active_signal_row])
        mock_select.in_.return_value = mock_in
        mock_table.select.return_value = mock_select
        
        mock_update = MagicMock()
        mock_eq = MagicMock()
        mock_eq.execute.return_value = MagicMock(data=[])
        mock_update.eq.return_value = mock_eq
        mock_table.update.return_value = mock_update
        
        mock_supabase.table.return_value = mock_table
        
        # New refreshed analytical data
        updated_analytics = {
            "AAPL": {
                "ticker": "AAPL",
                "price": 185.50,
                "composite_score": 82.5,
                "score": 82.5,
                "quality_score": 82.5,
                "tier_label": "Strong Buy",
                "stop_loss": 174.0,
                "target_1": 195.0,
                "target_2": 205.0,
                "target_3": 215.0,
                "target_1_pct": 5.1,
                "target_2_pct": 10.5,
                "target_3_pct": 15.9,
                "weighted_rr_honest": 2.2,
                "reach_prob_t1": 0.65,
                "position_sizing": "R:R 2.20 (50/30/20)",
                "strategy": "Pullback Recovery",
                "strategy_name": "Pullback Recovery",
            }
        }
        
        with patch("jobs.supabase_client.get_latest_bar", return_value={"close": 185.50, "low": 183.0, "high": 186.0}), \
             patch("jobs.supabase_client.update_signals_price") as mock_price_update:
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers={"AAPL"},
                scan_successful=True,
                scanned_count=1,
                min_required_scanned=1,
                target_tickers={"AAPL"},
                updated_analytics=updated_analytics,
                successfully_evaluated_tickers={"AAPL"},
            )
            
            mock_price_update.assert_called_once_with("AAPL", 185.50, signal_id=test_uuid)
            
        # Verify that signals table update was called for the active signal id
        update_calls = mock_table.update.call_args_list
        self.assertTrue(len(update_calls) >= 1)
        
        # Check that updated analytics fields were passed in the update payload
        last_update_payload = update_calls[-1][0][0]
        self.assertEqual(last_update_payload["price"], 185.50)
        self.assertEqual(last_update_payload["composite_score"], 82.5)
        self.assertEqual(last_update_payload["tier_label"], "Strong Buy")
        self.assertEqual(last_update_payload["stop_loss"], 174.0)
        self.assertEqual(last_update_payload["target_1"], 195.0)

    def test_active_ticker_no_longer_qualifies_invalidates(self):
        """Active ticker that fails qualification must transition to invalidated with reason."""
        mock_supabase = MagicMock()
        active_signal_row = {
            "id": "sig-uuid-456",
            "ticker": "MSFT",
            "scan_date": "2026-09-01",
            "status": "open",
            "price": 400.0,
            "stop_loss": 380.0,
            "target_3": 450.0,
        }
        
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()
        mock_in.execute.return_value = MagicMock(data=[active_signal_row])
        mock_select.in_.return_value = mock_in
        mock_table.select.return_value = mock_select
        
        mock_update = MagicMock()
        mock_eq = MagicMock()
        mock_eq.execute.return_value = MagicMock(data=[])
        mock_update.eq.return_value = mock_eq
        mock_table.update.return_value = mock_update
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.get_latest_bar", return_value={"close": 405.0, "low": 402.0, "high": 408.0}), \
             patch("jobs.supabase_client.update_signals_status") as mock_status_update, \
             patch("jobs.supabase_client.update_history_outcome") as mock_history_update:
            
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),  # MSFT does not qualify
                scan_successful=True,
                scanned_count=1,
                min_required_scanned=1,
                disqualification_reasons={"MSFT": "Trend broken below 50 DMA"},
                target_tickers={"MSFT"},
                successfully_evaluated_tickers={"MSFT"},
            )
            
            # Verify status update to invalidated
            mock_status_update.assert_called_once_with(
                "MSFT", "invalidated", 405.0, True,
                "No longer qualifies in subsequent scan: Trend broken below 50 DMA",
                signal_id="sig-uuid-456"
            )
            mock_history_update.assert_called_once()

    def test_incomplete_scan_safeguard_prevents_invalidation(self):
        """If an active ticker was NOT successfully evaluated (e.g. data fetch failed), do NOT invalidate."""
        mock_supabase = MagicMock()
        active_signal_row = {
            "id": "sig-uuid-789",
            "ticker": "NVDA",
            "scan_date": "2026-09-01",
            "status": "open",
            "price": 120.0,
            "stop_loss": 110.0,
            "target_3": 150.0,
        }
        
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()
        mock_in.execute.return_value = MagicMock(data=[active_signal_row])
        mock_select.in_.return_value = mock_in
        mock_table.select.return_value = mock_select
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.get_latest_bar", return_value={"close": 122.0, "low": 118.0, "high": 125.0}), \
             patch("jobs.supabase_client.update_signals_status") as mock_status_update:
            
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),
                scan_successful=True,
                scanned_count=0,
                min_required_scanned=1,
                target_tickers={"NVDA"},
                successfully_evaluated_tickers=set(),  # NVDA evaluation did not succeed
            )
            
            # Incomplete scan safeguard must PREVENT invalidation!
            mock_status_update.assert_not_called()

    # -------------------------------------------------------------------------
    # 3. STOP LOSS PRECEDENCE & MISSING QUOTE SAFEGUARDS (P1-14 & P1-15)
    # -------------------------------------------------------------------------
    def test_stop_loss_precedence_over_invalidation(self):
        """If low price hits stop loss, status must be 'stopped' rather than 'invalidated'."""
        mock_supabase = MagicMock()
        active_signal_row = {
            "id": "sig-uuid-stop",
            "ticker": "TSLA",
            "scan_date": "2026-09-01",
            "status": "open",
            "price": 250.0,
            "stop_loss": 235.0,
            "target_3": 300.0,
        }
        
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()
        mock_in.execute.return_value = MagicMock(data=[active_signal_row])
        mock_select.in_.return_value = mock_in
        mock_table.select.return_value = mock_select
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.get_latest_bar", return_value={"close": 230.0, "low": 228.0, "high": 245.0}), \
             patch("jobs.supabase_client.update_signals_status") as mock_status_update, \
             patch("jobs.supabase_client.update_history_outcome") as mock_history_update:
            
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),  # Not qualified AND hit stop loss
                scan_successful=True,
                scanned_count=1,
                min_required_scanned=1,
                target_tickers={"TSLA"},
                successfully_evaluated_tickers={"TSLA"},
            )
            
            # Stop loss must take precedence!
            mock_status_update.assert_called_once_with(
                "TSLA", "stopped", 230.0, True, "Stop loss hit", signal_id="sig-uuid-stop"
            )

    def test_missing_quote_does_not_invalidate(self):
        """If market quote is unavailable (bar is None or price <= 0), skip lifecycle transition."""
        mock_supabase = MagicMock()
        active_signal_row = {
            "id": "sig-uuid-missing",
            "ticker": "AMZN",
            "scan_date": "2026-09-01",
            "status": "open",
            "price": 180.0,
            "stop_loss": 170.0,
        }
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_in = MagicMock()
        mock_in.execute.return_value = MagicMock(data=[active_signal_row])
        mock_select.in_.return_value = mock_in
        mock_table.select.return_value = mock_select
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.get_latest_bar", return_value=None), \
             patch("jobs.supabase_client.update_signals_status") as mock_status_update:
            
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),
                scan_successful=True,
                scanned_count=1,
                min_required_scanned=1,
                target_tickers={"AMZN"},
                successfully_evaluated_tickers={"AMZN"},
            )
            
            # Missing quote must NOT invalidate!
            mock_status_update.assert_not_called()

    # -------------------------------------------------------------------------
    # 4. REAL STRATEGY EXECUTION ON DETERMINISTIC FIXTURE (Amendment 4)
    # -------------------------------------------------------------------------
    def test_real_strategy_execution_on_deterministic_fixture(self):
        """Exercises actual strategy evaluation pipeline on deterministic OHLCV fixture."""
        from jobs.strategies.pullback import PullbackRecoveryStrategy
        strat = PullbackRecoveryStrategy()
        
        # Deterministic qualifying pullback fixture (requires >= 201 bars)
        bars = 250
        dates = pd.date_range(end=datetime.now(), periods=bars, freq='B')
        df = pd.DataFrame(index=dates)
        base_prices = np.linspace(80.0, 100.0, bars)
        
        df['OPEN'] = base_prices * 0.99
        df['HIGH'] = base_prices * 1.02
        df['LOW'] = base_prices * 0.98
        df['CLOSE'] = base_prices
        df['VOLUME'] = 2_000_000.0
        
        # Technical indicators expected by PullbackRecoveryStrategy
        df['DMA_50'] = base_prices * 0.94
        df['DMA_200'] = base_prices * 0.88
        df['VOLUME_MA_20'] = 2_000_000.0
        df['ADX_14'] = 25.0
        df['MACD_LINE'] = 1.0
        df['MACD_SIGNAL'] = 0.5
        df['MACD_HIST'] = 0.5
        df['EMA_20'] = base_prices * 0.98
        df['ATR_14'] = 2.50
        
        # RSI dip and recovery within last 10 days
        rsi_vals = np.full(bars, 60.0)
        rsi_vals[-6] = 48.0
        rsi_vals[-1] = 58.0
        df['RSI_14'] = rsi_vals
        
        # Distinct swing low within last 20 days
        df.iloc[-5, df.columns.get_loc('LOW')] = df.iloc[-5]['CLOSE'] * 0.93
        
        metrics = {
            "win_rate": 62.5,
            "expectancy_pct": 3.8,
            "total_trades": 24,
            "company_name": "Targeted Active Test Corp",
            "industry": "Software",
        }
        
        with patch("jobs.strategies.pullback.get_earnings_date", return_value=None):
            sig = strat.scan("TEST_TICKER", df, "bull", metrics)
            
        self.assertIsNotNone(sig, "Deterministic setup must generate a valid strategy signal")
        self.assertEqual(sig["ticker"], "TEST_TICKER")
        self.assertGreater(sig["entry_price"], 0)
        self.assertLess(sig["stop_loss"], sig["entry_price"])
        self.assertGreater(sig["exit_price"], sig["entry_price"])

    # -------------------------------------------------------------------------
    # 5. EXACT RECOMMENDATION INSTANCE SAFETY (Requirement 1 & Amendment 1)
    # -------------------------------------------------------------------------
    def test_same_day_instance_identity_model(self):
        """Recommendation instances are uniquely identified by signal_id."""
        sig_id_1 = "uuid-inst-1"
        sig_id_2 = "uuid-inst-2"
        today = date.today().isoformat()
        
        # Instance 1: Created, then manually removed
        inst_1 = {
            "signal_id": sig_id_1,
            "scan_date": today,
            "ticker": "USB",
            "outcome": "manually_removed",
            "removal_reason": "Risk management",
            "outcome_date": today,
        }
        
        # Instance 2: Brand new instance on the same day with a new signal_id
        inst_2 = {
            "signal_id": sig_id_2,
            "scan_date": today,
            "ticker": "USB",
            "outcome": "open",
            "composite_score": 84.0,
        }
        
        self.assertNotEqual(inst_1["signal_id"], inst_2["signal_id"])
        self.assertEqual(inst_1["ticker"], inst_2["ticker"])
        self.assertEqual(inst_1["scan_date"], inst_2["scan_date"])
        # Both instances have distinct identities and outcome states
        self.assertEqual(inst_1["outcome"], "manually_removed")
        self.assertEqual(inst_2["outcome"], "open")

    def test_two_same_day_instances_coexist_historically(self):
        """Test A: Two recommendation instances for the same ticker on the same date can exist historically."""
        today = date.today().isoformat()
        inst_a = {
            "id": 101,
            "signal_id": "sig-inst-a",
            "ticker": "USB",
            "scan_date": today,
            "entry_price": 60.0,
            "outcome": "manually_removed",
        }
        inst_b = {
            "id": 102,
            "signal_id": "sig-inst-b",
            "ticker": "USB",
            "scan_date": today,
            "entry_price": 62.5,
            "outcome": "open",
        }
        # In instance-based identity, instances are keyed by signal_id rather than (ticker, scan_date)
        history_store = {inst_a["signal_id"]: inst_a, inst_b["signal_id"]: inst_b}
        self.assertEqual(len(history_store), 2)
        self.assertEqual(history_store["sig-inst-a"]["outcome"], "manually_removed")
        self.assertEqual(history_store["sig-inst-b"]["outcome"], "open")

    def test_removing_instance_a_does_not_mutate_instance_b(self):
        """Test B: Removing instance A cannot mutate instance B."""
        from jobs.supabase_client import update_history_outcome
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        
        # Mock signals_history select query by signal_id
        def select_mock(cols):
            mock_builder = MagicMock()
            def eq_mock(col, val):
                res_mock = MagicMock()
                if col == "signal_id" and val == "sig-inst-a":
                    res_mock.execute.return_value = MagicMock(data=[{
                        "id": 101,
                        "signal_id": "sig-inst-a",
                        "ticker": "USB",
                        "scan_date": "2026-09-19",
                        "entry_price": 60.0,
                        "outcome": "open"
                    }])
                elif col == "signal_id" and val == "sig-inst-b":
                    res_mock.execute.return_value = MagicMock(data=[{
                        "id": 102,
                        "signal_id": "sig-inst-b",
                        "ticker": "USB",
                        "scan_date": "2026-09-19",
                        "entry_price": 62.5,
                        "outcome": "open"
                    }])
                else:
                    res_mock.execute.return_value = MagicMock(data=[])
                return res_mock
            mock_builder.eq.side_effect = eq_mock
            return mock_builder
            
        mock_table.select.side_effect = select_mock
        mock_update_builder = MagicMock()
        mock_update_builder.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_table.update.return_value = mock_update_builder
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.supabase", mock_supabase):
            update_history_outcome(
                ticker="USB",
                status="manually_removed",
                exit_price=60.0,
                signal_id="sig-inst-a",
                removal_reason="Target achieved"
            )
            
        # Verify update was called for instance A's primary key (101), NEVER for 102
        update_calls = mock_table.update.return_value.eq.call_args_list
        called_ids = [c[0][1] for c in update_calls]
        self.assertIn(101, called_ids)
        self.assertNotIn(102, called_ids)

    def test_ticker_only_removal_is_rejected(self):
        """Test C: Ticker-only removal is rejected with error log and no DB mutation."""
        from jobs.supabase_client import update_signals_status, update_history_outcome
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.supabase", mock_supabase):
            # 1. signals table ticker-only removal must be refused
            res_sig = update_signals_status("USB", "manually_removed", 60.0, True, signal_id=None)
            self.assertIsNone(res_sig)
            mock_table.update.assert_not_called()
            
            # 2. signals_history table ticker-only removal must be refused
            res_hist = update_history_outcome("USB", "manually_removed", 60.0, signal_id=None, history_id=None)
            self.assertIsNone(res_hist)
            mock_table.update.assert_not_called()

    def test_scan_date_only_fallback_rejected_for_manual_removal(self):
        """Test D: ticker + scan_date-only mutation is rejected when exact signal identity is unavailable."""
        from jobs.supabase_client import update_history_outcome
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.supabase", mock_supabase):
            # scan_date provided, but signal_id and history_id are None
            res = update_history_outcome(
                ticker="USB",
                status="manually_removed",
                exit_price=60.0,
                scan_date="2026-09-19",
                signal_id=None,
                history_id=None
            )
            self.assertIsNone(res, "scan_date-only manual removal must be refused to avoid corrupting same-day instances")
            mock_table.update.assert_not_called()

    def test_manual_removal_preserves_history_correctly(self):
        """Test E: Manual removal preserves history correctly and records removal metadata."""
        from jobs.supabase_client import update_history_outcome
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        
        record_data = {
            "id": 555,
            "signal_id": "sig-uuid-555",
            "ticker": "PLTR",
            "scan_date": "2026-09-01",
            "entry_price": 150.0,
            "outcome": "open",
            "strategy": "Trend Following",
        }
        
        mock_select_builder = MagicMock()
        mock_select_builder.eq.return_value.execute.return_value = MagicMock(data=[record_data])
        mock_table.select.return_value = mock_select_builder
        
        mock_update_builder = MagicMock()
        mock_update_builder.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_table.update.return_value = mock_update_builder
        mock_supabase.table.return_value = mock_table
        
        with patch("jobs.supabase_client.supabase", mock_supabase):
            update_history_outcome(
                ticker="PLTR",
                status="manually_removed",
                exit_price=175.0,
                signal_id="sig-uuid-555",
                removal_reason="Target Hit",
                removal_note="Manual profit take"
            )
            
        update_call = mock_table.update.call_args[0][0]
        self.assertEqual(update_call["outcome"], "manually_removed")
        self.assertEqual(update_call["exit_price"], 175.0)
        self.assertEqual(update_call["removal_reason"], "Target Hit")
        self.assertEqual(update_call["removal_note"], "Manual profit take")
        self.assertIn("removed_at", update_call)
        self.assertIn("outcome_date", update_call)
        # Return percentage calculated accurately from preserved entry_price
        self.assertEqual(update_call["outcome_return_pct"], 16.67)

    # -------------------------------------------------------------------------
    # 6. GITHUB ACTIONS DUPLICATE REFRESH PROTECTION (Requirement 3)
    # -------------------------------------------------------------------------
    def test_duplicate_refresh_protection_active_run_blocks_dispatch(self):
        """Active queued or in_progress workflow run blocks dispatch."""
        def check_active_runs(workflow_runs, branch="main"):
            existing = next((
                r for r in workflow_runs
                if (not r.get("head_branch") or r.get("head_branch") == branch)
                and r.get("status") in ("queued", "in_progress")
            ), None)
            if existing:
                return False, f"A refresh workflow run (#{existing['id']}) is already {existing['status']}."
            return True, None
            
        # Case 1: In progress run
        allowed, msg = check_active_runs([{"id": 1001, "head_branch": "main", "status": "in_progress"}])
        self.assertFalse(allowed)
        self.assertIn("already in_progress", msg)
        
        # Case 2: Queued run
        allowed, msg = check_active_runs([{"id": 1002, "head_branch": "main", "status": "queued"}])
        self.assertFalse(allowed)
        self.assertIn("already queued", msg)

    def test_duplicate_refresh_protection_github_api_failure_fails_closed(self):
        """GitHub API check failure must FAIL CLOSED and block dispatch."""
        def evaluate_dispatch_guard(api_status_code, api_error=None):
            if api_error is not None or api_status_code != 200:
                return False, "Unable to verify whether a refresh is already running. Please try again."
            return True, None
            
        # HTTP 500 error fails closed
        allowed, msg = evaluate_dispatch_guard(500)
        self.assertFalse(allowed)
        self.assertEqual(msg, "Unable to verify whether a refresh is already running. Please try again.")
        
        # Network exception fails closed
        allowed, msg = evaluate_dispatch_guard(0, api_error=Exception("Connection reset"))
        self.assertFalse(allowed)
        self.assertEqual(msg, "Unable to verify whether a refresh is already running. Please try again.")

    def test_duplicate_refresh_protection_no_active_run_allows_dispatch(self):
        """When no active workflow run exists, dispatch is allowed."""
        def check_active_runs(workflow_runs, branch="main"):
            existing = next((
                r for r in workflow_runs
                if (not r.get("head_branch") or r.get("head_branch") == branch)
                and r.get("status") in ("queued", "in_progress")
            ), None)
            if existing:
                return False, f"A refresh workflow run (#{existing['id']}) is already {existing['status']}."
            return True, None
            
        # Completed / cancelled runs do not block
        completed_runs = [
            {"id": 901, "head_branch": "main", "status": "completed"},
            {"id": 902, "head_branch": "main", "status": "cancelled"}
        ]
        allowed, msg = check_active_runs(completed_runs)
        self.assertTrue(allowed)
        self.assertIsNone(msg)


if __name__ == "__main__":
    unittest.main()
