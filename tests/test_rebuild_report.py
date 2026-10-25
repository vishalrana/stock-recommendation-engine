"""Monthly rebuild drift report (scripts/time_price_refresh.drift_report)."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.time_price_refresh import drift_report


class TestDriftReport(unittest.TestCase):
    def test_lists_and_checks(self):
        d = pd.bdate_range("2025-01-01", periods=260)
        old = pd.DataFrame({"A": 100.0, "B": 100.0, "C": 100.0, "S": 100.0}, index=d)
        old.loc[d[:20], "C"] = np.nan                    # 240 live bars (close-less rows of old caches don't count)
        new = old.copy()
        new["A"] = 99.0                                  # re-adjusted: drift
        new["C"] = 100.0                                 # now a full year
        new.loc[d[:50], "S"] = np.nan                    # Yahoo serves fewer bars
        r = drift_report(old, new, ["A", "B", "C", "S"], d[-1].date())
        self.assertEqual((r["drifted"], r["newly_long"], r["shorter"]), (["A"], ["C"], ["S"]))
        self.assertEqual(r["session_coverage_pct"], 100.0)
        self.assertEqual(r["history_kept_pct"], 66.67)   # A, B kept a year; S lost it
        self.assertEqual(r["compared_session"], d[-1].date().isoformat())

    def test_coverage_is_measured_on_the_live_cache_last_session(self):
        d = pd.bdate_range("2025-01-01", periods=260)
        old = pd.DataFrame({"A": 100.0, "B": 100.0}, index=d[:-1])   # the live cache stops a session earlier
        new = pd.DataFrame({"A": 100.0, "B": 100.0}, index=d)
        new.loc[d[-2], "B"] = np.nan
        r = drift_report(old, new, ["A", "B"], d[-1].date())
        self.assertEqual(r["session_coverage_pct"], 50.0)


class TestPartialLastSession(unittest.TestCase):
    def _cache(self, d, last_names):
        from src.data.cache_manager import CacheManager
        cm = CacheManager(cache_dir=d)                         # first: it clears a directory without its version file
        days = pd.bdate_range("2026-10-19", periods=3)          # 10-19, 10-20, 10-21
        for day in days:
            names = last_names if day == days[-1] else [f"T{i}" for i in range(100)]
            idx = pd.MultiIndex.from_tuples([(t, day) for t in names], names=["Ticker", "Date"])
            pd.DataFrame({"CLOSE": 1.0}, index=idx).to_parquet(os.path.join(d, f"{day.date()}.parquet"))
        return cm.cached_closes("0000-00-00", "9999-99-99")

    def test_a_session_one_ticker_covers_is_dropped(self):
        import tempfile
        from scripts.time_price_refresh import trim_partial_last_session
        with tempfile.TemporaryDirectory() as d:
            cov, dropped = trim_partial_last_session(self._cache(d, ["T0"]), d)   # 2026-10-21 at 20:46 ET
            self.assertEqual((cov, dropped), (1.0, "2026-10-21"))
            self.assertFalse(os.path.exists(os.path.join(d, "2026-10-21.parquet")))
            self.assertTrue(os.path.exists(os.path.join(d, "2026-10-20.parquet")))

    def test_a_full_session_is_kept(self):
        import tempfile
        from scripts.time_price_refresh import trim_partial_last_session
        with tempfile.TemporaryDirectory() as d:
            cov, dropped = trim_partial_last_session(self._cache(d, [f"T{i}" for i in range(99)]), d)
            self.assertEqual((cov, dropped), (99.0, None))
            self.assertTrue(os.path.exists(os.path.join(d, "2026-10-21.parquet")))


class TestSecAudit(unittest.TestCase):
    def test_wrongly_parsed_documents_stand_out(self):
        import json
        import tempfile
        from scripts.time_price_refresh import sec_acceptance_audit
        with tempfile.TemporaryDirectory() as d:
            # 2026-10-23 05:00Z: header-correct times; 2026-10-25 13:00Z: 4 h early (07:00 -> 03:00)
            for name, ts, times in (("CIK0000000001", 1792731600, ["17:53:18", "08:00:00"]),
                                    ("CIK0000000002", 1792933200, ["03:00:00", "12:30:00"])):
                rows = [["8-K", f"2026-07-29T{t}", "2.02"] for t in times]
                json.dump({"fetched_at": ts, "recent_rows": rows, "pages": {}}, open(os.path.join(d, name + ".json"), "w"))
            audit = sec_acceptance_audit(d)
        self.assertEqual(audit["2026-10-23T05"]["share_before_06_et"], 0.0)
        self.assertEqual(audit["2026-10-25T13"]["share_before_06_et"], 0.5)
        self.assertEqual(audit["2026-10-23T05"]["wrong_documents"], 0)
        self.assertEqual(audit["2026-10-25T13"]["wrong_documents"], 1)


class TestSplitSweep(unittest.TestCase):
    def test_lists_only_what_the_wider_check_adds(self):
        from unittest import mock
        from scripts.time_price_refresh import split_sweep
        idx = pd.bdate_range("2026-09-21", periods=10)
        jump = pd.Series([10.0] * 6 + [100.0] * 4, index=idx)          # 1-for-10 left unadjusted, jump on idx[6]
        cm = mock.MagicMock()
        cm._read_json.return_value = {"LATE": [[str(idx[7].date()), 0.1]],      # Yahoo lists it a session late
                                      "ONDATE": [[str(idx[6].date()), 0.1]]}    # on the jump: today's rule
        cm.cached_closes.return_value = pd.DataFrame({"LATE": jump, "ONDATE": jump})
        cm.split_flagged.return_value = {"ONDATE"}
        out = split_sweep(cm)
        self.assertEqual(out["newly_flagged_tickers"], ["LATE"])
        n = out["newly_flagged"][0]
        self.assertEqual((n["session"], n["offset"], n["miss_pct_of_target"]), (str(idx[6].date()), -1, 0.0))
        self.assertEqual((out["flagged_today_rule"], out["stored_not_recomputed"]), (1, []))


class TestSplitExposure(unittest.TestCase):
    def test_ideas_from_an_unadjusted_split_on_are_counted(self):
        import tempfile
        from unittest import mock
        from scripts.time_price_refresh import split_exposure
        idx = pd.bdate_range("2026-01-05", periods=60)
        with tempfile.TemporaryDirectory() as d:
            hist = os.path.join(d, "hist")
            os.makedirs(hist)
            # the stored history the run used: BAD's 1-for-10 on idx[30] left unadjusted, OK has no split
            pd.DataFrame({"CLOSE": [1.0] * 30 + [10.0] * 30}, index=idx).to_parquet(os.path.join(hist, "BAD.parquet"))
            pd.DataFrame({"CLOSE": [5.0] * 60}, index=idx).to_parquet(os.path.join(hist, "OK.parquet"))
            trades = pd.DataFrame([("BAD", idx[35], idx[36], idx[45], -5.5),      # filled after the split
                                   ("BAD", idx[20], idx[21], idx[32], 3.5),       # open across it
                                   ("BAD", idx[5], idx[6], idx[15], 0.5),         # before it
                                   ("OK", idx[35], idx[36], idx[45], 1.0)],
                                  columns=["ticker", "signal_date", "fill_date", "exit_date", "beta_adjusted_pct"])
            trades = trades.assign(strategy="Trend Following", outcome="expired", net_return_pct=trades.beta_adjusted_pct)
            for c in ("signal_date", "fill_date", "exit_date"):
                trades[c] = trades[c].dt.date.astype(str)
            trades.to_csv(os.path.join(d, "production_backtest_trades_production_universe.csv"), index=False)
            yahoo = pd.concat({t: pd.DataFrame({"Close": 1.0, "Stock Splits": 0.0}, index=idx) for t in ("BAD", "OK")}, axis=1)
            yahoo.loc[idx[30], ("BAD", "Stock Splits")] = 0.1
            with mock.patch("yfinance.download", return_value=yahoo):
                out = split_exposure(d, hist)
        self.assertEqual((out["tickers_with_unadjusted_split"], out["before_entry"], out["during_hold"]), (1, 1, 1))
        self.assertEqual({h["fill_date"]: h["when"] for h in out["affected"]},
                         {str(idx[36].date()): "before_entry", str(idx[21].date()): "during_hold"})
        self.assertEqual(out["mean_beta_adjusted_pct"], {"all": -0.125, "without_affected": 0.75, "affected": -1.0})


if __name__ == "__main__":
    unittest.main()
