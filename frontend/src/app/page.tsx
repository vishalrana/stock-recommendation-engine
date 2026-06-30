import { getSupabase } from '../lib/supabase';
import { Recommendation, ScanLog } from '../types/database';
import RecommendationsTable from '../components/recommendations-table';

// Force dynamic rendering — never prerender at build time
export const dynamic = 'force-dynamic';
export const revalidate = 0;

async function getRecommendations() {
  const { data, error } = await getSupabase()
    .from('recommendations')
    .select('*');

  if (error) {
    console.error('Error fetching recommendations:', error);
    throw new Error(error.message);
  }

  return (data || []) as Recommendation[];
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

  try {
    const [recommendations, latestScanLog] = await Promise.all([
      getRecommendations(),
      getLatestScanLog(),
    ]);

    data = recommendations;
    scanLog = latestScanLog;
    regime = scanLog?.regime || (data.length > 0 ? data[0].regime : null);
  } catch (e: any) {
    errorMsg = e.message || 'Failed to load recommendations';
  }

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
          <div className="bg-white rounded-2xl shadow-sm border border-gray-200/80 p-6">
            <RecommendationsTable data={data} regime={regime} scanLog={scanLog} />
          </div>
        )}
      </div>
    </main>
  );
}
