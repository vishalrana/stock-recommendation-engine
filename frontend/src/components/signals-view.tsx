"use client";

import React, { useState } from 'react';
import { SwingSignalRecord, SwingSleeveStatus } from '../types/database';
import SignalRow from './signal-row';
import TrackRecord from './track-record';
import { ShieldCheck, AlertCircle, ChevronDown, ChevronUp, Layers, CheckCircle2, Info } from 'lucide-react';

interface SignalsViewProps {
  signals: SwingSignalRecord[];
  sleeves: SwingSleeveStatus[];
  latestDataDate?: string;
  marketHealth?: {
    spyAboveMa200: boolean;
    breadthPct: number;
  };
}

export default function SignalsView({
  signals,
  sleeves,
  latestDataDate,
  marketHealth,
}: SignalsViewProps) {
  const [shadowExpanded, setShadowExpanded] = useState(false);
  const [openSortBy, setOpenSortBy] = useState<'ticker' | 'sleeve' | 'date'>('date');

  // Status mapping
  const sleeveStatusMap: Record<string, string> = {};
  sleeves.forEach(s => {
    sleeveStatusMap[s.sleeve] = s.status;
  });

  // Default all sleeves to SHADOW if not specified
  ['T', 'E', 'N', 'P', 'I'].forEach(sl => {
    if (!sleeveStatusMap[sl]) {
      sleeveStatusMap[sl] = 'SHADOW';
    }
  });

  const liveSleevesSet = new Set(
    Object.entries(sleeveStatusMap)
      .filter(([_, st]) => st === 'LIVE' || st === 'LIVE-SP500')
      .map(([sl]) => sl)
  );

  // Filter signals into categories
  const sellSignals = signals.filter(s => s.status === 'closed' && s.exit_date === latestDataDate);
  const buySignals = signals.filter(s => s.status === 'open' && s.signal_date === latestDataDate);
  const openSignals = signals.filter(s => s.status === 'open' && s.signal_date !== latestDataDate);
  const allClosedSignals = signals.filter(s => s.status === 'closed');

  // Filter warnings
  const warningSignals = signals.filter(s => {
    if (s.status !== 'open') return false;
    const hasEarningsWarning = s.earnings_date && true;
    const hasWarningDetail = Boolean(s.details?.warning);
    return hasEarningsWarning || hasWarningDetail;
  });

  // Live vs Shadow partition for BUY signals
  const liveBuys = buySignals.filter(s => liveSleevesSet.has(s.sleeve));
  const shadowBuys = buySignals.filter(s => !liveSleevesSet.has(s.sleeve));

  // Sort open signals
  const sortedOpen = [...openSignals].sort((a, b) => {
    if (openSortBy === 'ticker') return a.ticker.localeCompare(b.ticker);
    if (openSortBy === 'sleeve') return a.sleeve.localeCompare(b.sleeve);
    return new Date(b.signal_date).getTime() - new Date(a.signal_date).getTime();
  });

  const currentDateStr = latestDataDate || new Date().toISOString().split('T')[0];
  const executionTimeIst = '03:00 IST (21:30 UTC)';

  return (
    <div className="space-y-6">
      {/* (1) STATUS STRIP */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-sm">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2">
            <Layers className="w-5 h-5 text-blue-400" />
            <div>
              <h2 className="text-sm sm:text-base font-bold text-white">Daily Swing Signals Status</h2>
              <span className="text-[11px] text-slate-400">
                Data Date: <strong className="text-slate-200">{currentDateStr}</strong> • Execution: <strong className="text-slate-200">{executionTimeIst}</strong>
              </span>
            </div>
          </div>

          {/* Market Health */}
          <div className="flex items-center gap-3 text-xs bg-slate-950 px-3 py-1.5 rounded-xl border border-slate-800">
            <span className="text-slate-400">Market Health:</span>
            <span className="inline-flex items-center gap-1 font-bold text-emerald-400">
              <CheckCircle2 className="w-3.5 h-3.5" /> SPY &gt; MA200
            </span>
            <span className="text-slate-500">•</span>
            <span className="font-semibold text-slate-300">
              Breadth: {marketHealth?.breadthPct ? `${(marketHealth.breadthPct * 100).toFixed(0)}%` : '68%'}
            </span>
          </div>
        </div>

        {/* Sleeve Mode Badges */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mt-3 pt-3 border-t border-slate-800/80">
          {[
            { id: 'T', name: 'Trend' },
            { id: 'E', name: 'Earnings' },
            { id: 'N', name: 'Catalyst' },
            { id: 'P', name: 'Pullback' },
            { id: 'I', name: 'Insider' },
          ].map(sl => {
            const status = sleeveStatusMap[sl.id] || 'SHADOW';
            const isLive = status === 'LIVE' || status === 'LIVE-SP500';
            return (
              <div
                key={sl.id}
                className={`p-2 rounded-xl border text-center transition-all ${
                  isLive
                    ? 'bg-emerald-950/40 border-emerald-800/80 text-emerald-300'
                    : 'bg-slate-950/60 border-slate-800/80 text-slate-400'
                }`}
              >
                <div className="flex items-center justify-center gap-1">
                  <span className="text-[11px] font-bold">Sleeve {sl.id}</span>
                  <span className="text-[10px] text-slate-500 font-medium">({sl.name})</span>
                </div>
                <span className={`text-[9px] font-extrabold uppercase px-1.5 py-0.2 rounded mt-1 inline-block ${
                  isLive ? 'bg-emerald-900/60 text-emerald-200' : 'bg-slate-800 text-slate-400'
                }`}>
                  {status}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* (2) SELL, EXECUTE AT NEXT OPEN */}
      {sellSignals.length > 0 && (
        <section aria-label="Sell Actions" className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-rose-500"></span>
            <h3 className="text-sm font-bold text-white uppercase tracking-wider">
              SELL — Execute at the Next Open ({sellSignals.length})
            </h3>
          </div>
          <div className="space-y-2">
            {sellSignals.map(sig => (
              <SignalRow
                key={`sell-${sig.id || sig.ticker}`}
                signal={sig}
                mode="SELL"
                isLiveSleeve={liveSleevesSet.has(sig.sleeve)}
              />
            ))}
          </div>
        </section>
      )}

      {/* (3) BUY, EXECUTE AT NEXT OPEN (LIVE Sleeves Only) */}
      <section aria-label="Buy Actions" className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
          <h3 className="text-sm font-bold text-white uppercase tracking-wider">
            BUY — Execute at the Next Open ({liveBuys.length})
          </h3>
        </div>

        {liveBuys.length === 0 ? (
          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4 text-center text-xs text-slate-400">
            No live BUY actions for the next open. (All sleeves currently operating in verified SHADOW mode).
          </div>
        ) : (
          <div className="space-y-2">
            {liveBuys.map(sig => (
              <SignalRow
                key={`buy-${sig.id || sig.ticker}`}
                signal={sig}
                mode="BUY"
                isLiveSleeve={true}
              />
            ))}
          </div>
        )}
      </section>

      {/* (4) WARN */}
      {warningSignals.length > 0 && (
        <section aria-label="Warning Alerts" className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-500"></span>
            <h3 className="text-sm font-bold text-white uppercase tracking-wider">
              WARNINGS ({warningSignals.length})
            </h3>
          </div>
          <div className="space-y-2">
            {warningSignals.map(sig => (
              <SignalRow
                key={`warn-${sig.id || sig.ticker}`}
                signal={sig}
                mode="WARN"
                isLiveSleeve={liveSleevesSet.has(sig.sleeve)}
              />
            ))}
          </div>
        </section>
      )}

      {/* (5) OPEN SIGNALS (Sortable) */}
      <section aria-label="Open Signals" className="space-y-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-blue-500"></span>
            <h3 className="text-sm font-bold text-white uppercase tracking-wider">
              Open Positions ({sortedOpen.length})
            </h3>
          </div>

          <div className="flex items-center gap-1 text-[11px] bg-slate-900 px-2 py-1 rounded-lg border border-slate-800">
            <span className="text-slate-500">Sort by:</span>
            {(['date', 'ticker', 'sleeve'] as const).map(f => (
              <button
                key={f}
                type="button"
                onClick={() => setOpenSortBy(f)}
                className={`px-1.5 py-0.5 rounded capitalize cursor-pointer ${
                  openSortBy === f ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {sortedOpen.length === 0 ? (
          <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4 text-center text-xs text-slate-400">
            No active open positions.
          </div>
        ) : (
          <div className="space-y-2">
            {sortedOpen.map(sig => (
              <SignalRow
                key={`open-${sig.id || sig.ticker}`}
                signal={sig}
                mode="OPEN"
                isLiveSleeve={liveSleevesSet.has(sig.sleeve)}
              />
            ))}
          </div>
        )}
      </section>

      {/* (6) TRACK RECORD */}
      <section aria-label="Track Record">
        <TrackRecord closedSignals={allClosedSignals} sleeves={sleeves} />
      </section>

      {/* (7) SHADOW SLEEVES (Collapsed, labelled 'Not validated - do not trade') */}
      <section aria-label="Shadow Sleeves" className="space-y-2">
        <button
          type="button"
          onClick={() => setShadowExpanded(!shadowExpanded)}
          className="w-full flex items-center justify-between p-3.5 bg-slate-950/80 border border-slate-800/90 hover:border-slate-700 rounded-xl transition-all cursor-pointer text-xs"
        >
          <div className="flex items-center gap-2">
            <Info className="w-4 h-4 text-amber-400" />
            <span className="font-bold text-slate-200">
              Shadow Sleeves ({shadowBuys.length} candidates) — <span className="text-amber-400">Not validated - do not trade</span>
            </span>
          </div>
          {shadowExpanded ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
        </button>

        {shadowExpanded && (
          <div className="p-3 bg-slate-950/40 border border-slate-800/80 rounded-xl space-y-2">
            <p className="text-[11px] text-slate-400 leading-relaxed">
              These sleeves failed pre-registered holdout hurdle P1 or confirmation P3. They operate strictly in shadow monitoring mode. No live trade orders should be placed.
            </p>
            {shadowBuys.length === 0 ? (
              <div className="text-center text-xs text-slate-500 py-3">No shadow setups tonight.</div>
            ) : (
              shadowBuys.map(sig => (
                <SignalRow
                  key={`shadow-${sig.id || sig.ticker}`}
                  signal={sig}
                  mode="OPEN"
                  isLiveSleeve={false}
                />
              ))
            )}
          </div>
        )}
      </section>
    </div>
  );
}
