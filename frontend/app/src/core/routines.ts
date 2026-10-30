import {
  BATTLE_REPORT_MAX_AGE_SEC,
  battleView,
  freshBattleReport,
  recordDecision,
  type BattleIdentity,
  type RecordDecision,
} from './battle.ts';
import { compatClaimLine, isShoppingAction, isShoppingVariant, type ActionKey } from './actions.ts';
import { commandTerminal, commandView, payloadPaths, type CommandView } from './commands.ts';
import { planHolds, queuePlan } from './shoppingPlan.ts';
import type { ContextRead } from './connection.ts';
import { finite, hasOwn, id, list, own, ownPath, record, revision, stringList } from './read.ts';
import type { DiagnosticQuery } from './runtime.ts';
import { opaqueToken, sameToken, type OpaqueToken } from './tokens.ts';
import { decide, type ChipDecision } from './vocabulary.ts';

export type RoutineTiming = (ChipDecision | RecordDecision) & {
  readonly nextInSec: number | null;
  readonly windowEndsAt: number | null;
  readonly blocker: unknown;
  readonly state: unknown;
  readonly savedEnabled: unknown;
  readonly savedIntervalSec: unknown;
};

/** F-050/F-055: all deadlines are relative to runtime.server_time. */
export function routineTiming(
  read: ContextRead,
  shown: BattleIdentity | null,
  routine: ActionKey,
  elapsedSec = 0,
): RoutineTiming {
  const battle =
    read?.kind === 'answered' && read.sameContext ? own(read.body, 'battle') : undefined;
  const raw = ownPath(battle, 'runtime', 'routines', routine);
  const fields = {
    nextInSec: null,
    windowEndsAt: null,
    blocker: own(raw, 'blocker'),
    state: own(raw, 'state'),
    savedEnabled: ownPath(battle, 'effective', 'routines', routine, 'enabled'),
    savedIntervalSec: ownPath(battle, 'effective', 'routines', routine, 'interval_sec'),
  };
  if (own(battle, 'status') === 'prepared')
    return {
      ...(typeof fields.savedEnabled === 'boolean'
        ? recordDecision(
            'F-050',
            fields.savedEnabled ? 'on_when_wave_starts' : 'prepared_routine_off',
            battle,
          )
        : decide('F-050', 'unknown', 'prepared_setting_unconfirmed', null)),
      ...fields,
    };
  const report = freshBattleReport(read, shown, elapsedSec);
  if (report.kind === 'unknown')
    return { ...decide('F-055', 'unknown', 'old_report', null), ...fields };
  if (record(raw) === undefined)
    return { ...decide('F-055', 'unknown', 'routine_unreported', null), ...fields };
  const windowEndsAt = finite(own(raw, 'window_ends_at'));
  const nextDue = finite(own(raw, 'next_due_at'));
  const now = report.value.runtimeTime + elapsedSec;
  if (windowEndsAt !== undefined && windowEndsAt > now && own(raw, 'enabled') === false)
    return { ...decide('F-055', null, 'paused_until_window', 'EV-9'), ...fields, windowEndsAt };
  if (fields.blocker !== null && fields.blocker !== undefined)
    return {
      ...decide('F-055', 'waiting', 'routine_blocker', 'EV-9'),
      ...fields,
      windowEndsAt: windowEndsAt ?? null,
    };
  const knownState = ['active', 'scheduled', 'off', 'watching', 'boundary_wait'].some(
    (state) => state === fields.state,
  );
  if (!knownState)
    return { ...decide('F-055', 'unknown', 'routine_state_unrecognised', null), ...fields };
  const enabled = own(raw, 'enabled');
  if (typeof enabled !== 'boolean')
    return { ...decide('F-055', 'unknown', 'routine_setting_unconfirmed', null), ...fields };
  return {
    ...decide(
      'F-055',
      null,
      !enabled ? 'routine_off' : nextDue !== undefined ? 'next_due' : 'routine_reported',
      'EV-9',
    ),
    ...fields,
    nextInSec: enabled && nextDue !== undefined ? Math.max(0, nextDue - now) : null,
    windowEndsAt: windowEndsAt ?? null,
  };
}

export interface RoutineResult {
  readonly decision: ChipDecision | RecordDecision;
  readonly confirmed: boolean;
  readonly observedAt: number | null;
  readonly reason: unknown;
  readonly raw: unknown;
  readonly confirmedPurchases: number | null;
}

/** F-057/F-068: result evidence is never converted into a balance or gem count. */
export function routineResult(runtime: unknown, routine: ActionKey): RoutineResult {
  // backend/runtime/battle_session.py publish/_manual_progress: raw paths only.
  const outcome = ownPath(runtime, 'routines', routine, 'last_outcome');
  const routineRecord = ownPath(runtime, 'routines', routine);
  const confirmed = own(outcome, 'confirmed') === true;
  const shopping = isShoppingAction(routine);
  const fields = {
    confirmed,
    observedAt: finite(own(outcome, 'observed_at')) ?? null,
    reason: shopping ? ownPath(outcome, 'last_sweep', 'reason') : own(outcome, 'reason'),
    raw: outcome,
    confirmedPurchases: shopping
      ? (finite(ownPath(outcome, 'last_sweep', 'confirmed_purchases')) ?? null)
      : null,
  };
  if (
    record(routineRecord) === undefined ||
    !hasOwn(routineRecord, 'last_outcome') ||
    (outcome !== null && record(outcome) === undefined)
  )
    return { ...fields, decision: decide('F-057', 'unknown', 'routine_result_unreadable', null) };
  if (outcome === null)
    return { ...fields, decision: recordDecision('F-057', 'no_result_yet', routineRecord) };
  if (shopping)
    return { ...fields, decision: recordDecision('F-068', 'shopping_result_reported', outcome) };
  return {
    ...fields,
    decision: confirmed
      ? recordDecision('F-057', compatClaimLine(routine, fields.reason), outcome)
      : decide('F-057', 'unknown', 'result_unconfirmed', null),
  };
}

export interface ActionGate {
  readonly enabled: boolean;
  readonly reason:
    | 'ready'
    | 'no_tracked_battle'
    | 'battle_not_active'
    | 'action_unsupported'
    | 'report_unavailable'
    | 'control_change_pending'
    | 'another_action_in_progress'
    | 'commands_unreadable'
    | 'earlier_outcome_unknown'
    | 'unsaved_live_queue'
    | 'queue_unavailable';
}

/** F-056: one open action blocks ALL three buttons; controls do not. */
export function actionGate(
  read: ContextRead,
  shown: BattleIdentity | null,
  routine: string,
  earlierOutcomeUnknown: boolean,
  elapsedSec = 0,
  shopping: {
    readonly requireQueue?: boolean;
    readonly pendingControl?: boolean;
    readonly liveDraft?: boolean;
    readonly unresolvedCommand?: boolean;
    readonly pendingAction?: boolean;
  } = {},
): ActionGate {
  const deny = (reason: ActionGate['reason']): ActionGate => ({ enabled: false, reason });
  if (read?.kind !== 'answered' || !read.sameContext) return deny('report_unavailable');
  const battle = own(read.body, 'battle');
  if (battle === null) return deny('no_tracked_battle');
  if (own(battle, 'status') !== 'active') return deny('battle_not_active');
  if (!stringList(ownPath(battle, 'capabilities', 'actions'))?.includes(routine))
    return deny('action_unsupported');
  if (freshBattleReport(read, shown, elapsedSec).kind === 'unknown')
    return deny('report_unavailable');
  const isShopping = isShoppingAction(routine);
  if (
    isShopping &&
    (shopping.requireQueue === true || isShoppingVariant(routine)) &&
    queuePlan(ownPath(battle, 'effective', 'shopping_plan')) === null
  )
    return deny('queue_unavailable');
  if (isShopping && shopping.liveDraft) return deny('unsaved_live_queue');
  if (isShopping && shopping.unresolvedCommand) return deny('earlier_outcome_unknown');
  if (isShopping && shopping.pendingAction) return deny('another_action_in_progress');
  if (isShopping && shopping.pendingControl) return deny('control_change_pending');
  const header = battleView(read, shown, elapsedSec);
  if (header.row !== 'B5') return deny('control_change_pending');
  const commands = list(own(battle, 'commands'));
  if (commands === undefined) return deny('commands_unreadable');
  for (const command of commands) {
    if (commandView(command).decision.row === 'K7') return deny('commands_unreadable');
    if (own(command, 'kind') === 'action' && !commandTerminal(command))
      return deny('another_action_in_progress');
  }
  if (
    isShopping &&
    (shopping.requireQueue === true || isShoppingVariant(routine)) &&
    commands.some((command) => own(command, 'kind') === 'controls' && !commandTerminal(command))
  )
    return deny('control_change_pending');
  if (earlierOutcomeUnknown) return deny('earlier_outcome_unknown');
  return { enabled: true, reason: 'ready' };
}

export type RunNowAnswer =
  | { readonly kind: 'lost' }
  | { readonly kind: 'answered'; readonly status: number; readonly body: unknown };

export type RunNowSettlement =
  | { readonly kind: 'recorded'; readonly command: CommandView }
  | { readonly kind: 'not_recorded'; readonly reason: unknown }
  | { readonly kind: 'unknown'; readonly decision: ChipDecision };

/** F-059/7.4: an HTTP answer settles this request; another action is no proof. */
export function runNowAnswer(answer: RunNowAnswer): RunNowSettlement {
  return commandAnswer(answer, 'action');
}

/**
 * The answer of one battle command request (the actions and live-controls
 * routes share one handler, dashboard.py api_battle_command): 200 with a
 * command of that kind recorded it; 400, 404 or 409 refused it before anything
 * was recorded; every other answer leaves it unknown.
 */
export function commandAnswer(answer: RunNowAnswer, kind: 'action' | 'controls'): RunNowSettlement {
  if (answer.kind === 'answered') {
    if (
      answer.status === 200 &&
      own(answer.body, 'ok') === true &&
      record(own(answer.body, 'command')) !== undefined &&
      id(ownPath(answer.body, 'command', 'id')) !== undefined &&
      ownPath(answer.body, 'command', 'kind') === kind
    )
      return { kind: 'recorded', command: commandView(own(answer.body, 'command')) };
    if ([400, 404, 409].includes(answer.status))
      return { kind: 'not_recorded', reason: own(answer.body, 'error') };
  }
  return { kind: 'unknown', decision: decide('F-059', 'unknown', 'may_have_been_recorded', null) };
}

/** A later row settles only the retained key AND battle/operation/generation. */
export function settleRunNow(
  read: ContextRead,
  shown: BattleIdentity,
  requestKey: OpaqueToken,
): RunNowSettlement {
  return settleCommand(read, shown, requestKey, 'action');
}

/**
 * The first command of `kind` that a read of exactly `shown` (same context, battle,
 * generation and operation) lists and `matches` accepts; undefined when there is none.
 */
function listedCommand(
  read: ContextRead,
  shown: BattleIdentity,
  kind: 'action' | 'controls',
  matches: (command: unknown) => boolean,
): unknown {
  if (read?.kind !== 'answered' || !read.sameContext) return undefined;
  const battle = own(read.body, 'battle');
  if (
    own(battle, 'id') !== shown.id ||
    own(battle, 'generation') !== shown.generation ||
    own(battle, 'operation_id') !== shown.operationId
  )
    return undefined;
  return (list(own(battle, 'commands')) ?? []).find(
    (command) =>
      own(command, 'kind') === kind &&
      own(command, 'battle_id') === shown.id &&
      own(command, 'generation') === shown.generation &&
      own(command, 'operation_id') === shown.operationId &&
      id(own(command, 'id')) !== undefined &&
      revision(own(command, 'revision')) !== undefined &&
      matches(command),
  );
}

/** SQ8: a recorded receipt still fences another attempt until a same-battle read reports it terminal. */
export function commandReceiptPending(
  read: ContextRead,
  shown: BattleIdentity | null,
  answer: unknown,
  kind: 'action' | 'controls',
): boolean {
  const receipt = own(answer, 'command');
  const commandId = id(own(receipt, 'id'));
  if (shown === null || commandId === undefined || own(receipt, 'kind') !== kind) return false;
  const current = listedCommand(read, shown, kind, (command) => own(command, 'id') === commandId);
  return !commandTerminal(current ?? receipt);
}

function keyListed(command: unknown, requestKey: OpaqueToken): boolean {
  return (list(own(command, 'request_keys')) ?? []).some((key) =>
    sameToken(requestKey, opaqueToken(key)),
  );
}

/** A later read settles a battle command only by a command of its kind carrying its key. */
export function settleCommand(
  read: ContextRead,
  shown: BattleIdentity,
  requestKey: OpaqueToken,
  kind: 'action' | 'controls',
): RunNowSettlement {
  const command = listedCommand(read, shown, kind, (found) => keyListed(found, requestKey));
  return command === undefined
    ? { kind: 'unknown', decision: decide('F-059', 'unknown', 'may_have_been_recorded', null) }
    : { kind: 'recorded', command: commandView(command) };
}

/** F6-11-IR-01: what a later read of the change's own battle says its command did. */
export type LiveChangeProgress = 'applied' | 'not_applied' | 'waiting' | 'not_listed';

/** A removed leaf is absent/null only when every reported parent is a readable map. */
function resetPathGone(overrides: unknown, path: string): boolean {
  const parts = path.split('.');
  let parent = overrides;
  for (const [index, key] of parts.entries()) {
    if (record(parent) === undefined) return false;
    const value = own(parent, key);
    if (value === undefined) return true;
    if (index === parts.length - 1) return value === null;
    parent = value;
  }
  return false;
}

/**
 * F6-11-IR-01: a recorded live change (live settings or a reset) is applied only when a read
 * of the battle it was sent to (same battle, generation and operation) lists its command as
 * applied; rejected or expired is not applied; requested or pending is still waiting. The
 * command is the one the change's answer named (its id), else the one carrying its request
 * key. A read of another battle, or one not listing the command, proves nothing.
 */
export function liveChangeProgress(
  read: ContextRead,
  shown: BattleIdentity,
  answer: unknown,
  requestKey: OpaqueToken | null,
  resetPaths: readonly string[] = [],
): LiveChangeProgress {
  const commandId = id(ownPath(answer, 'command', 'id'));
  const command = listedCommand(read, shown, 'controls', (found) =>
    commandId !== undefined
      ? own(found, 'id') === commandId
      : requestKey !== null && keyListed(found, requestKey),
  );
  if (command === undefined) return 'not_listed';
  const view = commandView(command);
  const row = view.decision.row;
  // RL2: an applied reset must also show every removed leaf gone from both maps.
  // A command row alone cannot prove that the reset reached the battle's controls.
  const sentPayload = ownPath(answer, 'command', 'payload') ?? view.payload;
  const removed = new Set([
    ...resetPaths,
    ...payloadPaths(sentPayload).filter(
      (path) => ownPath(sentPayload, ...path.split('.')) === null,
    ),
  ]);
  const battle = read?.kind === 'answered' ? own(read.body, 'battle') : undefined;
  if (
    row === 'K4' &&
    [...removed].some(
      (path) =>
        !view.appliedPaths?.includes(path) ||
        ['overrides', 'applied_overrides'].some((key) => !resetPathGone(own(battle, key), path)),
    )
  )
    return 'waiting';
  const plan =
    ownPath(answer, 'command', 'payload', 'shopping_plan') ?? own(view.payload, 'shopping_plan');
  if (
    row === 'K4' &&
    plan !== undefined &&
    plan !== null &&
    (!view.appliedPaths?.includes('shopping_plan') ||
      !planHolds(
        ownPath(
          read?.kind === 'answered' ? read.body : undefined,
          'battle',
          'effective',
          'shopping_plan',
        ),
        plan,
      ))
  )
    return 'waiting';
  return row === 'K4'
    ? 'applied'
    : row === 'K5' || row === 'K6'
      ? 'not_applied'
      : row === 'K7'
        ? 'not_listed'
        : 'waiting';
}

export interface ShoppingView {
  readonly plan: unknown;
  readonly line: 'queue_for_this_battle' | 'shopping_policy_needs_review';
  readonly queue: readonly { readonly raw: unknown; readonly label: unknown }[];
  readonly cadence: unknown;
  readonly cashReserve: unknown;
  readonly decision: unknown;
  readonly pendingConfirmation: unknown;
  readonly lastConfirmation: unknown;
  readonly reportedAt: number | null;
  readonly warnings: readonly ChipDecision[];
}

/** F-066–F-069: raw paths, full queue, independent protection warnings. */
export function shoppingView(body: unknown): ShoppingView {
  const battle = own(body, 'battle');
  const plan = ownPath(battle, 'effective', 'shopping_plan');
  const outcome = ownPath(battle, 'runtime', 'routines', 'shopping', 'last_outcome');
  const catalog = list(own(body, 'catalog')) ?? [];
  const warnings: ChipDecision[] = [];
  if (own(outcome, 'reserve_supported') === false)
    warnings.push(decide('F-069', 'blocked', 'reserve_not_protected', null));
  if (own(outcome, 'quantity_observation_supported') === false)
    warnings.push(decide('F-069', 'waiting', 'purchase_quantity_unverified', null));
  return {
    plan,
    line: own(plan, 'version') === 2 ? 'queue_for_this_battle' : 'shopping_policy_needs_review',
    queue: (list(own(plan, 'queue')) ?? []).map((raw) => ({
      raw,
      label: own(
        catalog.find(
          (item) => id(own(item, 'id')) !== undefined && own(item, 'id') === own(raw, 'id'),
        ),
        'label',
      ),
    })),
    cadence: own(plan, 'cadence'),
    cashReserve: own(plan, 'cash_reserve'),
    decision: own(outcome, 'decision'),
    pendingConfirmation: own(outcome, 'pending_confirmation'),
    lastConfirmation: own(outcome, 'last_confirmation'),
    reportedAt: finite(ownPath(battle, 'runtime', 'routines', 'shopping', 'server_time')) ?? null,
    warnings,
  };
}

/** F6-11 RL7 (spec F-062): the Ad gems collector's own bookkeeping, as reported. */
export interface AdGemsDetails {
  /** `visible`/`absent` only from an observation still fresh at `now`; else `unknown`. */
  readonly visibility: 'visible' | 'absent' | 'unknown';
  /** Why the visibility is unknown: the server's reason, or a stale observation. */
  readonly visibilityReason: string | null;
  readonly observationStale: boolean;
  readonly firstSeenAt: number | null;
  /**
   * When the offer appeared: after `afterAt`, by its first sighting `byAt`, `gapSec` apart
   * (the observation interval). Null when either end is not reported: the first sighting is
   * not the spawn time.
   */
  readonly appearance: {
    readonly afterAt: number;
    readonly byAt: number;
    readonly gapSec: number;
  } | null;
  readonly lastObservedAt: number | null;
  /** Collection timing as the legacy page showed it (run-workspace.js observationValues). */
  readonly collection: AdCollection;
  readonly nextCheckAt: number | null;
  readonly failedAttempt: {
    readonly reason: string | null;
    readonly observedAt: number | null;
  } | null;
  readonly counters: {
    readonly attempts: number | null;
    readonly confirmed: number | null;
    readonly skips: number | null;
    readonly windowsMissed: number | null;
  };
  readonly durations: {
    readonly schedulingWaitSec: number | null;
    readonly responseDelaySec: number | null;
    readonly verificationSec: number | null;
  };
}

export type AdCollection =
  | { readonly kind: 'off' }
  | {
      readonly kind: 'window';
      readonly responseDueAt: number | null;
      readonly windowEndsAt: number;
    }
  | { readonly kind: 'scheduled'; readonly nextDueAt: number | null };

/**
 * RL7: the Ad gems details of `battle.runtime.routines.ad_gems` at `now` (server seconds:
 * the report's runtime time plus the time since it arrived). An observation counts as
 * fresh within the collector's own `stale_after_sec`, else the battle report's limit.
 */
export function adGemsDetails(raw: unknown, now: number | null): AdGemsDetails {
  const at = (key: string) => finite(own(raw, key)) ?? null;
  const lastObservedAt = at('last_observed_at');
  const staleAfter = finite(own(raw, 'stale_after_sec')) ?? BATTLE_REPORT_MAX_AGE_SEC;
  const fresh = lastObservedAt !== null && now !== null && now - lastObservedAt <= staleAfter;
  const reported = own(raw, 'visibility');
  const visibility =
    fresh && (reported === 'visible' || reported === 'absent') ? reported : 'unknown';
  const failed = own(raw, 'last_failed_attempt');
  const count = (key: string) => finite(ownPath(raw, 'counters', key)) ?? null;
  const seconds = (key: string) => finite(ownPath(raw, 'durations', key)) ?? null;
  const firstSeenAt = at('first_seen_at');
  const appearanceAfterAt = at('appearance_after_at');
  return {
    visibility,
    visibilityReason: visibility !== 'unknown' ? null : (id(own(raw, 'visibility_reason')) ?? null),
    observationStale: lastObservedAt !== null && !fresh,
    firstSeenAt,
    appearance:
      firstSeenAt === null || appearanceAfterAt === null
        ? null
        : {
            afterAt: appearanceAfterAt,
            byAt: firstSeenAt,
            gapSec: Math.max(0, firstSeenAt - appearanceAfterAt),
          },
    lastObservedAt,
    collection: adCollection(raw),
    nextCheckAt: at('next_check_at'),
    failedAttempt:
      record(failed) === undefined
        ? null
        : {
            reason: id(own(failed, 'reason')) ?? id(own(failed, 'outcome')) ?? null,
            observedAt: finite(own(failed, 'observed_at')) ?? null,
          },
    counters: {
      attempts: count('attempts'),
      confirmed: count('confirmed'),
      skips: count('skips'),
      windowsMissed: count('windows_missed'),
    },
    durations: {
      schedulingWaitSec: seconds('scheduling_wait_sec'),
      responseDelaySec: seconds('response_delay_sec'),
      verificationSec: seconds('verification_sec'),
    },
  };
}

function adCollection(raw: unknown): AdCollection {
  const manual = own(raw, 'manual_active') === true || own(raw, 'manual_pending') === true;
  if (own(raw, 'enabled') === false && !manual) return { kind: 'off' };
  const windowEndsAt = finite(own(raw, 'window_ends_at'));
  return windowEndsAt === undefined
    ? { kind: 'scheduled', nextDueAt: finite(own(raw, 'next_due_at')) ?? null }
    : {
        kind: 'window',
        responseDueAt: finite(own(raw, 'response_due_at')) ?? null,
        windowEndsAt,
      };
}

/** RL7: Runtime events of this battle (and this routine's offer), from a minute before its first sighting. */
export function routineEventsQuery(battle: unknown, routine: unknown): DiagnosticQuery {
  const query: Record<string, string> = {};
  const battleId = id(own(battle, 'id'));
  const operationId = id(own(battle, 'operation_id'));
  const offerId = id(own(routine, 'offer_id'));
  const firstSeen = finite(own(routine, 'first_seen_at'));
  if (battleId !== undefined) query['battle_id'] = battleId;
  if (operationId !== undefined) query['operation_id'] = operationId;
  if (offerId !== undefined) query['offer_id'] = offerId;
  if (firstSeen !== undefined) query['since'] = String(Math.max(0, firstSeen - 60));
  return query;
}

/** RL7 (spec F-063): Floating gems' latest refusal, as reported (null: none recorded). */
export function floatingRefusal(
  raw: unknown,
): { readonly reason: string | null; readonly observedAt: number | null } | null {
  const refusal = own(raw, 'last_refusal');
  return record(refusal) === undefined
    ? null
    : {
        reason: id(own(refusal, 'reason')) ?? null,
        observedAt: finite(own(refusal, 'observed_at')) ?? null,
      };
}
