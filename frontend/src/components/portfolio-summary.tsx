import React from 'react';
import { Recommendation } from '../types/database';

interface PortfolioSummaryProps {
  latestPortfolioValue: number;
  openPositions: Recommendation[];
}

export const STARTING_PRINCIPAL = 10000;

export default function PortfolioSummary({ latestPortfolioValue, openPositions }: PortfolioSummaryProps) {
  // 1. Calculate Unrealized P&L from open positions
  let totalUnrealizedPnlDollars = 0;
  
  for (const pos of openPositions) {
    const entry = pos.entry_price ? Number(pos.entry_price) : 0;
    const price = pos.price ? Number(pos.price) : 0;
    
    if (entry > 0 && price > 0) {
      const returnPct = ((price - entry) / entry) * 100;
      
      // Extract allocation percentage (defaults to 5% if parsing fails)
      let allocationPct = 0.05;
      if (pos.position_sizing) {
        const raw = pos.position_sizing.replace('Kelly:', '').replace('K:', '').replace('%', '').trim();
        const parsed = parseFloat(raw);
        if (!isNaN(parsed)) {
          allocationPct = parsed / 100.0;
        }
      }
      
      const allocationDollars = allocationPct * latestPortfolioValue;
      const unrealizedPnlDollars = allocationDollars * (returnPct / 100);
      totalUnrealizedPnlDollars += unrealizedPnlDollars;
    }
  }

  // 2. Compute Total Value & Return metrics
  const totalValue = latestPortfolioValue + totalUnrealizedPnlDollars;
  const allTimeReturnPct = ((totalValue - STARTING_PRINCIPAL) / STARTING_PRINCIPAL) * 100;
  
  const isPositive = totalValue >= STARTING_PRINCIPAL;
  const returnSign = isPositive ? '+' : '';
  const returnColorClass = isPositive ? 'text-emerald-600' : 'text-rose-600';
  
  return (
    <div className="mb-8 p-6 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 rounded-2xl shadow-lg relative overflow-hidden transition-all duration-300 hover:shadow-xl hover:border-slate-700">
      {/* Subtle glowing decorative gradient */}
      <div className="absolute top-0 right-0 w-64 h-64 bg-blue-500/10 rounded-full blur-3xl -mr-20 -mt-20 pointer-events-none" />
      
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 relative z-10">
        <div>
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Growth Dashboard</span>
          <h2 className="text-3xl font-extrabold text-white tracking-tight mt-1">
            Current Portfolio: <span className="text-blue-400">${totalValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          </h2>
        </div>
        
        <div className="bg-slate-900/80 border border-slate-800 px-4 py-2.5 rounded-xl flex items-center gap-3">
          <div className="flex flex-col items-end">
            <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider">All-Time Return</span>
            <span className={`text-base font-bold tracking-tight ${returnColorClass}`}>
              {returnSign}{allTimeReturnPct.toFixed(2)}%
            </span>
          </div>
          <div className="w-px h-8 bg-slate-800" />
          <div className="flex flex-col">
            <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider">Net P&L</span>
            <span className={`text-base font-bold tracking-tight ${returnColorClass}`}>
              {returnSign}${(totalValue - STARTING_PRINCIPAL).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
        </div>
      </div>
      
      {/* Mini details grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 pt-4 border-t border-slate-800/80 text-xs">
        <div>
          <span className="text-slate-500 block font-medium">Starting Principal</span>
          <span className="text-slate-300 font-mono font-bold">${STARTING_PRINCIPAL.toLocaleString()}</span>
        </div>
        <div>
          <span className="text-slate-500 block font-medium">Realized Equity</span>
          <span className="text-slate-300 font-mono font-bold">${latestPortfolioValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
        </div>
        <div>
          <span className="text-slate-500 block font-medium">Unrealized P&L</span>
          <span className={`font-mono font-bold ${totalUnrealizedPnlDollars >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
            {totalUnrealizedPnlDollars >= 0 ? '+' : ''}${totalUnrealizedPnlDollars.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        </div>
        <div>
          <span className="text-slate-500 block font-medium">Active Allocations</span>
          <span className="text-slate-300 font-mono font-bold">{openPositions.length} positions</span>
        </div>
      </div>
    </div>
  );
}
