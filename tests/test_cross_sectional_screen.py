"""Cross-Sectional Momentum screen: the live scan and the backtest rank the same stocks the same way."""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs.generate_signals import run_cross_sectional_screen
from src.pipeline_steps import cross_sectional_screen


class _Cache:
    def __init__(self, closes):
        self.closes = closes

    def get_ticker_history(self, ticker, start, end):
        s = self.closes[ticker].dropna()
        s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
        return pd.DataFrame({"CLOSE": s})


class TestCrossSectionalScreen(unittest.TestCase):
    def setUp(self):
        idx = pd.bdate_range("2025-01-02", periods=400)
        rng = np.random.default_rng(7)
        closes = {f"T{i:02d}": 100 * np.exp(np.cumsum(rng.normal(0.0005 * i, 0.02, len(idx)))) for i in range(30)}
        self.closes = pd.DataFrame(closes, index=idx)
        # A recent listing: too short for the backtest to scan, long enough to be ranked (it moves the cutoff)
        self.closes.loc[: idx[-90], "NEW"] = np.nan
        self.closes.loc[idx[-90]:, "NEW"] = np.geomspace(1, 1e4, 90)
        self.closes.loc[idx[-63], "GAP"] = np.nan   # missing close 63 sessions back: not ranked that day
        self.closes["GAP"] = self.closes["GAP"].fillna(self.closes["T00"])
        self.closes.loc[idx[-64], "GAP"] = np.nan

    def test_live_matches_backtest_on_every_session(self):
        full = cross_sectional_screen(self.closes)       # the backtest: one pass over the whole history
        cache = _Cache(self.closes)
        for d in self.closes.index[-30:]:
            live = run_cross_sectional_screen(list(self.closes.columns), cache, as_of_date=d.date().isoformat())
            self.assertEqual([t for t, _ in live], [t for t, _ in full[d]], d)
            np.testing.assert_allclose([r for _, r in live], [r for _, r in full[d]])

    def test_definition(self):
        d = self.closes.index[-1]
        top = cross_sectional_screen(self.closes)[d]
        ret = (self.closes.iloc[-1] / self.closes.iloc[-64] - 1).dropna()
        self.assertEqual(len(top), int(len(ret) * 0.15))
        self.assertEqual([t for t, _ in top], list(ret.sort_values(ascending=False).index[: len(top)]))
        self.assertIn("NEW", [t for t, _ in top])        # ranked (and first) although it has only 90 bars
        self.assertNotIn("GAP", [t for t, _ in cross_sectional_screen(self.closes)[self.closes.index[-1]]])

    def test_ranking_matches_the_screen(self):
        from src.pipeline_steps import cross_sectional_ranking
        d = self.closes.index[-1]
        ranking = cross_sectional_ranking(self.closes, d)
        top = cross_sectional_screen(self.closes)[d]
        self.assertEqual(list(ranking.ticker[: len(top)]), [t for t, _ in top])
        self.assertEqual(list(ranking["rank"]), list(range(1, len(ranking) + 1)))

    def test_the_nightly_saves_the_ranking_its_screen_used(self):
        import json
        import tempfile
        from unittest import mock
        import jobs.generate_signals as gs
        d = self.closes.index[-1]
        ranking = {}
        top = run_cross_sectional_screen(list(self.closes.columns), _Cache(self.closes), as_of_date=d.date().isoformat(),
                                         ranking=ranking)
        self.assertEqual((ranking["session"], ranking["cutoff"]), (d.date().isoformat(), len(top)))
        self.assertEqual(ranking["ranked"], len(ranking["rows"]))
        self.assertEqual([r[0] for r in ranking["rows"][: len(top)]], [t for t, _ in top])   # the screen's own order
        self.assertNotIn("GAP", [r[0] for r in ranking["rows"]])                             # no 63-session return
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(gs, "EXCLUSIONS_PATH", os.path.join(tmp, "x.json")):
            gs.write_exclusions(d, [], [], [], ranking)
            doc = json.load(open(gs.EXCLUSIONS_PATH, encoding="utf-8"))
        self.assertEqual(doc["cs_ranking"]["rows"][0], ranking["rows"][0])

    def test_ties_by_ticker(self):
        idx = pd.bdate_range("2026-01-02", periods=70)
        c = pd.DataFrame({t: np.r_[np.full(69, 100.0), 120.0] for t in ("ZZ", "AA", "MM")}, index=idx)
        c["LOW"] = 100.0
        for t in "BCDEFGH":
            c[t] = 100.0
        top = cross_sectional_screen(c)[idx[-1]]
        self.assertEqual([t for t, _ in top], ["AA"])
        self.assertAlmostEqual(top[0][1], 20.0)


if __name__ == "__main__":
    unittest.main()
