# Research history and decision records

Moved verbatim from CLAUDE.md on 2026-10-14; CLAUDE.md holds the binding rules and links here.

## Status log (CLAUDE.md "Current status", to 2026-10-14)

- **2026-10-14: swing fix merged (PR #3) and recommendation data reset.** All five swing sleeves are SHADOW (research run 38436737951; see "Swing signals program: results and operations").
  - Why reset: remove the records written by the placeholder swing engine and earlier scans before the fixed engine's first run.
  - How: `scripts/clear_and_reset_db.py --yes --reseed-sleeves`. It first backed up all 15 tables (2,797 rows) to `backups/supabase_20261013T230522Z/`, which is kept locally only, gitignored, and must never be committed.
  - Cleared (rows before): `signals` (180), `signals_history` (369), `scan_log` (2), `context_cache` (21). `portfolio_state`, `swing_signals`, `swing_events`, `swing_sleeve_status`, `swing_ledger`, `news_items`, `news_labels` and `analyst_snapshots` were already empty.
  - Kept, unchanged: `earnings_calendar` (1,710), `ticker_metrics` (515), `delisted_tickers` (0). No other tables exist; the `recommendations` view rebuilds from the tables.
  - Afterwards, `swing_sleeve_status` was reseeded from `config/swing_sleeves.json` (T, E, N, P, I all SHADOW). There was no `signals-ledger` branch to delete.

- **2026-10-11: profitability program completed** (see that section). Production then applied the full-hold earnings blackout, the event lock and the stop cap / R:R floor (about 2–3 new ideas a month: run 38122679781, 127 ideas in 49 months, R:R floor on). Later the same day the R:R floor was turned off (commit 03e2169); the configuration without it averages about 45 ideas a month (run 38500883442, 2,227 ideas), with far fewer in earnings season (see "Live Ideas configuration backtest" and "Live vs backtest funnel"). Evidence comes from run 38122679781. The final verdict is no edge.

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

## Research findings (2026-10-10, setup level)

Method: all 27,268 strategy setups from the production-pipeline backtest (Oct 2022 – Oct 2026), point-in-time features, forward net return minus SPY over the same window. Ranking power is measured as the monthly Spearman rank-IC (t-stat over months), with differences given as signal-month block-bootstrap 95% CIs. The sample is almost all bull market, so treat every result as provisional.

- **Momentum sub-score (45–50% of the trend and breakout weights) does not rank stocks.** IC: 52-Week High −0.02 (t −0.8), Cross-Sectional −0.04 (t −2.8), Pullback −0.03, Trend +0.01, Mean Reversion +0.04. It is not reliably positive in either half of the sample, and its quintiles are not monotonic.
- **The composite ≥ 65 cut adds nothing.** Excess per setup, ≥ 65 vs < 65: 52-Week High −0.44 vs −0.46, Cross-Sectional −0.68 vs −0.17, Trend −0.17 vs −0.35. Pullback setups never reach 65.
- **12-month return ranks better.** IC: 52-Week High +0.06 (t 1.8), Cross-Sectional +0.06 (t 2.3), Pullback +0.07 (t 3.2), positive in both halves, which matches the documented momentum anomaly. ATR% also "predicts" (IC +0.05 to +0.14), but that is beta in a rising market, not skill.
- **The entry-location gate keeps the worse setups.** Mean excess: BUY −0.46 (11,626), WAIT −0.26 (14,555), REJECT −0.11 (1,087). For Trend Following, BUY minus blocked is −0.30 (CI −0.60 to −0.03).
- **Fundamentals carry no information (point-in-time SEC, D/E coverage 82%).** Excess with the trait minus without it: D/E < 1 −0.01 (CI −0.27 to +0.28); current ratio > 1.5 +0.21 (−0.23 to +0.62); distress veto −0.01 (−0.54 to +0.53); P/E below median +0.16 (−0.13 to +0.50). Loss-makers beat profitable companies by +1.13 (+0.44 to +1.85), which is speculative beta in a bull sample, not a quality signal.

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

- Trials registry: `config/research_trials.jsonl` (committed), one JSON record per trial per run with its `run_id`. Each run writes `outputs/research_trials.jsonl` (`src/research/trials.py`, uploaded with the artifact, kept 90 days) and the workflow merges it into the committed file on main (`scripts/persist_trials.py`, deduplicated, retried when another run pushed first). Smoke runs that produce statistics are recorded too, marked `smoke`. Backfill 2026-10-20: 85 records from the 12 runs whose artifacts were still available, plus 116 local smoke records (`run_id` null); backtest run 38059174571 (2026-10-10, before the registry) has no artifact left.
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
    - The R:R floor removes about 98% of ideas, because T1 is a median 0.8 R away. Expect about 2–3 new ideas a month instead of about 7 a day (R:R floor on; superseded the same day when the floor was turned off: about 45 a month, run 38500883442).
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

## Live Ideas configuration backtest (2026-10-14, GitHub run 38500883442, sha c88ab5e)

- Why: production applies the full-hold earnings blackout, event lock and stop cap with the R:R floor off (user decision 2026-10-11), a combination never backtested. The harness also gated the stop cap on the retired `RR_FLOOR_STOP_CAP_ENABLED`; backtest and live now share `trade_hygiene.rr_stop_enabled()` and `rr_stop_rejection()` (PR #6). Ad hoc measurement (`trial_phase` adhoc): no rule was changed or adopted.
- Window: signals 2022-10-21..2026-10-09 (996 sessions), production universe (5,592 stocks + 15 sector ETFs; survivorship as in the earlier production-universe runs), 10 bps/side, placebo 200.
- Ideas issued: 2,227 (2,216 closed, 11 censored). Per month: mean 45.2, median 36, range 0–145 (1 of 49 months with none).
- Ideas per trading day: mean 2.23, median 1; 0 ideas on 36.9% of days, 1 on 21.0%, 2–5 on 32.3%, 6+ on 9.7% (max 37).
- Candidates removed, in harness order (each count is among the survivors of the previous filter), from 387,741 scored setups:

  | Filter | Removed |
  |---|---|
  | Score < 65 | 360,641 |
  | Fixed-days earnings blackout (live earnings filter) | 2,979 |
  | Earnings full-hold | 4,848 |
  | Event lock | 821 |
  | Stop cap | 10,698 |
  | Entry-location gate | 3,339 |
  | Already an active idea / issued that day / invalid plan | 2,188 |
  | Issued | 2,227 |

- Results, beta-adjusted %/idea (signal-month block-bootstrap 95% CI):
  - FULL −0.42 (−1.04 to +0.15); excess vs SPY −0.48 (−1.16 to +0.15); mean β 0.88.
  - LATE (signals from 2025-04-09, 951 ideas) −0.36 (−1.46 to +0.66).
  - Placebo: 94.5th percentile; paired diff vs placebo median +0.49 (CI +0.00 to +0.98).
  - Break-even cost −11.2 bps/side (it loses before costs). With the Corwin-Schultz half-spread, −1.09 (−1.70 to −0.53).
  - Left tail: 5th-percentile idea −8.73%, worst idea −18.07%; 11.4% of stops gapped through; 0% of ideas with earnings inside the hold.
  - By strategy (ideas, beta-adj): Trend Following 1,853, −0.54; Mean Reversion 187, +0.58; Post-Earnings Drift 106, +0.32; Cross-Sectional Momentum 62, −1.20; Sector Rotation 8, −1.25. Exits: 1,264 expiry, 950 stop, 2 target.
- Verdict: no edge (every `edge_*_verdict` is NO_EDGE_DETECTED_CI_INCLUDES_ZERO; the EDGE CLAIM criteria fail: FULL CI includes zero, LATE mean < 0, placebo below the 95th percentile).
- Correction: with the R:R floor off, the live book issues about 45 ideas a month, not the 2–3 a month of the floor-on book (run 38122679781, 127 ideas).

## Card evidence switched to run 38500883442 (2026-10-14, PR #7)

- `config/strategy_performance.json` was adopted from run 38500883442 (all 108 setup-level fields reproduce). It replaces 38122679781, but the setup-level evidence is the same: it is measured on all setups (one open per strategy+ticker), which the R:R floor never touched. The two runs differ by one Cross-Sectional Momentum setup out of 23,108, so the score inputs are effectively unchanged.
- New, display only: per-strategy stats of the run's issued ideas (`issued_ideas_by_strategy`, copied to `frontend/src/lib/issued-evidence.json`). The cards show them, and any per-strategy stat with n < 100 shows "insufficient sample (n=X)" (`MIN_STRATEGY_SAMPLE`, `frontend/src/lib/strategy-sample.ts`).

  | Strategy | Issued ideas | Win rate % (95% CI) | Net %/idea (CI) | Beta-adj. %/idea (CI) |
  |---|---|---|---|---|
  | Trend Following | 1,853 | 50.0 (44.9 to 55.1) | +0.05 (−0.58 to +0.68) | −0.54 (−1.24 to +0.15) |
  | Mean Reversion | 187 | 70.1 (42.9 to 74.6) | +1.61 (−1.37 to +2.50) | +0.58 (+0.43 to +1.48) |
  | Post-Earnings Drift | 106 | 58.5 (49.0 to 63.8) | +0.96 (−0.51 to +2.03) | +0.32 (−0.44 to +1.66) |
  | Cross-Sectional Momentum | 62 | insufficient sample | | |
  | Sector Rotation | 8 | insufficient sample | | |

  - Mean Reversion's beta-adjusted CI excludes zero. This is not evidence of an edge: it is one strategy among several looked at, and at setup level Mean Reversion is +0.01 (CI −0.60 to +0.71).
- Survivorship: delisted stocks are missing from the universe, which flatters these results.

## Exit decomposition (2026-10-14, GitHub run 38580020684; research only)

- Question: does the loss come from the stop and exit mechanics or from what is picked?
  - Method: the 2,216 closed ideas of run 38500883442 were re-scored with fixed-horizon exits (no stop, no target; exit at the close of holding day h, with the fill bar as day 1). Same fill (next open), same point-in-time beta, 10 bps/side.
  - Placebo: the same `PlaceboEngine` draws (random liquid stocks, seed 20261011, 200 per idea) under the same exits.
  - `scripts/research_exit_decomposition.py`, workflow `research_exit_decomposition.yml`.
- Self-check: the engine's simulation of the live rules matches the harness's recorded returns (mean absolute difference 0.011 pp; 99.5% of ideas within 0.05 pp), and it reproduces the live book's placebo percentile (94.5).
- Results, beta-adjusted %/idea (signal-month block-bootstrap 95% CI):

  | Exit | Period | n | Book mean (CI) | Placebo mean | Placebo pct | Paired diff vs placebo median (CI) | Break-even bps/side |
  |---|---|---|---|---|---|---|---|
  | Live rules (stop, targets, hold) | FULL | 2,216 | −0.42 (−1.04 to +0.15) | −0.47 | 94.5 | +0.49 (+0.00 to +0.98) | −11.2 |
  | Live rules | LATE | 951 | −0.36 (−1.46 to +0.66) | +0.66 | 72.5 | +0.10 (−0.73 to +0.96) | −8.1 |
  | 5 days | FULL | 2,216 | −0.09 (−0.45 to +0.27) | −0.31 | 95.0 | +0.33 (+0.09 to +0.56) | +5.4 |
  | 5 days | LATE | 951 | −0.17 (−0.82 to +0.44) | +0.06 | 71.5 | +0.16 (−0.20 to +0.47) | +1.3 |
  | 10 days | FULL | 2,216 | −0.35 (−0.90 to +0.17) | −0.50 | 93.5 | +0.42 (+0.08 to +0.75) | −7.4 |
  | 10 days | LATE | 951 | −0.37 (−1.41 to +0.60) | +0.25 | 59.0 | +0.20 (−0.40 to +0.81) | −8.6 |
  | 20 days | FULL | 2,206 | −0.41 (−1.21 to +0.39) | −0.55 | 94.5 | +0.72 (+0.31 to +1.16) | −10.4 |
  | 20 days | LATE | 941 | +0.02 (−1.42 to +1.34) | +0.97 | 75.5 | +0.76 (−0.08 to +1.58) | +10.8 |

  - 10 ideas had fewer than 20 bars of data left; they are censored, not forced to close.
  - Placebo means have a fat right tail in the LATE period (for example, live rules: mean +0.66 against a 95th percentile of +0.71), probably from bad prices among randomly drawn small caps. The percentile and the paired median diff are the robust figures.
- Stop gap-through (run 38500883442, 710 initial-stop exits):
  - 9 ideas opened below their stop at the fill and exited there for about 0% (+0.007% mean).
  - 72 (10.1%) gapped through the stop during the hold. Overshoot past the stop: mean 1.33%, median 0.49%, 95th percentile 6.16%, max 10.12%. Planned vs realized loss from the fill on those 72: 6.10% vs 7.34%.
  - Across all stop-outs during the hold: planned 6.14% vs realized 6.27%. 18 losses exceeded 10%; the worst were FOXA −17.1% (planned −7.8%), M −16.2%, LAUR −14.9% and NVS −13.0%.
  - Some planned losses exceed the 10% cap because the cap is measured from the signal close and the fill opened higher.
- Reading:
  - The stop and exit mechanics are not the main source of the loss. With no stop and no target, a 20-day hold loses the same as the live rules (−0.41 vs −0.42). Gap-through adds about 0.13 pp per stop-out. The stop cap binds on average.
  - The loss grows with the holding period: about −0.02%/idea/day at 5 and 20 days.
  - Random stocks from the same universe also lose after beta in the FULL period at every horizon (−0.31 to −0.55). The negative baseline is the universe vs SPY over these windows (survivorship-flattered small caps, a size factor that beta leaves in), not the exits.
  - Selection: in the FULL period the book beats the placebo median at every horizon, with CIs excluding zero (+0.33 to +0.72). In the cleaner LATE period every paired CI includes zero and the placebo percentiles are 59–76. The selection information is not confirmed out of sample.
- Survivorship: delisted stocks are missing from both the book and the placebo universe, which flatters results.

## R:R floor on vs off (2026-10-14, report only)

- Why it was turned off:
  - Commit 03e2169 (2026-10-11, "Phase 0: R:R floor off (user decision)") split `RR_FLOOR_STOP_CAP_ENABLED` into `STOP_CAP_ENABLED = True` and `RR_FLOOR_ENABLED = False`. The commit and CLAUDE.md record it only as "user decision, not a tested result".
  - No reason is recorded in the commits, the docs or the session transcript.
  - Documented context: the Phase 2 record (commit 8c4157a) notes the floor removed about 98% of ideas (T1 is a median 0.8 R away), leaving about 2–3 new ideas a month, and R1 flagged `rr_floor_stop_cap` "costs return; kept as a risk rule".
- Side by side (both production-universe runs, same window, 10 bps/side, placebo 200):

  | | Run 38122679781 (floor on) | Run 38500883442 (floor off) |
  |---|---|---|
  | Ideas | 127 | 2,227 |
  | Ideas / month | 2.6 | 45.2 |
  | Beta-adj. %/idea (CI) | −0.34 (−1.16 to +0.50) | −0.42 (−1.04 to +0.15) |
  | LATE | −0.45 (44 ideas) | −0.36 (951 ideas) |
  | Placebo percentile | 80.0 | 94.5 |
  | Break-even bps/side | −7.2 | −11.2 |
  | 5th-percentile idea | −7.07% | −8.73% |
  | Worst idea | −9.66% | −18.07% |

  - Neither passes the edge criteria. The floor shrinks the left tail mostly by issuing 17× fewer ideas.
- Survivorship: delisted stocks are missing, which flatters both runs.

## Live vs backtest funnel (2026-10-20; diagnosis, no rule change)

- Question: since the 2026-10-14 reset the nightly scan published one idea (OBE, 2026-10-16) in five sessions (2026-10-12 to 10-16), against "about 45 a month" (2.2 a session) in run 38500883442.
- Live: nightly runs 38482415942 and 38495006619 (both 10-12), 38519581800 and 38588371160 (both 10-13), 38688490634 (10-14), 38857173638 (10-15) and 39006338293 (10-16).
  - All status success. No `[STALE DATA]`, `stale_tickers_skipped` 0, earnings provider budget 0.
  - About 2,540 liquid tickers per run, 271 candidates a session.
  - Counts below come from `signals_history`, latest run per scan date.
- Backtest: per-session funnel from GitHub run 39355364038 (same configuration; harness now writes `production_backtest_funnel_by_date*.csv`). It reproduces run 38500883442: 2,213 ideas, −0.43%/idea beta-adjusted.
- Per session (mean). Live order: earnings filter, stop cap, full-hold, event lock, entry gate. Backtest order: fixed-days, full-hold, event lock, stop cap, entry gate.
- Caveat (added 2026-10-20): the two sides applied the stages in different orders, so only the totals compare, not the per-stage counts. "Score ≥ 65" also compares two formulas: live scores then included analyst and news context, the backtest never did. Both are fixed (see "Parity correction"); from run 39384088732 the harness uses the live order.

  | Stage | Live 10-12..10-16 (5 sessions) | Backtest, all sessions | Backtest, Octobers | Backtest, Oct 10–20 of 2023–25 | Backtest, last 20 sessions |
  |---|---|---|---|---|---|
  | Scored candidates | 271.4 | 389.3 | 326.8 | 352.8 | 336.5 |
  | Score < 65 | 245.2 | 362.2 | 306.7 | 336.6 | 317.7 |
  | Score ≥ 65 | 26.2 | 27.1 | 20.1 | 16.2 | 18.8 |
  | Already active / same ticker that day | 0 | 2.2 | 1.1 | 1.0 | 1.7 |
  | Fixed-days earnings filter | 0.2 | 3.1 | 3.3 | 1.7 | 0.5 |
  | Earnings full-hold | 8.4 | 4.8 | 5.8 | 7.0 | 6.3 |
  | Event lock | 0 | 0.8 | 0.5 | 0.4 | 1.0 |
  | Stop cap | 17.0 | 10.7 | 6.4 | 4.9 | 6.3 |
  | Entry-location gate | 0.4 | 3.3 | 1.8 | 0.9 | 2.1 |
  | Issued | 0.2 | 2.22 | 1.16 | 0.39 | 1.05 |

- Reading:
  - The score stage is not the gap: live passes 26 candidates a session at ≥ 65, as many as the backtest average and more than the backtest's mid-October sessions.
  - The hygiene stage removes almost everything in mid-October, live and in the backtest alike. Backtest October is the lowest month after April (1.16 a session). Oct 10–20 of 2023–2025 issued 9 ideas in 23 sessions, 0.39 a session; 2023 had none in 9 sessions.
  - At the backtest's mid-October pass-through (2.4% of score-qualified candidates), the live week's 131 candidates imply about 3 ideas. One was issued (Poisson probability of one or fewer ≈ 0.17).
  - "About 45 a month" is an annual average: by month the backtest issues 1.05 (April) to 3.75 (November) a session.
  - If the stop cap were off, the full-hold rule alone would block 109 of the 131 (83%). All 47 actual full-hold rejections used estimated dates; only 2 were blocked by the ±5-session band alone.
  - Of the 22 candidates full-hold would let through, 9 have no known earnings date (E, BTE, EDU, VET, OBE, TTE: foreign 6-K filers). In reporting season the unknown-date policy (issue and flag) mostly lets through names whose report probably falls inside the hold. OBE's 6-K history (2025-10-30, 2026-05-07, 2026-07-30) points to about 2026-10-29, inside its hold.
- Live-vs-backtest mismatches found (each reported for a decision; none changed):
  - Context cache: `context_cache` keeps the analyst target but not the analyst rating, so a cache hit loses the 10-point buy-rating bonus. Context falls 25 points (100 → 75, 62.5 → 37.5) and composites 2.5–3.75. The two scans of 2026-10-12 on identical prices had 19 and 9 candidates at ≥ 65. The backtest never has context (weights renormalized).
  - PEAD: live finds the last report only in the weekly-refreshed `earnings_calendar` (1,710 tickers) and counts days from the run date. The backtest uses SEC 8-K item 2.02 dates for every filer and the bar date. Live PEAD produced 0 candidates in five sessions; the backtest averages 19 a session, 4.7 in Oct 10–20.
  - Market date: the scan takes its market date from SPY's latest cached bar. A run before Yahoo posts the session (runs 38482415942 and 38495006619, after the 2026-10-13 close) silently scans the previous session instead of logging `stale_data`.
  - Audit: `rejected_signals_to_insert` is reset after the scoring loop (`jobs/generate_signals.py` around line 1491), so candidates that fail scoring validation are never logged. Reruns of a scan date duplicate `signals_history` (10-12: 356 rows for 178 candidates; 10-13: 520 for 260).
- Survivorship: the backtest universe is today's security master, so delisted stocks are missing, which flatters results.

## Parity correction (2026-10-20; PRs #14–#18)

- Goal: the live Ideas scan implements exactly the configuration the backtest measures, so the forward test is an out-of-sample check of it. No rule was loosened to raise the idea count.
- Root causes (each a live-vs-backtest difference):
  - Context in the score: live composites included analyst and news context (weight 0.10–0.20 by strategy); the backtest never has it. A setup at 65 in the backtest scored about 68.5 live with context 100 and about 62 with context 37.5.
  - Earnings-date look-ahead in the backtest: the fixed-days rule, the full-hold rule (`confirmed = [e.reaction ... if e.reaction > d]`) and Pullback's earnings buffer read the actual later 8-K date. Live only has estimates.
  - Unknown earnings dates: issued and flagged in both; now rejected in both (user decision).
  - PEAD: live read the last report from the weekly `earnings_calendar` (1,710 tickers) and counted days from the run's clock; the backtest used SEC 8-K item 2.02 dates and the bar date. Live PEAD found 0 candidates in the week of 2026-10-12; the first parity scan found 3.
  - Live earnings filter: news sentiment > 0.20 could override the fixed-days blackout and ≤ −0.30 could veto it; the backtest had neither.
  - Market date from SPY's latest bar (a run before the bars were posted scanned the session before); audit rows of scoring failures dropped; reruns duplicated `signals_history`.
- Changes:
  - PR #14: `CONTEXT_SCORE_COMPONENTS = ()`; context is display-only (`CONTEXT_DISPLAY_COMPONENTS`); news no longer reaches the earnings filter. Test `tests/test_score_parity.py`.
  - PR #15: `src/earnings_pit.py` (SEC 8-K item 2.02 reports accepted by 20:00 ET) feeds PEAD, Pullback and `trade_hygiene.earnings_gate` in live and backtest; next report = point-in-time estimate with the ±5-session band; unknown → rejected (`REJECT_UNKNOWN_EARNINGS`); harness hygiene order = live order (stop cap, earnings, event lock).
  - PR #16: market date = latest completed NYSE session from the run's clock; lagging bars → `stale_data`. Test `tests/test_market_date_guard.py`.
  - PR #17: scoring failures logged; `signals_history` idempotent per scan date + ticker + strategy; `scripts/dedupe_signals_history.py`.
  - PR #18: `--replay-session` parity replay (`parity_replay.yml`, `scripts/parity_replay.py`) and the `stop_cap_off` variant.
- Unknown earnings dates before the change (run 39355364038 issued ideas, beta-adjusted %/idea, signal-month block-bootstrap 95% CI):

  | Period | Unknown date | Known date | Unknown − known |
  |---|---|---|---|
  | FULL | +0.34 (−0.80 to +1.43), n 341 | −0.57 (−1.22 to +0.05), n 1,861 | +0.91 (−0.28 to +2.01) |
  | LATE | +0.68 (−1.10 to +2.16), n 187 | −0.62 (−1.82 to +0.52), n 763 | +1.29 (−0.74 to +2.77) |

  Rejecting them removed ideas that did better in this sample; every CI includes zero.
- Supabase (approved): after PR #17, `scripts/dedupe_signals_history.py --yes` backed up `signals_history` (2,033 rows) to `backups/supabase_20261020T064853Z/` and removed the earlier run's rejected rows: 2026-10-12 356 → 178, 2026-10-13 520 → 260; 2,033 → 1,595 rows.

### Parity run (GitHub run 39384088732, sha 4d2f2b7; extra trial "parity-correction-extra")

- Logged in `outputs/research_trials.jsonl` as an extra trial (a parity correction, not a rule search), with the stop-cap trial in the same pass.

  | | Run 38500883442 (before) | Run 39384088732 (parity) |
  |---|---|---|
  | Ideas issued | 2,227 | 1,663 |
  | Ideas a month (mean / median) | 45.2 / 36 | 33.8 / 26 |
  | Ideas a session | 2.22 | 1.66 |
  | Beta-adj. %/idea FULL (CI) | −0.42 (−1.04 to +0.15) | −0.79 (−1.39 to −0.24) |
  | LATE (CI) | −0.36 (−1.46 to +0.66), n 951 | −0.95 (−2.06 to +0.06), n 672 |
  | Excess vs SPY | −0.48 | −0.82 |
  | Placebo percentile; paired diff vs placebo median (CI) | 94.5; +0.49 (+0.00 to +0.98) | 85.5; +0.30 (−0.20 to +0.77) |
  | Break-even cost | −11.2 bps/side | −29.5 bps/side |
  | 5th-percentile / worst idea | −8.73% / −18.07% | −8.93% / −18.58% |
  | Ideas with a report inside the hold (actual, ex post) | 0.0% (look-ahead) | 6.7% (of 1,646 with SEC dates) |

- Ideas a session by calendar month (parity run): Jan 0.99, Feb 2.87, Mar 0.77, Apr 0.69, May 1.64, Jun 1.04, Jul 1.65, Aug 2.07, Sep 1.96, Oct 0.76, Nov 3.32, Dec 2.29 (before: Jan 1.83, Feb 3.44, Mar 1.16, Apr 1.06, May 2.06, Jun 2.12, Jul 2.01, Aug 2.47, Sep 2.85, Oct 1.13, Nov 3.77, Dec 2.93).
- Funnel (live order): 387,275 scored; 27,000 at ≥ 65; stop cap 15,964; earnings unknown 1,256, fixed days 1,355, full hold 2,512; event lock 233; entry gate 2,424; issued 1,663.
- By strategy (issued ideas, beta-adj.): Trend Following 1,347, −1.03 (CI −1.65 to −0.37); Mean Reversion 157, +0.54; PEAD 100, +0.34; Cross-Sectional 42, −0.60; Sector Rotation 8, −1.25.
- Verdict: the FULL beta-adjusted CI is below zero (`edge_beta_adjusted_verdict_all` NEGATIVE_EDGE); LATE includes zero. The earlier figures were flattered by the earnings look-ahead and by the unknown-date ideas (+0.34). No EDGE CLAIM criterion passes.
- Evidence adopted from this run: `config/strategy_performance.json` (108 setup-level fields reproduce) and `frontend/src/lib/issued-evidence.json` (the cards' issued-idea lines; their label "Issued ideas (backtest, live filters)" is now accurate).

### Stop-cap trial (report only; same pass, extra trial)

| | Stop cap on (production) | Stop cap off |
|---|---|---|
| Ideas | 1,654 closed | 4,025 closed |
| Ideas a month (mean / median) | 33.8 / 26 | 82.1 / 68 |
| Beta-adj. %/idea FULL (CI) | −0.79 (−1.39 to −0.24) | −0.82 (−1.38 to −0.28) |
| LATE | −0.95 | −0.88 (CI −2.05 to +0.15) |
| Paired diff off − on, FULL (CI) | | −0.04 (−0.37 to +0.31) |
| Paired diff off − on, LATE (CI) | | +0.06 (−0.53 to +0.65) |
| Placebo percentile | 85.5 | 86.5 |

- The stop cap removes 59% of the ideas without a measurable effect on the return per idea in either period. Nothing was changed.

### Parity replay (GitHub run 39384095078, session 2026-10-19)

- The live nightly scan of 2026-10-19 (run 39377064887) and the harness's replay of the same session (prices re-downloaded, the live evidence file, OBE open as the book):
  - Issued sets equal: none on either side.
  - Candidates: live 238, harness 235; 232 in common, all at the same stage; largest composite gap 0.60 (DRI, at 49.5), four above 0.05, all below 65.
  - Differences: ABBV (Trend Following) and DEO (Cross-Sectional) were candidates in the harness only, both rejected by the stop cap. Cause: the strategy scan saw different price bars (the harness re-downloaded adjusted history on 2026-10-20; the live cache adds bars to history adjusted earlier). Six live-only candidates all scored below 65.
- Survivorship: the universe is today's security master, so delisted stocks are missing, which flatters every result above.
