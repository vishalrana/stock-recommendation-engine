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
import { ArrowUpDown, ArrowUp, ArrowDown, Search, Info, RefreshCw } from 'lucide-react';
import { useRouter } from 'next/navigation';

function getDaysHeld(entryDateStr: string | null | undefined, exitDateStr: string | null | undefined): string {
  if (!entryDateStr) return '-';
  try {
    const entry = new Date(entryDateStr + 'T00:00:00');
    const exit = exitDateStr ? new Date(exitDateStr + 'T00:00:00') : new Date();
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
    const entry = new Date(entryDateStr + 'T00:00:00');
    const exit = exitDateStr ? new Date(exitDateStr + 'T00:00:00') : new Date();
    const diffTime = exit.getTime() - entry.getTime();
    return Math.max(0, Math.floor(diffTime / (1000 * 60 * 60 * 24)));
  } catch {
    return 0;
  }
}

function getKellySize(score: number, rr: number): { kellyPct: number } {
  let winRate = 0.35;
  if (score >= 90) winRate = 0.75;
  else if (score >= 80) winRate = 0.68;
  else if (score >= 70) winRate = 0.60;
  else if (score >= 60) winRate = 0.52;
  else if (score >= 50) winRate = 0.45;

  const r = rr > 0 ? rr : 2.0;
  const kelly = winRate - (1 - winRate) / r;
  const halfKelly = Math.max(0, kelly / 2);

  return {
    kellyPct: halfKelly * 100
  };
}

function parseAllocationPct(positionSizing: string | null | undefined, score: number, rr: number): number {
  if (positionSizing) {
    const raw = positionSizing.replace('Kelly:', '').replace('K:', '').replace('%', '').trim();
    const parsed = parseFloat(raw);
    if (!isNaN(parsed)) {
      return parsed;
    }
  }
  return getKellySize(score, rr).kellyPct;
}

interface TableProps {
  data: Recommendation[];
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

function ExpandableDetails({ row }: { row: any }) {
  const ticker = row.original.ticker;
  const company = row.original.company_name;
  const industry = row.original.industry;

  // Context breakdown
  const context_analyst = row.original.context_analyst ?? 0;
  const context_earnings = row.original.context_earnings ?? 0;
  const context_news = row.original.context_news ?? 0;
  const context_fundamental = row.original.context_fundamental ?? 0;

  // Sell signal details
  const sell_signal = row.original.sell_signal;
  const sell_signal_reason = row.original.sell_signal_reason;
  const sell_price = row.original.sell_price;

  return (
    <div className="space-y-4 text-gray-700 p-6 bg-slate-50/50 rounded-b-xl border-t border-gray-100">
      <div className="border-b border-gray-200/60 pb-3">
        <h4 className="text-base font-bold text-gray-900">{company || ticker}</h4>
        <span className="text-xs text-gray-500 font-medium">{industry || 'General Industry'}</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Metadata & Context */}
        <div className="space-y-4 lg:col-span-1">
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
          <div className="bg-white p-4 border border-gray-200 rounded-xl shadow-sm">
            <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">⚡ Position Details</h5>
            {sell_signal || row.original.status === 'closed' ? (
              <div className="text-xs font-bold text-red-600 leading-relaxed">
                {row.original.status === 'closed' ? '🏁 Exit complete:' : '⚠️ Active sell alert:'} {sell_signal_reason}
                {sell_price && <span className="block font-mono text-gray-700 mt-1">at ${Number(sell_price).toFixed(2)}</span>}
              </div>
            ) : (
              <div className="text-xs text-gray-500 font-medium">
                Monitoring active in real-time. No trigger breaches detected.
              </div>
            )}
          </div>
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

export default function RecommendationsTable({ data, scanLog, latestPortfolioValue }: TableProps) {
  const [activeTab, setActiveTab] = useState<'active' | 'closed'>('active');
  const [sorting, setSorting] = useState<SortingState>([{ id: 'entry_date', desc: true }]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [expanded, setExpanded] = useState<ExpandedState>({});
  
  const router = useRouter();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastSynced, setLastSynced] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<{ text: string; isError: boolean } | null>(null);

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
          isError: true
        });
      } else if (!res.ok) {
        setSyncMessage({
          text: `Failed to sync market: ${result.error || 'Unknown error'}`,
          isError: true
        });
      } else {
        const now = new Date();
        const nyTimeStr = now.toLocaleTimeString('en-US', {
          timeZone: 'America/New_York',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false
        });
        setLastSynced(`${nyTimeStr} ET`);
        setSyncMessage({
          text: 'Market synced successfully!',
          isError: false
        });
        router.refresh();
      }
    } catch (e: any) {
      setSyncMessage({
        text: `Sync failed: ${e.message || e}`,
        isError: true
      });
    } finally {
      setIsRefreshing(false);
      setTimeout(() => setSyncMessage(null), 5000);
    }
  };

  // Filter recommendations based on active/closed tab
  const filteredData = useMemo(() => {
    if (activeTab === 'active') {
      return data.filter(r => r.status === 'open' || r.status === 'pending');
    } else {
      return data.filter(r => r.status === 'closed' || r.status?.startsWith('cancelled'));
    }
  }, [data, activeTab]);

  const columns = React.useMemo<ColumnDef<Recommendation>[]>(
    () => [
      {
        accessorKey: 'ticker',
        header: 'Ticker',
        cell: ({ row }) => {
          const ticker = row.original.ticker;
          const company = row.original.company_name;
          const tier = row.original.tier_label;
          const score = row.original.composite_score || 50;
          const rr = row.original.weighted_rr || 2.0;
          const kellyPct = parseAllocationPct(row.original.position_sizing, score, rr);

          const getTierColor = (t: string | null) => {
            if (t === 'Strong Buy') return 'bg-green-50 text-green-700 border-green-200';
            if (t === 'Buy') return 'bg-blue-50 text-blue-700 border-blue-200';
            return 'bg-gray-50 text-gray-600 border-gray-200';
          };

          return (
            <div className="flex flex-col items-start gap-1">
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-gray-900 tracking-tight text-base leading-tight">{ticker}</span>
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold border ${getTierColor(tier || null)}`}>
                  {tier}
                </span>
              </div>
              <span className="text-[11px] text-gray-500 truncate max-w-[150px] font-medium leading-normal" title={company || ''}>
                {company}
              </span>
              <span className="text-[10px] text-gray-400 font-semibold mt-0.5">
                Allocation: {kellyPct.toFixed(1)}%
              </span>
            </div>
          );
        },
        size: 155,
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
            <span>{activeTab === 'active' ? 'Current Price' : 'Exit Price'}</span>
            <Info className="w-3.5 h-3.5 text-gray-400 group-hover:text-gray-600 transition-colors" />
            <div className="absolute bottom-full left-1/2 z-30 mb-2 -translate-x-1/2 w-52 rounded-lg bg-gray-950 px-3 py-2 text-[10px] font-medium text-white opacity-0 shadow-xl transition-all duration-200 group-hover:opacity-100 pointer-events-none border border-gray-800 normal-case tracking-normal">
              <span className="block font-bold text-[11px]">Price Updates</span>
              <span className="text-gray-400 block mt-0.5 leading-normal">Refreshed every 15 min during market hours (4:00–20:00 ET).</span>
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
          
          if (status.startsWith('cancelled') && sell_price) {
            return (
              <div className="flex flex-col">
                <span className="font-mono text-xs font-bold text-gray-400">${Number(sell_price).toFixed(2)}</span>
                <span className="text-[10px] text-red-500 font-semibold">Cancelled</span>
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

          if (!t1 && !t2 && !t3) {
            const stopLoss = row.original.stop_loss;
            return (
              <div className="flex flex-col gap-0.5">
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider bg-amber-50 text-amber-700 border border-amber-200">
                  ⚡ Trailing Stop
                </span>
                {stopLoss && (
                  <span className="font-mono text-[10px] text-red-600 font-bold">
                    Stop: ${Number(stopLoss).toFixed(2)}
                  </span>
                )}
              </div>
            );
          }

          return (
            <div className="flex flex-col gap-0.5 font-mono text-[10px]">
              <span className="text-green-600 font-semibold">T1: ${t1 ? Number(t1).toFixed(0) : '-'}</span>
              <span className="text-green-600 font-semibold">T2: ${t2 ? Number(t2).toFixed(0) : '-'}</span>
              <span className="text-green-700 font-bold">T3: ${t3 ? Number(t3).toFixed(0) : '-'}</span>
            </div>
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
          const price = (status === 'closed' && sell_price) ? sell_price : currentPrice;

          if (!entry || !price || Number(entry) === 0) return 0;
          return ((Number(price) - Number(entry)) / Number(entry)) * 100;
        },
        header: 'P&L',
        cell: ({ row }) => {
          const entry = row.original.entry_price;
          const status = row.original.status || 'open';
          const currentPrice = row.original.price;
          const sell_price = row.original.sell_price;
          const price = (status === 'closed' && sell_price) ? sell_price : currentPrice;

          if (!entry || !price) return <span className="text-gray-300">—</span>;

          const entryVal = Number(entry);
          const priceVal = Number(price);
          if (entryVal === 0) return <span className="text-gray-300">—</span>;

          const pnl = ((priceVal - entryVal) / entryVal) * 100;
          const isPos = pnl >= 0;
          
          // Calculate absolute dollars
          let allocationPct = 0.05;
          if (row.original.position_sizing) {
            const raw = row.original.position_sizing.replace('Kelly:', '').replace('K:', '').replace('%', '').trim();
            const parsed = parseFloat(raw);
            if (!isNaN(parsed)) {
              allocationPct = parsed / 100.0;
            }
          }
          const tradeSize = allocationPct * latestPortfolioValue;
          const pnlDollars = tradeSize * (pnl / 100);
          
          const sign = isPos ? '+' : '';
          const colorClass = isPos ? 'text-green-600' : 'text-red-600';

          if (Math.abs(pnl) < 0.005 && status !== 'closed') {
            return (
              <div className="flex flex-col">
                <span className="font-mono text-xs font-bold text-gray-400">$0.00 (0.00%)</span>
                <span className="text-[9px] text-gray-400 font-medium">Entry day</span>
              </div>
            );
          }

          if (status.startsWith('cancelled')) {
            return <span className="text-gray-400 font-mono text-xs">—</span>;
          }

          return (
            <div className="flex flex-col">
              <span className={`font-mono text-xs font-bold ${colorClass}`}>
                {sign}${pnlDollars.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ({sign}{pnl.toFixed(2)}%)
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
    [latestPortfolioValue, activeTab, scanLog]
  );

  const table = useReactTable({
    data: filteredData,
    columns,
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
      {/* Top Banner */}
      <RegimeBanner scanLog={scanLog} />

      {/* Tabs Layout */}
      <div className="flex border-b border-gray-200 mb-6 gap-2">
        <button
          onClick={() => {
            setActiveTab('active');
            setSorting([{ id: 'entry_date', desc: true }]);
            setExpanded({});
          }}
          className={`py-2 px-4 font-bold text-sm border-b-2 transition-all duration-200 ${
            activeTab === 'active'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          Active Positions ({data.filter(r => r.status === 'open' || r.status === 'pending').length})
        </button>
        <button
          onClick={() => {
            setActiveTab('closed');
            setSorting([{ id: 'price', desc: true }]); // exit_date is within exit price accessor
            setExpanded({});
          }}
          className={`py-2 px-4 font-bold text-sm border-b-2 transition-all duration-200 ${
            activeTab === 'closed'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          Closed History ({data.filter(r => r.status === 'closed' || r.status?.startsWith('cancelled')).length})
        </button>
      </div>

      {filteredData.length === 0 ? (
        <div className="text-center py-12 px-4 max-w-lg mx-auto bg-gray-50 rounded-lg border border-gray-100 shadow-sm">
          <div className="text-4xl mb-4">💤</div>
          <h3 className="text-lg font-semibold text-gray-900">
            {activeTab === 'active' ? 'No active positions or recommendations' : 'No closed history found'}
          </h3>
          <p className="text-gray-500 mt-2 font-medium italic">&quot;Cash is a position.&quot;</p>
        </div>
      ) : (
        <>
          {/* Search Input & Refresh Button */}
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4 mb-6">
            <div className="flex items-center gap-2 max-w-sm border border-gray-300 rounded-lg px-3 py-2 bg-white focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500 flex-1">
              <Search className="w-4 h-4 text-gray-400" />
              <input
                type="text"
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                placeholder="Filter by ticker, company, or industry..."
                className="w-full text-sm outline-none bg-transparent text-gray-700 placeholder-gray-400"
              />
            </div>
            
            <div className="flex items-center gap-3">
              {lastSynced && (
                <span className="text-xs font-semibold text-slate-500">
                  Last Sync: {lastSynced}
                </span>
              )}
              <button
                onClick={handleSyncMarket}
                disabled={isRefreshing}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 border border-gray-300 rounded-lg text-sm font-semibold text-gray-700 bg-white hover:bg-gray-50 transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 text-gray-500 ${isRefreshing ? 'animate-spin' : ''}`} />
                <span>Sync Live Market</span>
              </button>
            </div>
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
                          <ExpandableDetails row={row} />
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
            <span>Showing {table.getRowModel().rows.length} of {filteredData.length} records</span>
            <span>Click rows to expand details and load TradingView chart | Column headers to sort</span>
          </div>
        </>
      )}
    </div>
  );
}
