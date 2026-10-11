"use client";

import React, { useState } from 'react';
import { SwingSignalRecord } from '../types/database';
import { AlertTriangle, ArrowUpRight, ArrowDownRight, ExternalLink, ChevronDown, ChevronUp, ShieldAlert, Tag } from 'lucide-react';

interface SignalRowProps {
  signal: SwingSignalRecord;
  mode: 'BUY' | 'SELL' | 'WARN' | 'OPEN';
  isLiveSleeve?: boolean;
}

export default function SignalRow({ signal, mode, isLiveSleeve = false }: SignalRowProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const sleeveBadgeColors: Record<string, string> = {
    T: 'bg-emerald-950/60 text-emerald-300 border-emerald-800/80',
    E: 'bg-indigo-950/60 text-indigo-300 border-indigo-800/80',
    N: 'bg-amber-950/60 text-amber-300 border-amber-800/80',
    P: 'bg-cyan-950/60 text-cyan-300 border-cyan-800/80',
    I: 'bg-purple-950/60 text-purple-300 border-purple-800/80',
  };

  const sleeveNames: Record<string, string> = {
    T: 'Trend Following',
    E: 'Earnings Reaction',
    N: 'Catalyst Gap',
    P: 'Large-Cap Pullback',
    I: 'Insider Cluster',
  };

  const whyMoved = signal.details?.why_it_moved;
  const isShadow = !isLiveSleeve;

  // SELL Mode Row
  if (mode === 'SELL') {
    const isWin = (signal.net_return_pct ?? 0) >= 0;
    return (
      <div className="bg-slate-900/60 border border-slate-800 hover:border-rose-900/50 rounded-xl p-3 sm:p-4 transition-all">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2.5">
            <span className="text-sm sm:text-base font-black text-white tracking-wide">{signal.ticker}</span>
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${sleeveBadgeColors[signal.sleeve] || 'bg-slate-800 text-slate-300 border-slate-700'}`}>
              Sleeve {signal.sleeve}
            </span>
            <span className="text-[11px] font-semibold text-rose-400 bg-rose-950/40 px-2 py-0.5 rounded border border-rose-800/60">
              SELL at next open
            </span>
          </div>

          <div className="flex items-center gap-4 text-xs font-semibold">
            <div className="text-right">
              <span className="text-[10px] text-slate-400 block">Exit / Fill</span>
              <span className="text-slate-200">${Number(signal.exit_price ?? signal.entry_signal_close).toFixed(2)}</span>
            </div>
            <div className="text-right">
              <span className="text-[10px] text-slate-400 block">Held</span>
              <span className="text-slate-300">{signal.holding_sessions ?? 0} sessions</span>
            </div>
            <div className="text-right">
              <span className="text-[10px] text-slate-400 block">Net Return</span>
              <span className={`font-bold ${isWin ? 'text-emerald-400' : 'text-rose-400'}`}>
                {signal.net_return_pct !== null && signal.net_return_pct !== undefined ? `${signal.net_return_pct >= 0 ? '+' : ''}${Number(signal.net_return_pct).toFixed(2)}%` : '—'}
              </span>
            </div>
          </div>
        </div>

        <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/60 pt-2">
          <span>Reason: <strong className="text-slate-200">{signal.exit_reason || 'Rule exit'}</strong></span>
          {signal.details?.why_exit?.summary && (
            <span className="italic text-slate-400/90 truncate max-w-md">"{signal.details.why_exit.summary}"</span>
          )}
        </div>
      </div>
    );
  }

  // BUY Mode Row
  if (mode === 'BUY') {
    // Strict requirement: Shadow sleeves never show a BUY action
    if (isShadow) {
      return null;
    }

    const refClose = Number(signal.entry_signal_close);
    const stop = Number(signal.stop_loss);
    const target = signal.target ? Number(signal.target) : null;
    const riskPct = refClose > 0 ? ((refClose - stop) / refClose) * 100 : 0;

    return (
      <div className="bg-slate-900/80 border border-slate-800 hover:border-emerald-800/60 rounded-xl p-3 sm:p-4 transition-all shadow-sm">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2.5">
            <span className="text-base font-black text-white tracking-wide">{signal.ticker}</span>
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${sleeveBadgeColors[signal.sleeve] || 'bg-slate-800 text-slate-300 border-slate-700'}`}>
              Sleeve {signal.sleeve} ({sleeveNames[signal.sleeve] || signal.sleeve})
            </span>
            <span className="text-[11px] font-bold text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/80 animate-pulse">
              BUY at next open
            </span>
          </div>

          <div className="flex items-center gap-3 sm:gap-4 text-xs font-semibold">
            <div className="text-right">
              <span className="text-[10px] text-slate-400 block">Ref Close</span>
              <span className="text-slate-100 font-bold">${refClose.toFixed(2)}</span>
            </div>
            <div className="text-right">
              <span className="text-[10px] text-slate-400 block">Stop</span>
              <span className="text-rose-400 font-bold">${stop.toFixed(2)}</span>
            </div>
            <div className="text-right">
              <span className="text-[10px] text-slate-400 block">Target</span>
              <span className="text-emerald-400 font-bold">{target ? `$${target.toFixed(2)}` : 'None (Rule exit)'}</span>
            </div>
            <div className="text-right">
              <span className="text-[10px] text-slate-400 block">Risk/Unit</span>
              <span className="text-amber-400 font-bold">{riskPct.toFixed(1)}%</span>
            </div>
          </div>
        </div>

        {/* Why it moved & citations */}
        {whyMoved && (
          <div className="mt-2.5 bg-slate-950/60 border border-slate-800/80 rounded-lg p-2.5 text-xs">
            <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1">
              <span className="font-semibold text-slate-300">Why it moved ({whyMoved.label})</span>
            </div>
            <p className="text-slate-300 leading-relaxed">{whyMoved.summary}</p>
            {whyMoved.sources && whyMoved.sources.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-2">
                {whyMoved.sources.map((src, i) => (
                  <a
                    key={i}
                    href={src.url || '#'}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300 hover:underline bg-blue-950/40 px-2 py-0.5 rounded border border-blue-900/50"
                  >
                    <span>[{src.index}] {src.source}: {src.title ? src.title.slice(0, 30) + '...' : 'Link'}</span>
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  // WARN Mode Row
  if (mode === 'WARN') {
    return (
      <div className="bg-amber-950/20 border border-amber-800/60 rounded-xl p-3 transition-all flex items-start gap-3">
        <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
        <div className="text-xs">
          <div className="flex items-center gap-2">
            <span className="font-bold text-white">{signal.ticker}</span>
            <span className="text-[10px] font-semibold text-amber-300 bg-amber-950/60 px-1.5 py-0.2 rounded border border-amber-800/80">
              Sleeve {signal.sleeve}
            </span>
          </div>
          <p className="text-amber-200/90 mt-1">
            {signal.details?.warning || signal.earnings_date ? `Earnings report within 5 sessions (${signal.earnings_date})` : 'Catalyst or adverse movement alert'}
          </p>
        </div>
      </div>
    );
  }

  // OPEN Mode Row
  return (
    <div className="bg-slate-900/50 border border-slate-800/90 hover:border-slate-700 rounded-xl p-3 transition-all">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-white">{signal.ticker}</span>
          <span className={`text-[10px] font-bold px-1.5 py-0.2 rounded border ${sleeveBadgeColors[signal.sleeve] || 'bg-slate-800 text-slate-300'}`}>
            Sleeve {signal.sleeve}
          </span>
          {isShadow && (
            <span className="text-[9px] font-bold text-slate-400 bg-slate-800 px-1.5 py-0.2 rounded border border-slate-700">
              SHADOW
            </span>
          )}
        </div>

        <div className="flex items-center gap-4 text-xs font-semibold">
          <div className="text-right">
            <span className="text-[10px] text-slate-400 block">Entry Close</span>
            <span className="text-slate-200">${Number(signal.entry_signal_close).toFixed(2)}</span>
          </div>
          <div className="text-right">
            <span className="text-[10px] text-slate-400 block">Stop</span>
            <span className="text-rose-400">${Number(signal.stop_loss).toFixed(2)}</span>
          </div>
          <div className="text-right">
            <span className="text-[10px] text-slate-400 block">Target</span>
            <span className="text-emerald-400">{signal.target ? `$${Number(signal.target).toFixed(2)}` : 'None'}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
