# Stock Recommendation Engine — Working Guide

This is the single source of truth for project scope and working rules. Add new guidance here instead of creating new files.

## What this is

A personal-use, quantitative research and recommendation engine for US equities. It scans a stock universe, applies the configured strategies, scores and ranks setups, calculates indicative entry, stop-loss, target and risk/reward levels and target-reach probabilities, and publishes recommendations. The user reviews the output and decides independently whether to trade.

## What this is not

- Not a trading bot: no order placement or brokerage execution.
- Not a portfolio manager: no position sizing, Kelly sizing, capital allocation or portfolio-state logic.
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
- **Check calculations rigorously**: RSI/ATR and other indicators (inputs, conventions, edge cases); scores, thresholds, ranking and filters; entry, stop, target and R:R; probabilities (validity, sample size, calibration); backtest execution; scale-outs and same-bar stop/target collisions; lifecycle transitions; timestamps, ticker identity and data provenance.
- **Present probabilities honestly**: they must be mathematically valid, calibrated and backed by enough data, and never presented as certainties.
- **Lifecycle integrity**: keep each recommendation's identity and status correct, preserve history, and never wrongly duplicate, replace or close a recommendation.
- **Backtests** must prevent look-ahead and survivorship bias, use realistic execution assumptions and trading costs, and validate out of sample.
- **Fail correctly**: on incomplete scans, stale or missing data, provider failures or thin samples, never silently publish misleading recommendations or corrupt existing records.
- **Strategy changes need evidence**: do not change the production strategy without backtest validation.

## Current status (as of 2026-10-09)

- Import errors in the universe provider and Supabase client were fixed. A project report claims 39 regression suites passed and a benchmark dry run succeeded.
- The hosted nightly workflow has not yet had a confirmed successful run. A successful Vercel deploy is not proof that the Python scan completed.
- The earlier backtest figures (−0.45% OOS expectancy) came from a harness that did not run the production pipeline, so they are superseded.
- Production-pipeline backtest (2026-10-09; 516 stocks + 15 ETFs; Oct 2022 – Oct 2026; 4,917 issued trades):
  - Net expectancy was +0.39%/trade (95% block-bootstrap CI −0.21% to +0.93%), so **no edge was detected**.
  - Holding SPY over the same windows returned +0.94%/trade, putting **excess vs SPY at −0.55%/trade (CI −1.12% to −0.06%)**. The recommendations underperformed simply holding the index.
  - Almost no trades reach T2 or T3, and most exit at the holding-period limit.
  - Ideas that scored ≥65 did worse than all setups of the same strategy (52-Week High: +0.31% vs +0.66%), so the composite score is not adding selection value in this test.
  - The sample is almost entirely a bull market (bear/sideways: 74 trades).

## Lifecycle and outcome conventions (since 2026-10-09)

- Open recommendations are tracked by replaying every bar after the signal date through `PositionScaleOutTracker` (`reconcile_recommendation_lifecycle`). A missed nightly run never hides a stop or target hit.
- The tracked fill is the open of the first bar after the signal date (`entry_fill_price`). `entry_price` is the indicative entry shown at issuance. Returns are measured from the fill.
- Stop, targets, reach probabilities, R:R and strategy are frozen at issuance. A re-scan refreshes only analytical fields (score, tier, indicators, P/E).
- Same-bar ambiguity is STOP_FIRST, including the ratcheted stop on the bar where a target fills. A target fills at the open when the bar gaps above it. Trades whose data runs out are left unresolved, never force-closed.
- If context data is missing or times out, it is excluded from the composite score and the remaining weights are renormalized. It is not scored as 0.
- Migration `supabase/migration_lifecycle_replay_and_pe.sql` adds the columns these depend on.

## Strategy decisions (user-approved 2026-10-09)

- **Exits:** stop, final target, or the strategy's holding period (`hold_days` in `STRATEGY_TARGET_CONFIG`, counted in trading bars from the fill), which gives `expired`. Failing to requalify in a later scan does not close an idea.
- **Win rate / expectancy evidence:** per-strategy figures from the production-pipeline backtest (`config/strategy_performance.json`, read by `src/strategy_evidence.py`), shrunk toward 50% / 0%. There are no hard-coded expectancy priors, and per-ticker legacy metrics are not used in scoring.
- **Stops:** each strategy's own structural stop is used as issued. There is no minimum floor and no 7% cap.
- **Momentum sub-score:** rewards strength for trend, breakout, momentum, sector and PEAD strategies. Pullback and Mean Reversion keep the near-average scoring.
- **Rules:** Pullback needs RSI at least 5 points off its 10-bar low (dip below 50, recovery band 45–67). Mean Reversion needs RSI 3+ points off its 5-bar low on an up-close day. The unused `is_blocked` guardrails are removed.
- **Reach probability:** setup-conditional (the strategy's backtest target-hit rate) once the strategy has at least 30 trades. Otherwise it falls back to the ticker base rate. `reach_prob_source` records which one was used.

## Backtest

- `scripts/validate_backtest_pipeline.py` replays the production pipeline using the shared `src/pipeline_steps.py`, with walk-forward evidence and confidence intervals from a signal-month block bootstrap. Rerun it after any change to strategy, scoring or exit logic, then commit the regenerated `config/strategy_performance.json`.
- Known limitations (listed in its output): survivorship, no historical earnings calendar (so PEAD and the earnings blackout cannot be tested), no historical context data, no VIX history, and thresholds that were hand-tuned before the harness existed.

## Other docs

- `PROJECT_CONTEXT.md`: early research notes (phases 1–4, strategy v1.1 candidate). Parts may be outdated, so this file takes precedence where they conflict.
- `ROADMAP.md`, `RESEARCH_LOG.md`: roadmap and research history.
