import { fetchPortfolioSignals, fetchScanLogSignals, getLatestScanLog } from '../lib/database';
import { Recommendation, ScanLog } from '../types/database';
import RecommendationsTable from '../components/recommendations-table';
import PortfolioSummary from '../components/portfolio-summary';

// Force dynamic rendering — never prerender at build time
export const dynamic = 'force-dynamic';
export const revalidate = 0;

function formatDateLong(val: string | null | undefined): string {
  if (!val) return '-';
  const parts = val.split('-');
  if (parts.length !== 3) return val;
  const year = parseInt(parts[0], 10);
  const month = parseInt(parts[1], 10) - 1;
  const day = parseInt(parts[2], 10);
  const date = new Date(year, month, day);
  return date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

export default async function Page() {
  let portfolioData: Recommendation[] = [];
  let scanLogData: Recommendation[] = [];
  let errorMsg = '';
  let regime: string | null = null;
  let scanLog: ScanLog | null = null;

  try {
    const [portfolioSignals, scanLogs, latestScanLog] = await Promise.all([
      fetchPortfolioSignals(),
      fetchScanLogSignals(),
      getLatestScanLog(),
    ]);

    portfolioData = portfolioSignals;
    scanLogData = scanLogs;
    scanLog = latestScanLog;
    regime = scanLog?.regime || (portfolioData.length > 0 ? portfolioData[0].regime : null);
  } catch (e: any) {
    errorMsg = e.message || 'Failed to load recommendations';
  }

  // Active trade setups (open, pending, or partial scale-outs)
  const openPositions = portfolioData.filter(
    (p) => p.status === 'open' || p.status === 'pending' || p.status === 'hit_t1' || p.status === 'hit_t2'
  );

  return (
    <main className="min-h-screen bg-[#f8f9fa] py-8 px-4 sm:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-8 flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-2 pb-4 border-b border-gray-200/80">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 sm:text-3xl tracking-tight flex items-center gap-2">
              <span className="w-1.5 h-6 bg-blue-600 rounded-full inline-block"></span>
              Stock Recommendations
            </h1>
          </div>
          {scanLog?.scan_date && (
            <div className="text-xs text-gray-500 font-medium pl-3.5 sm:pl-0 sm:text-right">
              Last database scan: <span className="font-semibold text-gray-800">{formatDateLong(scanLog.scan_date)}</span>
            </div>
          )}
        </header>

        {errorMsg ? (
          <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded-xl shadow-sm">
            <div className="flex">
              <div className="flex-shrink-0">
                <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                </svg>
              </div>
              <div className="ml-3">
                <h3 className="text-sm font-semibold text-red-800">Database Connection Error</h3>
                <div className="mt-2 text-sm text-red-700">
                  <p>{errorMsg}</p>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-6">
            <PortfolioSummary openPositions={openPositions} />
            <div className="bg-white rounded-2xl shadow-sm border border-gray-200/80 p-6">
              <RecommendationsTable
                portfolioData={portfolioData}
                scanLogData={scanLogData}
                regime={regime}
                scanLog={scanLog}
              />
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
