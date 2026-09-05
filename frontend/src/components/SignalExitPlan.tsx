"use client";

import React from 'react';
import { Recommendation } from '../types/database';
import { parseScaleOut } from '../lib/position-utils';

interface SignalExitPlanProps {
  recommendation: Recommendation;
}

export default function SignalExitPlan({ recommendation }: SignalExitPlanProps) {
  const rec = recommendation;
  const entryPrice = rec.entry_price ? Number(rec.entry_price) : 0;
  const weightsStr = rec.scale_out_weights || '50/30/20';
  const [w1, w2, w3] = parseScaleOut(weightsStr);

  const isT2Removed = rec.target_2 === null || rec.target_2 === undefined;
  const isT3Removed = rec.target_3 === null || rec.target_3 === undefined;

  const t1PctStr = rec.target_1_pct 
    ? `+${Number(rec.target_1_pct).toFixed(1)}%` 
    : (entryPrice > 0 && rec.target_1 ? `+${(((Number(rec.target_1) - entryPrice) / entryPrice) * 100).toFixed(1)}%` : '+12.0%');

  const t2PctStr = rec.target_2_pct 
    ? `+${Number(rec.target_2_pct).toFixed(1)}%` 
    : (entryPrice > 0 && rec.target_2 ? `+${(((Number(rec.target_2) - entryPrice) / entryPrice) * 100).toFixed(1)}%` : '+22.0%');

  const t3PctStr = rec.target_3_pct 
    ? `+${Number(rec.target_3_pct).toFixed(1)}%` 
    : (entryPrice > 0 && rec.target_3 ? `+${(((Number(rec.target_3) - entryPrice) / entryPrice) * 100).toFixed(1)}%` : '+35.0%');

  // Compute remaining position percentages
  const remainingAfterT1 = Math.max(0, 100 - w1);
  const remainingAfterT2 = isT2Removed ? remainingAfterT1 : Math.max(0, remainingAfterT1 - w2);
  const runnerPct = isT3Removed ? remainingAfterT2 : Math.max(0, 100 - w1 - w2 - w3);

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-gray-900 uppercase tracking-wider">🎯 Scale-Out Exit Plan</span>
          <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded-md bg-slate-100 text-slate-700">
            Scale: {weightsStr}
          </span>
        </div>
        <span className="text-xs font-semibold text-slate-500">
          Manual Broker Execution
        </span>
      </div>

      {/* Visual Stacked Percentage Bar */}
      <div className="space-y-1.5">
        <div className="w-full h-3 bg-gray-100 rounded-full overflow-hidden flex">
          {w1 > 0 && (
            <div
              style={{ width: `${w1}%` }}
              className="h-full bg-emerald-500 transition-all duration-300"
              title={`T1: ${w1}%`}
            />
          )}
          {!isT2Removed && w2 > 0 && (
            <div
              style={{ width: `${w2}%` }}
              className="h-full bg-blue-500 transition-all duration-300"
              title={`T2: ${w2}%`}
            />
          )}
          {!isT3Removed && w3 > 0 && (
            <div
              style={{ width: `${w3}%` }}
              className="h-full bg-purple-500 transition-all duration-300"
              title={`T3: ${w3}%`}
            />
          )}
          {runnerPct > 0 && (
            <div
              style={{ width: `${runnerPct}%` }}
              className="h-full bg-slate-400 transition-all duration-300"
              title={`Runner: ${runnerPct}%`}
            />
          )}
        </div>
        <div className="flex justify-between text-[9px] text-gray-500 font-medium">
          <span className="text-emerald-700 font-bold">T1: {w1}%</span>
          {!isT2Removed ? (
            <span className="text-blue-700 font-bold">T2: {w2}%</span>
          ) : (
            <span className="text-gray-400 line-through">T2: N/A</span>
          )}
          {!isT3Removed && w3 > 0 ? (
            <span className="text-purple-700 font-bold">T3: {w3}%</span>
          ) : runnerPct > 0 ? (
            <span className="text-slate-600 font-bold">Runner: {runnerPct}%</span>
          ) : (
            <span className="text-gray-400 line-through">T3: N/A</span>
          )}
        </div>
      </div>

      {/* Target Rows Breakdown */}
      <div className="space-y-3 pt-1">
        {/* T1 */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold text-gray-800 flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block"></span>
              T1 ({t1PctStr} {rec.target_1 ? `@ $${Number(rec.target_1).toFixed(2)}` : ''})
            </span>
            <span className="font-mono font-bold text-emerald-700">
              Sell {w1}%
            </span>
          </div>
          <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
            <div className="bg-emerald-500 h-full rounded-full" style={{ width: `${w1}%` }} />
          </div>
          <p className="text-[10px] text-emerald-700 font-medium pl-3.5">
            → On T1 hit: Ratchet stop loss to Breakeven (${entryPrice > 0 ? entryPrice.toFixed(2) : '-'})
          </p>
        </div>

        {/* T2 */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className={`font-bold flex items-center gap-1.5 ${isT2Removed ? 'text-gray-400' : 'text-gray-800'}`}>
              <span className={`w-2 h-2 rounded-full inline-block ${isT2Removed ? 'bg-gray-300' : 'bg-blue-500'}`}></span>
              T2 {isT2Removed ? '—' : `(${t2PctStr} ${rec.target_2 ? `@ $${Number(rec.target_2).toFixed(2)}` : ''})`}
            </span>
            <span className={`font-mono font-bold ${isT2Removed ? 'text-gray-400' : 'text-blue-700'}`}>
              {isT2Removed ? '—' : `Sell ${w2}%`}
            </span>
          </div>
          <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${isT2Removed ? 'bg-gray-200' : 'bg-blue-500'}`}
              style={{ width: `${isT2Removed ? 0 : w2}%` }}
            />
          </div>
          {!isT2Removed && rec.target_1 && (
            <p className="text-[10px] text-blue-700 font-medium pl-3.5">
              → On T2 hit: Ratchet stop loss to T1 (${Number(rec.target_1).toFixed(2)})
            </p>
          )}
          {isT2Removed && (
            <p className="text-[10px] text-gray-400 pl-3.5 italic">
              Removed by reach probability filter (below statistical threshold)
            </p>
          )}
        </div>

        {/* T3 */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className={`font-bold flex items-center gap-1.5 ${isT3Removed ? 'text-gray-400' : 'text-gray-800'}`}>
              <span className={`w-2 h-2 rounded-full inline-block ${isT3Removed ? 'bg-gray-300' : 'bg-purple-500'}`}></span>
              T3 {isT3Removed ? '—' : `(${t3PctStr} ${rec.target_3 ? `@ $${Number(rec.target_3).toFixed(2)}` : ''})`}
            </span>
            <span className={`font-mono font-bold ${isT3Removed ? 'text-gray-400' : 'text-purple-700'}`}>
              {isT3Removed ? '—' : `Sell ${w3}%`}
            </span>
          </div>
          <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${isT3Removed ? 'bg-gray-200' : 'bg-purple-500'}`}
              style={{ width: `${isT3Removed ? 0 : w3}%` }}
            />
          </div>
          {isT3Removed && (
            <p className="text-[10px] text-gray-400 pl-3.5 italic">
              Removed by reach probability filter (below statistical threshold)
            </p>
          )}
        </div>
      </div>

      {/* Position Flow Management Footer */}
      <div className="bg-slate-50 border border-slate-100 rounded-lg p-2.5 space-y-1 text-[11px]">
        <div className="flex justify-between text-gray-600">
          <span>Remaining position after T1:</span>
          <span className="font-bold font-mono text-gray-900">{remainingAfterT1}%</span>
        </div>
        {!isT2Removed && (
          <div className="flex justify-between text-gray-600">
            <span>Remaining position after T2:</span>
            <span className="font-bold font-mono text-gray-900">{remainingAfterT2}%</span>
          </div>
        )}
        {runnerPct > 0 && (
          <div className="flex justify-between text-slate-700 font-semibold pt-0.5 border-t border-slate-200/60">
            <span>Trailing Stop Runner Lot:</span>
            <span className="font-bold font-mono text-slate-900">{runnerPct}%</span>
          </div>
        )}
      </div>
    </div>
  );
}
