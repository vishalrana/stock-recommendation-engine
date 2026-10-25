"""A scan that skips more than 1% of its tickers as stale (2026-10-21: 5,148 of 5,605) is stale_data:
no new ideas, the count in error_message, and the 04:00 attempt rescans."""

import argparse
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SESSION = date(2026, 10, 21)


class NoSignal:
    name = "Trend Following"

    def scan(self, ticker, df, regime, metrics):
        return None


def _history(last):
    idx = pd.bdate_range(end=last, periods=320)
    close = np.linspace(40.0, 60.0, len(idx))
    return pd.DataFrame({"OPEN": close, "HIGH": close, "LOW": close, "CLOSE": close, "VOLUME": 5_000_000.0}, index=idx)


def _scan(n_tickers, n_stale, exclusions_path=None):
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    stale = set(tickers[:n_stale])
    tables = {}
    client = MagicMock()
    client.table.side_effect = lambda name: tables.setdefault(name, MagicMock())
    cm = MagicMock()
    cm.is_stale.return_value = False
    cm.get_last_cached_date.return_value = SESSION
    cm.pending_refetch.return_value = set()
    cm.split_flagged.return_value = set()
    cm.get_ticker_history.side_effect = lambda t, s, e: _history("2026-10-20" if t in stale else "2026-10-21")
    with patch("jobs.generate_signals.get_client", return_value=client), \
            patch("src.utils.market_date.latest_completed_session", return_value=SESSION), \
            patch("jobs.generate_signals.get_regime",
                  return_value={"regime": "bull", "spy_price": 500, "spy_200dma": 450, "date": "2026-10-21"}), \
            patch("jobs.generate_signals.apply_vix_override", return_value=("bull", ["Trend Following"])), \
            patch("jobs.generate_signals.glob.glob", return_value=["dummy.parquet"]), \
            patch("jobs.generate_signals.load_universe", return_value=(tickers, {}, {})), \
            patch("jobs.generate_signals.open_idea_tickers", return_value=[]), \
            patch("jobs.strategies.sector_rotation.SECTOR_ETFS", {}), \
            patch("jobs.generate_signals.get_cache_manager", return_value=cm), \
            patch("jobs.generate_signals.fetch_earnings_calendar", return_value={}), \
            patch("jobs.generate_signals.load_cached_metrics", return_value={}), \
            patch("jobs.generate_signals._prepare_sec_earnings"), \
            patch("jobs.generate_signals._sec_run_status", return_value=(None, False)), \
            patch("jobs.generate_signals.STRATEGIES", [NoSignal()]), \
            patch("jobs.generate_signals.reconcile_recommendation_lifecycle"), \
            patch("jobs.generate_signals.EXCLUSIONS_PATH",
                  exclusions_path or os.path.join(tempfile.gettempdir(), "nightly_exclusions_test.json")), \
            patch("argparse.ArgumentParser.parse_args",
                  return_value=argparse.Namespace(dry_run=False, force_refresh=False, verbose=False, cache_mode="local")):
        from jobs.generate_signals import main
        main()
    return [c.args[0] for c in tables["scan_log"].upsert.call_args_list][-1]


class TestStaleShare(unittest.TestCase):
    def test_the_2026_10_21_shape_is_stale_data(self):
        row = _scan(20, 19)
        self.assertEqual(row["status"], "stale_data")
        self.assertIn("Stale bars: 19 of 20 tickers (95.0%) have no 2026-10-21 bar", row["error_message"])
        self.assertEqual(row["signals_recommended"], 0)

    def test_the_full_exclusion_lists_are_saved(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nightly_exclusions.json")
            _scan(20, 19, path)
            doc = json.load(open(path, encoding="utf-8"))
        self.assertEqual(doc["session"], "2026-10-21")
        self.assertEqual(len(doc["stale"]), 19)                         # all of them, not 15 as in scan_log

    def test_a_normal_night_stays_success(self):
        row = _scan(200, 1)                      # 0.5%, as the 1-3 of ~5,500 on normal nights
        self.assertEqual(row["status"], "success")


if __name__ == "__main__":
    unittest.main()
