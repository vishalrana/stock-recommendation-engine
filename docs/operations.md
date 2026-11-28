# Operations

Moved verbatim from CLAUDE.md on 2026-10-14; CLAUDE.md holds the binding rules and links here.

## Operations (GitHub Actions)

- The repo is public, so GitHub Actions minutes on standard runners are free; the free plan's minute quota applies only to private repos. The GitHub CLI is installed and logged in (`C:\Program Files\GitHub CLI\gh.exe`, scopes repo and workflow). Use it to dispatch workflows (`gh workflow run`), read logs (`gh run view --log`) and manage secrets (`gh secret set`).
- **Nightly scan timing (2026-10-10, about 8.4 min for the scan step):**

  | Part | Time |
  |---|---|
  | Price download (5,598 tickers) | 277 s |
  | Post-Earnings Drift scan | 110 s |
  | Indicators and Pullback scan | 47 s |
  | Scoring and context | 27 s |

  - Drift: the scan answers from the preloaded earnings calendar instead of making one Supabase query per ticker.
  - Download: it uses 200-ticker chunks with a 0.25 s pause instead of 100-ticker chunks with a 1 s sleep, and re-requests a 7-day overlap so a failed chunk heals the next night (same request count). yfinance throughput did not rise with more threads or concurrent chunks (tested), so those were not changed.
- **Per-ticker freshness gate:** a ticker whose latest cached bar is older than the market session is never evaluated. Provider health reports `stale_tickers_skipped`.
- **Refresh Current Ideas** restores the nightly's `data-cache-v2-*` cache and never saves one. It used its own nearly empty `v1` cache, so every idea was skipped for having too few bars and Refresh changed nothing. It now downloads its few tickers up to the market date. Its lifecycle safeguard requires current price data for every target, not strategy eligibility; before this, one open idea under the liquidity floor (AII) blocked updates for all of them. Verified 2026-10-10 in run 38056801266 (1 min 46 s): all 30 ideas reconciled, LILAK closed at its stop, GDDY reached T1, and SEC fundamentals were written for 29 of 29 open ideas.
- **Refresh button (site):**
  - It sends every active idea (the old cap of 30 is gone; the limit is now 200) and polls for up to 15 minutes, showing the queued or running state and the elapsed time.
  - Each outcome has its own message with a link to the run.
  - After success, the cards adopt the refreshed server data. They used to keep a `useState` copy seeded once, so the page showed the pre-refresh list until a full reload.
- If a GitHub run page keeps showing "in progress" after a run finished, check `gh run view <id>`. On 2026-10-10 a 46-second refresh run still showed as running in the browser after 9 minutes.

## Swing signals operations

#### Operations
- Nightly workflow: `.github/workflows/swing_signals.yml`, schedule `20 1 * * 2-6` (01:20 UTC, Tuesday–Saturday, after each US session).
- Ledger: daily events go to `ledger/YYYY-MM-DD.jsonl` on the orphan branch `signals-ledger`.
- Research: `.github/workflows/swing_research.yml`, which has data, replication, a per-sleeve matrix and decide jobs. The first full run takes about 1 h with a warm cache and about 5–6 h cold.
- Optional secrets: `APPRISE_URLS` (alerts; LIVE sleeves only), `GROQ_API_KEY` / `GEMINI_API_KEY` (shadow LLM labels), `FINNHUB_API_KEY` / `FMP_API_KEY` (analyst archive). Missing secrets degrade to unavailable. As of 2026-10-14 only `SEC_USER_AGENT`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and `TIINGO_API_KEY` are set.

## Database reset (2026-10-14)

- See `docs/research-history.md`, Status log, entry 2026-10-14, for the reason, the backup and the row counts.

## Parity tooling and the nightly schedule (2026-10-20)

- `parity_replay.yml` (manual, input `session`; and every Saturday 12:00 UTC for the latest session of the past week whose nightly succeeded, usually Friday's): replays one live Ideas session through the harness and compares the decisions (`scripts/parity_replay.py`); about 25 minutes (prices and SEC filings re-downloaded; run 39384095078 took 23). A session whose nightly status is not `success` is not replayed (a warning in the run).
- `scripts/dedupe_signals_history.py <dates>`: dry run by default; `--yes` backs up `signals_history` and removes earlier runs' rejected rows for those scan dates.
- The nightly cron is 01:00 UTC Tuesday–Saturday, but GitHub starts scheduled runs hours late: a median of 4.7 h in mid-October and 9.2 h in the week of 2026-11-23 (see "Late starts" below). A run whose bars stop before the latest completed session is `stale_data` and publishes nothing.
- Second attempt (2026-10-20, user decision): cron `0 4 * * 2-6`. A scheduled run exits before any download when `scan_log` already has a `success` row for the latest completed session (`generate_signals.session_already_succeeded`, `SKIP_IF_SESSION_DONE`); any other status (`stale_data`, `degraded_sec`, ...) or an unreadable `scan_log` scans again. The two runs share the `nightly-scan` concurrency group, so they never overlap; `validate-outcomes` is skipped when the scan was. Manual dispatches always scan.
- Weekly full refresh: added 2026-10-20 (Saturday `--cache-mode force`) and reverted 2026-10-21 before its first run: it wiped the cache inside the job that scans Friday's session, nothing checked completeness, and a partial download would have been restored all week. Every nightly is incremental. Re-adjusted history is handled outside the scan (see the status log, "Data handling after the forward-test start"). `CACHE_MODE` in the environment is never read; the mode is passed on the command line, and `--force-refresh` wins over the default.
- `price_refresh_timing.yml` ("Price cache tools", no Supabase): `time` restores the nightly cache and times `refresh_with_actions` (not saved; `--actions-since` for a heavier night; `--screen` for the live Cross-Sectional ranks on a session); `rebuild` downloads the full adjusted history into a fresh directory and saves it as the newest nightly cache only if coverage, history length and the last bar pass. Every writer of the nightly cache (the nightly, this workflow and `refresh_earnings.yml`, since #75) is in the `nightly-scan` concurrency group, so none overlaps another and the newest saved cache is the one restored. Rebuilt once on 2026-10-21 (run 39634873548). Since 2026-10-22: the cron fires every Sunday at 12:00 UTC and the job skips at "Monthly only" except on the first Sunday of the month, when it rebuilds (mode `rebuild`, first scheduled rebuild 41333457607 on 2026-11-01), with a drift report in the job summary; input `cache_key` restores an exact saved cache for diagnostics; `--screen` writes the live ranking (artifact `price-cache-tools`). `parity_replay.yml` exits 1 when the issued sets differ or a 65+ candidate's stage differs, and writes the difference table to the job summary.

## Workflow health check (2026-11-28)

`workflow_health.yml` runs `scripts/workflow_health.py` daily at 14:07 UTC, before the US open, and on dispatch. It needs `actions: write` only to re-enable a workflow. A failure makes GitHub email the owner. With `APPRISE_URLS` set, the same text also goes out through Apprise; that secret isn't set yet.

| Check | Fails when |
|---|---|
| Workflow states | A workflow with a cron isn't `active`. One disabled for inactivity (`disabled_inactivity`) is re-enabled through the API and still reported. One disabled by hand is reported and left as it is. |
| Nightly | The latest completed NYSE session has no `success` row in `scan_log`, and no non-success row with a run id. |
| Swing | `swing_sleeve_status` has no processed marker for the latest completed session (`details.last_session` or `processed_session`). |
| Weekly runs | One of these has no non-cancelled run since its slot, checked the next day: Saturday's parity replay and forward-test review, Sunday's earnings refresh and cache tools. On a month's first Sunday, the cache rebuild must also have run and passed. |
| Late starts (warning only) | A nightly or swing run of the last day was created more than 6 h after its cron. Since November this warns most days (see below). |

When it fails:
- **A workflow isn't active.** For `disabled_inactivity`, nothing to do: it was re-enabled, and the next slot fires. For `disabled_manually`, look for the reason in the repo and the Actions UI. Re-enable it (`gh workflow enable <file>`) only if nothing records a reason.
- **No successful nightly for the session.**
  - Look at the session's run with `gh run list --workflow nightly_scan.yml` and its `scan_log` row. `stale_data`, `degraded_sec` and `degraded_universe` say why in `error_message`.
  - The 04:00 UTC retry rescans any non-success. If both runs missed, dispatch `gh workflow run nightly_scan.yml` before the next session's open.
  - A session that can't be scanned stays a gap. It is never backfilled into LIVE.
- **No swing marker.**
  - Read the latest `swing_signals.yml` run's job summary, which lists unreconciled rows with their reasons.
  - A dispatch (`gh workflow run swing_signals.yml`) is safe to repeat: the processed-session guard issues no BUY twice, and the ledgers are idempotent.
  - Its "Ledger consistency" step must pass.
- **A weekly run is missing.** Dispatch it (`gh workflow run <file>`). For a failed first-Sunday rebuild, dispatch `price_refresh_timing.yml` with mode `rebuild`, outside the nightly's hours.
- **The check itself couldn't run** (an API error or Supabase unreachable). It fails closed. Rerun it with `gh run rerun <id>`.

## Forward-test reviews (2026-11-28)

- `forward_test_review.yml` runs `scripts/forward_test_review.py` on Saturdays at 13:17 UTC.
  - It reviews only when a date in `docs/forward-test-reviews.md` (R1 2027-01-16, R2 2027-04-17, R3 2027-10-16, R4 2028-10-14) has passed and `docs/forward-test-reviews/<date>.md` doesn't exist yet. Otherwise it says no review is due and stops.
  - A review first runs the swing job read-only (`--dry-run --review-report`, swing cache restored, never saved), then the Ideas review.
  - It commits the report, with its run id, to main.
  - On STOP or END it also commits `config/forward_test_status.json` and `frontend/src/lib/forward-test-status.json`, and the app shows the banner after the Vercel deploy.
- Dispatch inputs:
  - `dry_run`: fixtures only, no live outcomes, no commit. Dry run 46159215427.
  - `as_of`: run as of a date.
- Inputs: `data/forward_test/backtest_40205392302_trades.csv` (for the STOP paths; sha256 in that folder's README) and `replay_ideas.jsonl`, which is empty because the forward test has no gap after 2026-10-21.

## Swing ledger consistency and corrections (2026-11-28)

- Every swing run ends with `scripts/check_swing_ledger.py --ref origin/signals-ledger`. The check requires:
  - every `swing_signals` row has exactly one BUY in the jsonl and in `swing_ledger`;
  - every closed row has exactly one SELL, dated by its exit session;
  - `void` rows are excluded.

  It runs even when the engine step fails, but not on dry runs.
- Corrections are never edits.
  - `gh workflow run swing_signals.yml -f plan_corrections=true` writes the plan as the artifact `swing-corrections-plan`. It reads Supabase and writes nothing.
  - `python scripts/correct_swing_records.py <plan> --apply --ledger-dir <signals-ledger worktree>/ledger` first backs up `swing_signals`, `swing_ledger` and `swing_events` to `backups/swing_<UTC>/` (local) and checks the read-back. It then updates Supabase and appends CORRECTION lines to the jsonl. Commit and push those lines to `signals-ledger`.
  - The first correction: plan 46156151999, backup `backups/swing_20261128T150843Z/`, ledger commit `c28bf1f`. See `docs/research-history.md`.

## Late starts and an external trigger (D6: documented, not set up)

GitHub creates scheduled runs late, and the delay is growing. This is run `created_at` minus the cron slot, for every scheduled run since 2026-10-14 (weekly table in `docs/research-history.md`):

| Workflow | Runs | Median | p90 | Max | Week of 2026-11-23, median |
|---|---|---|---|---|---|
| `nightly_scan.yml` (01:00 and 04:00 UTC) | 63 | 6.9 h | 8.8 h | 10.7 h | 9.2 h |
| `swing_signals.yml` (01:20 UTC) | 34 | 6.4 h | 8.1 h | 9.6 h | 8.4 h |

The median is over 2 h, so this is the setup for an external trigger if the delay starts to matter. It matters once a nightly would start after 13:30 UTC, an hour before the US open (14:30 UTC; 13:30 UTC in summer):

1. **Token.** Create a fine-grained personal access token at GitHub, Settings, Developer settings, Fine-grained tokens:
   - repository access: only `stock-recommendation-engine`;
   - permissions: Actions read and write, and Metadata read (added automatically); nothing else;
   - an expiry date of 90 days or less, with a reminder to rotate it.
2. **Caller.** Store the token in the external scheduler's secret store, never in this repo, and call `workflow_dispatch` at the cron times:

   ```
   POST https://api.github.com/repos/vishalrana/stock-recommendation-engine/actions/workflows/nightly_scan.yml/dispatches
   Authorization: Bearer <token>
   Accept: application/vnd.github+json
   {"ref": "main"}
   ```

   For the swing job, use `swing_signals.yml`, with `{"ref": "main", "inputs": {"dry_run": "false"}}`. Any scheduler that sends an HTTPS POST at a fixed UTC time will do, for example a cron-job service or a cloud function on a timer.
3. **Keep the GitHub crons as a fallback.**
   - A dispatched nightly always scans. The late scheduled runs (01:00 and 04:00) then exit before any download for a session that already succeeded, because `SKIP_IF_SESSION_DONE` applies to scheduled runs.
   - The swing job's processed-session guard makes a second run for the same session issue nothing.
   - Both workflows' concurrency groups keep a dispatched run and a late scheduled run from overlapping.
4. **Check.** For a week, the health check's late-start warnings should stop. Revoke the token if the trigger is removed.
