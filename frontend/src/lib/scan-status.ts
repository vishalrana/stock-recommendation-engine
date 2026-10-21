import type { ScanLog } from '../types/database';

// The latest scan's status when it is not a clean success (stale_data, degraded_sec, degraded_universe,
// failed), with its note, for the header next to the scan date. null when the scan succeeded.
export function scanStatusNote(log: Pick<ScanLog, 'status' | 'error_message'> | null | undefined): string | null {
  if (!log || !log.status || log.status === 'success') return null;
  const note = (log.error_message || '').trim();
  return note ? `${log.status}: ${note}` : log.status;
}
