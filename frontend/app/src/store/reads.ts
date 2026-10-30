import type { ConfigRead } from '../core/config.ts';
import type { ContextRead } from '../core/connection.ts';
import { assessStatus, type ReadAssessment, type StatusRead } from '../core/gate.ts';
import type { KnownLeaseHolder } from '../core/lease.ts';
import type { StatusContext } from '../core/statusContext.ts';
import type { DashboardState, ReadRecord, ResourceId } from './types.ts';

/**
 * Reads as core takes them. Core's checked constructors receive only what
 * core itself issued: a status read reaches every decision through
 * `assessStatus` (memoized per read record, so one read is assessed once and
 * every selector shares its brands), never through a hand-built record.
 * Every read in the store belongs to the current epoch, so each is a
 * same-context read (S-014).
 */
const assessments = new WeakMap<ReadRecord, ReadAssessment>();

/** core/gate.ts `StatusRead` of a stored read. */
export function statusReadOf(read: ReadRecord): StatusRead {
  return {
    startedSeq: read.startedSeq,
    outcome:
      read.outcome.kind === 'answered'
        ? { kind: 'answered', body: read.outcome.body }
        : { kind: 'failed', reason: read.outcome.reason },
  };
}

/** D-1, D-2 and D-3 of a stored status read, assessed once. */
export function assessedRead(read: ReadRecord): ReadAssessment {
  const known = assessments.get(read);
  if (known !== undefined) return known;
  const assessment = assessStatus(statusReadOf(read));
  assessments.set(read, assessment);
  return assessment;
}

/** core/config.ts `ConfigRead` of a stored config read. */
export function configReadOf(read: ReadRecord | null): ConfigRead | null {
  if (read === null) return null;
  return {
    startedSeq: read.startedSeq,
    outcome:
      read.outcome.kind === 'answered'
        ? { kind: 'answered', body: read.outcome.body }
        : { kind: 'failed', reason: read.outcome.reason },
  };
}

/** core/connection.ts `ContextRead` of a stored read (null: not read yet). */
export function contextReadOf(read: ReadRecord | null): ContextRead {
  if (read === null) return null;
  return read.outcome.kind === 'answered'
    ? { kind: 'answered', body: read.outcome.body, sameContext: true }
    : { kind: 'failed' };
}

export function latestRead(state: DashboardState, resource: ResourceId): ReadRecord | null {
  return state.reads[resource].latest;
}

export function lastAnsweredRead(state: DashboardState, resource: ResourceId): ReadRecord | null {
  return state.reads[resource].lastAnswered;
}

/** The body of the newest answered read of `resource` (undefined: none). */
export function answeredBody(state: DashboardState, resource: ResourceId): unknown {
  const read = state.reads[resource].lastAnswered;
  return read?.outcome.kind === 'answered' ? read.outcome.body : undefined;
}

/** Whole seconds since `read` arrived, by the store clock (browser intervals only). */
export function readAgeSec(state: DashboardState, read: ReadRecord): number {
  return Math.max(0, Math.floor((state.nowMs - read.arrivedAtMs) / 1000));
}

/**
 * B-05: an answered status read STARTED longer ago than this (by the store clock:
 * intervals between two browser readings, never a server time; measured from
 * the start, so a delayed answer is as old as the request that fetched it) no
 * longer describes now.
 * The Control decisions then treat it as the last read of a page whose newest
 * read did not answer: every observation Unknown with "last read {age}",
 * Pause, Turn input off and the known Stops kept, until a newer read arrives.
 * Visible polling reads status at least every 2.5 s with a 5 s timeout, so a
 * healthy page never reaches it; a resumed or stalled page does.
 */
export const STATUS_EXPIRY_SEC = 10;

const expiredAssessments = new WeakMap<ReadRecord, ReadAssessment>();

/** True when `read` answered and was started more than STATUS_EXPIRY_SEC ago by the store clock. */
export function statusExpired(state: DashboardState, read: ReadRecord): boolean {
  return (
    read.outcome.kind === 'answered' &&
    Math.floor((state.nowMs - read.startedAtMs) / 1000) > STATUS_EXPIRY_SEC
  );
}

/** An expired read as a read that did not answer (core sees no current status). */
function expiredAssessment(read: ReadRecord): ReadAssessment {
  const known = expiredAssessments.get(read);
  if (known !== undefined) return known;
  const assessment = assessStatus({
    startedSeq: read.startedSeq,
    outcome: { kind: 'failed', reason: 'read_expired' },
  });
  expiredAssessments.set(read, assessment);
  return assessment;
}

/** The newest status read as the Control decisions see it now (an expired read does not answer). */
export function currentStatusAssessment(state: DashboardState): ReadAssessment | null {
  const latest = state.reads.status.latest;
  if (latest === null) return null;
  return statusExpired(state, latest) ? expiredAssessment(latest) : assessedRead(latest);
}

/**
 * The Control slots' status context (core/statusContext.ts): the newest status
 * read assessed, and on a warm page whose newest read failed or expired, the
 * last answered read with its age ("last read {age}", S-092 keeps its Stops).
 */
export function statusContextOf(
  state: DashboardState,
  leaseHolders: readonly KnownLeaseHolder[],
): StatusContext {
  const slot = state.reads.status;
  const newest = currentStatusAssessment(state);
  const last =
    slot.latest !== null && statusExpired(state, slot.latest) ? slot.latest : slot.lastAnswered;
  const lastGood =
    newest !== null && !newest.answered && last !== null
      ? { read: assessedRead(last), ageSec: readAgeSec(state, last) }
      : null;
  return { newest, lastGood, leaseHolders };
}
