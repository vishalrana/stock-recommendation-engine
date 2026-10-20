"""
Remove duplicate signals_history rows left by reruns of a scan date
===================================================================
Before 2026-10-20 a rerun of a scan date inserted every audit row again (signals_history writes are
idempotent since then, jobs/generate_signals._replace_history_audit). For each given scan date this
keeps the rows of the latest run (one run = one created_at) and deletes the earlier runs' rejected
rows. Rows with any other outcome (published ideas, lifecycle) are never deleted.

  python scripts/dedupe_signals_history.py 2026-10-12 2026-10-13          # dry run: counts only
  python scripts/dedupe_signals_history.py 2026-10-12 2026-10-13 --yes    # back up, then delete

The backup goes to backups/supabase_<UTC>/signals_history.json (gitignored, never committed).
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.clear_and_reset_db import backup_all, count_rows, fetch_all_rows  # noqa: E402


def plan(rows, scan_dates):
    """{scan_date: (latest created_at, ids to delete, rows kept)} for the given dates."""
    out = {}
    for d in scan_dates:
        day = [r for r in rows if r["scan_date"] == d]
        if not day:
            out[d] = (None, [], 0)
            continue
        latest = max(r["created_at"] for r in day)
        drop = [r["id"] for r in day if r["created_at"] != latest and r.get("outcome") == "rejected"]
        out[d] = (latest, drop, len(day) - len(drop))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan_dates", nargs="+")
    ap.add_argument("--yes", action="store_true", help="back up signals_history, then delete")
    args = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    from jobs.supabase_client import get_client
    client = get_client()
    rows = fetch_all_rows(client, "signals_history", ["id"])
    before = len(rows)
    work = plan(rows, args.scan_dates)
    per_date = defaultdict(int)
    for r in rows:
        per_date[r["scan_date"]] += 1
    for d, (latest, drop, kept) in work.items():
        print(f"{d}: {per_date.get(d, 0)} rows; latest run {latest}; delete {len(drop)} earlier-run rejected rows; keep {kept}")
    total = sum(len(v[1]) for v in work.values())
    print(f"signals_history: {before} rows; {total} to delete")
    if not args.yes:
        print("DRY RUN: nothing backed up or deleted (pass --yes).")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = PROJECT_ROOT / "backups" / f"supabase_{stamp}"
    written = backup_all(client, {"signals_history": ["id"]}, backup_dir)
    if written["signals_history"] != before:
        print(f"Backup count {written['signals_history']} != {before}: nothing deleted.")
        return 1
    print(f"Backed up {before} rows to {backup_dir}")
    for d, (_, drop, _) in work.items():
        for i in range(0, len(drop), 100):
            client.table("signals_history").delete().in_("id", drop[i:i + 100]).execute()
    after = count_rows(client, "signals_history")
    rows_after = fetch_all_rows(client, "signals_history", ["id"])
    for d in args.scan_dates:
        print(f"{d}: {sum(r['scan_date'] == d for r in rows_after)} rows after")
    print(f"signals_history: {before} -> {after} rows")
    return 0 if after == before - total else 1


if __name__ == "__main__":
    sys.exit(main())
