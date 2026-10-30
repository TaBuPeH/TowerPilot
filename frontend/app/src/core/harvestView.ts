import { tapsSetting, type ConfigRead } from './config.ts';
import type { ConnectionLine } from './connection.ts';
import { collectionGuard, type CollectionGuardPermit, type GuardRefusal } from './guards.ts';
import {
  collectionEvidence,
  gemOutcomes,
  operationPublication,
  ownerPublication,
  runGemReport,
  type GemOutcomes,
} from './collection.ts';
import { confirmCollecting, confirmIdle } from './evidence.ts';
import { gateOf } from './gate.ts';
import {
  choiceView,
  pauseOffered,
  type ChoiceView,
  type KnownOn,
  type UnsettledOn,
} from './harvestChoice.ts';
import { finite, id, own, pid, record, text } from './read.ts';
import { harvestView } from './readability.ts';
import { drawnRead, staleAge, type StatusContext } from './statusContext.ts';
import { decide, observed, type ChipDecision } from './vocabulary.ts';

/**
 * The Gem harvesting slot (spec 3.6.2, safety rules S-030, S-034, S-035,
 * S-037, S-104): Seen now from the first matching row of the gem table, Your
 * choice from D-1 alone (S-034: two separate facts), the cause when
 * harvesting can't act (S-037) and whether Turn on may be offered (S-104).
 */

export type GemRow =
  | 'G1'
  | 'G2'
  | 'G3a'
  | 'G3b'
  | 'G3c'
  | 'G3d'
  | 'G3e'
  | 'G3f'
  | 'G3g'
  | 'G4a'
  | 'G4b'
  | 'G4c'
  | 'G4d'
  | 'G5a'
  | 'G5b'
  | 'G5c'
  | 'G5d'
  | 'G5e'
  | 'G5f'
  | 'G5g';

export type GemLine =
  | 'harvest_unreadable'
  | 'last_read'
  | 'collecting_gems'
  | 'claims_failing'
  | 'gem_routines_off'
  | 'stop_not_confirmed'
  | 'starting'
  | 'waiting_to_track_battle'
  | 'collector_state_unknown'
  | 'run_collects_gems'
  | 'run_not_collecting'
  | 'another_process_holds'
  | 'run_gems_unknown'
  | 'handing_over'
  | 'collector_start_due'
  | 'nothing_starts_collector'
  | 'harvest_scheduler_failing'
  | 'blocked_cause'
  | 'retrying_in'
  | 'retry_time_not_reported'
  | 'no_gem_collection'
  | 'state_unknown';

export type GemDecision = ChipDecision<GemRow, GemLine>;

/** The first matching row of the gem table (spec 3.6.2). */
export function gemSeen(context: StatusContext): GemDecision {
  const read = drawnRead(context);
  if (read === null) {
    return decide('G1', 'unknown', 'harvest_unreadable', null, { reason: 'not_read' });
  }
  if (read.harvest.kind === 'unknown') {
    return decide('G1', 'unknown', 'harvest_unreadable', null, { reason: read.harvest.reason });
  }
  const age = staleAge(context);
  if (age !== null) {
    return decide('G2', 'unknown', 'last_read', null, { ageSec: age });
  }
  const harvest = read.harvest.value;
  const view = harvestView(harvest);
  const operation = view.operation;
  if (record(operation) !== undefined) {
    // The collector's state is the backend's published `code`; it is read, not recomputed (invariant 11).
    // A collecting code still needs the fresh publication D-6 proves before it is shown (S-028).
    const code = view.code;
    if (code === 'collecting' || code === 'claims_failing') {
      const collecting = collectionEvidence(harvest);
      const claim = confirmCollecting(harvest);
      if (collecting.kind === 'known' && claim.kind === 'known') {
        return code === 'claims_failing'
          ? decide('G3g', 'failed', 'claims_failing', 'EV-2', {
              streak: gemOutcomes(harvest, 'operation')?.failureStreak ?? null,
            })
          : observed('G3a', 'running', 'collecting_gems', claim.value, {
              collectOnly: collecting.value.startedByCollectOnly,
            });
      }
    }
    if (
      code === 'gem_routines_paused' &&
      operationPublication(harvest) !== null &&
      view.inputRevoked === false
    ) {
      return decide('G3b', 'paused', 'gem_routines_off', 'EV-2');
    }
    if (
      code === 'collector_stopping' ||
      code === 'harvest_off' ||
      code === 'harvest_changed' ||
      code === 'policy_invalid'
    ) {
      return decide('G3c', 'stopping', 'stop_not_confirmed', null);
    }
    if (code === 'collector_starting') {
      return decide('G3d', 'waiting', 'starting', null);
    }
    if (code === 'between_battles' || code === 'waiting_for_battle') {
      return decide('G3e', 'waiting', 'waiting_to_track_battle', null);
    }
    return decide('G3f', 'unknown', 'collector_state_unknown', null);
  }
  const owner = view.owner;
  if (record(owner) !== undefined) {
    if (runGemReport(harvest).kind === 'known') {
      return decide('G4a', 'running', 'run_collects_gems', 'EV-4');
    }
    if (
      own(owner, 'kind') === 'operation' &&
      own(owner, 'gathers') === false &&
      own(own(owner, 'gems'), 'basis') === 'published_routines' &&
      ownerPublication(harvest) !== null
    ) {
      return decide('G4b', 'waiting', 'run_not_collecting', 'EV-2');
    }
    if (own(owner, 'kind') === 'process') {
      return decide('G4c', 'waiting', 'another_process_holds', null, {
        foreign: own(owner, 'foreign') === true,
        pid: pid(own(owner, 'pid')) ?? null,
      });
    }
    return decide('G4d', 'unknown', 'run_gems_unknown', null);
  }
  const host = view.host;
  switch (view.state) {
    case 'transferring':
      return decide('G5a', 'waiting', 'handing_over', null);
    case 'starting': {
      const lastError = own(host, 'last_error');
      if (own(host, 'reconciler') === 'active' && (lastError === null || lastError === undefined)) {
        return decide('G5b', 'waiting', 'collector_start_due', null, { code: 'launch_due' });
      }
      return own(host, 'reconciler') === 'active'
        ? decide('G5c', 'blocked', 'harvest_scheduler_failing', null)
        : decide('G5c', 'blocked', 'nothing_starts_collector', null);
    }
    case 'blocked':
      return decide('G5d', 'blocked', 'blocked_cause', null, { code: view.code });
    case 'backing_off': {
      const next = finite(view.nextAttemptAt);
      return next === undefined
        ? decide('G5e', 'waiting', 'retry_time_not_reported', null)
        : decide('G5e', 'waiting', 'retrying_in', null, {
            seconds: Math.max(0, next - view.serverTime),
          });
    }
    case 'off': {
      const gate = gateOf(read, null);
      const idle = gate.kind === 'known' ? confirmIdle(gate.value, context.leaseHolders) : gate;
      if (view.choice !== 'on' && idle.kind === 'known') {
        return observed('G5f', 'idle', 'no_gem_collection', idle.value);
      }
      return decide('G5g', 'unknown', 'state_unknown', null, { state: view.state });
    }
    default:
      return decide('G5g', 'unknown', 'state_unknown', null, { state: view.state });
  }
}

/** S-037: why harvesting can't act, as a code (null when it can or the reason is unknown). */
export type GemCause =
  | { readonly kind: 'reconciler_inactive' }
  | { readonly kind: 'reconciler_failing'; readonly error: unknown }
  | { readonly kind: 'blocked'; readonly code: string | null; readonly message: unknown }
  | { readonly kind: 'backing_off'; readonly retryInSec: number | null }
  | { readonly kind: 'another_process' }
  | {
      readonly kind: 'source_invalid';
      readonly code: string | null;
      readonly edit: unknown;
      /** `source.message`, shown as sent (F-025). */
      readonly message: unknown;
    };

/**
 * S-037: when harvesting can't act, its cause is stated (reconciler inactive
 * or failing - from `host`, even while `state` reads starting - circuit open,
 * backing off with the time from server values only, setup guard, taps
 * disabled, input revoked, emulator offline, policy invalid), with the next
 * step where the record gives one (`source.edit`). Never a collecting claim.
 */
export function gemCause(context: StatusContext, decision: GemDecision): GemCause | null {
  const read = drawnRead(context);
  if (read === null || read.harvest.kind === 'unknown') {
    return null;
  }
  const view = harvestView(read.harvest.value);
  switch (decision.row) {
    case 'G5c':
      return decision.line === 'nothing_starts_collector'
        ? { kind: 'reconciler_inactive' }
        : { kind: 'reconciler_failing', error: own(view.host, 'last_error') };
    case 'G5d': {
      if (own(view.source, 'valid') === false) {
        return {
          kind: 'source_invalid',
          code: text(own(view.source, 'code')) ?? view.code,
          edit: own(view.source, 'edit'),
          message: own(view.source, 'message'),
        };
      }
      return { kind: 'blocked', code: view.code, message: view.message };
    }
    case 'G5e': {
      const next = finite(view.nextAttemptAt);
      return {
        kind: 'backing_off',
        retryInSec: next === undefined ? null : Math.max(0, next - view.serverTime),
      };
    }
    case 'G4c':
      return { kind: 'another_process' };
    default:
      return null;
  }
}

/** Why Turn on is not offered: the first F-027 prerequisite that fails, in this order. */
export type TurnOnReason =
  | 'choice_unknown'
  | 'request_in_flight'
  | 'request_unresolved'
  | 'context_unknown'
  | 'status_incomplete'
  | 'not_connected'
  | 'taps_off'
  | 'taps_unknown'
  | 'source_missing'
  | 'source_invalid'
  | 'guard';

/**
 * The F-027 prerequisites that are not in the status read itself, as the
 * store holds them; core judges each.
 */
export interface TurnOnFacts {
  /** The latest config read: taps must read allowed for the active instance. */
  readonly config: ConfigRead | null;
  /** The connection line core decided for the read the person saw (C1-C4): only `connected` passes. */
  readonly connection: ConnectionLine;
  /**
   * The newest harvest request of this context: `sending`, `unresolved` (its
   * outcome unknown and no later read settled it) or `none`.
   */
  readonly harvestRequest: 'none' | 'sending' | 'unresolved';
  /**
   * The person chose a run whose own saved settings were read and collect
   * (F-026): a server record without a resolved source does not block Turn on
   * then; an invalid resolved source still does.
   */
  readonly pickedSourceCollects?: boolean;
}

/**
 * F-027 (S-104, S-100, S-032, S-010): Turn on is offered only with a readable
 * revision, no unresolved harvest request, a D-4 read, the emulator connected,
 * taps allowed, a valid source and a permitting collection guard.
 */
export type TurnOnView =
  | {
      readonly offered: true;
      readonly expectedRevision: number;
      readonly permit: CollectionGuardPermit;
      /** S-104: "the source changed since On" qualifier (`source.changed_since_enable`). */
      readonly sourceChanged: boolean;
    }
  | {
      readonly offered: false;
      readonly reason: TurnOnReason;
      readonly guard: GuardRefusal | null;
      readonly sourceEdit: unknown;
    };

function refused(reason: TurnOnReason, sourceEdit?: unknown): TurnOnView {
  return { offered: false, reason, guard: null, sourceEdit };
}

/**
 * F-027, S-104, S-100, S-032, S-010: Turn on needs the revision of the read
 * the person saw (D-1), no harvest request of this context in flight or
 * unresolved, that read passing D-4 (fresh scan, complete ownership), the
 * emulator connected (C2), taps allowed in the latest config read, a
 * validated source (`source.valid === true`) and a CollectionGuardPermit
 * judged from `collection_guard` alone; the status root's guard is passed in.
 * Compare-and-set protects the revision only; it proves none of the others.
 */
export function turnOnView(
  context: StatusContext,
  rootCollectionGuard: unknown,
  facts: TurnOnFacts,
): TurnOnView {
  const read = drawnRead(context);
  if (read === null || read.harvest.kind === 'unknown' || staleAge(context) !== null) {
    return refused('choice_unknown');
  }
  if (facts.harvestRequest === 'sending') return refused('request_in_flight');
  if (facts.harvestRequest === 'unresolved') return refused('request_unresolved');
  const view = harvestView(read.harvest.value);
  const source = view.source;
  if (id(own(view.identity, 'instance')) === undefined) {
    return refused('context_unknown', own(source, 'edit'));
  }
  if (gateOf(read, null).kind !== 'known') return refused('status_incomplete', own(source, 'edit'));
  if (facts.connection !== 'connected') return refused('not_connected', own(source, 'edit'));
  const taps = tapsSetting(facts.config);
  if (taps.kind !== 'known') return refused('taps_unknown', own(source, 'edit'));
  if (!taps.allowTaps) return refused('taps_off', own(source, 'edit'));
  if (record(source) === undefined && facts.pickedSourceCollects !== true) {
    return refused('source_missing');
  }
  if (
    record(source) !== undefined &&
    (own(source, 'valid') !== true || id(own(source, 'preset')) === undefined)
  ) {
    return refused('source_invalid', own(source, 'edit'));
  }
  const guard = collectionGuard(rootCollectionGuard, view.serverTime);
  if (guard.kind === 'refused') {
    return {
      offered: false,
      reason: 'guard',
      guard: guard.refusal,
      sourceEdit: own(source, 'edit'),
    };
  }
  return {
    offered: true,
    expectedRevision: view.revision,
    permit: guard.permit,
    sourceChanged: own(source, 'changed_since_enable') === true,
  };
}

/** The outcomes line is for the rows that say gems are being handled: the collector's (G3a, G3g) or a run's own (G4a). */
function outcomesOf(read: ReturnType<typeof drawnRead>, seen: GemDecision): GemOutcomes | null {
  if (read === null || read.harvest.kind === 'unknown') {
    return null;
  }
  switch (seen.row) {
    case 'G3a':
    case 'G3g':
      return gemOutcomes(read.harvest.value, 'operation');
    case 'G4a':
      return gemOutcomes(read.harvest.value, 'owner');
    default:
      return null;
  }
}

/** The Gem harvesting slot's view: Your choice and Seen now as separate facts (S-034). */
export interface GemSlotView {
  readonly choice: ChoiceView;
  readonly seen: GemDecision;
  readonly pause: boolean;
  readonly cause: GemCause | null;
  /** This battle's confirmations, missed windows and latest miss, when the row shows a collector's or run's gems. */
  readonly outcomes: GemOutcomes | null;
}

/**
 * S-034: desired and observed are separate facts: the pill comes from D-1
 * alone (core/harvestChoice.ts), the chip from the gem table alone; a manual
 * collector (choice unset, collecting) is Not set beside Running, never "On".
 */
export function gemSlot(
  context: StatusContext,
  known: KnownOn | null,
  unsettled: readonly UnsettledOn[],
): GemSlotView {
  const read = drawnRead(context);
  const seen = gemSeen(context);
  const harvest = read === null ? null : read.harvest;
  return {
    choice:
      harvest === null
        ? { word: 'unknown', qualifier: null, reason: 'not_read' }
        : choiceView(harvest, known),
    seen,
    pause: pauseOffered(harvest, known, unsettled),
    cause: gemCause(context, seen),
    outcomes: outcomesOf(read, seen),
  };
}
