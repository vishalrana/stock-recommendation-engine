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

- `parity_replay.yml` (manual, input `session`): replays one live Ideas session through the harness and compares the decisions (`scripts/parity_replay.py`); about 25 minutes (prices and SEC filings re-downloaded; run 39384095078 took 23).
- `scripts/dedupe_signals_history.py <dates>`: dry run by default; `--yes` backs up `signals_history` and removes earlier runs' rejected rows for those scan dates.
- The nightly cron is 01:00 UTC Tuesday–Saturday, but GitHub has been starting it around 05:30 UTC. A run whose bars stop before the latest completed session is now `stale_data` and publishes nothing.
- Proposed, not added: a second scheduled attempt (for example `0 4 * * 2-6`) that exits at once when `scan_log` already has a `success` row for the expected session, so a stale first run does not lose the session.
