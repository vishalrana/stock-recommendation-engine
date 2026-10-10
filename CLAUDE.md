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

## Current status (as of 2026-10-10)

- Import errors in the universe provider and Supabase client were fixed. A project report claims 39 regression suites passed and a benchmark dry run succeeded.
- The hosted nightly workflow completed on 2026-10-09 but, because of the one-session cache lag and an unmigrated `reference_entry_price` column, it published ideas from stale bars and dropped every optional field. Both are fixed in code. `supabase/migration_2026_10_10_fundamentals.sql` adds those columns and the fundamentals provenance columns, and still has to be applied.
- Yahoo blocks GitHub's runners, so fundamentals now come from SEC EDGAR first. CI needs the `SEC_USER_AGENT` repository secret (a name and contact email); without it, fundamentals fall back to Yahoo and stay mostly empty in CI. After the next run, check the "Provider health" notice.
- The backtest universe is the 2025-02-13 cache constituents, but the window starts 2022-10-21. Results before the universe date are survivorship-inflated. The late period (from 2025-04-09) is the cleaner test; its figures are given with the latest backtest below.
- Earnings blackout (user-approved 2026-10-10): `EARNINGS_BLACKOUT_DAYS` now counts NYSE trading sessions after the scan date up to and including the report date (`trading_sessions_between` in `src/utils/market_date.py`, which applies the NYSE holiday rules). Before this it counted calendar days. `days_to_earnings` stays in calendar days, because that is what the card shows. The windows are still far shorter than the 5–25-day holding periods, so the card warns when earnings fall inside the holding period. The backtest cannot test the blackout (no historical earnings calendar).
- **Universe mismatch (open decision):** production scans the broad US universe (5,598 common equities; about 2,750 pass the liquidity filter). The strategy evidence comes from the 516 mostly large-cap stocks of the first cache file. The production-universe backtest (2026-10-10; see "Production-universe backtest" below) found the extra names worse, not better. Recommendation pending the user's decision: switch the nightly to `UNIVERSE_SOURCE=benchmark` (S&P 500 + Nasdaq-100), and keep downloading the tickers of open ideas so their lifecycle keeps updating.
- CI failed on every push because `get_client()` called `sys.exit()` without secrets. This is fixed: the suite passes 40/40 with and without Supabase credentials.
- The earlier backtest figures (−0.45% OOS expectancy) came from a harness that did not run the production pipeline, so they are superseded.
- Latest production-pipeline backtest (2026-10-10, with 52-Week High switched off): 2,984 issued trades.
  - Net +0.66%/trade (CI +0.05% to +1.29%). That CI excludes zero, but only because the market rose; the raw `edge_verdict_*` therefore reads "positive" and must not be taken as an edge.
  - Excess vs SPY −0.21%/trade (CI −0.71% to +0.31%). Beta-adjusted −0.18%/trade (CI −0.63% to +0.29%), mean β 0.92. Late period (1,110 trades): beta-adjusted −0.26%/trade (CI −1.18% to +0.72%). There is still no selection edge.
  - Before the switch-off (52-Week High on): 4,405 trades, excess −0.52%/trade, beta-adjusted −0.41%/trade (CI −0.90% to +0.04%).
- Previous run (2026-10-09; 516 stocks + 15 ETFs; Oct 2022 – Oct 2026; 4,917 issued trades):
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

## Data and context conventions (since 2026-10-10)

- `CacheManager.refresh_cache(start, end)` treats `end` as inclusive and adds a day for yfinance, whose `end` is exclusive. Before this, every nightly scan ran one session behind its regime date. If the cache still does not reach the market date after refresh, the scan publishes no new ideas and logs `stale_data`.
- Context (analyst, fundamentals, news) counts only when the provider actually returned data (`DataQuality.VALID`). Provider failures make context unavailable, so the weights are renormalized. `ContextScorer` has no technical fallback, because RSI, ADX and volume already sit in the momentum score. Failed fetches are never written to `context_cache`.
- The context score is the share of available points earned (earned ÷ available × 100). It counts only components that have data and are listed in `CONTEXT_SCORE_COMPONENTS` (`src/quant_config.py`, default: analyst + news). A missing component is excluded, never scored as 0. Rows carry `context_max_points` so the ranker uses the same denominator.
- Context is fetched only for candidates that could reach 65 with a perfect context score, which is safe because a renormalized score can never exceed that best case. Yahoo `.info` is called once per ticker with backoff.
- News sentiment is the mean over all scored headlines, with neutral headlines counted as 0.
- D/E display: `negative_equity` (stored with each idea) is True when total equity is zero or negative. The card then shows "Neg. equity", and a D/E above 10 is shown as ">10", with the reason on hover. Yahoo's D/E is never used for a negative-equity company, and an older D/E on an open idea is cleared once equity turns negative.
- P/E, D/E and current ratio are display-only, refreshed nightly for every open idea, and never used in qualification. This is enforced: fundamentals are left out of `CONTEXT_SCORE_COMPONENTS`, so their points and the D/E–current-ratio distress veto cannot move the score. In the point-in-time test they carried no information at the setup level. Adding them to the score changed issued-trade returns by only +0.05%/trade beta-adjusted (CI −0.08 to +0.19); see Research findings.
- **Fundamentals source (since 2026-10-10):** SEC EDGAR company facts come first (`src/providers/context/sec_fundamentals.py`). Yahoo `.info` only fills gaps, because Yahoo blocks GitHub's runners.
  - SEC requires a contact User-Agent. Set it in the `SEC_USER_AGENT` repository secret and in the local `.env`, never in a committed file. Without it, SEC is disabled and Yahoo alone is used.
  - TTM EPS uses the 10-K, then 10-K + current YTD − prior YTD, then the last four quarters. If reported EPS differs from TTM net income ÷ latest diluted shares by more than 35% on the same period end, the net-income figure is used, which catches splits between filings. EPS ≤ 0 means "Loss", with no P/E.
  - D/E is all borrowings (including current maturities and commercial paper) plus finance and operating leases, divided by total equity including non-controlling interest. If borrowings were tagged in other periods but not on the latest balance sheet, D/E is unknown, never 0.
  - Data older than 200 days is not used. `fundamentals_source` and `fundamentals_as_of` (balance-sheet date) are stored with each idea.
  - A holding-company reorganisation starts a new SEC filer with no history: XOM (August 2026), BLK (2024), BG (2023). Until the new filer has a 10-K or four quarters, its TTM EPS (and so P/E) comes only from Yahoo, which means none in CI. D/E and current ratio are unaffected. The value shows as missing, never as a wrong number.
  - Summaries are cached for 72 hours in `data/cache/fundamentals/`, which the workflows cache.
- Each scan prints a "Provider health" GitHub notice: SEC and Yahoo success counts, and context and display-fundamentals coverage. It is readable from the run's check annotations without admin rights.
- `supabase/migration_2026_10_10_fundamentals.sql` adds `eps_ttm`, `fundamentals_source` and `fundamentals_as_of`. It also adds the never-applied `reference_entry_price`, `weighted_scaleout_rr` and `entry_location_zone` columns, and it is idempotent.
- Inserts drop only the specific unmigrated column the database names (`write_with_missing_column_retry`). If the logs show `[SCHEMA] Wrote without unmigrated column(s)`, apply the missing migration.
- **Applying migrations:** `supabase/setup_run_migration.sql` was run on 2026-10-10. Since then, `python scripts/apply_migration.py <file.sql>` applies a migration file statement by statement, using the service key from `.env` (`--dry-run` lists the statements). Applied: `migration_2026_10_10_fundamentals.sql` and `migration_2026_10_10_negative_equity.sql`. Before the setup, `python scripts/apply_migration.py <file.sql>` applies a migration file statement by statement, using the service key from `.env` (`--dry-run` lists the statements). Migration files must be idempotent. Until that setup is done, migrations need the SQL editor.
- `get_client()` raises `SupabaseConfigError` instead of exiting, so CI can run tests without secrets.

## Strategy decisions (user-approved 2026-10-09)

- **Exits:** stop, final target, or the strategy's holding period (`hold_days` in `STRATEGY_TARGET_CONFIG`, counted in trading bars from the fill), which gives `expired`. Failing to requalify in a later scan does not close an idea.
- **Win rate / expectancy evidence:** per-strategy figures from the production-pipeline backtest (`config/strategy_performance.json`, read by `src/strategy_evidence.py`), shrunk toward 50% / 0%. There are no hard-coded expectancy priors, and per-ticker legacy metrics are not used in scoring.
- **Stops:** each strategy's own structural stop is used as issued. There is no minimum floor and no 7% cap.
- **Momentum sub-score:** rewards strength for trend, breakout, momentum, sector and PEAD strategies. Pullback and Mean Reversion keep the near-average scoring.
- **Rules:** Pullback needs RSI at least 5 points off its 10-bar low (dip below 50, recovery band 45–67). Mean Reversion needs RSI 3+ points off its 5-bar low on an up-close day. The unused `is_blocked` guardrails are removed.
- **Trend-quality gates (2026-10-10, backtested):**
  - Pullback requires price > 50-day > 200-day with a rising 200-day in every regime. The bull-regime shortcut "price > 50-day" admitted bounces inside downtrends.
  - Cross-Sectional Momentum requires price above a rising 200-day.
  - Trend Following requires a rising 200-day.
  - Against the same harness and data, issued-trade excess vs SPY went from −0.55% to −0.52%/trade. Cross-Sectional issued trades went from +0.06% to +0.43% net, and Trend Following from −0.46% to −0.23% vs SPY. All differences are within the confidence intervals, so the gates are kept as definitional fixes, not as proven improvements.
- **Reach probability:** setup-conditional (the strategy's backtest target-hit rate) once the strategy has at least 30 trades. Otherwise it falls back to the ticker base rate. `reach_prob_source` records which one was used.
- **52-Week High switched off (user-approved test-and-fix, 2026-10-10).** It is removed from `REGIME_STRATEGY_MAP` (bull was its only regime). Open 52-Week High ideas keep being tracked to their stop, target or 25-day limit. Re-adding it requires a new backtest. See "52-Week High decision" below.

## 52-Week High decision (2026-10-10)

- **Why:** it produced 72% of issued ideas and the weakest results: −0.48%/trade beta-adjusted (CI −1.03 to +0.04), and −0.75 in the late period. Its rules had been loosened from the original spec (within 2% of the high → 5%, RSI 55–75 → 45–80, ADX ≥ 20 → 15, volume ≥ 1.2× → 0.8×, within 3% of the 20-day high → 5%), so it bought almost any stock near its high in a bull market.
- **Method:** three alternatives were fixed before any results, plus a rule. An alternative is adopted only if its paired beta-adjusted return per issued idea, over the whole book, beats production in both the full and the late period. If several qualify, the largest full-period gain wins. The screen ran `--variants w52_off,w52_original_rules,w52_breakout` against identical setups.

| Option | Ideas | Ideas / month | Beta-adj. per idea | vs production, full | vs production, late |
|---|---|---|---|---|---|
| production (relaxed rules) | 4,405 | 89.9 | −0.41 (CI −0.91 to +0.05) | — | — |
| **off** | 2,984 | 63.5 | −0.18 (CI −0.63 to +0.29) | **+0.23 (CI +0.02 to +0.45)** | **+0.36 (CI +0.04 to +0.67)** |
| original (stricter) rules | 3,186 | 65.0 | −0.23 (CI −0.74 to +0.25) | +0.18 (CI −0.00 to +0.38) | +0.25 (CI −0.06 to +0.56) |
| true breakout (new 52-week closing high on ≥ 1.5× volume) | 3,137 | 65.4 | −0.27 (CI −0.77 to +0.21) | +0.14 (CI −0.04 to +0.33) | +0.14 (CI −0.13 to +0.39) |

- All three passed the rule. "Off" had the largest gain, and its CIs exclude zero in both periods, so it was adopted. The full validation run (scans recomputed, evidence regenerated without 52-Week High) reproduced the screen exactly: 2,984 ideas, beta-adjusted −0.18%/trade. Even under the stricter rules, 52-Week High ideas stayed negative (−0.25 beta-adjusted).
- Ideas fall only 29%, because many of the same stocks are then issued under Trend Following (964 → 2,550 ideas, −0.19 beta-adjusted). Other strategies' evidence does not depend on 52-Week High, so the screen is exact for this option.
- The engine is still not positive after adjusting for beta. This removes its worst component; it does not create an edge.

## Production-universe backtest (2026-10-10, GitHub run 38059174571)

Today's security master was applied to Oct 2022 – Oct 2026: 5,598 stocks, 821 s replay, 82,914 setups, 7,261 issued ideas. Survivorship flatters small caps here, because stocks that delisted are missing. Figures are beta-adjusted %/trade, with signal-month block-bootstrap 95% CIs.

| Segment | All setups | Issued ideas |
|---|---|---|
| Whole universe | −0.71 (−1.03 to −0.39) | −0.50 (−1.14 to +0.20); late period −0.32 |
| Large caps (the 516 backtested) | −0.35 (−0.62 to −0.06) | −0.27 (−0.86 to +0.29) |
| Rest of the universe | −0.81 (−1.16 to −0.45) | −0.58 (−1.30 to +0.19) |

- In every strategy except Mean Reversion, small- and mid-cap setups are worse than large-cap ones. Cross-Sectional Momentum setups outside the large caps: −1.17 (−1.56 to −0.75).
- Trend Following is 95% of issued ideas (52-Week High off).
- By 20-day dollar volume at the signal, issued ideas over $100M ADV did worst (−0.68 and −0.75, CIs excluding zero), so liquidity alone is not the explanation.
- Issued Mean Reversion ideas: +0.45 on 202 ideas, almost all from the 2022 bear market (no late-period trades). At the setup level, Mean Reversion is +0.01 (−0.60 to +0.71), so this is not evidence of an edge.
- There is no edge in either universe. The broad universe adds about 2.4× the ideas (7.3 vs 3.0 a day) at a lower quality per idea.

## Research findings (2026-10-10, setup level)

Method: all 27,268 strategy setups from the production-pipeline backtest (Oct 2022 – Oct 2026), point-in-time features, forward net return minus SPY over the same window. Ranking power is measured as the monthly Spearman rank-IC (t-stat over months), with differences given as signal-month block-bootstrap 95% CIs. The sample is almost all bull market, so treat every result as provisional.

- **Momentum sub-score (45–50% of the trend and breakout weights) does not rank stocks.** IC: 52-Week High −0.02 (t −0.8), Cross-Sectional −0.04 (t −2.8), Pullback −0.03, Trend +0.01, Mean Reversion +0.04. It is not reliably positive in either half of the sample, and its quintiles are not monotonic.
- **The composite ≥ 65 cut adds nothing.** Excess per setup, ≥ 65 vs < 65: 52-Week High −0.44 vs −0.46, Cross-Sectional −0.68 vs −0.17, Trend −0.17 vs −0.35. Pullback setups never reach 65.
- **12-month return ranks better.** IC: 52-Week High +0.06 (t 1.8), Cross-Sectional +0.06 (t 2.3), Pullback +0.07 (t 3.2), positive in both halves, which matches the documented momentum anomaly. ATR% also "predicts" (IC +0.05 to +0.14), but that is beta in a rising market, not skill.
- **The entry-location gate keeps the worse setups.** Mean excess: BUY −0.46 (11,626), WAIT −0.26 (14,555), REJECT −0.11 (1,087). For Trend Following, BUY minus blocked is −0.30 (CI −0.60 to −0.03).
- **Fundamentals carry no information (point-in-time SEC, D/E coverage 82%).** Excess with the trait minus without it: D/E < 1 −0.01 (CI −0.27 to +0.28); current ratio > 1.5 +0.21 (−0.23 to +0.62); distress veto −0.01 (−0.54 to +0.53); P/E below median +0.16 (−0.13 to +0.50). Loss-makers beat profitable companies by +1.13 (+0.44 to +1.85), which is speculative beta in a bull sample, not a quality signal.

## Selection-variant backtest (2026-10-10)

Run while 52-Week High was still active. Method: one pass of `validate_backtest_pipeline.py --variants all`, so every variant sees the same setups, walk-forward evidence and execution. Each row is issued trades. "Beta-adjusted" is net return − β × SPY return over the trade window, with β estimated from the 252 sessions before the signal. Differences are paired block-bootstrap 95% CIs. The late period starts 2025-04-09, after the universe date, so it is the cleaner test.

| Variant | Trades | β | Excess vs SPY | Beta-adj. | Beta-adj. vs production, full | Beta-adj. vs production, late |
|---|---|---|---|---|---|---|
| production (technical momentum, entry gate, no fundamentals) | 4,405 | 0.88 | −0.52 | −0.41 | — | — |
| `rs_momentum` (12-1 month RS percentile) | 4,042 | 1.10 | −0.28 | −0.40 | +0.00 (−0.34 to +0.38) | +0.27 (−0.25 to +0.93) |
| `entry_info` (entry verdict not a gate) | 5,975 | 0.89 | −0.47 | −0.35 | +0.06 (−0.09 to +0.22) | +0.09 (−0.14 to +0.34) |
| `rs_momentum_entry_info` | 5,186 | 1.11 | −0.17 | −0.30 | +0.11 (−0.21 to +0.45) | +0.52 (+0.06 to +1.11) |
| `fundamentals_in_score` | 4,145 | 0.92 | −0.43 | −0.36 | +0.05 (−0.08 to +0.19) | +0.27 (+0.01 to +0.50) |

- **No variant earns a positive beta-adjusted return.** Relative strength improves raw excess only by buying higher-beta stocks (β 1.10 vs 0.88); beta-adjusted over the full period it is identical to production.
- The two late-period CIs that exclude zero are 2 of 8 comparisons on about 18 monthly blocks, and the full period does not confirm them. They are not evidence for a change.
- The entry gate blocks about 1,500 trades without changing per-trade quality, so it neither helps nor hurts measurably.
- Production per strategy, beta-adjusted, full period: 52-Week High −0.48 (CI −1.03 to +0.04; 72% of trades), Trend Following −0.25, Cross-Sectional −0.07, Sector Rotation −0.10, Mean Reversion −0.38 (47 trades). The late period is worse: 52-Week High −0.75.
- Production defaults are unchanged (technical, gate, fundamentals display-only). The switches remain available for future tests.

## Backtest

- `scripts/validate_backtest_pipeline.py` replays the production pipeline using the shared `src/pipeline_steps.py`, with walk-forward evidence and confidence intervals from a signal-month block bootstrap. Rerun it after any change to strategy, scoring or exit logic, then commit the regenerated `config/strategy_performance.json`.
- Every trade carries a point-in-time `beta` (252 sessions before the signal) and `beta_adjusted_pct` (net − β × SPY return over the trade window). The stats include `mean_beta_adjusted_pct` with its CI, and the summary includes `edge_beta_adjusted_verdict_*`. Judge selection skill on the beta-adjusted and vs-SPY verdicts, not on `edge_verdict_*` (absolute return).
- The cached phase-1 scans are keyed by strategy code, indicators, data extent and `REGIME_STRATEGY_MAP`, so switching a strategy on or off forces a rescan.
- `--universe production` backtests today's broad US security master (about 5,600 tickers) instead of the first cache file's 516 stocks. It fails if the security master is unavailable.
  - Run it on GitHub: the workflow `backtest_production_universe.yml` (manual dispatch, optional `limit` for a smoke test, about 2–4 h) uploads the summary, trades and setups, and `outputs/strategy_performance_production_universe.json` as an artifact.
  - It never overwrites `config/strategy_performance.json`; adopting that evidence is a decision.
  - Survivorship: today's list is applied to the whole window, so every period is chosen with hindsight, and small caps are biased upward the most.
  - `--phase1-only` caches the scans. Trades carry `adv20_usd` (20-day dollar volume at the signal) for size tiers.
  - Walk-forward evidence is incremental (`WalkForwardEvidenceBook`) and matches daily re-aggregation exactly.
- `--variants all` (or a comma-separated list) evaluates alternatives in the same pass, against identical setups, evidence and execution. Selection variants: `rs_momentum`, `entry_info`, `rs_momentum_entry_info`, `fundamentals_in_score`. Strategy screening variants (`STRATEGY_VARIANTS`, candidate filters): `w52_off`, `w52_original_rules`, `w52_breakout`. They need 52-Week High re-enabled in `REGIME_STRATEGY_MAP`; otherwise they do nothing. A strategy variant keeps that strategy's walk-forward evidence from all its setups, so a winner must be built into the strategy and re-validated with a full run. Each variant keeps its own book, with one active idea per ticker. Results go to `outputs/production_backtest_variants.json`, with paired CIs against production. The switches live in `src/quant_config.py` (`MOMENTUM_MODEL`, `ENTRY_LOCATION_MODE`, `CONTEXT_SCORE_COMPONENTS`) and are read through `SelectionSettings` by both the scan and the backtest. `fundamentals_in_score` uses point-in-time SEC fundamentals: facts filed strictly before the signal date, with the 200-day freshness rule.
- Known limitations (listed in its output): survivorship, no historical earnings calendar (so PEAD and the earnings blackout cannot be tested), no historical context data, no VIX history, and thresholds that were hand-tuned before the harness existed. The 516-stock universe is narrower than production's (see Current status).

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

## Other docs

- `PROJECT_CONTEXT.md`: early research notes (phases 1–4, strategy v1.1 candidate). Parts may be outdated, so this file takes precedence where they conflict.
- `ROADMAP.md`, `RESEARCH_LOG.md`: roadmap and research history.
