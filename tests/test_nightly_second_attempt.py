"""Nightly second attempt (nightly_scan.yml): a scheduled run exits when the session already succeeded."""

import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs.generate_signals as gs


def _client(rows=None, error=None):
    c = mock.Mock()
    q = c.table.return_value.select.return_value.eq.return_value
    if error:
        q.execute.side_effect = error
    else:
        q.execute.return_value.data = rows
    return c


class TestSecondAttempt(unittest.TestCase):
    def _done(self, client):
        with mock.patch.object(gs, "get_client", return_value=client), \
                mock.patch("src.utils.market_date.latest_completed_session", return_value=date(2026, 10, 20)):
            done = gs.session_already_succeeded()
        client.table.return_value.select.return_value.eq.assert_called_with("scan_date", "2026-10-20")
        return done

    def test_only_a_successful_scan_of_the_session_counts(self):
        self.assertTrue(self._done(_client([{"status": "success"}])))
        for status in ("stale_data", "degraded_sec", "degraded_universe", "failed"):
            self.assertFalse(self._done(_client([{"status": status}])), status)
        self.assertFalse(self._done(_client([])))
        self.assertFalse(self._done(_client(error=RuntimeError("down"))))   # unreadable: scan again

    def test_main_skips_before_any_download(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            env = {"SKIP_IF_SESSION_DONE": "true", "GITHUB_OUTPUT": out}
            with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["generate_signals"]), \
                    mock.patch.object(gs, "session_already_succeeded", return_value=True), \
                    mock.patch.object(gs, "run_scan") as run:
                gs.main()
            run.assert_not_called()
            self.assertEqual(open(out).read(), "skipped=true\n")
            with mock.patch.dict(os.environ, {"SKIP_IF_SESSION_DONE": "false"}), \
                    mock.patch.object(sys, "argv", ["generate_signals", "--cache-mode", "force"]), \
                    mock.patch.object(gs, "session_already_succeeded") as check, \
                    mock.patch.object(gs, "run_scan") as run:
                gs.main()                                                   # manual dispatch: always scans
            check.assert_not_called()
            self.assertEqual(run.call_args.kwargs["cache_mode"], "force")


if __name__ == "__main__":
    unittest.main()
