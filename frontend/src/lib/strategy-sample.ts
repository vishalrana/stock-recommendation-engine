import issued from './issued-evidence.json';

// Per-strategy stats with fewer ideas than this are hidden on the cards (too few to read).
export const MIN_STRATEGY_SAMPLE = 100;
// ...or from fewer signal months: the CIs resample whole months, and a few month-blocks give no valid 95% CI.
export const MIN_STRATEGY_MONTHS = 12;

type Ci = (number | null)[];
export type IssuedStats = {
  n: number;
  months: number;
  win_rate_pct: number | null;
  win_rate_ci95: Ci;
  net_expectancy_pct: number | null;
  net_expectancy_ci95: Ci;
  beta_adjusted_pct: number | null;
  beta_adjusted_ci95: Ci;
};

export const ISSUED_RUN_ID: string = issued.run_id;
const STATS = issued.strategies as Record<string, IssuedStats>;

export const insufficientSample = (n: number) => `insufficient sample (n=${n})`;
export const tooFewMonths = (n: number, months: number) =>
  `${n} ideas in ${months} month${months === 1 ? '' : 's'}: too few months to judge`;

const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
const ci = (c: Ci) => (c[0] != null && c[1] != null ? ` (CI ${pct(c[0])} to ${pct(c[1])})` : '');

// One line for the card: the backtest's issued ideas of this strategy, or why they are not shown.
export function issuedStatsLine(strategy: string | null | undefined, stats: Record<string, IssuedStats> = STATS): string | null {
  const s = strategy ? stats[strategy] : undefined;
  if (!s) return null;
  if (s.n < MIN_STRATEGY_SAMPLE) return insufficientSample(s.n);
  if (!(s.months >= MIN_STRATEGY_MONTHS)) return tooFewMonths(s.n, s.months ?? 0);
  const parts = [`${s.n} ideas`];
  if (s.win_rate_pct != null) parts.push(`${s.win_rate_pct.toFixed(0)}% won`);
  if (s.beta_adjusted_pct != null) parts.push(`${pct(s.beta_adjusted_pct)}/idea vs SPY after beta${ci(s.beta_adjusted_ci95)}`);
  return parts.join(' · ');
}
