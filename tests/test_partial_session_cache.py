"""A cache whose newest daily file holds the session for a few tickers only (2026-10-21: a rebuild run
at 20:46 ET, before Yahoo posted most bars) must still download the session for every other ticker."""

import argparse
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.cache_manager import CacheManager

TICKERS = [f"T{i:02d}" for i in range(20)]
SESSION = date(2026, 10, 21)


def _cache(d):
    """Cache with every ticker through 2026-10-20 and only T00 (5%) on the session."""
    cm = CacheManager(cache_dir=d)
    for day, names in ((date(2026, 10, 20), TICKERS), (SESSION, TICKERS[:1])):
        idx = pd.MultiIndex.from_tuples([(t, pd.Timestamp(day)) for t in names], names=["Ticker", "Date"])
        cm.save_data_for_date(day.isoformat(), pd.DataFrame({"OPEN": 1.0, "HIGH": 1.0, "LOW": 1.0, "CLOSE": 1.0,
                                                             "VOLUME": 1.0}, index=idx))
    return cm


class TestPartialSession(unittest.TestCase):
    def test_helper_lists_the_tickers_without_the_session(self):
        with tempfile.TemporaryDirectory() as d:
            cm = _cache(d)
            self.assertEqual(cm.get_last_cached_date(), SESSION)              # the newest file has the session...
            self.assertEqual(cm.tickers_without_close(TICKERS, SESSION), TICKERS[1:])   # ...for T00 only

    def test_nightly_downloads_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            real = _cache(d)
            cm = MagicMock()
            cm.get_last_cached_date.return_value = SESSION
            cm.tickers_without_close.side_effect = real.tickers_without_close
            cm.pending_refetch.return_value = set()
            cm.split_flagged.return_value = set()
            with patch("jobs.generate_signals.get_client", return_value=MagicMock()), \
                    patch("src.utils.market_date.latest_completed_session", return_value=SESSION), \
                    patch("jobs.generate_signals.get_regime",
                          return_value={"regime": "bull", "spy_price": 500, "spy_200dma": 450, "date": "2026-10-21"}), \
                    patch("jobs.generate_signals.apply_vix_override", return_value=("bull", [])), \
                    patch("jobs.generate_signals.glob.glob", return_value=["dummy.parquet"]), \
                    patch("jobs.generate_signals.load_universe", return_value=(TICKERS, {}, {})), \
                    patch("jobs.generate_signals.open_idea_tickers", return_value=[]), \
                    patch("jobs.generate_signals.SECTOR_ETFS", {}, create=True), \
                    patch("jobs.strategies.sector_rotation.SECTOR_ETFS", {}), \
                    patch("jobs.generate_signals.get_cache_manager", return_value=cm), \
                    patch("jobs.generate_signals.fetch_earnings_calendar", return_value={}), \
                    patch("jobs.generate_signals.load_cached_metrics", return_value={}), \
                    patch("jobs.generate_signals._prepare_sec_earnings"), \
                    patch("jobs.generate_signals.STRATEGIES", []), \
                    patch("jobs.generate_signals.reconcile_recommendation_lifecycle"), \
                    patch("argparse.ArgumentParser.parse_args",
                          return_value=argparse.Namespace(dry_run=False, force_refresh=False, verbose=False,
                                                          cache_mode="incremental")):
                from jobs.generate_signals import main
                main()
            self.assertTrue(cm.refresh_with_actions.called, "the 95% without the session must be downloaded")
            args = cm.refresh_with_actions.call_args.args
            self.assertEqual(sorted(args[0]), TICKERS[1:])
            self.assertEqual(args[2], SESSION.isoformat())


if __name__ == "__main__":
    unittest.main()
