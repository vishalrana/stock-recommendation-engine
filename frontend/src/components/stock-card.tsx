"use client";

import React from 'react';
import { Recommendation } from '../types/database';
import { Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import { getStrategyExplanation } from '../lib/strategy-explanations';

interface StockCardProps {
  recommendation: Recommendation;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onRemove: (rec: Recommendation) => void;
}

function getDaysActive(dateStr?: string | null): string {
  if (!dateStr) return '1d';
  try {
    const parseDate = (d: string) => new Date(d.includes('T') ? d : `${d}T00:00:00`);
    const start = parseDate(dateStr);
    const now = new Date();
    const diffDays = Math.max(0, Math.floor((now.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)));
    return diffDays === 0 ? 'Today' : `${diffDays}d active`;
  } catch {
    return '1d';
  }
}

export default function StockCard({
  recommendation,
  isExpanded,
  onToggleExpand,
  onRemove,
}: StockCardProps) {
  const ticker = recommendation.ticker?.toUpperCase() || 'UNKNOWN';
  const company = recommendation.company_name || '';
  const price = recommendation.price ? Number(recommendation.price).toFixed(2) : '—';
  
  const t1 = recommendation.target_1 ? Number(recommendation.target_1).toFixed(2) : null;
  const t2 = recommendation.target_2 ? Number(recommendation.target_2).toFixed(2) : null;
  const t3 = recommendation.target_3 ? Number(recommendation.target_3).toFixed(2) : null;
  const stop = recommendation.stop_loss ? Number(recommendation.stop_loss).toFixed(2) : '—';
  const activeDays = getDaysActive(recommendation.entry_date || recommendation.scan_date);

  const strategy = recommendation.strategy_name || recommendation.strategy;
  const whyExplanation = getStrategyExplanation(strategy, recommendation.narrative);

  // Format Targets string: T1 $XX.XX · T2 $XX.XX (and append T3 only if genuinely available)
  const targetParts: string[] = [];
  if (t1) targetParts.push(`T1 $${t1}`);
  if (t2) targetParts.push(`T2 $${t2}`);
  if (t3) targetParts.push(`T3 $${t3}`);
  const targetsDisplay = targetParts.length > 0 ? targetParts.join(' · ') : 'Trailing Stop';

  return (
    <div
      onClick={onToggleExpand}
      className={`group bg-[#121826] hover:bg-[#151c2e] border transition-all duration-200 rounded-2xl p-4 sm:p-5 cursor-pointer select-none relative ${
        isExpanded
          ? 'border-blue-500/60 shadow-lg shadow-blue-500/5 ring-1 ring-blue-500/20'
          : 'border-slate-800/80 hover:border-slate-700/80 shadow-md'
      }`}
    >
      {/* Top row: Ticker & Company on left | Current price on right */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-extrabold text-xl text-white tracking-tight leading-tight">
              {ticker}
            </h3>
          </div>
          {company && (
            <p className="text-xs text-slate-400 font-medium truncate mt-0.5 leading-normal" title={company}>
              {company}
            </p>
          )}
        </div>

        <div className="text-right flex-shrink-0">
          <span className="font-mono text-xl font-bold text-white tracking-tight">
            ${price}
          </span>
        </div>
      </div>

      {/* Main information: Targets, Stop & Active */}
      <div className="mt-3.5 pt-3 border-t border-slate-800/60 flex flex-col gap-1.5 text-xs">
        <div className="flex items-baseline justify-between text-slate-300">
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Targets</span>
          <span className="font-mono font-medium text-emerald-400">
            {targetsDisplay}
          </span>
        </div>

        <div className="flex items-baseline justify-between text-slate-400 text-[11px]">
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Risk & Time</span>
          <span className="font-mono text-slate-300">
            Stop ${stop} <span className="text-slate-600">·</span> <span className="text-slate-400">{activeDays}</span>
          </span>
        </div>
      </div>

      {/* Card footer: Subtle Remove action + Expand indicator */}
      <div className="mt-3.5 pt-2.5 border-t border-slate-800/40 flex items-center justify-between">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove(recommendation);
          }}
          className="inline-flex items-center gap-1.5 py-1 px-2.5 -ml-1 text-xs font-medium text-slate-400 hover:text-rose-400 hover:bg-rose-950/30 rounded-lg transition-colors cursor-pointer"
          title={`Remove ${ticker} recommendation`}
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Remove</span>
        </button>

        <div className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400 group-hover:text-slate-200 transition-colors">
          <span>{isExpanded ? 'Hide Chart' : 'Inspect Idea'}</span>
          {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </div>
      </div>

      {/* Expanded view: Technical Candlestick Chart + Why this idea? */}
      {isExpanded && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="mt-4 pt-4 border-t border-slate-800 space-y-4 animate-in fade-in zoom-in-98 duration-200 cursor-default"
        >
          {/* TradingView Candlestick Chart */}
          <div className="w-full rounded-xl overflow-hidden border border-slate-800 bg-[#0b0f19] shadow-inner">
            <div className="p-2.5 bg-slate-900/90 border-b border-slate-800 flex items-center justify-between text-[11px]">
              <span className="font-bold text-slate-300 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                Daily Candlestick Chart ({ticker})
              </span>
              <span className="text-slate-500 font-mono">NY ET</span>
            </div>
            <div className="w-full h-72 sm:h-96">
              <iframe
                title={`TradingView Chart for ${ticker}`}
                src={`https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(
                  ticker
                )}&interval=D&hidesidetoolbar=1&symboledit=1&saveimage=1&toolbarbg=0b0f19&studies=%5B%5D&theme=dark&style=1&timezone=America%2FNew_York`}
                className="w-full h-full border-0"
                allowFullScreen
              />
            </div>
          </div>

          {/* Why this idea? Section */}
          <div className="p-3.5 bg-slate-900/60 border border-slate-800/80 rounded-xl">
            <h4 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
              Why this idea?
            </h4>
            <p className="text-xs text-slate-200 leading-relaxed font-medium">
              {whyExplanation}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
