"use client";

import React from 'react';
import { Recommendation } from '../types/database';
import { Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import { getStrategyExplanation } from '../lib/strategy-explanations';

interface StockCardProps {
  recommendation: Recommendation;
  livePrice?: number | null;
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

function formatEarningsDate(dateStr?: string | null): string {
  if (!dateStr) return '';
  try {
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const year = parseInt(parts[0], 10);
      const month = parseInt(parts[1], 10) - 1;
      const day = parseInt(parts[2], 10);
      const d = new Date(year, month, day);
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }
    const d = new Date(dateStr.includes('T') ? dateStr : `${dateStr}T00:00:00`);
    return isNaN(d.getTime()) ? dateStr : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return dateStr;
  }
}

export default function StockCard({
  recommendation,
  livePrice,
  isExpanded,
  onToggleExpand,
  onRemove,
}: StockCardProps) {
  const ticker = recommendation.ticker?.toUpperCase() || 'UNKNOWN';
  const company = recommendation.company_name || '';

  // Prefer live quote data when available, falling back to stored recommendation.price
  const displayedPriceVal = (livePrice !== undefined && livePrice !== null)
    ? livePrice
    : recommendation.price;
  const price = (displayedPriceVal !== undefined && displayedPriceVal !== null && !isNaN(Number(displayedPriceVal)))
    ? Number(displayedPriceVal).toFixed(2)
    : '—';

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

  // Build validated supporting evidence items (hide any criteria that are unavailable)
  const evidenceItems: { label: string; detail: string }[] = [];

  // 1. Positive earnings surprise
  if (
    recommendation.earnings_surprise_pct !== null &&
    recommendation.earnings_surprise_pct !== undefined &&
    !isNaN(Number(recommendation.earnings_surprise_pct)) &&
    Number(recommendation.earnings_surprise_pct) > 0
  ) {
    evidenceItems.push({
      label: 'Positive earnings',
      detail: `+${Number(recommendation.earnings_surprise_pct).toFixed(1)}% earnings surprise`,
    });
  } else if (
    recommendation.context_earnings !== null &&
    recommendation.context_earnings !== undefined &&
    !isNaN(Number(recommendation.context_earnings)) &&
    Number(recommendation.context_earnings) >= 15
  ) {
    evidenceItems.push({
      label: 'Positive earnings',
      detail: 'Earnings momentum',
    });
  }

  // 2. Positive news sentiment (never fabricate headlines)
  if (
    recommendation.finbert_sentiment !== null &&
    recommendation.finbert_sentiment !== undefined &&
    !isNaN(Number(recommendation.finbert_sentiment)) &&
    Number(recommendation.finbert_sentiment) > 0.20
  ) {
    evidenceItems.push({
      label: 'Positive news',
      detail: 'Positive news sentiment',
    });
  } else if (
    recommendation.context_news !== null &&
    recommendation.context_news !== undefined &&
    !isNaN(Number(recommendation.context_news)) &&
    Number(recommendation.context_news) >= 15
  ) {
    evidenceItems.push({
      label: 'Positive news',
      detail: 'Positive news signal',
    });
  }

  // 3. Next earnings date (hide if null / unknown)
  if (recommendation.next_earnings_date && recommendation.next_earnings_date.trim() !== '') {
    const formattedDate = formatEarningsDate(recommendation.next_earnings_date);
    if (formattedDate) {
      const days = (recommendation.days_to_earnings !== null && recommendation.days_to_earnings !== undefined && Number(recommendation.days_to_earnings) > 0)
        ? ` (${recommendation.days_to_earnings}d)`
        : '';
      evidenceItems.push({
        label: 'Next earnings',
        detail: `${formattedDate}${days}`,
      });
    }
  }

  // 4. Fundamentally strong (strictly require actual D/E < 1.0 and CR > 1.5 with genuine data)
  const de = recommendation.de_ratio;
  const cr = recommendation.current_ratio;
  if (
    de !== null &&
    de !== undefined &&
    cr !== null &&
    cr !== undefined &&
    !isNaN(Number(de)) &&
    !isNaN(Number(cr)) &&
    Number(de) < 1.0 &&
    Number(cr) > 1.5
  ) {
    evidenceItems.push({
      label: 'Fundamentally strong',
      detail: `D/E ${Number(de).toFixed(2)} · Current ratio ${Number(cr).toFixed(2)}`,
    });
  }

  // Win rate and Analytical R:R helpers
  let winRateDisplay = 'Not enough history';
  if (
    recommendation.past_win_rate !== null &&
    recommendation.past_win_rate !== undefined &&
    !isNaN(Number(recommendation.past_win_rate)) &&
    Number(recommendation.past_win_rate) > 0
  ) {
    const rawWr = Number(recommendation.past_win_rate);
    const wrPct = rawWr <= 1.0 ? rawWr * 100 : rawWr;
    winRateDisplay = `${wrPct.toFixed(0)}%`;
  }

  const rrVal = recommendation.weighted_rr_honest ?? recommendation.weighted_rr ?? recommendation.risk_reward;
  const rrDisplay = (rrVal !== null && rrVal !== undefined && !isNaN(Number(rrVal)))
    ? `${Number(rrVal).toFixed(2)}:1 (analytical)`
    : null;

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
            {recommendation.tier_label && (
              <span
                className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                  recommendation.tier_label === 'Strong Buy'
                    ? 'bg-purple-950/60 text-purple-300 border-purple-800/80'
                    : 'bg-emerald-950/60 text-emerald-300 border-emerald-800/80'
                }`}
              >
                {recommendation.tier_label}
              </span>
            )}
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

      {/* Supporting Evidence (Rendered only when validated evidence criteria are met) */}
      {evidenceItems.length > 0 && (
        <div className="mt-3 pt-2.5 border-t border-slate-800/60">
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1.5">
            Supporting Evidence
          </span>
          <div className="space-y-1">
            {evidenceItems.map((item, idx) => (
              <div key={idx} className="flex items-center justify-between text-[11px]">
                <span className="flex items-center gap-1.5 text-slate-300 font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0" />
                  {item.label}
                </span>
                <span className="font-mono text-slate-400 text-[10px]">
                  {item.detail}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

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

      {/* Expanded view: Technical Candlestick Chart + Strategy Setup + Analytical R:R / Win Rate */}
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

          {/* Strategy Setup and Analytical Context */}
          <div className="p-3.5 bg-slate-900/60 border border-slate-800/80 rounded-xl space-y-2.5">
            <div>
              <h4 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">
                Strategy Setup
              </h4>
              <p className="text-xs text-slate-200 leading-relaxed font-medium">
                {whyExplanation}
              </p>
            </div>

            {/* Analytical Metrics Block */}
            <div className="pt-2 border-t border-slate-800/60 grid grid-cols-2 gap-2 text-[11px]">
              <div>
                <span className="text-slate-500 block text-[10px] uppercase font-bold tracking-wider">
                  Historical Win Rate
                </span>
                <span className="text-slate-300 font-mono font-medium">
                  {winRateDisplay}
                </span>
              </div>
              {rrDisplay && (
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase font-bold tracking-wider">
                    Analytical R:R
                  </span>
                  <span className="text-slate-300 font-mono font-medium">
                    {rrDisplay}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
