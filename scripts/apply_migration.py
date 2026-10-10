"""
Apply a Supabase migration file through the service-role key.

Requires the one-time helper `public.run_migration(sql text)` (see supabase/setup_run_migration.sql),
which only the service_role may execute. Statements are sent one at a time and the run stops at
the first failure. Migration files should be idempotent (IF NOT EXISTS / guarded UPDATEs).

Usage:
  python scripts/apply_migration.py supabase/migration_2026_10_10_fundamentals.sql [--dry-run]
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def split_statements(sql: str) -> list:
    """Split on semicolons outside quotes, dollar-quoted bodies and comments."""
    out, buf, i, n = [], [], 0, len(sql)
    quote = None      # "'" or '"'
    dollar = None     # active dollar-quote tag, e.g. "$$" or "$body$"
    while i < n:
        ch = sql[i]
        if dollar:
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
        elif quote:
            if ch == quote:
                quote = None
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "$":
            j = sql.find("$", i + 1)
            tag = sql[i:j + 1] if j != -1 else ""
            if tag and all(c.isalnum() or c == "_" for c in tag[1:-1]):
                dollar = tag
                buf.append(tag)
                i = j + 1
                continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a Supabase migration file")
    parser.add_argument("path")
    parser.add_argument("--dry-run", action="store_true", help="Print the statements without running them")
    args = parser.parse_args()
    with open(args.path, encoding="utf-8") as f:
        statements = split_statements(f.read())
    print(f"{len(statements)} statements in {args.path}")
    if args.dry_run:
        for k, stmt in enumerate(statements, 1):
            print(f"--- [{k}]\n{stmt}")
        return 0

    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.supabase_client import get_client
    client = get_client()
    for k, stmt in enumerate(statements, 1):
        first = " ".join(stmt.split())[:90]
        try:
            client.rpc("run_migration", {"sql": stmt}).execute()
        except Exception as e:
            print(f"[{k}/{len(statements)}] FAILED: {first}\n    {e}")
            return 1
        print(f"[{k}/{len(statements)}] ok: {first}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
