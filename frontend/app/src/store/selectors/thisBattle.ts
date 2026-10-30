import {
  ACTION_RESULTS,
  actionNext,
  actionOutcome,
  actionsReading,
  actionsView,
  COMPAT_ROUTINES,
  fieldType,
  fieldValue,
  isShoppingAction,
  itemRunNowRefusal,
  runNowReason,
  settingFields,
  type ActionCode,
  type ActionEffectWord,
  type ActionItemView,
  type ActionKey,
  type ActionLastView,
  type ActionsView,
} from '../../core/actions.ts';
import { battleIdentity, battleView } from '../../core/battle.ts';
import { commandView, commandViewTerminal, type CommandView } from '../../core/commands.ts';
import { entries, finite, id, list, own, ownPath, revision, text } from '../../core/read.ts';
import {
  actionGate,
  commandReceiptPending,
  routineResult,
  routineTiming,
  runNowAnswer,
  shoppingView,
} from '../../core/routines.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { RunNowCommand } from '../commands.ts';
import { contextIntents } from '../intents.ts';
import { settledByRead } from '../fences.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { chipVM, commandVM, type ChipVM, type CommandVM } from '../viewModels.ts';
import { shoppingLiveScope } from './shoppingLive.ts';
import { selectOutcomes, type OutcomeLineVM } from './outcomes.ts';

export interface RoutineRowVM {
  readonly routine: ActionKey;
  readonly timing: ChipVM;
  readonly nextInSec: number | null;
  readonly windowEndsAt: number | null;
  readonly blocker: ServerText<'message'> | null;
  readonly state: ServerText<'detail'> | null;
  readonly stateCode: string | null;
  readonly savedEnabled: boolean | null;
  readonly savedIntervalSec: number | null;
  readonly resultAgeSec: number | null;
  readonly result: {
    readonly chip: ChipVM;
    readonly confirmed: boolean;
    readonly observedAt: number | null;
    readonly reason: ServerText<'message'> | null;
    readonly confirmedPurchases: number | null;
  };
  readonly runNow: CommandVM<RunNowCommand>;
}

/** A server code for its cell: one this build reads, none sent, or one it does not read (as sent). */
export type ActionCodeVM<K extends string> =
  | { readonly kind: 'known'; readonly code: K }
  | { readonly kind: 'unreported' }
  | { readonly kind: 'unrecognised'; readonly code: ServerText<'id'> | null };

/** AC1 (rule 2): an item's last execution: the result and effect this build reads, its age and reason. */
export interface ActionLastVM {
  readonly result: ActionCodeVM<(typeof ACTION_RESULTS)[number]>;
  readonly effect: ActionCodeVM<ActionEffectWord>;
  readonly ageSec: number | null;
  readonly reason: ServerText<'message'> | null;
  /** Started by a Run now command. */
  readonly manual: boolean;
}

/** AC1/AC5: the Next cell of an item row. */
export type ActionNextVM =
  | { readonly kind: 'off' }
  | { readonly kind: 'running'; readonly endsInSec: number | null }
  | { readonly kind: 'blocker'; readonly code: ServerText<'message'> | null }
  | { readonly kind: 'due' }
  | { readonly kind: 'in'; readonly sec: number }
  | { readonly kind: 'not_reported' }
  | { readonly kind: 'state'; readonly code: 'yielded' | 'blocked' | 'unknown' }
  /** Rule 6: a state this build does not read; the code as sent goes under Why. */
  | { readonly kind: 'unrecognised'; readonly code: ServerText<'id'> | null }
  /** AC5: the projection stopped advancing; the row is what was known `ageSec` ago. */
  | { readonly kind: 'last_known'; readonly ageSec: number }
  /** AC5: a reading that is not current (read-only, unreadable time, or an old battle report). */
  | { readonly kind: 'not_current' }
  /** A prepared battle: nothing is scheduled before its waves start. */
  | { readonly kind: 'prepared' };

/** AC1: one descriptor field other than the switch, with its value in force. */
export interface ActionSettingVM {
  readonly path: string;
  readonly label: ServerText<'name'> | null;
  readonly unit: ServerText<'name'> | null;
  /** A number or On/Off as in force, or null when not reported. */
  readonly value: number | boolean | null;
  /** Rule 6: the field's type when this build does not read it (its value is then not shown). */
  readonly unrecognisedType: ServerText<'id'> | null;
}

/** AC1/AC3: one row of This battle per `runtime.actions` item, in server order. */
export interface ActionRowVM {
  /** The row's key: `<id>/<instance>`. */
  readonly key: string;
  /** The item's routine key (its Run now token), or null when it cannot be addressed. */
  readonly token: string | null;
  readonly label: ServerText<'name'> | null;
  /** The action id as sent, shown when the item has no label. */
  readonly code: ServerText<'id'> | null;
  readonly enabled: boolean | null;
  readonly settings: readonly ActionSettingVM[];
  readonly next: ActionNextVM;
  readonly last: ActionLastVM | null;
  readonly runNow: CommandVM<RunNowCommand>;
  /** The descriptor's own reason when it offers no Run now (`run_now` is a string). */
  readonly runNowServerReason: ServerText<'message'> | null;
  readonly shopping: boolean;
}

export interface ThisBattleVM {
  readonly header: ChipVM;
  /** S5 compatibility rows: only when the battle publishes no `runtime.actions`. */
  readonly routines: readonly RoutineRowVM[];
  /** AC1: one row per `runtime.actions` item, or null when the battle publishes no projection. */
  readonly actions: readonly ActionRowVM[] | null;
  /** AC5: why the action readings are not current, shown once above the rows (null: current). */
  readonly actionsNotice: { readonly reason: string; readonly ageSec: number | null } | null;
  /**
   * The one reason every Run now and Try once button shares, shown once for the region (each
   * disabled button's description points at it); null when they differ or some are enabled.
   */
  readonly sharedReason: string | null;
  readonly commands: readonly BattleCommandVM[];
  readonly noTrackedBattle: boolean;
  readonly battleKey: string;
  /** The battle's run (`blueprint`), for the "Saved settings in Runs" link (Runs brief RUN-12). */
  readonly runKey: string | null;
  readonly label: ServerText<'name'> | null;
  readonly wave: number | null;
  readonly changed: boolean;
  readonly readAgeSec: number | null;
  readonly reportAgeSec: number | null;
  readonly queue: QueueVM;
  readonly outcomes: readonly (OutcomeLineVM & { readonly command: BattleCommandVM | null })[];
  readonly resendAvailable: boolean;
  readonly tryOnce: readonly ShoppingTryOnceVM[];
}

export interface ShoppingTryOnceVM {
  readonly action: string;
  readonly label: ServerText<'name'> | null;
  readonly known: 'shopping' | 'attack' | 'defense' | 'utility' | null;
  readonly button: CommandVM<RunNowCommand>;
}

export interface QueueVM {
  readonly needsReview: boolean;
  readonly mode: string | null;
  readonly cadence: string | null;
  readonly reserve: number | null;
  readonly items: readonly {
    readonly key: string;
    readonly label: ServerText<'name'> | null;
    readonly enabled: boolean;
    readonly tapLimit: number | null;
    readonly weight: number | null;
    readonly tryOnce: ShoppingTryOnceVM | null;
  }[];
  readonly warnings: readonly ChipVM[];
  readonly reportedAgeSec: number | null;
  readonly decision: ServerText<'detail'> | null;
  readonly decisionReason: ServerText<'message'> | null;
  readonly decisionUpgrade: ServerText<'name'> | null;
  readonly pendingUpgrade: ServerText<'name'> | null;
  readonly confirmationUpgrade: ServerText<'name'> | null;
  readonly confirmationOutcome: ServerText<'detail'> | null;
  readonly confirmationReason: ServerText<'message'> | null;
}

/** Each row remains a ChipVM, with the command's dated reason and action evidence. */
export interface BattleCommandVM extends ChipVM {
  readonly key: string;
  readonly action: string | null;
  readonly actionText: ServerText<'id'> | null;
  readonly serverEvidence: ServerText<'detail'> | null;
  readonly shoppingState: 'requested' | 'started' | 'completed' | null;
  readonly resultAgeSec: number | null;
  readonly resultChip: ChipVM | null;
  readonly id: ServerText<'id'> | null;
  readonly revision: number | null;
  readonly requestedAt: number | null;
  readonly updatedAt: number | null;
  readonly reason: ServerText<'message'> | null;
  readonly result: {
    readonly confirmed: boolean;
    readonly observedAt: number | null;
    readonly reason: ServerText<'message'> | null;
    readonly confirmedPurchases: number | null;
  };
  /**
   * AC3: the execution this Run now started, from the item whose `last.command_id` names this
   * command (B4), once the command is finished (K4 to K6); null when no item links it. Never
   * inferred from the command's own state.
   */
  readonly outcome: ActionLastVM | null;
}

function codeVM<K extends string>(code: ActionCode<K>): ActionCodeVM<K> {
  return code.kind === 'unrecognised' ? { kind: 'unrecognised', code: codeText(code.raw) } : code;
}

function lastVM(last: ActionLastView, age: (at: number | null) => number | null): ActionLastVM {
  const outcome = actionOutcome(last);
  return {
    result: codeVM(outcome.result),
    effect: codeVM(outcome.effect),
    ageSec: age(last.finishedAt),
    reason: fromServer('message', last.reason),
    manual: last.trigger === 'manual',
  };
}

/** The raw code as display text (a non-string code shows as JSON; an absent one as nothing). */
function codeText(raw: unknown): ServerText<'id'> | null {
  return fromServer('id', typeof raw === 'string' ? raw : JSON.stringify(raw));
}

/**
 * Project a command read or receipt through the same command and routine rules. With a
 * projection, an action command's outcome is the execution it started (AC3); without one, the
 * S5 compatibility rows read the command's evidence as before.
 */
function commandRow(
  view: CommandView,
  index: number,
  age: (at: number | null) => number | null,
  projection: ActionsView | null,
): BattleCommandVM {
  const action = text(own(view.payload, 'action')) ?? null;
  const routine =
    projection === null ? COMPAT_ROUTINES.find((routine) => routine === action) : undefined;
  const result =
    routine === undefined || view.kind !== 'action'
      ? null
      : routineResult({ routines: { [routine]: { last_outcome: view.evidence } } }, routine);
  const commandId = text(view.id);
  const linked =
    projection === null ||
    view.kind !== 'action' ||
    commandId === undefined ||
    !commandViewTerminal(view)
      ? undefined
      : projection.items.find((item) => item.last?.commandId === commandId)?.last;
  return {
    ...chipVM(view.decision),
    key: text(view.id) ?? String(index),
    action,
    actionText: fromServer('id', action),
    serverEvidence: fromServer(
      'detail',
      view.evidence === null || view.evidence === undefined ? null : JSON.stringify(view.evidence),
    ),
    shoppingState:
      action !== null &&
      isShoppingAction(action) &&
      (view.state === 'requested' || view.state === 'started' || view.state === 'completed')
        ? view.state
        : null,
    resultAgeSec: age(result?.observedAt ?? null),
    resultChip: result === null ? null : chipVM(result.decision),
    id: fromServer('id', view.id),
    revision: view.revision,
    requestedAt: view.requestedAt,
    updatedAt: view.updatedAt,
    reason: fromServer('message', view.reason),
    result: {
      confirmed: result?.confirmed ?? false,
      observedAt: result?.observedAt ?? null,
      reason: fromServer('message', result?.reason),
      confirmedPurchases: result?.confirmedPurchases ?? null,
    },
    outcome: linked === undefined || linked === null ? null : lastVM(linked, age),
  };
}

export function selectThisBattle(state: DashboardState): ThisBattleVM {
  const read = latestRead(state, 'battlesActive');
  const answered = state.reads.battlesActive.lastAnswered;
  const identity = battleIdentity(
    own(answeredBody(state, 'battlesActive'), 'battle'),
    answered === null ? NaN : answered.arrivedAtMs / 1000,
  );
  const shown = identity.kind === 'known' ? identity.value : null;
  const contextRead = contextReadOf(read);
  const elapsed = read === null ? 0 : readAgeSec(state, read);
  const body = read?.outcome.kind === 'answered' ? read.outcome.body : undefined;
  const battle = own(body, 'battle');
  const header = chipVM(battleView(contextRead, shown, elapsed));
  const projection = actionsView(battle);
  const reading =
    contextRead?.kind === 'answered' && contextRead.sameContext
      ? actionsReading(body, elapsed)
      : null;
  // AC5: the compatibility rows (no projection) publish no readings to block on.
  const rowReading = projection === null ? null : reading;
  const intents = contextIntents(state);
  const serverTime = finite(own(body, 'server_time'));
  const age = (timestamp: number | null): number | null =>
    timestamp === null || serverTime === undefined
      ? null
      : Math.max(0, serverTime - timestamp + elapsed);
  const shopping = shoppingView(body);
  const battleIntents = intents.filter(
    (r) =>
      r.kind === 'run_now' &&
      shown !== null &&
      r.meta.operationId === shown.operationId &&
      r.meta.battleId === shown.id &&
      r.meta.generation === shown.generation,
  );
  const commandIntents = intents.filter(
    (r) =>
      (r.kind === 'run_now' || r.kind === 'live_controls') &&
      shown !== null &&
      r.meta.battleId === shown.id &&
      r.meta.generation === shown.generation &&
      r.meta.operationId === shown.operationId,
  );
  const shoppingOptions = {
    requireQueue: true,
    liveDraft:
      shown !== null &&
      Object.keys(state.runDrafts[shoppingLiveScope(state, shown)] ?? {}).length > 0,
    unresolvedCommand: commandIntents.some(
      (r) => r.phase === 'unconfirmed' && settledByRead(state, r) === null,
    ),
    pendingAction: commandIntents.some(
      (r) =>
        r.kind === 'run_now' &&
        (r.phase === 'sending' ||
          commandReceiptPending(contextRead, shown, r.answer?.body, 'action')),
    ),
    pendingControl: commandIntents.some(
      (r) =>
        r.kind === 'live_controls' &&
        (r.phase === 'sending' ||
          commandReceiptPending(contextRead, shown, r.answer?.body, 'controls')),
    ),
  };
  const tryButton = (action: string): CommandVM<RunNowCommand> => {
    const gate = actionGate(contextRead, shown, action, false, elapsed, shoppingOptions);
    const expectedRevision = revision(own(battle, 'control_revision'));
    const command =
      shown === null || expectedRevision === undefined
        ? null
        : {
            ...{ operationId: shown.operationId, battleId: shown.id, generation: shown.generation },
            expectedRevision,
            action,
            epoch: state.epoch,
          };
    const reason = runNowReason({
      gate: gate.enabled ? null : gate.reason,
      reading: rowReading,
      refusal: null,
      sending: false,
    });
    return commandVM(
      true,
      reason === null && command !== null,
      reason,
      shoppingOptions.pendingAction,
      command,
    );
  };
  const tryOnce: ShoppingTryOnceVM[] = [
    { action: 'shopping', known: 'shopping', label: null, button: tryButton('shopping') },
    ...(['attack', 'defense', 'utility'] as const).map((section) => ({
      action: `shopping:${section}`,
      known: section,
      label: null,
      button: tryButton(`shopping:${section}`),
    })),
  ];
  const upgradeTryOnce = (item: (typeof shopping.queue)[number]): ShoppingTryOnceVM | null => {
    if (own(item.raw, 'enabled') !== true) return null;
    const upgrade = id(own(item.raw, 'id'));
    const catalog = (list(own(body, 'catalog')) ?? []).find(
      (entry) => own(entry, 'id') === upgrade,
    );
    const section = id(own(catalog, 'section'));
    if (upgrade === undefined || section === undefined) return null;
    const action = `shopping:${section}:${upgrade}`;
    return {
      action,
      known: null,
      label: fromServer('name', item.label ?? upgrade),
      button: tryButton(action),
    };
  };
  const upgradeActions = shopping.queue.map(upgradeTryOnce);
  tryOnce.push(...upgradeActions.filter((item) => item !== null));
  const outcomes = selectOutcomes(state)
    .lines.filter((line) => battleIntents.some((record) => record.id === line.id))
    .map((line) => {
      const answer = battleIntents.find((record) => record.id === line.id)?.answer;
      const settled =
        answer === null || answer === undefined
          ? null
          : runNowAnswer({ kind: 'answered', status: answer.status, body: answer.body });
      return {
        ...line,
        command:
          settled?.kind === 'recorded' ? commandRow(settled.command, 0, age, projection) : null,
      };
    });
  const runtimeTime = finite(ownPath(battle, 'runtime', 'server_time'));
  const runtimeNow = runtimeTime === undefined ? null : runtimeTime + elapsed;
  const nextOf = (item: ActionItemView): ActionNextVM => {
    if (own(battle, 'status') === 'prepared') return { kind: 'prepared' };
    if (reading?.kind === 'old') return { kind: 'last_known', ageSec: reading.ageSec };
    if (reading?.kind !== 'current' || runtimeNow === null) return { kind: 'not_current' };
    const next = actionNext(item, runtimeNow);
    switch (next.kind) {
      case 'blocker':
        return { kind: 'blocker', code: fromServer('message', next.code) };
      case 'unrecognised':
        return { kind: 'unrecognised', code: codeText(next.raw) };
      default:
        return next;
    }
  };
  const actionRow = (item: ActionItemView): ActionRowVM => {
    const token = item.key;
    const matching = intents.filter(
      (r) =>
        r.kind === 'run_now' &&
        token !== null &&
        shown !== null &&
        r.meta.operationId === shown.operationId &&
        r.meta.battleId === shown.id &&
        r.meta.generation === shown.generation &&
        r.meta.action === token,
    );
    const sending = matching.some((r) => r.phase === 'sending');
    const gate =
      token === null
        ? null
        : actionGate(
            contextRead,
            shown,
            token,
            matching.some((r) => r.phase === 'unconfirmed' && settledByRead(state, r) === null),
            elapsed,
            isShoppingAction(token) ? shoppingOptions : {},
          );
    const refusal = itemRunNowRefusal(item);
    const reason = runNowReason({
      gate: gate === null || gate.enabled ? null : gate.reason,
      reading: rowReading,
      refusal,
      sending,
    });
    const expectedRevision = revision(own(battle, 'control_revision'));
    const command: RunNowCommand | null =
      shown === null || expectedRevision === undefined || token === null
        ? null
        : {
            operationId: shown.operationId,
            battleId: shown.id,
            generation: shown.generation,
            expectedRevision,
            action: token,
            epoch: state.epoch,
          };
    return {
      key: `${item.id}/${item.instance}`,
      token,
      label: fromServer('name', item.label),
      code: fromServer('id', item.id),
      enabled: item.enabled,
      settings: settingFields(item).map((field) => {
        const value = fieldValue(item, battle, field.path);
        const type = fieldType(field);
        return {
          path: field.path,
          label: fromServer('name', field.label),
          unit: fromServer('name', field.unit),
          value: typeof value === 'boolean' ? value : (finite(value) ?? null),
          unrecognisedType: type.kind === 'known' ? null : codeText(field.type),
        };
      }),
      next: nextOf(item),
      last: item.last === null ? null : lastVM(item.last, age),
      runNow: commandVM(true, reason === null && command !== null, reason, sending, command),
      runNowServerReason: refusal?.kind === 'server' ? fromServer('message', refusal.reason) : null,
      shopping: token !== null && isShoppingAction(token),
    };
  };
  const actionRows =
    projection === null || header.row === 'B2' ? null : projection.items.map(actionRow);
  const actionsNotice =
    actionRows === null || reading === null
      ? null
      : reading.kind === 'old'
        ? { reason: 'action_report_old', ageSec: reading.ageSec }
        : reading.kind === 'read_only' ||
            (reading.kind === 'unknown' && reading.reason === 'action_report_time_invalid')
          ? { reason: reading.reason, ageSec: null }
          : null;
  const vm: Omit<ThisBattleVM, 'sharedReason'> = {
    header,
    actions: actionRows,
    actionsNotice,
    tryOnce,
    battleKey: shown === null ? 'untracked' : `${shown.id}/${shown.generation}`,
    runKey: id(own(battle, 'blueprint')) ?? null,
    label: fromServer('name', own(battle, 'label')),
    wave:
      finite(ownPath(battle, 'runtime', 'observed_wave')) ?? finite(own(battle, 'wave')) ?? null,
    changed: entries(own(battle, 'overrides')).length > 0,
    readAgeSec: answered === null ? null : readAgeSec(state, answered),
    reportAgeSec: age(finite(ownPath(battle, 'runtime', 'server_time')) ?? null),
    outcomes,
    resendAvailable:
      read?.outcome.kind === 'answered' && header.row !== 'B1' && header.row !== 'B2',
    queue: {
      needsReview: shopping.line === 'shopping_policy_needs_review',
      mode: text(own(shopping.plan, 'queue_mode')) ?? null,
      cadence: text(shopping.cadence) ?? null,
      reserve: finite(shopping.cashReserve) ?? null,
      items: shopping.queue.map((item, index) => ({
        key: text(own(item.raw, 'id')) ?? String(index),
        label: fromServer('name', item.label) ?? fromServer('name', own(item.raw, 'id')),
        enabled: own(item.raw, 'enabled') === true,
        tapLimit: finite(own(item.raw, 'tap_limit')) ?? null,
        weight: finite(own(item.raw, 'weight')) ?? null,
        tryOnce: upgradeActions[index] ?? null,
      })),
      warnings: shopping.warnings.map(chipVM),
      reportedAgeSec: age(shopping.reportedAt),
      decision: fromServer('detail', own(shopping.decision, 'action')),
      decisionReason: fromServer('message', own(shopping.decision, 'reason')),
      decisionUpgrade: fromServer('name', own(shopping.decision, 'upgrade')),
      pendingUpgrade: fromServer('name', own(shopping.pendingConfirmation, 'upgrade')),
      confirmationUpgrade: fromServer('name', own(shopping.lastConfirmation, 'upgrade')),
      confirmationOutcome: fromServer('detail', own(shopping.lastConfirmation, 'outcome')),
      confirmationReason: fromServer('message', own(shopping.lastConfirmation, 'reason')),
    },
    noTrackedBattle: header.row === 'B2',
    routines:
      header.row === 'B2' || projection !== null
        ? []
        : COMPAT_ROUTINES.map((routine): RoutineRowVM => {
            const matching = intents.filter(
              (r) =>
                r.kind === 'run_now' &&
                shown !== null &&
                r.meta.operationId === shown.operationId &&
                r.meta.battleId === shown.id &&
                r.meta.generation === shown.generation &&
                r.meta.action === routine,
            );
            const sending = matching.some((r) => r.phase === 'sending');
            const gate = actionGate(
              contextRead,
              shown,
              routine,
              matching.some((r) => r.phase === 'unconfirmed' && settledByRead(state, r) === null),
              elapsed,
              isShoppingAction(routine) ? shoppingOptions : {},
            );
            const timing = routineTiming(contextRead, shown, routine, elapsed);
            const result = routineResult(own(battle, 'runtime'), routine);
            const expectedRevision = revision(own(battle, 'control_revision'));
            const command: RunNowCommand | null =
              shown === null || expectedRevision === undefined
                ? null
                : {
                    operationId: shown.operationId,
                    battleId: shown.id,
                    generation: shown.generation,
                    expectedRevision,
                    action: routine,
                    epoch: state.epoch,
                  };
            return {
              routine,
              timing: chipVM(timing),
              nextInSec: timing.nextInSec,
              windowEndsAt: timing.windowEndsAt,
              blocker: fromServer('message', timing.blocker),
              state: fromServer('detail', timing.state),
              stateCode: text(timing.state) ?? null,
              savedEnabled: typeof timing.savedEnabled === 'boolean' ? timing.savedEnabled : null,
              savedIntervalSec: finite(timing.savedIntervalSec) ?? null,
              resultAgeSec: age(result.observedAt),
              result: {
                chip: chipVM(result.decision),
                confirmed: result.confirmed,
                observedAt: result.observedAt,
                reason: fromServer('message', result.reason),
                confirmedPurchases: result.confirmedPurchases,
              },
              runNow: commandVM(
                true,
                gate.enabled && !sending && command !== null,
                sending ? 'sending' : gate.reason === 'ready' ? null : gate.reason,
                sending,
                command,
              ),
            };
          }),
    commands: (list(ownPath(body, 'battle', 'commands')) ?? []).map((row, index) =>
      commandRow(commandView(row), index, age, projection),
    ),
  };
  return { ...vm, sharedReason: sharedReason(vm) };
}

/**
 * The reason every shown Run now and Try once button shares, or null. Try once buttons are shown
 * only with a shopping row. A descriptor's own reason and Sending stay with their row.
 */
function sharedReason(vm: Omit<ThisBattleVM, 'sharedReason'>): string | null {
  const rows = vm.actions ?? vm.routines;
  const shopping =
    vm.actions === null
      ? vm.routines.some((row) => isShoppingAction(row.routine))
      : vm.actions.some((row) => row.shopping);
  const buttons = [
    ...rows.map((row) => row.runNow),
    ...(shopping ? vm.tryOnce.map((item) => item.button) : []),
  ];
  const reasons = new Set(buttons.map((button) => (button.enabled ? null : button.reason)));
  const [reason] = [...reasons];
  return buttons.length > 1 &&
    reasons.size === 1 &&
    reason !== undefined &&
    reason !== null &&
    reason !== 'server_reason' &&
    reason !== 'sending'
    ? reason
    : null;
}
