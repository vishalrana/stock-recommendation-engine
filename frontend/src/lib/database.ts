import { getSupabase } from './supabase';
import { Recommendation, ScanLog } from '../types/database';

export async function fetchPortfolioSignals(): Promise<Recommendation[]> {
  const supabase = getSupabase();

  // 1. Fetch ONLY active qualified recommendations without capital constraints
  const { data: activeSignals, error: activeError } = await supabase
    .from('signals')
    .select('*')
    .in('status', ['open', 'pending'])
    .order('scan_date', { ascending: false });

  if (activeError) {
    console.error('Error fetching active recommendations:', activeError);
  }

  // Fetch ticker metrics for win rates and trades
  const { data: metricsData } = await supabase.from('ticker_metrics').select('*');
  const metricsMap = new Map((metricsData || []).map((m: any) => [m.ticker?.toUpperCase(), m]));

  const activeFormatted = (activeSignals || []).map((s: any) => {
    const m = metricsMap.get(s.ticker?.toUpperCase()) || {};
    return {
      ...s,
      tier_label: s.tier_label || 'Buy',
      status: s.status || 'open',
      entry_date: s.entry_date || s.scan_date,
      past_win_rate: m.win_rate ?? 0,
      total_trades: (m.wins ?? 0) + (m.losses ?? 0),
      expectancy_pct: m.expectancy_pct ?? 0,
      wins: m.wins ?? 0,
      losses: m.losses ?? 0,
    };
  });

  activeFormatted.sort((a: any, b: any) => {
    const dateA = new Date(a.scan_date || 0).getTime();
    const dateB = new Date(b.scan_date || 0).getTime();
    if (dateB !== dateA) return dateB - dateA;
    return (Number(b.composite_score) || 0) - (Number(a.composite_score) || 0);
  });

  return activeFormatted as Recommendation[];
}

export async function fetchScanLogSignals(): Promise<Recommendation[]> {
  const supabase = getSupabase();

  const sevenDaysAgo = new Date();
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 14);
  const cutoffDateStr = sevenDaysAgo.toISOString().split('T')[0];

  // 1. Fetch historical completed recommendation outcomes from signals_history
  const { data: closedHistory, error: historyError } = await supabase
    .from('signals_history')
    .select('*')
    .neq('outcome', 'open')
    .order('scan_date', { ascending: false });

  if (historyError) {
    console.error('Error fetching closed history:', historyError);
  }

  // 2. Fetch completed or rejected signals from signals table
  const { data: scanLogSignals, error: scanError } = await supabase
    .from('signals')
    .select('*')
    .in('status', ['rejected', 'cancelled_gap_up', 'stopped', 'invalidated', 'manually_removed'])
    .gte('scan_date', cutoffDateStr)
    .order('scan_date', { ascending: false })
    .order('composite_score', { ascending: false });

  if (scanError) {
    console.error('Error fetching scan log signals:', scanError);
  }

  // Attach ticker metrics
  const { data: metricsData } = await supabase.from('ticker_metrics').select('*');
  const metricsMap = new Map((metricsData || []).map((m: any) => [m.ticker?.toUpperCase(), m]));

  const seenKeys = new Set<string>();
  const combined: Recommendation[] = [];

  // Add historical outcomes first
  for (const h of (closedHistory || [])) {
    const key = `${h.scan_date}_${h.ticker?.toUpperCase()}`;
    seenKeys.add(key);

    const m = metricsMap.get(h.ticker?.replace(' (P)', '').toUpperCase()) || {};
    const outcome = h.outcome || 'closed';
    let reason = h.sell_signal_reason || 'Recommendation Outcome';
    if (outcome === 'stopped') reason = 'Stop loss hit';
    else if (outcome === 'invalidated') reason = h.sell_signal_reason || 'No longer qualifies in subsequent scan';
    else if (outcome === 'manually_removed') {
      const parts = [h.removal_reason || 'Manually removed'];
      if (h.removal_note) parts.push(h.removal_note);
      reason = parts.join(': ');
    }
    else if (outcome === 'hit_t3') reason = 'Target 3 hit';
    else if (outcome === 'hit_t2') reason = 'Target 2 hit';
    else if (outcome === 'hit_t1') reason = 'Target 1 hit';

    combined.push({
      ...h,
      tier_label: h.tier_label || 'Buy',
      entry_date: h.scan_date,
      exit_date: h.outcome_date || h.exit_date,
      status: outcome,
      outcome: outcome,
      sell_signal: true,
      sell_signal_reason: reason,
      sell_price: h.exit_price || h.price,
      past_win_rate: m.win_rate ?? 0,
      total_trades: (m.wins ?? 0) + (m.losses ?? 0),
      expectancy_pct: m.expectancy_pct ?? 0,
      wins: m.wins ?? 0,
      losses: m.losses ?? 0,
    });
  }

  // Add scan rejections and lifecycle transitions from signals
  for (const s of (scanLogSignals || [])) {
    const key = `${s.scan_date}_${s.ticker?.toUpperCase()}`;
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);

    const m = metricsMap.get(s.ticker?.toUpperCase()) || {};
    combined.push({
      ...s,
      tier_label: s.tier_label || (s.status === 'rejected' ? 'Rejected' : 'Buy'),
      entry_date: s.entry_date || s.scan_date,
      past_win_rate: m.win_rate ?? 0,
      total_trades: (m.wins ?? 0) + (m.losses ?? 0),
      expectancy_pct: m.expectancy_pct ?? 0,
      wins: m.wins ?? 0,
      losses: m.losses ?? 0,
    });
  }

  // Sort by scan_date DESC, composite_score DESC
  combined.sort((a, b) => {
    const dateA = new Date(a.scan_date || 0).getTime();
    const dateB = new Date(b.scan_date || 0).getTime();
    if (dateB !== dateA) return dateB - dateA;
    return (Number(b.composite_score) || 0) - (Number(a.composite_score) || 0);
  });

  return combined;
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

export function calculatePWin(score: number): number {
  const z = -0.15 * (score - 65.0);
  const sigmoid = 1.0 / (1.0 + Math.exp(z));
  const p = 0.35 + 0.40 * sigmoid;
  return Math.max(0.35, Math.min(0.75, Math.round(p * 10000) / 10000));
}

export function getRejectionReason(sig: Recommendation): string {
  if (sig.status === 'manually_removed' || sig.outcome === 'manually_removed') {
    const reason = sig.removal_reason || 'Manually removed by user';
    return sig.removal_note ? `${reason}: ${sig.removal_note}` : reason;
  }
  if (sig.status === 'invalidated' || sig.outcome === 'invalidated') {
    return sig.sell_signal_reason || 'No longer qualifies in subsequent scan';
  }
  if (sig.status === 'stopped' || sig.outcome === 'stopped') {
    return 'Stop loss hit';
  }
  if (sig.status === 'hit_t3' || sig.outcome === 'hit_t3') {
    return 'Target 3 hit';
  }
  if (sig.status === 'hit_t2' || sig.outcome === 'hit_t2') {
    return 'Target 2 hit';
  }
  if (sig.status === 'hit_t1' || sig.outcome === 'hit_t1') {
    return 'Target 1 hit';
  }
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
