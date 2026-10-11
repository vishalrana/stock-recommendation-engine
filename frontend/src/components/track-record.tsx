"use client";

import React, { useState } from 'react';
import { SwingSignalRecord } from '../types/database';
import { Activity } from 'lucide-react';

interface TrackRecordProps {
  closedSignals: SwingSignalRecord[];   // closed signals issued by LIVE sleeves only
}

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export default function TrackRecord({ closedSignals }: TrackRecordProps) {
  const [selected, setSelected] = useState<string>('combined');
  const rows = (selected === 'combined' ? closedSignals : closedSignals.filter((s) => s.sleeve === selected))
    .slice()
    .sort((a, b) => String(a.exit_date || '').localeCompare(String(b.exit_date || '')));
  const net = rows.map((s) => Number(s.net_return_pct)).filter(finite);
  const ba = rows.map((s) => Number(s.beta_adj_pct)).filter(finite);
  const mean = (x: number[]) => (x.length ? x.reduce((a, b) => a + b, 0) / x.length : null);
  const winRate = net.length ? (100 * net.filter((x) => x > 0).length) / net.length : null;
  const fmt = (v: number | null) => (v === null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);

  // Cumulative equal-unit beta-adjusted P&L (sum of per-signal returns, no compounding)
  const cum: number[] = [];
  ba.reduce((acc, x) => { cum.push(acc + x); return acc + x; }, 0);
  const W = 500, H = 140, P = 16;
  const lo = Math.min(0, ...cum), hi = Math.max(0, ...cum);
  const span = hi - lo || 1;
  const x = (i: number) => P + (i / Math.max(1, cum.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - lo) / span) * (H - 2 * P);

  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-sm">
      <div className="flex items-center justify-between flex-wrap gap-3 pb-3 border-b border-slate-800/80">
        <h3 className="text-base font-bold text-white flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-400" /> Live track record
        </h3>
        <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-xl border border-slate-800 text-xs font-semibold flex-wrap">
          {['combined', 'T', 'E', 'N', 'P', 'I'].map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSelected(s)}
              className={`px-2.5 py-1 rounded-lg cursor-pointer ${selected === s ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-slate-200'}`}
            >
              {s === 'combined' ? 'All' : s}
            </button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="mt-4 text-xs text-slate-400 text-center">No closed live signals yet.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
            {[
              ['Closed signals', String(rows.length)],
              ['Win rate', winRate === null ? '—' : `${winRate.toFixed(0)}%`],
              ['Mean net / signal', fmt(mean(net))],
              ['Mean after beta / signal', fmt(mean(ba))],
            ].map(([label, value]) => (
              <div key={label} className="bg-slate-950/70 p-3 rounded-xl border border-slate-800/80">
                <span className="text-[10px] text-slate-400 block uppercase font-bold">{label}</span>
                <span className="text-lg font-black text-white">{value}</span>
              </div>
            ))}
          </div>
          {cum.length >= 2 && (
            <div className="mt-4 bg-slate-950 p-3 rounded-xl border border-slate-800/80">
              <span className="text-xs font-semibold text-slate-300">Cumulative units after beta (equal unit per signal)</span>
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-32" role="img" aria-label="Cumulative beta-adjusted units">
                <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="#334155" strokeDasharray="4 4" />
                <polyline fill="none" stroke="#38bdf8" strokeWidth="2" points={cum.map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
              </svg>
            </div>
          )}
          <p className="mt-3 text-[10px] text-slate-500">Backtests and short live records do not prove future profit.</p>
        </>
      )}
    </div>
  );
}
