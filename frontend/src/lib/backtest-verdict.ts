// Verdict of the latest production-universe backtest under the pre-registered EDGE CLAIM rule
// (CLAUDE.md, "Profitability program"). The positive wording may be used only when every EDGE
// CLAIM criterion passed: FULL beta-adjusted CI > 0, LATE mean > 0, above the 95th percentile of
// the random-stock placebo, and survives StepM at 5%. Updated by hand from the run's summary.
export const BACKTEST_VERDICT = {
  edgeClaimPassed: false,
  runId: '39384088732',
  line: 'Backtest (run 39384088732): the book loses to SPY after beta, -0.79% an idea (95% CI -1.39 to -0.24, below zero).',
} as const;

// A target counts as "rarely reached" below this backtest hit rate (percent).
export const RARELY_REACHED_PCT = 10;
