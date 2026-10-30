import type { ContextRead } from './connection.ts';
import { knownFact, unknownFact, type Fact } from './facts.ts';
import { finite, hasOwn, id, own, ownPath, revision } from './read.ts';
import { decide, recordDecision, type ChipDecision, type RecordDecision } from './vocabulary.ts';

export interface BattleIdentity {
  readonly id: string;
  readonly generation: string;
  readonly operationId: string;
}

// Record-only decisions live with the other decisions (core/vocabulary.ts).
export { recordDecision, type RecordDecision } from './vocabulary.ts';

/** Public shape: backend/control/battles.py public/active. */
export function battleIdentity(battle: unknown, observedAt: number): Fact<BattleIdentity> {
  const battleId = id(own(battle, 'id'));
  const generation = id(own(battle, 'generation'));
  const operationId = id(own(battle, 'operation_id'));
  return battleId === undefined || generation === undefined || operationId === undefined
    ? unknownFact('battle_identity_invalid')
    : knownFact(Object.freeze({ id: battleId, generation, operationId }), observedAt);
}

export interface FreshBattleReport {
  readonly battle: unknown;
  readonly serverTime: number;
  readonly runtimeTime: number;
  readonly ageSec: number;
}

/** A battle report (or a routine observation without its own limit) older than this is stale. */
export const BATTLE_REPORT_MAX_AGE_SEC = 15;

/** EV-9 and F-055: elapsed is monotonic time since arrival, never wall time. */
export function freshBattleReport(
  read: ContextRead,
  shown: BattleIdentity | null,
  elapsedSec = 0,
): Fact<FreshBattleReport> {
  if (read?.kind !== 'answered' || !read.sameContext || shown === null)
    return unknownFact('battle_read_unavailable');
  const battle = own(read.body, 'battle');
  if (
    own(battle, 'status') !== 'active' ||
    own(battle, 'id') !== shown.id ||
    own(battle, 'generation') !== shown.generation ||
    own(battle, 'operation_id') !== shown.operationId
  )
    return unknownFact('battle_identity_changed');
  const age = battleReportAge(read.body, elapsedSec);
  if (age.kind !== 'fresh') return unknownFact(age.reason);
  const { serverTime, runtimeTime, ageSec } = age;
  return knownFact({ battle, serverTime, runtimeTime, ageSec }, serverTime);
}

/** The EV-9 age of a battle read, before its identity is checked (see `battleReportAge`). */
export type BattleReportAge =
  | {
      readonly kind: 'fresh';
      readonly serverTime: number;
      readonly runtimeTime: number;
      readonly ageSec: number;
    }
  | { readonly kind: 'not_fresh'; readonly reason: 'battle_time_invalid' | 'battle_report_old' };

/**
 * EV-9 and F-055, the one age rule of a battle read: `server_time - battle.runtime.server_time +
 * elapsed` (elapsed is monotonic time since arrival) must lie in 0..BATTLE_REPORT_MAX_AGE_SEC.
 * `body` is the answered body of GET /api/battles/active. `freshBattleReport` adds the identity
 * checks; core/actions `actionsReading` builds the timed-action reading on it.
 */
export function battleReportAge(body: unknown, elapsedSec: number): BattleReportAge {
  const serverTime = finite(own(body, 'server_time'));
  const runtimeTime = finite(ownPath(body, 'battle', 'runtime', 'server_time'));
  const elapsed = finite(elapsedSec);
  if (serverTime === undefined || runtimeTime === undefined || elapsed === undefined || elapsed < 0)
    return { kind: 'not_fresh', reason: 'battle_time_invalid' };
  const ageSec = serverTime - runtimeTime + elapsed;
  if (
    serverTime < runtimeTime ||
    !Number.isFinite(ageSec) ||
    ageSec < 0 ||
    ageSec > BATTLE_REPORT_MAX_AGE_SEC
  )
    return { kind: 'not_fresh', reason: 'battle_report_old' };
  return { kind: 'fresh', serverTime, runtimeTime, ageSec };
}

/**
 * F6-11 RL7: the server's clock now, in seconds: the battle read's `server_time` plus the
 * monotonic time since it arrived (null when the read names no time).
 */
export function battleServerNow(body: unknown, elapsedSec: number): number | null {
  const serverTime = finite(own(body, 'server_time'));
  return serverTime === undefined || !Number.isFinite(elapsedSec) || elapsedSec < 0
    ? null
    : serverTime + elapsedSec;
}

/**
 * How a battle's last control change stands (B5, B6; Runs brief RUN-09):
 * `applied` when `applied_revision` equals `control_revision`, `pending` while
 * it is lower, `unknown` when either is missing or the applied one is ahead.
 */
export function controlApplication(battle: unknown): 'applied' | 'pending' | 'unknown' {
  const applied = revision(own(battle, 'applied_revision'));
  const control = revision(own(battle, 'control_revision'));
  if (applied === undefined || control === undefined) return 'unknown';
  if (applied === control) return 'applied';
  return applied < control ? 'pending' : 'unknown';
}

/** B1â€“B7, first matching row. B2 is solely a fact about tracking records. */
export function battleView(
  read: ContextRead,
  shown: BattleIdentity | null,
  elapsedSec = 0,
): ChipDecision | RecordDecision<'B2', 'no_tracked_battle'> {
  if (read?.kind !== 'answered' || !read.sameContext)
    return decide('B1', 'unknown', 'battle_unknown', null);
  const battle = own(read.body, 'battle');
  if (battle === null) return recordDecision('B2', 'no_tracked_battle', read.body);
  if (own(battle, 'status') === 'prepared')
    return decide('B3', 'waiting', 'prepared_waiting_for_observation', null);
  const runtime = own(battle, 'runtime');
  if (
    own(battle, 'status') === 'active' &&
    (!hasOwn(runtime, 'server_time') || own(runtime, 'server_time') === null)
  )
    return decide('B4', 'waiting', 'waiting_for_first_report', null);
  const report = freshBattleReport(read, shown, elapsedSec);
  if (report.kind === 'known') {
    const application = controlApplication(battle);
    if (application === 'applied') return decide('B5', 'running', 'up_to_date', 'EV-9');
    if (application === 'pending')
      return decide('B6', 'waiting', 'waiting_for_control_application', 'EV-9');
  }
  const serverTime = finite(own(read.body, 'server_time'));
  const runtimeTime = finite(own(runtime, 'server_time'));
  const age =
    serverTime !== undefined && runtimeTime !== undefined
      ? serverTime - runtimeTime + elapsedSec
      : NaN;
  return decide('B7', 'unknown', 'old_report', null, { ageSec: finite(age) ?? null });
}
