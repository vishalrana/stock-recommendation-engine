'use server';

import { revalidatePath } from 'next/cache';
import { getSupabase } from '../lib/supabase';

export interface RemoveRecommendationParams {
  ticker: string;
  id?: string;
  reason: string;
  note?: string;
}

export async function removeRecommendationAction({
  ticker,
  id,
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

    try {
      let query = supabase.from('signals').update(updateSignalsData);
      if (id) {
        query = query.eq('id', id);
      } else {
        query = query.eq('ticker', tickerClean).in('status', ['open', 'pending']);
      }
      const { error: sigError } = await query;
      if (sigError) throw sigError;
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

        let query = supabase.from('signals').update(updateSignalsData);
        if (id) {
          query = query.eq('id', id);
        } else {
          query = query.eq('ticker', tickerClean).in('status', ['open', 'pending']);
        }
        await query;
      } else {
        console.error('Error updating signals on manual removal:', err);
      }
    }

    // 2. Update signals_history table
    const updateHistoryData: any = {
      outcome: 'manually_removed',
      outcome_date: today,
      removal_reason: reason,
      removal_note: note?.trim() || null,
      removed_at: nowIso,
    };

    try {
      const { error: histError } = await supabase
        .from('signals_history')
        .update(updateHistoryData)
        .eq('ticker', tickerClean)
        .eq('outcome', 'open');
      if (histError) throw histError;
    } catch (err: any) {
      if (
        err.message?.includes('removal_reason') ||
        err.message?.includes('removal_note') ||
        err.message?.includes('removed_at') ||
        err.code === '42703'
      ) {
        delete updateHistoryData.removal_reason;
        delete updateHistoryData.removal_note;
        delete updateHistoryData.removed_at;

        await supabase
          .from('signals_history')
          .update(updateHistoryData)
          .eq('ticker', tickerClean)
          .eq('outcome', 'open');
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
