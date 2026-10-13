# Stock Recommendation Engine — Working Guide

This is the single source of truth for project scope and working rules. Add new guidance here instead of creating new files.

## What this is

A personal-use, quantitative research and recommendation engine for US equities. It scans a stock universe, applies the configured strategies, scores and ranks setups, calculates indicative entry, stop-loss, target and risk/reward levels and target-reach probabilities, and publishes recommendations. The user reviews the output and decides independently whether to trade.

## What this is not

- Not a trading bot: no order placement or brokerage execution.
- Not a portfolio manager: no position sizing, Kelly sizing, capital allocation or portfolio-state logic.
  - **Exception (adopted 2026-10-11, profitability program Phase 6):** display-only sizing guidance on the cards.
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

## Current status (as of 2026-10-11)

- **2026-10-11: profitability program completed** (see that section). Production now applies the full-hold earnings blackout, the event lock and the stop cap / R:R floor (about 2–3 new ideas a month). Evidence comes from run 38122679781. The final verdict is no edge.

- Import errors in the universe provider and Supabase client were fixed. A project report claims 39 regression suites passed and a benchmark dry run succeeded.
- The hosted nightly workflow completed on 2026-10-09 but, because of the one-session cache lag and an unmigrated `reference_entry_price` column, it published ideas from stale bars and dropped every optional field. Both are fixed in code, and `supabase/migration_2026_10_10_fundamentals.sql` (applied 2026-10-10) adds those columns and the fundamentals provenance columns.
- Yahoo blocks GitHub's runners, so fundamentals now come from SEC EDGAR first. The `SEC_USER_AGENT` repository secret (a name and contact email) is set; refresh run 38056801266 made 31 of 31 SEC requests successfully. Without it, fundamentals fall back to Yahoo and stay mostly empty in CI.
- Survivorship in both backtests:
  - The cache_start run uses the 2025-02-13 cache constituents (516 stocks) from 2022-10-21, so results before the universe date are survivorship-inflated.
  - The production-universe run applies today's security master to the whole window, so stocks that delisted are missing throughout, which flatters small caps.
  - The late period (from 2025-04-09) is the cleaner test.
- Earnings blackout (user-approved 2026-10-10): `EARNINGS_BLACKOUT_DAYS` now counts NYSE trading sessions after the scan date up to and including the report date (`trading_sessions_between` in `src/utils/market_date.py`, which applies the NYSE holiday rules). Before this it counted calendar days. `days_to_earnings` stays in calendar days, because that is what the card shows. Since 2026-10-11 the full-hold rule replaces this window for new ideas (see Profitability program, Phase 2), and the backtest tests both on SEC 8-K item 2.02 dates.
- **Universe (user decision 2026-10-10): keep the broad universe.** `UNIVERSE_SOURCE` stays `expanded`: 5,598 common equities, about 2,750 of them liquid. Strategy evidence now comes from the production-universe backtest run 38059174571, adopted into `config/strategy_performance.json`, so scores and cards are measured on the universe the scan covers. Delisted stocks are missing from that run, which flatters small caps.
- **Recommendation data reset (2026-10-10):**
  - Why: a clean start after adopting the production-universe evidence. The existing ideas had been scored with the 516-stock evidence and the pre-fix pipeline.
  - How: `scripts/clear_and_reset_db.py --yes`. It first backed up all 8 tables (7,305 rows) to `backups/supabase_20261010T164159Z/`, which is kept locally only, gitignored, and must never be committed.
  - Cleared: `signals` (611 rows), `signals_history` (3,539), `scan_log` (21), `context_cache` (908) and the decommissioned `portfolio_state` (1).
  - Kept, unchanged: `earnings_calendar` (1,710), `ticker_metrics` (515), `delisted_tickers` (0). The `recommendations` view rebuilds from the tables.
  - First scan after the reset: nightly run 38068760022, market date 2026-10-09, status success, 3 ideas (TEVA, SNOW, PBT). Refresh run 38069398766 reconciled all three.
- CI failed on every push because `get_client()` called `sys.exit()` without secrets. This is fixed: the suite passes 40/40 with and without Supabase credentials.
- The earlier backtest figures (−0.45% OOS expectancy) came from a harness that did not run the production pipeline, so they are superseded.
- **Evidence basis (adopted 2026-10-10): production-universe run 38059174571.**
  - 7,261 issued ideas: −0.50%/idea beta-adjusted (CI −1.14 to +0.20), late period −0.32. There is no edge.
  - All setups, beta-adjusted %/trade: Trend Following −0.77, Pullback −0.48, Cross-Sectional −1.05, Mean Reversion +0.01, Sector Rotation −0.73.
- Earlier cache_start (516-stock) run (2026-10-10, with 52-Week High switched off): 2,984 issued trades.
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
- **Strategy evidence on cards:**
  - Each idea stores `strategy_beta_adjusted_pct` and `strategy_excess_vs_spy_pct` (migration `migration_2026_10_10_strategy_vs_spy.sql`), frozen at issuance like the other `strategy_*` fields.
  - The card shows "54% of N trades · +0.12%/trade" and, below it in the same type or bolder, "−0.77%/trade vs SPY after beta" (rose when negative). Hovering shows the plain vs-SPY figure.
  - Tiers and labels are unchanged.
- **Universe loading:**
  - The order is the broad universe (`USEquitiesUniverseProvider`), then the S&P 500 + Nasdaq-100 benchmark from Wikipedia (ideas are still published, with status `degraded_universe`). There is no further fallback: the 100-row `outputs/backtest_summary.csv` substitute was removed.
  - If neither loads, the nightly publishes no new ideas. It re-evaluates the open ideas through the targeted path so lifecycle tracking continues, and logs `degraded_universe` with the reason in `scan_log`.
  - The degraded check now uses the effective source (the `UNIVERSE_SOURCE` default); passing no source used to skip it.
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
- **Applying migrations:** `python scripts/apply_migration.py <file.sql>` applies a migration file statement by statement through `public.run_migration`, using the service key from `.env`; `--dry-run` lists the statements.
  - `public.run_migration` was created once, on 2026-10-10, by running `supabase/setup_run_migration.sql` in the SQL editor.
  - Migration files must be idempotent.
  - Applied 2026-10-10: `migration_2026_10_10_fundamentals.sql`, `migration_2026_10_10_negative_equity.sql` and `migration_2026_10_10_strategy_vs_spy.sql`.
- `get_client()` raises `SupabaseConfigError` instead of exiting, so CI can run tests without secrets.

## Strategy decisions (user-approved 2026-10-09)

- **Exits:** stop, final target, or the strategy's holding period (`hold_days` in `STRATEGY_TARGET_CONFIG`, counted in trading bars from the fill), which gives `expired`. Failing to requalify in a later scan does not close an idea.
- **Win rate / expectancy evidence:** per-strategy figures from the production-universe backtest, shrunk toward 50% / 0%. They live in `config/strategy_performance.json`, adopted from run 38059174571 by `scripts/adopt_strategy_evidence.py`, and are read by `src/strategy_evidence.py`. There are no hard-coded expectancy priors, and per-ticker legacy metrics are not used in scoring. The aggregates also carry beta-adjusted and vs-SPY sums and counts; these are display-only (cards), and scoring never reads them.
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

- The large-cap vs rest split does **not** show that smaller stocks are worse, because the comparison is confounded:
  - The 2025-02-13 list of 516 is applied from Oct 2022, so stocks that became large caps count as large caps during the runs that got them there.
  - Every backtested rule change (trend-quality gates, 52-Week High) was chosen on the 516.
  - Beta against SPY leaves the size factor in.
  - By point-in-time 20-day dollar volume, the most liquid ideas did worst: over $100M ADV, −0.68 and −0.75.
  - The issued-idea CIs overlap: large caps −0.27 (−0.86 to +0.29), rest −0.58 (−1.30 to +0.19).
  - The gap nearly closes in the late period. Full period: broad run −0.50 vs 516 run −0.18. Late period: −0.32 vs −0.26.
- Trend Following is 95% of issued ideas (52-Week High off).
- Issued Mean Reversion ideas: +0.45 on 202 ideas, almost all from the 2022 bear market (no late-period trades). At the setup level, Mean Reversion is +0.01 (−0.60 to +0.71), so this is not evidence of an edge.
- There is no edge in either universe. The broad universe adds about 2.4× the ideas (7.3 vs 3.0 a day).

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

## Profitability program (pre-registered 2026-10-11)

Recorded before any of its backtests or IC studies were run. The definitions and keep/kill rules below are verbatim and are applied mechanically. Only the trials listed here may be run. Any other trial must be logged as an extra trial and reported as such. Nothing in this program guarantees profitability: the realistic best case is a few %/yr of beta-adjusted alpha with large tracking error, statistically indistinguishable from zero over 4 years for a 3–10-name book. Never write "edge" unless the EDGE CLAIM criteria pass.

**Definitions (verbatim):**
- Primary metric: beta-adjusted %/issued idea (existing beta_adjusted_pct), with signal-month block-bootstrap 95% CI (existing machinery), net of 10 bps/side.
- Periods: FULL (whole window) and LATE (signals from 2025-04-09).
- Paired diff: paired_block_diff of a variant vs production on the same pass.
- Random-stock placebo (new): for every issued idea of a book, draw a random stock from the same date's eligible liquid universe (price >= $5, ADV20 >= $5M, not event-locked), with the same entry rule (next open), same hold_days, the same stop distance in ATR multiples, the same costs. Repeat 200 times with fixed seeds. Report the book's percentile vs the placebo distribution, and the paired diff vs the placebo median with a block-bootstrap CI.
- Multiple testing: every variant run (including smoke-run failures that produce statistics) is appended to outputs/research_trials.jsonl (name, phase, git sha, params, n_ideas, mean beta-adj, CI, late mean, daily or per-idea return series path). Apply a Holm correction across all variants in a phase, and run arch.bootstrap.StepM (stationary bootstrap) on the per-signal-month beta-adjusted return series of all variants vs production.

**Keep/kill rules (verbatim, decided now, applied mechanically later):**
- R1 RISK-CONTROL FILTERS (Phase 2: earnings_full_hold, event_lock, rr_floor_stop_cap, and their combination): ADOPT unless the full-period paired diff vs production has a 95% CI entirely below zero (then KILL). Also report the left tail (5th-percentile idea return, worst idea, share of stops gapped through, share of ideas with earnings inside the hold). If the point estimate is below -0.10 in both periods but the CI includes zero, ADOPT and flag it in the report as "costs return; kept as a risk rule".
- R2 SELECTION/STRUCTURE IMPROVEMENTS (Phases 4-6 variants): ADOPT only if the paired diff vs production (post-Phase-2 production) is > 0 in BOTH FULL and LATE, AND the FULL CI excludes zero, AND it survives the Holm correction within its phase. If several pass, adopt the one with the largest FULL gain. Ties: prefer the one with fewer parameters.
- R3 SIGNAL ADOPTION (Phase 3 IC study, horizon 20 sessions, monthly non-overlapping rank IC on the liquid universe vs forward return minus beta x SPY): a signal or composite is "valid" if mean IC > 0 with Newey-West t >= 2.0, positive in both halves and in the LATE period. The pre-specified composite C6 is adopted as the ranking signal if valid AND its IC is >= the 12-1 momentum IC (paired monthly diff point estimate >= 0). Otherwise use 12-1 momentum alone if valid. Otherwise adopt nothing.
- R4 PATTERN FEATURES (Phase 5): a pattern feature is kept as a ranking input only if its INCREMENTAL IC (residualized on 12-1 momentum and 52w-high proximity each month) has t >= 3.0, positive in both halves. The hs_top_veto filter follows R2. Failing features stay computed and displayed only, or are removed if unused.
- R5 ML (Phase 7, gated): run ONLY if R3 adopted something. Adopt LightGBM only if its purged walk-forward OOS monthly rank IC exceeds the adopted signal's IC with a paired bootstrap CI above zero, in both halves and LATE. Otherwise KILL and delete the model code path (keep the research script).
- EDGE CLAIM (separate from adoption): the cards and CLAUDE.md may say "positive beta-adjusted edge (backtest)" only if the final production book has a FULL beta-adjusted CI > 0, LATE mean > 0, is above the 95th percentile of the random placebo, and survives StepM at 5%. Otherwise the cards keep the vs-SPY figure and add the plain line "Backtest: no edge vs SPY after beta; holding SPY did as well or better."

**Pre-registered trials (the complete list):**

| Phase | Rule | Trials |
|---|---|---|
| 2 Trade hygiene | R1 | `earnings_full_hold`, `event_lock`, `rr_floor_stop_cap`, `hygiene_all` (all three together) |
| 3 Signal IC (20-session horizon) | R3 | `MOM12_1`, `RESMOM`, `HI52`, `FIP` (used as −FIP), `REV21` (−), `RVOL252` (−), `C6` (equal-weight average of those six ranks). Reported only, never adoptable: `MOM63`, `beta252`, `MAX21`, `ADV20`, sector-ETF 12-1 momentum |
| 4 Selection / structure | R2 (Holm over these six) | `rank_select`, `cs_mom_12_1`, `momentum_rank_book`, `trail_after_t1`, `mr_liquid_only`, `no_rebound_entries` |
| 5 Patterns | R4 (features), R2 (veto) | Features `hs_top_confirmed_20d`, `inv_hs_confirmed_20d`, `double_bottom_breakout_20d`, `base_depth_pct`, `contraction_count`, `dist_to_neckline_atr`, `breakout_volume_ratio`; variant `hs_top_veto` |
| 7 ML (only if R3 adopts a signal) | R5 | `lightgbm_ranker` (purged walk-forward OOS) |
| Final | EDGE CLAIM | the final production book |

- Trials registry: `outputs/research_trials.jsonl` (gitignored), one JSON record per trial per run, written by `src/research/trials.py`. Smoke runs that produce statistics are recorded too, marked `smoke`.
- Phase 4 is skipped, along with Phase 7, if neither C6 nor MOM12_1 is valid under R3.

**Program progress:**
- Phase 1 (2026-10-11): measurement tools only, with no strategy change.
  - `src/research/` contains:
    - `trials.py`: the trials registry.
    - `placebo.py`: the random-stock placebo, plus a fast numpy exit engine that matches `evaluate_signal_outcome` on 1,500 random paths.
    - `costs.py`: the Corwin-Schultz half-spread with the pairwise overnight adjustment, the three cost scenarios and the break-even cost.
    - `stats.py`: Holm, Newey-West t, a paired month bootstrap with one-sided p-values, a StepM wrapper and the deflated Sharpe ratio.
    - `survivorship.py`: the S&P 500 member-day gauge.
  - Harness (`scripts/validate_backtest_pipeline.py`):
    - New flags: `--placebo N` (200 by default, 20 with `--limit`, 0 = off) and `--trial-phase`.
    - LATE is fixed at signals from 2025-04-09 (`LATE_FROM`).
    - Each book reports its cost scenarios and placebo percentile.
    - Holm and StepM run over the run's variants (`multiple_testing`).
    - The survivorship gauge is listed in the limitations.
    - Every book of every run goes into the registry. Smoke runs also write `production_backtest_summary*_smoke.json`.
  - The workflow `backtest_production_universe.yml` has new inputs `variants`, `trial_phase` and `placebo`. It installs `requirements-research.txt` (research only; `requirements.txt` is unchanged) and uploads the registry.
  - Corwin-Schultz caveat: daily high-low estimators overstate the spreads of liquid large caps (AAPL ≈ 0.25% half-spread, against about 0.01% in reality). Cost scenario (b) is therefore pessimistic for them. The break-even cost per side does not depend on the estimate.
  - Tests: `scripts/test_research_program.py`.
- Phase 2 (2026-10-11, R1): trade hygiene, GitHub run 38118783161.
  - Setup:
    - Production universe: 5,592 stocks, Oct 2022 – Oct 2026.
    - The SEC calendar covers 3,720 of 5,135 stocks with filings: 79,951 earnings events.
    - The baseline now applies the live fixed-days blackout and lets PEAD fire.
    - The rights-offering search hit 288 tickers.

    | Variant | Ideas | Beta-adj. %/idea (CI) | LATE | Paired vs production, full (CI) | Late | R1 |
    |---|---|---|---|---|---|---|
    | production (baseline) | 6,950 | −0.54 (−1.20 to +0.09) | −0.34 | — | — | — |
    | `earnings_full_hold` | 5,668 | −0.59 (−1.12 to −0.05) | −0.42 | −0.05 (−0.29 to +0.18) | −0.07 | ADOPT |
    | `event_lock` | 6,709 | −0.52 (−1.19 to +0.12) | −0.32 | +0.01 (−0.01 to +0.04) | +0.02 | ADOPT |
    | `rr_floor_stop_cap` | 162 | −0.64 (−1.40 to +0.17) | −1.10 | −0.11 (−0.95 to +0.73) | −0.76 | ADOPT, flagged "costs return; kept as a risk rule" |
    | `hygiene_all` | 127 | −0.34 (−1.16 to +0.50) | −0.45 | +0.19 (−0.68 to +1.06) | −0.11 | ADOPT |

  - Holm-adjusted one-sided p (variant > production): 1.00 / 0.66 / 1.00 / 0.95. StepM: no variant superior.
  - Left tail, production → `hygiene_all`:
    - 5th-percentile idea −12.9% → −7.1%; worst idea −48.4% → −9.7%.
    - Stops gapped through: 14.5% → 14.0%.
    - Ideas with earnings inside the hold: 16.4% → 0%.
  - **All three rules are now production defaults** (`EARNINGS_BLACKOUT_MODE = "full_hold"`, `EVENT_LOCK_ENABLED`, `RR_FLOOR_STOP_CAP_ENABLED`).
    - The R:R floor removes about 98% of ideas, because T1 is a median 0.8 R away. Expect about 2–3 new ideas a month instead of about 7 a day.
    - They apply to new ideas only. The open TEVA, SNOW and PBT ideas keep being tracked to their exits.
  - Royalty trusts (PBT among them) are excluded from the universe; they produced 0 issued ideas in the evidence run.
  - Live checks (tests): a TEVA-like idea is blocked by the full-hold rule (next report est. 2026-10-28, inside the hold). PBT is locked by its Form 425s since 2026-05-18. A SNOW-like stop of −14.2% exceeds the cap.
  - SEC calendar vs the stored `earnings_calendar`:
    - Overall, 88.6% of 1,656 match within ±1 session, just under the 90% target.
    - Among SEC 8-K item 2.02 filers the match is 97.2%.
    - Most misses (144) are foreign private issuers that report on Form 6-K and so have no SEC calendar. Only 28 recent dates truly disagree.
  - Production baseline book:
    - Placebo: 89.5th percentile. The paired diff vs the placebo median is −0.14 (CI −0.58 to +0.31). The placebo means have a fat right tail (95th percentile +2.34), probably from bad prices among randomly drawn small caps, so the median-based diff is the robust figure.
    - Costs: break-even −16.8 bps/side, so it loses before costs. With the Corwin-Schultz half-spread (mean 0.35%) the book is −1.24%/idea.
    - Survivorship gauge: 4.3% of S&P 500 member-days have no bar (44 members never seen).
  - PEAD now fires: 299 issued ideas at −0.24, and 8,417 setups at −0.53.
- Phase 3 (2026-10-11, R3): signal IC study, GitHub run 38119537011.
  - Setup: `scripts/research_signal_ic.py`, production universe (5,140 stocks with history), 47 monthly non-overlapping dates from Oct 2022. Target: 20-session forward log return − β252 × SPY.
  - Survivorship: today's security master is applied to the whole window, so delisted stocks are missing.

    | Signal | Mean IC | NW t | 1st half | 2nd half | LATE | R3 valid |
    |---|---|---|---|---|---|---|
    | MOM12_1 | 0.029 | 1.81 | 0.038 | 0.021 | 0.001 | no |
    | RESMOM (34 months) | 0.005 | 0.37 | 0.015 | −0.006 | −0.006 | no |
    | HI52 | 0.074 | 3.88 | 0.079 | 0.068 | 0.053 | yes |
    | −FIP | 0.003 | 0.25 | 0.008 | −0.003 | −0.015 | no |
    | −REV21 | −0.012 | −1.23 | −0.004 | −0.020 | −0.021 | no |
    | −RVOL252 | 0.097 | 5.21 | 0.121 | 0.073 | 0.059 | yes |
    | **C6** | **0.062** | **3.30** | 0.082 | 0.044 | 0.023 | **yes** |

  - Report-only signals (IC, NW t): MOM63 −0.007 (−0.47), β252 −0.058 (−2.62), MAX21 −0.071 (−4.45), ADV20 0.031 (2.91), sector-ETF momentum −0.021 (−1.34).
  - **Decision: C6 is adopted as the ranking signal.** It is valid, and C6 − MOM12_1 = +0.033 IC (CI +0.013 to +0.055). Phases 4 and 7 therefore run.
  - Reading:
    - C6's strength comes from low volatility and 52-week-high proximity: a low-risk / betting-against-beta tilt, measured beta-adjusted, in a bull sample.
    - Plain 12-1 momentum does not pass in this universe and window.
    - An IC is a ranking statistic, not a tradable return.

- Phase 4 (2026-10-11, R2, Holm over six): run 38122679781.
  - Production here is the post-Phase-2 book: 127 ideas, −0.34%/idea beta-adjusted (CI −1.16 to +0.50), LATE −0.45.

    | Variant | Ideas | Beta-adj. %/idea | Paired full (CI) | Paired late | Holm p | R2 |
    |---|---|---|---|---|---|---|
    | `rank_select` (C6 ≥ 80th pct) | 1,302 | −0.60 | −0.25 (−1.15 to +0.61) | −0.06 | 1.00 | KILL |
    | `cs_mom_12_1` (C6 top 10% screen) | 113 | −0.32 | +0.02 (−0.40 to +0.44) | +0.18 | 1.00 | KILL |
    | `momentum_rank_book` | 2,012 | +0.40 (−0.90 to +1.75) | +0.75 (−0.56 to +2.13) | +2.07 | 0.94 | KILL |
    | `trail_after_t1` | 126 | −0.34 | +0.00 (−0.18 to +0.20) | −0.10 | 1.00 | KILL |
    | `mr_liquid_only` | 127 | −0.34 | 0.00 | 0.00 | 1.00 | KILL |
    | `no_rebound_entries` (no Pullback / Mean Reversion ideas) | 127 | −0.34 | 0.00 | 0.00 | 1.00 | KILL |

  - Nothing adopted. StepM finds no variant superior.
  - `mr_liquid_only` and `no_rebound_entries` change nothing, because the 127-idea book holds no Mean Reversion or Pullback ideas (Pullback setups never reach 65).
  - The momentum rank book is the only positive point estimate. Its CI includes zero, and it is a different product (about 10 ideas a week), so treat it as a hypothesis, not a result.
- Phase 5 (2026-10-11, R4 / R2):
  - Setup: `src/patterns.py` uses ATR-zigzag and rolling-window pivots with confirmation indices (design adapted from neurotrader888/TechnicalAnalysisAutomation, MIT). The H&S, inverse H&S, double-bottom, flat-base / contraction and bull-flag templates are point-in-time; `scripts/test_no_lookahead.py` checks this.
  - R4, incremental IC residualized on MOM12_1 and HI52 (IC run 38119537011):
    - Only `hs_top_confirmed_20d` qualifies (+0.035, t 3.04, positive in both halves). Its sign is the opposite of a bearish pattern: recent H&S tops did slightly *better*.
    - The rest are not kept: inverse H&S −0.014; double-bottom breakout −0.037 (t −4.4); base depth −0.063 (t −4.6); contraction count +0.002; distance to neckline −0.012; breakout volume ratio −0.044 (t −4.9).
    - The kept feature is not used in ranking: C6 is the adopted signal, and R4 only permits keeping it. The features stay computed in research only.
  - R2: the `hs_top_veto` variant gives −0.02 (CI −0.07 to 0.00), late −0.06: KILL.
- Phase 6 (2026-10-11): display only.
  - Cards show the strategy's realized expected R and exit mix. T2/T3 are labelled "rarely reached in backtest (x%)" below a 10% hit rate. An idea issued without a known earnings date shows an earnings-unknown note.
  - Browser-local sizing guidance (see "What this is not").
  - An edge banner whose wording comes from `frontend/src/lib/backtest-verdict.ts`.
- Phase 7 (2026-10-11, R5): run 38120977022.
  - Setup: LightGBM on monthly feature ranks, purged walk-forward (one month purged), 34 out-of-sample months.
  - Out-of-sample IC 0.070 (NW t 3.66) vs C6 0.065 on the same months. Paired diff +0.005 (CI −0.035 to +0.043); first half −0.007.
  - **KILL.** There was no production model path to delete; the research code stays in `scripts/research_signal_ic.py --ml`.
- **Final verdict (2026-10-11, EDGE CLAIM, run 38122679781): no edge.**
  - The final production book: 127 ideas, 38 signal months.
    - Beta-adjusted −0.34%/idea (CI −1.16 to +0.50); LATE −0.45 (44 ideas).
    - Excess vs SPY −0.74 (CI −1.71 to +0.26); mean β 0.80.
  - Placebo: 80th percentile. The paired diff vs the placebo median is +1.35 (CI +0.40 to +2.21): stocks drawn at random with the same ATR-scaled plan did worse. This is not one of the EDGE CLAIM criteria.
  - StepM: nothing superior. Break-even cost: −7 bps/side.
  - None of the four EDGE CLAIM criteria passes. The cards say "Backtest: no edge vs SPY after beta; holding SPY did as well or better."
  - Evidence: `config/strategy_performance.json` was adopted from this run (108 fields reproduced). It now includes PEAD, expected R and the exit mix.
  - Trials run: 4 (Phase 2), 7 signals + 7 pattern features (Phases 3 and 5), 6 + 1 variants (Phases 4 and 5), 1 ML. Registry: `outputs/research_trials.jsonl` (local), with smoke runs marked.

## Backtest

- `scripts/validate_backtest_pipeline.py` replays the production pipeline using the shared `src/pipeline_steps.py`, with walk-forward evidence and confidence intervals from a signal-month block bootstrap.
- **Adopting evidence**, after any change to strategy, scoring or exit logic:
  1. Run `gh workflow run backtest_production_universe.yml` and wait for it.
  2. Download the artifact: `gh run download <id> -n backtest-production-universe -D outputs/production_universe_run_<id>`.
  3. Adopt it: `python scripts/adopt_strategy_evidence.py outputs/production_universe_run_<id> --run-id <id>`. This rebuilds `config/strategy_performance.json` from the setup-trades CSV and writes nothing unless every field of the run's own `strategy_performance_production_universe.json` reproduces exactly.
  4. Commit the result.

  The local cache_start (516-stock) run writes its evidence to `outputs/strategy_performance_cache_start.json` and never touches `config/`.
- Every trade carries a point-in-time `beta` (252 sessions before the signal) and `beta_adjusted_pct` (net − β × SPY return over the trade window). The stats include `mean_beta_adjusted_pct` with its CI, and the summary includes `edge_beta_adjusted_verdict_*`. Judge selection skill on the beta-adjusted and vs-SPY verdicts, not on `edge_verdict_*` (absolute return).
- The cached phase-1 scans are keyed by strategy code, indicators, data extent and `REGIME_STRATEGY_MAP`, so switching a strategy on or off forces a rescan.
- `--universe production` backtests today's broad US security master (about 5,600 tickers) instead of the first cache file's 516 stocks. It fails if the security master is unavailable.
  - Run it on GitHub: the workflow `backtest_production_universe.yml` (manual dispatch, optional `limit` for a smoke test, about 2–4 h) uploads the summary, trades and setups, and `outputs/strategy_performance_production_universe.json` as an artifact.
  - It never writes `config/`; adopting its evidence is the step above.
  - Survivorship: today's list is applied to the whole window, so every period is chosen with hindsight, and small caps are biased upward the most.
  - `--phase1-only` caches the scans. Trades carry `adv20_usd` (20-day dollar volume at the signal) for size tiers.
  - Walk-forward evidence is incremental (`WalkForwardEvidenceBook`) and matches daily re-aggregation exactly.
- `--variants all` (or a comma-separated list) evaluates alternatives in the same pass, against identical setups, evidence and execution. Selection variants: `rs_momentum`, `entry_info`, `rs_momentum_entry_info`, `fundamentals_in_score`. Strategy screening variants (`STRATEGY_VARIANTS`, candidate filters): `w52_off`, `w52_original_rules`, `w52_breakout`. They need 52-Week High re-enabled in `REGIME_STRATEGY_MAP`; otherwise they do nothing. A strategy variant keeps that strategy's walk-forward evidence from all its setups, so a winner must be built into the strategy and re-validated with a full run. Each variant keeps its own book, with one active idea per ticker. Results go to `outputs/production_backtest_variants.json`, with paired CIs against production. The switches live in `src/quant_config.py` (`MOMENTUM_MODEL`, `ENTRY_LOCATION_MODE`, `CONTEXT_SCORE_COMPONENTS`) and are read through `SelectionSettings` by both the scan and the backtest. `fundamentals_in_score` uses point-in-time SEC fundamentals: facts filed strictly before the signal date, with the 200-day freshness rule.
- Known limitations (listed in its output): survivorship (gauge: 4.3% of S&P 500 member-days missing), earnings dates only for SEC 8-K item 2.02 filers (foreign 6-K filers have none, and future dates are assumed known at the signal), no historical context data, no VIX history, and thresholds that were hand-tuned before the harness existed. The 516-stock universe is narrower than production's (see Current status).

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

## Swing signals program (pre-registered 2026-10-11)

### 1. DEFINITIONS (exact; backtest and live code use the same functions)
Calendar and timing
- Session = NYSE trading session (SessionCalendar / trading_sessions_between). t = signal session.
- A decision at t uses daily bars through the close of t and SEC filings with acceptance time <= 20:00 ET on t. Filings accepted later belong to t+1.
- Fill = open of session t+1. The fill session is holding session 1.
- Resting exits (stop, target) are GTC orders placed at the fill, checked on each holding session in this order:
  - stop: if open <= stop, exit at the open; else if low <= stop, exit at the stop;
  - target: if open >= target, exit at the open; else if high >= target, exit at the target;
  - if stop and target are both touched on one bar: STOP_FIRST (as in fast_outcome).
- Rule exits (time limits, sleeve rule exits, next-earnings exit, event-lock exit) are DECIDED at a close s and EXECUTED at the open of s+1.
- Report-only sensitivity "close-checked stops": stops are evaluated on closes and executed at the next open.
Prices and indicators
- Download with yfinance auto_adjust=False, actions=True. Yahoo's Open/High/Low/Close are split-adjusted; Adj Close also adjusts for dividends.
- Total-return OHLC = OHLC x (Adj Close / Close). Use it for indicators, returns and backtest fills.
- Unadjusted close(t) = Close(t) x the product of split ratios effective after t. Use it ONLY for the $5 price filter.
- Dollar volume = Close x Volume (invariant to split adjustment).
- Test against known splits (NVDA 2024 10:1, AAPL 2020 4:1).
- Live levels are quoted in actual prices on the signal date: the latest bar's Close equals the traded price.
- MA_n(t) = mean total-return close over sessions t-n+1..t. "Rising 200-day MA" = MA200(t) > MA200(t-20).
- ATR14 = Wilder ATR(14) (src/patterns.wilder_atr). RSI2 = Wilder RSI with period 2. SMA5 = MA_5.
- ADV20(t) = mean dollar volume over t-19..t.
- Liquid(t): unadjusted close >= $5, ADV20 >= $5,000,000 nominal, and >= 252 sessions of history. Give liquid_mask an optional raw-close argument; keep its old default behaviour unchanged.
- C6pct(t) = percentile (0,1] of composite_c6 among the names of the evaluated universe that are Liquid on t. Rank within U1 for U1 tests and live, and within U2 for U2 tests. Inputs exactly as compute_features/composite_c6: the equal-weight ranks of MOM12_1, RESMOM, HI52, -FIP, -REV21 and -RVOL252, with >= 4 of the 6 present.
- beta(t) = BETA252 from compute_features at t.
- MAD(t) = MA21(t) / MA200(t) (report-only).
Universes
- U1 (broad): today's security master (production_universe), restricted to Liquid(t). It is survivorship-biased: delisted names are missing.
- U2 (point-in-time S&P 500): symbols that are S&P 500 members on t per data/reference/sp500_ticker_start_end.csv (mapped with yahoo_symbol), have a bar on t, and are Liquid(t).
  - Measure and report the member-days with no bar, by year.
  - Flag symbols whose first yfinance bar comes after their membership start (likely recycled or renamed) and exclude member-days before that first bar.
- U1-top500(t): the 500 U1 names with the highest ADV20 on t.
Events
- Earnings reaction sessions = earnings_events() from SEC 8-K item 2.02 (reaction_session of the acceptance time).
- "Earnings within 5 sessions" at t: a reaction session falls in t+1..t+5.
  - Backtest: SEC reaction sessions, assumed known 5 sessions ahead (a listed limitation).
  - Live: confirmed dates from the earnings_calendar table; else estimate_next_earnings() from SEC history; if the date is unknown, issue the signal and tag it earnings_unknown.
- Event-locked(t): event_lock(filings accepted by the cutoff, as_of=t) is not None, OR price_pin(total-return close) is true on t.
- NEG8K = 8-K items {1.03, 2.04, 2.06, 3.01, 3.02, 4.01, 4.02}. Verified meanings per SEC Form 8-K instructions:
  - Item 1.03: Bankruptcy or Receivership
  - Item 2.04: Triggering Events That Accelerate or Increase a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement (Default / Acceleration)
  - Item 2.06: Material Impairments
  - Item 3.01: Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; Transfer of Listing
  - Item 3.02: Unregistered Sales of Equity Securities (Dilution)
  - Item 4.01: Changes in Registrant's Certifying Accountant (Auditor change / resignation)
  - Item 4.02: Non-Reliance on Previously Issued Financial Statements or a Related Audit Report or Completed Interim Review (Restatement)
Returns
- net_pct = (exit - fill) / fill x 100 - 2 x 0.10 (10 bps per side is the primary cost). Also report cost_scenarios() and the break-even cost per side.
- spy_pct = SPY return between the same execution points (SPY open on the fill session; SPY price on the exit's execution point).
- excess_pct = net_pct - spy_pct.
- beta_adj_pct = net_pct - beta(t) x spy_pct. This is the PRIMARY METRIC.
- Signal month = calendar month of t. CIs are signal-month block-bootstrap 95% intervals (10,000 resamples, fixed seed; existing machinery).
Book rules
- One open signal per (sleeve, ticker).
- Cooldown: no new signal for the same (sleeve, ticker) until 10 sessions after its exit execution.
- Combined live book: one open signal per ticker across all sleeves. If several sleeves fire for the same ticker on the same session, priority is E > I > N > P > T.
- Standalone sleeve evaluation (the PASS rules) ignores other sleeves.
- Equal unit per signal. The track record is the sum of per-signal returns in units: no compounding, no sizing.
Placebo (built on PlaceboEngine)
- For each signal, draw a random ticker from the same universe that is Liquid and not Event-locked on t and is not the signal's ticker. It fills on the same session.
- Its stop and target use the same ATR14 multiples, applied to the placebo's own ATR14 and close.
- Max hold = the sleeve's max hold. If the real signal ended on a non-price exit, the placebo exits on the same execution session.
- 200 runs with stable_seed(sleeve, universe, run).
- Report the book's percentile among the 200 placebo means, and the paired diff vs the placebo median with a block-bootstrap CI.
- Report-only "placebo-trend" for T: draws restricted to names with Close > MA200 and a rising MA200 on t.

### 2. SLEEVES (pre-registered; no parameter may change after results exist)
Common gates G0 for every BUY at t:
  - the ticker is in the evaluated universe and Liquid(t);
  - not Event-locked(t);
  - no open signal and no cooldown for (sleeve, ticker);
  - no earnings within 5 sessions.

T - Trend (the drafted Momentum Signals rule)
  BUY at the close of t if: G0; C6pct(t) >= 0.90; Close(t) > MA200(t); MA200(t) > MA200(t-20).
  Levels (C = Close(t), A = ATR14(t)): stop = C - 3A; target = C + 6A (2R).
  Exits:
    - stop or target (resting orders);
    - time: decided at the close of holding session 60;
    - event lock: decided at any close where Event-locked is true.
  Open T signals hold through earnings (WARN only).

E - Earnings reaction (EAR + abnormal volume)
  Event d0 = an 8-K item 2.02 reaction session. r = total-return close-to-close return. b = the stock's SECTOR_TO_ETF ETF, or SPY when the ETF has no bar.
  EAR = sum over sessions d0-1..d0+1 of (r_stock - r_b).
  AVOL = mean volume(d0-1..d0+1) / mean volume(d0-63..d0-8) - 1.
  Reference set: all events in the same universe whose reaction session falls in the 63 sessions ending at d0-1. At least 50 events are required; otherwise no signal.
  EARpct and AVOLpct = percentiles of EAR and AVOL within the reference set.
  BUY at the close of t = d0+1 if: G0; EAR > 0; EARpct >= 0.80; AVOLpct >= 0.80.
  Levels: stop = Close(t) - 3 x ATR14(t); no target.
  Exits:
    - stop (resting);
    - next earnings: decided at the close of (next reaction session d0' + 1);
    - time cap: decided at the close of holding session 75;
    - event lock.
  E holds through the next report by design; the card must say so.

N - Catalyst gap
  BUY at the close of the gap session t if all hold:
    - G0;
    - Open(t) / Close(t-1) - 1 >= 0.05;
    - Volume(t) >= 3 x median Volume(t-50..t-1);
    - Close(t) >= Open(t);
    - Close(t) - Low(t) >= 0.5 x (High(t) - Low(t));
    - at least one 8-K or 6-K accepted in (16:00 ET on t-1, 20:00 ET on t];
    - none of those 8-Ks carries a NEG8K item.
  Never enter at the gap-day open: the fill is Open(t+1).
  Levels: stop = Close(t) - 3 x ATR14(t-1) (the pre-gap ATR); no target.
  Exits:
    - stop (resting);
    - time: decided at the close of holding session 40;
    - event lock.

P - Large-cap pullback
  Universe: U1-top500 for U1 tests and live; all of U2 for U2 tests.
  BUY at the close of t if: G0; Close(t) > MA200(t); MA200(t) > MA200(t-20); RSI2(t) < 10; Close(t) < SMA5(t).
  Levels: stop = Close(t) - 3 x ATR14(t); no target.
  Exits:
    - stop (resting);
    - rule exit: decided at the first close s on or after the fill session with Close(s) > SMA5(s);
    - time: decided at the close of holding session 10;
    - event lock.

I - Insider cluster buying
  Data:
    - History: SEC Insider Transactions Data Sets (Forms 3/4/5, 2006Q1 onward; https://www.sec.gov/dera/data/form-345). Take table and field names from its readme.
    - Live: daily Form 4 XML from EDGAR for U1 issuers, parsed into the same schema.
  Qualifying purchase (all must hold):
    - Form 4 original (not /A), non-derivative table;
    - transaction code P, acquired (A);
    - the reporting owner is a director or officer;
    - shares x price >= $10,000;
    - AFF10B5ONE is not true, where the field is present;
    - not routine. Routine = the same reporting owner made a code-P purchase in the same calendar month in each of the three prior calendar years.
  Cluster completed on filing date D (all must hold):
    - >= 2 distinct reporting owners made qualifying purchases in the same issuer;
    - their filing dates fall within [D - 30 calendar days, D];
    - total value >= $100,000;
    - at least one qualifying filing came after the issuer's last fired I signal.
  Timing: t = the first NYSE session strictly after D. The data sets carry a date, not a time, so this is conservative; live uses the same rule.
  BUY at the close of t if: G0, and a cluster completed on D.
  Levels: stop = Close(t) - 3 x ATR14(t); no target.
  Exits:
    - stop (resting);
    - time: decided at the close of holding session 40;
    - event lock.

### 3. OVERLAYS (tested), REPORT-ONLY ANALYSES, SHADOW-ONLY LAYERS
Tested overlays (only on sleeves that pass; adoption by rule O in section 4):
  G1 regime/breadth gate (idea from RyanJHamby/stock-screener, MIT): new BUYs at t only if SPY Close(t) > SPY MA200(t) AND breadth(t) >= 0.40. breadth(t) = the share of the evaluated universe's Liquid names with Close > MA200.
  V1 deterministic 8-K veto: no BUY at t if the ticker has an 8-K with any NEG8K item accepted within the 10 sessions ending at t.
  X1 pre-earnings exit (T only): for the next confirmed reaction session d0 inside the hold, the exit is decided at the close of session d0-2 and executed at the open of d0-1. Evaluate it paired, on T's base entries.
Report-only (never adoptable in this program):
  - Signal replication on the holdout: monthly IC (20-session beta-adjusted target) of C6, MOM12_1, RESMOM, HI52, -FIP, -REV21, -RVOL252 and MAD. Run in U1 and U2, with NW t and the three five-year blocks.
  - Pattern replication: incremental IC of every PATTERN_FEATURES feature, residualized on MOM12_1 and HI52, at U2 and U1 month-ends. If breakout_volume_ratio or base_depth_pct is negative with t <= -3 in BOTH universes, record it in CLAUDE.md as a candidate veto for a FUTURE pre-registered study. Do not use it now.
  - placebo-trend for T.
  - Close-checked stops.
  - Cost scenarios.
  - Recent period: signals Oct 2022-Oct 2026. This is unseen data only for E, N, P and I.
  - Top-3-per-day sub-book. Order: sleeve priority; then C6pct descending (T), EARpct descending (E), cluster value descending (I), gap descending (N), RSI2 ascending (P).
  - Combined book: signals per month, and concurrent open signals (median, 95th percentile, max) = units of capital needed.
  - Monte Carlo trade-order shuffle (idea from Jesse, MIT): 2,000 shuffles per passing sleeve, giving a max-drawdown distribution in units.
Shadow-only (forward data only; rules in section 4):
  L1 LLM negative-event veto.
  D1 days-to-cover veto, plus a report-only 2021-2026 retrospective.
  A1 analyst archive.

### 4. PRE-REGISTRATION (commit BEFORE any new backtest or IC run)
Holdout: signals with t from 2008-01-02 to 2022-09-30. Exits may run past 2022-09-30 on real data. Oct 2022 onward is the window C6 was chosen on and is NOT part of the holdout.

S-PASS rules (per sleeve, holdout only; U2 is primary because its membership is point-in-time):
  P1 U2: mean beta_adj_pct > 0 and the 95% CI lower bound > 0.
  P2 U2: the book mean is above the 95th percentile of its 200 placebo means, AND the paired diff vs the placebo median is > 0 with CI lower bound > 0.
  P3 U1 (confirmation): mean beta_adj_pct > 0 AND placebo percentile >= 90.
  P4 U2 stability: mean > 0 in at least 2 of the blocks [2008-01..2012-12], [2013-01..2017-12], [2018-01..2022-09], and no block mean < -0.50.
  P5 Multiple testing: Holm over the five sleeves, applied to the one-sided month-block bootstrap p of U2 mean beta_adj_pct > 0. The adjusted p must be < 0.05.
  P6 Costs: U2 break-even cost >= 15 bps per side.
  P7 Sample: >= 100 U2 signals spread over >= 36 distinct signal months.
  P8 (T only): C6 replication in the U2 holdout has mean IC > 0 with NW t >= 2.0.
  Outcome:
    - P1-P8 all pass -> LIVE on U1.
    - P1, P2 and P4-P8 pass but P3 fails -> LIVE-SP500 (live restricted to current S&P 500 members).
    - Anything else -> SHADOW: computed, tracked and shown, never alerted as a BUY.
    - If a sleeve's data cannot be built -> SHADOW with reason "untested".
Rule O (overlays; U2 holdout; Holm within each overlay family G1 and V1 across the LIVE sleeves; K = kept signals, R = removed signals):
  G1 or V1 is ADOPTED for a sleeve only if all three hold:
    - |R| <= 0.5 x (|K| + |R|);
    - the U1 point estimate of mean(R) - mean(K) is <= 0;
    - (a) mean(R) < mean(K) with one-sided Holm-adjusted p < 0.05, OR (b) mean(R) <= mean(K) AND the worst rolling-252-session equal-unit cumulative beta_adj P&L improves by >= 25%.
  X1 is ADOPTED only if either holds:
    - the paired per-signal diff (X1 - base) is > 0 with CI lower bound > 0;
    - the paired mean is >= -0.05 AND the 5th-percentile signal return improves by >= 20%.
  Overlays never turn a SHADOW sleeve LIVE.
Live kill rules (per LIVE sleeve; closed live signals; beta_adj_pct at 10 bps per side; m_h and s_h = holdout mean and SD per signal in the universe the sleeve trades):
  K3: first checked once the sleeve has >= 63 live sessions AND >= 20 closed live signals, then monthly. Move to SHADOW if live mean < m_h - 2 x s_h / sqrt(n).
  K12: checked at >= 252 live sessions AND >= 50 closed signals. Move to SHADOW if live mean <= 0 OR the live paired diff vs the live placebo median is <= 0. The live placebo uses the same construction, 200 runs.
  KDD: checked at any time. Move to SHADOW if the cumulative equal-unit beta_adj P&L since go-live (closed signals plus open ones marked at the close) falls below the 1st percentile of 2,000 month-block bootstrap paths of the holdout with the same number of signals.
  On any kill:
    - send an alert and log a trial;
    - only a new pre-registered study can reverse it;
    - open signals of a killed sleeve keep getting their SELLs. Never orphan an open signal.
Shadow adoption rules:
  L1:
    - A BUY is L1-flagged if a validated LLM label with direction -1 and materiality >= 2 exists for the ticker, with first_seen within the 10 sessions ending at t.
    - Pool live and shadow sleeves. After >= 126 sessions of logging and >= 30 flagged BUYs, ADOPT as a BUY veto if mean beta_adj(flagged) < mean beta_adj(unflagged) with month-block bootstrap one-sided p < 0.10, AND flagged BUYs are <= 20% of all BUYs.
    - Otherwise it stays a WARN label. Re-evaluate every 126 sessions on cumulative data; each evaluation is a logged trial.
  D1:
    - A BUY is D1-flagged if the ticker's latest PUBLISHED days-to-cover is in the top decile of U1.
    - Same adoption rule as L1, but after >= 252 sessions.
    - The 2021-2026 retrospective is report-only (it is not unseen data).
  A1:
    - Archive only. No analyst-based rule may be pre-registered before 252 sessions of archive exist.
    - Never display target-implied upside as an expected return.
Wording rule:
  - For LIVE sleeves only, the UI may say "Validated on the 2008-2022 backtest" with the U2 and U1 numbers, always followed by "Backtests and short live records do not prove future profit."
  - Never write "edge" or "profitable".
Trial list (record every run in outputs/research_trials.jsonl with phase "swing"; mark smoke runs):
  - S-T, S-E, S-N, S-P, S-I (the Holm family of 5);
  - O-G1/<sleeve>, O-V1/<sleeve>, O-X1/T;
  - the report-only items of section 3;
  - live K3, K12 and KDD checks, and L1 and D1 evaluations, as they occur.

Registry location: `outputs/research_trials.jsonl`

### Swing signals program: results and operations

#### Swing fix (2026-10-12 to 2026-10-14, branch `swing-fix`, PR #3)
- PR #2 had been merged with placeholder logic and fabricated results. Its research and live scripts used constants instead of inputs:
  - `c6_pct = 0.95`, EAR/AVOL percentiles `0.85`;
  - `has_8k_or_6k=True`, `has_neg8k=False`, `is_g0=True`;
  - a fixed $150,000 insider cluster, `rsi2 = 8.5`, `beta or 1.0`;
  - a noise "placebo".
  The sleeve results, the pattern "t < -2.5" statement and the replication figures previously recorded here were not produced by any run. They have been removed.
- Research and live now share `src/swing/` (`engine.py`, `data.py`, `live.py`, `exits.py`), so both use the same point-in-time definitions:
  - T uses C6pct;
  - E uses 8-K item 2.02 reactions, EAR vs the sector ETF, AVOL, and percentiles against the prior 63 sessions' announcers;
  - N uses real 8-K/6-K lookups with a 20:00 ET cutoff;
  - I uses the SEC Form 3/4/5 data sets plus live Form 4 XML;
  - G0 runs the real checks, and beta is BETA252;
  - the placebo is a `PlaceboEngine` subclass that draws random eligible stocks, 200 runs.
- Tests:
  - `tests/test_swing_pit.py`: placeholder scan, input variation, truncation look-ahead harness, G0 gates and placebo;
  - `tests/test_long_history.py`: guard against truncated price histories.
  CI runs pytest.
- Live job (`jobs/generate_swing_signals.py`):
  - It keeps its own incremental price cache (`swing-live-v2-*`).
  - It issues no BUYs unless the latest bar is the latest completed NYSE session, but still reconciles exits on stale data.
  - Only LIVE sleeves alert.
- The decide stage writes `config/swing_sleeves.json` and `config/swing/{decisions,replication,patterns}.json` only from a non-smoke GitHub run, with all five sleeves and replication present, at least 98% U1 price coverage, and the price-history check run. Each file records the run id.
- Run 38189718125 (2026-10-12) was not adopted. It reused cached files from a throttled run, and the split check had written NVDA from 2024 only. `scripts/build_long_history.py` now re-downloads every ticker, never replaces a cached file with a history that starts later, and stops retrying once a retry pass recovers nothing.

#### Holdout results (GitHub run 38436737951, 2026-10-13, sha e1190aa; holdout signals 2008-01-02..2022-09-30)
- Data:
  - U1 price coverage 99.98% (5,591 of 5,592).
  - SEC filings for 5,617 of 5,636 tickers.
  - Insider data sets: 84 quarters (2026Q2–Q4 not yet published).
- Beta-adjusted %/signal at 10 bps/side, with signal-month block-bootstrap 95% CIs. "Placebo pct" is the book's percentile among 200 random-stock placebo means.

| Sleeve | U2 signals | U2 beta-adj (CI) | U2 placebo pct | U1 signals | U1 beta-adj (CI) | U1 placebo pct | Mode |
|---|---|---|---|---|---|---|---|
| T Trend | 5,669 | −0.15 (−0.43 to +0.12) | 37.5 | 20,476 | −0.30 (−0.60 to −0.01) | 93.5 | SHADOW |
| E Earnings reaction | 1,144 | +0.17 (−0.70 to +1.11) | 85.5 | 4,073 | +0.24 (−0.80 to +1.32) | 98.0 | SHADOW |
| N Catalyst gap | 411 | −0.71 (−1.74 to +0.29) | 14.5 | 2,242 | −0.92 (−1.83 to +0.05) | 1.0 | SHADOW |
| P Large-cap pullback | 22,696 | −0.16 (−0.23 to −0.10) | 99.5 | 31,233 | −0.13 (−0.20 to −0.05) | 100.0 | SHADOW |
| I Insider cluster | 694 | +0.30 (−0.74 to +1.44) | 87.0 | 3,008 | −0.14 (−1.23 to +1.05) | 68.5 | SHADOW |

- P1 failed for every sleeve, so **all five are SHADOW**. The Holm-adjusted p (P5) is 1.00 for every sleeve.
  - E failed P1, P2, P4 and P5. Its U2 blocks were +1.75, +0.33 and −1.26.
  - I failed P1–P5. Its U2 blocks were +2.10, −0.78 and −0.59.
  - P beats its placebo but loses after beta in both universes, so it fails P1 and P6. Its break-even cost is about 2 bps/side.
  - T fails P8: C6 has U2 IC t 1.65.
- Overlays (G1, V1, X1) were not run, because they apply only to LIVE sleeves.
- Recent period (signals from Oct 2022, report-only), U2 beta-adj: T −0.66, E −0.03, N −0.40, P −0.19, I −1.78.

#### Replication (report-only; same run; monthly 20-session beta-adjusted IC, 177 months)

| Signal | U2 IC (NW t) | U1 IC (NW t) |
|---|---|---|
| C6 | +0.022 (1.65) | +0.031 (2.85) |
| MOM12_1 | −0.006 (−0.41) | +0.002 (0.16) |
| RESMOM | −0.001 (−0.14) | +0.001 (0.15) |
| HI52 | +0.008 (0.51) | +0.023 (1.84) |
| −FIP | +0.009 (0.98) | +0.009 (1.54) |
| −REV21 | +0.012 (1.47) | +0.012 (2.00) |
| −RVOL252 | +0.055 (3.56) | +0.058 (4.41) |
| MAD | −0.015 (−1.01) | −0.005 (−0.39) |

- In the holdout, plain 12-1 momentum does not rank stocks. Low volatility is the only strong component.
- Pattern features (incremental IC, residualized on MOM12_1 and HI52):
  - `base_depth_pct` is −0.039 in both universes (t −4.20 U2, −4.54 U1). Under the pre-registered rule it is recorded as a **candidate veto for a future pre-registered study**, not used now.
  - `breakout_volume_ratio` is t −1.49 in U2 and −2.70 in U1, so it does not qualify.
  - The others are within ±2.7 t.

#### Current status
- All five sleeves are SHADOW: computed, tracked and shown, never alerted as a BUY.
- The Signals tab shows them in a collapsed "Not validated - do not trade" drawer with no BUY action.

#### Operations
- Nightly workflow: `.github/workflows/swing_signals.yml`, schedule `20 1 * * 2-6` (01:20 UTC, Tuesday–Saturday, after each US session).
- Ledger: daily events go to `ledger/YYYY-MM-DD.jsonl` on the orphan branch `signals-ledger`.
- Research: `.github/workflows/swing_research.yml`, which has data, replication, a per-sleeve matrix and decide jobs. The first full run takes about 1 h with a warm cache and about 5–6 h cold.
- Optional secrets: `APPRISE_URLS` (alerts; LIVE sleeves only), `GROQ_API_KEY` / `GEMINI_API_KEY` (shadow LLM labels), `FINNHUB_API_KEY` / `FMP_API_KEY` (analyst archive). Missing secrets degrade to unavailable. As of 2026-10-14 only `SEC_USER_AGENT`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and `TIINGO_API_KEY` are set.

#### Known limitations (measured)
- U2 survivorship: S&P 500 membership is daily (fja05680 start/end dates). Yahoo, however, has no history for most members that later delisted or were acquired; it keeps a stub row for such symbols (AVB, EA and QRVO stopped trading in 2026).
  - Member-days with no bar: 39% in 2008, falling to 8% in 2022.
  - 52 symbols have a first bar after their membership start (recycled or renamed symbols, and stubs); their earlier member-days are excluded.
- U1 is today's security master, so delisted stocks are missing throughout.
- Earnings dates come from SEC 8-K item 2.02 and are assumed known 5 sessions ahead. Foreign 6-K filers have none.
- The insider data sets carry a filing date, not a time, so t is the first session after the filing date.

#### Credits and licences
- fja05680/sp500 (MIT): historical S&P 500 membership (`data/reference/sp500_ticker_start_end.csv`).
- neurotrader888/TechnicalAnalysisAutomation (MIT): pivot design adapted in `src/patterns.py`.
- Apprise (BSD-2-Clause): alert library.
- RyanJHamby/stock-screener (G1) and Jesse (Monte Carlo shuffle): ideas only, no code used.
- No GPL/AGPL code.

#### Database reset (2026-10-14)
- Reason: clear the records written by the placeholder swing engine and the demo data before the fixed engine's first scan.
- See the reset entry under Current status for tables and row counts.
