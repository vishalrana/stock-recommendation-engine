"""
Back up, then clear the recommendation data in Supabase
=======================================================
Dry run by default: prints the plan and row counts and changes nothing. With --yes it:

1. backs up EVERY table the API exposes, all rows (paginated by primary key), as JSON to
   backups/supabase_<UTC timestamp>/ (gitignored; the repo is public, never commit a backup),
   and checks each backup's row count against the table's count before anything is deleted.
   Read-only views (e.g. recommendations) are derived from the tables and are not backed up;
2. deletes all rows from the recommendation tables (CLEAR_TABLES) and empties the
   decommissioned portfolio_state (EMPTY_DECOMMISSIONED) when no code still uses it;
3. leaves KEEP_TABLES and every other table untouched, and prints row counts before and after.

Usage:
  python scripts/clear_and_reset_db.py          # dry run
  python scripts/clear_and_reset_db.py --yes    # back up, then delete
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CLEAR_TABLES = ("signals", "signals_history", "scan_log", "context_cache")
EMPTY_DECOMMISSIONED = ("portfolio_state",)
KEEP_TABLES = ("earnings_calendar", "ticker_metrics", "delisted_tickers")
PAGE_SIZE = 1000
# Code that would still depend on a decommissioned table (the reset script itself and the test that
# asserts the table is unused are excluded).
USAGE_SCAN_DIRS = ("jobs", "src", "frontend/src", ".github", "scripts")
USAGE_SCAN_EXCLUDE = ("scripts/clear_and_reset_db.py", "scripts/test_decommissioning_and_recommendation_isolation.py",
                      "scripts/test_clear_and_reset_db.py")


def api_relations(url: str, key: str) -> tuple:
    """
    ({table: [primary-key columns]}, [read-only views]) from the PostgREST API spec. A relation
    that accepts DELETE is a table; a GET-only relation is a view.
    """
    import requests
    resp = requests.get(f"{url}/rest/v1/", headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
    resp.raise_for_status()
    spec = resp.json()
    tables, views = {}, []
    for name, definition in (spec.get("definitions") or {}).items():
        methods = set((spec.get("paths", {}).get(f"/{name}") or {}).keys())
        if "delete" not in methods:
            views.append(name)
            continue
        props = definition.get("properties") or {}
        tables[name] = [col for col, meta in props.items() if "<pk/>" in str(meta.get("description", ""))]
    return tables, sorted(views)


def count_rows(client, table: str) -> int:
    return client.table(table).select("*", count="exact").limit(1).execute().count or 0


def fetch_all_rows(client, table: str, order_by: list) -> list:
    rows, start = [], 0
    while True:
        query = client.table(table).select("*")
        for col in order_by:
            query = query.order(col)
        page = query.range(start, start + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def backup_all(client, relations: dict, backup_dir: Path) -> dict:
    """Write every relation's rows to backup_dir/<relation>.json. Returns {relation: rows written}."""
    backup_dir.mkdir(parents=True, exist_ok=False)
    written = {}
    for name, pk in sorted(relations.items()):
        rows = fetch_all_rows(client, name, pk)
        with open(backup_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(rows, f, default=str)
        written[name] = len(rows)
    with open(backup_dir / "_manifest.json", "w", encoding="utf-8") as f:
        json.dump({"created_utc": backup_dir.name.replace("supabase_", ""), "rows": written,
                   "primary_keys": relations}, f, indent=2)
    return written


def still_used(table: str) -> list:
    """Source files (outside the exclusions) that still reference a table."""
    hits = []
    pattern = re.compile(r"\b%s\b" % re.escape(table))
    for d in USAGE_SCAN_DIRS:
        for path in (PROJECT_ROOT / d).rglob("*"):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if not path.is_file() or rel in USAGE_SCAN_EXCLUDE or "node_modules" in rel or "__pycache__" in rel:
                continue
            if path.suffix not in (".py", ".ts", ".tsx", ".yml", ".yaml", ".sql", ".js"):
                continue
            try:
                if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                    hits.append(rel)
            except OSError:
                continue
    return hits


def delete_all_rows(client, table: str, pk: list) -> None:
    """Delete every row (PostgREST needs a filter; a primary key is never null)."""
    if not pk:
        raise RuntimeError(f"{table}: no primary key found in the API spec; refusing an unfiltered delete")
    client.table(table).delete().not_.is_(pk[0], "null").execute()


def plan(relations: dict) -> tuple:
    clear = [t for t in CLEAR_TABLES if t in relations]
    empty, skipped_in_use = [], {}
    for t in EMPTY_DECOMMISSIONED:
        if t in relations:
            users = still_used(t)
            if users:
                skipped_in_use[t] = users
            else:
                empty.append(t)
    return clear, empty, skipped_in_use


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Back up, then clear the recommendation data in Supabase")
    parser.add_argument("--yes", action="store_true", help="Actually back up and delete (default: dry run)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    from jobs.supabase_client import get_client
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY missing.")
        return 1
    client = get_client()
    relations, views = api_relations(url, key)
    clear, empty, skipped_in_use = plan(relations)
    targets = clear + empty

    before = {t: count_rows(client, t) for t in sorted(relations)}
    print("Row counts before:")
    for t, n in before.items():
        role = "CLEAR" if t in clear else "EMPTY (decommissioned)" if t in empty else "keep" if t in KEEP_TABLES else "untouched"
        print(f"  {t:20s} {n:8d}  {role}")
    for t, users in skipped_in_use.items():
        print(f"  {t}: still referenced by {users}; not emptied")
    if views:
        print(f"  views (derived from the tables, not backed up): {', '.join(views)}")

    if not args.yes:
        print(f"\nDRY RUN: nothing backed up or deleted. With --yes: back up {len(relations)} tables to "
              f"backups/supabase_<UTC timestamp>/, then delete all rows from {', '.join(targets)}.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = PROJECT_ROOT / "backups" / f"supabase_{stamp}"
    written = backup_all(client, relations, backup_dir)
    short = {t: (written[t], before[t]) for t in relations if written[t] != before[t]}
    if short:
        print(f"ABORT: backup row counts differ from the tables {short}; nothing deleted. Backup: {backup_dir}")
        return 1
    print(f"\nBacked up {sum(written.values())} rows from {len(written)} tables to {backup_dir}")

    for t in targets:
        delete_all_rows(client, t, relations[t])

    after = {t: count_rows(client, t) for t in sorted(relations)}
    print("Row counts after:")
    problems = []
    for t in sorted(relations):
        deleted = before[t] - after[t]
        print(f"  {t:20s} {after[t]:8d}  (deleted {deleted})")
        if t in targets and after[t] != 0:
            problems.append(f"{t} still has {after[t]} rows")
        if t not in targets and after[t] != before[t]:
            problems.append(f"{t} changed from {before[t]} to {after[t]} rows")
    if problems:
        print("PROBLEMS: " + "; ".join(problems))
        return 1
    print("Cleared tables are empty; every other table is unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
