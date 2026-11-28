"""Swing record integrity (2026-11-28): re-entry while open, the processed-session guard, rebased
levels, late SELLs, idempotent ledger writes, the consistency check and the correction plan."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs.generate_swing_signals import (blocked_until, ledger_key, processed_sessions, scan_window, sessions_after,
                                         simulate_row)
from scripts.check_swing_ledger import differences, effective, expected
from scripts.correct_swing_records import first_run_end, ledger_redates, plan, replay_book
from scripts.merge_ledger import merge
from src.swing.engine import g0_book_state
from src.swing.rules import check_g0
from src.trade_hygiene import SessionCalendar

CAL = SessionCalendar()


class TestReentry(unittest.TestCase):
    def test_an_open_signal_with_a_recent_close_gives_no_new_buy(self):
        # 2026-11-02 on: a pair with an open signal and a closed one in the last 30 days was re-issued nightly
        busy = blocked_until({("T", "GSL")}, [{"sleeve": "T", "ticker": "GSL", "exit_date": "2026-10-15"}], CAL)
        self.assertEqual(busy["T"]["GSL"], date.max)
        d = date(2026, 11, 10)
        open_keys, cooldowns = g0_book_state(busy["T"], "T", "GSL", d)
        ok, why = check_g0("GSL", "T", d, True, False, open_keys, cooldowns, [], CAL)
        self.assertFalse(ok)
        self.assertEqual(why, "Open signal already active")

    def test_the_latest_exit_sets_the_cooldown(self):
        closes = [{"sleeve": "P", "ticker": "HUM", "exit_date": "2026-11-25"}, {"sleeve": "P", "ticker": "HUM", "exit_date": "2026-10-30"}]
        busy = blocked_until(set(), closes, CAL)
        self.assertEqual(busy["P"]["HUM"], CAL.shift(date(2026, 11, 25), 11))   # not the last row's


class TestProcessedGuard(unittest.TestCase):
    def rows(self, **processed):
        return {sl: {"details": {"last_session": "2026-11-25", "stale": False,
                                 **({"processed_session": processed[sl]} if sl in processed else {})}}
                for sl in ("T", "E", "N", "P", "I")}

    def test_a_rerun_of_a_processed_session_issues_nothing_but_a_skipped_sleeve_catches_up(self):
        last = date(2026, 11, 25)                                   # Thanksgiving: no new session on 11-27
        job_last, own = processed_sessions(self.rows(T="2026-11-25", I="2026-11-24"))
        self.assertIsNone(scan_window(own["T"], job_last, last, CAL))             # T scanned 11-25 already
        self.assertIsNone(scan_window(own["E"], job_last, last, CAL))             # rows before the marker: the job's session
        self.assertEqual(scan_window(own["I"], job_last, last, CAL), (last, True))    # I was skipped: runs once, late

    def test_a_new_session_runs_normally_and_a_lagging_sleeve_catches_up_from_its_own_session(self):
        job_last, own = processed_sessions(self.rows(I="2026-11-20"))
        self.assertEqual(scan_window(own["T"], job_last, date(2026, 11, 27), CAL), (date(2026, 11, 27), False))
        self.assertEqual(scan_window(own["I"], job_last, date(2026, 11, 27), CAL), (date(2026, 11, 23), False))
        self.assertEqual(scan_window(date(2026, 10, 1), job_last, date(2026, 11, 27), CAL)[0],
                         CAL.shift(date(2026, 11, 27), -5))           # at most 5 sessions back


def asset(lows, closes):
    idx = pd.bdate_range("2026-11-02", periods=len(closes))
    c = np.array(closes, float)
    a = SimpleNamespace(index=idx, c=c, reactions=[])
    a.pos = lambda d: int(idx.get_loc(pd.Timestamp(d))) if pd.Timestamp(d) in idx else None
    a.locked_array = lambda: np.zeros(len(c), bool)
    a.bars = lambda: pd.DataFrame({"TR_OPEN": c, "TR_HIGH": c * 1.01, "TR_LOW": np.array(lows, float), "TR_CLOSE": c,
                                   "SMA5": c}, index=idx)
    spy = {d.date(): 400.0 for d in idx}                     # SwingContext holds SPY as {date: price}
    return a, SimpleNamespace(spy_open=spy, spy_close=spy), idx


class TestRebase(unittest.TestCase):
    def test_a_refetched_history_no_longer_stops_out_on_the_old_basis(self):
        # Issued at 100 with stop 97; a later dividend re-adjusts the history by 0.97, so the old-basis stop sits
        # at the bars' entry level. The rebased stop (94.09) does not trigger.
        closes = [100 * 0.97] * 8
        a, ctx, idx = asset([c * 0.995 for c in closes], closes)
        row = {"ticker": "PSTL", "sleeve": "T", "signal_date": str(idx[0].date()), "stop_loss": 97.0, "target": 130.0,
               "entry_signal_close": 100.0, "beta": 1.0}
        tr, ratio = simulate_row(row, a, ctx)
        self.assertAlmostEqual(ratio, 0.97)
        self.assertTrue(tr is None or tr.exit_date is None)           # still open (None: no exit before the data ends)
        tr0, _ = simulate_row(dict(row, entry_signal_close=97.0), a, ctx)   # no rebase: the false stop
        self.assertEqual(tr0.exit_reason, "stop")

    def test_late_sells_count_sessions(self):
        self.assertEqual(sessions_after(date(2026, 10, 19), date(2026, 11, 13), CAL), 19)
        self.assertEqual(sessions_after(date(2026, 11, 25), date(2026, 11, 25), CAL), 0)


class TestLedger(unittest.TestCase):
    def test_merge_writes_each_event_once(self):
        buy = {"timestamp": "2026-11-27T10:08:13+00:00", "session_date": "2026-11-25", "action": "BUY", "ticker": "COHR",
               "sleeve": "T", "signal_date": "2026-11-25", "price": 1.0}
        with tempfile.TemporaryDirectory() as d:
            new, branch = os.path.join(d, "new"), os.path.join(d, "branch")
            os.makedirs(new)
            os.makedirs(branch)
            with open(os.path.join(branch, "2026-11-25.jsonl"), "w") as f:     # an older line: no signal_date
                f.write(json.dumps(dict(buy, timestamp="2026-11-26T09:58:00+00:00", signal_date=None)) + "\n")
            with open(os.path.join(new, "2026-11-25.jsonl"), "w") as f:
                f.write(json.dumps(buy) + "\n" + json.dumps(buy) + "\n" + json.dumps(dict(buy, ticker="FISI")) + "\n")
            self.assertEqual(merge(new, branch), {"appended": 1, "skipped": 2})
            self.assertEqual(merge(new, branch), {"appended": 0, "skipped": 3})      # idempotent
        self.assertEqual(ledger_key("2026-11-20", "SELL", "T", "GSL"), ("2026-11-20", "SELL", "T", "GSL", None))

    def test_consistency_counts_corrections(self):
        sigs = [{"sleeve": "T", "ticker": "A", "signal_date": "2026-11-02", "status": "closed", "exit_date": "2026-11-05"},
                {"sleeve": "T", "ticker": "A", "signal_date": "2026-11-03", "status": "void", "exit_date": "2026-11-06"},
                {"sleeve": "T", "ticker": "B", "signal_date": "2026-11-02", "status": "open", "exit_date": None}]
        ev = [{"action": "BUY", "sleeve": "T", "ticker": "A", "session_date": "2026-11-02"},
              {"action": "BUY", "sleeve": "T", "ticker": "A", "session_date": "2026-11-03"},
              {"action": "SELL", "sleeve": "T", "ticker": "A", "session_date": "2026-11-04"},
              {"action": "SELL", "sleeve": "T", "ticker": "A", "session_date": "2026-11-06"},
              {"action": "BUY", "sleeve": "T", "ticker": "B", "session_date": "2026-11-02"},
              {"action": "BUY", "sleeve": "T", "ticker": "B", "session_date": "2026-11-02"},
              {"action": "SELL", "sleeve": "T", "ticker": "B", "session_date": "2026-11-04"}]
        self.assertEqual(len(differences(expected(sigs), effective(ev))), 6)
        fixes = [{"action": "CORRECTION", "change": "void", "target": {"sleeve": "T", "ticker": "A", "signal_date": "2026-11-03", "exit_session": "2026-11-06"}},
                 {"action": "CORRECTION", "change": "exit_changed", "target": {"sleeve": "T", "ticker": "A", "signal_date": "2026-11-02", "exit_session": "2026-11-04"},
                  "new": {"exit_session": "2026-11-05"}},
                 {"action": "CORRECTION", "change": "duplicate", "target": {"sleeve": "T", "ticker": "B", "action": "BUY", "session_date": "2026-11-02", "extra": 1}},
                 {"action": "CORRECTION", "change": "reopen", "target": {"sleeve": "T", "ticker": "B", "signal_date": "2026-11-02", "exit_session": "2026-11-04"}}]
        self.assertEqual(differences(expected(sigs), effective(ev + fixes)), [])


def sig(i, signal_date, status="open", exit_date=None, reason=None, ticker="GSL"):
    return {"id": i, "sleeve": "T", "ticker": ticker, "signal_date": signal_date, "status": status, "exit_date": exit_date,
            "exit_reason": reason, "exit_price": 1.0, "entry_signal_close": 1.0, "created_at": signal_date}


class TestPlan(unittest.TestCase):
    def test_book_replay_voids_reentries_and_reopens_a_false_stop(self):
        rows = [sig(1, "2026-10-15", "closed", "2026-11-05", "stop"),      # the false stop, logged 11-21
                sig(2, "2026-11-10"), sig(3, "2026-11-11", "closed", "2026-11-20", "stop"),
                sig(4, "2026-10-01", "closed", "2026-10-02", "target", ticker="KDP"), sig(5, "2026-10-08", ticker="KDP"),
                sig(6, "2026-10-20", ticker="KDP")]
        out = replay_book(rows, {1: {"open": True, "ratio": 0.98}}, CAL)
        ch = {c["id"]: c["change"] for c in out["corrections"]}
        # 2 and 3 were issued while 1 (reopened) is open; 5 inside KDP's cooldown; 6 after it ends (10-19)
        self.assertEqual(ch, {1: "reopen", 2: "void", 3: "void", 5: "void"})
        self.assertIn("cooldown", next(c["reason"] for c in out["corrections"] if c["id"] == 5))
        self.assertEqual(next(c for c in out["corrections"] if c["id"] == 3)["target"]["exit_session"], "2026-11-20")

    def test_a_kept_signal_after_the_cooldown_stays(self):
        rows = [sig(4, "2026-10-01", "closed", "2026-10-02", "target", ticker="KDP"), sig(6, "2026-10-20", ticker="KDP")]
        self.assertEqual(replay_book(rows, {}, CAL)["corrections"], [])

    def test_late_buys_are_those_of_a_later_run(self):
        ev = [{"action": "BUY", "session_date": "2026-11-25", "timestamp": t} for t in
              ("2026-11-26T09:57:00+00:00", "2026-11-26T10:02:00+00:00", "2026-11-27T10:08:13+00:00")]
        self.assertEqual(first_run_end(ev)["2026-11-25"].isoformat(), "2026-11-26T10:02:00+00:00")

    def test_redating_matches_one_line_per_row(self):
        ev = [{"action": "SELL", "sleeve": "T", "ticker": "GSL", "session_date": d, "timestamp": t, "price": p}
              for d, t, p in (("2026-11-05", "2026-11-21T08:49:01+00:00", 38.3584), ("2026-11-19", "2026-11-21T08:50:47+00:00", 39.6519),
                              ("2026-11-20", "2026-11-21T08:50:41+00:00", 38.909))]
        led = [{"id": i, "action": "SELL", "sleeve": "T", "ticker": "GSL", "session_date": "2026-11-20", "created_at": t, "price": p}
               for i, t, p in ((1, "2026-11-21T08:48:36+00:00", 38.35840), (2, "2026-11-21T08:48:36+00:00", 39.65190),
                               (3, "2026-11-21T08:48:36+00:00", 38.90900))]
        self.assertEqual({x["ledger_id"]: x["session_date"] for x in ledger_redates(led, ev)}, {1: "2026-11-05", 2: "2026-11-19"})

    def test_plan_counts_before_and_after(self):
        rows = [sig(1, "2026-11-02"), sig(2, "2026-11-03"), sig(3, "2026-11-04", ticker="ZZZ")]
        ev = [{"action": "BUY", "sleeve": "T", "ticker": r["ticker"], "session_date": r["signal_date"],
               "timestamp": r["signal_date"] + "T09:00:00+00:00", "price": 1.0} for r in rows]
        doc = plan(rows, {}, CAL, ev, [], "1")
        self.assertEqual(doc["counts"]["before"]["pairs_with_several_open"], 1)
        self.assertEqual(doc["counts"]["after"]["pairs_with_several_open"], 0)
        self.assertEqual(doc["counts"]["changes"], {"void": 1})


if __name__ == "__main__":
    unittest.main()
