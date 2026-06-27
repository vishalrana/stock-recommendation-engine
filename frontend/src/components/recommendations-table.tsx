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
import { ArrowUpDown, ArrowUp, ArrowDown, Search } from 'lucide-react';

interface TableProps {
  data: Recommendation[];
  regime: string | null;
  scanLog: ScanLog | null;
}

function RegimeBanner({ scanLog, count }: { scanLog: ScanLog | null; count: number }) {
  const regime = scanLog?.regime || 'bull';
  const regimeStr = regime === 'bull' ? 'Bull Market' : regime === 'bear' ? 'Bear Market' : 'Sideways Market';

  let bannerBg = 'bg-green-50 border-green-200 text-green-800';
  if (regime === 'bear') bannerBg = 'bg-red-50 border-red-200 text-red-800';
  else if (regime === 'sideways') bannerBg = 'bg-blue-50 border-blue-200 text-blue-800';

  let text = '';
  if (count === 0) {
    text = `No signals — Cash is a position`;
  } else {
    text = `${regimeStr} — ${count} signal${count > 1 ? 's' : ''} tonight`;
  }

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
  const winRate = row.original.past_win_rate || 0;
  const trades = row.original.total_trades || 0;
  const wins = Math.round(trades * (winRate / 100));
  const losses = trades - wins;

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

      {/* Grid containing indicator values */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm mb-4">
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">RSI</div>
          <div className="text-gray-900 font-medium mt-0.5">{row.original.current_rsi?.toFixed(1) || '-'}</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">ADX</div>
          <div className="text-gray-900 font-medium mt-0.5">{row.original.adx_value?.toFixed(1) || '-'}</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">Volume</div>
          <div className="text-gray-900 font-medium mt-0.5">{row.original.volume_ratio?.toFixed(2) || '-'}x</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">MACD</div>
          <div className="text-gray-900 font-medium mt-0.5">{row.original.macd_histogram?.toFixed(4) || '-'}</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">EMA20</div>
          <div className="text-gray-900 font-medium mt-0.5">{row.original.ema20 ? `$${row.original.ema20.toFixed(2)}` : '-'}</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">Composite Score</div>
          <div className="text-gray-900 font-medium mt-0.5">{row.original.composite_score?.toFixed(1) || '-'}</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">Expectancy</div>
          <div className="text-gray-900 font-medium mt-0.5">
            {row.original.expectancy_pct ? `${row.original.expectancy_pct > 0 ? '+' : ''}${row.original.expectancy_pct.toFixed(2)}%` : '-'}
          </div>
        </div>
        <div>
          <div className="text-gray-500 text-xs font-semibold uppercase tracking-wider">Track Record</div>
          <div className="text-gray-900 font-medium mt-0.5">
            {trades > 0 ? `${wins} wins / ${losses} losses` : '-'}
          </div>
        </div>
      </div>

      {row.original.narrative && (
        <div className="mb-4 text-xs text-gray-600 bg-white border border-gray-100 p-2.5 rounded-md max-w-2xl shadow-sm">
          <span className="font-semibold text-gray-700 block mb-0.5">Narrative Story:</span>
          {row.original.narrative}
        </div>
      )}

      <div className="flex gap-2">
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
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'ticker', desc: false }
  ]);
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
              <div className="text-xs text-gray-500 truncate max-w-[120px]" title={company || ''}>{company}</div>
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
        id: 'verdict',
        header: 'Verdict',
        cell: ({ row }) => {
          const tier = row.original.tier_label;
          const narrative = row.original.narrative || 'Technical setup';
          const isStrongBuy = tier === 'Strong Buy';
          return (
            <div>
              <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                isStrongBuy ? 'bg-green-100 text-green-800 border border-green-200' : 'bg-blue-100 text-blue-800 border border-blue-200'
              }`}>
                {tier}
              </span>
              <div className="text-xs text-gray-500 mt-1 max-w-[150px] truncate" title={narrative}>
                {narrative}
              </div>
            </div>
          );
        },
        size: 160,
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
          const t1 = row.original.target_1;
          const t2 = row.original.target_2;
          const t3 = row.original.target_3;
          const t1pct = row.original.target_1_pct;
          const t2pct = row.original.target_2_pct;
          const t3pct = row.original.target_3_pct;
          const rr = row.original.weighted_rr;

          let rrColor = 'text-gray-500';
          let rrBg = 'bg-gray-100';
          if (rr !== undefined && rr !== null) {
            if (rr >= 2.0) { rrColor = 'text-green-700'; rrBg = 'bg-green-50'; }
            else if (rr >= 1.0) { rrColor = 'text-yellow-700'; rrBg = 'bg-yellow-50'; }
            else { rrColor = 'text-red-700'; rrBg = 'bg-red-50'; }
          }

          return (
            <div className="space-y-0.5 min-w-[130px] group relative cursor-help" title="Sell 50% at first target, move stop to breakeven">
              <div className="text-xs text-gray-700">
                50% at <span className="font-semibold text-gray-900">${Math.round(t1 || 0)}</span>
                <span className="text-green-600 ml-1">+{Math.round(t1pct || 0)}%</span>
              </div>
              <div className="text-xs text-gray-500 hidden sm:block">
                30% at <span className="font-medium">${Math.round(t2 || 0)}</span>
                <span className="text-blue-600 ml-1">+{Math.round(t2pct || 0)}%</span>
              </div>
              <div className="text-xs text-gray-500 hidden sm:block">
                20% at <span className="font-medium">${Math.round(t3 || 0)}</span>
                <span className="text-purple-600 ml-1">+{Math.round(t3pct || 0)}%</span>
              </div>
              <div className="block sm:hidden text-[10px] text-blue-500 font-semibold mt-0.5">2 more targets</div>
              {rr !== null && rr !== undefined && (
                <div className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${rrBg} ${rrColor} inline-block mt-1 hidden sm:inline-block`}>
                  R/R: {rr.toFixed(1)}
                </div>
              )}
            </div>
          );
        },
        size: 150,
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
          const maxShares = risk > 0 ? Math.floor(100 / risk) : 0;
          return (
            <div className="text-xs group relative cursor-help" title={`Risk per share. For $10K account at 1% risk = ${maxShares} shares`}>
              <div className="text-red-600 font-semibold">-${risk.toFixed(2)}</div>
              <div className="text-gray-500">({riskPct.toFixed(1)}%)</div>
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
          if (winRate === undefined || winRate === null || !trades) {
            return <span className="text-xs text-gray-400">No data</span>;
          }

          let color = 'text-yellow-600';
          if (winRate >= 70 && trades >= 10) color = 'text-green-600 font-semibold';
          else if (winRate < 50) color = 'text-red-600 font-semibold';

          return (
            <div className="text-xs cursor-help" title={`Based on ${trades} past similar setups`}>
              <div className={`font-medium ${color}`}>{winRate.toFixed(0)}% wins</div>
              <div className="text-gray-500">({trades} trades)</div>
            </div>
          );
        },
        size: 100,
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
