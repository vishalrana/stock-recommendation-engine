"use client";

import React, { useState } from 'react';
import { SleeveHoldoutStats, SwingSignalRecord, SwingSleeveStatus } from '../types/database';
import SignalRow from './signal-row';
import TrackRecord from './track-record';
import { ChevronDown, ChevronUp, Info, Layers } from 'lucide-react';

interface SignalsViewProps {
  signals: SwingSignalRecord[];
  sleeves: SwingSleeveStatus[];
  latestDataDate?: string;
}

const SLEEVES: Array<{ id: SwingSignalRecord['sleeve']; name: string }> = [
  { id: 'T', name: 'Trend' },
  { id: 'E', name: 'Earnings reaction' },
  { id: 'N', name: 'Catalyst gap' },
  { id: 'P', name: 'Large-cap pullback' },
  { id: 'I', name: 'Insider buying' },
];

const isLiveMode = (m?: string | null) => m === 'LIVE' || m === 'LIVE-SP500';

function fmtPct(v?: number | null, digits = 2): string {
  return v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(digits)}%`;
}

function HoldoutLine({ label, stats }: { label: string; stats?: SleeveHoldoutStats | null }) {
  if (!stats || stats.n_signals === null || stats.n_signals === undefined) {
    return <div className="text-[10px] text-slate-500">{label}: no result</div>;
  }
  const ci = stats.ci95_beta_adj;
  const pct = stats.placebo_percentile;
  return (
    <div className="text-[10px] text-slate-400 leading-snug">
      {label}: {stats.n_signals} signals · {fmtPct(stats.mean_beta_adj_pct)} after beta
      {ci && ci[0] !== null && ci[1] !== null ? ` (CI ${fmtPct(ci[0])} to ${fmtPct(ci[1])})` : ''}
      {pct !== null && pct !== undefined && Number.isFinite(Number(pct)) ? ` · placebo pct ${Number(pct).toFixed(0)}` : ''}
    </div>
  );
}

export default function SignalsView({ signals, sleeves, latestDataDate }: SignalsViewProps) {
  const [shadowExpanded, setShadowExpanded] = useState(false);
  const bySleeve = new Map(sleeves.map((s) => [s.sleeve, s]));
  const tested = sleeves.some((s) => Boolean(s.research_run_id));

  // A signal is live only if it was issued while its sleeve was LIVE (the mode stored with it).
  const live = signals.filter((s) => isLiveMode(s.mode ?? s.details?.sleeve_status));
  const shadow = signals.filter((s) => !isLiveMode(s.mode ?? s.details?.sleeve_status));

  const sells = live.filter((s) => (s.status === 'closed' && s.exit_date === latestDataDate) ||
                                   (s.status === 'open' && Boolean(s.details?.pending_exit)));
  const buys = live.filter((s) => s.status === 'open' && s.signal_date === latestDataDate);
  const openLive = live.filter((s) => s.status === 'open' && s.signal_date !== latestDataDate && !s.details?.pending_exit);
  const warns = live.filter((s) => s.status === 'open' && (s.earnings_unknown || Boolean(s.details?.warning)));
  const shadowOpen = shadow.filter((s) => s.status === 'open');

  return (
    <div className="space-y-6">
      {/* Status strip */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <Layers className="w-5 h-5 text-blue-400" />
          <div>
            <h2 className="text-sm sm:text-base font-bold text-white">Swing signals</h2>
            <span className="text-[11px] text-slate-400">
              {latestDataDate ? <>Latest scan: <strong className="text-slate-200">{latestDataDate}</strong></> : 'No scan has run yet'}
              {' · '}Runs after the US close
            </span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2 mt-3 pt-3 border-t border-slate-800/80">
          {SLEEVES.map((sl) => {
            const row = bySleeve.get(sl.id);
            const status = row?.status || 'SHADOW';
            const live = isLiveMode(status);
            const hasResult = Boolean(row?.research_run_id);
            return (
              <div
                key={sl.id}
                className={`p-2.5 rounded-xl border ${live ? 'bg-emerald-950/40 border-emerald-800/80' : 'bg-slate-950/60 border-slate-800/80'}`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[11px] font-bold text-slate-200">{sl.id} · {sl.name}</span>
                  <span className={`text-[9px] font-extrabold uppercase px-1.5 rounded ${live ? 'bg-emerald-900/60 text-emerald-200' : 'bg-slate-800 text-slate-400'}`}>
                    {status}
                  </span>
                </div>
                {hasResult ? (
                  <div className="mt-1 space-y-0.5">
                    <HoldoutLine label="S&P 500 (point in time)" stats={row?.details?.u2} />
                    <HoldoutLine label="Broad universe" stats={row?.details?.u1} />
                    <div className="text-[9px] text-slate-500">Holdout 2008-01 to 2022-09 · run {row?.research_run_id}</div>
                  </div>
                ) : (
                  <div className="mt-1 text-[10px] text-slate-500">Not yet tested</div>
                )}
              </div>
            );
          })}
        </div>
        {tested && sleeves.some((s) => isLiveMode(s.status)) && (
          <p className="mt-2 text-[10px] text-slate-500">
            Backtests and short live records do not prove future profit.
          </p>
        )}
      </div>

      {signals.length === 0 ? (
        <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-6 text-center text-xs text-slate-400">
          No signals yet - first scan runs after the next US close.
        </div>
      ) : (
        <>
          {sells.length > 0 && (
            <section aria-label="Sell Actions" className="space-y-2">
              <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-rose-500" /> Sell at the next open ({sells.length})
              </h3>
              {sells.map((sig) => <SignalRow key={`sell-${sig.id}`} signal={sig} mode="SELL" isLiveSleeve />)}
            </section>
          )}

          <section aria-label="Buy Actions" className="space-y-2">
            <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" /> Buy at the next open ({buys.length})
            </h3>
            {buys.length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4 text-center text-xs text-slate-400">
                {sleeves.some((s) => isLiveMode(s.status))
                  ? 'No buy signals from live sleeves tonight.'
                  : 'No sleeve is live, so there are no buy signals.'}
              </div>
            ) : (
              buys.map((sig) => <SignalRow key={`buy-${sig.id}`} signal={sig} mode="BUY" isLiveSleeve />)
            )}
          </section>

          {warns.length > 0 && (
            <section aria-label="Warning Alerts" className="space-y-2">
              <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-500" /> Warnings ({warns.length})
              </h3>
              {warns.map((sig) => <SignalRow key={`warn-${sig.id}`} signal={sig} mode="WARN" isLiveSleeve />)}
            </section>
          )}

          {openLive.length > 0 && (
            <section aria-label="Open Signals" className="space-y-2">
              <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-blue-500" /> Open live signals ({openLive.length})
              </h3>
              {openLive.map((sig) => <SignalRow key={`open-${sig.id}`} signal={sig} mode="OPEN" isLiveSleeve />)}
            </section>
          )}

          <section aria-label="Track Record">
            <TrackRecord closedSignals={live.filter((s) => s.status === 'closed')} />
          </section>
        </>
      )}

      {/* Shadow sleeves: computed and tracked, never a buy action */}
      <section aria-label="Shadow Sleeves" className="space-y-2">
        <button
          type="button"
          onClick={() => setShadowExpanded(!shadowExpanded)}
          aria-expanded={shadowExpanded}
          className="w-full flex items-center justify-between p-3.5 bg-slate-950/80 border border-slate-800/90 hover:border-slate-700 rounded-xl transition-all cursor-pointer text-xs"
        >
          <span className="flex items-center gap-2 font-bold text-slate-200 text-left">
            <Info className="w-4 h-4 text-amber-400 shrink-0" />
            Shadow signals ({shadowOpen.length} open) — <span className="text-amber-400">Not validated - do not trade</span>
          </span>
          {shadowExpanded ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
        </button>
        {shadowExpanded && (
          <div className="p-3 bg-slate-950/40 border border-slate-800/80 rounded-xl space-y-2">
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Shadow sleeves are computed and tracked so their forward record can be checked. They are not validated and are not trade recommendations.
            </p>
            {shadowOpen.length === 0 ? (
              <div className="text-center text-xs text-slate-500 py-3">No shadow signals open.</div>
            ) : (
              shadowOpen.map((sig) => <SignalRow key={`shadow-${sig.id}`} signal={sig} mode="OPEN" isLiveSleeve={false} />)
            )}
          </div>
        )}
      </section>
    </div>
  );
}
