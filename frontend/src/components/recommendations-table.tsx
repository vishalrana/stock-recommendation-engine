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
  if (regime === 'bull') {
    return (
      <div className="mb-6 rounded-lg border border-green-300 bg-green-50 px-4 py-3 text-sm font-medium text-green-800">
        <span className="mr-2 font-bold">Bull Market Regime Active</span>
        &mdash; Strategy 1.2 Rev B Composite Scoring Active (Focusing on Technical Momentum &amp; Trend continuation).
      </div>
    );
  }

  if (regime === 'bear') {
    return (
      <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
        <span className="mr-2 font-bold">Bear Market Regime Active</span>
        &mdash; Composite Scoring Active (Boosting low-beta/defensive sectors).
      </div>
    );
  }

  if (regime === 'sideways') {
    return (
      <div className="mb-6 rounded-lg border border-blue-300 bg-blue-50 px-4 py-3 text-sm font-medium text-blue-800">
        <span className="mr-2 font-bold">Sideways Market Regime Active</span>
        &mdash; Composite Scoring Active (Boosting mean-reversion candidates near RSI 50).
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
  if (val > 40) return 'text-green-600 font-semibold';
  if (val >= 25) return 'text-yellow-600 font-semibold';
  return 'text-red-600 font-semibold';
}

function expectancyColor(val: number): string {
  if (val > 2) return 'text-green-600 font-semibold';
  if (val >= 0) return 'text-yellow-600 font-semibold';
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

function tierBadge(val: string | null | undefined): React.ReactNode {
  if (!val) return '-';
  let badgeClass = 'bg-gray-100 text-gray-800 border border-gray-200';
  if (val === 'Strong Buy') badgeClass = 'bg-green-100 text-green-800 border border-green-200 font-semibold';
  else if (val === 'Buy') badgeClass = 'bg-blue-100 text-blue-800 border border-blue-200 font-semibold';
  else if (val === 'Watch') badgeClass = 'bg-gray-100 text-gray-600 border border-gray-200';
  else if (val === 'Speculative') badgeClass = 'border border-red-200 text-red-600 bg-red-50/20';
  
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ${badgeClass}`}>
      {val}
    </span>
  );
}

function compositeScoreBar(val: number | null | undefined): React.ReactNode {
  if (typeof val !== 'number') return '-';
  let barColor = 'bg-red-500';
  if (val >= 70) barColor = 'bg-green-500';
  else if (val >= 55) barColor = 'bg-yellow-500';
  else if (val >= 40) barColor = 'bg-gray-400';
  
  return (
    <div className="flex flex-col gap-1 w-24">
      <span className="font-bold text-gray-900">{val.toFixed(1)}</span>
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
        cell: (info) => tierBadge(info.getValue() as string),
      },
      {
        accessorKey: 'composite_score',
        header: 'Composite Score',
        cell: (info) => compositeScoreBar(info.getValue() as number),
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
