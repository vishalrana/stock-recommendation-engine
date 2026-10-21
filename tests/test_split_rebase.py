"""Open ideas across splits and re-adjusted history: stored levels are rebased to the bars' basis."""

import os
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.outcome.outcome_calculator import rebase_levels

SCAN = "2026-09-01"
IDEA = {"id": "s1", "ticker": "ABC", "status": "open", "entry_price": 100.0, "stop_loss": 92.0, "price": 100.0,
        "target_1": 115.0, "target_2": 125.0, "target_3": 135.0, "strategy": "Pullback Recovery", "scan_date": SCAN}
# (open, high, low, close); the first row is the scan date. 10 bars after it = Pullback's holding period.
EXPIRES = [(99.0, 101.0, 98.0, 100.0), (101.0, 104.5, 101.5, 103.0)] + [(103.0, 104.0, 102.0, 103.5)] * 8 \
    + [(103.5, 105.0, 103.0, 104.0)]
HITS_T1 = [(99.0, 101.0, 98.0, 100.0), (101.0, 104.5, 101.5, 103.0), (103.0, 108.0, 102.5, 107.0),
           (107.0, 116.0, 106.0, 115.5), (115.0, 117.0, 112.0, 114.0)]
HITS_T3 = [(99.0, 101.0, 98.0, 100.0), (101.0, 104.0, 100.0, 103.0), (103.0, 116.0, 102.0, 115.0),
           (115.0, 126.0, 114.0, 125.0), (125.0, 136.0, 124.0, 135.0)]


def _frame(rows, factor=1.0, ex_index=None):
    """Bars from the scan date. factor re-adjusts history: every bar for a split (ex_index None),
    or only the bars before ex_index for a dividend."""
    idx = pd.bdate_range(SCAN, periods=len(rows))
    out = []
    for i, r in enumerate(rows):
        f = factor if (ex_index is None or i < ex_index) else 1.0
        out.append({"OPEN": r[0] * f, "HIGH": r[1] * f, "LOW": r[2] * f, "CLOSE": r[3] * f})
    return pd.DataFrame(out, index=idx)


def _lifecycle(frame):
    """Run the lifecycle on one open idea; returns (closing call kwargs or None, open-state fields or None)."""
    import jobs.generate_signals as gs
    sb = mock.MagicMock()
    sb.table().select().in_().execute.return_value.data = [dict(IDEA)]
    with mock.patch("jobs.supabase_client.get_bars_after", return_value=frame), \
            mock.patch("jobs.supabase_client.update_signals_status") as status, \
            mock.patch("jobs.supabase_client.update_history_outcome") as hist, \
            mock.patch("jobs.supabase_client.update_signals_price"), \
            mock.patch.object(gs, "_safe_signal_update") as upd:
        gs.reconcile_recommendation_lifecycle(supabase=sb, qualified_tickers=set(), scan_successful=True,
                                              scanned_count=500, min_required_scanned=50)
    closed = dict(hist.call_args.kwargs, outcome=hist.call_args.args[1], exit_price=status.call_args.args[2]) if hist.called else None
    return closed, (upd.call_args.args[2] if upd.called else None)


class TestRebaseLevels(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(rebase_levels((92.0, None), 100.0, 100.05), ([92.0, None], 1.0))   # within 0.1%
        self.assertEqual(rebase_levels((92.0, None), 100.0, 50.0), ([46.0, None], 0.5))
        self.assertEqual(rebase_levels((92.0,), None, 50.0), ([92.0], 1.0))                 # unusable close


class TestLifecycleAcrossAdjustments(unittest.TestCase):
    def test_split_during_the_hold_changes_no_outcome(self):
        base, _ = _lifecycle(_frame(EXPIRES))
        split, _ = _lifecycle(_frame(EXPIRES, 0.5))                 # 2:1 split: Yahoo halves every bar
        self.assertEqual(base["outcome"], "expired")
        for k in ("outcome", "outcome_date", "holding_days"):
            self.assertEqual(split[k], base[k], k)
        self.assertAlmostEqual(split["return_pct"], base["return_pct"], places=9)
        self.assertAlmostEqual(split["exit_price"], base["exit_price"] * 0.5)

    def test_dividend_readjustment_changes_no_outcome(self):
        base, _ = _lifecycle(_frame(EXPIRES))
        div, _ = _lifecycle(_frame(EXPIRES, 0.99, ex_index=6))      # 1% dividend, ex-date on bar 6
        for k in ("outcome", "outcome_date", "holding_days"):
            self.assertEqual(div[k], base[k], k)

    def test_cache_refresh_never_changes_an_open_ideas_past_path(self):
        _, before = _lifecycle(_frame(HITS_T1))
        self.assertEqual(before["position_state"], "hit_t1")
        for factor, ex in ((0.5, None), (0.99, 3)):
            _, after = _lifecycle(_frame(HITS_T1, factor, ex))
            for k in ("status", "entry_date", "position_state"):
                self.assertEqual(after[k], before[k], (factor, k))
            ratio = factor
            self.assertAlmostEqual(after["current_stop"], before["current_stop"] * ratio, places=6)


class TestValidatorAcrossSplits(unittest.TestCase):
    def test_split_changes_no_outcome(self):
        import jobs.validate_ranking as vr
        row = {"ticker": "ABC", "scan_date": SCAN, "entry_price": 100.0, "stop_loss": 92.0,
               "target_1": 115.0, "target_2": 125.0, "target_3": 135.0}
        res = {}
        for factor in (1.0, 0.5):
            df = _frame(HITS_T3, factor).rename(columns=str.title)
            with mock.patch("jobs.validate_ranking.yf.download", return_value=df):
                res[factor] = vr.evaluate_signal(dict(row))
        self.assertNotEqual(res[1.0]["outcome"], "stopped")        # unrebased, the halved path reads as stopped
        self.assertEqual(res[0.5]["outcome"], res[1.0]["outcome"])
        self.assertEqual(res[0.5]["outcome_date"], res[1.0]["outcome_date"])
        self.assertAlmostEqual(res[0.5]["outcome_return_pct"], res[1.0]["outcome_return_pct"], places=6)


if __name__ == "__main__":
    unittest.main()
