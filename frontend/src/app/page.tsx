import { getSupabase } from '../lib/supabase';
import { Recommendation, ScanLog } from '../types/database';
import RecommendationsTable from '../components/recommendations-table';

// Force dynamic rendering — never prerender at build time
export const dynamic = 'force-dynamic';

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

export default async function Page() {
  let data: Recommendation[] = [];
  let errorMsg = '';
  let regime: string | null = null;

  try {
    const [recommendations, scanLog] = await Promise.all([
      getRecommendations(),
      getLatestScanLog(),
    ]);

    data = recommendations;
    regime = scanLog?.regime || (data.length > 0 ? data[0].regime : null);
  } catch (e: any) {
    errorMsg = e.message || 'Failed to load recommendations';
  }

  return (
    <main className="min-h-screen bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-10">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <h1 className="text-3xl font-extrabold text-gray-900 sm:text-4xl tracking-tight">
                Stock Recommendation Engine
              </h1>
              <p className="mt-2 text-sm text-gray-600">
                Strategy 1.3 &mdash; Regime-Aware Ranking. RSI Pullback+Recovery, ADX&ge;20, MACD Confirmed. Updated nightly.
              </p>
            </div>
            {data.length > 0 && (
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800 self-start md:self-auto font-medium">
                Latest Scan Date: <span className="font-bold">{data[0].scan_date}</span>
              </div>
            )}
          </div>
        </header>

        {errorMsg ? (
          <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded shadow-sm">
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
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
            <RecommendationsTable data={data} regime={regime} />
          </div>
        )}
      </div>
    </main>
  );
}
