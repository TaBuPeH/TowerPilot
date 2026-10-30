import { postEndpoint, type WriteOutcome } from '../client.ts';
import { sameContext, type PageContext } from '../context.ts';
import type { Sequence } from '../sequence.ts';
import type { KeyValueStorage } from '../storage.ts';
import { browserTimers, randomId, type TimerHandle, type Timers } from '../timers.ts';
import type { Transport } from '../transport.ts';
import { ENDPOINTS, type Endpoint, type EndpointId } from '../wire.ts';
import { restoreUnresolved, saveUnresolved } from './persistence.ts';
import { settledPhase } from './phases.ts';
import {
  lateAnswerPossible,
  type IntentDraft,
  type IntentKind,
  type IntentRecord,
  type ReadSettlement,
  type SettledPhase,
} from './types.ts';

/** The phase a read settlement gives (not_applied: nothing was applied by this request). */
const SETTLED_BY_READ: Readonly<Record<ReadSettlement, SettledPhase>> = {
  off_confirmed: 'confirmed',
  applied: 'confirmed',
  not_applied: 'refused',
  stopped: 'confirmed',
  recorded: 'confirmed',
};

/**
 * The write-intent machine (spec 7.4, ADR rule 3, plan R8). The only sender
 * of writes besides the C-20 diagnostics. Its rules:
 *
 *  - One record per click; the body and idempotency key are frozen at the
 *    click and serialized once; a resend sends exactly those bytes.
 *  - Nothing is ever sent on its own: no retry after a timeout, an error or a
 *    lost answer, no send from a read, a render, a restore or a timer. The
 *    only ways a request leaves are `send` (a person's click, through a
 *    command) and `resend` (a person's explicit "send the same request
 *    again"), and `resend` refuses unless the record's outcome is unknown, it
 *    was not replaced by a newer request, its kind offers a resend (7.4 column
 *    "Resend offered") and it belongs to the current context (S-014).
 *  - D-10: a request still unanswered after 15 s becomes `unconfirmed`; its
 *    late answer settles only its own record and only while that record is
 *    still unknown (in flight or unconfirmed).
 *  - S-011: a newer request on the same target replaces older records (their
 *    lines become history; S-003, S-005); a Turn input off also replaces a
 *    Turn on (7.4 "superseded by any Pause or input Off").
 *  - S-004: no queue at all; a revocation never waits behind another write.
 *  - Unresolved records are persisted per context (api/intents/persistence.ts),
 *    dismissed ones included (Dismiss hides only the line, OD-10), and
 *    restored on reload without any send.
 */
export const WRITE_WINDOW_MS = 15000;

/** Kinds whose record a person may send again unchanged (spec 7.4 "Resend offered"). */
export const RESENDABLE: readonly IntentKind[] = [
  'pause',
  'turn_on',
  'start',
  'start_gems',
  'run_now',
  'handoff',
  'live_controls',
  // Recognition (R1, R4, R5): the same key answers the same operation, a lost apply is
  // answered from the operation's record (calibrate_writes *_retry_*, apply_resend_*).
  'calibrate_start',
  'calibrate_observe',
  'calibrate_apply',
];

export interface IntentMachineOptions {
  readonly transport: Transport;
  readonly sequence: Sequence;
  readonly storage: KeyValueStorage | null;
  readonly endpoints?: Readonly<Record<EndpointId, Endpoint>>;
  readonly timers?: Timers;
  readonly newId?: () => string;
  readonly windowMs?: number;
  /** The context and epoch new records are made in (updated with `setContext`). */
  readonly context?: PageContext | null;
  readonly epoch?: number;
}

export type ResendRefusal =
  | 'not_found'
  | 'not_resendable'
  | 'not_unknown'
  | 'superseded'
  | 'other_context'
  | 'sending'
  | 'settled'
  | 'needs_confirmation';

export interface IntentMachine {
  /** Every record, oldest first (restored ones included). */
  readonly snapshot: () => readonly IntentRecord[];
  readonly subscribe: (listener: () => void) => () => void;
  /** A person's click: record it and send it once. */
  readonly send: (draft: IntentDraft) => IntentRecord;
  /**
   * A person's explicit resend of the identical request. A handoff needs a new
   * dialog confirmation (`confirmedAgain`, S-101). Returns why it was refused, or null when sent.
   */
  readonly resend: (
    id: string,
    options?: { readonly confirmedAgain?: boolean },
  ) => ResendRefusal | null;
  /**
   * OD-10: hide the record's line; nothing is sent and no fact is cleared. An unresolved
   * record stays in session storage, marked dismissed, until its outcome is known (R9-01).
   */
  readonly dismiss: (id: string) => void;
  /**
   * The person continues from the newest read although this unresolved record's outcome is
   * not known ("accept the current state"): it stays unknown and journaled, never resent.
   */
  readonly accept: (id: string) => void;
  /**
   * A later read settled an unconfirmed record (spec 7.4 "Settled by"). When its answer can
   * no longer arrive (lost, aborted, reloaded, or answered unknown) it is resolved for good:
   * its phase follows the settlement and it leaves session storage. A record whose window
   * passed without an answer keeps `unconfirmed` (its late answer still settles it, D-10) but
   * carries the settlement, persisted, so no resend is offered for it, here or after a reload
   * (R8-06). Records in flight are left as they are. Never sends anything.
   */
  readonly settleByRead: (id: string, settlement: ReadSettlement) => void;
  /** The page context changed (or became known): new records are made in it. */
  readonly setContext: (context: PageContext | null, epoch: number) => void;
  readonly context: () => PageContext | null;
  /** Stop every window timer (late answers still settle their records). */
  readonly dispose: () => void;
}

interface Send {
  readonly token: number;
  window: TimerHandle | null;
}

export function createIntentMachine(options: IntentMachineOptions): IntentMachine {
  const endpoints = options.endpoints ?? ENDPOINTS;
  const timers = options.timers ?? browserTimers;
  const newId = options.newId ?? randomId;
  const windowMs = options.windowMs ?? WRITE_WINDOW_MS;
  let context: PageContext | null = options.context ?? null;
  let epoch = options.epoch ?? 0;
  let records: readonly IntentRecord[] = Object.freeze([...restoreUnresolved(options.storage)]);
  const latestSend = new Map<string, Send>();
  const listeners = new Set<() => void>();
  let tokens = 0;

  const publish = (next: readonly IntentRecord[]) => {
    records = Object.freeze([...next]);
    saveUnresolved(options.storage, records);
    for (const listener of [...listeners]) listener();
  };

  const update = (id: string, change: (current: IntentRecord) => IntentRecord | null) => {
    const index = records.findIndex((item) => item.id === id);
    const current = records[index];
    if (current === undefined) return;
    const replaced = change(current);
    if (replaced === null) return;
    publish(records.map((item, at) => (at === index ? Object.freeze(replaced) : item)));
  };

  /**
   * F6R-01: the answer of attempt `attempt` (1: the click, 2 and up: a person's resends of
   * the identical request). A receipt from any attempt settles the record. A refusal, or an
   * unknown answer, settles it only when it is the one attempt of a request never resent:
   * a refused resend (the request lock answers before the server looks up the original's
   * receipt) proves nothing about an earlier attempt, so the record keeps its unknown
   * outcome, its body, its key and its resend, with the refusal kept beside it; an older
   * attempt's answer arriving after a newer send leaves the newer attempt to decide.
   */
  const settle = (id: string, token: number, attempt: number, outcome: WriteOutcome) => {
    const send = latestSend.get(id);
    if (outcome.kind === 'lost') {
      // Only the latest send's loss counts; an older send's loss says nothing new.
      if (send?.token !== token) return;
      if (send.window !== null) timers.clearTimeout(send.window);
      update(id, (current) =>
        current.phase !== 'sending'
          ? null
          : {
              ...current,
              phase: 'unconfirmed',
              lostReason: outcome.reason,
              lostSeq: options.sequence.next(),
            },
      );
      return;
    }
    if (send?.token === token && send.window !== null) {
      timers.clearTimeout(send.window);
      send.window = null;
    }
    const answerSeq = options.sequence.next();
    update(id, (current) => {
      // D-10: an answer settles its own record only while that record is still unknown.
      if (current.phase !== 'sending' && current.phase !== 'unconfirmed') return null;
      const answer = {
        status: outcome.answer.status,
        body: outcome.answer.body,
        contract: outcome.answer.contract,
        issues: outcome.answer.issues,
      };
      const phase = settledPhase(current.kind, answer, current.meta);
      const newerSent = attempt < current.sends;
      if (phase === 'refused' && (newerSent || attempt > 1)) {
        return {
          ...current,
          // The newest attempt answered: the request is unknown again, resend offered.
          phase: newerSent ? current.phase : 'unconfirmed',
          attemptRefusal: answer,
        };
      }
      if (phase === 'unconfirmed' && newerSent) return null;
      return {
        ...current,
        phase,
        attemptRefusal: null,
        answer,
        answerSeq,
        answeredAtMs: timers.now(),
        lostReason: null,
        // B-01: the answer is newer evidence than any read that settled the record before it;
        // the store judges it again against reads started after this answer.
        readSettlement: null,
      };
    });
  };

  const post = (item: IntentRecord) => {
    tokens += 1;
    const token = tokens;
    const previous = latestSend.get(item.id);
    if (previous?.window !== null && previous?.window !== undefined) {
      timers.clearTimeout(previous.window);
    }
    const send: Send = { token, window: null };
    send.window = timers.setTimeout(() => {
      send.window = null;
      if (latestSend.get(item.id)?.token !== token) return;
      update(item.id, (current) =>
        current.phase !== 'sending'
          ? null
          : {
              ...current,
              phase: 'unconfirmed',
              lostReason: 'window_expired',
              lostSeq: options.sequence.next(),
            },
      );
    }, windowMs);
    latestSend.set(item.id, send);
    const attempt = item.sends;
    void postEndpoint(
      options.transport,
      endpoints[item.request.endpoint],
      item.request.path,
      item.request.body,
    ).then((outcome) => {
      settle(item.id, token, attempt, outcome);
    });
  };

  return {
    snapshot: () => records,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    send: (draft) => {
      const sentSeq = options.sequence.next();
      const item: IntentRecord = Object.freeze({
        id: newId(),
        kind: draft.kind,
        target: draft.target,
        context: draft.context,
        epoch,
        request: Object.freeze({
          endpoint: draft.endpoint,
          path: draft.path,
          body: JSON.stringify(draft.body),
        }),
        key: draft.key,
        meta: Object.freeze({ ...draft.meta }),
        firstSentSeq: sentSeq,
        sentSeq,
        sends: 1,
        phase: 'sending',
        answer: null,
        answerSeq: null,
        answeredAtMs: null,
        lostReason: null,
        readSettlement: null,
        supersededBySeq: null,
        dismissed: false,
        restored: false,
      });
      const next = records.map((existing) =>
        existing.supersededBySeq === null &&
        sameContext(existing.context, draft.context) &&
        (existing.target === draft.target || draft.supersedes.includes(existing.kind))
          ? Object.freeze({ ...existing, supersededBySeq: sentSeq })
          : existing,
      );
      publish([...next, item]);
      post(item);
      return item;
    },
    resend: (id, resendOptions = {}) => {
      const item = records.find((entry) => entry.id === id);
      if (item === undefined) return 'not_found';
      if (!RESENDABLE.includes(item.kind)) return 'not_resendable';
      if (item.phase === 'sending') return 'sending';
      if (item.phase !== 'unconfirmed') return 'not_unknown';
      if (item.supersededBySeq !== null) return 'superseded';
      if (item.readSettlement !== null) return 'settled';
      if (!sameContext(item.context, context)) return 'other_context';
      if (item.kind === 'handoff' && resendOptions.confirmedAgain !== true) {
        return 'needs_confirmation';
      }
      const sentSeq = options.sequence.next();
      const resent: IntentRecord = Object.freeze({
        ...item,
        sentSeq,
        sends: item.sends + 1,
        phase: 'sending',
        lostReason: null,
        dismissed: false,
      });
      publish(records.map((entry) => (entry.id === id ? resent : entry)));
      post(resent);
      return null;
    },
    dismiss: (id) => {
      update(id, (current) => (current.dismissed ? null : { ...current, dismissed: true }));
    },
    accept: (id) => {
      update(id, (current) =>
        current.accepted === true || current.phase !== 'unconfirmed'
          ? null
          : { ...current, accepted: true },
      );
    },
    settleByRead: (id, settlement) => {
      update(id, (current) => {
        if (current.phase !== 'unconfirmed') return null;
        if (
          current.readSettlement !== null &&
          (current.readSettlement !== settlement || lateAnswerPossible(current))
        )
          return null;
        return lateAnswerPossible(current)
          ? { ...current, readSettlement: settlement }
          : { ...current, phase: SETTLED_BY_READ[settlement], readSettlement: settlement };
      });
    },
    setContext: (next, nextEpoch) => {
      context = next;
      epoch = nextEpoch;
    },
    context: () => context,
    dispose: () => {
      for (const send of latestSend.values()) {
        if (send.window !== null) timers.clearTimeout(send.window);
        send.window = null;
      }
    },
  };
}
