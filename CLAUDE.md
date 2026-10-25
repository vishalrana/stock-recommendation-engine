# Stock Recommendation Engine — Working Guide

This file holds the scope, the binding rules and the current state. Detailed records live in `docs/` (see "Where things live"); add history and results there, and change this file only for rules and state.

## What this is

A personal-use, quantitative research and recommendation engine for US equities. It scans a stock universe, applies the configured strategies, scores and ranks setups, calculates indicative entry, stop-loss, target and risk/reward levels and target-reach probabilities, and publishes recommendations. The user reviews the output and decides independently whether to trade.

## What this is not

- Not a trading bot: no order placement or brokerage execution.
- Not a portfolio manager: no position sizing, Kelly sizing, capital allocation or portfolio-state logic.
  - **Exception (adopted 2026-10-11, profitability program Phase 6):** display-only sizing guidance on the cards (hidden while Ideas are SHADOW, `frontend/src/lib/ideas-mode.ts`).
    - Shares are floor(account × risk % ÷ (entry − stop)), capped at the account size.
    - The account size and risk per idea are stored only in the viewer's browser (`localStorage`, `frontend/src/lib/sizing.ts`). They are never sent to the server, stored in Supabase, or read by the engine.
    - Nothing is ordered, allocated or tracked as a portfolio.
- Not a commercial or multi-tenant product.
- Not a security-hardening project: keep security proportionate to personal use.
- Not proof of profitability: passing tests or plausible-looking output does not establish a trading edge.

## Architecture

- **Python engine**: universe discovery, data processing, indicators, regime, strategies, scoring, risk and targets, recommendation generation.
- **GitHub Actions**: scheduled scans and automated checks.
- **Supabase**: stores recommendations and operational data.
- **Vercel app (`frontend/`)**: displays results only. It must never run the Python engine.
- **UX**: show ideas first, with deeper analysis on request. "Refresh" re-evaluates active tickers; it does not trigger a full-universe scan.

## Priority order

1. Mathematical and quantitative correctness.
2. Correct strategy qualification, scoring and ranking.
3. Recommendation lifecycle and history integrity.
4. Reliable data handling and scheduled execution.
5. Valid backtesting and evidence of a genuine edge.
6. A simple, useful personal interface.
7. Security and infrastructure, only where proportionate and necessary.

Get the maths and recommendation logic right first. Do not add complexity for its own sake.

## Rules

- **Analytics are not qualifiers.** Risk/reward, target-reach probabilities, targets and stop-losses are informational outputs. They must not qualify or reject a recommendation when qualification is meant to be score-based. A stock qualifies because it meets the defined strategy criteria. Flag any code where these metrics act as hidden gates.
  - **Exception (user decision 2026-10-11, not a tested result):** one explicit risk rule in `src/quant_config.py` (`src/trade_hygiene.py`), `STOP_CAP_ENABLED = True`, rejects a *new* idea if:
    - its stop sits further below the entry than min(3 × ATR14, 10% of entry).
    The R:R floor (`RR_FLOOR_ENABLED = False`, split from `RR_FLOOR_STOP_CAP_ENABLED`) was turned off on 2026-10-11 by user decision (this is a user decision, not a tested result; it affects only the Ideas product).
  - It acts only after the score has qualified the idea. Tiers stay score-based (`assign_tier`). It never closes or changes an open idea. Reach probabilities remain display-only. No other analytic may gate.
- **Check calculations rigorously**: RSI/ATR and other indicators (inputs, conventions, edge cases); scores, thresholds, ranking and filters; entry, stop, target and R:R; probabilities (validity, sample size, calibration); backtest execution; scale-outs and same-bar stop/target collisions; lifecycle transitions; timestamps, ticker identity and data provenance.
- **Present probabilities honestly**: they must be mathematically valid, calibrated and backed by enough data, and never presented as certainties.
- **Lifecycle integrity**: keep each recommendation's identity and status correct, preserve history, and never wrongly duplicate, replace or close a recommendation.
- **Backtests** must prevent look-ahead and survivorship bias, use realistic execution assumptions and trading costs, and validate out of sample.
- **Fail correctly**: on incomplete scans, stale or missing data, provider failures or thin samples, never silently publish misleading recommendations or corrupt existing records.
- **Strategy changes need evidence**: do not change the production strategy without backtest validation.

- **No look-ahead**: a decision at session t uses only bars through the close of t and filings accepted by the cutoff. Research and live share the same functions (`src/swing/`, `src/pipeline_steps.py`); keep the truncation and placeholder tests (`tests/test_swing_pit.py`, `scripts/test_no_lookahead.py`) passing.
- **Fail closed**: stale bars (latest bar older than the latest completed NYSE session), missing data or incomplete research inputs mean no new ideas or signals. Exits and lifecycle still reconcile. Never substitute constants for missing inputs.
- **Pre-registration**: research programs are pre-registered before any run. Their definitions and keep/kill rules are applied mechanically, every trial is logged in `config/research_trials.jsonl` (committed; each research workflow pushes its run's records with `scripts/persist_trials.py`), and parameters never change after results exist. Swing: `docs/swing-program.md`. Profitability program: `docs/research-history.md`.
- **Results come from GitHub runs**: adopt research or evidence files only from a successful, non-smoke GitHub Actions run, with the run id recorded in the file. Never report anything as passed or verified without a GitHub Actions run id showing success.
- **Never write "edge" or "profitable"** unless the pre-registered edge criteria pass.
- **Live = backtest (parity, since 2026-10-20)**: the nightly Ideas scan and the backtest harness apply the same rules through the same functions, and a rule change goes into both. Context (analyst, news) is display-only; every earnings date comes from SEC 8-K item 2.02 filings known at the decision (`src/earnings_pit.py`, `trade_hygiene.earnings_gate`); unknown dates are rejected. Check with `parity_replay.yml`.
- **Secrets and backups**: the repo is public. Never commit `.env`, keys, `backups/` or the SEC contact email; secrets live in `.env` and GitHub secrets only.

## Current state (2026-10-20)

- **Ideas** (nightly scan, `nightly_scan.yml`): **SHADOW** since 2026-10-20 (user decision). Still scanned and logged every night on the parity configuration (score without context; point-in-time SEC earnings dates with unknown dates rejected; full-hold earnings rule, event lock, stop cap; R:R floor off), shown under "Not validated - do not trade", never alerted (the Ideas scan has no alert path). Backtest of exactly this configuration (run 39451924637, with the shared Cross-Sectional screen): 1,660 ideas, about 34 a month (strongly seasonal: 0.7–0.8 a session in March, April and October), −0.77%/idea beta-adjusted (CI −1.37 to −0.23, below zero: the book loses to SPY after beta), LATE −0.95 (CI −2.06 to +0.07). Scoring evidence (`config/strategy_performance.json`) stays at run 39384088732 for the forward test; the cards' issued-idea figures come from run 39451924637.
- **Forward test**: starts with scan date 2026-10-19 (nightly run 39377064887, the first on the parity configuration); the configuration is final from scan date 2026-10-20. The 2026-10-19 scan ran the Cross-Sectional screen from before PR #20 and issued nothing, so no forward-test data is affected. Ideas issued before it (OBE, 2026-10-16) stay tracked but are excluded from forward-test evaluation. Scan date 2026-10-21 is a gap (the nightly scanned a stale cache and issued nothing; no backfill) and is left out of forward-test counts. Data-handling changes made after the start (price refetch after dividends and splits, stored levels rebased before replays) are logged in `docs/research-history.md`.
- **Swing signals** (`swing_signals.yml`, 01:20 UTC Tue–Sat): all five sleeves T, E, N, P and I are **SHADOW**. The pre-registered 2008–2022 holdout (run 38436737951) failed P1 for every sleeve, so they are shown under "Not validated - do not trade" and never alerted.
- **Data**: Supabase was reset on 2026-10-14 (backup `backups/supabase_20261013T230522Z/`, local only). Duplicate `signals_history` rows of 2026-10-12 and 2026-10-13 were removed on 2026-10-20 (backup `backups/supabase_20261020T064853Z/`). Details: `docs/research-history.md`.
- **Open items**: no `APPRISE_URLS` secret; sleeve I has no live signals until SEC publishes the 2026 Q2–Q4 insider data sets.

## Tests

- Python: `python scripts/run_all_regressions.py` (every suite; new tests must be registered there).
- Frontend (`frontend/`): `npm run lint`, `npm test`, `npm run build`.
- CI (`.github/workflows/ci.yml`) runs both on every push and PR. Merge nothing while CI is red.

## Where things live

| Topic | File |
|---|---|
| Data, lifecycle and strategy conventions | `docs/data-conventions.md` |
| Backtest harness and backtest results | `docs/backtest-results.md` |
| Research findings, profitability program, decision history, status log | `docs/research-history.md` |
| Swing program definitions, sleeves, P-rules, limitations | `docs/swing-program.md` |
| GitHub Actions, scan timing, Refresh, swing operations | `docs/operations.md` |
| Older reports and audits | `docs/*.md`, root `*_AUDIT.md` / `*_VALIDATION.md` / `*_REFACTOR.md`, `ROADMAP.md`, `RESEARCH_LOG.md` (historical; this file wins on conflict) |

Project skills (loaded on demand): `backtest-and-research`, `database-maintenance`, `release-and-deploy` in `.claude/skills/`.

# Compact instructions

When compacting, keep: test results (suite counts, pass/fail, failing output), every file path changed, every GitHub run id with its status, PR numbers, and open decisions or questions for the user.
