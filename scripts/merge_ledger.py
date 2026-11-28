"""
Merge tonight's ledger/*.jsonl into the signals-ledger branch's files (swing_signals.yml)
========================================================================================
The branch is append-only. An event is written once: a BUY or SELL per (session, action, sleeve,
ticker, signal date), a CORRECTION per (session, target, change). Lines already on the branch,
and repeats within tonight's lines, are skipped (a re-run of a session wrote its events again:
45 BUYs on 2026-11-27, which `sort -u` kept because each line carries its own timestamp).

  python scripts/merge_ledger.py <tonight's ledger dir> <the branch's ledger dir>
"""

import glob
import json
import os
import sys
from typing import Dict, Tuple


def event_key(e: Dict) -> Tuple:
    if e.get("action") == "CORRECTION":
        t = e.get("target") or {}
        return ("CORRECTION", e.get("session_date"), t.get("sleeve"), t.get("ticker"), t.get("signal_date"), e.get("change"))
    sd = e.get("signal_date") or (e.get("session_date") if e.get("action") == "BUY" else None)
    return (e.get("session_date"), e.get("action"), e.get("sleeve"), e.get("ticker"), sd)


def merge(new_dir: str, branch_dir: str) -> Dict[str, int]:
    os.makedirs(branch_dir, exist_ok=True)
    stats = {"appended": 0, "skipped": 0}
    for path in sorted(glob.glob(os.path.join(new_dir, "*.jsonl"))):
        target = os.path.join(branch_dir, os.path.basename(path))
        seen = set()
        if os.path.exists(target):
            with open(target, encoding="utf-8") as f:
                seen = {event_key(json.loads(line)) for line in f if line.strip()}
        add = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                k = event_key(json.loads(line))
                if k in seen:
                    stats["skipped"] += 1
                    continue
                seen.add(k)
                add.append(line.rstrip("\n") + "\n")
        if add:
            with open(target, "a", encoding="utf-8") as f:
                f.writelines(add)
        stats["appended"] += len(add)
    return stats


if __name__ == "__main__":
    print(json.dumps(merge(sys.argv[1], sys.argv[2])))
