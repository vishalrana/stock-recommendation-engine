"""
The nightly scan's market date comes from the NYSE calendar and the run's clock (2026-10-20). A run
whose data stops before the latest completed session is stale: no new ideas, lifecycle still runs.
"""

import argparse
import os
import sys
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.market_date import latest_completed_session

NY = ZoneInfo("America/New_York")


class TestLatestCompletedSession(unittest.TestCase):
    def test_clock_cases(self):
        cases = [
            (datetime(2026, 10, 19, 10, 0, tzinfo=NY), date(2026, 10, 16)),   # Monday before the close
            (datetime(2026, 10, 19, 16, 10, tzinfo=NY), date(2026, 10, 16)),  # inside the 16:15 buffer
            (datetime(2026, 10, 19, 16, 20, tzinfo=NY), date(2026, 10, 19)),  # after the close
            (datetime(2026, 10, 20, 1, 50, tzinfo=NY), date(2026, 10, 19)),   # nightly run (05:50 UTC)
            (datetime(2026, 10, 17, 12, 0, tzinfo=NY), date(2026, 10, 16)),   # Saturday
            (datetime(2026, 11, 26, 18, 0, tzinfo=NY), date(2026, 11, 25)),   # Thanksgiving
        ]
        for now, expected in cases:
            self.assertEqual(latest_completed_session(now), expected, now)

    def test_swing_job_uses_the_same_function(self):
        import src.swing.live as live
        self.assertIs(live.latest_completed_session, latest_completed_session)


class TestStaleRun(unittest.TestCase):
    def test_data_behind_the_session_is_stale_and_lifecycle_still_runs(self):
        tables = {}

        def table(name):
            return tables.setdefault(name, MagicMock())

        client = MagicMock()
        client.table.side_effect = table
        cm = MagicMock()
        cm.is_stale.return_value = False
        cm.get_last_cached_date.return_value = "2026-10-16"   # Yahoo has not posted 2026-10-19 yet
        with patch("jobs.generate_signals.get_client", return_value=client), \
                patch("src.utils.market_date.latest_completed_session", return_value=date(2026, 10, 19)), \
                patch("jobs.generate_signals.get_regime",
                      return_value={"regime": "bull", "spy_price": 500, "spy_200dma": 450, "date": "2026-10-16"}), \
                patch("jobs.generate_signals.apply_vix_override", return_value=("bull", ["Pullback Recovery"])), \
                patch("jobs.generate_signals.glob.glob", return_value=["dummy.parquet"]), \
                patch("jobs.generate_signals.load_universe", return_value=(["AAPL"], {"AAPL": "Apple"}, {"AAPL": "Tech"})), \
                patch("jobs.generate_signals.get_cache_manager", return_value=cm), \
                patch("jobs.generate_signals.fetch_earnings_calendar", return_value={}), \
                patch("jobs.generate_signals.load_cached_metrics", return_value={}), \
                patch("jobs.generate_signals._prepare_sec_earnings"), \
                patch("jobs.generate_signals.STRATEGIES", []), \
                patch("jobs.generate_signals.reconcile_recommendation_lifecycle") as rec, \
                patch("argparse.ArgumentParser.parse_args",
                      return_value=argparse.Namespace(dry_run=False, force_refresh=False, verbose=False, cache_mode="local")):
            from jobs.generate_signals import main
            main()
        self.assertTrue(rec.called, "lifecycle must still be reconciled on stale data")
        logged = [c.args[0] for c in tables["scan_log"].upsert.call_args_list]
        self.assertTrue(logged, "the scan must be logged")
        self.assertEqual(logged[-1]["scan_date"], "2026-10-19")   # the session from the calendar, not SPY's bar
        self.assertEqual(logged[-1]["status"], "stale_data")
        self.assertEqual(logged[-1]["signals_recommended"], 0)


if __name__ == "__main__":
    unittest.main()
