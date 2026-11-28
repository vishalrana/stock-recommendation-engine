"""
Correct the swing records (2026-11-28; user decision D4)
========================================================
plan()   swing_signals replayed per (sleeve, ticker) under the book rules (one open signal; no new one
         until 10 sessions after the exit): a signal issued while the pair's kept signal was open, or
         inside its cooldown, is voided. A kept closed signal is re-simulated with its levels rebased onto
         tonight's bars (jobs/generate_swing_signals.simulate_row): a stop that no longer triggers is
         reopened, an exit that moves is changed. Ledger events logged by a later run are marked late.
         Per source (jsonl ledger, swing_ledger): events beyond one per signal are cancelled as
         duplicates, missing ones backfilled; swing_ledger SELLs are re-dated to their exit session.
         Built on GitHub by the swing job (--plan-corrections), so the run id is the plan's.
apply()  writes the plan: backup of swing_signals, swing_ledger and swing_events first (backups/,
         local, never committed); status 'void' (ignored by the app and every metric); CORRECTION
         rows in swing_ledger and CORRECTION lines appended to the jsonl files of the sessions they
         correct (never an edit or deletion).

  python scripts/correct_swing_records.py <plan.json>            # dry run: the counts
  python scripts/correct_swing_records.py <plan.json> --apply    # back up, write Supabase, write ledger lines
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.check_swing_ledger import differences, effective, expected, table_events  # noqa: E402

EXIT_FIELDS = ("fill_date", "entry_fill_price", "holding_sessions", "exit_date", "exit_price", "exit_reason",
               "net_return_pct", "spy_return_pct", "excess_pct", "beta_adj_pct")
COOLDOWN = 11   # first session a new signal is allowed after an exit (10-session cooldown)


def trade_fields(tr, ratio: float) -> Dict[str, Any]:
    """A re-simulated trade as record fields; {"open": True} while it has no exit."""
    if tr is None or tr.exit_date is None:
        return {"open": True, "ratio": ratio}
    return {"open": False, "ratio": ratio, "fill_date": tr.fill_date.isoformat(), "entry_fill_price": tr.fill_price,
            "holding_sessions": tr.holding_sessions, "exit_date": tr.exit_date.isoformat(), "exit_price": tr.exit_price,
            "exit_reason": tr.exit_reason, "net_return_pct": tr.net_return_pct, "spy_return_pct": tr.spy_return_pct,
            "excess_pct": tr.excess_pct, "beta_adj_pct": tr.beta_adj_pct}


def _target(r: Dict[str, Any], **extra) -> Dict[str, Any]:
    return dict({"sleeve": r["sleeve"], "ticker": r["ticker"], "signal_date": r["signal_date"]}, **extra)


def replay_book(rows: List[Dict[str, Any]], resim: Dict[Any, Dict[str, Any]], cal) -> Dict[str, Any]:
    """(corrections, corrected rows by id) from the book rules and the re-simulated exits."""
    corrections, final = [], {}
    pairs = defaultdict(list)
    for r in rows:
        if r["status"] != "void":
            pairs[(r["sleeve"], r["ticker"])].append(r)
    for g in pairs.values():
        g.sort(key=lambda r: (r["signal_date"], r.get("created_at") or ""))
        open_since, last_exit = None, None
        for r in g:
            sd = date.fromisoformat(r["signal_date"])
            if open_since is not None or (last_exit is not None and sd < cal.shift(last_exit, COOLDOWN)):
                why = (f"issued while the pair's signal of {open_since} was open" if open_since is not None
                       else f"issued inside the 10-session cooldown after the exit of {last_exit}")
                corrections.append({"id": r["id"], "change": "void", "reason": why, "price": r["entry_signal_close"],
                                    "target": _target(r, **({"exit_session": r["exit_date"]} if r["status"] == "closed" else {}))})
                continue
            new, row = resim.get(r["id"]), dict(r)
            if r["status"] == "closed" and new is not None:
                if new["open"]:
                    corrections.append({"id": r["id"], "change": "reopen", "price": r["exit_price"],
                                        "reason": "the stop no longer triggers on levels rebased onto re-adjusted bars",
                                        "target": _target(r, exit_session=r["exit_date"])})
                    row.update({k: None for k in EXIT_FIELDS if k not in ("fill_date", "entry_fill_price")}, status="open")
                elif (new["exit_date"], new["exit_reason"]) != (r["exit_date"], r["exit_reason"]):
                    corrections.append({"id": r["id"], "change": "exit_changed", "price": new["exit_price"],
                                        "reason": "re-simulated with levels rebased onto re-adjusted bars",
                                        "target": _target(r, exit_session=r["exit_date"]),
                                        "new": {"exit_session": new["exit_date"], "exit_reason": new["exit_reason"]},
                                        "fields": {k: new[k] for k in EXIT_FIELDS}})
                    row.update({k: new[k] for k in EXIT_FIELDS})
            final[r["id"]] = row
            # the pair's state for later signals: tonight's re-simulation where there is one
            state = new if new is not None else ({"open": True} if row["status"] == "open" else {"open": False, "exit_date": row["exit_date"]})
            if state["open"]:
                open_since = r["signal_date"]
            else:
                last_exit = date.fromisoformat(state["exit_date"])
    return {"corrections": corrections, "final": final}


def run_session(logged_at: str, cal) -> date:
    """The session a run processed: the one before its New York date (runs log on the next morning)."""
    from zoneinfo import ZoneInfo
    return cal.shift(datetime.fromisoformat(logged_at).astimezone(ZoneInfo("America/New_York")).date(), -1)


RUN_GAP = 1200   # seconds: lines of one run are written within minutes; runs of a night are hours apart


def first_run_end(events: List[Dict]) -> Dict[str, datetime]:
    """Per session, when the first run that logged its BUYs wrote its last one."""
    by = defaultdict(list)
    for e in events:
        if e.get("action") == "BUY":
            by[e["session_date"]].append(datetime.fromisoformat(e["timestamp"]))
    out = {}
    for d, ts in by.items():
        ts.sort()
        end = ts[0]
        for t in ts[1:]:
            if (t - end).total_seconds() > RUN_GAP:
                break
            end = t
        out[d] = end
    return out


def late_marks(final: Dict[Any, Dict[str, Any]], events: List[Dict], cal) -> List[Dict[str, Any]]:
    """Kept, unchanged signals whose BUY or SELL a later run logged: a BUY not written by the first run that
    logged its session's BUYs (a re-run or a catch-up), a SELL logged a session or more after its exit."""
    first_end = first_run_end(events)
    logged = defaultdict(list)
    for e in events:
        if e.get("action") in ("BUY", "SELL"):
            logged[(e["action"], e["sleeve"], e["ticker"], e["session_date"])].append(e["timestamp"])
    out = []
    for r in final.values():
        b = sorted(logged.get(("BUY", r["sleeve"], r["ticker"], r["signal_date"]), []))
        if b and datetime.fromisoformat(b[0]) > first_end[r["signal_date"]]:
            out.append({"id": r["id"], "change": "late", "price": r["entry_signal_close"], "event": "BUY",
                        "reason": "logged by a later run than its session's first", "target": _target(r),
                        "new": {"logged_at": b[0]}, "session_date": r["signal_date"]})
        if r["status"] == "closed" and r.get("exit_date"):
            s = sorted(logged.get(("SELL", r["sleeve"], r["ticker"], r["exit_date"]), []))
            if s:
                n, a, b2 = 0, date.fromisoformat(r["exit_date"]), run_session(s[0], cal)
                while a < b2:
                    a, n = cal.shift(a, 1), n + 1
                if n > 0:
                    out.append({"id": r["id"], "change": "late", "price": r["exit_price"], "event": "SELL",
                                "reason": f"SELL logged {n} session(s) after its exit session",
                                "target": _target(r, exit_session=r["exit_date"]),
                                "new": {"late_sessions": n, "logged_at": s[0]}, "session_date": r["exit_date"]})
    return out


def ledger_fixes(final: Dict[Any, Dict[str, Any]], corrections: List[Dict], events: List[Dict]) -> Dict[str, List]:
    """Events beyond one per signal (duplicates) and missing ones, after the signal corrections."""
    exp = expected(final.values())
    got = effective(list(events) + [dict(c, action="CORRECTION") for c in corrections])
    dup, missing = [], []
    for (a, sl, tk, d), e, g in differences(exp, got):
        (dup if g > e else missing).append({"action": a, "sleeve": sl, "ticker": tk, "session_date": d, "n": abs(g - e)})
    return {"duplicate": dup, "missing": missing}


def ledger_redates(ledger_rows: List[Dict], events: List[Dict]) -> List[Dict[str, Any]]:
    """swing_ledger SELL rows dated by their run's session: the exit session from the jsonl line the same run
    wrote (same sleeve and ticker, written within 10 minutes)."""
    lines = defaultdict(list)
    for e in events:
        if e.get("action") == "SELL":
            lines[(e["sleeve"], e["ticker"])].append([datetime.fromisoformat(e["timestamp"]), e["session_date"], float(e["price"]), False])
    out = []
    for r in sorted((r for r in ledger_rows if r["action"] == "SELL"), key=lambda r: r["created_at"]):
        t = datetime.fromisoformat(r["created_at"])
        near = sorted((abs((t - ln[0]).total_seconds()), i) for i, ln in enumerate(lines.get((r["sleeve"], r["ticker"]), []))
                      if not ln[3] and abs(ln[2] - float(r["price"])) <= 1e-3)      # one line per row; jsonl prices have 4 decimals
        if near and near[0][0] <= 600:
            ln = lines[(r["sleeve"], r["ticker"])][near[0][1]]
            ln[3] = True
            if ln[1] != r["session_date"]:
                out.append({"ledger_id": r["id"], "session_date": ln[1], "run_session": r["session_date"]})
    return out


def plan(rows: List[Dict], resim: Dict[Any, Dict[str, Any]], cal, events: List[Dict], ledger_rows: List[Dict],
         run_id: Optional[str]) -> Dict[str, Any]:
    book = replay_book(rows, resim, cal)
    corrections = book["corrections"] + late_marks(book["final"], events, cal)
    redates = ledger_redates(ledger_rows, events)
    moved = {x["ledger_id"]: x["session_date"] for x in redates}
    table = table_events([dict(r, session_date=moved.get(r["id"], r["session_date"])) for r in ledger_rows])
    after = list(book["final"].values()) + [dict(r, status="void") for r in rows if r["status"] == "void"]
    for c in corrections:
        if c["change"] == "void":
            after.append({**next(r for r in rows if r["id"] == c["id"]), "status": "void"})
    doc = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "corrections": corrections,
           "ledger_redates": redates, "jsonl": ledger_fixes(book["final"], book["corrections"], events),
           "swing_ledger": ledger_fixes(book["final"], book["corrections"], table),
           "resimulated": len(resim), "rebased": sum(1 for v in resim.values() if v.get("ratio", 1.0) != 1.0)}
    doc["counts"] = counts(rows, after, doc)
    return doc


def counts(before: List[Dict], after: List[Dict], doc: Dict[str, Any]) -> Dict[str, Any]:
    def state(rows):
        live = [r for r in rows if r["status"] != "void"]
        o = [r for r in live if r["status"] == "open"]
        per_pair = Counter((r["sleeve"], r["ticker"]) for r in o)
        return {"open_by_sleeve": dict(sorted(Counter(r["sleeve"] for r in o).items())),
                "pairs_with_several_open": sum(1 for v in per_pair.values() if v > 1),
                "void": sum(1 for r in rows if r["status"] == "void")}
    ch = Counter(c["change"] for c in doc["corrections"])
    return {"before": state(before), "after": state(list({r["id"]: r for r in after}.values())), "changes": dict(ch),
            "void_by_sleeve": dict(Counter(c["target"]["sleeve"] for c in doc["corrections"] if c["change"] == "void")),
            "jsonl": {k: sum(x["n"] for x in v) for k, v in doc["jsonl"].items()},
            "swing_ledger": {k: sum(x["n"] for x in v) for k, v in doc["swing_ledger"].items()},
            "ledger_redates": len(doc["ledger_redates"])}


# ---------------------------------------------------------------------------- apply
def _line(c: Dict[str, Any], run_id: str, now: str) -> Dict[str, Any]:
    session = c.get("session_date") or (c["target"].get("exit_session") if c["change"] in ("reopen", "exit_changed")
                                        else c["target"]["signal_date"])
    return {"timestamp": now, "logged_at": now, "session_date": session, "action": "CORRECTION",
            "sleeve": c["target"]["sleeve"], "ticker": c["target"]["ticker"], "target": c["target"],
            "change": c["change"], **({"new": c["new"]} if c.get("new") else {}), "reason": c["reason"],
            "price": c.get("price"), "run_id": run_id}


def ledger_lines(doc: Dict[str, Any], source: str, rows_by_key: Callable, run_id: str, now: str) -> List[Dict[str, Any]]:
    """CORRECTION lines for the signal corrections and one source's duplicates, BUY/SELL lines for its gaps."""
    out = [_line(c, run_id, now) for c in doc["corrections"]]
    for d in doc[source]["duplicate"]:
        tgt = {"sleeve": d["sleeve"], "ticker": d["ticker"], "action": d["action"], "session_date": d["session_date"],
               "extra": d["n"]}
        out.append(_line({"change": "duplicate", "target": tgt, "reason": f"{d['action']} logged {d['n']} extra time(s)",
                          "session_date": d["session_date"], "price": None}, run_id, now))
    for m in doc[source]["missing"]:
        r = rows_by_key(m)
        out += [{"timestamp": now, "logged_at": now, "session_date": m["session_date"], "action": m["action"],
                 "ticker": m["ticker"], "sleeve": m["sleeve"], "signal_date": r["signal_date"] if r else None,
                 "price": (r or {}).get("entry_signal_close" if m["action"] == "BUY" else "exit_price"), "units": 1.0,
                 "details": {"late": True, "backfilled_by": run_id}}] * m["n"]
    return out


def apply(doc: Dict[str, Any], client, branch_ledger_dir: str, backup_dir: str) -> Dict[str, int]:
    """Back up the three swing tables, then write the plan to Supabase and the jsonl files."""
    from jobs.generate_swing_signals import fetch_rows
    run_id, now = str(doc["run_id"]), datetime.now(timezone.utc).isoformat()
    os.makedirs(backup_dir, exist_ok=True)
    for t in ("swing_signals", "swing_ledger", "swing_events"):
        rows = fetch_rows(client, t)
        path = os.path.join(backup_dir, f"{t}.json")
        json.dump(rows, open(path, "w", encoding="utf-8"), default=str)
        if len(json.load(open(path, encoding="utf-8"))) != len(rows):
            raise SystemExit(f"Backup of {t} did not read back: nothing written.")
    signals = {r["id"]: r for r in fetch_rows(client, "swing_signals")}
    for c in doc["corrections"]:
        r = signals[c["id"]]
        mark = {"change": c["change"], "reason": c["reason"], "run_id": run_id, "at": now}
        if c["change"] == "void":
            upd = {"status": "void", "details": dict(r.get("details") or {}, void=mark)}
        elif c["change"] == "reopen":
            upd = {"status": "open", **{k: None for k in EXIT_FIELDS if k not in ("fill_date", "entry_fill_price")},
                   "details": dict(r.get("details") or {}, reopened=dict(mark, old_exit_date=r["exit_date"], old_exit_reason=r["exit_reason"]))}
        elif c["change"] == "exit_changed":
            upd = {**c["fields"], "details": dict(r.get("details") or {},
                                                  exit_changed=dict(mark, old_exit_date=r["exit_date"], old_exit_reason=r["exit_reason"]))}
        elif c["event"] == "SELL":   # late: the nightly's keys (a late BUY has its own, so an on-time SELL is not labelled)
            upd = {"details": dict(r.get("details") or {}, late=True, late_sessions=c["new"]["late_sessions"],
                                   logged_at=c["new"]["logged_at"])}
        else:
            upd = {"details": dict(r.get("details") or {}, late_buy=True, buy_logged_at=c["new"]["logged_at"])}
        client.table("swing_signals").update(dict(upd, updated_at=now)).eq("id", c["id"]).execute()
        signals[c["id"]] = dict(r, **upd)
    for x in doc["ledger_redates"]:
        row = client.table("swing_ledger").select("details").eq("id", x["ledger_id"]).execute().data[0]
        client.table("swing_ledger").update({"session_date": x["session_date"], "details": dict(
            row.get("details") or {}, run_session=x["run_session"], redated_by=run_id)}).eq("id", x["ledger_id"]).execute()

    def row_for(m):
        return next((r for r in signals.values() if r["sleeve"] == m["sleeve"] and r["ticker"] == m["ticker"]
                     and r["status"] != "void" and (r["signal_date"] if m["action"] == "BUY" else r.get("exit_date")) == m["session_date"]), None)

    written = {"swing_ledger": 0, "jsonl": 0}
    for e in ledger_lines(doc, "swing_ledger", row_for, run_id, now):
        d = {k: e[k] for k in ("target", "change", "new", "reason", "run_id") if k in e} if e["action"] == "CORRECTION" else e["details"]
        client.table("swing_ledger").insert({"session_date": e["session_date"], "action": e["action"], "ticker": e["ticker"],
                                             "sleeve": e["sleeve"], "price": e.get("price") or 0.0, "units": 1.0,
                                             "details": dict(d, **({"signal_date": e["signal_date"]} if e.get("signal_date") else {}))}).execute()
        written["swing_ledger"] += 1
    by_file = defaultdict(list)
    for e in ledger_lines(doc, "jsonl", row_for, run_id, now):
        by_file[e["session_date"]].append(e)
    for session, lines in by_file.items():
        with open(os.path.join(branch_ledger_dir, f"{session}.jsonl"), "a", encoding="utf-8") as f:
            f.writelines(json.dumps(e, default=str) + "\n" for e in lines)
        written["jsonl"] += len(lines)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ledger-dir", default=None, help="a signals-ledger worktree's ledger/ directory (with --apply)")
    a = ap.parse_args()
    doc = json.load(open(a.plan, encoding="utf-8"))
    print(json.dumps({"run_id": doc["run_id"], **doc["counts"]}, indent=1))
    if not a.apply:
        return 0
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.supabase_client import get_client
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(json.dumps(apply(doc, get_client(), a.ledger_dir, os.path.join(PROJECT_ROOT, "backups", f"swing_{stamp}"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
