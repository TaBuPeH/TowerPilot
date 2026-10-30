/**
 * D-9 Operation status (safety rules, Definitions). Final: cancelled,
 * interrupted, failed, succeeded. Active: accepted, running, applying,
 * cancelling. Anything else - another word, a non-string, a missing value -
 * is unknown and claims nothing.
 */
export type OperationStatusClass = 'final' | 'active' | 'unknown';

const FINAL: readonly string[] = ['cancelled', 'interrupted', 'failed', 'succeeded'];
const ACTIVE: readonly string[] = ['accepted', 'running', 'applying', 'cancelling'];

export function classifyStatus(value: unknown): OperationStatusClass {
  if (typeof value !== 'string') {
    return 'unknown';
  }
  if (FINAL.includes(value)) {
    return 'final';
  }
  return ACTIVE.includes(value) ? 'active' : 'unknown';
}

/** `stop.state: 'active'` covers exactly these operation statuses (D-9). */
export const STOP_STATE_ACTIVE_STATUSES: readonly string[] = ['accepted', 'running', 'applying'];
