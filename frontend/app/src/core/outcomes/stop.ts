import { evidenceGate, type StatusRead } from '../gate.ts';
import { classifyStatus, STOP_STATE_ACTIVE_STATUSES } from '../operationStatus.ts';
import { id, list, own, ownPath, pid, revision, text } from '../read.ts';
import {
  answerRevisionClaims,
  statusRevisionClaims,
  stopRevision,
  type RevisionClaim,
} from '../stopRevision.ts';
import { leaseHolders, type LeaseHolder } from '../lease.ts';

export interface StopOperation {
  readonly id: string;
  readonly revision: number;
  readonly status: string;
  readonly error: unknown;
  readonly raw: unknown;
}

export interface StopEffects {
  readonly paused: boolean;
  readonly requested: boolean;
  readonly signalled: readonly number[];
  readonly family: 'stopped' | 'stopping' | 'unverified' | 'gone' | null;
  readonly released: boolean;
}

/** S-052: a malformed effects record attributes nothing, even if state says stopped. */
export function stopEffects(raw: unknown): StopEffects | null {
  const paused = own(raw, 'paused');
  const requested = own(raw, 'requested');
  const released = own(raw, 'released');
  const family = own(raw, 'family');
  const values = list(own(raw, 'signalled'));
  if (
    typeof paused !== 'boolean' ||
    typeof requested !== 'boolean' ||
    typeof released !== 'boolean' ||
    values === undefined ||
    (family !== null &&
      family !== 'stopped' &&
      family !== 'stopping' &&
      family !== 'unverified' &&
      family !== 'gone')
  )
    return null;
  const signalled: number[] = [];
  for (const value of values) {
    const signalPid = pid(value);
    if (signalPid === undefined) return null;
    signalled.push(signalPid);
  }
  return { paused, requested, signalled, family, released };
}

function matchingOperation(raw: unknown, operationId: string): StopOperation | null {
  const rev = revision(own(raw, 'revision'));
  const status = text(own(raw, 'status'));
  if (
    id(operationId) === undefined ||
    own(raw, 'id') !== operationId ||
    rev === undefined ||
    status === undefined ||
    classifyStatus(status) === 'unknown'
  )
    return null;
  return { id: operationId, revision: rev, status, error: own(raw, 'error'), raw };
}

const UNVERIFIED_REASONS: readonly string[] = [
  'worker_not_verifiable_lease_free',
  'worker_not_verifiable_lease_held',
  'worker_not_verifiable_lease_unreadable',
  'lease_held_after_stop',
  'lease_unreadable_after_stop',
];

export type StopState = 'stopped' | 'released' | 'stopping' | 'unverified' | 'ended' | 'active';

/** backend/control/operations.py stop_state (r4 and r6): reason is null except unverified. */
function readStopState(stop: unknown): StopState | null {
  const state = own(stop, 'state');
  const reason = own(stop, 'reason');
  if (state === 'unverified')
    return typeof reason === 'string' && UNVERIFIED_REASONS.includes(reason) ? state : null;
  return (state === 'stopped' ||
    state === 'released' ||
    state === 'stopping' ||
    state === 'ended' ||
    state === 'active') &&
    reason === null
    ? state
    : null;
}

interface StopAnswerDetails {
  readonly operation: StopOperation | null;
  readonly effects: StopEffects | null;
  readonly error: unknown;
  readonly reason: string | null;
  readonly holders: readonly LeaseHolder[];
  readonly holder: unknown;
  readonly stopAgainRevision: number | null;
  readonly turnInputOff: boolean;
}

export type StopAnswerClass =
  | (StopAnswerDetails & {
      readonly kind: StopState;
      readonly endedAttribution: 'this_stop_signalled' | 'unattributed';
    })
  | (StopAnswerDetails & { readonly kind: 'not_applied'; readonly code: string })
  | (StopAnswerDetails & { readonly kind: 'partial'; readonly conflict: unknown })
  | (StopAnswerDetails & { readonly kind: 'action_lock'; readonly keepStop: true })
  | (StopAnswerDetails & { readonly kind: 'unknown' });

/** S-051..S-057. A success reports its past state; current stopped/free still require D-4. */
export function classifyStopAnswer(
  answer: unknown,
  operationId: string,
  claims: readonly RevisionClaim[] = [],
): StopAnswerClass {
  const status = own(answer, 'status');
  const body = own(answer, 'body');
  const stop = own(body, 'stop');
  const operation = matchingOperation(own(body, 'operation'), operationId);
  const effects = stopEffects(own(stop, 'by_this_request'));
  const reason = text(own(stop, 'reason')) ?? null;
  const holder = own(stop, 'holder');
  const active = operation !== null && classifyStatus(operation.status) === 'active';
  const newestRevision =
    operation === null
      ? null
      : stopRevision(operationId, [...claims, ...answerRevisionClaims(body)]);
  const details: StopAnswerDetails = {
    operation,
    effects,
    error: own(body, 'error'),
    reason,
    holders: leaseHolders(holder),
    holder,
    stopAgainRevision: active ? newestRevision : null,
    turnInputOff: false,
  };
  const unknown: StopAnswerClass = {
    ...details,
    kind: 'unknown',
    operation: null,
    effects: null,
    holders: [],
    holder: null,
    reason: null,
    stopAgainRevision: null,
  };
  if (status === 409) {
    const code = own(body, 'code');
    if (code === 'action_lock') return { ...unknown, kind: 'action_lock', keepStop: true };
    if (code === 'stop_outcome_partial')
      return { ...details, kind: 'partial', conflict: own(body, 'conflict') };
    if (
      typeof code === 'string' &&
      [
        'operation_changed',
        'operation_unknown',
        'operation_required',
        'not_cancellable',
        'stop_refused',
      ].includes(code)
    )
      return { ...details, effects: null, kind: 'not_applied', code };
    return unknown;
  }
  if (status !== 200 || own(body, 'ok') !== true || operation === null) return unknown;
  const state = readStopState(stop);
  if (state === null) return unknown;
  if (
    (state === 'active' && !STOP_STATE_ACTIVE_STATUSES.includes(operation.status)) ||
    ((state === 'stopping' || state === 'unverified') && operation.status !== 'cancelling') ||
    (state === 'stopped' && operation.status !== 'cancelled') ||
    (state === 'released' && operation.status !== 'interrupted') ||
    (state === 'ended' && classifyStatus(operation.status) !== 'final')
  )
    return unknown;
  return {
    ...details,
    kind: state,
    turnInputOff: state === 'unverified' && reason !== 'worker_not_verifiable_lease_free',
    endedAttribution:
      state === 'ended' && effects !== null && effects.signalled.length > 0
        ? 'this_stop_signalled'
        : 'unattributed',
  };
}

/** S-056: unknown answers offer Stop again only after a later D-4 read names this active id. */
export function stopAgainAfterRead(
  operationId: string,
  read: StatusRead,
  after: number,
  claims: readonly RevisionClaim[] = [],
): number | null {
  const gate = evidenceGate(read, after);
  if (gate.kind === 'unknown') return null;
  const ownership = gate.value.ownership;
  const statuses: unknown[] = [];
  if (ownership.operation?.id === operationId) statuses.push(ownership.operation.status);
  if (ownership.owner?.kind === 'operation' && ownership.owner.operationId === operationId)
    statuses.push(own(ownership.owner.raw, 'status'));
  for (const row of ownership.processes) {
    if (row.operationId === operationId) statuses.push(ownPath(row.raw, 'operation', 'status'));
  }
  if (
    !statuses.some((status) => classifyStatus(status) === 'active') ||
    statuses.some((status) => classifyStatus(status) === 'final')
  )
    return null;
  return stopRevision(operationId, [
    ...claims,
    ...statusRevisionClaims(read.outcome.kind === 'answered' ? read.outcome.body : undefined),
  ]);
}
