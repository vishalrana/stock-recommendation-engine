"""scripts/verify_head.sh: the checkout must be origin/main, except for trials-registry commits."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "verify_head.sh")
BASH = shutil.which("bash") or "bash"   # on Windows a bare "bash" can resolve to the WSL stub


def git(*a, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)


class TestVerifyHead(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        d = self.d.name
        self.origin, self.run_dir, self.pusher = (os.path.join(d, x) for x in ("origin.git", "run", "pusher"))
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=d)
        git("clone", "-q", self.origin, self.pusher, cwd=d)
        os.makedirs(os.path.join(self.pusher, "config"))
        self._commit({"config/research_trials.jsonl": "", "app.py": "v1\n"}, "init")
        git("clone", "-q", self.origin, self.run_dir, cwd=d)              # the nightly's checkout

    def tearDown(self):
        self.d.cleanup()

    def _commit(self, files, msg):
        for path, text in files.items():
            with open(os.path.join(self.pusher, path), "a", encoding="utf-8") as f:
                f.write(text)
        git("add", "-A", cwd=self.pusher)
        git("commit", "-q", "-m", msg, cwd=self.pusher)
        git("push", "-q", "origin", "HEAD:main", cwd=self.pusher)

    def _check(self):
        return subprocess.run([BASH, SCRIPT], cwd=self.run_dir, capture_output=True, text=True).returncode

    def test_same_head_passes(self):
        self.assertEqual(self._check(), 0)

    def test_trials_only_commits_pass(self):
        self._commit({"config/research_trials.jsonl": "{}\n"}, "Trials registry: 1 records")
        self._commit({"config/research_trials.jsonl": "{}\n"}, "Trials registry: 1 records")
        self.assertEqual(self._check(), 0)

    def test_code_commit_fails(self):
        self._commit({"config/research_trials.jsonl": "{}\n"}, "Trials registry: 1 records")
        self._commit({"app.py": "v2\n"}, "code change")
        self.assertEqual(self._check(), 1)


if __name__ == "__main__":
    unittest.main()
