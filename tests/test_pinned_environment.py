"""Every workflow runs on the pinned environment (user decision 2026-10-26): ubuntu-24.04, Python
3.11.17, pip 26.2.1 and the exact versions in requirements.lock."""

import glob
import os
import re
import sys
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPinnedEnvironment(unittest.TestCase):
    def test_lock_is_exact(self):
        pins = [l.strip() for l in open(os.path.join(ROOT, "requirements.lock"), encoding="utf-8")
                if l.strip() and not l.startswith("#")]
        self.assertGreater(len(pins), 100)
        for p in pins:
            self.assertRegex(p, r"^[A-Za-z0-9_.-]+==[0-9][^ ]*$")
        self.assertIn("pandas==3.0.6", pins)                     # the evidence runs' pandas

    def test_workflows_install_the_lock(self):
        for path in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")):
            text = open(path, encoding="utf-8").read()
            wf = yaml.safe_load(text)
            name = os.path.basename(path)
            for job_id, job in wf["jobs"].items():
                self.assertEqual(job.get("runs-on"), "ubuntu-24.04", f"{name}:{job_id}")
                for step in job.get("steps", []):
                    ver = (step.get("with") or {}).get("python-version")
                    if ver is not None:
                        self.assertEqual(str(ver), "3.11.17", f"{name}:{job_id}")
            self.assertIsNone(re.search(r"pip install -r requirements(-research)?\.txt|jobs/requirements\.txt", text), name)
            self.assertNotIn("--upgrade pip", text, name)


if __name__ == "__main__":
    unittest.main()
