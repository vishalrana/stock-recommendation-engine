"""update_live_prices never stores a bar for a session that has not closed (a mid-session run)."""

import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.swing.live as live


def frame(days):
    idx = pd.DatetimeIndex(pd.to_datetime(days))
    return pd.DataFrame({"OPEN": 10.0, "HIGH": 11.0, "LOW": 9.0, "CLOSE": 10.0, "VOLUME": 1e6,
                         "DIVIDEND": 0.0, "SPLIT": 0.0}, index=idx)


class TestCompletedBarsOnly(unittest.TestCase):
    def test_partial_bar_dropped_and_healed(self):
        with tempfile.TemporaryDirectory() as d:
            # Cache from an earlier mid-session run holds the still-forming 2026-10-14 bar
            frame(["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]).to_parquet(os.path.join(d, "AAA.parquet"))
            fresh = frame(["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"])
            with mock.patch.object(live, "latest_completed_session", return_value=date(2026, 10, 13)), \
                    mock.patch("scripts.build_long_history.download_chunk", return_value={"AAA": fresh}), \
                    mock.patch.object(live.time, "sleep"):
                stats = live.update_live_prices(["AAA"], price_dir=d)
            got = pd.read_parquet(os.path.join(d, "AAA.parquet"))
            self.assertEqual(stats["incremental"], 1)
            self.assertEqual(got.index[-1], pd.Timestamp("2026-10-13"))
            self.assertNotIn(pd.Timestamp("2026-10-14"), got.index)


    def test_fresh_share_gate(self):
        s = date(2026, 10, 13)
        frames = {f"T{i}": frame(["2026-10-12", "2026-10-13"]) for i in range(90)}
        frames.update({f"L{i}": frame(["2026-10-09", "2026-10-12"]) for i in range(10)})   # bar posted late
        frames["DEAD"] = frame(["2026-01-05"])                                             # delisted: not counted
        pct, n = live.fresh_share(frames, s)
        self.assertEqual((round(pct, 1), n), (90.0, 100))
        self.assertLess(pct, live.MIN_FRESH_PCT)


if __name__ == "__main__":
    unittest.main()
