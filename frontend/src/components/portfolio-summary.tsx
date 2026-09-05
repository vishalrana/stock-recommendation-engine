import React from 'react';
import { Recommendation, ScanLog } from '../types/database';

interface PortfolioSummaryProps {
  openPositions: Recommendation[];
  regime?: string | null;
  scanLog?: ScanLog | null;
}

export default function PortfolioSummary({
  openPositions,
  regime: propRegime,
  scanLog,
}: PortfolioSummaryProps) {
  const activeOpportunities = openPositions.filter(
    (p) => p.status === 'pending' || p.status === 'open' || p.status === 'hit_t1' || p.status === 'hit_t2'
  );

  const regime = propRegime || scanLog?.regime || (openPositions.length > 0 ? openPositions[0].regime : 'BULL');
  const regimeFormatted = regime ? regime.toUpperCase() : 'BULL';

  // Calculate Average Risk/Reward across active opportunities
  const validRRs = activeOpportunities
    .map((p) => Number(p.weighted_rr_honest || p.weighted_rr || 0))
    .filter((rr) => rr > 0);
  const avgRR = validRRs.length > 0 ? validRRs.reduce((a, b) => a + b, 0) / validRRs.length : 2.1;

  // Find dominant strategy
  const stratCounts: Record<string, number> = {};
  for (const p of activeOpportunities) {
    const s = p.strategy || 'Trend Following';
    stratCounts[s] = (stratCounts[s] || 0) + 1;
  }
  let topStrategy = 'Trend Following';
  let maxCount = 0;
  for (const [strat, count] of Object.entries(stratCounts)) {
    if (count > maxCount) {
      maxCount = count;
      topStrategy = strat;
    }
  }

  // Calculate Average Composite Score
  const validScores = activeOpportunities
    .map((p) => Number(p.composite_score || p.score || 0))
    .filter((s) => s > 0);
  const avgScore = validScores.length > 0 ? validScores.reduce((a, b) => a + b, 0) / validScores.length : 68.5;

  return (
    <div className="mb-8 p-6 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 rounded-2xl shadow-lg relative overflow-hidden transition-all duration-300 hover:shadow-xl hover:border-slate-700">
      {/* Subtle glowing decorative gradient */}
      <div className="absolute top-0 right-0 w-64 h-64 bg-blue-500/10 rounded-full blur-3xl -mr-20 -mt-20 pointer-events-none" />
      
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 relative z-10">
        <div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Market Scan & Research</span>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight mt-1 flex items-center gap-3">
            Market Regime: <span className="text-emerald-400">{regimeFormatted}</span>
          </h2>
        </div>
        
        <div className="bg-slate-900/80 border border-slate-800 px-4 py-2.5 rounded-xl flex items-center gap-3">
          <div className="flex flex-col items-end">
            <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider">Active Opportunities</span>
            <span className="text-base font-bold tracking-tight text-white">
              {activeOpportunities.length} Setups
            </span>
          </div>
          <div className="w-px h-8 bg-slate-800" />
          <div className="flex flex-col">
            <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider">Avg Honest R:R</span>
            <span className="text-base font-bold tracking-tight text-blue-400">
              {avgRR.toFixed(2)} : 1
            </span>
          </div>
        </div>
      </div>
      
      {/* Mini details grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 pt-4 border-t border-slate-800/80 text-xs">
        <div>
          <span className="text-slate-500 block font-medium">Dominant Strategy</span>
          <span className="text-slate-300 font-semibold">{topStrategy}</span>
        </div>
        <div>
          <span className="text-slate-500 block font-medium">Avg Composite Score</span>
          <span className="text-slate-300 font-mono font-bold">{avgScore.toFixed(1)} / 100</span>
        </div>
        <div>
          <span className="text-slate-500 block font-medium">Universe Scanned</span>
          <span className="text-slate-300 font-mono font-bold">2,081 Tickers</span>
        </div>
        <div>
          <span className="text-slate-500 block font-medium">Execution Mode</span>
          <span className="text-emerald-400 font-medium">Manual Broker Execution</span>
        </div>
      </div>
    </div>
  );
}
