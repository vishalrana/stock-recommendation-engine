"use client";

import React from 'react';
import { Recommendation } from '../types/database';
import { getDollarExits } from '../lib/position-utils';

interface SignalExitPlanProps {
  recommendation: Recommendation;
  latestPortfolioValue?: number;
}

export default function SignalExitPlan({ recommendation, latestPortfolioValue = 10000 }: SignalExitPlanProps) {
  const rec = recommendation;
  const entryPrice = rec.entry_price ? Number(rec.entry_price) : 0;
  const allocDollars = rec.allocated_dollars 
    ? Number(rec.allocated_dollars) 
    : (rec.composite_score ? Math.min(500, (5.0 / 100) * latestPortfolioValue) : 500);

  const breakdown = getDollarExits(allocDollars, rec.scale_out_weights, {
    target_1: rec.target_1,
    target_2: rec.target_2,
    target_3: rec.target_3,
  });

  const t1PctStr = rec.target_1_pct 
    ? `+${Number(rec.target_1_pct).toFixed(1)}%` 
    : (entryPrice > 0 && rec.target_1 ? `+${(((Number(rec.target_1) - entryPrice) / entryPrice) * 100).toFixed(1)}%` : '+12.0%');

  const t2PctStr = rec.target_2_pct 
    ? `+${Number(rec.target_2_pct).toFixed(1)}%` 
    : (entryPrice > 0 && rec.target_2 ? `+${(((Number(rec.target_2) - entryPrice) / entryPrice) * 100).toFixed(1)}%` : '+22.0%');

  const t3PctStr = rec.target_3_pct 
    ? `+${Number(rec.target_3_pct).toFixed(1)}%` 
    : (entryPrice > 0 && rec.target_3 ? `+${(((Number(rec.target_3) - entryPrice) / entryPrice) * 100).toFixed(1)}%` : '+35.0%');

  const [w1, w2, w3] = [breakdown.t1.pct, breakdown.t2.pct, breakdown.t3.pct];

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-gray-900 uppercase tracking-wider">🎯 Exit Plan</span>
          <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded-md bg-slate-100 text-slate-700">
            Scale: {breakdown.scaleOutWeights}
          </span>
        </div>
        <span className="text-xs font-bold text-emerald-700 font-mono">
          ${breakdown.totalAllocated.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Allocated
        </span>
      </div>

      {/* Visual Stacked Bar */}
      <div className="space-y-1.5">
        <div className="w-full h-3 bg-gray-100 rounded-full overflow-hidden flex">
          {w1 > 0 && (
            <div
              style={{ width: `${w1}%` }}
              className="h-full bg-emerald-500 transition-all duration-300"
              title={`T1: ${w1}% ($${breakdown.t1.dollars.toFixed(2)})`}
            />
          )}
          {!breakdown.isT2Removed && w2 > 0 && (
            <div
              style={{ width: `${w2}%` }}
              className="h-full bg-blue-500 transition-all duration-300"
              title={`T2: ${w2}% ($${breakdown.t2.dollars.toFixed(2)})`}
            />
          )}
          {!breakdown.isT3Removed && w3 > 0 && (
            <div
              style={{ width: `${w3}%` }}
              className="h-full bg-purple-500 transition-all duration-300"
              title={`T3: ${w3}% ($${breakdown.t3.dollars.toFixed(2)})`}
            />
          )}
          {breakdown.runner.dollars > 0 && (
            <div
              style={{ width: `${breakdown.runner.pct}%` }}
              className="h-full bg-slate-400 transition-all duration-300"
              title={`Runner: ${breakdown.runner.pct}% ($${breakdown.runner.dollars.toFixed(2)})`}
            />
          )}
        </div>
        <div className="flex justify-between text-[9px] text-gray-400 font-medium">
          <span className="text-emerald-700 font-bold">T1: {w1}%</span>
          {!breakdown.isT2Removed ? (
            <span className="text-blue-700 font-bold">T2: {w2}%</span>
          ) : (
            <span className="text-gray-400 line-through">T2: Removed</span>
          )}
          {!breakdown.isT3Removed && w3 > 0 ? (
            <span className="text-purple-700 font-bold">T3: {w3}%</span>
          ) : breakdown.runner.dollars > 0 ? (
            <span className="text-slate-600 font-bold">Runner: {breakdown.runner.pct}%</span>
          ) : (
            <span className="text-gray-400 line-through">T3: Removed</span>
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
            <span className="font-mono font-bold text-gray-900">
              ${breakdown.t1.dollars.toFixed(2)} <span className="text-gray-500 font-normal">({w1}%)</span>
            </span>
          </div>
          <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
            <div className="bg-emerald-500 h-full rounded-full" style={{ width: `${w1}%` }} />
          </div>
          <p className="text-[10px] text-emerald-700 font-medium pl-3.5">
            → Stop ratchets to breakeven (${entryPrice ? Number(entryPrice).toFixed(2) : '-'})
          </p>
        </div>

        {/* T2 */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className={`font-bold flex items-center gap-1.5 ${breakdown.isT2Removed ? 'text-gray-400' : 'text-gray-800'}`}>
              <span className={`w-2 h-2 rounded-full inline-block ${breakdown.isT2Removed ? 'bg-gray-300' : 'bg-blue-500'}`}></span>
              T2 {breakdown.isT2Removed ? '—' : `(${t2PctStr} ${rec.target_2 ? `@ $${Number(rec.target_2).toFixed(2)}` : ''})`}
            </span>
            <span className={`font-mono font-bold ${breakdown.isT2Removed ? 'text-gray-400' : 'text-gray-900'}`}>
              {breakdown.isT2Removed ? (
                <span>— <span className="text-[10px] font-normal text-gray-400">(0%)</span></span>
              ) : (
                <span>${breakdown.t2.dollars.toFixed(2)} <span className="text-gray-500 font-normal">({w2}%)</span></span>
              )}
            </span>
          </div>
          <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${breakdown.isT2Removed ? 'bg-gray-200' : 'bg-blue-500'}`}
              style={{ width: `${breakdown.isT2Removed ? 0 : w2}%` }}
            />
          </div>
          {breakdown.isT2Removed && (
            <p className="text-[10px] text-gray-400 pl-3.5 italic">
              Removed by reach probability filter (under min threshold)
            </p>
          )}
        </div>

        {/* T3 */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className={`font-bold flex items-center gap-1.5 ${breakdown.isT3Removed ? 'text-gray-400' : 'text-gray-800'}`}>
              <span className={`w-2 h-2 rounded-full inline-block ${breakdown.isT3Removed ? 'bg-gray-300' : 'bg-purple-500'}`}></span>
              T3 {breakdown.isT3Removed ? '—' : `(${t3PctStr} ${rec.target_3 ? `@ $${Number(rec.target_3).toFixed(2)}` : ''})`}
            </span>
            <span className={`font-mono font-bold ${breakdown.isT3Removed ? 'text-gray-400' : 'text-gray-900'}`}>
              {breakdown.isT3Removed ? (
                <span>— <span className="text-[10px] font-normal text-gray-400">(0%)</span></span>
              ) : (
                <span>${breakdown.t3.dollars.toFixed(2)} <span className="text-gray-500 font-normal">({w3}%)</span></span>
              )}
            </span>
          </div>
          <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${breakdown.isT3Removed ? 'bg-gray-200' : 'bg-purple-500'}`}
              style={{ width: `${breakdown.isT3Removed ? 0 : w3}%` }}
            />
          </div>
          {breakdown.isT3Removed && (
            <p className="text-[10px] text-gray-400 pl-3.5 italic">
              Removed by reach probability filter (under min threshold)
            </p>
          )}
        </div>
      </div>

      {/* Remaining Balances Footer */}
      <div className="bg-slate-50 border border-slate-100 rounded-lg p-2.5 space-y-1 text-[11px] font-mono">
        <div className="flex justify-between text-gray-600">
          <span>Remaining after T1:</span>
          <span className="font-bold text-gray-900">${breakdown.remainingAfterT1.toFixed(2)}</span>
        </div>
        {!breakdown.isT2Removed && (
          <div className="flex justify-between text-gray-600">
            <span>Remaining after T2:</span>
            <span className="font-bold text-gray-900">${breakdown.remainingAfterT2.toFixed(2)}</span>
          </div>
        )}
        {breakdown.runner.dollars > 0 && (
          <div className="flex justify-between text-slate-700 font-semibold pt-0.5 border-t border-slate-200/60">
            <span>Breakeven Runner Lot:</span>
            <span className="font-bold text-slate-900">${breakdown.runner.dollars.toFixed(2)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
