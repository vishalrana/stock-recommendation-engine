"use client";

import React, { useState } from 'react';
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
import { ArrowUpDown, ArrowUp, ArrowDown, Search, HelpCircle } from 'lucide-react';

const formatPrice = (val: number | null | undefined): string => {
  if (val === null || val === undefined) return '0';
  return Math.round(val).toString();
};

function ContextBadge({ score }: { score: number }) {
  if (score < 5)  return <span className="px-2 py-1 rounded-full text-xs bg-gray-100 text-gray-500 font-medium">Low</span>;
  if (score < 10) return <span className="px-2 py-1 rounded-full text-xs bg-amber-100 text-amber-700 font-medium">Moderate</span>;
  return           <span className="px-2 py-1 rounded-full text-xs bg-emerald-100 text-emerald-700 font-medium">Strong</span>;
}

const STRATEGY_NOTES: Record<string, string> = {
  'Trend Following':          'Hold until trailing stop (10-day low) hit. Targets are 12%/22%/35% — trends run further than pullbacks.',
  'Pullback Recovery':        'Exit at targets or stop. Short hold 5-15 days. Targets are 7%/12%/18%.',
  'Mean Reversion':           'Quick reversion trade. Hold 3-10 days. Targets are 5%/10%/15%.',
  '52-Week High Breakout':    'Breakout momentum trade. Hold 3-6 weeks. Targets are 10%/18%/28%.',
  '52-Week High':             'Breakout momentum trade. Hold 3-6 weeks. Targets are 10%/18%/28%.',
  'Post-Earnings Drift':      'Post-earnings drift. Hold 2-4 weeks. Targets are 8%/15%/22%.',
  'Sector Rotation':          'Sector leadership trade. Hold 2-6 weeks. Targets are 8%/15%/22%.',
  'Cross-Sectional Momentum': 'Relative strength trade. Hold 2-6 weeks. Targets are 10%/18%/25%.',
};

interface TableProps {
  data: Recommendation[];
  regime: string | null;
  scanLog: ScanLog | null;
}

function RegimeBanner({ scanLog, count }: { scanLog: ScanLog | null; count: number }) {
  const regime = scanLog?.regime || 'bull';
  const regimeStr = regime === 'bull' ? 'Bullish' : regime === 'bear' ? 'Bearish' : 'Sideways';
  const activeStrategies = scanLog?.active_strategies ?? 0;
  const tickersScanned = scanLog?.tickers_scanned ?? 0;
  const rsiBreadth = scanLog?.rsi_breadth_pct ?? 0.0;

  // Pulse animation colors
  const pulseColor = regime === 'bull' ? 'bg-emerald-500 animate-pulse' : regime === 'bear' ? 'bg-rose-500 animate-pulse' : 'bg-blue-500 animate-pulse';
  const regimeBg = regime === 'bull' ? 'bg-emerald-50 text-emerald-800 border-emerald-100' : regime === 'bear' ? 'bg-rose-50 text-rose-800 border-rose-100' : 'bg-blue-50 text-blue-800 border-blue-100';

  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
      {/* Regime Card */}
      <div className="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-sm flex items-center justify-between transition-all hover:shadow-md">
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

      {/* Breadth Card */}
      <div className="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-sm transition-all hover:shadow-md">
        <span className="text-[10px] uppercase tracking-wider text-gray-400 font-bold">RSI Breadth</span>
        <div className="text-xl font-bold text-gray-900 mt-1 flex items-baseline gap-1">
          {rsiBreadth.toFixed(1)}%
          <span className="text-xs text-gray-500 font-normal">passed RSI gate</span>
        </div>
      </div>

      {/* Strategies Card */}
      <div className="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-sm transition-all hover:shadow-md">
        <span className="text-[10px] uppercase tracking-wider text-gray-400 font-bold">Active Engine</span>
        <div className="text-xl font-bold text-gray-900 mt-1 flex items-baseline gap-1">
          {activeStrategies}
          <span className="text-xs text-gray-500 font-normal">strategies scanning</span>
        </div>
      </div>
    </div>
  );
}

function CopyButton({ ticker }: { ticker: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(ticker);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={handleCopy}
      className="inline-flex items-center px-3 py-1.5 bg-gray-100 text-gray-800 text-xs rounded-lg hover:bg-gray-200 font-semibold transition-all active:scale-95 border border-gray-200/60"
    >
      {copied ? '✓ Copied' : 'Copy Ticker'}
    </button>
  );
}

function ExpandableDetails({ row }: { row: any }) {
  const wins = row.original.wins ?? row.original.past_wins ?? 0;
  const losses = row.original.losses ?? row.original.past_losses ?? 0;
  const winRate = Math.round(row.original.past_win_rate || 0);
  const entry = row.original.entry_price || 0;
  const stop = row.original.stop_loss || 0;
  const risk = entry - stop;
  const riskPct = entry && risk ? (risk / entry) * 100 : 0;
  const maxShares = risk > 0 ? Math.floor(100 / risk) : 0;

  return (
    <div className="p-5 bg-gray-50/50 border-t rounded-b-2xl space-y-4">
      {/* Expanded Ticker Header */}
      <div className="border-b border-gray-200/60 pb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <span className="text-base font-bold text-gray-900">{row.original.company_name}</span>
          <span className="text-xs text-gray-500 ml-2">({row.original.industry || 'General Industry'})</span>
        </div>
        <div className="text-xs text-gray-500 font-medium">
          Current Price: <span className="font-semibold text-gray-900">${row.original.price?.toFixed(2)}</span>
        </div>
      </div>

      {/* Technical Indicators & Performance Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Technical Indicators */}
        <div>
          <h4 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">📊 Technical Indicators</h4>
          <div className="grid grid-cols-3 gap-3 text-xs bg-white p-3 border border-gray-200/60 rounded-xl shadow-sm">
            <div>
              <div className="text-gray-400 font-medium">RSI</div>
              <div className="text-gray-900 font-bold mt-0.5">{row.original.current_rsi?.toFixed(1) || '—'}</div>
            </div>
            <div>
              <div className="text-gray-400 font-medium">ADX</div>
              <div className="text-gray-900 font-bold mt-0.5">{row.original.adx_value?.toFixed(1) || '—'}</div>
            </div>
            <div>
              <div className="text-gray-400 font-medium">Volume Ratio</div>
              <div className="text-gray-900 font-bold mt-0.5">{row.original.volume_ratio?.toFixed(2) || '—'}x</div>
            </div>
            <div className="pt-2 border-t border-gray-100">
              <div className="text-gray-400 font-medium">MACD Hist</div>
              <div className="text-gray-900 font-bold mt-0.5">{row.original.macd_histogram?.toFixed(4) || '—'}</div>
            </div>
            <div className="pt-2 border-t border-gray-100">
              <div className="text-gray-400 font-medium">EMA 20</div>
              <div className="text-gray-900 font-bold mt-0.5">{row.original.ema20 ? `$${Math.round(row.original.ema20)}` : '—'}</div>
            </div>
            <div className="pt-2 border-t border-gray-100">
              <div className="text-gray-400 font-medium">Regime Adjustment</div>
              <div className="text-gray-900 font-bold mt-0.5">{row.original.regime || '—'}</div>
            </div>
          </div>
        </div>

        {/* Performance Metrics */}
        <div>
          <h4 className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">📈 Performance Metrics</h4>
          <div className="grid grid-cols-2 gap-3 text-xs bg-white p-3 border border-gray-200/60 rounded-xl shadow-sm">
            <div>
              <div className="text-gray-400 font-medium">Composite Setup Score</div>
              <div className="text-gray-900 font-bold mt-0.5">{row.original.composite_score?.toFixed(1) || '—'}</div>
            </div>
            <div>
              <div className="text-gray-400 font-medium">Context Score</div>
              <div className="text-gray-900 font-bold mt-0.5">
                {row.original.context_score !== undefined && row.original.context_score !== null 
                  ? `${row.original.context_score.toFixed(1)} / 15.0` 
                  : '—'}
              </div>
            </div>
            <div className="pt-2 border-t border-gray-100">
              <div className="text-gray-400 font-medium">Expectancy (Win %)</div>
              <div className="text-gray-900 font-bold mt-0.5">
                {row.original.expectancy_pct ? `${row.original.expectancy_pct > 0 ? '+' : ''}${row.original.expectancy_pct.toFixed(2)}%` : '—'}
              </div>
            </div>
            <div className="pt-2 border-t border-gray-100">
              <div className="text-gray-400 font-medium">Historical Win Rate</div>
              <div className="text-gray-900 font-bold mt-0.5">
                {wins} wins / {losses} losses ({winRate}%)
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Position Sizing and Verdict Summary */}
      <div className="bg-white border border-gray-200/60 rounded-xl p-4 shadow-sm space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div>
            <span className="text-gray-400 font-semibold block mb-0.5">POSITION SIZE ALLOCATION</span>
            <span className="text-gray-900 font-bold">Max shares: {maxShares} shares</span>
            <span className="text-gray-500 block mt-0.5">(Based on standard $10,000 portfolio at 1.0% max risk threshold of $100)</span>
          </div>
          <div>
            <span className="text-gray-400 font-semibold block mb-0.5">STOP LOSS RANGE</span>
            <span className="text-gray-900 font-bold">{riskPct.toFixed(1)}% from entry price</span>
            <span className="text-gray-500 block mt-0.5">
              {riskPct > 15 
                ? '⚠️ Warning: High stop distance. Limit leverage or position size.'
                : '✓ Healthy parameters for normal swing setups.'}
            </span>
          </div>
        </div>

        <div className="pt-3 border-t border-gray-100">
          <span className="text-[10px] text-gray-400 uppercase tracking-wider font-bold block mb-1">Signal Summary Verdict</span>
          <p className="text-xs text-gray-700 leading-relaxed font-medium">{row.original.narrative || 'Technical setup qualifies for strategic execution.'}</p>
        </div>
      </div>

      {/* Strategy and Risk Note Banner */}
      {(() => {
        const strategyName = row.original.strategy || 'Pullback Recovery';
        const strategyNote = STRATEGY_NOTES[strategyName];
        if (!strategyNote) return null;
        return (
          <div className="px-3.5 py-2.5 bg-gray-100 border border-gray-200/60 rounded-xl text-xs text-gray-600 flex items-start gap-2">
            <span className="inline-block px-1.5 py-0.5 bg-gray-200 text-gray-800 rounded font-bold uppercase text-[9px] shrink-0 mt-0.5">Info</span>
            <p>
              <strong className="text-gray-700">Strategy Note: </strong>
              {strategyNote}
            </p>
          </div>
        );
      })()}

      {/* Action Footer Buttons */}
      <div className="flex gap-2 pt-2">
        <a
          href={`https://www.tradingview.com/chart/?symbol=${row.original.ticker}`}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center px-4.5 py-2 bg-blue-600 text-white text-xs rounded-xl hover:bg-blue-700 font-bold tracking-wide transition-all shadow-sm hover:shadow active:scale-95 hover:-translate-y-0.5"
        >
          Open Interactive Chart
        </a>
        <CopyButton ticker={row.original.ticker} />
      </div>
    </div>
  );
}

export default function RecommendationsTable({ data, regime, scanLog }: TableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [expanded, setExpanded] = useState<ExpandedState>({});

  const columns = React.useMemo<ColumnDef<Recommendation>[]>(
    () => [
      {
        accessorKey: 'ticker',
        header: 'Ticker',
        cell: ({ row }) => {
          const ticker = row.original.ticker;
          const company = row.original.company_name;
          const industry = row.original.industry;
          const price = row.original.price;
          return (
            <div className="flex flex-col">
              <span className="font-bold text-gray-900 tracking-tight text-base leading-tight">{ticker}</span>
              <span className="text-[11px] text-gray-500 truncate max-w-[150px] font-medium leading-normal" title={company || ''}>
                {company}
              </span>
              <span className="text-[10px] text-gray-400 font-medium truncate max-w-[150px] leading-normal">
                {industry || 'N/A'} • ${formatPrice(price)}
              </span>
            </div>
          );
        },
        size: 160,
      },
      {
        id: 'strategy_verdict',
        header: 'Setup',
        cell: ({ row }) => {
          const strategy = row.original.strategy || 'Pullback Recovery';
          const tier = row.original.tier_label;
          const isStrongBuy = tier === 'Strong Buy';

          const getStrategyColor = (strat: string) => {
            switch (strat) {
              case 'Trend Following': return 'bg-purple-50 text-purple-700 border-purple-100';
              case 'Mean Reversion': return 'bg-amber-50 text-amber-700 border-amber-100';
              case 'Sector Rotation': return 'bg-teal-50 text-teal-700 border-teal-100';
              case 'Post-Earnings Drift': return 'bg-rose-50 text-rose-700 border-rose-100';
              case '52-Week High': return 'bg-indigo-50 text-indigo-700 border-indigo-100';
              case 'Cross-Sectional Momentum': return 'bg-emerald-50 text-emerald-700 border-emerald-100';
              default: return 'bg-gray-50 text-gray-700 border-gray-200';
            }
          };

          return (
            <div className="flex flex-col gap-1 items-start">
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${getStrategyColor(strategy)}`}>
                {strategy}
              </span>
              <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-bold ${
                isStrongBuy ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-blue-50 text-blue-700 border border-blue-200'
              }`}>
                {tier}
              </span>
            </div>
          );
        },
        size: 140,
      },
      {
        id: 'price_params',
        header: 'Parameters',
        cell: ({ row }) => {
          const entry = row.original.entry_price;
          const stop = row.original.stop_loss;
          const risk = entry && stop ? entry - stop : 0;
          const riskPct = entry && risk ? (risk / entry) * 100 : 0;
          return (
            <div className="flex flex-col text-xs leading-normal">
              <div>
                <span className="text-gray-400 font-semibold uppercase text-[9px] mr-1.5">Entry</span>
                <span className="font-mono text-gray-900 font-bold">${formatPrice(entry)}</span>
              </div>
              <div className="mt-0.5">
                <span className="text-gray-400 font-semibold uppercase text-[9px] mr-1.5">Stop</span>
                <span className="font-mono text-red-600 font-bold">${formatPrice(stop)}</span>
              </div>
              <div className="text-[10px] text-gray-400 font-semibold mt-0.5">
                Risk: {riskPct.toFixed(1)}%
              </div>
            </div>
          );
        },
        size: 110,
      },
      {
        id: 'profitPlan',
        header: 'Profit Plan',
        cell: ({ row }) => {
          const target_1_price = row.original.target_1;
          const target_2_price = row.original.target_2;
          const target_3_price = row.original.target_3;
          const t1_pct = Math.round(row.original.target_1_pct || 0);
          const t2_pct = Math.round(row.original.target_2_pct || 0);
          const t3_pct = Math.round(row.original.target_3_pct || 0);
          const rr = row.original.weighted_rr;
          const weighted_rr = rr !== undefined && rr !== null ? rr.toFixed(1) : '-';

          return (
            <div className="flex flex-col gap-0.5 text-xs text-gray-700 leading-normal">
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-gray-400 text-[10px] w-4">T1</span>
                <span className="font-mono font-bold">${formatPrice(target_1_price)}</span>
                <span className="text-emerald-600 font-bold">+{t1_pct}%</span>
              </div>
              <div className="flex items-center gap-1.5 text-gray-500">
                <span className="font-bold text-gray-400 text-[10px] w-4">T2</span>
                <span className="font-mono font-bold">${formatPrice(target_2_price)}</span>
                <span className="text-emerald-500 font-semibold">+{t2_pct}%</span>
              </div>
              <div className="flex items-center gap-1.5 text-gray-400">
                <span className="font-bold text-gray-400 text-[10px] w-4">T3</span>
                <span className="font-mono font-bold">${formatPrice(target_3_price)}</span>
                <span className="text-emerald-400 font-semibold">+{t3_pct}%</span>
              </div>
              <div className="text-[10px] text-gray-400 font-semibold mt-0.5">
                Weighted R/R: <span className="font-bold text-gray-600">{weighted_rr}x</span>
              </div>
            </div>
          );
        },
        size: 140,
      },
      {
        id: 'context_score',
        accessorKey: 'context_score',
        header: () => (
          <div 
            className="flex items-center gap-1 cursor-help justify-center sm:justify-start"
            title="Context Score (Analyst Ratings + News Sentiment + Earnings Surprises)"
          >
            <span>Context</span>
            <HelpCircle className="h-3.5 w-3.5 text-gray-400 hover:text-gray-600 transition-colors" />
          </div>
        ),
        cell: ({ row }) => {
          const score = row.original.context_score;
          if (score === undefined || score === null || score === 0) {
            return <span className="text-gray-300 text-xs">—</span>;
          }
          return <ContextBadge score={score} />;
        },
        size: 90,
      },
      {
        id: 'chart',
        header: 'Chart',
        cell: ({ row }) => {
          const ticker = row.original.ticker;
          return (
            <a
              href={`https://www.tradingview.com/chart/?symbol=${ticker}`}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-600 hover:text-blue-700 border border-blue-100 transition-all active:scale-95"
              title={`Open ${ticker} on TradingView`}
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 3v18h18"/>
                <path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3"/>
              </svg>
            </a>
          );
        },
        size: 60,
      },
    ],
    []
  );

  const table = useReactTable({
    data,
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
      <RegimeBanner scanLog={scanLog} count={data.length} />

      {data.length === 0 ? (
        <div className="text-center py-12 px-4 max-w-lg mx-auto bg-gray-50 rounded-lg border border-gray-100 shadow-sm">
          <div className="text-4xl mb-4">💤</div>
          <h3 className="text-lg font-semibold text-gray-900">No high-confidence setups tonight</h3>
          <p className="text-gray-500 mt-2 font-medium italic">"Cash is a position."</p>
          <p className="text-gray-400 text-xs mt-3 leading-relaxed">
            The market is in <span className="font-semibold text-gray-600">{scanLog?.regime || 'bull'}</span> regime, but no stocks passed our quality filters.
            This is normal — not every day has a good setup.
          </p>
        </div>
      ) : (
        <>
          {/* Search Input */}
          <div className="flex items-center gap-2 mb-6 max-w-sm border border-gray-300 rounded-lg px-3 py-2 bg-white focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500">
            <Search className="w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={globalFilter}
              onChange={(e) => setGlobalFilter(e.target.value)}
              placeholder="Filter by ticker, company, or industry..."
              className="w-full text-sm outline-none bg-transparent text-gray-700 placeholder-gray-400"
            />
          </div>

          {/* Responsive Table Wrapper */}
          <div className="overflow-x-auto border border-gray-200 rounded-lg shadow bg-white">
            <table className="min-w-full divide-y divide-gray-200 text-left text-sm text-gray-700">
              <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500 tracking-wider">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => {
                      const sortDirection = header.column.getIsSorted();
                      const headerMeta = header.column.columnDef.meta as any;
                      const className = headerMeta?.className || '';
                      return (
                        <th
                          key={header.id}
                          onClick={header.column.getToggleSortingHandler()}
                          className={`px-6 py-3 cursor-pointer select-none hover:bg-gray-100 transition-colors ${className}`}
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
                      onClick={() => row.toggleExpanded()}
                      className="hover:bg-blue-50/10 transition-colors cursor-pointer"
                    >
                      {row.getVisibleCells().map((cell) => {
                        const cellMeta = cell.column.columnDef.meta as any;
                        const className = cellMeta?.className || '';
                        return (
                          <td key={cell.id} className={`px-6 py-4 whitespace-nowrap text-gray-900 ${className}`}>
                            {flexRender(
                              cell.column.columnDef.cell,
                              cell.getContext()
                            )}
                          </td>
                        );
                      })}
                    </tr>
                    {row.getIsExpanded() && (
                      <tr className="bg-gray-50/50">
                        <td colSpan={row.getVisibleCells().length} className="px-0 py-0 border-t">
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
            <span>Showing {table.getRowModel().rows.length} of {data.length} recommendations</span>
            <span>Click rows to expand details | Column headers to sort</span>
          </div>
        </>
      )}
    </div>
  );
}
