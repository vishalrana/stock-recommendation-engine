"""A candidate that fails scoring validation is logged as rejected (it used to be dropped)."""

import argparse
import os
import sys
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeStrategy:
    name = "Pullback Recovery"

    def scan(self, ticker, df, regime, metrics):
        p = float(df["CLOSE"].iloc[-1])
        return {"ticker": ticker, "strategy": self.name, "scan_date": "2026-10-19", "company_name": "Apple",
                "industry": "Tech", "price": p, "entry_price": p, "stop_loss": p * 0.95, "exit_price": p * 1.1,
                "current_rsi": 50.0, "volume_ratio": 1.0, "adx_value": 25.0, "macd_histogram": 0.1}


class TestScoringFailureIsLogged(unittest.TestCase):
    def test_scoring_failure_reaches_the_audit_rows(self):
        idx = pd.bdate_range(end="2026-10-19", periods=320)
        close = np.linspace(40.0, 60.0, len(idx))
        df = pd.DataFrame({"OPEN": close, "HIGH": close * 1.01, "LOW": close * 0.99, "CLOSE": close,
                           "VOLUME": 5_000_000.0}, index=idx)
        tables = {}
        client = MagicMock()
        client.table.side_effect = lambda name: tables.setdefault(name, MagicMock())
        cm = MagicMock()
        cm.is_stale.return_value = False
        cm.get_last_cached_date.return_value = "2026-10-19"
        cm.get_ticker_history.return_value = df
        with patch("jobs.generate_signals.get_client", return_value=client), \
                patch("src.utils.market_date.latest_completed_session", return_value=date(2026, 10, 19)), \
                patch("jobs.generate_signals.get_regime", return_value={"regime": "bull", "spy_price": 500, "spy_200dma": 450, "date": "2026-10-19"}), \
                patch("jobs.generate_signals.apply_vix_override", return_value=("bull", ["Pullback Recovery"])), \
                patch("jobs.generate_signals.glob.glob", return_value=["dummy.parquet"]), \
                patch("jobs.generate_signals.load_universe", return_value=(["AAPL"], {"AAPL": "Apple"}, {"AAPL": "Tech"})), \
                patch("jobs.generate_signals.get_cache_manager", return_value=cm), \
                patch("jobs.generate_signals.fetch_earnings_calendar", return_value={}), \
                patch("jobs.generate_signals.load_cached_metrics", return_value={}), \
                patch("jobs.generate_signals._prepare_sec_earnings"), \
                patch("jobs.generate_signals.STRATEGIES", [FakeStrategy()]), \
                patch("src.pipeline_steps.composite_score", return_value="Validation failed: test feature missing"), \
                patch("jobs.generate_signals.reconcile_recommendation_lifecycle"), \
                patch.dict(os.environ, {"SKIP_NLP": "true"}), \
                patch("argparse.ArgumentParser.parse_args",
                      return_value=argparse.Namespace(dry_run=False, force_refresh=False, verbose=False, cache_mode="local")):
            from jobs.generate_signals import main
            main()
        inserted = [r for c in tables["signals"].insert.call_args_list for r in c.args[0]]
        reasons = {r["ticker"]: r.get("rejection_reason") for r in inserted}
        self.assertEqual(reasons.get("AAPL"), "Validation failed: test feature missing")


if __name__ == "__main__":
    unittest.main()
