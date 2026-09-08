"use client";

import React, { useState } from 'react';
import { Recommendation, ScanHistoryEntry, ScanLog } from '../types/database';
import StockCard from './stock-card';
import ClosedIdeasView from './closed-ideas-view';
import ScanHistoryView from './scan-history-view';
import RemoveIdeaModal from './remove-idea-modal';
import { removeRecommendationAction } from '../app/actions';
import { Lightbulb, Archive, History, Sparkles, RefreshCw, ShieldCheck } from 'lucide-react';
import { useRouter } from 'next/navigation';

interface StockIdeasAppProps {
  initialActiveIdeas: Recommendation[];
  initialClosedIdeas: Recommendation[];
  scanHistory: ScanHistoryEntry[];
  latestScanLog: ScanLog | null;
}

type TabType = 'ideas' | 'closed' | 'scans';

function formatScanDateHeader(dateStr?: string | null): string {
  if (!dateStr) return '';
  try {
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const year = parseInt(parts[0], 10);
      const month = parseInt(parts[1], 10) - 1;
      const day = parseInt(parts[2], 10);
      const d = new Date(year, month, day);
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }
    return dateStr;
  } catch {
    return dateStr;
  }
}

export default function StockIdeasApp({
  initialActiveIdeas,
  initialClosedIdeas,
  scanHistory,
  latestScanLog,
}: StockIdeasAppProps) {
  const router = useRouter();

  // Primary Navigation State (Default: 'ideas')
  const [activeTab, setActiveTab] = useState<TabType>('ideas');

  // Active / Closed Datasets with server-confirmed updates
  const [activeIdeas, setActiveIdeas] = useState<Recommendation[]>(initialActiveIdeas || []);
  const [closedIdeas, setClosedIdeas] = useState<Recommendation[]>(initialClosedIdeas || []);

  // Expanded Active Idea (Card tap target)
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Manual Removal Modal State
  const [removingIdea, setRemovingIdea] = useState<Recommendation | null>(null);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  // Server-confirmed removal handler
  const handleConfirmRemoval = async (reason: string, note?: string) => {
    if (!removingIdea) return { success: false, error: 'No idea selected' };

    const ticker = removingIdea.ticker;
    const targetId = removingIdea.id;
    const scanDate = removingIdea.scan_date;

    const res = await removeRecommendationAction({
      ticker,
      id: targetId,
      scanDate,
      reason,
      note,
    });

    if (res.success) {
      // Server confirmed: safely transition from active to closed
      const removedItem: Recommendation = {
        ...removingIdea,
        status: 'manually_removed',
        outcome: 'manually_removed',
        removal_reason: reason,
        removal_note: note || null,
        exit_date: new Date().toISOString().split('T')[0],
        sell_signal_reason: note ? `${reason}: ${note}` : reason,
      };

      setActiveIdeas((prev) =>
        prev.filter((item) => {
          if (targetId && item.id) return item.id !== targetId;
          return item.ticker?.toUpperCase() !== ticker.toUpperCase();
        })
      );

      setClosedIdeas((prev) => [removedItem, ...prev]);

      setRemovingIdea(null);
      setSuccessBanner(`${ticker} moved to Closed Stock Ideas.`);
      setTimeout(() => setSuccessBanner(null), 4000);

      router.refresh();
      return { success: true };
    } else {
      return { success: false, error: res.error || 'Failed to remove recommendation.' };
    }
  };

  const handleRefresh = () => {
    setIsRefreshing(true);
    router.refresh();
    setTimeout(() => setIsRefreshing(false), 800);
  };

  const lastScanDate = formatScanDateHeader(latestScanLog?.scan_date);

  return (
    <div className="min-h-screen bg-[#0b0f19] text-slate-100 flex flex-col antialiased selection:bg-blue-500 selection:text-white pb-24 md:pb-12">
      {/* Top Header */}
      <header className="sticky top-0 z-30 bg-[#0b0f19]/90 backdrop-blur-md border-b border-slate-800/80 px-4 sm:px-6 lg:px-8 py-3.5">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-blue-600/20 border border-blue-500/30 flex items-center justify-center text-blue-400">
              <Sparkles className="w-4 h-4" />
            </div>
            <div>
              <h1 className="text-base sm:text-lg font-bold text-white tracking-tight leading-tight">
                Stock Ideas
              </h1>
              {lastScanDate && (
                <p className="text-[11px] text-slate-400 font-medium">
                  Scan date: <span className="text-slate-300 font-semibold">{lastScanDate}</span>
                </p>
              )}
            </div>
          </div>

          {/* Desktop Navigation Tabs */}
          <nav className="hidden md:flex items-center gap-1.5 bg-slate-900/90 border border-slate-800 p-1 rounded-xl">
            <button
              type="button"
              onClick={() => setActiveTab('ideas')}
              className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                activeTab === 'ideas'
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
              }`}
            >
              <Lightbulb className="w-3.5 h-3.5" />
              <span>Ideas</span>
              {activeIdeas.length > 0 && (
                <span
                  className={`text-[10px] px-1.5 py-0.2 rounded-full font-bold ${
                    activeTab === 'ideas' ? 'bg-blue-800 text-white' : 'bg-slate-800 text-slate-300'
                  }`}
                >
                  {activeIdeas.length}
                </span>
              )}
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('closed')}
              className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                activeTab === 'closed'
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
              }`}
            >
              <Archive className="w-3.5 h-3.5" />
              <span>Closed</span>
              {closedIdeas.length > 0 && (
                <span
                  className={`text-[10px] px-1.5 py-0.2 rounded-full font-bold ${
                    activeTab === 'closed' ? 'bg-blue-800 text-white' : 'bg-slate-800 text-slate-300'
                  }`}
                >
                  {closedIdeas.length}
                </span>
              )}
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('scans')}
              className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                activeTab === 'scans'
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
              }`}
            >
              <History className="w-3.5 h-3.5" />
              <span>Scans</span>
            </button>
          </nav>

          {/* Refresh Action */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="text-slate-400 hover:text-white p-2 rounded-xl hover:bg-slate-800/80 transition-colors cursor-pointer"
              title="Refresh Data"
              aria-label="Refresh Data"
            >
              <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-blue-400' : ''}`} />
            </button>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="max-w-6xl mx-auto w-full px-4 sm:px-6 lg:px-8 pt-5 sm:pt-7 flex-1">
        {/* Success Feedback Notification */}
        {successBanner && (
          <div className="mb-5 p-3.5 bg-emerald-950/60 border border-emerald-800/80 rounded-2xl text-xs text-emerald-300 flex items-center gap-2 shadow-lg animate-in fade-in slide-in-from-top-2 duration-200">
            <ShieldCheck className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            <span className="font-semibold">{successBanner}</span>
          </div>
        )}

        {/* TAB 1: CURRENT STOCK IDEAS */}
        {activeTab === 'ideas' && (
          <section>
            <div className="flex items-center justify-between mb-4 px-1">
              <div>
                <h2 className="text-sm font-extrabold text-white uppercase tracking-wider">
                  Current Stock Ideas
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Tap an idea to inspect the live technical chart and trade setup.
                </p>
              </div>
              <span className="text-xs font-bold text-slate-400 font-mono">
                {activeIdeas.length} Active
              </span>
            </div>

            {activeIdeas.length === 0 ? (
              <div className="py-20 px-4 text-center">
                <div className="w-12 h-12 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center mx-auto mb-3 text-slate-500">
                  <Lightbulb className="w-6 h-6" />
                </div>
                <h3 className="text-base font-bold text-slate-200">No Active Stock Ideas Right Now</h3>
                <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
                  Quality and risk gates prevented sub-optimal setups tonight. The next market scan will identify new opportunities.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {activeIdeas.map((idea) => {
                  const cardId = idea.id || `${idea.ticker}_${idea.scan_date}`;
                  return (
                    <StockCard
                      key={cardId}
                      recommendation={idea}
                      isExpanded={expandedId === cardId}
                      onToggleExpand={() => toggleExpand(cardId)}
                      onRemove={(rec) => setRemovingIdea(rec)}
                    />
                  );
                })}
              </div>
            )}
          </section>
        )}

        {/* TAB 2: CLOSED STOCK IDEAS */}
        {activeTab === 'closed' && (
          <section>
            <div className="mb-4 px-1">
              <h2 className="text-sm font-extrabold text-white uppercase tracking-wider">
                Closed Stock Ideas
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Authoritative historical log of completed trade outcomes and manual removals.
              </p>
            </div>
            <ClosedIdeasView closedIdeas={closedIdeas} />
          </section>
        )}

        {/* TAB 3: SCAN HISTORY */}
        {activeTab === 'scans' && (
          <section>
            <div className="mb-4 px-1">
              <h2 className="text-sm font-extrabold text-white uppercase tracking-wider">
                Scan History
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Chronological record of systematic market scans and qualified ideas.
              </p>
            </div>
            <ScanHistoryView scanHistory={scanHistory} />
          </section>
        )}
      </main>

      {/* MOBILE STICKY BOTTOM NAVIGATION BAR */}
      <nav
        aria-label="Mobile Navigation"
        className="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-[#0d1322]/95 backdrop-blur-lg border-t border-slate-800/90 px-3 py-2 flex items-center justify-around shadow-2xl"
      >
        <button
          type="button"
          onClick={() => setActiveTab('ideas')}
          className={`flex flex-col items-center justify-center flex-1 py-1 rounded-xl transition-all cursor-pointer ${
            activeTab === 'ideas' ? 'text-blue-400' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <div className="relative">
            <Lightbulb className={`w-5 h-5 ${activeTab === 'ideas' ? 'stroke-[2.5]' : 'stroke-2'}`} />
            {activeIdeas.length > 0 && (
              <span className="absolute -top-1 -right-2.5 bg-blue-600 text-white text-[9px] font-bold px-1 rounded-full min-w-[14px] text-center">
                {activeIdeas.length}
              </span>
            )}
          </div>
          <span className={`text-[10px] mt-1 font-bold ${activeTab === 'ideas' ? 'text-blue-400' : 'text-slate-400'}`}>
            Ideas
          </span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('closed')}
          className={`flex flex-col items-center justify-center flex-1 py-1 rounded-xl transition-all cursor-pointer ${
            activeTab === 'closed' ? 'text-blue-400' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Archive className={`w-5 h-5 ${activeTab === 'closed' ? 'stroke-[2.5]' : 'stroke-2'}`} />
          <span className={`text-[10px] mt-1 font-bold ${activeTab === 'closed' ? 'text-blue-400' : 'text-slate-400'}`}>
            Closed
          </span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('scans')}
          className={`flex flex-col items-center justify-center flex-1 py-1 rounded-xl transition-all cursor-pointer ${
            activeTab === 'scans' ? 'text-blue-400' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <History className={`w-5 h-5 ${activeTab === 'scans' ? 'stroke-[2.5]' : 'stroke-2'}`} />
          <span className={`text-[10px] mt-1 font-bold ${activeTab === 'scans' ? 'text-blue-400' : 'text-slate-400'}`}>
            Scans
          </span>
        </button>
      </nav>

      {/* Manual Removal Confirmation Modal */}
      {removingIdea && (
        <RemoveIdeaModal
          recommendation={removingIdea}
          isOpen={true}
          onClose={() => setRemovingIdea(null)}
          onConfirm={handleConfirmRemoval}
        />
      )}
    </div>
  );
}
