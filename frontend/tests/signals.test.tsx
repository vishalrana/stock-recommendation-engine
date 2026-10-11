import test from 'node:test';
import assert from 'node:assert';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SignalsView from '../src/components/signals-view';
import { SwingSignalRecord, SwingSleeveStatus } from '../src/types/database';

test('SignalsView: SELL renders before BUY', () => {
  const mockSleeves: SwingSleeveStatus[] = [
    { sleeve: 'T', name: 'Trend', status: 'LIVE', universe: 'u2' },
  ];

  const mockSignals: SwingSignalRecord[] = [
    {
      id: 1,
      ticker: 'AAPL',
      sleeve: 'T',
      status: 'closed',
      signal_date: '2026-10-08',
      exit_date: '2026-10-09',
      entry_signal_close: 150.0,
      exit_price: 155.0,
      exit_reason: 'target',
      stop_loss: 145.0,
      net_return_pct: 3.33,
      holding_sessions: 5,
    },
    {
      id: 2,
      ticker: 'MSFT',
      sleeve: 'T',
      status: 'open',
      signal_date: '2026-10-09',
      entry_signal_close: 300.0,
      stop_loss: 285.0,
      target: 330.0,
    },
  ];

  const html = renderToStaticMarkup(
    <SignalsView
      signals={mockSignals}
      sleeves={mockSleeves}
      latestDataDate="2026-10-09"
    />
  );

  const sellIdx = html.indexOf('aria-label="Sell Actions"');
  const buyIdx = html.indexOf('aria-label="Buy Actions"');

  assert.ok(sellIdx !== -1, 'SELL section must be rendered');
  assert.ok(buyIdx !== -1, 'BUY section must be rendered');
  assert.ok(sellIdx < buyIdx, 'SELL must render before BUY');
});

test('SignalsView: shadow sleeves never show a BUY action', () => {
  const mockSleeves: SwingSleeveStatus[] = [
    { sleeve: 'E', name: 'Earnings', status: 'SHADOW', universe: 'shadow' },
    { sleeve: 'T', name: 'Trend', status: 'SHADOW', universe: 'shadow' },
  ];

  const mockSignals: SwingSignalRecord[] = [
    {
      id: 10,
      ticker: 'NVDA',
      sleeve: 'E',
      status: 'open',
      signal_date: '2026-10-09',
      entry_signal_close: 120.0,
      stop_loss: 110.0,
    },
  ];

  const html = renderToStaticMarkup(
    <SignalsView
      signals={mockSignals}
      sleeves={mockSleeves}
      latestDataDate="2026-10-09"
    />
  );

  // Extract Buy Actions section
  const buySectionMatch = html.match(/<section[^>]*aria-label="Buy Actions"[^>]*>([\s\S]*?)<\/section>/);
  assert.ok(buySectionMatch, 'Buy actions section exists');
  const buySectionContent = buySectionMatch[1];

  // The shadow setup NVDA must NOT have a BUY action in the Buy Actions section
  assert.ok(!buySectionContent.includes('NVDA'), 'Shadow sleeve candidate NVDA must never appear in live BUY section');
  assert.ok(buySectionContent.includes('No live BUY actions for the next open'), 'Should indicate zero live BUY actions');

  // Verify that it is noted under Shadow Sleeves
  assert.ok(html.includes('Shadow Sleeves'), 'Shadow sleeves drawer must exist');
});
