# Research history and decision records

Moved verbatim from CLAUDE.md on 2026-10-14; CLAUDE.md holds the binding rules and links here.

## Status log (CLAUDE.md "Current status", to 2026-10-14)

- **2026-10-14: swing fix merged (PR #3) and recommendation data reset.** All five swing sleeves are SHADOW (research run 38436737951; see "Swing signals program: results and operations").
  - Why reset: remove the records written by the placeholder swing engine and earlier scans before the fixed engine's first run.
  - How: `scripts/clear_and_reset_db.py --yes --reseed-sleeves`. It first backed up all 15 tables (2,797 rows) to `backups/supabase_20261013T230522Z/`, which is kept locally only, gitignored, and must never be committed.
  - Cleared (rows before): `signals` (180), `signals_history` (369), `scan_log` (2), `context_cache` (21). `portfolio_state`, `swing_signals`, `swing_events`, `swing_sleeve_status`, `swing_ledger`, `news_items`, `news_labels` and `analyst_snapshots` were already empty.
  - Kept, unchanged: `earnings_calendar` (1,710), `ticker_metrics` (515), `delisted_tickers` (0). No other tables exist; the `recommendations` view rebuilds from the tables.
  - Afterwards, `swing_sleeve_status` was reseeded from `config/swing_sleeves.json` (T, E, N, P, I all SHADOW). There was no `signals-ledger` branch to delete.

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
