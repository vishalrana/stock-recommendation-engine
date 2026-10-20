"""signals_history writes are idempotent per scan date + ticker + strategy (rerun of a scan date)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs.generate_signals import _replace_history_audit


class FakeTable:
    """The few PostgREST calls _replace_history_audit makes, over an in-memory list of rows."""

    def __init__(self, rows):
        self.rows = rows
        self._filters, self._delete = [], False

    def select(self, _cols):
        self._filters, self._delete = [], False
        return self

    def delete(self):
        self._filters, self._delete = [], True
        return self

    def eq(self, col, val):
        self._filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        self._filters.append(lambda r, c=col, v=set(vals): r.get(c) in v)
        return self

    def limit(self, _n):
        return self

    def execute(self):
        hit = [r for r in self.rows if all(f(r) for f in self._filters)]
        if self._delete:
            self.rows[:] = [r for r in self.rows if r not in hit]
        return type("R", (), {"data": [dict(r) for r in hit]})()


class FakeClient:
    def __init__(self, rows):
        self.t = FakeTable(rows)

    def table(self, name):
        assert name == "signals_history"
        return self.t


def row(ticker, strategy, outcome="rejected", scan_date="2026-10-12", run="r1"):
    return {"scan_date": scan_date, "ticker": ticker, "strategy": strategy, "outcome": outcome, "run": run}


class TestHistoryIdempotent(unittest.TestCase):
    def test_rerun_replaces_audit_rows_and_never_touches_published_ideas(self):
        db = [row("AAA", "Trend Following"), row("BBB", "Trend Following"), row("CCC", "Pullback Recovery"),
              row("OBE", "Cross-Sectional Momentum", outcome="open"),            # a published idea's row
              row("AAA", "Trend Following", scan_date="2026-10-13")]             # another scan date
        client = FakeClient(db)
        rerun = [row("AAA", "Trend Following", run="r2"), row("BBB", "Trend Following", run="r2"),
                 row("DDD", "Trend Following", run="r2"),
                 row("OBE", "Cross-Sectional Momentum", run="r2")]                 # same key as the published idea
        to_insert = _replace_history_audit(client, rerun)
        db.extend(to_insert)
        keys = [(r["scan_date"], r["ticker"], r["strategy"]) for r in db]
        self.assertEqual(len(keys), len(set(keys)), "one row per scan date + ticker + strategy")
        on_12 = {(r["ticker"], r["strategy"]): r for r in db if r["scan_date"] == "2026-10-12"}
        self.assertEqual(on_12[("AAA", "Trend Following")]["run"], "r2")          # latest run wins
        self.assertEqual(on_12[("CCC", "Pullback Recovery")]["run"], "r1")        # not in the rerun: kept
        self.assertEqual(on_12[("OBE", "Cross-Sectional Momentum")]["outcome"], "open")   # never replaced
        self.assertIn(("DDD", "Trend Following"), on_12)
        self.assertEqual(sum(r["scan_date"] == "2026-10-13" for r in db), 1)      # other dates untouched

    def test_first_run_inserts_everything(self):
        client = FakeClient([])
        rows = [row("AAA", "Trend Following"), row("AAA", "Pullback Recovery")]
        self.assertEqual(_replace_history_audit(client, rows), rows)


class TestDedupePlan(unittest.TestCase):
    def test_keeps_latest_run_and_published_rows(self):
        from scripts.dedupe_signals_history import plan
        rows = [{"id": 1, "scan_date": "2026-10-12", "created_at": "t1", "outcome": "rejected"},
                {"id": 2, "scan_date": "2026-10-12", "created_at": "t2", "outcome": "rejected"},
                {"id": 3, "scan_date": "2026-10-12", "created_at": "t1", "outcome": "open"},
                {"id": 4, "scan_date": "2026-10-14", "created_at": "t1", "outcome": "rejected"}]
        latest, drop, kept = plan(rows, ["2026-10-12"])["2026-10-12"]
        self.assertEqual((latest, drop, kept), ("t2", [1], 2))


if __name__ == "__main__":
    unittest.main()
