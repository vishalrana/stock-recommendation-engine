import type { ScanLog } from '../types/database';

// For the header next to the scan date: the latest scan's status when it is not a clean success
// (stale_data, degraded_sec, degraded_universe, failed), and its note whenever there is one (a
// successful scan can still note tickers awaiting a price refetch or with an unadjusted split).
export function scanStatusNote(log: Pick<ScanLog, 'status' | 'error_message'> | null | undefined): string | null {
  if (!log || !log.status) return null;
  const note = (log.error_message || '').trim();
  if (log.status === 'success') return note || null;
  return note ? `${log.status}: ${note}` : log.status;
}
