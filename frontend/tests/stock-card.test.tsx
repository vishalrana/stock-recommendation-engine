import test from 'node:test';
import assert from 'node:assert';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import StockCard from '../src/components/stock-card';
import { Recommendation } from '../src/types/database';

// OBE as stored by nightly run 39006338293 (scan 2026-10-16)
const obe = {
  id: 'obe', ticker: 'OBE', company_name: 'Obsidian Energy Ltd.', strategy: 'Cross-Sectional Momentum',
  status: 'pending', scan_date: '2026-10-16', tier_label: 'Buy', composite_score: 67.95,
  price: 11.97, entry_price: 11.97, reference_entry_price: 11.97, stop_loss: 11.04, target_1: 13.04,
  target_2: null, target_3: null, scale_out_weights: '70/30/0',
  weighted_scaleout_rr: 0.8, weighted_rr_honest: 0.8, weighted_rr: 0.8, risk_reward: 0.8,
  strategy_win_rate: 52.35, strategy_trades: 23107, strategy_expectancy_pct: -0.1368,
  strategy_beta_adjusted_pct: -1.0459, strategy_exit_target_pct: 0.03, strategy_exit_stop_pct: 39.58,
  strategy_exit_expiry_pct: 60.39, earnings_unknown: true, pe_ratio: 39.9, de_ratio: 0.2677, current_ratio: 0.781,
} as unknown as Recommendation;

const render = (livePrice?: number) => renderToStaticMarkup(
  <StockCard recommendation={obe} livePrice={livePrice} isExpanded={true} onToggleExpand={() => {}} onRemove={() => {}} />,
);

test('card: header price says whether it is the scan close or a live quote', () => {
  assert.ok(render().includes('at scan · Oct 16, 2026'));
  const live = render(12.16);
  assert.ok(live.includes('$12.16') && live.includes('live quote') && !live.includes('at scan'));
});

test('card: all-setup stats and issued ideas are labelled apart', () => {
  const html = render();
  assert.ok(html.includes('Win rate, all setups (unfiltered)'));
  assert.ok(html.includes('52% of 23,107 setups · -0.14%/setup'));
  assert.ok(html.includes('-1.05%/setup vs SPY after beta'));
  assert.ok(html.includes('Issued ideas (backtest, live filters)'));
  assert.ok(html.includes('insufficient sample (n=44)'), 'issued Cross-Sectional ideas (n=44) stay suppressed');
  assert.ok(html.includes('all targets &lt;1%'), 'a 0.03% hit share is not shown as 0%');
});

test('card: T1 reward:risk next to the scale-out figure, with the plan explained', () => {
  const html = render();
  assert.ok(html.includes('T1 alone 1.15:1'));          // (13.04 - 11.97) / (11.97 - 11.04)
  assert.ok(html.includes('Scale-out plan 0.80:1'));    // 0.70 x 1.15: the 30% runner closes at breakeven
  assert.ok(html.includes('70% at T1, 30% runner closed at breakeven'));
});

test('card: fundamentals wrap as whole ratios', () => {
  const html = render();
  for (const item of ['P/E 39.9', 'D/E 0.27', 'CR 0.78']) {
    assert.ok(html.includes(`<span class="whitespace-nowrap">${item}</span>`), item);
  }
  assert.ok(html.includes('flex flex-wrap justify-end'));
});

test('card: next earnings is the gate date, labelled estimated, warning by the gate band and window', () => {
  const card = (days: number) => renderToStaticMarkup(
    <StockCard recommendation={{ ...obe, strategy: 'Trend Following', earnings_unknown: false,
      next_earnings_date: '2026-11-20', days_to_earnings: days } as unknown as Recommendation}
      isExpanded={true} onToggleExpand={() => {}} onRemove={() => {}} />,
  );
  const warn = 'Earnings report falls inside the holding period';
  const near = card(27);                    // band 22..32 touches the window 1..22 (20 sessions + 2 buffer)
  assert.ok(near.includes('Nov 20, 2026 (estimated ±5 sessions, in 27 sessions)'));
  assert.ok(near.includes(warn));
  assert.ok(!card(28).includes(warn));      // band 23..33: clear of the window, as the gate passed it
});

test('card: Ideas are SHADOW, labelled not validated', () => {
  assert.ok(render().includes('Shadow · Not validated - do not trade'));
});

test('card: while Ideas are SHADOW the tier badge is neutral and there is no sizing guidance', () => {
  const html = renderToStaticMarkup(
    <StockCard recommendation={obe} isExpanded={true} onToggleExpand={() => {}} onRemove={() => {}}
      sizing={{ account: 10000, riskPct: 1 }} />,
  );
  assert.ok(/class="[^"]*text-slate-300[^"]*">Buy<\/span>/.test(html), 'neutral Buy badge');
  assert.ok(!/text-emerald-300[^"]*">Buy</.test(html), 'no green Buy next to do not trade');
  assert.ok(!html.includes('Sizing guidance') && !html.includes('shares ≈'));
});
