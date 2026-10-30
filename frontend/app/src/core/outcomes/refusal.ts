import { confirmFree, confirmStopped, type Confirmed } from '../evidence.ts';
import { unknownFact, type Fact } from '../facts.ts';
import { evidenceGate, type StatusRead } from '../gate.ts';
import {
  leaseHolders,
  leaseResolvedBy,
  type KnownLeaseHolder,
  type LeaseHolder,
} from '../lease.ts';
import { ownerIdentity, readOwnerRecord, type OwnerIdentity } from '../ownership.ts';
import { hasOwn, id, list, own, ownPath, pid, record } from '../read.ts';
import {
  answerRevisionClaims,
  statusRevisionClaims,
  stopRevision,
  type RevisionClaim,
} from '../stopRevision.ts';

export type Recovery =
  | { readonly kind: 'pause' }
  | { readonly kind: 'stop'; readonly operationId: string; readonly revision: number | null }
  | { readonly kind: 'retry' }
  | { readonly kind: 'none' };

/** S-070: body recovery only; no retry of the refused request. */
export function refusalRecovery(body: unknown, claims: readonly RevisionClaim[] = []): Recovery {
  const raw = own(body, 'recovery');
  if (own(raw, 'action') === 'pause_harvesting') return { kind: 'pause' };
  const operationId = id(own(raw, 'operation_id'));
  if (own(raw, 'action') === 'stop_operation' && operationId !== undefined) {
    return {
      kind: 'stop',
      operationId,
      revision: stopRevision(operationId, [...claims, ...answerRevisionClaims(body)]),
    };
  }
  return { kind: 'none' };
}

interface RefusalBase {
  readonly error: unknown;
  readonly recovery: Recovery;
}

export type RefusalClass =
  | (RefusalBase & {
      readonly kind: 'operation_owner';
      readonly operationId: string;
      readonly pid: number | null;
      readonly status: unknown;
      readonly operationKind: unknown;
      readonly owner: unknown;
      readonly starting: boolean;
    })
  | (RefusalBase & {
      readonly kind: 'process_owner';
      readonly pid: number;
      readonly script: unknown;
      readonly foreign: boolean;
      readonly owner: unknown;
    })
  | (RefusalBase & {
      readonly kind: 'lease_holder';
      readonly holders: readonly LeaseHolder[];
      readonly unidentified: boolean;
      readonly owner: unknown;
    })
  | (RefusalBase & { readonly kind: 'busy' | 'owner_unreadable' | 'not_owner_notice' });

/** Section 7.5: class follows BODY SHAPE; code is consulted only with no owner key. */
export function classifyRefusal(
  body: unknown,
  claims: readonly RevisionClaim[] = [],
): RefusalClass {
  const error = own(body, 'error');
  const owner = own(body, 'owner');
  const operationId = id(own(owner, 'operation_id'));
  const ownerPid = pid(own(owner, 'pid'));
  if (
    record(owner) !== undefined &&
    own(owner, 'kind') === 'operation' &&
    operationId !== undefined
  ) {
    return {
      kind: 'operation_owner',
      operationId,
      pid: ownerPid ?? null,
      status: own(owner, 'status'),
      operationKind: own(owner, 'operation_kind'),
      owner,
      starting: own(owner, 'status') === 'accepted' && own(owner, 'pid') === null,
      error,
      recovery: refusalRecovery(body, claims),
    };
  }
  if (record(owner) !== undefined && own(owner, 'kind') === 'process') {
    if (ownerPid !== undefined)
      return {
        kind: 'process_owner',
        pid: ownerPid,
        script: own(owner, 'script'),
        foreign: own(owner, 'foreign') === true,
        owner,
        error,
        recovery: { kind: 'none' },
      };
    if (own(owner, 'pid') === null) {
      const holders = leaseHolders(owner);
      return {
        kind: 'lease_holder',
        holders,
        unidentified: holders.length === 0,
        owner,
        error,
        recovery: { kind: 'none' },
      };
    }
  }
  if (!hasOwn(body, 'owner')) {
    if (own(body, 'code') === 'action_lock')
      return { kind: 'busy', error, recovery: { kind: 'none' } };
    if (own(body, 'code') === 'device_owner_unreadable')
      return { kind: 'owner_unreadable', error, recovery: { kind: 'retry' } };
  }
  return { kind: 'not_owner_notice', error, recovery: { kind: 'none' } };
}

/**
 * S-073: usable by the store for EV-5/EV-6. An unnamed lease never resolves.
 * A listed holder resolves only on a later D-4 read without its pid anywhere
 * and without a lease owner (core/lease.ts `leaseResolvedBy`, the one rule).
 */
export function leaseHolderResolved(owner: unknown, read: StatusRead, after: number): boolean {
  const gate = evidenceGate(read, after);
  return gate.kind === 'known' && leaseResolvedBy({ owner, learnedSeq: after }, gate.value);
}

function noticeIdentity(notice: RefusalClass): OwnerIdentity | null {
  if (notice.kind !== 'operation_owner' && notice.kind !== 'process_owner') return null;
  const parsed = readOwnerRecord(notice.owner);
  if (parsed !== null && parsed !== undefined) return ownerIdentity(parsed);
  return notice.kind === 'operation_owner'
    ? { operationId: notice.operationId, pid: notice.pid }
    : { operationId: null, pid: notice.pid };
}

function currentOwner(body: unknown): unknown {
  const harvest = own(body, 'harvest');
  const owner = own(harvest, 'owner');
  if (record(owner) !== undefined) return owner;
  const operation = own(harvest, 'operation');
  const operationId = id(own(operation, 'id'));
  if (operationId !== undefined) {
    return {
      kind: 'operation',
      operation_id: operationId,
      operation_kind: own(operation, 'kind'),
      pid: pid(ownPath(operation, 'worker', 'pid')) ?? null,
      status: own(operation, 'status'),
      recovery: {
        action:
          ownPath(harvest, 'desired', 'choice') === 'on' &&
          own(operation, 'harvest_managed') === true
            ? 'pause_harvesting'
            : 'stop_operation',
        operation_id: operationId,
        expected_revision: own(operation, 'revision'),
      },
    };
  }
  const row = list(own(body, 'processes'))?.[0];
  if (row === undefined) return null;
  const rowId = id(own(row, 'operation_id'));
  return rowId === undefined
    ? { kind: 'process', pid: own(row, 'pid'), script: own(row, 'runner') }
    : {
        kind: 'operation',
        operation_id: rowId,
        pid: own(row, 'pid'),
        status: ownPath(row, 'operation', 'status'),
        recovery: {
          action: 'stop_operation',
          operation_id: rowId,
          expected_revision: own(row, 'operation_revision'),
        },
      };
}

export interface RefusalNoticeAssessment {
  readonly stopped: Fact<Confirmed<'stopped'>>;
  readonly free: Fact<Confirmed<'free'>>;
  readonly canRetry: boolean;
  readonly recovery: Recovery;
  readonly currentOwner: RefusalClass | null;
}

/** S-074: current claims and recovery come from one later D-4 read. */
export function assessRefusalNotice(
  notice: RefusalClass,
  read: StatusRead,
  after: number,
  claims: readonly RevisionClaim[] = [],
  leaseHolders: readonly KnownLeaseHolder[] = [],
): RefusalNoticeAssessment {
  const gate = evidenceGate(read, after);
  const unresolved: RefusalNoticeAssessment = {
    stopped: unknownFact('release_unconfirmed'),
    free: unknownFact('release_unconfirmed'),
    canRetry: false,
    recovery: notice.recovery,
    currentOwner: null,
  };
  if (
    notice.kind !== 'operation_owner' &&
    notice.kind !== 'process_owner' &&
    notice.kind !== 'lease_holder'
  )
    return unresolved;
  if (gate.kind === 'unknown') return unresolved;
  const current = currentOwner(read.outcome.kind === 'answered' ? read.outcome.body : undefined);
  const currentNotice =
    current === null
      ? null
      : classifyRefusal({ owner: current, recovery: own(current, 'recovery') }, [
          ...claims,
          ...statusRevisionClaims(read.outcome.kind === 'answered' ? read.outcome.body : undefined),
        ]);
  const identity = noticeIdentity(notice);
  const pauseConfirmed = notice.recovery.kind !== 'pause' || gate.value.view.choice !== 'on';
  const stopped =
    identity === null
      ? unknownFact('lease_release_has_no_operation')
      : !pauseConfirmed
        ? unknownFact('choice_not_off')
        : confirmStopped(identity, gate.value);
  // A lease-holder notice is itself a lease owner this page knows of (S-073).
  const noticeHolder: KnownLeaseHolder | null =
    notice.kind === 'lease_holder' ? { owner: notice.owner, learnedSeq: after } : null;
  const leaseResolved = noticeHolder === null || leaseResolvedBy(noticeHolder, gate.value);
  const free = confirmFree(
    gate.value,
    noticeHolder === null ? leaseHolders : [...leaseHolders, noticeHolder],
  );
  const released = notice.kind === 'lease_holder' ? leaseResolved : stopped.kind === 'known';
  return {
    stopped,
    free,
    canRetry: released && pauseConfirmed && free.kind === 'known',
    recovery: currentNotice?.recovery ?? (released ? { kind: 'none' } : notice.recovery),
    currentOwner: currentNotice,
  };
}
