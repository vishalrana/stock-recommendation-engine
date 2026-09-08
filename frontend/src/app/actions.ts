'use server';

import { revalidatePath } from 'next/cache';
import { getSupabase } from '../lib/supabase';

export interface RemoveRecommendationParams {
  ticker: string;
  id?: string;
  scanDate?: string;
  reason: string;
  note?: string;
}

export async function removeRecommendationAction({
  ticker,
  id,
  scanDate,
  reason,
  note,
}: RemoveRecommendationParams) {
  try {
    const supabase = getSupabase();
    const tickerClean = ticker.trim().toUpperCase();
    const today = new Date().toISOString().split('T')[0];
    const nowIso = new Date().toISOString();
    const sellReason = note && note.trim() ? `${reason}: ${note.trim()}` : reason;

    // 1. Update signals table
    const updateSignalsData: any = {
      status: 'manually_removed',
      sell_signal: true,
      sell_signal_reason: sellReason,
      sell_signal_date: today,
      exit_date: today,
      removal_reason: reason,
      removal_note: note?.trim() || null,
      removed_at: nowIso,
    };

    // Strict safeguard: manual removal requires an unambiguous recommendation instance identifier
    if (!id && !scanDate) {
      return {
        success: false,
        error: 'Exact recommendation instance identifier (id or scanDate) is required for manual removal. Ticker-only removal is prohibited.',
      };
    }

    let targetScanDate = scanDate;

    try {
      if (id) {
        if (!targetScanDate) {
          const { data: sigRow } = await supabase
            .from('signals')
            .select('scan_date, ticker')
            .eq('id', id)
            .maybeSingle();
          if (sigRow?.scan_date) {
            targetScanDate = sigRow.scan_date;
          }
        }
        const { error: sigError } = await supabase.from('signals').update(updateSignalsData).eq('id', id);
        if (sigError) throw sigError;
      } else if (targetScanDate) {
        const { error: sigError } = await supabase
          .from('signals')
          .update(updateSignalsData)
          .eq('ticker', tickerClean)
          .eq('scan_date', targetScanDate);
        if (sigError) throw sigError;
      }
    } catch (err: any) {
      // Graceful fallback if removal_reason/note/removed_at columns are pending DB migration
      if (
        err.message?.includes('removal_reason') ||
        err.message?.includes('removal_note') ||
        err.message?.includes('removed_at') ||
        err.code === '42703'
      ) {
        delete updateSignalsData.removal_reason;
        delete updateSignalsData.removal_note;
        delete updateSignalsData.removed_at;

        if (id) {
          await supabase.from('signals').update(updateSignalsData).eq('id', id);
        } else if (targetScanDate) {
          await supabase.from('signals').update(updateSignalsData).eq('ticker', tickerClean).eq('scan_date', targetScanDate);
        }
      } else {
        console.error('Error updating signals on manual removal:', err);
      }
    }

    // 2. Update signals_history table for the EXACT recommendation instance
    const updateHistoryData: any = {
      outcome: 'manually_removed',
      outcome_date: today,
      sell_signal_reason: sellReason,
      removal_reason: reason,
      removal_note: note?.trim() || null,
      removed_at: nowIso,
    };

    const isNumericId = id && /^\d+$/.test(id);

    const executeHistoryUpdate = async (data: any) => {
      // Priority 1: Exact history numeric primary key if provided
      if (isNumericId) {
        return supabase.from('signals_history').update(data).eq('id', Number(id));
      }
      // Priority 2: Exact (ticker, scan_date) instance key
      if (targetScanDate) {
        return supabase.from('signals_history').update(data).eq('ticker', tickerClean).eq('scan_date', targetScanDate);
      }
      // Priority 3: Exact signal_id linkage if column exists
      if (id) {
        const res = await supabase.from('signals_history').update(data).eq('signal_id', id);
        if (!res.error) return res;
      }
      // Strict safeguard: refuse unsafe ticker-only fallback
      throw new Error('No exact recommendation instance match found in signals_history. Ticker-only fallback refused.');
    };

    try {
      const { error: histError } = await executeHistoryUpdate(updateHistoryData);
      if (histError) throw histError;
    } catch (err: any) {
      if (
        err.message?.includes('removal_reason') ||
        err.message?.includes('removal_note') ||
        err.message?.includes('removed_at') ||
        err.message?.includes('signal_id') ||
        err.code === '42703'
      ) {
        delete updateHistoryData.removal_reason;
        delete updateHistoryData.removal_note;
        delete updateHistoryData.removed_at;

        await executeHistoryUpdate(updateHistoryData);
      } else {
        console.error('Error updating signals_history on manual removal:', err);
      }
    }

    // 3. Revalidate path to refresh server components
    revalidatePath('/');

    return { success: true, ticker: tickerClean };
  } catch (error: any) {
    console.error('Failed to remove recommendation:', error);
    return { success: false, error: error.message || 'Failed to remove recommendation' };
  }
}

export interface LiveQuoteResult {
  price?: number;
  error?: string;
}

/**
 * Lightweight server action to fetch live market quotes for active stock ideas.
 * - Credentials remain strictly server-side
 * - Zero database writes or schema interaction
 * - No historical data, indicator, or strategy calculation
 * - Handles partial failure gracefully per ticker
 */
export async function fetchLiveQuotesAction(
  tickers: string[]
): Promise<Record<string, LiveQuoteResult>> {
  const results: Record<string, LiveQuoteResult> = {};
  if (!Array.isArray(tickers) || tickers.length === 0) {
    return results;
  }

  const uniqueTickers = Array.from(
    new Set(tickers.map((t) => t.trim().toUpperCase()).filter(Boolean))
  ).slice(0, 30);

  const tiingoKey = process.env.TIINGO_API_KEY;
  const finnhubKey = process.env.FINNHUB_API_KEY;

  await Promise.all(
    uniqueTickers.map(async (symbol) => {
      // 1. Tiingo IEX quote (primary provider)
      if (tiingoKey) {
        try {
          const res = await fetch(
            `https://api.tiingo.com/iex/${encodeURIComponent(symbol)}?token=${tiingoKey}`,
            { cache: 'no-store' }
          );
          if (res.status === 200) {
            const data = await res.json();
            if (Array.isArray(data) && data.length > 0) {
              const row = data[0];
              const price = row.last ?? row.tngoLast ?? row.close ?? row.prevClose;
              if (price !== undefined && price !== null && Number(price) > 0) {
                results[symbol] = { price: Math.round(Number(price) * 100) / 100 };
                return;
              }
            }
          }
        } catch (err) {
          console.warn(`[Live Quote] Tiingo error for ${symbol}:`, err);
        }
      }

      // 2. Finnhub quote (secondary provider)
      if (finnhubKey) {
        try {
          const res = await fetch(
            `https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(symbol)}&token=${finnhubKey}`,
            { cache: 'no-store' }
          );
          if (res.status === 200) {
            const data = await res.json();
            if (data && typeof data.c === 'number' && data.c > 0) {
              results[symbol] = { price: Math.round(Number(data.c) * 100) / 100 };
              return;
            }
          }
        } catch (err) {
          console.warn(`[Live Quote] Finnhub error for ${symbol}:`, err);
        }
      }

      // 3. Yahoo Finance v8 chart fallback
      try {
        const res = await fetch(
          `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1d&interval=1m`,
          {
            cache: 'no-store',
            headers: {
              'User-Agent':
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
              Accept: 'application/json',
            },
          }
        );
        if (res.status === 200) {
          const data = await res.json();
          const meta = data?.chart?.result?.[0]?.meta;
          const price = meta?.regularMarketPrice ?? meta?.chartPreviousClose ?? meta?.previousClose;
          if (price !== undefined && price !== null && Number(price) > 0) {
            results[symbol] = { price: Math.round(Number(price) * 100) / 100 };
            return;
          }
        }
      } catch (err) {
        console.warn(`[Live Quote] Yahoo fallback error for ${symbol}:`, err);
      }

      // Fallback: quote unavailable
      results[symbol] = { error: 'Quote unavailable' };
    })
  );

  return results;
}

export interface TriggerRefreshIdeasResult {
  success: boolean;
  message?: string;
  error?: string;
  runId?: number;
  tickers?: string[];
  dispatchedAt?: number;
}

export interface WorkflowRunStatusResult {
  status: 'queued' | 'in_progress' | 'completed' | 'unknown';
  conclusion: 'success' | 'failure' | 'cancelled' | 'timed_out' | null;
  runId?: number;
  error?: string;
}

/**
 * Server action to trigger GitHub Actions workflow for targeted current-idea refresh.
 * - Runs securely in Vercel Server Environment
 * - Zero Python execution in Vercel runtime (delegated to GitHub Actions)
 * - GitHub token is strictly server-side (process.env.GITHUB_TOKEN)
 * - Tickers are strictly validated and sanitized against shell injection
 * - Dispatches .github/workflows/refresh_current_ideas.yml with tickers input
 */
export async function triggerRefreshCurrentIdeasAction(
  activeTickers?: string[]
): Promise<TriggerRefreshIdeasResult> {
  try {
    const supabase = getSupabase();
    let tickersToRefresh = activeTickers;

    if (!tickersToRefresh || tickersToRefresh.length === 0) {
      const { data: rows, error: fetchErr } = await supabase
        .from('signals')
        .select('ticker')
        .in('status', ['open', 'pending']);

      if (fetchErr) {
        throw new Error(`Failed to query active recommendations: ${fetchErr.message}`);
      }

      tickersToRefresh = (rows || []).map((r: any) => r.ticker).filter(Boolean);
    }

    // Strict validation and sanitization: only alphanumeric, dot, hyphen tickers
    const sanitizedTickers = Array.from(
      new Set(
        tickersToRefresh
          .map((t) => (typeof t === 'string' ? t.trim().toUpperCase() : ''))
          .filter((t) => /^[A-Z0-9.\-_]{1,10}$/.test(t))
      )
    ).slice(0, 30);

    if (sanitizedTickers.length === 0) {
      return {
        success: true,
        message: 'No active stock ideas to refresh.',
        tickers: [],
      };
    }

    const token =
      process.env.GITHUB_TOKEN ||
      process.env.GH_TOKEN ||
      process.env.GITHUB_PAT;

    if (!token) {
      return {
        success: false,
        error:
          'GITHUB_TOKEN is not configured in the server environment. Please set GITHUB_TOKEN in your Vercel Project Settings.',
      };
    }

    const repo =
      process.env.GITHUB_REPOSITORY ||
      `${process.env.GITHUB_REPO_OWNER || 'vishalrana'}/${
        process.env.GITHUB_REPO_NAME || 'stock-recommendation-engine'
      }`;
    const branch = process.env.GITHUB_BRANCH || 'main';

    // Record timestamp threshold before dispatch (allowing 3s margin for runner clock skew)
    const dispatchedAt = Date.now() - 3000;

    const dispatchUrl = `https://api.github.com/repos/${repo}/actions/workflows/refresh_current_ideas.yml/dispatches`;

    const dispatchRes = await fetch(dispatchUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        ref: branch,
        inputs: {
          tickers: sanitizedTickers.join(','),
        },
      }),
      cache: 'no-store',
    });

    if (dispatchRes.status !== 204) {
      const errText = await dispatchRes.text();
      throw new Error(
        `Failed to trigger GitHub workflow (${dispatchRes.status}): ${errText || dispatchRes.statusText}`
      );
    }

    // Attempt to locate newly created run matching our dispatch timestamp
    let runId: number | undefined;
    try {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const runsUrl = `https://api.github.com/repos/${repo}/actions/workflows/refresh_current_ideas.yml/runs?event=workflow_dispatch&per_page=5`;
      const runsRes = await fetch(runsUrl, {
        headers: {
          Accept: 'application/vnd.github+json',
          Authorization: `Bearer ${token}`,
          'X-GitHub-Api-Version': '2022-11-28',
        },
        cache: 'no-store',
      });
      if (runsRes.status === 200) {
        const runsData = await runsRes.json();
        const runs = (runsData?.workflow_runs || []) as any[];
        // STRICT CORRELATION: Run MUST have been created at or after dispatchedAt
        const matchingRun = runs.find((r) => {
          const runCreatedAt = new Date(r.created_at).getTime();
          return runCreatedAt >= dispatchedAt && (!r.head_branch || r.head_branch === branch);
        });
        if (matchingRun) {
          runId = matchingRun.id;
        }
      }
    } catch {
      // Non-fatal: status polling will search using dispatchedAt filter
    }

    return {
      success: true,
      runId,
      dispatchedAt,
      tickers: sanitizedTickers,
      message: `Workflow dispatched for ${sanitizedTickers.join(', ')}`,
    };
  } catch (error: any) {
    console.error('Failed to trigger refresh workflow:', error);
    return {
      success: false,
      error: error?.message || 'Failed to trigger refresh workflow',
    };
  }
}

/**
 * Checks the status of the GitHub Actions workflow run with strict timestamp correlation.
 */
export async function checkRefreshCurrentIdeasStatusAction(
  runId?: number,
  dispatchedAt?: number
): Promise<WorkflowRunStatusResult> {
  try {
    const token =
      process.env.GITHUB_TOKEN ||
      process.env.GH_TOKEN ||
      process.env.GITHUB_PAT;

    if (!token) {
      return { status: 'unknown', conclusion: null, error: 'GITHUB_TOKEN not configured' };
    }

    const repo =
      process.env.GITHUB_REPOSITORY ||
      `${process.env.GITHUB_REPO_OWNER || 'vishalrana'}/${
        process.env.GITHUB_REPO_NAME || 'stock-recommendation-engine'
      }`;
    const branch = process.env.GITHUB_BRANCH || 'main';

    let targetRun: any = null;

    if (runId) {
      const runUrl = `https://api.github.com/repos/${repo}/actions/runs/${runId}`;
      const res = await fetch(runUrl, {
        headers: {
          Accept: 'application/vnd.github+json',
          Authorization: `Bearer ${token}`,
          'X-GitHub-Api-Version': '2022-11-28',
        },
        cache: 'no-store',
      });
      if (res.status === 200) {
        const candidate = await res.json();
        // Strict correlation check: candidate run MUST be created at or after dispatchedAt
        if (!dispatchedAt || new Date(candidate.created_at).getTime() >= dispatchedAt) {
          targetRun = candidate;
        }
      }
    }

    // Fallback: query recent runs and find the run created at or after dispatchedAt
    if (!targetRun) {
      const runsUrl = `https://api.github.com/repos/${repo}/actions/workflows/refresh_current_ideas.yml/runs?event=workflow_dispatch&per_page=5`;
      const res = await fetch(runsUrl, {
        headers: {
          Accept: 'application/vnd.github+json',
          Authorization: `Bearer ${token}`,
          'X-GitHub-Api-Version': '2022-11-28',
        },
        cache: 'no-store',
      });
      if (res.status === 200) {
        const data = await res.json();
        const runs = (data?.workflow_runs || []) as any[];
        targetRun = runs.find((r) => {
          const runCreatedAt = new Date(r.created_at).getTime();
          const matchTime = !dispatchedAt || runCreatedAt >= dispatchedAt;
          const matchBranch = !r.head_branch || r.head_branch === branch;
          return matchTime && matchBranch;
        });
      }
    }

    // If no run created after dispatchedAt is found yet, GitHub Actions is still queueing
    // NEVER mistake an older historical run for the new run
    if (!targetRun) {
      return { status: 'queued', conclusion: null };
    }

    const status = targetRun.status as 'queued' | 'in_progress' | 'completed';
    const conclusion = targetRun.conclusion as 'success' | 'failure' | 'cancelled' | 'timed_out' | null;

    return {
      status: status || 'in_progress',
      conclusion,
      runId: targetRun.id,
    };
  } catch (error: any) {
    console.error('Error checking workflow status:', error);
    return { status: 'unknown', conclusion: null, error: error?.message };
  }
}

/**
 * Finalizes current ideas refresh by revalidating Server Components.
 */
export async function completeRefreshCurrentIdeasAction(): Promise<{ success: boolean }> {
  try {
    revalidatePath('/');
    return { success: true };
  } catch {
    return { success: false };
  }
}

