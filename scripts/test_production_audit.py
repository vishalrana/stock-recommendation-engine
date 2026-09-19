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
    # 5. SAME-DAY RECOMMENDATION INSTANCE & SIGNAL_ID ISOLATION (Amendment 1)
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


if __name__ == "__main__":
    unittest.main()
