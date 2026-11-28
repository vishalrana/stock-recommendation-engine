"""Workflow health check (scripts/workflow_health.py): cron slots, states, weekly runs, nightly, late starts."""

import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.workflow_health as wh

UTC = dt.timezone.utc


def T(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


class FakeGitHub:
    def __init__(self, workflows=(), runs=None, jobs=None):
        self._workflows, self._runs, self._jobs = list(workflows), runs or {}, jobs or {}
        self.enabled = []

    def workflows(self):
        return self._workflows

    def runs(self, workflow, since):
        return [r for r in self._runs.get(workflow, []) if wh._ts(r["created_at"]) >= since]

    def jobs(self, run_id):
        return self._jobs.get(run_id, [])

    def put(self, path):
        self.enabled.append(path)


def run(i, created, event="schedule", conclusion="success", started=None, updated=None):
    return {"id": i, "event": event, "created_at": created, "run_started_at": started or created,
            "updated_at": updated or created, "conclusion": conclusion}


SCHEDULED = {"nightly_scan.yml": ["0 1 * * 2-6", "0 4 * * 2-6"], "swing_signals.yml": ["20 1 * * 2-6"],
             "parity_replay.yml": ["0 12 * * 6"], "refresh_earnings.yml": ["0 12 * * 0"],
             "price_refresh_timing.yml": ["0 12 * * 0"]}


class TestCron(unittest.TestCase):
    def test_last_fire(self):
        self.assertEqual(wh.last_fire("0 12 * * 6", T("2026-11-29T14:07")), T("2026-11-28T12:00"))   # Sun -> Sat
        self.assertEqual(wh.last_fire("0 1 * * 2-6", T("2026-11-28T00:30")), T("2026-11-27T01:00"))  # before Sat's slot
        self.assertEqual(wh.last_fire("20 1 * * 2-6", T("2026-11-30T14:07")), T("2026-11-28T01:20"))  # Mon -> Sat
        self.assertEqual(wh.last_fire("0 12 * * 0", T("2026-11-29T12:00")), T("2026-11-29T12:00"))    # on the minute
        self.assertEqual(wh.last_fire("7 14 * * *", T("2026-11-29T14:06")), T("2026-11-28T14:07"))

    def test_every_repo_cron_parses(self):
        found = wh.crons()
        self.assertTrue({"nightly_scan.yml", "swing_signals.yml", "parity_replay.yml"} <= set(found))
        for name, cs in found.items():
            for c in cs:
                wh.last_fire(c, T("2026-11-28T14:07"))


class TestStates(unittest.TestCase):
    def test_inactivity_is_undone_and_reported_manual_is_only_reported(self):
        gh = FakeGitHub(workflows=[
            {"id": 1, "path": ".github/workflows/nightly_scan.yml", "state": "disabled_inactivity"},
            {"id": 2, "path": ".github/workflows/parity_replay.yml", "state": "disabled_manually"},
            {"id": 3, "path": ".github/workflows/swing_signals.yml", "state": "active"}])
        fails = wh.check_states(gh, {k: SCHEDULED[k] for k in ("nightly_scan.yml", "parity_replay.yml",
                                                                 "swing_signals.yml", "refresh_earnings.yml")})
        self.assertEqual(gh.enabled, ["/actions/workflows/1/enable"])               # never the manual one
        self.assertEqual(len(fails), 3)
        self.assertTrue(any("re-enabled" in f and "nightly_scan" in f for f in fails))
        self.assertTrue(any("disabled_manually" in f and "not re-enabled" in f for f in fails))
        self.assertTrue(any("refresh_earnings.yml: not registered" in f for f in fails))


class TestWeekly(unittest.TestCase):
    def test_a_missed_saturday_replay_fails_on_sunday(self):
        ok = {"refresh_earnings.yml": [run(5, "2026-11-22T18:53:00Z")],
              "price_refresh_timing.yml": [run(6, "2026-11-22T19:18:00Z")]}
        gh = FakeGitHub(runs=dict(ok, **{"parity_replay.yml": []}))
        fails = wh.check_weekly(gh, SCHEDULED, T("2026-11-29T14:07"))
        self.assertEqual(fails, ["parity_replay.yml: no run since its Sat 2026-11-28 12:00 UTC slot"])
        gh = FakeGitHub(runs=dict(ok, **{"parity_replay.yml": [run(7, "2026-11-28T17:30:00Z")]}))
        self.assertEqual(wh.check_weekly(gh, SCHEDULED, T("2026-11-29T14:07")), [])
        gh = FakeGitHub(runs=dict(ok, **{"parity_replay.yml": [run(7, "2026-11-28T17:30:00Z", conclusion="cancelled")]}))
        self.assertEqual(len(wh.check_weekly(gh, SCHEDULED, T("2026-11-29T14:07"))), 1)   # a cancelled run did nothing
        # Saturday's check, two hours after the slot: last week's run still counts
        gh = FakeGitHub(runs=dict(ok, **{"parity_replay.yml": [run(7, "2026-11-21T18:46:00Z")]}))
        self.assertEqual(wh.check_weekly(gh, SCHEDULED, T("2026-11-28T14:07")), [])

    def test_first_sunday_rebuild_must_have_run(self):
        runs = {"parity_replay.yml": [run(1, "2026-12-05T17:00:00Z")], "refresh_earnings.yml": [run(2, "2026-12-06T17:00:00Z")],
                "price_refresh_timing.yml": [run(3, "2026-12-06T18:00:00Z")]}
        skipped = {3: [{"steps": [{"name": "Monthly only", "conclusion": "success"}, {"name": "Run", "conclusion": "skipped"}]}]}
        fails = wh.check_weekly(FakeGitHub(runs=runs, jobs=skipped), SCHEDULED, T("2026-12-07T14:07"))
        self.assertEqual(fails, ["price_refresh_timing.yml: the first-Sunday rebuild of 2026-12-06 did not run or failed its checks"])
        rebuilt = {3: [{"steps": [{"name": "Run", "conclusion": "success"}]}]}
        self.assertEqual(wh.check_weekly(FakeGitHub(runs=runs, jobs=rebuilt), SCHEDULED, T("2026-12-07T14:07")), [])


def scan_log(rows):
    client = mock.MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = rows
    return client


class TestNightly(unittest.TestCase):
    SESSION = dt.date(2026, 11, 27)
    GH = FakeGitHub(runs={"nightly_scan.yml": [run(46127475496, "2026-11-28T08:47:52Z", updated="2026-11-28T09:00:00Z")]})

    def test_success_names_its_run(self):
        f, n = wh.check_nightly(scan_log([{"scan_date": "2026-11-27", "status": "success",
                                            "created_at": "2026-11-28T08:56:44+00:00"}]), self.GH, self.SESSION)
        self.assertEqual((f, n), ([], ["nightly: session 2026-11-27 success (run 46127475496)"]))

    def test_missing_row_fails(self):
        f, _ = wh.check_nightly(scan_log([]), self.GH, self.SESSION)
        self.assertEqual(f, ["nightly: no scan_log row for session 2026-11-27"])

    def test_non_success_passes_only_when_documented(self):
        gap = {"scan_date": "2026-11-27", "status": "stale_data", "created_at": "2026-11-29T10:00:00+00:00",
               "error_message": "No nightly run: the scheduled workflow did not trigger. Forward-test gap; replay backfill run 46200000000."}
        f, n = wh.check_nightly(scan_log([gap]), self.GH, self.SESSION)
        self.assertEqual(f, [])
        self.assertIn("documented (run 46200000000)", n[0])
        f, _ = wh.check_nightly(scan_log([dict(gap, error_message="stale")]), self.GH, self.SESSION)
        self.assertEqual(f, ["nightly: session 2026-11-27 is stale_data with no run id to document it"])


class TestLateStartsAndSummary(unittest.TestCase):
    def test_late_start_is_a_warning(self):
        gh = FakeGitHub(runs={"swing_signals.yml": [run(1, "2026-11-28T08:54:41Z"), run(2, "2026-11-28T06:00:00Z", event="workflow_dispatch")],
                              "nightly_scan.yml": [run(3, "2026-11-28T08:47:52Z")]})
        warns = wh.late_starts(gh, SCHEDULED, T("2026-11-28T14:07"))
        self.assertEqual(warns, ["nightly_scan.yml: run 3 started 7.8 h after its Sat 01:00 UTC cron",
                                 "swing_signals.yml: run 1 started 7.6 h after its Sat 01:20 UTC cron"])

    def test_runs_pair_with_the_days_slots_in_creation_order(self):
        # GitHub created both runs of 2026-11-27 after 04:00: the first is still the 01:00 slot's
        runs = [run(10, "2026-11-27T13:38:07Z"), run(9, "2026-11-27T09:34:42Z"), run(8, "2026-11-26T09:19:28Z")]
        pairs = {r["id"]: s for r, s in wh.slot_of_runs(runs, SCHEDULED["nightly_scan.yml"])}
        self.assertEqual(pairs, {8: T("2026-11-26T01:00"), 9: T("2026-11-27T01:00"), 10: T("2026-11-27T04:00")})
        # at 13:00 only run 10 is under 24 hours old; the whole UTC day still pairs it with the 04:00 slot
        gh = FakeGitHub(runs={"nightly_scan.yml": runs})
        self.assertEqual(wh.late_starts(gh, SCHEDULED, T("2026-11-28T13:00")),
                         ["nightly_scan.yml: run 10 started 9.6 h after its Fri 04:00 UTC cron"])

    def test_a_check_that_cannot_run_fails_and_apprise_is_reported(self):
        gh = FakeGitHub(workflows=[{"id": 1, "path": f".github/workflows/{k}", "state": "active"} for k in SCHEDULED])
        broken = mock.MagicMock()
        broken.table.side_effect = RuntimeError("supabase down")
        fails, warns, notes = wh.run_checks(gh, broken, T("2026-11-28T14:07"), dt.date(2026, 11, 27), SCHEDULED,
                                            swing_processed=lambda s: None)
        self.assertTrue(any(f.startswith("nightly: the check could not run") for f in fails))
        self.assertIn("swing: session 2026-11-27 has no processed marker", fails)
        with mock.patch.dict(os.environ, {"APPRISE_URLS": ""}):
            self.assertIn("APPRISE_URLS is not set", wh.notify("x", True))


class TestSwingMarker(unittest.TestCase):
    def marker(self, rows):
        client = mock.MagicMock()
        client.table.return_value.select.return_value.execute.return_value.data = rows
        return wh.swing_marker(client)

    def test_processed_unless_stale_or_older(self):
        s = dt.date(2026, 11, 27)
        ok = [{"sleeve": "T", "details": {"last_session": "2026-11-27", "stale": False, "processed_session": "2026-11-27"}},
              {"sleeve": "I", "details": {"last_session": "2026-11-27", "stale": False, "processed_session": "2026-11-25"}}]
        self.assertEqual(self.marker(ok)(s), "swing_sleeve_status last_session; no BUY scan yet on it for sleeve(s) I")
        self.assertIsNone(self.marker([{"sleeve": "T", "details": {"last_session": "2026-11-27", "stale": True}}])(s))
        self.assertIsNone(self.marker([{"sleeve": "T", "details": {"last_session": "2026-11-25"}}])(s))


if __name__ == "__main__":
    unittest.main()
