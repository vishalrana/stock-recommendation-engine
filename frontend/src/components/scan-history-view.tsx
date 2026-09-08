"use client";

import React, { useState } from 'react';
import { ScanHistoryEntry } from '../types/database';
import { Calendar, ChevronDown, ChevronUp, Sparkles, Filter, CheckCircle, ShieldAlert } from 'lucide-react';

interface ScanHistoryViewProps {
  scanHistory: ScanHistoryEntry[];
}

function formatScanDate(dateStr?: string | null): string {
  if (!dateStr) return '—';
  try {
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const year = parseInt(parts[0], 10);
      const month = parseInt(parts[1], 10) - 1;
      const day = parseInt(parts[2], 10);
      const d = new Date(year, month, day);
      return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' });
    }
    return dateStr;
  } catch {
    return dateStr;
  }
}

export default function ScanHistoryView({ scanHistory }: ScanHistoryViewProps) {
  const [expandedDates, setExpandedDates] = useState<Record<string, boolean>>({});

  const toggleDetails = (dateStr: string) => {
    setExpandedDates((prev) => ({
      ...prev,
      [dateStr]: !prev[dateStr],
    }));
  };

  if (!scanHistory || scanHistory.length === 0) {
    return (
      <div className="py-16 px-4 text-center">
        <div className="w-12 h-12 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center mx-auto mb-3 text-slate-500">
          <Calendar className="w-6 h-6" />
        </div>
        <h3 className="text-base font-bold text-slate-200">No Scan Records Available</h3>
        <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
          Nightly systematic market scans will log their historical run results here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between px-1">
        <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Market Scans ({scanHistory.length})
        </span>
      </div>

      <div className="space-y-3">
        {scanHistory.map((scan) => {
          const isExpanded = !!expandedDates[scan.scan_date];
          const hasNewIdeas = scan.newIdeas && scan.newIdeas.length > 0;
          const newIdeasCount = hasNewIdeas ? scan.newIdeas.length : scan.signals_qualified;
          const filteredCount = scan.filteredSetups ? scan.filteredSetups.length : 0;

          return (
            <div
              key={scan.scan_date}
              className="bg-[#121826] border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-md transition-all duration-200"
            >
              {/* Card Header: Date & Scan Title */}
              <div className="flex items-start justify-between gap-2 pb-3 border-b border-slate-800/60">
                <div>
                  <h3 className="font-extrabold text-lg text-white tracking-tight">
                    {formatScanDate(scan.scan_date)}
                  </h3>
                  <p className="text-xs text-slate-400 font-medium mt-0.5">
                    Market Scan · <span className="text-slate-300 font-semibold">{scan.tickers_scanned.toLocaleString()} Tickers</span>
                  </p>
                </div>

                <span className="px-2.5 py-1 rounded-full text-[11px] font-bold border border-slate-700 bg-slate-800/80 text-slate-300">
                  {scan.regime}
                </span>
              </div>

              {/* Ideas Summary */}
              <div className="my-3.5">
                <div className="flex items-center gap-2 mb-2">
                  <Sparkles className={`w-4 h-4 ${hasNewIdeas ? 'text-emerald-400' : 'text-slate-500'}`} />
                  <span className="text-sm font-bold text-slate-200">
                    {hasNewIdeas
                      ? `${newIdeasCount} new stock ${newIdeasCount === 1 ? 'idea' : 'ideas'}`
                      : 'No new stock ideas qualified'}
                  </span>
                </div>

                {hasNewIdeas ? (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {scan.newIdeas.map((idea) => (
                      <div
                        key={idea.ticker}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-950/40 border border-emerald-800/60 text-emerald-300 text-xs font-semibold"
                      >
                        <span className="font-bold">{idea.ticker}</span>
                        <span className="text-emerald-500 font-normal">·</span>
                        <span className="text-[11px] text-emerald-400/90 font-medium">{idea.strategy}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-400">
                    Quality and risk filters preserved capital. Cash is a valid position.
                  </p>
                )}
              </div>

              {/* Scan Details Toggle Button */}
              {filteredCount > 0 && (
                <div className="pt-2 border-t border-slate-800/40 flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => toggleDetails(scan.scan_date)}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-slate-400 hover:text-slate-200 transition-colors py-1 cursor-pointer"
                  >
                    <Filter className="w-3.5 h-3.5" />
                    <span>{isExpanded ? 'Hide Scan Details' : `View Scan Details (${filteredCount} screened)`}</span>
                    {isExpanded ? <ChevronUp className="w-3.5 h-3.5 ml-0.5" /> : <ChevronDown className="w-3.5 h-3.5 ml-0.5" />}
                  </button>
                </div>
              )}

              {/* Expandable Screened / Disqualified Setups */}
              {isExpanded && filteredCount > 0 && (
                <div className="mt-3 pt-3 border-t border-slate-800 space-y-2 animate-in fade-in duration-150">
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block mb-2">
                    Disqualified Setup Audit ({filteredCount})
                  </span>
                  <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
                    {scan.filteredSetups.map((setup, sIdx) => (
                      <div
                        key={`${setup.ticker}_${sIdx}`}
                        className="p-2.5 rounded-xl bg-slate-900/60 border border-slate-800/60 flex flex-col sm:flex-row sm:items-center justify-between text-xs gap-1"
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-slate-200 font-mono">{setup.ticker}</span>
                          {setup.strategy && (
                            <span className="text-[10px] text-slate-500 font-medium truncate max-w-[120px]">
                              {setup.strategy}
                            </span>
                          )}
                        </div>
                        <span className="text-[11px] text-amber-400/90 font-medium">
                          {setup.reason}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
