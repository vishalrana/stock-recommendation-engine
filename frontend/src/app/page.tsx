import { getSupabase } from '../lib/supabase';
import { Recommendation, ScanLog } from '../types/database';
import RecommendationsTable from '../components/recommendations-table';
import PortfolioSummary from '../components/portfolio-summary';

// Force dynamic rendering — never prerender at build time
export const dynamic = 'force-dynamic';
export const revalidate = 0;

async function getRecommendations() {
  const supabase = getSupabase();
  
  // 1. Fetch active recommendations from recommendations view
  const { data: activeData, error: activeError } = await supabase
    .from('recommendations')
    .select('*');

  if (activeError) {
    console.error('Error fetching active recommendations:', activeError);
    throw new Error(activeError.message);
  }

  // 2. Fetch closed signals from signals_history
  const { data: historyData, error: historyError } = await supabase
    .from('signals_history')
    .select('*');

  if (historyError) {
    console.error('Error fetching signals history:', historyError);
    throw new Error(historyError.message);
  }

  // 3. Union they, mapping signals_history fields to match Recommendation type
  const activeMapped = (activeData || []).map((r: any) => ({
    ...r,
    status: r.status || 'open'
  }));

  const historyMapped = (historyData || [])
    .filter((h: any) => h.outcome !== 'open')
    .map((h: any) => {
      let status = 'closed';
      if (h.outcome === 'open') {
        status = 'open';
      }
      
      let sellReason = 'Closed';
      if (h.outcome === 'stopped') {
        sellReason = 'Stop loss hit';
      } else if (h.outcome === 'hit_t3') {
        sellReason = 'Target 3 hit – full exit';
      } else if (h.outcome === 'hit_t2') {
        sellReason = 'Target 2 hit – sell 30%';
      } else if (h.outcome === 'hit_t1') {
        sellReason = 'Target 1 hit – sell 50%';
      }

      return {
        ...h,
        entry_date: h.entry_date || h.scan_date,
        exit_date: h.exit_date || h.outcome_date,
        status,
        sell_signal: true,
        sell_signal_reason: sellReason,
        sell_price: h.exit_price || h.price,
        past_win_rate: h.past_win_rate || 0,
        total_trades: h.total_trades || 0,
        expectancy_pct: h.expectancy_pct || 0
      };
    });

  const combined = [...activeMapped, ...historyMapped];
  
  // Sort by scan_date descending
  combined.sort((a, b) => {
    const dateA = new Date(a.scan_date || 0).getTime();
    const dateB = new Date(b.scan_date || 0).getTime();
    return dateB - dateA;
  });

  return combined as Recommendation[];
}

async function getLatestScanLog(): Promise<ScanLog | null> {
  try {
    const { data, error } = await getSupabase()
      .from('scan_log')
      .select('*')
      .order('scan_date', { ascending: false })
      .limit(1);

    if (error || !data || data.length === 0) {
      return null;
    }

    return data[0] as ScanLog;
  } catch {
    return null;
  }
}

async function getLatestPortfolioValue(): Promise<number> {
  try {
    const { data, error } = await getSupabase()
      .from('portfolio_state')
      .select('portfolio_value')
      .order('created_at', { ascending: false })
      .limit(1);

    if (error || !data || data.length === 0) {
      return 10000.0;
    }

    return parseFloat(data[0].portfolio_value) || 10000.0;
  } catch {
    return 10000.0;
  }
}

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
    timeZone: 'UTC'
  });
}

export default async function Page() {
  let data: Recommendation[] = [];
  let errorMsg = '';
  let regime: string | null = null;
  let scanLog: ScanLog | null = null;
  let latestPortfolioValue = 10000.0;

  try {
    const [recommendations, latestScanLog, portfolioVal] = await Promise.all([
      getRecommendations(),
      getLatestScanLog(),
      getLatestPortfolioValue()
    ]);

    data = recommendations;
    scanLog = latestScanLog;
    latestPortfolioValue = portfolioVal;
    regime = scanLog?.regime || (data.length > 0 ? data[0].regime : null);
  } catch (e: any) {
    errorMsg = e.message || 'Failed to load recommendations';
  }

  const openPositions = data.filter(p => p.status === 'open');

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
            <PortfolioSummary latestPortfolioValue={latestPortfolioValue} openPositions={openPositions} />
            <div className="bg-white rounded-2xl shadow-sm border border-gray-200/80 p-6">
              <RecommendationsTable data={data} regime={regime} scanLog={scanLog} latestPortfolioValue={latestPortfolioValue} />
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
