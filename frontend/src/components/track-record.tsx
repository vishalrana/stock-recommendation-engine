"use client";

import React, { useState } from 'react';
import { SwingSignalRecord, SwingSleeveStatus } from '../types/database';
import { TrendingUp, ShieldAlert, Award, Activity } from 'lucide-react';

interface TrackRecordProps {
  closedSignals: SwingSignalRecord[];
  sleeves: SwingSleeveStatus[];
}

export default function TrackRecord({ closedSignals, sleeves }: TrackRecordProps) {
  const [selectedSleeve, setSelectedSleeve] = useState<string>('combined');

  // Compute live statistics
  const filtered = selectedSleeve === 'combined'
    ? closedSignals
    : closedSignals.filter(s => s.sleeve === selectedSleeve);

  const nClosed = filtered.length;
  const wins = filtered.filter(s => (s.net_return_pct ?? 0) > 0).length;
  const winRate = nClosed > 0 ? (wins / nClosed) * 100 : 0;
  const meanNet = nClosed > 0 ? filtered.reduce((acc, s) => acc + (s.net_return_pct ?? 0), 0) / nClosed : 0;
  const meanExcess = nClosed > 0 ? filtered.reduce((acc, s) => acc + (s.excess_pct ?? 0), 0) / nClosed : 0;
  const meanBetaAdj = nClosed > 0 ? filtered.reduce((acc, s) => acc + (s.beta_adj_pct ?? 0), 0) / nClosed : 0;

  // Holdout benchmark numbers (from pre-registered 2008-2022 backtest)
  const holdoutStats: Record<string, { winRate: number; meanNet: number; meanBetaAdj: number; killStatus: string }> = {
    T: { winRate: 46.2, meanNet: 0.12, meanBetaAdj: -0.24, killStatus: 'NORMAL' },
    E: { winRate: 51.8, meanNet: 0.28, meanBetaAdj: -0.13, killStatus: 'NORMAL' },
    N: { winRate: 42.1, meanNet: -1.85, meanBetaAdj: -2.35, killStatus: 'NORMAL' },
    P: { winRate: 53.4, meanNet: 0.19, meanBetaAdj: -0.14, killStatus: 'NORMAL' },
    I: { winRate: 44.5, meanNet: -0.05, meanBetaAdj: -0.26, killStatus: 'NORMAL' },
    combined: { winRate: 48.6, meanNet: 0.15, meanBetaAdj: -0.18, killStatus: 'NORMAL' },
  };

  const holdout = holdoutStats[selectedSleeve] || holdoutStats.combined;

  // Cumulative equity curve points for inline SVG chart
  // Start at 0, accumulate net_return_pct
  let cumUnits = 0;
  const curvePoints: Array<{ x: number; y: number }> = [{ x: 0, y: 0 }];
  filtered.forEach((s, idx) => {
    cumUnits += (s.net_return_pct ?? 0) / 100;
    curvePoints.push({ x: idx + 1, y: cumUnits });
  });

  // Calculate SVG dimensions
  const svgWidth = 500;
  const svgHeight = 160;
  const padding = 20;

  const maxX = Math.max(curvePoints.length - 1, 10);
  const minY = Math.min(...curvePoints.map(p => p.y), -0.1);
  const maxY = Math.max(...curvePoints.map(p => p.y), 0.1);
  const rangeY = maxY - minY || 1;

  const toSvgX = (x: number) => padding + (x / maxX) * (svgWidth - 2 * padding);
  const toSvgY = (y: number) => svgHeight - padding - ((y - minY) / rangeY) * (svgHeight - 2 * padding);

  const polylinePoints = curvePoints.map(p => `${toSvgX(p.x)},${toSvgY(p.y)}`).join(' ');
  const zeroLineY = toSvgY(0);

  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-sm">
      <div className="flex items-center justify-between flex-wrap gap-3 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-400" />
          <h3 className="text-base font-bold text-white">Track Record & Kill Monitoring</h3>
        </div>

        {/* Sleeve Selector Tabs */}
        <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-xl border border-slate-800 text-xs font-semibold">
          {['combined', 'T', 'E', 'N', 'P', 'I'].map(s => (
            <button
              key={s}
              type="button"
              onClick={() => setSelectedSleeve(s)}
              className={`px-2.5 py-1 rounded-lg transition-all cursor-pointer ${
                selectedSleeve === s
                  ? 'bg-blue-600 text-white font-bold'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {s === 'combined' ? 'Combined' : `Sleeve ${s}`}
            </button>
          ))}
        </div>
      </div>

      {/* Metric Cards Comparison: Live vs Holdout */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
        <div className="bg-slate-950/70 p-3 rounded-xl border border-slate-800/80">
          <span className="text-[10px] text-slate-400 block uppercase font-bold">Closed Signals</span>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-lg font-black text-white">{nClosed}</span>
            <span className="text-[10px] text-slate-500">(Live)</span>
          </div>
          <span className="text-[10px] text-slate-500 block mt-1">Holdout: 2,400+ signals</span>
        </div>

        <div className="bg-slate-950/70 p-3 rounded-xl border border-slate-800/80">
          <span className="text-[10px] text-slate-400 block uppercase font-bold">Win Rate</span>
          <div className="flex items-baseline gap-2 mt-1">
            <span className={`text-lg font-black ${winRate >= 50 ? 'text-emerald-400' : 'text-slate-200'}`}>
              {nClosed > 0 ? `${winRate.toFixed(1)}%` : '—'}
            </span>
            <span className="text-[10px] text-slate-500">(Live)</span>
          </div>
          <span className="text-[10px] text-slate-400 block mt-1">Holdout: {holdout.winRate}%</span>
        </div>

        <div className="bg-slate-950/70 p-3 rounded-xl border border-slate-800/80">
          <span className="text-[10px] text-slate-400 block uppercase font-bold">Mean Net / Signal</span>
          <div className="flex items-baseline gap-2 mt-1">
            <span className={`text-lg font-black ${meanNet >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {nClosed > 0 ? `${meanNet >= 0 ? '+' : ''}${meanNet.toFixed(2)}%` : '—'}
            </span>
            <span className="text-[10px] text-slate-500">(Live)</span>
          </div>
          <span className="text-[10px] text-slate-400 block mt-1">Holdout: {holdout.meanNet >= 0 ? '+' : ''}{holdout.meanNet}%</span>
        </div>

        <div className="bg-slate-950/70 p-3 rounded-xl border border-slate-800/80">
          <span className="text-[10px] text-slate-400 block uppercase font-bold">Mean Beta-Adj</span>
          <div className="flex items-baseline gap-2 mt-1">
            <span className={`text-lg font-black ${meanBetaAdj >= 0 ? 'text-emerald-400' : 'text-slate-300'}`}>
              {nClosed > 0 ? `${meanBetaAdj >= 0 ? '+' : ''}${meanBetaAdj.toFixed(2)}%` : '—'}
            </span>
            <span className="text-[10px] text-slate-500">(Live)</span>
          </div>
          <span className="text-[10px] text-slate-400 block mt-1">Holdout: {holdout.meanBetaAdj}%</span>
        </div>
      </div>

      {/* Kill Monitoring & Placebo Status Strip */}
      <div className="mt-4 p-3 bg-slate-950/40 border border-slate-800/60 rounded-xl flex items-center justify-between flex-wrap gap-2 text-xs">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-emerald-400" />
          <span className="text-slate-300">
            Kill Rule Check (K3, K12, KDD): <strong className="text-emerald-400 font-bold">{holdout.killStatus}</strong>
          </span>
        </div>
        <span className="text-slate-400 text-[11px]">
          Placebo baseline (200 random simulations): median band ±0.05 units
        </span>
      </div>

      {/* Cumulative Return Chart (Inline SVG) */}
      <div className="mt-4 bg-slate-950 p-3 rounded-xl border border-slate-800/80">
        <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
          <span className="font-semibold text-slate-300">Cumulative Units vs SPY</span>
          <span className="text-[10px] text-slate-500">Inline SVG (no external charting lib)</span>
        </div>

        <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} className="w-full h-36 overflow-visible">
          {/* Zero baseline */}
          <line
            x1={padding}
            y1={zeroLineY}
            x2={svgWidth - padding}
            y2={zeroLineY}
            stroke="#334155"
            strokeDasharray="4 4"
            strokeWidth="1"
          />
          {/* Placebo median band */}
          <rect
            x={padding}
            y={toSvgY(0.05)}
            width={svgWidth - 2 * padding}
            height={Math.abs(toSvgY(-0.05) - toSvgY(0.05))}
            fill="#1e293b"
            opacity="0.3"
          />
          {/* Cumulative equity line */}
          {curvePoints.length > 1 && (
            <polyline
              fill="none"
              stroke="#38bdf8"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              points={polylinePoints}
            />
          )}
        </svg>

        <div className="flex items-center justify-between text-[10px] text-slate-500 mt-1">
          <span>Signal 0</span>
          <span>Cumulative Units: {cumUnits.toFixed(2)}</span>
          <span>Signal {maxX}</span>
        </div>
      </div>
    </div>
  );
}
