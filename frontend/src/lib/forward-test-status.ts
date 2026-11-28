import status from './forward-test-status.json';

// Written by the forward-test review workflow (scripts/forward_test_review.py) when a pre-registered rule
// (docs/forward-test-reviews.md) stops or ends the Ideas forward test; "running" until then.
export interface ForwardTestStatus {
  state: string;
  message?: string;
  run_id?: string | null;
}

export const FORWARD_TEST_STATUS: ForwardTestStatus = status;

export function forwardTestBanner(s: ForwardTestStatus = FORWARD_TEST_STATUS): string | null {
  if ((s.state !== 'STOP' && s.state !== 'END') || !s.message) return null;
  return `${s.message} (GitHub Actions run ${s.run_id ?? 'n/a'}).`;
}
