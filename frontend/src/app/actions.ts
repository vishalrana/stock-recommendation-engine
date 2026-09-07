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
