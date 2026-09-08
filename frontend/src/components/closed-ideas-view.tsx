"use client";

import React from 'react';
import { Recommendation } from '../types/database';
import { CheckCircle2, AlertOctagon, Ban, XCircle, Clock } from 'lucide-react';

interface ClosedIdeasViewProps {
  closedIdeas: Recommendation[];
}

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr.includes('T') ? dateStr : `${dateStr}T00:00:00`);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return dateStr;
  }
}

function getOutcomeBadge(outcome?: string | null, reason?: string | null) {
  const o = outcome?.toLowerCase() || '';

  if (o === 'hit_t3' || o.includes('t3')) {
    return {
      label: 'Target 3 Reached',
      color: 'bg-purple-950/60 text-purple-300 border-purple-800/80',
      icon: CheckCircle2,
    };
  }
  if (o === 'hit_t2' || o.includes('t2')) {
    return {
      label: 'Target 2 Reached',
      color: 'bg-emerald-950/60 text-emerald-300 border-emerald-800/80',
      icon: CheckCircle2,
    };
  }
  if (o === 'hit_t1' || o.includes('t1')) {
    return {
      label: 'Target 1 Reached',
      color: 'bg-blue-950/60 text-blue-300 border-blue-800/80',
      icon: CheckCircle2,
    };
  }
  if (o === 'stopped' || o.includes('stop')) {
    return {
      label: 'Stop Loss',
      color: 'bg-rose-950/60 text-rose-300 border-rose-800/80',
      icon: AlertOctagon,
    };
  }
  if (o === 'manually_removed') {
    return {
      label: reason || 'Manually Removed',
      color: 'bg-slate-800 text-slate-300 border-slate-700',
      icon: Ban,
    };
  }
  if (o === 'invalidated') {
    return {
      label: reason || 'Idea Invalidated',
      color: 'bg-amber-950/60 text-amber-300 border-amber-800/80',
      icon: XCircle,
    };
  }

  return {
    label: reason || 'Closed',
    color: 'bg-slate-800 text-slate-300 border-slate-700',
    icon: CheckCircle2,
  };
}

export default function ClosedIdeasView({ closedIdeas }: ClosedIdeasViewProps) {
  if (!closedIdeas || closedIdeas.length === 0) {
    return (
      <div className="py-16 px-4 text-center">
        <div className="w-12 h-12 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center mx-auto mb-3 text-slate-500">
          <Clock className="w-6 h-6" />
        </div>
        <h3 className="text-base font-bold text-slate-200">No Closed Stock Ideas Yet</h3>
        <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
          Completed recommendation outcomes, stopped ideas, and manually removed setups will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between px-1">
        <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          {closedIdeas.length} Historical {closedIdeas.length === 1 ? 'Record' : 'Records'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {closedIdeas.map((rec, index) => {
          const ticker = rec.ticker?.toUpperCase() || 'UNKNOWN';
          const company = rec.company_name || '';
          const t1 = rec.target_1 ? Number(rec.target_1).toFixed(2) : null;
          const t2 = rec.target_2 ? Number(rec.target_2).toFixed(2) : null;
          const t3 = rec.target_3 ? Number(rec.target_3).toFixed(2) : null;
          const stop = rec.stop_loss ? Number(rec.stop_loss).toFixed(2) : '—';
          const closedDate = formatDate(rec.exit_date || rec.outcome_date || rec.scan_date);
          const daysActive = rec.outcome_holding_days !== undefined && rec.outcome_holding_days !== null
            ? `${rec.outcome_holding_days}d active`
            : '—';

          const returnPct = rec.outcome_return_pct !== undefined && rec.outcome_return_pct !== null
            ? Number(rec.outcome_return_pct)
            : null;

          const badge = getOutcomeBadge(rec.outcome || rec.status, rec.sell_signal_reason);
          const BadgeIcon = badge.icon;

          // Format targets
          const targetParts: string[] = [];
          if (t1) targetParts.push(`T1 $${t1}`);
          if (t2) targetParts.push(`T2 $${t2}`);
          if (t3) targetParts.push(`T3 $${t3}`);
          const targetsDisplay = targetParts.length > 0 ? targetParts.join(' · ') : 'Trailing Stop';

          const key = rec.id ? `closed_${rec.id}` : `${ticker}_${rec.scan_date}_${index}`;

          return (
            <div
              key={key}
              className="bg-[#121826] border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-md flex flex-col justify-between"
            >
              <div>
                {/* Header row: Ticker + Company & Return */}
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <h3 className="font-extrabold text-xl text-white tracking-tight leading-tight">
                      {ticker}
                    </h3>
                    {company && (
                      <p className="text-xs text-slate-400 font-medium truncate mt-0.5 leading-normal" title={company}>
                        {company}
                      </p>
                    )}
                  </div>

                  {returnPct !== null ? (
                    <div className="text-right flex-shrink-0">
                      <span
                        className={`font-mono text-base font-bold tracking-tight ${
                          returnPct > 0
                            ? 'text-emerald-400'
                            : returnPct < 0
                            ? 'text-rose-400'
                            : 'text-slate-300'
                        }`}
                      >
                        {returnPct > 0 ? `+${returnPct.toFixed(1)}%` : `${returnPct.toFixed(1)}%`}
                      </span>
                    </div>
                  ) : (
                    <div className="text-right flex-shrink-0">
                      <span className="font-mono text-xs text-slate-500 font-medium">Outcome logged</span>
                    </div>
                  )}
                </div>

                {/* Targets & Stop Info */}
                <div className="mt-3.5 pt-3 border-t border-slate-800/60 flex flex-col gap-1.5 text-xs">
                  <div className="flex items-baseline justify-between text-slate-300">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Targets</span>
                    <span className="font-mono font-medium text-slate-300">{targetsDisplay}</span>
                  </div>

                  <div className="flex items-baseline justify-between text-slate-400 text-[11px]">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Stop & Duration</span>
                    <span className="font-mono text-slate-300">
                      Stop ${stop} <span className="text-slate-600">·</span> <span className="text-slate-400">{daysActive}</span>
                    </span>
                  </div>

                  <div className="flex items-baseline justify-between text-slate-400 text-[11px]">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Closed Date</span>
                    <span className="font-mono text-slate-400">{closedDate}</span>
                  </div>
                </div>
              </div>

              {/* Outcome Badge */}
              <div className="mt-4 pt-3 border-t border-slate-800/60">
                <div
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border ${badge.color} max-w-full truncate`}
                  title={badge.label}
                >
                  <BadgeIcon className="w-3.5 h-3.5 flex-shrink-0" />
                  <span className="truncate">{badge.label}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
