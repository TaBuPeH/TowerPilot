import { entries, finite, hasOwn, id, list, own, ownPath, record, revision } from './read.ts';

/** History owns historical evidence checks; rules are documented in history.rules.md. */
export const HISTORY_PAGE_SIZE = 20;

export type HistoryStatsState =
  'verified' | 'unverified' | 'failed' | 'pending' | 'missing' | 'unknown';

export function historyStatsState(battle: unknown): HistoryStatsState {
  const stats = ownPath(battle, 'runtime', 'stats_capture');
  if (stats === null || stats === undefined) return 'missing';
  const status = own(stats, 'status');
  if (status === 'failed' || status === 'pending' || status === 'missing') return status;
  if (status === 'captured_unverified' || status === 'unverified') return 'unverified';
  if (status !== 'verified') return 'unknown';
  return own(stats, 'verified') === true &&
    own(stats, 'fresh') === true &&
    own(stats, 'structured') === true &&
    own(stats, 'tier_matches') === true &&
    own(stats, 'wave_consistent') === true &&
    id(own(battle, 'id')) !== undefined &&
    own(stats, 'battle_id') === own(battle, 'id') &&
    id(own(battle, 'envelope_hash')) !== undefined &&
    own(stats, 'envelope_hash') === own(battle, 'envelope_hash') &&
    own(battle, 'start_certainty') === 'observed_entry'
    ? 'verified'
    : 'unverified';
}

export interface HistoryCollection {
  readonly state: 'sampled' | 'unverified' | 'missing';
  readonly claims: number | null;
  readonly balances: number | null;
  readonly increase: number | null;
  readonly startedAt: number | null;
  readonly throughAt: number | null;
}

/** Use server counters only, never reconstruct gems from confirmations or fill absent counts with zero. */
export function historyCollection(battle: unknown): HistoryCollection {
  const raw = ownPath(battle, 'runtime', 'collection_evidence');
  const missing = {
    state: raw === null || raw === undefined ? 'missing' : 'unverified',
    claims: null,
    balances: null,
    increase: null,
    startedAt: null,
    throughAt: null,
  } as const;
  if (raw === null || raw === undefined) return missing;
  const start = finite(own(raw, 'started_at'));
  const through = finite(own(raw, 'observed_through_at'));
  const observedStart = finite(own(battle, 'observed_started_at'));
  const claims = revision(own(raw, 'ad_claim_confirmations'));
  const balances = revision(own(raw, 'orbit_balance_confirmations'));
  const increase = revision(own(raw, 'orbit_recorded_increase'));
  const confirmations = own(raw, 'latest_confirmations');
  if (
    own(raw, 'coverage') !== 'sampled_driver_observations' ||
    id(own(battle, 'generation')) === undefined ||
    own(raw, 'generation') !== own(battle, 'generation') ||
    start === undefined ||
    through === undefined ||
    observedStart === undefined ||
    start < observedStart ||
    through < start ||
    claims === undefined ||
    balances === undefined ||
    increase === undefined ||
    record(confirmations) === undefined ||
    (claims > 0 && !hasOwn(confirmations, 'ad_gems')) ||
    ((balances > 0 || increase > 0) && !hasOwn(confirmations, 'floating_gems')) ||
    entries(confirmations).some(([, confirmation]) => {
      const at = finite(own(confirmation, 'observed_at'));
      return (
        at === undefined || at < start || at > through || id(own(confirmation, 'id')) === undefined
      );
    })
  )
    return missing;
  return { state: 'sampled', claims, balances, increase, startedAt: start, throughAt: through };
}

export function historyRows(body: unknown): readonly unknown[] {
  return (list(own(body, 'battles')) ?? []).slice(0, HISTORY_PAGE_SIZE);
}

export function historyHasNext(body: unknown): boolean {
  return (list(own(body, 'battles'))?.length ?? 0) > HISTORY_PAGE_SIZE;
}
