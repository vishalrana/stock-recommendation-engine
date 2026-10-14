---
name: backtest-and-research
description: Run or adopt a backtest or research study in this repo (production-universe backtest, swing holdout research, signal IC study), apply pre-registered keep/kill and P-rules, read placebo and trial results. Use before any strategy, scoring or exit change, or when asked to rerun or interpret research.
---

# Backtest and research

Definitions, results and rules live in `docs/`. Read the relevant one before acting:
- Swing program (definitions, sleeves, P1–P8, rule O, kill rules, placebo): `docs/swing-program.md`
- Ideas backtest harness, adopted results: `docs/backtest-results.md`
- Profitability program (R1–R5, EDGE CLAIM), research findings: `docs/research-history.md`

## Ground rules
- Pre-register before running: add the trial to the program's trial list first. Anything else is an extra trial and is reported as one.
- Every run that produces statistics is logged to `outputs/research_trials.jsonl` (`src/research/trials.py`), smoke runs marked `smoke`.
- Apply keep/kill and P-rules mechanically. Never change parameters after results exist.
- Adopt results only from a successful, non-smoke GitHub run, with its run id inside the committed file. A local or `--limit` run is never a result.
- Judge on beta-adjusted %/idea (signal-month block-bootstrap CI), FULL and LATE (signals from 2025-04-09), paired diffs, Holm/StepM. Never write "edge" unless the edge criteria pass.

## Ideas: production-universe backtest
1. `gh workflow run backtest_production_universe.yml` (inputs: `limit` for a smoke test, `variants`, `trial_phase`, `placebo`; 2–4 h).
2. `gh run download <id> -n backtest-production-universe -D outputs/production_universe_run_<id>`
3. `python scripts/adopt_strategy_evidence.py outputs/production_universe_run_<id> --run-id <id>` (writes `config/strategy_performance.json` only if every field reproduces).
4. Commit, with the run id in the message.

## Swing: holdout research
1. `gh workflow run swing_research.yml -f placebo=200` (jobs: data, then replication + patterns and the sleeve matrix T/E/N/P/I, then decide; about 1 h with a warm cache, 5–6 h cold).
2. Check `swing-data-summary/data_summary.json`: `u1_coverage_pct >= 98` and `history_check` present. The decide stage refuses otherwise.
3. Download `swing-decisions`; copy `decisions.json`, `replication.json`, `patterns.json` to `config/swing/` and `swing_sleeves.json` to `config/`. Confirm `provenance.github_run_id` and `smoke: false`.
4. Update `docs/swing-program.md` and `docs/backtest-results.md` with the real numbers, and the state line in CLAUDE.md.

## Placebo
Random eligible stocks (same universe, Liquid, not event-locked, not the signal's ticker), same fill session, same ATR multiples, same max hold or forced non-price exit, 200 runs with `stable_seed`. Report the percentile among placebo means and the paired diff vs the placebo median with its CI. Implementations: `src/research/placebo.py` (Ideas), `SwingPlacebo` in `src/swing/engine.py`.

## Signal IC study
`scripts/research_signal_ic.py` (workflow `research_signal_ic.yml`; `--ml` for the LightGBM research path).
