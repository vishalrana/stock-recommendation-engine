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
import { ArrowUpDown, ArrowUp, ArrowDown, Search, Info, RefreshCw, AlertCircle, Briefcase, FileText } from 'lucide-react';
import { useRouter } from 'next/navigation';
import SignalExitPlan from './SignalExitPlan';
import { getDollarExits } from '../lib/position-utils';
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

function parseAllocationPct(positionSizing: string | null | undefined, score: number, rr: number): number {
  if (positionSizing) {
    if (positionSizing.includes('/')) {
      return 5.0;
    }
    const raw = positionSizing.replace('Kelly:', '').replace('K:', '').replace('%', '').trim();
    const parsed = parseFloat(raw);
    if (!isNaN(parsed)) {
      return Math.min(parsed, 5.0);
    }
  }
  return 0.0;
}

interface TableProps {
  portfolioData?: Recommendation[];
  scanLogData?: Recommendation[];
  data?: Recommendation[];
  regime: string | null;
  scanLog: ScanLog | null;
  latestPortfolioValue: number;
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

function ExpandableDetails({ row, latestPortfolioValue, isScanLog }: { row: any; latestPortfolioValue?: number; isScanLog?: boolean }) {
  const ticker = row.original.ticker;
  const company = row.original.company_name;
  const industry = row.original.industry;
  const strategy = row.original.strategy_name || row.original.strategy;

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
      <div className="border-b border-gray-200/60 pb-3 flex justify-between items-start">
        <div>
          <h4 className="text-base font-bold text-gray-900">{company || ticker}</h4>
          <span className="text-xs text-gray-500 font-medium">{industry || strategy || 'General Industry'}</span>
        </div>
        {reason && (
          <div className="bg-amber-50 border border-amber-200 text-amber-800 px-3 py-1 rounded-lg text-xs font-semibold flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5 text-amber-600" />
            <span>Decision: {reason}</span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Exit Plan or Decision Details & Context */}
        <div className="space-y-4 lg:col-span-1">
          {!isScanLog ? (
            <SignalExitPlan recommendation={row.original} latestPortfolioValue={latestPortfolioValue} />
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

          {/* Action Panel */}
          {(sell_signal || row.original.status === 'closed') && (
            <div className="bg-white p-4 border border-gray-200 rounded-xl shadow-sm">
              <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">⚡ Position Details</h5>
              <div className="text-xs font-bold text-red-600 leading-relaxed">
                {row.original.status === 'closed' ? '🏁 Exit complete:' : '⚠️ Active sell alert:'} {sell_signal_reason}
                {sell_price && <span className="block font-mono text-gray-700 mt-1">at ${Number(sell_price).toFixed(2)}</span>}
              </div>
            </div>
          )}
        </div>

        {/* Right column: Interactive TradingView Chart */}
        <div className="lg:col-span-2 bg-white p-4 border border-gray-200 rounded-xl shadow-sm flex flex-col">
          <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-3">📈 Live Chart ({ticker})</h5>
          <div className="w-full h-80 rounded-lg overflow-hidden border border-gray-100 bg-slate-50">
            <iframe
              title={`Chart for ${ticker}`}
              src={`https://s.tradingview.com/widgetembed/?symbol=${ticker}&interval=D&hidesidetoolbar=1&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=light&style=1&timezone=America%2FNew_York`}
              className="w-full h-full border-0"
              allowFullScreen
            />
          </div>
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
  latestPortfolioValue,
}: TableProps) {
  // Tab State: 'portfolio' is default
  const [activeTab, setActiveTab] = useState<'portfolio' | 'scanLog'>('portfolio');
  const [sorting, setSorting] = useState<SortingState>([{ id: 'entry_date', desc: true }]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [expanded, setExpanded] = useState<ExpandedState>({});

  const router = useRouter();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [lastSyncTime, setLastSyncTime] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<{ text: string; isError: boolean } | null>(null);

  // Derived datasets
  const portfolioSignals = useMemo(() => {
    if (initialPortfolioData) return initialPortfolioData;
    if (initialLegacyData) {
      return initialLegacyData.filter(r => (Number(r.allocated_dollars) || 0) > 0 && r.status !== 'rejected');
    }
    return [];
  }, [initialPortfolioData, initialLegacyData]);

  const scanLogSignals = useMemo(() => {
    if (initialScanLogData) return initialScanLogData;
    if (initialLegacyData) {
      return initialLegacyData.filter(r => (Number(r.allocated_dollars) || 0) === 0 || r.status === 'rejected' || r.status === 'cancelled_gap_up');
    }
    return [];
  }, [initialScanLogData, initialLegacyData]);

  const activeDataset = activeTab === 'portfolio' ? portfolioSignals : scanLogSignals;

  // Recalculate only active/pending portfolio signals
  const handleRecalculateAll = async () => {
    setIsRecalculating(true);
    setSyncMessage(null);
    try {
      const res = await fetch('/api/signals/recalculate', { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        const s = data.summary;
        const msg = `${s.updatedCount} signals updated: ${s.openCount} open, ${s.hitT1Count} hit T1, ${s.hitT2Count} hit T2, ${s.hitT3Count} hit T3, ${s.stoppedCount} stopped.`;
        setSyncMessage({
          text: msg,
          isError: false,
        });
        router.refresh();
      } else {
        setSyncMessage({
          text: `Recalculation error: ${data.error || 'Failed'}`,
          isError: true,
        });
      }
    } catch (e: any) {
      setSyncMessage({
        text: `Recalculation failed: ${e.message || e}`,
        isError: true,
      });
    } finally {
      setIsRecalculating(false);
      setTimeout(() => setSyncMessage(null), 8000);
    }
  };

  // Centralized evaluation loop HTTP trigger
  const handleSyncMarket = async () => {
    setIsRefreshing(true);
    setSyncMessage(null);
    try {
      const res = await fetch('/api/sync-market', { method: 'POST' });
      const result = await res.json();

      if (res.status === 403) {
        setSyncMessage({
          text: `Market is currently closed: ${result.reason}`,
          isError: true,
        });
      } else if (!res.ok) {
        setSyncMessage({
          text: `Failed to sync market: ${result.error || 'Unknown error'}`,
          isError: true,
        });
      } else {
        const now = new Date();
        const nyTimeStr = now.toLocaleTimeString('en-US', {
          timeZone: 'America/New_York',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        });
        setLastSyncTime(`${nyTimeStr} ET`);
        setSyncMessage({
          text: 'Market synced successfully!',
          isError: false,
        });
        router.refresh();
      }
    } catch (e: any) {
      setSyncMessage({
        text: `Sync failed: ${e.message || e}`,
        isError: true,
      });
    } finally {
      setIsRefreshing(false);
      setTimeout(() => setSyncMessage(null), 5000);
    }
  };

  // Helper for tier color
  const getTierBadge = (tier: string | null | undefined) => {
    if (tier === 'Strong Buy') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    if (tier === 'Buy') return 'bg-blue-50 text-blue-700 border-blue-200';
    return 'bg-gray-50 text-gray-600 border-gray-200';
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
          const score = row.original.composite_score || 50;
          const rr = row.original.weighted_rr_honest || row.original.weighted_rr || 2.0;
          const kellyPct = parseAllocationPct(row.original.position_sizing, score, rr);
          const allocDollars = row.original.allocated_dollars
            ? Number(row.original.allocated_dollars)
            : (kellyPct / 100.0) * latestPortfolioValue;
          const entryPriceVal = row.original.entry_price ? Number(row.original.entry_price) : 0;
          let exactShares = row.original.max_shares && Number(row.original.max_shares) > 0
            ? Number(row.original.max_shares)
            : null;

          if ((!exactShares || exactShares === 0) && row.original.position_sizing && row.original.position_sizing.includes('sh')) {
            const match = row.original.position_sizing.match(/\(([\d.]+)\s*sh\)/);
            if (match && match[1]) {
              exactShares = parseFloat(match[1]);
            }
          }

          if ((!exactShares || exactShares === 0) && allocDollars > 0 && entryPriceVal > 0) {
            exactShares = allocDollars / entryPriceVal;
          }

          let sharesLabel: string | null = null;
          if (exactShares && exactShares > 0) {
            if (Number.isInteger(exactShares)) {
              sharesLabel = exactShares === 1 ? '1 share' : `${exactShares} shares`;
            } else {
              sharesLabel = `${exactShares.toFixed(2)} shares`;
            }
          }

          return (
            <div className="flex flex-col items-start gap-1">
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-gray-900 tracking-tight text-base leading-tight">{ticker}</span>
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold border ${getTierBadge(tier)}`}>
                  {tier}
                </span>
              </div>
              <span className="text-[11px] text-gray-500 truncate max-w-[150px] font-medium leading-normal" title={company || ''}>
                {company}
              </span>
              <span className="text-[10px] text-gray-500 font-semibold mt-0.5">
                Alloc: <span className="font-bold text-gray-900">${allocDollars.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span> ({kellyPct.toFixed(1)}%{sharesLabel ? ` • ${sharesLabel}` : ''})
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
        id: 'exit_dollars',
        header: 'Exit $ (Scale)',
        cell: ({ row }) => {
          const rec = row.original;
          const alloc = rec.allocated_dollars ? Number(rec.allocated_dollars) : 0;
          const breakdown = getDollarExits(alloc, rec.scale_out_weights, {
            target_1: rec.target_1,
            target_2: rec.target_2,
            target_3: rec.target_3,
          });

          return (
            <div className="flex flex-col gap-0.5 font-mono text-[10px]">
              <span className="text-emerald-700 font-semibold">T1: ${breakdown.t1.dollars.toFixed(0)}</span>
              {!breakdown.isT2Removed ? (
                <span className="text-blue-700 font-semibold">T2: ${breakdown.t2.dollars.toFixed(0)}</span>
              ) : null}
              {!breakdown.isT3Removed && breakdown.t3.dollars > 0 ? (
                <span className="text-purple-700 font-bold">T3: ${breakdown.t3.dollars.toFixed(0)}</span>
              ) : breakdown.runner.dollars > 0 ? (
                <span className="text-slate-600 font-medium">Runner: ${breakdown.runner.dollars.toFixed(0)}</span>
              ) : null}
            </div>
          );
        },
        size: 110,
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
        header: 'P&L',
        cell: ({ row }) => {
          const entry = row.original.entry_price;
          const status = row.original.status || 'open';
          const currentPrice = row.original.price;
          const sell_price = row.original.sell_price;
          const exit_price = row.original.exit_price;
          const price = status === 'closed' ? (sell_price || exit_price || currentPrice) : currentPrice;
          const alloc = row.original.allocated_dollars ? Number(row.original.allocated_dollars) : 0;

          // If signal has zero allocation, isolate P&L completely
          if (!entry || !price || alloc <= 0) return <span className="text-gray-300 font-mono text-xs">—</span>;

          const entryVal = Number(entry);
          const priceVal = Number(price);
          if (entryVal === 0) return <span className="text-gray-300 font-mono text-xs">—</span>;

          const pnl = ((priceVal - entryVal) / entryVal) * 100;
          const isPos = pnl >= 0;

          // Calculate exact absolute dollars on allocated capital
          let pnlDollars = 0;
          const shares = row.original.max_shares ? Number(row.original.max_shares) : null;

          if (shares && shares > 0) {
            pnlDollars = (priceVal - entryVal) * shares;
          } else {
            pnlDollars = alloc * (pnl / 100);
          }

          const sign = isPos ? '+' : pnl < 0 ? '-' : '';
          const colorClass = isPos ? 'text-green-600' : 'text-red-600';

          if (Math.abs(pnl) < 0.005 && status !== 'closed') {
            return (
              <div className="flex flex-col">
                <span className="font-mono text-xs font-bold text-gray-400">$0.00 (0.00%)</span>
                <span className="text-[9px] text-gray-400 font-medium">Entry day</span>
              </div>
            );
          }

          return (
            <div className="flex flex-col">
              <span className={`font-mono text-xs font-bold ${colorClass}`}>
                {sign}${Math.abs(pnlDollars).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ({sign}{Math.abs(pnl).toFixed(2)}%)
              </span>
              {status === 'closed' && (
                <span className="text-[9px] text-gray-400 font-semibold uppercase tracking-wider leading-none mt-0.5">
                  Final
                </span>
              )}
            </div>
          );
        },
        size: 130,
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
    [latestPortfolioValue, scanLog]
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
          <Briefcase className="w-4 h-4" />
          <span>Portfolio</span>
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
          <FileText className="w-4 h-4" />
          <span>Scan Log</span>
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
            {activeTab === 'portfolio' ? 'No active portfolio positions' : 'No rejected scan signals'}
          </h3>
          <p className="text-gray-500 mt-2 font-medium italic">
            {activeTab === 'portfolio' ? '"Cash is a position."' : 'All candidate signals met allocation criteria.'}
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
                placeholder={activeTab === 'portfolio' ? "Filter portfolio by ticker, company..." : "Filter scan log by ticker, strategy, reason..."}
                className="w-full text-sm outline-none bg-transparent text-gray-700 placeholder-gray-400"
              />
            </div>

            {/* Recalculate & Sync Buttons are ONLY active on Portfolio Tab */}
            {activeTab === 'portfolio' && (
              <div className="flex items-center gap-3">
                {lastSyncTime && (
                  <span className="text-xs text-gray-400 font-medium select-none">
                    Last Sync: {lastSyncTime}
                  </span>
                )}
                <button
                  onClick={handleRecalculateAll}
                  disabled={isRecalculating}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 border border-blue-200 rounded-lg text-sm font-semibold text-blue-700 bg-blue-50 hover:bg-blue-100 transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                >
                  <RefreshCw className={`w-4 h-4 text-blue-600 ${isRecalculating ? 'animate-spin' : ''}`} />
                  <span>{isRecalculating ? 'Recalculating...' : '🔄 Recalculate All'}</span>
                </button>
                <button
                  onClick={handleSyncMarket}
                  disabled={isRefreshing}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 border border-gray-300 rounded-lg text-sm font-semibold text-gray-700 bg-white hover:bg-gray-50 transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                >
                  <RefreshCw className={`w-4 h-4 text-gray-500 ${isRefreshing ? 'animate-spin' : ''}`} />
                  <span>Sync Live Market</span>
                </button>
              </div>
            )}
          </div>

          {/* Sync status message toast */}
          {syncMessage && (
            <div className={`mb-6 p-4 rounded-xl border text-xs font-semibold shadow-sm transition-all duration-300 ${
              syncMessage.isError
                ? 'bg-rose-50 border-rose-200 text-rose-800'
                : 'bg-emerald-50 border-emerald-200 text-emerald-800'
            }`}>
              {syncMessage.text}
            </div>
          )}

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
                            latestPortfolioValue={latestPortfolioValue}
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
