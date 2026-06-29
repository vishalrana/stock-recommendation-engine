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
  const regimeStr = regime === 'bull' ? 'Bull Market' : regime === 'bear' ? 'Bear Market' : 'Sideways Market';
  const activeStrategies = scanLog?.active_strategies ?? 0;

  let bannerBg = 'bg-green-50 border-green-200 text-green-800';
  if (regime === 'bear') bannerBg = 'bg-red-50 border-red-200 text-red-800';
  else if (regime === 'sideways') bannerBg = 'bg-blue-50 border-blue-200 text-blue-800';

  const text = `${regimeStr} — ${activeStrategies} strategies active | ${count} signal${count === 1 ? '' : 's'} tonight`;

  return (
    <div className={`mb-6 rounded-lg border px-4 py-3 text-sm font-semibold text-center sm:text-left ${bannerBg}`}>
      {text}
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
      className="inline-flex items-center px-3 py-1.5 bg-gray-200 text-gray-800 text-sm rounded hover:bg-gray-300 font-medium transition-colors"
    >
      {copied ? 'Copied!' : 'Copy Ticker'}
    </button>
  );
}

function ExpandableDetails({ row }: { row: any }) {
  const wins = row.original.wins ?? row.original.past_wins ?? 0;
  const losses = row.original.losses ?? row.original.past_losses ?? 0;
  const winRate = Math.round(row.original.past_win_rate || 0);

  return (
    <div className="p-4 bg-gray-50 border-t rounded-lg">
      {/* Mobile Only Targets Preview */}
      <div className="block sm:hidden border-b border-gray-200 pb-3 mb-3">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Profit Plan Targets:</div>
        <div className="space-y-1">
          <div className="text-xs text-gray-600">
            50% at <span className="font-semibold text-gray-900">${Math.round(row.original.target_1 || 0)}</span> <span className="text-green-600 font-medium">+{Math.round(row.original.target_1_pct || 0)}%</span>
          </div>
          <div className="text-xs text-gray-600">
            30% at <span className="font-semibold text-gray-900">${Math.round(row.original.target_2 || 0)}</span> <span className="text-blue-600 font-medium">+{Math.round(row.original.target_2_pct || 0)}%</span>
          </div>
          <div className="text-xs text-gray-600">
            20% at <span className="font-semibold text-gray-900">${Math.round(row.original.target_3 || 0)}</span> <span className="text-purple-600 font-medium">+{Math.round(row.original.target_3_pct || 0)}%</span>
          </div>
          {row.original.weighted_rr !== null && row.original.weighted_rr !== undefined && (
            <div className="text-xs font-semibold text-gray-800 mt-1">
              R/R Ratio: {row.original.weighted_rr.toFixed(1)} ⚠️
            </div>
          )}
        </div>
      </div>

      {/* Technical Indicators */}
      <div className="mb-4">
        <h4 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">📊 Technical Indicators</h4>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm bg-white p-3 border border-gray-100 rounded-md shadow-sm">
          <div>
            <div className="text-gray-500 text-xs">RSI</div>
            <div className="text-gray-900 font-semibold mt-0.5">{row.original.current_rsi?.toFixed(1) || '-'}</div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">ADX</div>
            <div className="text-gray-900 font-semibold mt-0.5">{row.original.adx_value?.toFixed(1) || '-'}</div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">Volume</div>
            <div className="text-gray-900 font-semibold mt-0.5">{row.original.volume_ratio?.toFixed(2) || '-'}x</div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">MACD</div>
            <div className="text-gray-900 font-semibold mt-0.5">{row.original.macd_histogram?.toFixed(4) || '-'}</div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">EMA20</div>
            <div className="text-gray-900 font-semibold mt-0.5">{row.original.ema20 ? `$${row.original.ema20.toFixed(2)}` : '-'}</div>
          </div>
        </div>
      </div>

      {/* Performance Metrics */}
      <div className="mb-4">
        <h4 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">📈 Performance Metrics</h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm bg-white p-3 border border-gray-100 rounded-md shadow-sm">
          <div>
            <div className="text-gray-500 text-xs">Composite Score</div>
            <div className="text-gray-900 font-semibold mt-0.5">{row.original.composite_score?.toFixed(1) || '-'}</div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">Context Score</div>
            <div className="text-gray-900 font-semibold mt-0.5">
              {row.original.context_score !== undefined && row.original.context_score !== null 
                ? `${row.original.context_score.toFixed(1)} / 15.0` 
                : '-'}
            </div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">Expectancy</div>
            <div className="text-gray-900 font-semibold mt-0.5">
              {row.original.expectancy_pct ? `${row.original.expectancy_pct > 0 ? '+' : ''}${row.original.expectancy_pct.toFixed(2)}%` : '-'}
            </div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">Track Record</div>
            <div className="text-gray-900 font-semibold mt-0.5">
              {wins} wins / {losses} losses ({winRate}%)
            </div>
          </div>
          <div className="col-span-2 md:col-span-4 pt-3 border-t border-gray-100">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Signal Summary</p>
            <p className="text-sm text-gray-700">{row.original.narrative || 'Technical setup'}</p>
          </div>
        </div>
      </div>

      {(() => {
        const strategyName = row.original.strategy || 'Pullback Recovery';
        const strategyNote = STRATEGY_NOTES[strategyName];
        if (!strategyNote) return null;
        return (
          <div className="mt-3 px-3 py-2 bg-gray-50 border border-gray-200 rounded-md text-sm text-gray-600">
            <span className="font-medium text-gray-700">Strategy note: </span>
            {strategyNote}
          </div>
        );
      })()}
      {(() => {
        const riskPct = row.original.risk_pct || 0;
        if (riskPct > 15) {
          return (
            <div className="mt-3 mb-4 p-2 bg-orange-50 border border-orange-200 rounded text-xs text-orange-700">
              <strong>⚠️ High Risk:</strong> {riskPct.toFixed(1)}% stop distance. 
              Consider a smaller position size or wider stop if volatility is expected.
            </div>
          );
        }
        return null;
      })()}
      <div className="flex gap-2 mt-4 pt-2">
        <a
          href={`https://www.tradingview.com/chart/?symbol=${row.original.ticker}`}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 font-medium transition-colors"
        >
          Open Chart
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
          const price = row.original.price;
          const industry = row.original.industry;
          return (
            <div className="group relative">
              <div className="font-semibold text-gray-900">{ticker}</div>
              <div className="text-xs text-gray-500 whitespace-normal break-words max-w-[140px] leading-tight" title={company || ''}>
                {company}
              </div>
              {/* Tooltip on hover */}
              <div className="absolute left-0 -top-10 hidden group-hover:block bg-gray-800 text-white text-xs rounded px-2.5 py-1 z-10 whitespace-nowrap shadow-md">
                ${price?.toFixed(2)} | {industry || 'N/A'}
              </div>
            </div>
          );
        },
        size: 140,
      },
      {
        id: 'strategy',
        header: 'Strategy',
        cell: ({ row }) => {
          const strategy = row.original.strategy || 'Pullback Recovery';
          const getStrategyColor = (strat: string) => {
            switch (strat) {
              case 'Trend Following': return 'bg-purple-100 text-purple-700';
              case 'Mean Reversion': return 'bg-amber-100 text-amber-700';
              case 'Sector Rotation': return 'bg-teal-100 text-teal-700';
              case 'Post-Earnings Drift': return 'bg-rose-100 text-rose-700';
              case '52-Week High': return 'bg-indigo-100 text-indigo-700';
              case 'Cross-Sectional Momentum': return 'bg-emerald-100 text-emerald-700';
              default: return 'bg-gray-100 text-gray-700'; // Pullback Recovery
            }
          };
          return (
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${getStrategyColor(strategy)}`}>
              {strategy}
            </span>
          );
        },
        size: 120,
      },
      {
        id: 'verdict',
        header: 'Verdict',
        cell: ({ row }) => {
          const tier = row.original.tier_label;
          const isStrongBuy = tier === 'Strong Buy';
          return (
            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${
              isStrongBuy ? 'bg-green-100 text-green-800 border border-green-200' : 'bg-blue-100 text-blue-800 border border-blue-200'
            }`}>
              {tier}
            </span>
          );
        },
        size: 110,
      },
      {
        accessorKey: 'entry_price',
        header: 'Entry',
        cell: (info) => {
          const val = info.getValue() as number;
          return <span className="font-medium text-gray-900">${Math.round(val)}</span>;
        },
        size: 80,
      },
      {
        accessorKey: 'stop_loss',
        header: 'Stop',
        cell: (info) => {
          const val = info.getValue() as number;
          return <span className="font-semibold text-red-600">${Math.round(val)}</span>;
        },
        size: 80,
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
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-semibold text-gray-900 w-6">T1</span>
                <span className="font-mono">${formatPrice(target_1_price)}</span>
                <span className="text-emerald-600 font-medium">+{t1_pct}%</span>
                <span className="text-xs text-gray-400 ml-auto">50% exit</span>
              </div>
              <div className="flex items-center gap-2 text-sm text-gray-600">
                <span className="font-semibold w-6">T2</span>
                <span className="font-mono">${formatPrice(target_2_price)}</span>
                <span className="text-emerald-500">+{t2_pct}%</span>
                <span className="text-xs text-gray-400 ml-auto">30% exit</span>
              </div>
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <span className="font-semibold w-6">T3</span>
                <span className="font-mono">${formatPrice(target_3_price)}</span>
                <span className="text-emerald-400">+{t3_pct}%</span>
                <span className="text-xs text-gray-400 ml-auto">20% exit</span>
              </div>
              <div className="pt-1 border-t border-gray-100 text-xs text-gray-500">
                Weighted R/R: <span className="font-semibold text-gray-700">{weighted_rr}x</span>
              </div>
            </div>
          );
        },
        size: 160,
      },
      {
        id: 'risk',
        header: 'Risk',
        meta: { className: 'hidden md:table-cell' },
        cell: ({ row }) => {
          const entry = row.original.entry_price;
          const stop = row.original.stop_loss;
          const risk = entry && stop ? entry - stop : 0;
          const riskPct = entry && risk ? (risk / entry) * 100 : 0;
          const isHighRisk = riskPct > 15;
          const maxShares = risk > 0 ? Math.floor(100 / risk) : 0;
          return (
            <div className="text-xs group relative cursor-help" title={`Risk per share. For $10K account at 1% risk = ${maxShares} shares`}>
              <div className={`font-medium ${isHighRisk ? 'text-orange-600 font-semibold' : 'text-red-600'}`}>-${Math.round(risk)}</div>
              <div className={`${isHighRisk ? 'text-orange-500 font-semibold' : 'text-gray-500'}`}>
                ({riskPct.toFixed(1)}%)
                {isHighRisk && ' ⚠️'}
              </div>
            </div>
          );
        },
        size: 80,
      },
      {
        id: 'trackRecord',
        header: 'Track Record',
        meta: { className: 'hidden md:table-cell' },
        cell: ({ row }) => {
          const winRate = row.original.past_win_rate;
          const trades = row.original.total_trades;
          const wins = row.original.wins ?? row.original.past_wins;
          const losses = row.original.losses ?? row.original.past_losses;

          if (!trades || trades === 0) {
            return <span className="text-xs text-gray-400">No data</span>;
          }

          let color = 'text-yellow-600';
          if (winRate >= 70 && trades >= 10) color = 'text-green-600 font-semibold';
          else if (winRate < 50) color = 'text-red-600 font-semibold';

          return (
            <div
              className="text-xs cursor-help"
              title={`${wins ?? '?'} wins / ${losses ?? '?'} losses across ${trades} completed trades`}
            >
              <div className={`font-medium ${color}`}>{winRate.toFixed(0)}% wins</div>
              <div className="text-gray-500">({trades} trades)</div>
            </div>
          );
        },
        size: 100,
      },
      {
        id: 'context_score',
        accessorKey: 'context_score',
        header: () => (
          <div className="group relative flex items-center gap-1 cursor-help justify-center sm:justify-start">
            <span>Context</span>
            <HelpCircle className="h-3.5 w-3.5 text-gray-400 hover:text-gray-600 transition-colors" />
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block bg-gray-900 text-white text-xs rounded-lg p-2.5 z-30 w-48 shadow-xl leading-relaxed pointer-events-none border border-gray-800 font-normal normal-case">
              Analyst ratings + Earnings surprises + News sentiment + Fundamentals
            </div>
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
              className="inline-flex items-center justify-center w-8 h-8 rounded-md bg-blue-600 hover:bg-blue-700 text-white transition-colors"
              title={`Open ${ticker} on TradingView`}
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
