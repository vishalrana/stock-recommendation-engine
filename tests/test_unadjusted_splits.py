"""Splits Yahoo leaves unadjusted (src/data/splits.py): live cache flags, harness truncation, lifecycle."""

import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.cache_manager import CacheManager
from src.data.splits import unadjusted_split

DAYS = pd.bdate_range("2026-08-03", periods=31)
SPLIT = DAYS[20]


def _closes(before, after, jump_at=SPLIT):
    return pd.Series([before if d < jump_at else after for d in DAYS], index=DAYS)


class TestCheck(unittest.TestCase):
    def test_rule(self):
        self.assertTrue(unadjusted_split(_closes(4.2, 12.6), SPLIT, 1 / 3))      # IGR: 1-for-3 left unadjusted
        self.assertFalse(unadjusted_split(_closes(12.6, 12.6), SPLIT, 1 / 3))    # adjusted history
        self.assertTrue(unadjusted_split(_closes(100.0, 50.0), SPLIT, 2.0))      # 2-for-1 left unadjusted
        self.assertTrue(unadjusted_split(_closes(100.0, 50.0, DAYS[21]), SPLIT, 2.0))   # Yahoo's date off by one
        self.assertFalse(unadjusted_split(_closes(100.0, 97.0), SPLIT, 2.0))     # an ordinary -3% day
        self.assertFalse(unadjusted_split(_closes(100.0, 50.0), SPLIT, 1.0))
        # Stock dividends listed as splits (SCCO 1.008, SNFCA 1.05): their move is an ordinary day's
        self.assertFalse(unadjusted_split(_closes(100.0, 99.0), SPLIT, 1.008))
        self.assertFalse(unadjusted_split(_closes(100.0, 95.0), SPLIT, 1.05))
        self.assertTrue(unadjusted_split(_closes(100.0, 80.0), SPLIT, 1.25))      # 5-for-4: checked
        self.assertTrue(unadjusted_split(_closes(10.0, 100.0), SPLIT, 0.1))       # KNDI-like 1-for-10


# Yahoo's listed date after the jump: (listed date, ratio, close-to-close log moves around it), from the
# harness's own call (yf.download period=5y, actions=True) on 2026-10-25, rounded to 0.01
YAHOO_LATE = {
    "DHY": ("2026-09-30", 0.1, [("2026-09-24", -0.01), ("2026-09-25", 0.0), ("2026-09-28", -0.01), ("2026-09-29", 2.30),
                                ("2026-09-30", -0.01), ("2026-10-01", 0.0), ("2026-10-02", 0.0)]),
    "BTOC": ("2026-10-15", 0.0625, [("2026-10-09", -2.78), ("2026-10-12", -0.08), ("2026-10-13", -0.10), ("2026-10-14", 2.78),
                                    ("2026-10-15", 0.05), ("2026-10-16", -0.07), ("2026-10-19", -0.01)]),
    "REVB": ("2026-10-20", 1 / 7, [("2026-10-14", -0.01), ("2026-10-15", 2.00), ("2026-10-16", -2.40), ("2026-10-19", 1.91),
                                   ("2026-10-20", 0.13), ("2026-10-21", -0.04), ("2026-10-22", 0.02)]),
    "MBRX": ("2026-10-20", 0.05, [("2026-10-14", -0.06), ("2026-10-15", 2.89), ("2026-10-16", -3.27), ("2026-10-19", 2.94),
                                  ("2026-10-20", 0.16), ("2026-10-21", 0.02), ("2026-10-22", -0.07)]),
    "SKIN": ("2026-10-14", 0.05, [("2026-10-08", 2.96), ("2026-10-09", -3.03), ("2026-10-12", 2.95), ("2026-10-13", 0.03),
                                  ("2026-10-14", 0.07), ("2026-10-15", 0.09), ("2026-10-16", -0.18)]),
}


def _from_moves(moves, start=10.0):
    days = [pd.Timestamp(d) for d, _ in moves]
    closes = [start]
    for _, m in moves:
        closes.append(closes[-1] * math.exp(m))
    return pd.Series(closes, index=[days[0] - pd.offsets.BDay(1)] + days)


class TestYahooLateDates(unittest.TestCase):
    def test_a_jump_one_or_two_sessions_before_the_listed_date_is_flagged(self):
        from src.data.splits import flagged_split_dates, unadjusted_move
        expect = {"DHY": ("2026-09-29", -1), "BTOC": ("2026-10-14", -1), "REVB": ("2026-10-19", -1),
                  "MBRX": ("2026-10-19", -1), "SKIN": ("2026-10-12", -2)}
        for t, (listed, ratio, moves) in YAHOO_LATE.items():
            m = unadjusted_move(_from_moves(moves), listed, ratio)
            self.assertIsNotNone(m, t)
            self.assertEqual((str(m["session"])[:10], m["offset"]), expect[t], t)
            self.assertLess(abs(m["move"] - m["target"]), 0.25 * abs(m["target"]), t)
            # the unadjusted prices start where the jump landed, not on Yahoo's date
            self.assertEqual(flagged_split_dates(_from_moves(moves), [(listed, ratio)]), [expect[t][0]], t)

    def test_an_ordinary_fall_two_sessions_before_a_5_for_4_is_not_a_split(self):
        self.assertFalse(unadjusted_split(_closes(100.0, 88.0, DAYS[18]), SPLIT, 1.25))   # -12%, two sessions before
        self.assertTrue(unadjusted_split(_closes(100.0, 80.0, DAYS[18]), SPLIT, 1.25))    # -20%: the unadjusted move
        self.assertTrue(unadjusted_split(_closes(100.0, 88.0), SPLIT, 1.25))              # on the listed date: today's rule


def _yahoo(frames, tickers, start, end, actions):
    parts = {}
    for t in tickers:
        df = frames.get(t)
        if df is None:
            continue
        df = df[(df.index >= pd.Timestamp(start)) & (df.index < pd.Timestamp(end))]
        parts[t] = df if actions else df.drop(columns=["Dividends", "Stock Splits"])
    return pd.concat(parts, axis=1) if parts else pd.DataFrame()


def _frame(closes, split_on=None, ratio=1 / 3):
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1000.0,
                       "Dividends": 0.0, "Stock Splits": 0.0}, index=closes.index)
    if split_on is not None:
        df.loc[split_on, "Stock Splits"] = ratio
    return df


class TestLiveCache(unittest.TestCase):
    def test_flag_refetch_and_clear(self):
        with tempfile.TemporaryDirectory() as d:
            cm = CacheManager(cache_dir=d)
            unadj = {"IGR": _frame(_closes(4.2, 12.6), SPLIT), "OK": _frame(_closes(50.0, 50.0))}
            # The rebuild (refresh_cache with actions) seeds the split events and the flags
            with mock.patch.object(cm, "download_batch_with_retry",
                                   side_effect=lambda t, s, e, actions=False: _yahoo(
                                       {k: v.iloc[:30] for k, v in unadj.items()}, t, s, e, actions)), mock.patch("time.sleep"):
                cm.refresh_cache(["IGR", "OK"], DAYS[0].date().isoformat(), DAYS[29].date().isoformat(), actions=True)
            self.assertEqual(cm.update_split_flags(), {"IGR": [SPLIT.date().isoformat()]})
            self.assertEqual(cm.split_flagged(), {"IGR"})

            def night(frames):
                with mock.patch.object(cm, "download_batch_with_retry",
                                       side_effect=lambda t, s, e, actions=False: _yahoo(frames, t, s, e, actions)), \
                        mock.patch("time.sleep"):
                    return cm.refresh_with_actions(["IGR", "OK"], DAYS[22].date().isoformat(), DAYS[30].date().isoformat(),
                                                   DAYS[0].date().isoformat(), since=DAYS[29].date())
            stats = night(unadj)                                           # still unadjusted: refetched, still flagged
            self.assertEqual(list(stats["split_flagged"]), ["IGR"])
            self.assertEqual(stats["refetched"], 1)
            adjusted = dict(unadj, IGR=_frame(_closes(12.6, 12.6), SPLIT))  # Yahoo fixes the history
            stats = night(adjusted)
            self.assertEqual(stats["split_flagged"], {})
            self.assertEqual(cm.split_flagged(), set())


class TestHarness(unittest.TestCase):
    def test_truncated_at_the_unadjusted_split(self):
        from scripts.validate_backtest_pipeline import truncate_unadjusted_splits
        bad = pd.DataFrame({"CLOSE": _closes(4.2, 12.6), "SPLIT": 0.0})
        bad.loc[SPLIT, "SPLIT"] = 1 / 3
        good = bad.assign(CLOSE=_closes(12.6, 12.6))
        out, cut = truncate_unadjusted_splits({"IGR": bad, "OK": good, "OLD": pd.DataFrame({"CLOSE": _closes(1.0, 1.0)})})
        self.assertEqual(cut, {"IGR": SPLIT.date().isoformat()})
        self.assertEqual(out["IGR"].index.max(), DAYS[19])               # no bar from the split on
        self.assertEqual(len(out["OK"]), 31)
        self.assertNotIn("SPLIT", out["OK"].columns)
        self.assertEqual(len(out["OLD"]), 31)                            # stored before split events: unchecked

    def test_truncated_where_an_early_jump_landed(self):
        from scripts.validate_backtest_pipeline import truncate_unadjusted_splits
        listed, ratio, moves = YAHOO_LATE["SKIN"]
        df = pd.DataFrame({"CLOSE": _from_moves(moves), "SPLIT": 0.0})
        df.loc[pd.Timestamp(listed), "SPLIT"] = ratio
        out, cut = truncate_unadjusted_splits({"SKIN": df})
        self.assertEqual(cut, {"SKIN": "2026-10-12"})
        self.assertEqual(out["SKIN"].index.max(), pd.Timestamp("2026-10-09"))           # the false jump is dropped too

    def test_a_split_listed_on_a_day_without_a_bar_is_checked(self):
        # GTBP, IGR, BRTX, BURU, SRTM, WCT on 2026-09-08: Yahoo's split row has no prices; the harness
        # dropped it with the event, so it ranked them on false gains while live excluded them
        import scripts.validate_backtest_pipeline as vb
        df = _frame(_closes(4.2, 12.6), SPLIT)
        df.loc[SPLIT, ["Open", "High", "Low", "Close", "Volume"]] = np.nan
        with tempfile.TemporaryDirectory() as d, mock.patch.object(vb, "BACKTEST_DATA_DIR", d),                 mock.patch("yfinance.download", return_value=pd.concat({"GTBP": df}, axis=1)):
            raw = vb.load_history(["GTBP"], refresh=True)
        self.assertNotIn(SPLIT, raw["GTBP"].index)
        self.assertEqual(raw["GTBP"].at[DAYS[21], "SPLIT"], 1 / 3)       # kept on the next bar
        _, cut = vb.truncate_unadjusted_splits(raw)
        self.assertEqual(cut, {"GTBP": DAYS[21].date().isoformat()})


class TestLifecycle(unittest.TestCase):
    def test_flagged_ticker_gets_no_lifecycle_step(self):
        import jobs.generate_signals as gs
        sb = mock.MagicMock()
        sb.table().select().in_().execute.return_value.data = [
            {"id": "s1", "ticker": "IGR", "status": "open", "entry_price": 10.0, "stop_loss": 9.0, "price": 10.0,
             "target_1": 11.0, "strategy": "Trend Following", "scan_date": "2026-09-01"}]
        fake = mock.Mock()
        fake.pending_refetch.return_value = set()
        fake.split_flagged.return_value = {"IGR"}
        with mock.patch("src.data.cache_manager.get_cache_manager", return_value=fake), \
                mock.patch("jobs.supabase_client.get_bars_after") as bars:
            gs.reconcile_recommendation_lifecycle(supabase=sb, qualified_tickers=set(), scan_successful=True,
                                                  scanned_count=500, min_required_scanned=50)
        bars.assert_not_called()


if __name__ == "__main__":
    unittest.main()
