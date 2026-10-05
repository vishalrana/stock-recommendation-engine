"""
Forensic Supabase Database Audit Script
========================================
Scans all python, typescript, javascript, and sql files in the codebase
to identify every table, view, column reference, and mutation to Supabase.
"""

import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Tables known in migrations or schema
CANDIDATE_TABLES = [
    "signals",
    "signals_history",
    "scan_log",
    "ticker_metrics",
    "context_cache",
    "earnings_calendar",
    "delisted_tickers",
    "positions",
    "portfolio",
    "recommendations",  # view
    "recommendations_with_history", # view
    "closed_recommendations", # view
]

IGNORE_DIRS = {".git", "node_modules", "venv", ".next", ".system_generated", ".cache"}

def audit_codebase():
    table_refs = {t: [] for t in CANDIDATE_TABLES}
    supabase_calls = []

    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if not f.endswith((".py", ".ts", ".tsx", ".sql", ".js", ".mjs")):
                continue
            fpath = Path(root) / f
            relpath = fpath.relative_to(PROJECT_ROOT).as_posix()
            
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                lines = fp.readlines()

            for idx, line in enumerate(lines, 1):
                # Search for supabase.table("...") or .from_("...") or .from("...")
                m = re.search(r'\.(?:table|from_|from)\(\s*["\']([a-zA-Z0-9_]+)["\']\s*\)', line)
                if m:
                    tbl = m.group(1)
                    supabase_calls.append((tbl, relpath, idx, line.strip()))
                    if tbl in table_refs:
                        table_refs[tbl].append((relpath, idx, line.strip()))
                    else:
                        table_refs[tbl] = [(relpath, idx, line.strip())]

                # Check for SQL table occurrences
                for t in CANDIDATE_TABLES:
                    if re.search(rf'\b{t}\b', line, re.IGNORECASE) and (relpath.endswith('.sql') or 'supabase' in relpath):
                        if (relpath, idx, line.strip()) not in table_refs[t]:
                            table_refs[t].append((relpath, idx, line.strip()))

    return table_refs, supabase_calls

if __name__ == "__main__":
    table_refs, supabase_calls = audit_codebase()
    print("==================================================")
    print("SUPABASE CODEBASE AUDIT: TABLE REFERENCES")
    print("==================================================")
    for tbl, refs in sorted(table_refs.items()):
        print(f"\nTABLE / VIEW: {tbl} (Total references: {len(refs)})")
        files = {r[0] for r in refs}
        print(f"  Files ({len(files)}): {sorted(files)[:8]}")
        for r in refs[:3]:
            print(f"    {r[0]}:{r[1]} -> {r[2][:90]}")
