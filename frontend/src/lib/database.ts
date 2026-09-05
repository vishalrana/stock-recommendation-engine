import { getSupabase } from './supabase';
import { Recommendation, ScanLog } from '../types/database';

export async function fetchPortfolioSignals(): Promise<Recommendation[]> {
  const supabase = getSupabase();

  // 1. Fetch active qualified recommendations without capital constraints
  const { data: activeSignals, error: activeError } = await supabase
    .from('signals')
    .select('*')
    .neq('status', 'rejected')
    .in('status', ['pending', 'open', 'hit_t1', 'hit_t2'])
    .order('scan_date', { ascending: false });

  if (activeError) {
    console.error('Error fetching active recommendations:', activeError);
  }

  // 2. Fetch closed trades / outcomes from signals_history
  const { data: closedHistory, error: historyError } = await supabase
    .from('signals_history')
    .select('*')
    .neq('outcome', 'open')
    .order('scan_date', { ascending: false });

  if (historyError) {
    console.error('Error fetching closed history:', historyError);
  }

  // Fetch ticker metrics for win rates and trades
  const { data: metricsData } = await supabase.from('ticker_metrics').select('*');
  const metricsMap = new Map((metricsData || []).map((m: any) => [m.ticker?.toUpperCase(), m]));

  const activeFormatted = (activeSignals || []).map((s: any) => {
    const m = metricsMap.get(s.ticker?.toUpperCase()) || {};
    return {
      ...s,
      tier_label: s.tier_label || 'Buy',
      status: s.status || 'pending',
      entry_date: s.entry_date || s.scan_date,
      past_win_rate: m.win_rate ?? 0,
      total_trades: (m.wins ?? 0) + (m.losses ?? 0),
      expectancy_pct: m.expectancy_pct ?? 0,
      wins: m.wins ?? 0,
      losses: m.losses ?? 0,
    };
  });

  const closedFormatted = (closedHistory || []).map((h: any) => {
    const m = metricsMap.get(h.ticker?.replace(' (P)', '').toUpperCase()) || {};
    let status = h.outcome;
    if (['stopped', 'stop_loss', 'hit_t3', 'hit_t2', 'hit_t1', 'closed'].includes(h.outcome)) {
      status = 'closed';
    }
    let reason = 'Closed';
    if (h.outcome === 'stopped') reason = 'Stop loss hit';
    else if (h.outcome === 'hit_t3') reason = 'Target 3 hit – full exit';
    else if (h.outcome === 'hit_t2') reason = 'Target 2 hit – sell 30%';
    else if (h.outcome === 'hit_t1') reason = 'Target 1 hit – sell 50%';

    return {
      ...h,
      tier_label: h.tier_label || 'Buy',
      entry_date: h.scan_date,
      exit_date: h.outcome_date,
      status: status || 'closed',
      sell_signal: true,
      sell_signal_reason: reason,
      sell_price: h.exit_price || h.price,
      past_win_rate: m.win_rate ?? 0,
      total_trades: (m.wins ?? 0) + (m.losses ?? 0),
      expectancy_pct: m.expectancy_pct ?? 0,
      wins: m.wins ?? 0,
      losses: m.losses ?? 0,
    };
  });

  const result = [...activeFormatted, ...closedFormatted];
  result.sort((a: any, b: any) => {
    const dateA = new Date(a.scan_date || 0).getTime();
    const dateB = new Date(b.scan_date || 0).getTime();
    if (dateB !== dateA) return dateB - dateA;
    return (Number(b.composite_score) || 0) - (Number(a.composite_score) || 0);
  });

  return result as Recommendation[];
}

export async function fetchScanLogSignals(): Promise<Recommendation[]> {
  const supabase = getSupabase();

  const sevenDaysAgo = new Date();
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
  const sevenDaysAgoStr = sevenDaysAgo.toISOString().split('T')[0];

  // Fetch audit log / rejected signals
  const { data: scanLogSignals, error } = await supabase
    .from('signals')
    .select('*')
    .in('status', ['rejected', 'cancelled_gap_up'])
    .gte('scan_date', sevenDaysAgoStr)
    .order('scan_date', { ascending: false })
    .order('composite_score', { ascending: false });

  if (error) {
    console.error('Error fetching scan log signals:', error);
  }

  // Attach ticker metrics
  const { data: metricsData } = await supabase.from('ticker_metrics').select('*');
  const metricsMap = new Map((metricsData || []).map((m: any) => [m.ticker?.toUpperCase(), m]));

  const formatted = (scanLogSignals || []).map((s: any) => {
    const m = metricsMap.get(s.ticker?.toUpperCase()) || {};
    return {
      ...s,
      tier_label: s.tier_label || 'Rejected',
      entry_date: s.entry_date || s.scan_date,
      past_win_rate: m.win_rate ?? 0,
      total_trades: (m.wins ?? 0) + (m.losses ?? 0),
      expectancy_pct: m.expectancy_pct ?? 0,
      wins: m.wins ?? 0,
      losses: m.losses ?? 0,
    };
  });

  return formatted as Recommendation[];
}

export async function getLatestScanLog(): Promise<ScanLog | null> {
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

export async function getLatestPortfolioValue(): Promise<number> {
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

export function calculatePWin(score: number): number {
  const z = -0.15 * (score - 65.0);
  const sigmoid = 1.0 / (1.0 + Math.exp(z));
  const p = 0.35 + 0.40 * sigmoid;
  return Math.max(0.35, Math.min(0.75, Math.round(p * 10000) / 10000));
}

export function getRejectionReason(sig: Recommendation): string {
  if (sig.rejection_reason) {
    return sig.rejection_reason;
  }
  if (sig.sell_signal_reason) {
    return sig.sell_signal_reason;
  }
  if (sig.earnings_rejected) {
    const days = sig.days_to_earnings !== undefined && sig.days_to_earnings !== null ? `${sig.days_to_earnings}d` : '';
    return `Earnings risk filter (${days})`;
  }
  if (sig.status === 'cancelled_gap_up') {
    return 'Cancelled: Gap > 3%';
  }
  if (sig.reach_prob_t1 !== undefined && sig.reach_prob_t1 !== null && Number(sig.reach_prob_t1) < 0.25) {
    return `ReachProb T1 < 25% (${(Number(sig.reach_prob_t1) * 100).toFixed(0)}%)`;
  }

  const rr = sig.weighted_rr_honest ?? sig.weighted_rr ?? 0;
  const score = sig.composite_score || 50;

  if (sig.tier_label === 'Rejected' || Number(score) < 65) {
    return `Tier Rejected (Score ${Number(score).toFixed(1)}, R:R ${Number(rr).toFixed(2)})`;
  }

  return 'Setup criteria not met';
}
