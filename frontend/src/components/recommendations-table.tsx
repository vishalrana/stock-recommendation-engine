"use client";

import React, { useState } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  ColumnDef,
  flexRender,
  SortingState,
} from '@tanstack/react-table';
import { Recommendation } from '../types/database';
import { ArrowUpDown, ArrowUp, ArrowDown, Search } from 'lucide-react';

interface TableProps {
  data: Recommendation[];
  regime: string | null;
}

function RegimeBanner({ regime }: { regime: string | null }) {
  const strategyDesc = "Strategy 1.3 — Regime-Aware Ranking. RSI Pullback+Recovery, ADX≥20, MACD Confirmed. Updated nightly.";

  if (regime === 'bull') {
    return (
      <div className="mb-6 rounded-lg border border-green-300 bg-green-50 px-4 py-3 text-sm font-medium text-green-800">
        <span className="mr-2 font-bold">Bull Market Regime Active</span>
        &mdash; {strategyDesc}
      </div>
    );
  }

  if (regime === 'bear') {
    return (
      <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
        <span className="mr-2 font-bold">Bear Market Regime Active</span>
        &mdash; {strategyDesc}
      </div>
    );
  }

  if (regime === 'sideways') {
    return (
      <div className="mb-6 rounded-lg border border-blue-300 bg-blue-50 px-4 py-3 text-sm font-medium text-blue-800">
        <span className="mr-2 font-bold">Sideways Market Regime Active</span>
        &mdash; {strategyDesc}
      </div>
    );
  }

  return (
    <div className="mb-6 rounded-lg border border-gray-300 bg-gray-50 px-4 py-3 text-sm font-medium text-gray-600">
      Loading market regime...
    </div>
  );
}

function winRateColor(val: number): string {
  if (val >= 40) return 'text-green-600 font-semibold';
  if (val >= 30) return 'text-yellow-600 font-semibold';
  return 'text-red-600 font-semibold';
}

function expectancyColor(val: number): string {
  if (val > 0) return 'text-green-600 font-semibold';
  return 'text-red-600 font-semibold';
}

function tradesBadge(val: number): React.ReactNode {
  if (val > 20) {
    return <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">High ({val})</span>;
  }
  if (val >= 10) {
    return <span className="inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">Medium ({val})</span>;
  }
  return <span className="inline-flex items-center rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800">Low ({val})</span>;
}

function formatDate(val: string | null | undefined): string {
  if (!val) return '-';
  const parts = val.split('-');
  if (parts.length !== 3) return val;
  const year = parseInt(parts[0], 10);
  const month = parseInt(parts[1], 10) - 1;
  const day = parseInt(parts[2], 10);
  const date = new Date(year, month, day);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function tierBadge(val: string | null | undefined, isFallback?: boolean | null): React.ReactNode {
  if (!val) return '-';
  let badgeClass = 'bg-gray-100 text-gray-800 border border-gray-200';
  if (val === 'Strong Buy') badgeClass = 'bg-green-100 text-green-800 border border-green-200 font-semibold';
  else if (val === 'Buy') badgeClass = 'bg-blue-100 text-blue-800 border border-blue-200 font-semibold';
  else if (val === 'Watch') {
    if (isFallback) {
      badgeClass = 'bg-purple-50 text-purple-700 border border-purple-200 font-medium';
    } else {
      badgeClass = 'bg-gray-100 text-gray-600 border border-gray-200';
    }
  }
  else if (val === 'Speculative') badgeClass = 'border border-red-200 text-red-600 bg-red-50/20';
  
  return (
    <div className="flex items-center gap-1.5">
      <span className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ${badgeClass}`}>
        {val}
      </span>
      {isFallback && (
        <span 
          className="inline-flex items-center rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-purple-800 cursor-help"
          title="Extended Bull Fallback signal (RSI pullback threshold relaxed to 57)"
        >
          Fallback
        </span>
      )}
    </div>
  );
}

const adxBadge = (adx: number | null) => {
  if (adx === null) return <span className="text-gray-400">—</span>;
  if (adx >= 40) return <span className="bg-green-100 text-green-800 px-2 py-0.5 rounded text-xs font-medium border border-green-200">ADX {adx.toFixed(0)} Power</span>;
  if (adx >= 25) return <span className="bg-blue-100 text-blue-800 px-2 py-0.5 rounded text-xs font-medium border border-blue-200">ADX {adx.toFixed(0)} Strong</span>;
  return <span className="bg-gray-100 text-gray-700 px-2 py-0.5 rounded text-xs font-medium border border-gray-200">ADX {adx.toFixed(0)}</span>;
};

function compositeScoreBar(val: number | null | undefined, macdHist: number | null): React.ReactNode {
  if (typeof val !== 'number') return '-';
  let barColor = 'bg-red-500';
  if (val >= 70) barColor = 'bg-green-500';
  else if (val >= 55) barColor = 'bg-yellow-500';
  else if (val >= 40) barColor = 'bg-gray-400';
  
  const macdArrow = (histogram: number | null) => {
    if (histogram === null) return null;
    if (histogram > 0) return <span className="text-green-600 font-bold ml-1" title={`MACD Hist: ${histogram.toFixed(4)}`}>↑</span>;
    return <span className="text-red-500 font-bold ml-1" title={`MACD Hist: ${histogram.toFixed(4)}`}>↓</span>;
  };

  return (
    <div className="flex flex-col gap-1 w-24">
      <div className="flex items-center">
        <span className="font-bold text-gray-900">{val.toFixed(1)}</span>
        {macdArrow(macdHist)}
      </div>
      <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
        <div className={`${barColor} h-1.5 rounded-full`} style={{ width: `${val}%` }} />
      </div>
    </div>
  );
}

export default function RecommendationsTable({ data, regime }: TableProps) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'composite_score', desc: true }
  ]);
  const [globalFilter, setGlobalFilter] = useState('');

  const columns = React.useMemo<ColumnDef<Recommendation>[]>(
    () => [
      {
        accessorKey: 'scan_date',
        header: 'Signal Date',
        cell: (info) => formatDate(info.getValue() as string),
      },
      {
        accessorKey: 'ticker',
        header: 'Ticker',
        cell: (info) => <span className="font-mono font-bold text-gray-800">{info.getValue() as string}</span>,
      },
      {
        accessorKey: 'company_name',
        header: 'Company',
        cell: (info) => info.getValue() || info.row.original.ticker,
      },
      {
        accessorKey: 'tier_label',
        header: 'Tier',
        cell: (info) => tierBadge(info.getValue() as string, info.row.original.is_fallback),
      },
      {
        accessorKey: 'adx_value',
        header: 'ADX(14)',
        cell: (info) => adxBadge(info.getValue() as number | null),
      },
      {
        accessorKey: 'composite_score',
        header: 'Composite Score',
        cell: (info) => compositeScoreBar(info.getValue() as number, info.row.original.macd_histogram),
      },
      {
        accessorKey: 'current_rsi',
        header: 'RSI(14)',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          if (typeof val !== 'number') return '-';
          const rsiMin = info.row.original.rsi_min_10d;
          return (
            <span 
              className="underline decoration-dotted cursor-help text-gray-800 font-medium"
              title={`RSI dipped to ${rsiMin !== null && rsiMin !== undefined ? rsiMin.toFixed(1) : 'N/A'} in last 10 days, now at ${val.toFixed(1)} — confirmed pullback recovery`}
            >
              {val.toFixed(1)}
            </span>
          );
        }
      },
      {
        accessorKey: 'industry',
        header: 'Industry',
        cell: (info) => info.getValue() || '-',
      },
      {
        accessorKey: 'past_win_rate',
        header: 'Win Rate',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          if (typeof val !== 'number') return '-';
          return <span className={winRateColor(val)}>{val.toFixed(1)}%</span>;
        },
      },
      {
        accessorKey: 'expectancy_pct',
        header: 'Expectancy',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          if (typeof val !== 'number') return '-';
          return (
            <span className={expectancyColor(val)}>
              {val >= 0 ? '+' : ''}{val.toFixed(2)}%
            </span>
          );
        },
      },
      {
        accessorKey: 'historical_signals',
        header: 'Total Trades',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          if (typeof val !== 'number') return '-';
          return tradesBadge(val);
        },
      },
      {
        accessorKey: 'score',
        header: 'Raw Score',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          return typeof val === 'number' ? <span className="font-mono text-gray-500">{val.toFixed(4)}</span> : '-';
        },
      },
      {
        accessorKey: 'entry_price',
        header: 'Entry Price',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          return typeof val === 'number' ? `$${val.toFixed(2)}` : '-';
        },
      },
      {
        accessorKey: 'exit_price',
        header: 'Exit Price',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          return typeof val === 'number' ? `$${val.toFixed(2)}` : '-';
        },
      },
      {
        accessorKey: 'stop_loss',
        header: 'Stop Loss',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          return typeof val === 'number' ? `$${val.toFixed(2)}` : '-';
        },
      },
      {
        accessorKey: 'upside_pct',
        header: 'Upside',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          return typeof val === 'number' ? <span className="text-green-600 font-bold">+{val.toFixed(1)}%</span> : '-';
        },
      },
      {
        accessorKey: 'risk_reward',
        header: 'Risk/Reward',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          return typeof val === 'number' ? `${val.toFixed(1)}x` : '-';
        },
      },
      {
        accessorKey: 'median_holding_days',
        header: 'Holding Time',
        cell: (info) => {
          const val = info.getValue() as number | null | undefined;
          return typeof val === 'number' ? `${val} days` : '-';
        },
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
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  return (
    <div className="w-full">
      {/* Regime Banner */}
      <RegimeBanner regime={regime} />

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
                  return (
                    <th
                      key={header.id}
                      onClick={header.column.getToggleSortingHandler()}
                      className="px-6 py-3 cursor-pointer select-none hover:bg-gray-100 transition-colors"
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
            {table.getRowModel().rows.length > 0 ? (
              table.getRowModel().rows.map((row) => (
                <tr key={row.id} className="hover:bg-gray-50 transition-colors">
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-6 py-4 whitespace-nowrap text-gray-900">
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-6 py-10 text-center text-gray-500 font-medium"
                >
                  {regime === 'bear'
                    ? 'Market conditions unfavorable. No recommendations during bear market regime.'
                    : 'No active stock recommendations found.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      
      {/* Bottom Counts */}
      <div className="mt-4 text-xs text-gray-500 flex justify-between px-1">
        <span>Showing {table.getRowModel().rows.length} of {data.length} recommendations</span>
        <span>Click column headers to sort</span>
      </div>
    </div>
  );
}
