import { knownFact, unknownFact, type Fact } from './facts.ts';
import { finite, own, record } from './read.ts';

/**
 * D-2 Fresh scan (safety rule S-023): freshness comes from the numbers, never
 * from the `fresh` label alone. `process_refresh.state === 'fresh'`, `error`
 * null or absent, `age_sec` finite with 0 <= age <= 6, `last_success_at`
 * finite, and with implied = H - last_success_at: -2 <= implied <= age + 2,
 * where H is `harvest.server_time` of the same body. The browser clock is
 * never used (decision 5).
 */
export interface FreshScan {
  readonly ageSec: number;
  readonly lastSuccessAt: number;
}

/** The oldest scan D-2 accepts, in seconds (`_procs_status` marks a scan stale above 6 s). */
export const SCAN_MAX_AGE_SEC = 6;
/** Rounding and a scan published between the two reads (R3-07 second opinion, decision 4). */
export const SCAN_SKEW_SEC = 2;

export function scanFresh(
  processRefresh: unknown,
  serverTime: number | undefined,
): Fact<FreshScan> {
  if (record(processRefresh) === undefined) {
    return unknownFact(processRefresh === undefined ? 'scan_missing' : 'scan_not_object');
  }
  if (own(processRefresh, 'state') !== 'fresh') {
    return unknownFact('scan_not_fresh');
  }
  const error = own(processRefresh, 'error');
  if (error !== undefined && error !== null) {
    return unknownFact('scan_error');
  }
  const ageSec = finite(own(processRefresh, 'age_sec'));
  if (ageSec === undefined || ageSec < 0 || ageSec > SCAN_MAX_AGE_SEC) {
    return unknownFact('scan_age');
  }
  const lastSuccessAt = finite(own(processRefresh, 'last_success_at'));
  if (lastSuccessAt === undefined) {
    return unknownFact('scan_last_success');
  }
  const now = finite(serverTime);
  if (now === undefined) {
    return unknownFact('server_time_invalid');
  }
  const implied = now - lastSuccessAt;
  if (implied < -SCAN_SKEW_SEC || implied > ageSec + SCAN_SKEW_SEC) {
    return unknownFact('scan_time_inconsistent');
  }
  return knownFact(Object.freeze({ ageSec, lastSuccessAt }), now);
}
