import test from 'node:test';
import assert from 'node:assert';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SignalsView from '../src/components/signals-view';
import TrackRecord from '../src/components/track-record';
import ClosedIdeasView from '../src/components/closed-ideas-view';
import ScanHistoryView from '../src/components/scan-history-view';
import { SwingSignalRecord, SwingSleeveStatus } from '../src/types/database';

const section = (html: string, label: string) =>
  html.match(new RegExp(`<section[^>]*aria-label="${label}"[^>]*>([\\s\\S]*?)</section>`))?.[1];

test('SignalsView: SELL renders before BUY for a live sleeve', () => {
  const sleeves: SwingSleeveStatus[] = [{ sleeve: 'T', name: 'Trend', status: 'LIVE', universe: 'u1', research_run_id: '1' }];
  const signals: SwingSignalRecord[] = [
    { id: 1, ticker: 'AAPL', sleeve: 'T', status: 'closed', mode: 'LIVE', signal_date: '2026-10-08', exit_date: '2026-10-09',
      entry_signal_close: 150, exit_price: 155, exit_reason: 'target', stop_loss: 145, net_return_pct: 3.33, holding_sessions: 5 },
    { id: 2, ticker: 'MSFT', sleeve: 'T', status: 'open', mode: 'LIVE', signal_date: '2026-10-09',
      entry_signal_close: 300, stop_loss: 285, target: 330 },
  ];
  const html = renderToStaticMarkup(<SignalsView signals={signals} sleeves={sleeves} latestDataDate="2026-10-09" />);
  const sellIdx = html.indexOf('aria-label="Sell Actions"');
  const buyIdx = html.indexOf('aria-label="Buy Actions"');
  assert.ok(sellIdx !== -1 && buyIdx !== -1 && sellIdx < buyIdx, 'SELL must render before BUY');
  assert.ok(section(html, 'Buy Actions')?.includes('MSFT'));
});

test('SignalsView: shadow signals never show a BUY action', () => {
  const sleeves: SwingSleeveStatus[] = [
    { sleeve: 'E', name: 'Earnings', status: 'SHADOW', universe: 'shadow' },
    { sleeve: 'T', name: 'Trend', status: 'SHADOW', universe: 'shadow' },
  ];
  const signals: SwingSignalRecord[] = [
    { id: 10, ticker: 'NVDA', sleeve: 'E', status: 'open', mode: 'SHADOW', signal_date: '2026-10-09', entry_signal_close: 120, stop_loss: 110 },
  ];
  const html = renderToStaticMarkup(<SignalsView signals={signals} sleeves={sleeves} latestDataDate="2026-10-09" />);
  const buy = section(html, 'Buy Actions') || '';
  assert.ok(!buy.includes('NVDA'), 'shadow NVDA must not appear among BUY actions');
  assert.ok(!html.includes('BUY at next open'), 'no BUY action label at all');
  assert.ok(html.includes('Not validated - do not trade'));
  assert.ok(!html.includes('aria-expanded="true"'), 'shadow drawer starts collapsed');
});

test('SignalsView: untested sleeves say so; nothing claims validation', () => {
  const html = renderToStaticMarkup(<SignalsView signals={[]} sleeves={[]} />);
  assert.ok(html.includes('No signals yet - first scan runs after the next US close'));
  assert.strictEqual((html.match(/Not yet tested/g) || []).length, 5, 'all five sleeves are marked not yet tested');
  for (const banned of ['Validated', 'verified', 'failed pre-registered', 'Breadth', 'SPY &gt; MA200', 'NaN', 'undefined']) {
    assert.ok(!html.includes(banned), `empty Signals tab must not show "${banned}"`);
  }
});

test('SignalsView: real holdout numbers are shown when a research run exists', () => {
  const sleeves: SwingSleeveStatus[] = [{
    sleeve: 'P', name: 'Pullback', status: 'SHADOW', universe: 'shadow', research_run_id: '123',
    details: { u2: { n_signals: 1500, mean_beta_adj_pct: -0.12, ci95_beta_adj: [-0.4, 0.15], placebo_percentile: 40 },
               u1: { n_signals: 2100, mean_beta_adj_pct: -0.05, ci95_beta_adj: [-0.3, 0.2], placebo_percentile: 55 } },
  }];
  const html = renderToStaticMarkup(<SignalsView signals={[]} sleeves={sleeves} />);
  assert.ok(html.includes('1500 signals') && html.includes('-0.12%') && html.includes('run 123'));
  assert.ok(!html.includes('NaN'));
});

test('Empty database: every tab renders without NaN or blank cards', () => {
  const pages = [
    renderToStaticMarkup(<SignalsView signals={[]} sleeves={[]} />),
    renderToStaticMarkup(<TrackRecord closedSignals={[]} />),
    renderToStaticMarkup(<ClosedIdeasView closedIdeas={[]} />),
    renderToStaticMarkup(<ScanHistoryView scanHistory={[]} />),
  ];
  for (const html of pages) {
    assert.ok(html.length > 50, 'a tab rendered nothing');
    assert.ok(!html.includes('NaN') && !html.includes('undefined') && !html.includes('Infinity'));
  }
  assert.ok(pages[1].includes('No closed live signals yet.'));
});
