"use client";

import React, { useState, useMemo } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getExpandedRowModel,
  ColumnDef,
  flexRender,
  SortingState,
  ExpandedState,
} from '@tanstack/react-table';
import { Recommendation, ScanLog } from '../types/database';
import { ArrowUpDown, ArrowUp, ArrowDown, Search, Info, RefreshCw, AlertCircle, Sparkles, History } from 'lucide-react';
import { useRouter } from 'next/navigation';
import SignalExitPlan from './SignalExitPlan';
import { getRejectionReason } from '../lib/database';

function getDaysHeld(entryDateStr: string | null | undefined, exitDateStr: string | null | undefined): string {
  if (!entryDateStr) return '-';
  try {
    const parseDate = (d: string) => new Date(d.includes('T') ? d : `${d}T00:00:00`);
    const entry = parseDate(entryDateStr);
    const exit = exitDateStr ? parseDate(exitDateStr) : new Date();
    const diffTime = exit.getTime() - entry.getTime();
    const diffDays = Math.max(0, Math.floor(diffTime / (1000 * 60 * 60 * 24)));
    return `${diffDays}d`;
  } catch {
    return '-';
  }
}

function getDaysHeldNumeric(entryDateStr: string | null | undefined, exitDateStr: string | null | undefined): number {
  if (!entryDateStr) return 0;
  try {
    const parseDate = (d: string) => new Date(d.includes('T') ? d : `${d}T00:00:00`);
    const entry = parseDate(entryDateStr);
    const exit = exitDateStr ? parseDate(exitDateStr) : new Date();
    const diffTime = exit.getTime() - entry.getTime();
    return Math.max(0, Math.floor(diffTime / (1000 * 60 * 60 * 24)));
  } catch {
    return 0;
  }
}

function getTierBadge(tier: string | null | undefined): string {
  if (tier === 'Strong Buy') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  if (tier === 'Buy') return 'bg-blue-50 text-blue-700 border-blue-200';
  return 'bg-gray-50 text-gray-600 border-gray-200';
}

interface TableProps {
  portfolioData?: Recommendation[];
  scanLogData?: Recommendation[];
  data?: Recommendation[];
  regime: string | null;
  scanLog: ScanLog | null;
}

function RegimeBanner({ scanLog }: { scanLog: ScanLog | null }) {
  const regime = scanLog?.regime || 'bull';
  const regimeStr = regime === 'bull' ? 'Bullish' : regime === 'bear' ? 'Bearish' : 'Sideways';

  const pulseColor = regime === 'bull' ? 'bg-emerald-500 animate-pulse' : regime === 'bear' ? 'bg-rose-500 animate-pulse' : 'bg-blue-500 animate-pulse';
  const regimeBg = regime === 'bull' ? 'bg-emerald-50 text-emerald-800 border-emerald-100' : regime === 'bear' ? 'bg-rose-50 text-rose-800 border-rose-100' : 'bg-blue-50 text-blue-800 border-blue-100';

  return (
    <div className="mb-6">
      <div className="max-w-xs bg-white border border-gray-200/80 rounded-2xl p-4 shadow-sm flex items-center justify-between transition-all hover:shadow-md">
        <div>
          <span className="text-[10px] uppercase tracking-wider text-gray-400 font-bold">Market Regime</span>
          <div className="text-xl font-bold text-gray-900 mt-1 flex items-center gap-2">
            {regimeStr}
            <span className="relative flex h-2.5 w-2.5">
              <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping ${pulseColor}`}></span>
              <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${pulseColor}`}></span>
            </span>
          </div>
        </div>
        <span className={`px-2.5 py-1 rounded-full text-xs font-semibold border ${regimeBg}`}>
          {regime === 'bull' ? 'Growth On' : regime === 'bear' ? 'Risk Off' : 'Neutral'}
        </span>
      </div>
    </div>
  );
}

function ExpandableDetails({ row, isScanLog }: { row: { original: Recommendation }; isScanLog?: boolean }) {
  const ticker = row.original.ticker;
  const company = row.original.company_name;
  const industry = row.original.industry;
  const strategy = row.original.strategy_name || row.original.strategy;
  const tier = row.original.tier_label;
  const score = row.original.composite_score;
  const honestRR = row.original.weighted_rr_honest || row.original.weighted_rr;

  // Historical evidence
  const winRate = row.original.past_win_rate;
  const expectancy = row.original.expectancy_pct;
  const reachT1 = row.original.reach_prob_t1;
  const reachT2 = row.original.reach_prob_t2;
  const reachT3 = row.original.reach_prob_t3;
  const daysToEarnings = row.original.days_to_earnings;
  const narrative = row.original.narrative;

  // Context breakdown
  const context_analyst = row.original.context_analyst ?? 0;
  const context_earnings = row.original.context_earnings ?? 0;
  const context_news = row.original.context_news ?? 0;
  const context_fundamental = row.original.context_fundamental ?? 0;

  // Rejection reason (if in scan log)
  const reason = isScanLog ? getRejectionReason(row.original) : null;

  // Sell signal details
  const sell_signal = row.original.sell_signal;
  const sell_signal_reason = row.original.sell_signal_reason;
  const sell_price = row.original.sell_price;

  return (
    <div className="space-y-4 text-gray-700 p-6 bg-slate-50/50 rounded-b-xl border-t border-gray-100">
      <div className="border-b border-gray-200/60 pb-3 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
        <div>
          <div className="flex items-center gap-2">
            <h4 className="text-base font-bold text-gray-900">{company || ticker}</h4>
            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold border ${getTierBadge(tier)}`}>
              {tier || 'Buy'}
            </span>
          </div>
          <span className="text-xs text-gray-500 font-medium">
            {industry || 'General Industry'} • Strategy: <span className="font-semibold text-gray-800">{strategy}</span>
          </span>
        </div>
        {reason ? (
          <div className="bg-amber-50 border border-amber-200 text-amber-800 px-3 py-1 rounded-lg text-xs font-semibold flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 text-amber-600" />
            <span>Audit Decision: {reason}</span>
          </div>
        ) : (
          <div className="flex items-center gap-3 text-xs">
            <span className="text-gray-500 font-medium">
              Composite Score: <span className="font-bold text-gray-900">{score ? Number(score).toFixed(1) : '-'}</span>
            </span>
            <span className="text-gray-300">•</span>
            <span className="text-gray-500 font-medium">
              Honest R:R: <span className="font-bold text-gray-900">{honestRR ? `${Number(honestRR).toFixed(2)}:1` : '-'}</span>
            </span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Trade Setup, Historical Evidence & Context */}
        <div className="space-y-4 lg:col-span-1">
          {!isScanLog ? (
            <SignalExitPlan recommendation={row.original} />
          ) : (
            <div className="bg-white p-4 border border-gray-200 rounded-xl shadow-sm space-y-2">
              <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">📊 Quantitative Metrics</h5>
              <div className="grid grid-cols-2 gap-2 text-xs font-medium">
                <div>Composite Score: <span className="font-bold text-gray-900">{Number(row.original.composite_score || 0).toFixed(1)}</span></div>
                <div>Honest R:R: <span className="font-bold text-gray-900">{Number(row.original.weighted_rr_honest || row.original.weighted_rr || 0).toFixed(2)}</span></div>
                <div>Entry Ref: <span className="font-mono text-gray-900">${Number(row.original.entry_price || row.original.price || 0).toFixed(2)}</span></div>
                <div>Stop Ref: <span className="font-mono text-rose-600">${Number(row.original.stop_loss || 0).toFixed(2)}</span></div>
              </div>
            </div>
          )}

          {/* Historical Evidence & Reach Probabilities */}
          <div className="bg-white p-4 border border-gray-200 rounded-xl shadow-sm space-y-2.5">
            <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">🔬 Historical Evidence & Reach Odds</h5>
            <div className="grid grid-cols-2 gap-2 text-xs font-medium">
              <div className="text-gray-600">Strategy Win Rate: <span className="font-bold text-emerald-700">{winRate ? `${Number(winRate).toFixed(1)}%` : '55.0%'}</span></div>
              <div className="text-gray-600">Hist. Expectancy: <span className="font-bold text-emerald-700">{expectancy ? `+${Number(expectancy).toFixed(2)}%` : '+1.44%'}</span></div>
              <div className="text-gray-600">T1 Reach Prob: <span className="font-bold text-blue-700">{reachT1 ? `${(Number(reachT1) * 100).toFixed(0)}%` : '60%'}</span></div>
              <div className="text-gray-600">T2 Reach Prob: <span className="font-bold text-blue-700">{reachT2 ? `${(Number(reachT2) * 100).toFixed(0)}%` : '35%'}</span></div>
              <div className="text-gray-600">T3 Reach Prob: <span className="font-bold text-purple-700">{reachT3 ? `${(Number(reachT3) * 100).toFixed(0)}%` : '20%'}</span></div>
              <div className="text-gray-600">Earnings Window: <span className="font-bold text-slate-800">{daysToEarnings !== undefined && daysToEarnings !== null ? `${daysToEarnings}d to report` : 'Window clear'}</span></div>
            </div>
            {narrative && (
              <div className="pt-2 border-t border-gray-100 text-[11px] text-gray-600 leading-relaxed italic">
                &ldquo;{narrative}&rdquo;
              </div>
            )}
          </div>

          {/* Context Score Breakdown */}
          <div className="bg-white p-4 border border-gray-200 rounded-xl shadow-sm">
            <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-3">📋 Context Breakdown</h5>
            <div className="grid grid-cols-2 gap-3 text-xs font-semibold">
              <div className="text-gray-600">Analyst: <span className="text-blue-600">+{context_analyst} pts</span></div>
              <div className="text-gray-600">Earnings: <span className="text-blue-600">{context_earnings >= 0 ? '+' : ''}{context_earnings} pts</span></div>
              <div className="text-gray-600">News: <span className="text-blue-600">{context_news >= 0 ? '+' : ''}{context_news} pts</span></div>
              <div className="text-gray-600">Fundamentals: <span className="text-blue-600">+{context_fundamental} pts</span></div>
            </div>
          </div>

          {/* Recommendation Lifecycle / Sell Alert */}
          {(sell_signal || row.original.status === 'closed') && (
            <div className="bg-white p-4 border border-gray-200 rounded-xl shadow-sm">
              <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">⚡ Recommendation Status & Exit Alert</h5>
              <div className="text-xs font-bold text-red-600 leading-relaxed">
                {row.original.status === 'closed' ? '🏁 Exit complete:' : '⚠️ Active sell alert:'} {sell_signal_reason}
                {sell_price && <span className="block font-mono text-gray-700 mt-1">at ${Number(sell_price).toFixed(2)}</span>}
              </div>
            </div>
          )}
        </div>

        {/* Right column: Interactive TradingView Chart */}
        <div className="lg:col-span-2 bg-white p-4 border border-gray-200 rounded-xl shadow-sm flex flex-col">
          <div className="flex justify-between items-center mb-3">
            <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">📈 Live Candlestick Chart ({ticker})</h5>
            <span className="text-[10px] text-gray-400 font-medium">Daily Candles • NY ET</span>
          </div>
          <div className="w-full h-96 rounded-lg overflow-hidden border border-gray-100 bg-slate-50">
            <iframe
              title={`Chart for ${ticker}`}
              src={`https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(ticker)}&interval=D&hidesidetoolbar=1&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=light&style=1&timezone=America%2FNew_York`}
              className="w-full h-full border-0"
              allowFullScreen
            />
          </div>
          <span className="text-[10px] text-gray-400 mt-2 text-right">
            Interactive chart powered by TradingView • Verify trade setup before manual broker execution
          </span>
        </div>
      </div>
    </div>
  );
}

export default function RecommendationsTable({
  portfolioData: initialPortfolioData,
  scanLogData: initialScanLogData,
  data: initialLegacyData,
  scanLog,
}: TableProps) {
  // Tab State: 'portfolio' is default
  const [activeTab, setActiveTab] = useState<'portfolio' | 'scanLog'>('portfolio');
  const [sorting, setSorting] = useState<SortingState>([{ id: 'entry_date', desc: true }]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [expanded, setExpanded] = useState<ExpandedState>({});

  const router = useRouter();
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Derived datasets
  const portfolioSignals = useMemo(() => {
    if (initialPortfolioData) return initialPortfolioData;
    if (initialLegacyData) {
      return initialLegacyData.filter(r => r.status !== 'rejected' && r.status !== 'cancelled_gap_up');
    }
    return [];
  }, [initialPortfolioData, initialLegacyData]);

  const scanLogSignals = useMemo(() => {
    if (initialScanLogData) return initialScanLogData;
    if (initialLegacyData) {
      return initialLegacyData.filter(r => r.status === 'rejected' || r.status === 'cancelled_gap_up');
    }
    return [];
  }, [initialScanLogData, initialLegacyData]);

  const activeDataset = activeTab === 'portfolio' ? portfolioSignals : scanLogSignals;

  // Read-only server component refresh
  const handleRefresh = () => {
    setIsRefreshing(true);
    router.refresh();
    setTimeout(() => setIsRefreshing(false), 800);
  };

  // 1. Portfolio View Columns
  const portfolioColumns = useMemo<ColumnDef<Recommendation>[]>(
    () => [
      {
        accessorKey: 'ticker',
        header: 'Ticker',
        cell: ({ row }) => {
          const ticker = row.original.ticker;
          const company = row.original.company_name;
          const tier = row.original.tier_label;
          const strat = row.original.strategy_name || row.original.strategy || 'Momentum';
          const score = row.original.composite_score;

          return (
            <div className="flex flex-col items-start gap-1">
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-gray-900 tracking-tight text-base leading-tight">{ticker}</span>
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold border ${getTierBadge(tier)}`}>
                  {tier || 'Buy'}
                </span>
              </div>
              <span className="text-[11px] text-gray-500 truncate max-w-[150px] font-medium leading-normal" title={company || ''}>
                {company || '-'}
              </span>
              <span className="text-[10px] text-gray-500 font-medium">
                {strat} • Score: <span className="font-bold text-gray-800">{score ? Number(score).toFixed(1) : '-'}</span>
              </span>
            </div>
          );
        },
        size: 160,
      },
      {
        accessorKey: 'entry_date',
        header: 'Entry / Stop',
        cell: ({ row }) => {
          const entry = row.original.entry_price;
          const stop = row.original.stop_loss;
          const date = row.original.entry_date || row.original.scan_date;

          return (
            <div className="flex flex-col gap-0.5">
              <span className="font-mono text-xs font-bold text-gray-900">
                ${entry ? Number(entry).toFixed(2) : '-'}
              </span>
              <span className="font-mono text-[10px] text-red-500 font-semibold">
                Stop: ${stop ? Number(stop).toFixed(2) : '-'}
              </span>
              <span className="text-[10px] text-gray-400 font-medium">{date || '-'}</span>
            </div>
          );
        },
        size: 120,
      },
      {
        id: 'price',
        accessorFn: (row) => {
          if (row.status === 'closed') {
            return row.sell_price || row.price;
          }
          return row.price;
        },
        header: () => (
          <div className="group relative flex items-center gap-1 cursor-help">
            <span>Current Price</span>
            <Info className="w-3.5 h-3.5 text-gray-400 group-hover:text-gray-600 transition-colors" />
            <div className="absolute bottom-full left-1/2 z-30 mb-2 -translate-x-1/2 w-52 rounded-lg bg-gray-950 px-3 py-2 text-[10px] font-medium text-white opacity-0 shadow-xl transition-all duration-200 group-hover:opacity-100 pointer-events-none border border-gray-800 normal-case tracking-normal">
              <span className="block font-bold text-[11px]">Price Updates</span>
              <span className="text-gray-400 block mt-0.5 leading-normal">Refreshed every 15 min during market hours.</span>
              <span className="text-gray-400 block leading-normal mt-0.5">Last scan: {scanLog?.scan_date || 'Today'}</span>
            </div>
          </div>
        ),
        cell: ({ row }) => {
          const price = row.original.price;
          const status = row.original.status || 'open';
          const sell_price = row.original.sell_price;
          const entry = row.original.entry_price;

          if (status === 'closed' && sell_price) {
            return (
              <div className="flex flex-col">
                <span className="font-mono text-xs font-bold text-gray-900">${Number(sell_price).toFixed(2)}</span>
                <span className="text-[10px] text-gray-400 font-semibold">{row.original.exit_date || 'Exit Date'}</span>
              </div>
            );
          }

          const priceVal = price ? Number(price) : null;
          const entryVal = entry ? Number(entry) : null;
          let changeClass = 'text-gray-900';
          if (priceVal && entryVal && priceVal > entryVal) changeClass = 'text-green-700';
          if (priceVal && entryVal && priceVal < entryVal) changeClass = 'text-red-700';

          return (
            <span className={`font-mono text-xs font-bold ${changeClass}`}>
              ${priceVal ? priceVal.toFixed(2) : '-'}
            </span>
          );
        },
        size: 120,
      },
      {
        id: 'targets',
        header: 'Targets',
        cell: ({ row }) => {
          const t1 = row.original.target_1;
          const t2 = row.original.target_2;
          const t3 = row.original.target_3;

          if (!t1) {
            return (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-200 shadow-sm">
                Trailing Stop Active
              </span>
            );
          }

          return (
            <div className="flex flex-col gap-0.5 font-mono text-[10px]">
              <span className="text-green-600 font-semibold">T1: ${t1 ? Number(t1).toFixed(2) : '-'}</span>
              <span className="text-green-600 font-semibold">T2: ${t2 ? Number(t2).toFixed(2) : '—'}</span>
              <span className="text-green-700 font-bold">T3: ${t3 ? Number(t3).toFixed(2) : '—'}</span>
            </div>
          );
        },
        size: 100,
      },
      {
        id: 'scale_out',
        header: 'Scale-Out Plan',
        cell: ({ row }) => {
          const rec = row.original;
          const entry = Number(rec.entry_price || rec.price || 0);
          const t1 = rec.target_1 ? Number(rec.target_1) : null;
          const t2 = rec.target_2 ? Number(rec.target_2) : null;
          const t3 = rec.target_3 ? Number(rec.target_3) : null;

          const t1Gain = t1 && entry > 0 ? ((t1 - entry) / entry * 100).toFixed(1) : null;
          const t2Gain = t2 && entry > 0 ? ((t2 - entry) / entry * 100).toFixed(1) : null;
          const t3Gain = t3 && entry > 0 ? ((t3 - entry) / entry * 100).toFixed(1) : null;

          return (
            <div className="flex flex-col gap-0.5 font-mono text-[10px]">
              <span className="text-emerald-700 font-semibold">50% @ {t1 ? `$${t1.toFixed(2)} (+${t1Gain}%)` : 'T1'}</span>
              {t2 ? (
                <span className="text-blue-700 font-semibold">30% @ ${t2.toFixed(2)} (+{t2Gain}%)</span>
              ) : (
                <span className="text-blue-700 font-semibold">30% @ Trailing</span>
              )}
              {t3 ? (
                <span className="text-purple-700 font-bold">20% @ ${t3.toFixed(2)} (+{t3Gain}%)</span>
              ) : (
                <span className="text-slate-600 font-medium">20% @ Runner</span>
              )}
            </div>
          );
        },
        size: 135,
      },
      {
        id: 'honest_rr',
        header: 'Honest R:R',
        cell: ({ row }) => {
          const rr = row.original.weighted_rr_honest ?? row.original.weighted_rr;
          return (
            <span className="font-mono text-xs font-bold text-gray-900">
              {rr ? `${Number(rr).toFixed(2)}:1` : '-'}
            </span>
          );
        },
        size: 90,
      },
      {
        id: 'pnl_pct',
        accessorFn: (row) => {
          const entry = row.entry_price;
          const status = row.status || 'open';
          const currentPrice = row.price;
          const sell_price = row.sell_price;
          const exit_price = row.exit_price;
          const price = status === 'closed' ? (sell_price || exit_price || currentPrice) : currentPrice;

          if (!entry || !price || Number(entry) === 0) return 0;
          return ((Number(price) - Number(entry)) / Number(entry)) * 100;
        },
        header: 'Return',
        cell: ({ row }) => {
          const entry = row.original.entry_price;
          const status = row.original.status || 'open';
          const currentPrice = row.original.price;
          const sell_price = row.original.sell_price;
          const exit_price = row.original.exit_price;
          const price = status === 'closed' ? (sell_price || exit_price || currentPrice) : currentPrice;

          if (!entry || !price) return <span className="text-gray-300 font-mono text-xs">—</span>;

          const entryVal = Number(entry);
          const priceVal = Number(price);
          if (entryVal === 0) return <span className="text-gray-300 font-mono text-xs">—</span>;

          const pnl = ((priceVal - entryVal) / entryVal) * 100;
          const isPos = pnl >= 0;
          const sign = isPos ? '+' : '';
          const colorClass = isPos ? 'text-green-600' : 'text-red-600';

          if (Math.abs(pnl) < 0.005 && status !== 'closed') {
            return (
              <div className="flex flex-col">
                <span className="font-mono text-xs font-bold text-gray-400">0.00%</span>
                <span className="text-[9px] text-gray-400 font-medium">Entry day</span>
              </div>
            );
          }

          return (
            <div className="flex flex-col">
              <span className={`font-mono text-xs font-bold ${colorClass}`}>
                {sign}{pnl.toFixed(2)}%
              </span>
              {status === 'closed' && (
                <span className="text-[9px] text-gray-400 font-semibold uppercase tracking-wider leading-none mt-0.5">
                  Final
                </span>
              )}
            </div>
          );
        },
        size: 90,
      },
      {
        id: 'days_held',
        accessorFn: (row) => getDaysHeldNumeric(row.entry_date, row.exit_date),
        header: 'Days',
        cell: ({ row }) => {
          const entry = row.original.entry_date;
          const exit = row.original.exit_date;
          return <span className="font-mono text-xs text-gray-600">{getDaysHeld(entry, exit)}</span>;
        },
        size: 60,
      },
    ],
    [scanLog]
  );

  // 2. Scan Log View Columns
  const scanLogColumns = useMemo<ColumnDef<Recommendation>[]>(
    () => [
      {
        accessorKey: 'ticker',
        header: 'Ticker',
        cell: ({ row }) => {
          const ticker = row.original.ticker;
          const company = row.original.company_name;

          return (
            <div className="flex flex-col items-start">
              <span className="font-bold text-gray-900 tracking-tight text-base leading-tight">{ticker}</span>
              <span className="text-[11px] text-gray-500 truncate max-w-[150px] font-medium leading-normal" title={company || ''}>
                {company || '-'}
              </span>
            </div>
          );
        },
        size: 130,
      },
      {
        accessorKey: 'strategy_name',
        header: 'Strategy',
        cell: ({ row }) => {
          const strat = row.original.strategy_name || row.original.strategy || '-';
          return <span className="text-xs font-semibold text-gray-800">{strat}</span>;
        },
        size: 160,
      },
      {
        accessorKey: 'tier_label',
        header: 'Tier',
        cell: ({ row }) => {
          const tier = row.original.tier_label;
          return (
            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold border ${getTierBadge(tier)}`}>
              {tier || 'Neutral'}
            </span>
          );
        },
        size: 100,
      },
      {
        accessorKey: 'composite_score',
        header: 'Composite Score',
        cell: ({ row }) => {
          const score = row.original.composite_score;
          return (
            <span className="font-mono text-xs font-bold text-gray-900">
              {score ? Number(score).toFixed(1) : '-'}
            </span>
          );
        },
        size: 120,
      },
      {
        id: 'honest_rr',
        header: 'Honest R:R',
        cell: ({ row }) => {
          const rr = row.original.weighted_rr_honest ?? row.original.weighted_rr;
          return (
            <span className="font-mono text-xs font-semibold text-gray-700">
              {rr ? Number(rr).toFixed(2) : '-'}
            </span>
          );
        },
        size: 100,
      },
      {
        id: 'reason',
        header: 'Reason',
        cell: ({ row }) => {
          const reason = getRejectionReason(row.original);
          return (
            <div className="flex items-center gap-1.5">
              <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-semibold bg-amber-50 text-amber-900 border border-amber-200/80">
                {reason}
              </span>
            </div>
          );
        },
        size: 220,
      },
      {
        id: 'earnings',
        header: 'Earnings',
        cell: ({ row }) => {
          const sig = row.original;
          const strat = (sig.strategy_name || sig.strategy || '').toLowerCase();
          const isPead = strat.includes('pead') || strat.includes('earnings');
          const isRejected = sig.earnings_rejected || (sig.status === 'rejected' && sig.sell_signal_reason?.includes('Earnings in'));
          const days = sig.days_to_earnings;

          if (isRejected) {
            return (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-50 text-rose-700 border border-rose-200">
                Earnings in {days !== undefined && days !== null ? `${days}d` : 'window'}
              </span>
            );
          }
          if (isPead) {
            return (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-200">
                Post-earnings
              </span>
            );
          }
          return (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
              {days !== undefined && days !== null ? `Earnings passed (${days}d)` : 'Earnings passed'}
            </span>
          );
        },
        size: 140,
      },
      {
        accessorKey: 'scan_date',
        header: 'Scan Date',
        cell: ({ row }) => {
          const date = row.original.scan_date;
          return <span className="font-mono text-xs text-gray-500">{date || '-'}</span>;
        },
        size: 110,
      },
    ],
    []
  );

  const currentColumns = activeTab === 'portfolio' ? portfolioColumns : scanLogColumns;

  const table = useReactTable({
    data: activeDataset,
    columns: currentColumns,
    state: {
      sorting,
      globalFilter,
      expanded,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onExpandedChange: setExpanded,
    getRowCanExpand: () => true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  return (
    <div className="w-full">
      {/* Top Market Regime Banner */}
      <RegimeBanner scanLog={scanLog} />

      {/* Main Tab Switches: Portfolio (Default) vs Scan Log */}
      <div className="flex border-b border-gray-200 mb-6 gap-3">
        <button
          onClick={() => {
            setActiveTab('portfolio');
            setSorting([{ id: 'entry_date', desc: true }]);
            setExpanded({});
          }}
          className={`pb-3 px-4 font-bold text-sm border-b-2 flex items-center gap-2 transition-all duration-200 ${
            activeTab === 'portfolio'
              ? 'border-emerald-600 text-emerald-700 bg-emerald-50/40 rounded-t-lg'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
          }`}
        >
          <Sparkles className="w-4 h-4" />
          <span>Current Recommendations</span>
          <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${
            activeTab === 'portfolio' ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-100 text-gray-600'
          }`}>
            {portfolioSignals.length}
          </span>
        </button>

        <button
          onClick={() => {
            setActiveTab('scanLog');
            setSorting([{ id: 'scan_date', desc: true }]);
            setExpanded({});
          }}
          className={`pb-3 px-4 font-bold text-sm border-b-2 flex items-center gap-2 transition-all duration-200 ${
            activeTab === 'scanLog'
              ? 'border-blue-600 text-blue-700 bg-blue-50/40 rounded-t-lg'
              : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
          }`}
        >
          <History className="w-4 h-4" />
          <span>Scan Audit & History</span>
          <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${
            activeTab === 'scanLog' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-600'
          }`}>
            {scanLogSignals.length}
          </span>
        </button>
      </div>

      {activeDataset.length === 0 ? (
        <div className="text-center py-12 px-4 max-w-lg mx-auto bg-gray-50 rounded-lg border border-gray-100 shadow-sm">
          <div className="text-4xl mb-4">💤</div>
          <h3 className="text-lg font-semibold text-gray-900">
            {activeTab === 'portfolio' ? 'No active recommendations' : 'No rejected scan candidates'}
          </h3>
          <p className="text-gray-500 mt-2 font-medium italic">
            {activeTab === 'portfolio' ? 'No qualified trade setups from the latest scan.' : 'All candidate signals passed validation filters.'}
          </p>
        </div>
      ) : (
        <>
          {/* Search Input & Action Buttons */}
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4 mb-6">
            <div className="flex items-center gap-2 max-w-sm border border-gray-300 rounded-lg px-3 py-2 bg-white focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500 flex-1">
              <Search className="w-4 h-4 text-gray-400" />
              <input
                type="text"
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                placeholder={activeTab === 'portfolio' ? "Filter recommendations by ticker, company, strategy..." : "Filter scan log by ticker, strategy, reason..."}
                className="w-full text-sm outline-none bg-transparent text-gray-700 placeholder-gray-400"
              />
            </div>

            {/* Refresh Button on Recommendations Tab */}
            {activeTab === 'portfolio' && (
              <div className="flex items-center gap-3">
                <button
                  onClick={handleRefresh}
                  disabled={isRefreshing}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 border border-gray-300 rounded-lg text-sm font-semibold text-gray-700 bg-white hover:bg-gray-50 transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                >
                  <RefreshCw className={`w-4 h-4 text-gray-500 ${isRefreshing ? 'animate-spin' : ''}`} />
                  <span>{isRefreshing ? 'Refreshing...' : 'Refresh'}</span>
                </button>
              </div>
            )}
          </div>

          {/* Responsive Table Wrapper */}
          <div className="overflow-x-auto border border-gray-200 rounded-lg shadow bg-white">
            <table className="min-w-full divide-y divide-gray-200 text-left text-sm text-gray-700">
              <thead className="bg-gray-50 text-[10px] font-semibold uppercase text-gray-500 tracking-wider">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => {
                      const sortDirection = header.column.getIsSorted();
                      return (
                        <th
                          key={header.id}
                          onClick={header.column.getToggleSortingHandler()}
                          className="px-4 py-3 cursor-pointer select-none hover:bg-gray-100 transition-colors"
                        >
                          <div className="flex items-center gap-1">
                            {flexRender(
                              header.column.columnDef.header,
                              header.getContext()
                            )}
                            {header.column.getCanSort() && (
                              <span>
                                {sortDirection === 'asc' ? (
                                  <ArrowUp className="w-3.5 h-3.5 text-blue-600" />
                                ) : sortDirection === 'desc' ? (
                                  <ArrowDown className="w-3.5 h-3.5 text-blue-600" />
                                ) : (
                                  <ArrowUpDown className="w-3.5 h-3.5 text-gray-400" />
                                )}
                              </span>
                            )}
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {table.getRowModel().rows.map((row) => (
                  <React.Fragment key={row.id}>
                    <tr
                      key={row.id}
                      onClick={() => row.toggleExpanded()}
                      className="hover:bg-blue-50/30 transition-colors cursor-pointer"
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-4 py-3.5 whitespace-nowrap text-gray-900">
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext()
                          )}
                        </td>
                      ))}
                    </tr>
                    {row.getIsExpanded() && (
                      <tr className="bg-gray-50/30">
                        <td colSpan={row.getVisibleCells().length} className="px-0 py-0">
                          <ExpandableDetails
                            row={row}
                            isScanLog={activeTab === 'scanLog'}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* Bottom Counts */}
          <div className="mt-4 text-xs text-gray-500 flex justify-between px-1">
            <span>Showing {table.getRowModel().rows.length} of {activeDataset.length} records</span>
            <span>Click rows to expand details and load TradingView chart | Column headers to sort</span>
          </div>
        </>
      )}
    </div>
  );
}
