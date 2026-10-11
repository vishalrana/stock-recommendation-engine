"""
Trials registry (pre-registered profitability program)
======================================================
Every variant run, including smoke runs that produce statistics, appends one JSON line to
outputs/research_trials.jsonl so that multiple-testing corrections can count every trial ever run.
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY_PATH = os.path.join(PROJECT_ROOT, "outputs", "research_trials.jsonl")


def git_sha() -> str:
    """Commit the trial ran on (GITHUB_SHA on Actions, else the local HEAD)."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=PROJECT_ROOT, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def record_trial(name: str, phase: str, params: Dict[str, Any], n_ideas: int,
                 mean_beta_adj: Optional[float], ci: Optional[List[float]], late_mean: Optional[float],
                 series_path: Optional[str] = None, smoke: bool = False, extra: Optional[Dict[str, Any]] = None,
                 path: str = REGISTRY_PATH, sha: Optional[str] = None) -> Dict[str, Any]:
    """Append one trial record and return it."""
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "phase": phase,
        "git_sha": sha or git_sha(),
        "params": params,
        "n_ideas": int(n_ideas),
        "mean_beta_adj": None if mean_beta_adj is None else round(float(mean_beta_adj), 4),
        "ci95": None if ci is None else [round(float(x), 4) for x in ci],
        "late_mean": None if late_mean is None else round(float(late_mean), 4),
        "series_path": series_path,
        "smoke": bool(smoke),
    }
    if extra:
        record.update(extra)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return record


def read_trials(path: str = REGISTRY_PATH) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
