// Verdict of the latest production-universe backtest under the pre-registered EDGE CLAIM rule
// (CLAUDE.md, "Profitability program"). The positive wording may be used only when every EDGE
// CLAIM criterion passed: FULL beta-adjusted CI > 0, LATE mean > 0, above the 95th percentile of
// the random-stock placebo, and survives StepM at 5%. Updated by hand from the run's summary.
export const BACKTEST_VERDICT = {
  edgeClaimPassed: false,
  runId: '38122679781',
  line: 'Backtest: no edge vs SPY after beta; holding SPY did as well or better.',
} as const;

// A target counts as "rarely reached" below this backtest hit rate (percent).
export const RARELY_REACHED_PCT = 10;
