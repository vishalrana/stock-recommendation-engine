import test from 'node:test';
import assert from 'node:assert';
import { FORWARD_TEST_STATUS, forwardTestBanner } from '../src/lib/forward-test-status';

test('forward-test banner: shown only on a pre-registered STOP or END', () => {
  assert.strictEqual(FORWARD_TEST_STATUS.state, 'running');
  assert.strictEqual(forwardTestBanner(), null);
  assert.strictEqual(forwardTestBanner({ state: 'CONTINUE', message: 'x' }), null);
  assert.strictEqual(
    forwardTestBanner({ state: 'STOP', message: 'Forward test stopped by its pre-registered rule on 2027-01-16', run_id: '123' }),
    'Forward test stopped by its pre-registered rule on 2027-01-16 (GitHub Actions run 123).');
});
