import test from 'node:test';
import assert from 'node:assert';
import { MIN_STRATEGY_SAMPLE, ISSUED_RUN_ID, issuedStatsLine, IssuedStats } from '../src/lib/strategy-sample';

const stats = (n: number): IssuedStats => ({
  n, win_rate_pct: 50, win_rate_ci95: [45, 55], net_expectancy_pct: 0.1, net_expectancy_ci95: [-0.5, 0.7],
  beta_adjusted_pct: -0.5, beta_adjusted_ci95: [-1.2, 0.2],
});

test('strategy stats below 100 ideas are suppressed', () => {
  assert.strictEqual(MIN_STRATEGY_SAMPLE, 100);
  assert.strictEqual(issuedStatsLine('X', { X: stats(99) }), 'insufficient sample (n=99)');
  const shown = issuedStatsLine('X', { X: stats(100) }) || '';
  assert.ok(shown.startsWith('100 ideas') && shown.includes('-0.50%/idea vs SPY after beta (CI -1.20% to +0.20%)'));
  assert.strictEqual(issuedStatsLine('Unknown', { X: stats(500) }), null);
});

test('adopted issued-idea evidence: small strategies are hidden', () => {
  assert.strictEqual(ISSUED_RUN_ID, '38500883442');
  assert.strictEqual(issuedStatsLine('Cross-Sectional Momentum'), 'insufficient sample (n=62)');
  assert.strictEqual(issuedStatsLine('Sector Rotation'), 'insufficient sample (n=8)');
  assert.ok((issuedStatsLine('Trend Following') || '').startsWith('1853 ideas'));
});
