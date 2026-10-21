"""Price cache: tickers with a new dividend or split are re-downloaded in full (CacheManager.refresh_with_actions)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.cache_manager import CacheManager

DAYS = pd.bdate_range("2026-09-01", periods=31)          # d0 .. d30; tonight's session is d30


def _yahoo(truth, tickers, start, end, actions):
    """What yf.download(group_by='ticker', auto_adjust=True) returns for the given window."""
    parts = {}
    for t in tickers:
        df = truth.get(t)
        if df is None:
            continue
        df = df[(df.index >= pd.Timestamp(start)) & (df.index < pd.Timestamp(end))]
        parts[t] = df if actions else df.drop(columns=["Dividends", "Stock Splits"])
    return pd.concat(parts, axis=1) if parts else pd.DataFrame()


def _bars(close, days=DAYS, split_on=None, dividend_on=None):
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000.0,
                       "Dividends": 0.0, "Stock Splits": 0.0}, index=days)
    if split_on is not None:
        df.loc[split_on, "Stock Splits"] = 2.0
    if dividend_on is not None:
        df.loc[dividend_on, "Dividends"] = 1.0
    return df


class TestRefetchAfterActions(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cm = CacheManager(cache_dir=self.dir.name)
        yesterday = {"AAA": _bars(100.0, DAYS[:30]), "BBB": _bars(100.0, DAYS[:30])}
        self.cm.ingest_dataframe_to_cache(_yahoo(yesterday, ["AAA", "BBB"], DAYS[0], DAYS[30], False))
        # Tonight BBB splits 2:1 on d30: Yahoo re-adjusts its whole history to the new basis
        self.truth = {"AAA": _bars(100.0), "BBB": _bars(50.0, split_on=DAYS[30])}

    def tearDown(self):
        self.dir.cleanup()

    def _refresh(self, truth=None, since=DAYS[29].date()):
        truth = truth or self.truth
        with mock.patch.object(self.cm, "download_batch_with_retry",
                               side_effect=lambda t, s, e, actions=False: _yahoo(truth, t, s, e, actions)), \
                mock.patch("time.sleep"):
            return self.cm.refresh_with_actions(["AAA", "BBB"], (DAYS[29] - pd.Timedelta(days=7)).date().isoformat(),
                                                DAYS[30].date().isoformat(), DAYS[0].date().isoformat(), since=since)

    def _closes(self, t):
        return self.cm.get_ticker_history(t, DAYS[0].date().isoformat(), DAYS[30].date().isoformat())["CLOSE"]

    def test_split_replaces_the_whole_history(self):
        stats = self._refresh()
        self.assertEqual((stats["with_actions"], stats["refetched"], stats["pending"]), (1, 1, []))
        self.assertEqual(set(self._closes("BBB")), {50.0})              # one basis: no -50% step
        self.assertEqual(len(self._closes("BBB")), 31)
        self.assertEqual(set(self._closes("AAA")), {100.0})
        self.assertEqual(len(self._closes("AAA")), 31)                  # tonight's bar added
        self.assertEqual(self.cm.pending_refetch(), set())

    def test_failed_refetch_is_pending_and_retried(self):
        broken = dict(self.truth)
        with mock.patch.object(self.cm, "download_batch_with_retry",
                               side_effect=lambda t, s, e, actions=False:
                               _yahoo(self.truth, t, s, e, actions) if actions else pd.DataFrame()), \
                mock.patch("time.sleep"):
            stats = self.cm.refresh_with_actions(["AAA", "BBB"], (DAYS[29] - pd.Timedelta(days=7)).date().isoformat(),
                                                 DAYS[30].date().isoformat(), DAYS[0].date().isoformat(), since=DAYS[29].date())
        self.assertEqual(stats["pending"], ["BBB"])
        self.assertEqual(self.cm.pending_refetch(), {"BBB"})
        self.assertEqual(set(self._closes("BBB")), {100.0, 50.0})       # the overlap alone leaves a step
        stats = self._refresh(broken, since=DAYS[30].date())             # next run: no new action, retried
        self.assertEqual((stats["refetched"], stats["pending"]), (1, []))
        self.assertEqual(set(self._closes("BBB")), {50.0})

    def test_short_refetch_never_replaces_history(self):
        short = dict(self.truth, BBB=self.truth["BBB"].iloc[-10:])
        stats = self._refresh(short)
        self.assertEqual(stats["pending"], ["BBB"])
        self.assertEqual(len(self._closes("BBB")), 31)                   # old rows kept, tonight's added

    def test_action_already_handled_is_not_refetched(self):
        truth = {"AAA": _bars(100.0, dividend_on=DAYS[27]), "BBB": _bars(100.0)}
        stats = self._refresh(truth)                                     # dividend before `since`
        self.assertEqual((stats["with_actions"], stats["refetched"]), (0, 0))

    def test_one_pass_over_the_daily_files(self):
        truth = dict(self.truth, AAA=_bars(100.0, dividend_on=DAYS[30]))   # two refetches, one chunk each
        with mock.patch.object(self.cm, "download_batch_with_retry",
                               side_effect=lambda t, s, e, actions=False: _yahoo(truth, t, s, e, actions)), \
                mock.patch.object(self.cm, "replace_ticker_history", wraps=self.cm.replace_ticker_history) as rep, \
                mock.patch("time.sleep"):
            stats = self.cm.refresh_with_actions(["AAA", "BBB"], (DAYS[29] - pd.Timedelta(days=7)).date().isoformat(),
                                                 DAYS[30].date().isoformat(), DAYS[0].date().isoformat(),
                                                 since=DAYS[29].date(), batch_size=1)
        rep.assert_called_once()
        self.assertEqual((stats["refetched"], stats["pending"]), (2, []))

    def test_lifecycle_skips_pending_tickers(self):
        import jobs.generate_signals as gs
        sb = mock.MagicMock()
        sb.table().select().in_().execute.return_value.data = [
            {"id": "s1", "ticker": "BBB", "status": "open", "entry_price": 100.0, "stop_loss": 92.0, "price": 100.0,
             "target_1": 110.0, "strategy": "Trend Following", "scan_date": "2026-09-01"}]
        fake_cm = mock.Mock()
        fake_cm.pending_refetch.return_value = {"BBB"}
        with mock.patch("src.data.cache_manager.get_cache_manager", return_value=fake_cm), \
                mock.patch("jobs.supabase_client.get_bars_after") as bars:
            gs.reconcile_recommendation_lifecycle(supabase=sb, qualified_tickers=set(), scan_successful=True,
                                                  scanned_count=500, min_required_scanned=50)
        bars.assert_not_called()


if __name__ == "__main__":
    unittest.main()
