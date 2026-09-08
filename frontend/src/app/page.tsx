import { fetchPortfolioSignals, fetchClosedSignals, fetchScanHistory, getLatestScanLog } from '../lib/database';
import { Recommendation, ScanHistoryEntry, ScanLog } from '../types/database';
import StockIdeasApp from '../components/stock-ideas-app';

// Force dynamic rendering — never prerender at build time
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default async function Page() {
  let activeIdeas: Recommendation[] = [];
  let closedIdeas: Recommendation[] = [];
  let scanHistory: ScanHistoryEntry[] = [];
  let latestScanLog: ScanLog | null = null;
  let errorMsg = '';

  try {
    const [active, closed, history, scanLog] = await Promise.all([
      fetchPortfolioSignals(),
      fetchClosedSignals(),
      fetchScanHistory(14),
      getLatestScanLog(),
    ]);

    activeIdeas = active;
    closedIdeas = closed;
    scanHistory = history;
    latestScanLog = scanLog;
  } catch (e: any) {
    errorMsg = e.message || 'Failed to load stock recommendations';
  }

  if (errorMsg) {
    return (
      <main className="min-h-screen bg-[#0b0f19] text-slate-100 flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-rose-950/40 border border-rose-800/80 p-6 rounded-2xl shadow-xl text-center">
          <h2 className="text-base font-bold text-rose-300">Connection Error</h2>
          <p className="text-xs text-rose-400/90 mt-2">{errorMsg}</p>
        </div>
      </main>
    );
  }

  return (
    <StockIdeasApp
      initialActiveIdeas={activeIdeas}
      initialClosedIdeas={closedIdeas}
      scanHistory={scanHistory}
      latestScanLog={latestScanLog}
    />
  );
}
