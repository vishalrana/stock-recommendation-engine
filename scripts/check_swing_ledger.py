"""
Swing ledger consistency (swing_signals.yml after the ledger push; also run by hand)
====================================================================================
Every swing_signals row has exactly one BUY on its signal session and every closed row exactly one
SELL on its exit session; a voided row has neither. This must hold in the jsonl ledger (branch
signals-ledger) and in swing_ledger alike. CORRECTION entries (scripts/correct_swing_records.py)
cancel or move the events they name. Dates: a BUY's session is its signal session, a SELL's its exit
session; when it was written is logged_at. Exit 1 on any difference, listed in the job summary.

  python scripts/check_swing_ledger.py [--ref origin/signals-ledger]
"""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from typing import Dict, Iterable, List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

Key = Tuple[str, str, str, str]          # (action, sleeve, ticker, session)


def effective(events: Iterable[Dict]) -> Counter:
    """BUY and SELL events per key after the CORRECTION entries."""
    c: Counter = Counter()
    for e in events:
        a = e.get("action")
        if a in ("BUY", "SELL"):
            c[(a, e["sleeve"], e["ticker"], str(e["session_date"])[:10])] += 1
        elif a == "CORRECTION":
            t, ch = e.get("target") or {}, e.get("change")
            sl, tk = t.get("sleeve"), t.get("ticker")
            if ch == "void":
                c[("BUY", sl, tk, t["signal_date"])] -= 1
                if t.get("exit_session"):
                    c[("SELL", sl, tk, t["exit_session"])] -= 1
            elif ch == "reopen":
                c[("SELL", sl, tk, t["exit_session"])] -= 1
            elif ch == "exit_changed":
                if t.get("exit_session"):
                    c[("SELL", sl, tk, t["exit_session"])] -= 1
                c[("SELL", sl, tk, (e.get("new") or {})["exit_session"])] += 1
            elif ch == "duplicate":
                c[(t["action"], sl, tk, t["session_date"])] -= int(t.get("extra", 1))
    return c


def expected(signals: Iterable[Dict]) -> Counter:
    c: Counter = Counter()
    for r in signals:
        if r["status"] == "void":
            continue
        c[("BUY", r["sleeve"], r["ticker"], r["signal_date"])] += 1
        if r["status"] == "closed":
            c[("SELL", r["sleeve"], r["ticker"], r["exit_date"])] += 1
    return c


def differences(exp: Counter, got: Counter) -> List[Tuple[Key, int, int]]:
    return sorted((k, exp.get(k, 0), got.get(k, 0)) for k in set(exp) | set(got) if exp.get(k, 0) != got.get(k, 0))


def jsonl_events(ref: str) -> List[Dict]:
    names = subprocess.run(["git", "ls-tree", "--name-only", ref, "ledger/"], capture_output=True, text=True,
                           check=True, cwd=PROJECT_ROOT).stdout.split()
    out = []
    for n in names:
        text = subprocess.run(["git", "show", f"{ref}:{n}"], capture_output=True, text=True, check=True, cwd=PROJECT_ROOT).stdout
        out += [json.loads(line) for line in text.splitlines() if line.strip()]
    return out


def table_events(rows: Iterable[Dict]) -> List[Dict]:
    """swing_ledger rows as ledger events (a CORRECTION row keeps target, change and new in details)."""
    out = []
    for r in rows:
        d = r.get("details") or {}
        out.append(dict(r, **{k: d[k] for k in ("target", "change", "new") if k in d}) if r["action"] == "CORRECTION" else r)
    return out


def report(signals: List[Dict], sources: Dict[str, List[Dict]]) -> Tuple[bool, str]:
    exp = expected(signals)
    lines, ok = [f"### Swing ledger consistency: {len(signals)} signals, "
                 f"{sum(1 for r in signals if r['status'] == 'void')} void", ""], True
    for name, events in sources.items():
        diff = differences(exp, effective(events))
        ok &= not diff
        lines.append(f"- {name}: {'consistent' if not diff else f'{len(diff)} differences'} ({len(events)} entries)")
        lines += [f"  - {a} {sl} {tk} {d}: expected {e}, found {g}" for (a, sl, tk, d), e, g in diff[:40]]
        if len(diff) > 40:
            lines.append(f"  - ... {len(diff) - 40} more")
    return ok, "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="origin/signals-ledger", help="git ref holding ledger/*.jsonl")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.generate_swing_signals import fetch_rows
    from jobs.supabase_client import get_client
    client = get_client()
    ok, text = report(fetch_rows(client, "swing_signals"),
                      {"jsonl ledger": jsonl_events(a.ref), "swing_ledger": table_events(fetch_rows(client, "swing_ledger"))})
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
