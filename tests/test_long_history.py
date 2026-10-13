"""Truncated-history guard in scripts/build_long_history.py (Yahoo can return a short history)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.build_long_history as blh


def frame(start: str, end: str = "2026-10-09") -> pd.DataFrame:
    idx = pd.bdate_range(start, end)
    raw = pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.0, "Adj Close": 10.0,
                        "Volume": 1e6, "Stock Splits": 0.0, "Dividends": 0.0}, index=idx)
    return blh.process_ticker_frame(raw)


class TestHistoryGuard(unittest.TestCase):
    def run_build(self, cached, fresh_by_call):
        calls = iter(fresh_by_call)
        with tempfile.TemporaryDirectory() as d:
            for t, start in cached.items():
                frame(start, "2026-09-01").to_parquet(os.path.join(d, f"{t}.parquet"))
            def fake(chunk, start_date="2004-01-01"):
                nxt = next(calls, {})
                return {t: frame(s) for t, s in nxt.items() if t in chunk}
            check = {}
            with mock.patch.object(blh, "download_chunk", side_effect=fake), mock.patch.object(blh.time, "sleep"), \
                    mock.patch.object(blh, "FAILURES_CSV", os.path.join(d, "f.csv")):
                ok, failed = blh.build_long_history(tickers=list(set(cached) | {"NEW"}), output_dir=d, history_check=check)
            firsts = {t: pd.read_parquet(os.path.join(d, f"{t}.parquet")).index[0].date().isoformat()
                      for t in ("AAA", "BBB", "CCC", "NEW") if os.path.exists(os.path.join(d, f"{t}.parquet"))}
            ends = {t: pd.read_parquet(os.path.join(d, f"{t}.parquet")).index[-1].date().isoformat() for t in firsts}
            return ok, failed, check, firsts, ends

    def test_truncated_fresh_never_replaces_longer_cache(self):
        cached = {"AAA": "2004-01-02", "BBB": "2024-05-01", "CCC": "2004-01-02"}
        first = {"AAA": "2026-08-14", "BBB": "2004-01-02", "CCC": "2004-01-02", "NEW": "2020-01-02"}
        retry = {"AAA": "2026-08-14"}                    # still truncated on every retry
        ok, failed, check, firsts, ends = self.run_build(cached, [first, {}, retry, retry, retry])
        self.assertEqual(firsts["AAA"], "2004-01-02")     # cached full history kept
        self.assertEqual(ends["AAA"], "2026-09-01")
        self.assertEqual(firsts["BBB"], "2004-01-02")     # truncated cache replaced by full fresh data
        self.assertEqual(ends["CCC"], "2026-10-09")       # refreshed
        self.assertEqual(firsts["NEW"], "2020-01-02")
        self.assertEqual((ok, failed), (4, 0))
        self.assertEqual(check["fresh_started_later"], 1)
        self.assertEqual(check["kept_cached_longer"], 1)

    def test_retry_recovers_full_history(self):
        cached = {"AAA": "2004-01-02"}
        first = {"AAA": "2026-08-14", "NEW": "2020-01-02"}
        ok, failed, check, firsts, ends = self.run_build(cached, [first, {"AAA": "2004-01-02"}])
        self.assertEqual((firsts["AAA"], ends["AAA"]), ("2004-01-02", "2026-10-09"))
        self.assertEqual(check["recovered_on_retry"], 1)
        self.assertEqual(check["kept_cached_longer"], 0)


if __name__ == "__main__":
    unittest.main()
