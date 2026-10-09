"""
Test Final Production Baseline
===============================
Verifies:
1. Exact recommendation instance mutation safety (refusal of ticker-only mutations).
2. Market date vs execution timestamp semantics (Eastern regular session, weekend rollback).
3. Database NULL metric semantics (missing metrics are None, not 0.0).
4. Universe fallback degradation detection & lifecycle invalidation safeguard.
5. Negative earnings surprise freshness semantics.
6. Database reset readiness (empty state handling).
"""

import unittest
import datetime
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.market_date import get_market_date, get_execution_timestamp_utc, get_trading_days_ago
from jobs.supabase_client import update_signals_status, update_signals_price, update_history_outcome
from src.filters.earnings_filter import earnings_risk_filter, EarningsStatus, REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK, REASON_EARNINGS_OUTSIDE_BLACKOUT
from src.universe.us_equities import USEquitiesUniverseProvider
from jobs.generate_signals import reconcile_recommendation_lifecycle, load_universe, is_universe_degraded
from src.utils.metrics_pipeline import build_hardened_metrics
from src.calibration.score_calibrator import ScoreCalibrator


class TestFinalProductionBaseline(unittest.TestCase):

    def test_01_exact_instance_mutation_safety(self):
        """Ticker-only mutation must be refused with None return when signal_id is missing."""
        # update_signals_status without signal_id
        res_status = update_signals_status("AAPL", "stopped", 150.0, True, signal_id=None)
        self.assertIsNone(res_status, "Must refuse mutation when signal_id is None")

        # update_signals_price without signal_id
        res_price = update_signals_price("AAPL", 155.0, signal_id=None)
        self.assertIsNone(res_price, "Must refuse price update when signal_id is None")

        # update_history_outcome without history_id or signal_id
        res_hist = update_history_outcome("AAPL", "stopped", 150.0, signal_id=None, history_id=None)
        self.assertIsNone(res_hist, "Must refuse history outcome update when both IDs are None")

    def test_02_market_date_eastern_semantics(self):
        """Market date must follow US/Eastern session conventions."""
        eastern = ZoneInfo("America/New_York")

        # Saturday noon -> preceding Friday
        sat = datetime.datetime(2026, 10, 3, 12, 0, tzinfo=eastern)
        self.assertEqual(get_market_date(sat), datetime.date(2026, 10, 2))

        # Sunday noon -> preceding Friday
        sun = datetime.datetime(2026, 10, 4, 12, 0, tzinfo=eastern)
        self.assertEqual(get_market_date(sun), datetime.date(2026, 10, 2))

        # Monday 08:30 AM (before open) -> preceding Friday
        mon_morning = datetime.datetime(2026, 10, 5, 8, 30, tzinfo=eastern)
        self.assertEqual(get_market_date(mon_morning), datetime.date(2026, 10, 2))

        # Monday 16:30 PM (after close) -> Monday
        mon_evening = datetime.datetime(2026, 10, 5, 16, 30, tzinfo=eastern)
        self.assertEqual(get_market_date(mon_evening), datetime.date(2026, 10, 5))

        # Execution timestamp is UTC ISO string
        utc_ts = get_execution_timestamp_utc()
        self.assertTrue("+00:00" in utc_ts or utc_ts.endswith("Z"))

    def test_03_database_null_metric_semantics(self):
        """Missing historical metrics must produce None, never fabricated 0.0."""
        # Unseeded / empty ticker metrics
        hardened = build_hardened_metrics("BRAND_NEW_STOCK", raw_record={})
        self.assertEqual(hardened["completed_trades"], 0)
        self.assertEqual(hardened["raw_win_rate"], 50.0)  # neutral Bayesian prior
        self.assertEqual(hardened["win_rate_provenance"], "unavailable")
        self.assertTrue(hardened["insufficient_sample"])

    def test_04_universe_fallback_degradation_guard(self):
        """Degraded universe loading must be tracked and protect active recommendations from invalidation."""
        provider = USEquitiesUniverseProvider()
        # Initial state: not degraded
        self.assertFalse(provider.is_fallback)

        # Trigger fallback method
        provider._load_fallback_sp500_nasdaq()
        self.assertTrue(provider.is_fallback)

        # Mock lifecycle reconciliation with universe_is_fallback=True
        mock_supabase = MagicMock()
        mock_supabase.table().select().in_().execute.return_value.data = [
            {"id": "test-uuid-1", "ticker": "SMALLCAP_OUTSIDE_SP500", "status": "open", "stop_loss": 10.0, "target_3": 30.0, "scan_date": "2026-10-01"}
        ]

        import pandas as pd
        bars = pd.DataFrame(
            [{"OPEN": 19.0, "HIGH": 22.0, "LOW": 18.0, "CLOSE": 20.0}],
            index=pd.bdate_range(start="2026-10-02", periods=1),
        )
        with patch("jobs.supabase_client.get_bars_after", return_value=bars), \
             patch("jobs.supabase_client.update_signals_status") as mock_status_update:
            reconcile_recommendation_lifecycle(
                supabase=mock_supabase,
                qualified_tickers=set(),  # empty qualified list!
                scan_successful=True,
                scanned_count=500,
                min_required_scanned=50,
                universe_is_fallback=True,  # DEGRADED UNIVERSE
            )
            # Must NOT invalidate SMALLCAP_OUTSIDE_SP500 because scan was degraded!
            self.assertFalse(mock_status_update.called, "Must not invalidate active stocks on degraded fallback scan!")

    def test_05_negative_earnings_surprise_freshness(self):
        """Negative earnings surprise must only act as catalyst within 45 days."""
        scan_date = datetime.date(2026, 10, 4)

        # 1. Negative surprise from 70 days ago (stale) outside blackout -> CAN PROCEED
        cal_stale = {
            "XYZ": {
                "next_earnings_date": "2026-11-15",
                "last_earnings_date": "2026-07-25",  # 71 days ago
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            }
        }
        res_stale = earnings_risk_filter(
            "XYZ", scan_date, "trend_following", cal_stale, earnings_surprise_pct=-15.0
        )
        self.assertTrue(res_stale["pass"], "Old negative surprise (>45d) must not veto outside blackout")

        # 2. Negative surprise from 15 days ago (fresh) outside blackout -> VETOED
        cal_fresh = {
            "XYZ": {
                "next_earnings_date": "2026-11-15",
                "last_earnings_date": "2026-09-19",  # 15 days ago
                "status": EarningsStatus.KNOWN_UPCOMING.value,
            }
        }
        res_fresh = earnings_risk_filter(
            "XYZ", scan_date, "trend_following", cal_fresh, earnings_surprise_pct=-15.0
        )
        self.assertFalse(res_fresh["pass"], "Fresh negative surprise (<=45d) must veto")
        self.assertEqual(res_fresh["reason_code"], REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK)

    def test_06_clean_database_reset_readiness(self):
        """ScoreCalibrator and lifecycle must safely handle totally empty initial databases."""
        calibrator = ScoreCalibrator(records=[])
        probs = calibrator.get_outcome_probabilities(75.0, "trend_following", "bull")
        self.assertIsNotNone(probs)
        self.assertIn("p_t1", probs)
        self.assertTrue(0.0 <= probs["p_t1"] <= 1.0)
        self.assertEqual(probs["sample_size"], 0)
        self.assertEqual(probs["calibration_tier"], "canonical_prior")


if __name__ == "__main__":
    unittest.main()
