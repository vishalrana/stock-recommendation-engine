import test from 'node:test';
import assert from 'node:assert';
import { scanStatusNote } from '../src/lib/scan-status';

test('scan status: shown with its note unless the scan succeeded', () => {
  assert.strictEqual(scanStatusNote({ status: 'success', error_message: null }), null);
  assert.strictEqual(scanStatusNote(null), null);
  assert.strictEqual(scanStatusNote({ status: 'stale_data', error_message: 'Latest cached session 2026-10-16 < market date 2026-10-19' }),
    'stale_data: Latest cached session 2026-10-16 < market date 2026-10-19');
  assert.strictEqual(scanStatusNote({ status: 'degraded_sec', error_message: '  ' }), 'degraded_sec');
  assert.strictEqual(scanStatusNote({ status: 'failed', error_message: 'Database insertion failed' }), 'failed: Database insertion failed');
});
