import { getSupabase } from './supabase';
import { Recommendation, ScanLog, ScanHistoryEntry } from '../types/database';

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
    else if (outcome === 'rejected') reason = h.sell_signal_reason || 'Filter rejected';

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

/**
 * Authoritative fetch for Closed Stock Ideas
 * Uses `signals_history` as the primary/authoritative source of truth for all historical
 * recommendation outcomes: stopped, hit_t1, hit_t2, hit_t3, invalidated, manually_removed.
 * Preserves exact recommendation instances using id / signal_id / scan_date.
 * Does NOT merge distinct historical instances for the same ticker.
 */
export async function fetchClosedSignals(): Promise<Recommendation[]> {
  const supabase = getSupabase();

  const { data: closedHistory, error: historyError } = await supabase
    .from('signals_history')
    .select('*')
    .in('outcome', ['stopped', 'hit_t1', 'hit_t2', 'hit_t3', 'invalidated', 'manually_removed'])
    .order('outcome_date', { ascending: false })
    .order('scan_date', { ascending: false });

  if (historyError) {
    console.error('Error fetching closed history from signals_history:', historyError);
  }

  // Also include any active records from `signals` that were just marked closed/stopped/invalidated/manually_removed
  // before being archived, to ensure immediate consistency
  const { data: activeClosed, error: activeError } = await supabase
    .from('signals')
    .select('*')
    .in('status', ['stopped', 'hit_t1', 'hit_t2', 'hit_t3', 'invalidated', 'manually_removed'])
    .order('scan_date', { ascending: false });

  if (activeError) {
    console.error('Error fetching closed signals from signals table:', activeError);
  }

  // Track unique instances by primary key / signal_id / (ticker + scan_date)
  const seenInstances = new Set<string>();
  const closedIdeas: Recommendation[] = [];

  for (const h of (closedHistory || [])) {
    const instanceKey = h.id ? `hist_${h.id}` : h.signal_id ? `sig_${h.signal_id}` : `${h.ticker}_${h.scan_date}`;
    seenInstances.add(instanceKey);

    const outcome = h.outcome || 'closed';
    let reason = h.sell_signal_reason || 'Closed';
    if (outcome === 'stopped') reason = 'Stop Loss';
    else if (outcome === 'hit_t1') reason = 'Target 1 reached';
    else if (outcome === 'hit_t2') reason = 'Target 2 reached';
    else if (outcome === 'hit_t3') reason = 'Target 3 reached';
    else if (outcome === 'invalidated') reason = h.sell_signal_reason || 'Idea invalidated';
    else if (outcome === 'manually_removed') {
      const parts = [h.removal_reason || 'Manually removed'];
      if (h.removal_note) parts.push(h.removal_note);
      reason = parts.join(': ');
    }

    closedIdeas.push({
      ...h,
      status: outcome,
      outcome: outcome,
      sell_signal: true,
      sell_signal_reason: reason,
      exit_date: h.outcome_date || h.exit_date,
      sell_price: h.exit_price || h.price,
    });
  }

  for (const s of (activeClosed || [])) {
    const instanceKey = s.id ? `sig_${s.id}` : `${s.ticker}_${s.scan_date}`;
    if (seenInstances.has(instanceKey)) continue;
    seenInstances.add(instanceKey);

    const outcome = s.status || 'closed';
    let reason = s.sell_signal_reason || 'Closed';
    if (outcome === 'stopped') reason = 'Stop Loss';
    else if (outcome === 'invalidated') reason = s.sell_signal_reason || 'Idea invalidated';
    else if (outcome === 'manually_removed') {
      const parts = [s.removal_reason || 'Manually removed'];
      if (s.removal_note) parts.push(s.removal_note);
      reason = parts.join(': ');
    }

    closedIdeas.push({
      ...s,
      status: outcome,
      outcome: outcome,
      sell_signal: true,
      sell_signal_reason: reason,
      exit_date: s.exit_date || s.scan_date,
      sell_price: s.exit_price || s.price,
    });
  }

  return closedIdeas;
}

/**
 * Chronological Scan History Fetcher
 * Aggregates scan_log entries with qualified recommendations and filtered/rejected setups.
 */
export async function fetchScanHistory(limit = 14): Promise<ScanHistoryEntry[]> {
  const supabase = getSupabase();
  try {
    const { data: logs, error: logsError } = await supabase
      .from('scan_log')
      .select('*')
      .order('scan_date', { ascending: false })
      .limit(limit);

    if (logsError || !logs || logs.length === 0) {
      return [];
    }

    const scanDates = logs.map((l: any) => l.scan_date);

    // Fetch signals and history across these scan dates
    const [signalsRes, historyRes] = await Promise.all([
      supabase
        .from('signals')
        .select('scan_date, ticker, company_name, strategy, strategy_name, status, rejection_reason, sell_signal_reason, composite_score')
        .in('scan_date', scanDates),
      supabase
        .from('signals_history')
        .select('scan_date, ticker, company_name, strategy, strategy_name, outcome, rejection_reason, sell_signal_reason, composite_score')
        .in('scan_date', scanDates),
    ]);

    const signalsData = signalsRes.data || [];
    const historyData = historyRes.data || [];

    return logs.map((log: any) => {
      const sDate = log.scan_date;
      const dateSignals = signalsData.filter((s: any) => s.scan_date === sDate);
      const dateHistory = historyData.filter((h: any) => h.scan_date === sDate);

      const qualifiedMap = new Map<string, { ticker: string; strategy: string; company_name?: string | null }>();
      const filteredMap = new Map<string, { ticker: string; strategy?: string | null; reason: string }>();

      for (const s of dateSignals) {
        const ticker = s.ticker?.toUpperCase();
        if (!ticker) continue;
        if (s.status === 'open' || s.status === 'pending') {
          qualifiedMap.set(ticker, {
            ticker,
            strategy: s.strategy_name || s.strategy || 'Momentum',
            company_name: s.company_name,
          });
        } else if (s.status === 'rejected') {
          filteredMap.set(ticker, {
            ticker,
            strategy: s.strategy_name || s.strategy,
            reason: getRejectionReason(s as any),
          });
        }
      }

      for (const h of dateHistory) {
        const ticker = h.ticker?.toUpperCase();
        if (!ticker) continue;
        if (h.outcome === 'rejected') {
          if (!filteredMap.has(ticker)) {
            filteredMap.set(ticker, {
              ticker,
              strategy: h.strategy_name || h.strategy,
              reason: h.rejection_reason || h.sell_signal_reason || 'Filter rejected',
            });
          }
        } else if (!qualifiedMap.has(ticker)) {
          qualifiedMap.set(ticker, {
            ticker,
            strategy: h.strategy_name || h.strategy || 'Momentum',
            company_name: h.company_name,
          });
        }
      }

      const qList = Array.from(qualifiedMap.values());
      const fList = Array.from(filteredMap.values());

      return {
        scan_date: sDate,
        tickers_scanned: Number(log.tickers_scanned || 0),
        signals_generated: Number(log.signals_generated || 0),
        signals_qualified: qList.length > 0 ? qList.length : Number(log.signals_qualified || 0),
        regime: (log.regime || 'bull').toUpperCase(),
        status: log.status || 'success',
        newIdeas: qList,
        filteredSetups: fList,
      };
    });
  } catch (err) {
    console.error('Error in fetchScanHistory:', err);
    return [];
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
