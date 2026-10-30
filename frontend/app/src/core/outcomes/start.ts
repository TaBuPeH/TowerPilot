import { classifyStatus } from '../operationStatus.ts';
import { finite, hasOwn, id, own, revision, stringList } from '../read.ts';
import { classifyRefusal, type RefusalClass } from './refusal.ts';

export type StartTransfer =
  | { readonly kind: 'none' | 'unconfirmed' }
  | {
      readonly kind: 'handover';
      readonly stoppedAtHandover: readonly string[];
      readonly wasStoppingAtHandover: readonly string[];
      readonly waitedSec: number;
    };

function transferView(body: unknown): StartTransfer {
  if (!hasOwn(body, 'transfer')) return { kind: 'none' };
  const transfer = own(body, 'transfer');
  const stopped = stringList(own(transfer, 'stopped'));
  const stopping = stringList(own(transfer, 'stopping'));
  const waitedSec = finite(own(transfer, 'waited_sec'));
  if (
    stopped === undefined ||
    stopping === undefined ||
    waitedSec === undefined ||
    waitedSec < 0 ||
    [...stopped, ...stopping].some((value) => value === '') ||
    new Set([...stopped, ...stopping]).size !== stopped.length + stopping.length
  )
    return { kind: 'unconfirmed' };
  return {
    kind: 'handover',
    stoppedAtHandover: stopped,
    wasStoppingAtHandover: stopping,
    waitedSec,
  };
}

export type StartAnswerClass =
  | {
      readonly kind: 'started';
      readonly operationId: string;
      readonly operation: unknown;
      readonly transfer: StartTransfer;
    }
  | {
      readonly kind: 'failed' | 'cancelled' | 'interrupted' | 'succeeded';
      readonly operationId: string;
      readonly operation: unknown;
      readonly error: unknown;
    }
  | { readonly kind: 'owner_refused'; readonly notice: RefusalClass; readonly error: unknown }
  | { readonly kind: 'guard_refused'; readonly guard: unknown; readonly error: unknown }
  | {
      readonly kind: 'context_changed' | 'refused';
      readonly code: unknown;
      readonly error: unknown;
    }
  | {
      readonly kind: 'unknown';
      readonly keepKey: true;
      readonly error: unknown;
      readonly code?: unknown;
    };

/** S-102 / section 7.4: only the answer attributes a start; transfer lists describe handover. */
export function classifyStartAnswer(answer: unknown): StartAnswerClass {
  const status = own(answer, 'status');
  const body = own(answer, 'body');
  const error = own(body, 'error');
  if (status === 200 && own(body, 'ok') === true) {
    const operation = own(body, 'operation');
    const operationId = id(own(body, 'operation_id'));
    const operationStatus = own(operation, 'status');
    if (
      operationId !== undefined &&
      own(operation, 'id') === operationId &&
      revision(own(operation, 'revision')) !== undefined
    ) {
      if (classifyStatus(operationStatus) === 'active') {
        return { kind: 'started', operationId, operation, transfer: transferView(body) };
      }
      if (
        operationStatus === 'failed' ||
        operationStatus === 'cancelled' ||
        operationStatus === 'interrupted' ||
        operationStatus === 'succeeded'
      ) {
        return { kind: operationStatus, operationId, operation, error: own(operation, 'error') };
      }
    }
  }
  if (typeof status === 'number' && Number.isInteger(status) && status >= 400 && status < 500) {
    const code = own(body, 'code');
    const notice = classifyRefusal(body);
    if (notice.kind === 'busy') return { kind: 'unknown', keepKey: true, error, code };
    if (notice.kind !== 'not_owner_notice') return { kind: 'owner_refused', notice, error };
    if (code === 'context_changed') return { kind: 'context_changed', code, error };
    const guard = own(body, 'setup_battle_guard') ?? own(body, 'collection_guard');
    if (own(guard, 'blocks_launch') === true) return { kind: 'guard_refused', guard, error };
    return { kind: 'refused', code, error };
  }
  return { kind: 'unknown', keepKey: true, error };
}

export type ClearFlagAnswerClass = {
  readonly kind: 'cleared' | 'refused' | 'unknown';
  readonly code: unknown;
  readonly error: unknown;
  readonly resend: false;
};

/** Section 7.4: a lost/5xx clear_flag is never resent or attributed from a later flag read. */
export function classifyClearFlagAnswer(answer: unknown): ClearFlagAnswerClass {
  const status = own(answer, 'status');
  const body = own(answer, 'body');
  const kind =
    status === 200 && own(body, 'ok') === true
      ? 'cleared'
      : typeof status === 'number' && Number.isInteger(status) && status >= 400 && status < 500
        ? 'refused'
        : 'unknown';
  return { kind, code: own(body, 'code'), error: own(body, 'error'), resend: false };
}

export type HandoffAnswerClass =
  | {
      readonly kind: 'recorded';
      readonly handoffId: string;
      readonly created: boolean;
      readonly handoff: unknown;
      readonly collectionGuard: unknown;
      readonly setupBattleGuard: unknown;
    }
  | { readonly kind: 'owner_refused'; readonly notice: RefusalClass; readonly error: unknown }
  | {
      readonly kind: 'refused';
      readonly code: unknown;
      readonly error: unknown;
      readonly guard: unknown;
    }
  | { readonly kind: 'unknown'; readonly reconfirm: true; readonly error: unknown };

/** S-101 / section 7.4: a handoff records collection-only consent; repeating requires a new dialog. */
export function classifyHandoffAnswer(answer: unknown): HandoffAnswerClass {
  const status = own(answer, 'status');
  const body = own(answer, 'body');
  const error = own(body, 'error');
  const handoff = own(body, 'handoff');
  const handoffId = id(own(handoff, 'id'));
  const created = own(body, 'created');
  if (
    status === 200 &&
    own(body, 'ok') === true &&
    handoffId !== undefined &&
    typeof created === 'boolean' &&
    own(handoff, 'scope') === 'collection_only' &&
    own(handoff, 'attestation') === 'new_manual_battle'
  ) {
    return {
      kind: 'recorded',
      handoffId,
      created,
      handoff,
      collectionGuard: own(body, 'collection_guard'),
      setupBattleGuard: own(body, 'setup_battle_guard'),
    };
  }
  if (typeof status === 'number' && Number.isInteger(status) && status >= 400 && status < 500) {
    const notice = classifyRefusal(body);
    if (notice.kind !== 'not_owner_notice') return { kind: 'owner_refused', notice, error };
    return {
      kind: 'refused',
      code: own(body, 'code'),
      error,
      guard: own(body, 'setup_battle_guard'),
    };
  }
  return { kind: 'unknown', reconfirm: true, error };
}
