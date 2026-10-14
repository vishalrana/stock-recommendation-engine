# Backtest results and backtest harness

Moved verbatim from CLAUDE.md on 2026-10-14; CLAUDE.md holds the binding rules and links here.

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

## Swing signals holdout (run 38436737951)

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
