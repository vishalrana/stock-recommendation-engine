"""
Workflow health check (.github/workflows/workflow_health.yml, daily 14:07 UTC, before the US open)
===================================================================================================
Fails, so GitHub emails the owner, and writes a job summary when:
  - a scheduled workflow is not active. GitHub disables a public repo's scheduled workflows after
    60 days without activity (disabled_inactivity): those are re-enabled through the API and still
    reported. One disabled by hand (disabled_manually) is reported and never re-enabled;
  - the latest completed NYSE session has no successful nightly in scan_log, and no non-success row
    documented with a run id;
  - the swing job has not processed the latest completed session (its per-session marker);
  - a weekly run is missing at the next day's check: Saturday's parity replay and forward-test review,
    Sunday's earnings refresh and cache-tools run (on a month's first Sunday, the rebuild must have run
    and passed).
It warns, without failing, when a nightly or swing run started more than 6 hours after its cron.
With APPRISE_URLS set, the same text also goes out through Apprise.
"""

import datetime as dt
import glob
import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

import requests
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

UTC = dt.timezone.utc
REPO = os.environ.get("GITHUB_REPOSITORY", "vishalrana/stock-recommendation-engine")
WEEKLY = ("parity_replay.yml", "refresh_earnings.yml", "price_refresh_timing.yml", "forward_test_review.yml")
LATE_CHECKED = ("nightly_scan.yml", "swing_signals.yml")
LATE_START = dt.timedelta(hours=6)
RUN_ID = re.compile(r"\b\d{10,12}\b")


def crons(root: str = PROJECT_ROOT) -> Dict[str, List[str]]:
    """Workflow file name -> its schedule crons (PyYAML reads the bare key `on` as True)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yml"))):
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        on = doc.get("on", doc.get(True)) or {}
        sched = on.get("schedule") if isinstance(on, dict) else None
        if sched:
            out[os.path.basename(path)] = [s["cron"] for s in sched]
    return out


def last_fire(cron: str, now: dt.datetime) -> dt.datetime:
    """The latest time at or before `now` (UTC) that `cron` fires.
    shortcut: fixed minute and hour, '*' day of month and month, day-of-week lists or ranges only
    (every cron in this repo, checked by a test); a cron with steps needs a real parser."""
    minute, hour, dom, month, dow = cron.split()
    assert dom == "*" and month == "*", cron
    days = set()
    for part in ("0-6" if dow == "*" else dow).split(","):
        a, _, b = part.partition("-")
        days |= {d % 7 for d in range(int(a), int(b or a) + 1)}      # cron: 0 and 7 are Sunday
    t = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    while t > now or t.isoweekday() % 7 not in days:
        t -= dt.timedelta(days=1)
    return t


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class GitHub:
    """The few Actions API calls the check needs (GITHUB_TOKEN; actions: write to re-enable)."""

    def __init__(self, token: Optional[str], repo: str = REPO):
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
        self.base = f"https://api.github.com/repos/{repo}"

    def get(self, path: str, **params) -> dict:
        r = self.s.get(self.base + path, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def put(self, path: str) -> None:
        self.s.put(self.base + path, timeout=30).raise_for_status()

    def workflows(self) -> List[dict]:
        return self.get("/actions/workflows", per_page=100)["workflows"]

    def runs(self, workflow: str, since: dt.datetime) -> List[dict]:
        return self.get(f"/actions/workflows/{workflow}/runs", per_page=100,
                        created=f">={since:%Y-%m-%dT%H:%M:%SZ}").get("workflow_runs", [])

    def jobs(self, run_id: int) -> List[dict]:
        return self.get(f"/actions/runs/{run_id}/jobs").get("jobs", [])


def check_states(gh, scheduled: Dict[str, List[str]]) -> List[str]:
    """Problems with the scheduled workflows' states; re-enables those GitHub disabled for inactivity."""
    fails = []
    by_name = {os.path.basename(w["path"]): w for w in gh.workflows()}
    for name in sorted(scheduled):
        w = by_name.get(name)
        if w is None:
            fails.append(f"{name}: not registered with GitHub Actions")
        elif w["state"] == "disabled_inactivity":
            gh.put(f"/actions/workflows/{w['id']}/enable")
            fails.append(f"{name}: GitHub disabled it after 60 days without repository activity; re-enabled through the API")
        elif w["state"] != "active":
            fails.append(f"{name}: state {w['state']}; not re-enabled (only inactivity is undone automatically)")
    return fails


def _rebuilt(gh, run: dict) -> bool:
    """A first-Sunday cache-tools run that ran the rebuild step and passed its checks."""
    steps = [s for j in gh.jobs(run["id"]) for s in j.get("steps", [])]
    return run.get("conclusion") == "success" and any(s["name"] == "Run" and s["conclusion"] == "success" for s in steps)


def check_weekly(gh, scheduled: Dict[str, List[str]], now: dt.datetime) -> List[str]:
    """Weekly runs missing at the next day's check (a slot at least 24 hours old with no run since)."""
    fails = []
    for name in WEEKLY:
        for cron in scheduled.get(name, []):
            due = last_fire(cron, now - dt.timedelta(days=1))
            runs = [r for r in gh.runs(name, due) if r.get("conclusion") != "cancelled"]   # a cancelled run did nothing
            if not runs:
                fails.append(f"{name}: no run since its {due:%a %Y-%m-%d %H:%M} UTC slot")
            elif name == "price_refresh_timing.yml" and due.day <= 7 and not any(
                    _rebuilt(gh, r) for r in runs if r["event"] == "schedule"):
                fails.append(f"{name}: the first-Sunday rebuild of {due:%Y-%m-%d} did not run or failed its checks")
    return fails


def nightly_run_for(gh, created_at: dt.datetime) -> Optional[int]:
    """The nightly run whose span holds a scan_log row's write time."""
    for r in gh.runs("nightly_scan.yml", created_at - dt.timedelta(hours=3)):
        start, end = _ts(r.get("run_started_at") or r["created_at"]), _ts(r["updated_at"])
        if start <= created_at <= end:
            return r["id"]
    return None


def check_nightly(client, gh, session: dt.date) -> Tuple[List[str], List[str]]:
    """(fails, notes) for the session's scan_log row: success, or a non-success documented with a run id."""
    rows = (client.table("scan_log").select("scan_date,status,created_at,error_message")
            .eq("scan_date", session.isoformat()).execute().data or [])
    if not rows:
        return [f"nightly: no scan_log row for session {session}"], []
    row = max(rows, key=lambda r: r["created_at"])
    run = nightly_run_for(gh, _ts(row["created_at"]))
    if row["status"] == "success":
        return [], [f"nightly: session {session} success (run {run or 'not found'})"]
    cited = RUN_ID.search(row.get("error_message") or "")
    if run or cited:
        return [], [f"nightly: session {session} {row['status']}, documented (run {run or cited.group(0)}): "
                    f"{(row.get('error_message') or '')[:200]}"]
    return [f"nightly: session {session} is {row['status']} with no run id to document it"], []


def slot_of_runs(runs: List[dict], cron_list: List[str]) -> List[Tuple[dict, dt.datetime]]:
    """Each schedule run paired with the cron slot it fired for. GitHub does not record the slot and now
    creates every nightly run after 04:00 UTC, so "the latest slot before it" would date both of a day's
    runs to 04:00: runs are paired with that UTC day's slots in creation order (the extra ones with the last)."""
    out, by_day = [], {}
    for r in sorted((r for r in runs if r["event"] == "schedule"), key=lambda r: r["created_at"]):
        by_day.setdefault(_ts(r["created_at"]).date(), []).append(r)
    for day, day_runs in by_day.items():
        end = dt.datetime.combine(day, dt.time(23, 59), UTC)
        slots = sorted({t for t in (last_fire(c, end) for c in cron_list) if t.date() == day})
        for i, r in enumerate(day_runs):
            if slots:
                out.append((r, slots[min(i, len(slots) - 1)]))
    return out


def swing_marker(client) -> Callable[[dt.date], Optional[str]]:
    """The swing job's processed marker: every run writes swing_sleeve_status.details.last_session (with
    stale=true when SPY's bar is old), and processed_session per sleeve, even when it issues nothing."""
    def processed(session: dt.date) -> Optional[str]:
        rows = client.table("swing_sleeve_status").select("sleeve,details").execute().data or []
        if not any((r.get("details") or {}).get("last_session") == session.isoformat()
                   and not (r.get("details") or {}).get("stale") for r in rows):
            return None
        lag = sorted(r["sleeve"] for r in rows if (r.get("details") or {}).get("processed_session")
                     not in (None, session.isoformat()))
        return "swing_sleeve_status last_session" + (f"; no BUY scan yet on it for sleeve(s) {', '.join(lag)}" if lag else "")
    return processed


def late_starts(gh, scheduled: Dict[str, List[str]], now: dt.datetime) -> List[str]:
    """Warnings: scheduled nightly and swing runs of the last day that started over 6 hours after their cron."""
    warns = []
    day_start = dt.datetime.combine((now - dt.timedelta(days=1)).date(), dt.time(0), UTC)   # whole UTC days
    for name in LATE_CHECKED:
        for r, slot in slot_of_runs(gh.runs(name, day_start), scheduled.get(name, [])):
            delay = _ts(r["created_at"]) - slot
            if delay > LATE_START and _ts(r["created_at"]) > now - dt.timedelta(days=1):   # each run once
                warns.append(f"{name}: run {r['id']} started {delay.total_seconds() / 3600:.1f} h "
                             f"after its {slot:%a %H:%M} UTC cron")
    return warns


def notify(text: str, failed: bool) -> str:
    """Send through Apprise when APPRISE_URLS is set; say so when it is not."""
    urls = [u.strip() for u in os.environ.get("APPRISE_URLS", "").split(",") if u.strip()]
    if not urls:
        return "APPRISE_URLS is not set: no Apprise message (GitHub's failed-run email is the alert)."
    import apprise
    ap = apprise.Apprise()
    for u in urls:
        ap.add(u)
    ok = ap.notify(title=f"Workflow health: {'FAILED' if failed else 'warnings'}", body=text)
    return "Apprise: sent." if ok else "Apprise: sending failed."


def run_checks(gh, client, now: dt.datetime, session: dt.date, scheduled: Dict[str, List[str]],
               swing_processed: Callable[[dt.date], Optional[str]]) -> Tuple[List[str], List[str], List[str]]:
    """(fails, warns, notes). A check that cannot run is a failure (fail closed)."""
    fails, warns, notes = [], [], []
    for name, fn in (("workflow states", lambda: check_states(gh, scheduled)),
                     ("weekly runs", lambda: check_weekly(gh, scheduled, now))):
        try:
            fails += fn()
        except Exception as e:
            fails.append(f"{name}: the check could not run ({type(e).__name__}: {e})")
    try:
        f, n = check_nightly(client, gh, session)
        fails += f
        notes += n
    except Exception as e:
        fails.append(f"nightly: the check could not run ({type(e).__name__}: {e})")
    try:
        marker = swing_processed(session)
        if marker:
            notes.append(f"swing: session {session} processed ({marker})")
        else:
            fails.append(f"swing: session {session} has no processed marker")
    except Exception as e:
        fails.append(f"swing: the check could not run ({type(e).__name__}: {e})")
    try:
        warns += late_starts(gh, scheduled, now)
    except Exception as e:
        warns.append(f"late starts: the check could not run ({type(e).__name__}: {e})")
    return fails, warns, notes


def summary(now: dt.datetime, session: dt.date, fails: List[str], warns: List[str], notes: List[str], sent: str) -> str:
    lines = [f"### Workflow health {now:%Y-%m-%d %H:%M} UTC: {'FAILED' if fails else 'OK'}", "",
             f"Latest completed session: {session}.", ""]
    for title, items in (("Failures", fails), ("Warnings (no failure)", warns), ("Checked", notes)):
        if items:
            lines += [f"**{title}**", ""] + [f"- {x}" for x in items] + [""]
    return "\n".join(lines + [sent, ""])


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from jobs.supabase_client import get_client
    from src.utils.market_date import latest_completed_session
    now = dt.datetime.now(UTC)
    session = latest_completed_session(now)
    gh, client = GitHub(os.environ.get("GITHUB_TOKEN")), get_client()
    fails, warns, notes = run_checks(gh, client, now, session, crons(), swing_processed=swing_marker(client))
    text = summary(now, session, fails, warns, notes, "")
    sent = notify(text, bool(fails)) if (fails or warns) else "No failures or warnings: no message sent."
    text = summary(now, session, fails, warns, notes, sent)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
