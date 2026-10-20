"""
Merge a run's trial records into the committed registry, config/research_trials.jsonl.

  python scripts/persist_trials.py outputs/research_trials.jsonl --run-id 123          # local file only
  python scripts/persist_trials.py outputs/research_trials.jsonl --run-id 123 --push   # on Actions

With --push the merge is made on a fresh checkout of origin/main and pushed there, retried when
another run pushed first, so concurrent research runs never overwrite each other's records.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.research.trials import COMMITTED_PATH, PROJECT_ROOT, merge_trials  # noqa: E402

REL_PATH = os.path.relpath(COMMITTED_PATH, PROJECT_ROOT).replace(os.sep, "/")


def git(*args: str, cwd: str = PROJECT_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def push(sources, run_id, attempts: int = 5, repo: str = PROJECT_ROOT) -> int:
    sources = [os.path.abspath(s) for s in sources]
    for attempt in range(1, attempts + 1):
        wt = tempfile.mkdtemp(prefix="trials-main-")
        try:
            if (git("fetch", "-q", "origin", "main", cwd=repo).returncode
                    or git("worktree", "add", "-q", "--detach", wt, "origin/main", cwd=repo).returncode):
                raise SystemExit("Could not check out origin/main")
            added = merge_trials(sources, os.path.join(wt, REL_PATH), run_id)
            if not added:
                print("Trials registry: nothing new")
                return 0
            git("add", REL_PATH, cwd=wt)
            msg = f"Trials registry: {added} records from run {run_id}"
            c = git("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-q", "-m", msg, cwd=wt)
            if c.returncode:
                raise SystemExit(f"Commit failed: {c.stderr}")
            p = git("push", "-q", "origin", "HEAD:main", cwd=wt)
            if p.returncode == 0:
                print(f"{msg}: pushed to main")
                return added
            print(f"Push attempt {attempt} rejected ({p.stderr.strip()}); retrying on the new main")
        finally:
            git("worktree", "remove", "--force", wt, cwd=repo)
        time.sleep(10 * attempt)
    raise SystemExit("Could not push the trials registry")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", help="trial files (missing ones are skipped)")
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"), help="run id for records without one")
    ap.add_argument("--push", action="store_true", help="merge on origin/main and push (Actions)")
    a = ap.parse_args()
    if a.push:
        push(a.sources, a.run_id)
    else:
        print(f"Trials registry: {merge_trials(a.sources, COMMITTED_PATH, a.run_id)} records added to {REL_PATH}")


if __name__ == "__main__":
    main()
