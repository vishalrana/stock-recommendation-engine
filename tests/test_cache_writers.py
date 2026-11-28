"""Every workflow that saves the nightly price cache (key data-cache-v2) is in the nightly-scan
concurrency group, so two saves never interleave: on a month's first Sunday the earnings refresh
and the cache rebuild fire on the same cron, and the next nightly restores the newest save."""

import glob
import os
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def saves_nightly_cache(wf: dict) -> bool:
    for job in wf.get("jobs", {}).values():
        for step in job.get("steps", []):
            uses = step.get("uses") or ""
            if (uses.startswith("actions/cache@") or uses.startswith("actions/cache/save@")) \
                    and "data-cache-v2" in str((step.get("with") or {}).get("key", "")):
                return True
    return False


class TestCacheWriters(unittest.TestCase):
    def test_nightly_cache_writers_share_one_group(self):
        writers = []
        for path in sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))):
            wf = yaml.safe_load(open(path, encoding="utf-8"))
            if saves_nightly_cache(wf):
                writers.append(os.path.basename(path))
                self.assertEqual((wf.get("concurrency") or {}).get("group"), "nightly-scan", os.path.basename(path))
        self.assertEqual(writers, ["nightly_scan.yml", "price_refresh_timing.yml", "refresh_earnings.yml"])


if __name__ == "__main__":
    unittest.main()
