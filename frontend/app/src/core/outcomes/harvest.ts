import { own, revision, stringList, text } from '../read.ts';
import { harvestView, type ReadableHarvest } from '../readability.ts';
import { opaqueToken, sameToken, type OpaqueToken } from '../tokens.ts';

export interface PauseReceipt {
  readonly requested: boolean;
  readonly stopped: readonly string[];
  readonly stopping: readonly string[];
  readonly errors: readonly string[];
  /** A past answer claim, never evidence about what is running now. */
  readonly noCollectorWasRunning: boolean;
}

export type HarvestAnswerClass =
  | { readonly kind: 'turned_on'; readonly revision: number; readonly replayed: boolean }
  | {
      readonly kind: 'paused';
      readonly revision: number;
      readonly stop: PauseReceipt;
      readonly replayed: boolean;
    }
  | {
      readonly kind: 'pause_unconfirmed';
      readonly revision: number | null;
      readonly reason: string;
    }
  | {
      readonly kind: 'not_applied';
      readonly code: string;
      readonly error: unknown;
      readonly recovery: 'read' | 'try_again';
      readonly keepPause: boolean;
    }
  | {
      readonly kind: 'unknown';
      readonly code: string | null;
      readonly error: unknown;
      readonly keepKey: true;
    };

/** S-004, S-010, S-015, S-029. Answers are receipts, not current-state evidence. */
export function classifyHarvestAnswer(
  answer: unknown,
  request: 'pause' | 'turn_on',
): HarvestAnswerClass {
  const status = own(answer, 'status');
  const body = own(answer, 'body');
  const code = text(own(body, 'code')) ?? null;
  const error = own(body, 'error');
  if (status === 409 && code === 'action_lock') {
    return {
      kind: 'not_applied',
      code,
      error,
      recovery: 'try_again',
      keepPause: request === 'pause',
    };
  }
  if (
    (status === 400 && code === 'invalid_request') ||
    (status === 409 &&
      code !== null &&
      [
        'harvest_revision_changed',
        'context_changed',
        'policy_invalid',
        'idempotency_key_reused',
      ].includes(code))
  ) {
    return { kind: 'not_applied', code, error, recovery: 'read', keepPause: request === 'pause' };
  }
  if (status !== 200 || own(body, 'ok') !== true) {
    return { kind: 'unknown', code, error, keepKey: true };
  }
  const desired = own(body, 'desired');
  const rev = revision(own(desired, 'revision'));
  if (request === 'turn_on') {
    return own(desired, 'choice') === 'on' && rev !== undefined
      ? { kind: 'turned_on', revision: rev, replayed: own(body, 'replayed') === true }
      : { kind: 'unknown', code, error, keepKey: true };
  }
  const stop = own(body, 'stop');
  const requested = own(stop, 'requested');
  const stopped = stringList(own(stop, 'stopped'));
  const stopping = stringList(own(stop, 'stopping'));
  const errors = stringList(own(stop, 'errors'));
  if (
    own(desired, 'choice') !== 'off' ||
    own(desired, 'enabled') !== false ||
    rev === undefined ||
    typeof requested !== 'boolean' ||
    stopped === undefined ||
    stopping === undefined ||
    errors === undefined
  ) {
    return { kind: 'pause_unconfirmed', revision: rev ?? null, reason: 'stop_not_in_contract' };
  }
  return {
    kind: 'paused',
    revision: rev,
    replayed: own(body, 'replayed') === true,
    stop: {
      requested,
      stopped,
      stopping,
      errors,
      noCollectorWasRunning:
        requested && stopped.length === 0 && stopping.length === 0 && errors.length === 0,
    },
  };
}

/** Section 7.4: the caller fences the read to the request's context and sequence. */
export function settleLostTurnOn(
  harvest: ReadableHarvest,
  expectedRevision: number,
  requestKey: OpaqueToken,
): 'applied' | 'not_applied' | 'unknown' {
  const view = harvestView(harvest);
  if (revision(expectedRevision) === undefined || view.revision <= expectedRevision)
    return 'unknown';
  return view.revision === expectedRevision + 1 &&
    sameToken(opaqueToken(own(view.desired, 'request_key')), requestKey)
    ? 'applied'
    : 'not_applied';
}
