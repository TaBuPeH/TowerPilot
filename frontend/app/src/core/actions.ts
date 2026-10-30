import { battleReportAge } from './battle.ts';
import { entries, finite, hasOwn, id, list, own, ownOr, ownPath, record, text } from './read.ts';

/**
 * A2-7 timed actions (brief backend/logs/reviews/ui-pages/a2-7-actions-brief.md; rules in
 * actions.rules.md): the one owner of what the app knows about a timed action. It reads the
 * projection `battle.runtime.actions` into plain views, holds the compatibility routine list for
 * a driver that publishes no projection, the shopping action predicates, the editor field order,
 * the live bounds source (R2) and the action-name and field-label lookups. It holds no copy: callers
 * pass their word tables.
 */

/** An action's routine key: the `<key>` of a control path `routines.<key>.<field>` and of a Run now token. */
export type ActionKey = string;

/**
 * S5: the routines a driver that never built the projection publishes (pre-A2 code can publish
 * only these), in the Dashboard's row order. Kept until every driver publishes `runtime.actions`
 * (root decision Q1, end of roadmap item 7); deleted with AC12.
 */
export const COMPAT_ROUTINES: readonly ActionKey[] = ['ad_gems', 'floating_gems', 'shopping'];

/** S5: the field order of the compatibility routines' controls (the editor's columns). */
export const COMPAT_FIELDS: readonly string[] = [
  'enabled',
  'interval_sec',
  'window_sec',
  'tap_interval_sec',
];

/** S5: the result line of a compatibility gem routine whose last outcome is confirmed. */
export function compatClaimLine(
  routine: ActionKey,
  reason: unknown,
): 'claim_confirmed' | 'claim_offer_gone' | 'floating_claim_confirmed' {
  if (routine !== 'ad_gems') return 'floating_claim_confirmed';
  return reason === OFFER_DISAPPEARED ? 'claim_offer_gone' : 'claim_confirmed';
}

/** S2: a shopping variant token (`shopping:<section>[:<upgrade>]`, F6-12 Try once). */
export function isShoppingVariant(token: string): boolean {
  return token.startsWith('shopping:');
}

/** S2: the shopping action itself or one of its variants (the shopping queue's own rules apply). */
export function isShoppingAction(token: string): boolean {
  return token === 'shopping' || isShoppingVariant(token);
}

/**
 * The display entry of an action code from a code-keyed name table, else `other`. Kept apart
 * from `fieldLabel` (both read own keys through `ownOr`) because C1 and C2 move action names,
 * and then field labels, to the projection's labels one at a time.
 */
export function actionName<T>(names: Readonly<Record<string, T>>, action: string, other: T): T {
  return ownOr(names, action, other);
}

/** The display entry of a field code from a code-keyed label table, else `other`. */
export function fieldLabel<T>(labels: Readonly<Record<string, T>>, field: string, other: T): T {
  return ownOr(labels, field, other);
}

/** One field of an item's descriptor, as sent (server text stays raw for the selectors). */
export interface ActionFieldView {
  readonly path: string;
  /** `bool` or `number` as sent; null when the type is not a string. */
  readonly type: string | null;
  readonly min: number | null;
  readonly max: number | null;
  readonly unit: string | null;
  readonly label: unknown;
  readonly live: boolean;
  readonly saved: boolean;
  readonly nullable: boolean;
}

/** One item of `runtime.actions.items`. */
export interface ActionItemView {
  readonly id: string;
  readonly instance: string;
  /**
   * The routine key of its control paths and Run now token: the published `key`, else `id`
   * when the item is its action's only instance (`instance === id`, brief gap G2), else null
   * (its controls cannot be addressed).
   */
  readonly key: ActionKey | null;
  readonly label: unknown;
  readonly fields: readonly ActionFieldView[];
  /** AC1: the server's state code as sent (`disabled`, `waiting`, `due`, `running`, ...). */
  readonly state: unknown;
  /** AC1: the value in force of the action's switch, or null when not a boolean. */
  readonly enabled: boolean | null;
  /** AC1: `eligibility.blocker` as sent (null or absent: none). */
  readonly blocker: unknown;
  readonly dueAt: number | null;
  readonly windowEndsAt: number | null;
  /** AC1: the newest finished execution (`last`), or null. */
  readonly last: ActionLastView | null;
  /** AC3: `descriptor.capabilities.run_now` as sent (`true`, `false` or a reason string). */
  readonly runNow: unknown;
  /** AC1: `values` as sent: each field's value in force (B2), keyed by field path. */
  readonly values: unknown;
}

/** AC1/AC3: an item's newest finished execution (`item.last`). */
export interface ActionLastView {
  readonly executionId: string | null;
  /** The Run now command that started it (B4), or null for an automatic one. */
  readonly commandId: string | null;
  readonly result: unknown;
  readonly effect: unknown;
  readonly reason: unknown;
  readonly trigger: unknown;
  readonly startedAt: number | null;
  readonly finishedAt: number | null;
}

/** The projection envelope `battle.runtime.actions`. */
export interface ActionsView {
  readonly schemaVersion: number | null;
  readonly observedAt: number | null;
  readonly controlRevision: number | null;
  readonly context: unknown;
  readonly items: readonly ActionItemView[];
}

function fieldView(raw: unknown): ActionFieldView | null {
  const path = id(own(raw, 'path'));
  if (path === undefined) return null;
  return {
    path,
    type: text(own(raw, 'type')) ?? null,
    min: finite(own(raw, 'min')) ?? null,
    max: finite(own(raw, 'max')) ?? null,
    unit: text(own(raw, 'unit')) ?? null,
    label: own(raw, 'label'),
    live: own(raw, 'live') === true,
    saved: own(raw, 'saved') === true,
    nullable: own(raw, 'nullable') === true,
  };
}

function itemView(raw: unknown): ActionItemView | null {
  const itemId = id(own(raw, 'id'));
  const instance = id(own(raw, 'instance'));
  if (itemId === undefined || instance === undefined) return null;
  const published = id(own(raw, 'key'));
  return {
    id: itemId,
    instance,
    key: published ?? (instance === itemId ? itemId : null),
    label: own(raw, 'label'),
    fields: (list(ownPath(raw, 'descriptor', 'fields')) ?? []).flatMap((field) => {
      const view = fieldView(field);
      return view === null ? [] : [view];
    }),
    state: own(raw, 'state'),
    enabled: typeof own(raw, 'enabled') === 'boolean' ? own(raw, 'enabled') === true : null,
    blocker: ownPath(raw, 'eligibility', 'blocker'),
    dueAt: finite(ownPath(raw, 'next', 'due_at')) ?? null,
    windowEndsAt: finite(ownPath(raw, 'active', 'window_ends_at')) ?? null,
    last: lastView(own(raw, 'last')),
    runNow: ownPath(raw, 'descriptor', 'capabilities', 'run_now'),
    values: own(raw, 'values'),
  };
}

function lastView(raw: unknown): ActionLastView | null {
  if (record(raw) === undefined) return null;
  return {
    executionId: id(own(raw, 'execution_id')) ?? null,
    commandId: id(own(raw, 'command_id')) ?? null,
    result: own(raw, 'result'),
    effect: own(raw, 'effect'),
    reason: own(raw, 'reason'),
    trigger: own(raw, 'trigger'),
    startedAt: finite(own(raw, 'started_at')) ?? null,
    finishedAt: finite(own(raw, 'finished_at')) ?? null,
  };
}

/**
 * The projection of a battle (`battle.runtime.actions`), or null when the battle publishes none
 * (a driver that never built it: the S5 compatibility rows apply). Items without a readable id
 * and instance are left out.
 */
export function actionsView(battle: unknown): ActionsView | null {
  const raw = ownPath(battle, 'runtime', 'actions');
  if (record(raw) === undefined) return null;
  return {
    schemaVersion: finite(own(raw, 'schema_version')) ?? null,
    observedAt: finite(own(raw, 'observed_at')) ?? null,
    controlRevision: finite(own(raw, 'control_revision')) ?? null,
    context: own(raw, 'context'),
    items: (list(own(raw, 'items')) ?? []).flatMap((item) => {
      const view = itemView(item);
      return view === null ? [] : [view];
    }),
  };
}

/** The item whose controls live under `routines.<key>`, if the projection holds one. */
export function itemOfKey(view: ActionsView | null, key: ActionKey): ActionItemView | undefined {
  return view?.items.find((item) => item.key === key);
}

/**
 * The order of a routine's live fields: its item's live descriptor fields, else the
 * compatibility order (a routine without an item, or a battle without a projection).
 */
export function liveFieldOrder(view: ActionsView | null, key: ActionKey): readonly string[] {
  const item = itemOfKey(view, key);
  return item === undefined
    ? COMPAT_FIELDS
    : item.fields.filter((field) => field.live).map((field) => field.path);
}

/**
 * `fields` in `order`: the fields `order` names first, in its order, then every other field in
 * the order it came (never dropped).
 */
export function orderedFields<F extends { readonly field: string }>(
  fields: readonly F[],
  order: readonly string[],
): readonly F[] {
  const named = new Set(order);
  return [
    ...order.flatMap((name) => fields.filter((field) => field.field === name)),
    ...fields.filter((field) => !named.has(field.field)),
  ];
}

/** R2: the projection gives some live number field its bounds (the live editor's bounds are known). */
export function hasLiveBounds(view: ActionsView | null): boolean {
  return (
    view?.items.some(
      (item) =>
        item.key !== null && item.fields.some((field) => field.live && field.type === 'number'),
    ) ?? false
  );
}

/**
 * R2: the bounds of a battle's live controls, keyed by control path like a run-controls read's
 * `constraints`. A number field of an addressable item takes its descriptor's bounds; every
 * other path keeps the saved run's `constraints` as read. Without a projection the saved
 * constraints are returned as they are.
 */
export function liveConstraints(view: ActionsView | null, saved: unknown): unknown {
  // A descriptor bound of null means the descriptor publishes no bound for that side; it is
  // not filled from the saved run (equal on every recorded pair, tests/unit/core/actions.test.ts).
  if (view === null) return saved;
  const bounds: Record<string, { readonly min: number | null; readonly max: number | null }> = {};
  for (const item of view.items) {
    if (item.key === null) continue;
    for (const field of item.fields)
      if (field.live && field.type === 'number')
        bounds[`routines.${item.key}.${field.path}`] = { min: field.min, max: field.max };
  }
  return { ...Object.fromEntries(entries(saved)), ...bounds };
}

/**
 * AC5 (brief section 6 rules 5 and 6): how far the projection of a battle read can be trusted.
 * - `current`: the battle report is fresh by EV-9 and the projection was built by that report's
 *   publish (`observed_at === runtime.server_time`).
 * - `absent`: a fresh report without `runtime.actions` (the S5 compatibility rows apply).
 * - `old`: the last projection stayed while the report moved on; `ageSec` is
 *   `server_time - observed_at + elapsed`. Shown as last known, never as current.
 * - `unknown`: the battle report is not fresh (EV-9 reasons), or `observed_at` is not a finite
 *   time at or before `runtime.server_time`.
 * - `read_only`: a `schema_version` other than 1, or a `context` that names another device,
 *   account, instance or profile than the battle's.
 */
export type ActionsReading =
  | { readonly kind: 'current' }
  | { readonly kind: 'absent' }
  | { readonly kind: 'old'; readonly ageSec: number }
  | {
      readonly kind: 'unknown';
      readonly reason: 'action_report_time_invalid' | 'battle_report_old' | 'battle_time_invalid';
    }
  | {
      readonly kind: 'read_only';
      readonly reason: 'action_schema_newer' | 'action_context_mismatch';
    };

/** The projection schema this build reads (brief section 3.1). */
const ACTIONS_SCHEMA_VERSION = 1;

/** Envelope context key -> battle context key; every pair must be equal (rule 6). */
const CONTEXT_KEYS: readonly (readonly [string, string])[] = [
  ['device_key', 'device_key'],
  ['account', 'account'],
  ['instance_name', 'instance'],
  ['profile', 'profile'],
];

/**
 * AC5: the reading of `battle.runtime.actions` in `read`, the answered body of GET
 * /api/battles/active (`{server_time, battle}`; the battle's own stamps cannot tell how old the
 * read is). `elapsedSec` is monotonic time since the read arrived. The battle's age is the EV-9
 * rule of core/battle (`battleReportAge`); this rule adds no number of its own.
 */
export function actionsReading(read: unknown, elapsedSec: number): ActionsReading {
  const age = battleReportAge(read, elapsedSec);
  if (age.kind !== 'fresh') return { kind: 'unknown', reason: age.reason };
  const battle = own(read, 'battle');
  const actions = ownPath(battle, 'runtime', 'actions');
  if (record(actions) === undefined) return { kind: 'absent' };
  if (own(actions, 'schema_version') !== ACTIONS_SCHEMA_VERSION)
    return { kind: 'read_only', reason: 'action_schema_newer' };
  const context = own(actions, 'context');
  const battleContext = own(battle, 'context');
  if (
    record(context) === undefined ||
    CONTEXT_KEYS.some(
      ([mine, theirs]) =>
        !hasOwn(context, mine) || own(context, mine) !== own(battleContext, theirs),
    )
  )
    return { kind: 'read_only', reason: 'action_context_mismatch' };
  const observedAt = finite(own(actions, 'observed_at'));
  if (observedAt === undefined || observedAt > age.runtimeTime)
    return { kind: 'unknown', reason: 'action_report_time_invalid' };
  if (observedAt === age.runtimeTime) return { kind: 'current' };
  // battleReportAge has already refused a non-finite or negative elapsed time.
  return { kind: 'old', ageSec: age.serverTime - observedAt + elapsedSec };
}

/** AC5: every reason `actionControlsBlocked` can return (copy: `copy.actions.blocked`). */
export const ACTION_CONTROL_BLOCKS = [
  'action_report_old',
  'action_report_time_invalid',
  'action_schema_newer',
  'action_context_mismatch',
  'battle_report_old',
  'battle_time_invalid',
] as const;

/** AC5: a reason that closes Run now, Try once and every live change send for a reading. */
export type ActionControlBlock = (typeof ACTION_CONTROL_BLOCKS)[number];

/**
 * AC5 (root decision Q2): the reason code that disables Run now and live edits for a reading,
 * or null when they follow their own gates. Shown to the person as the region's one reason.
 */
export function actionControlsBlocked(reading: ActionsReading): ActionControlBlock | null {
  switch (reading.kind) {
    case 'current':
    case 'absent':
      return null;
    case 'old':
      return 'action_report_old';
    case 'unknown':
    case 'read_only':
      return reading.reason;
  }
}

/** AC1: the item states this build reads (spec 2.4; `yielded`, `blocked` and `unknown` unrecorded). */
export const ACTION_STATES = [
  'disabled',
  'waiting',
  'due',
  'running',
  'yielded',
  'blocked',
  'unknown',
] as const;

/** AC1: the results of a finished execution (`last.result`, an execution's `outcome.result`). */
export const ACTION_RESULTS = [
  'succeeded',
  'no_op',
  'failed',
  'refused',
  'interrupted',
  'cancelled',
  'unknown',
] as const;

/** AC1 (rule 2): the effects of a finished execution; only `confirmed` reads as confirmed. */
export const ACTION_EFFECTS = ['confirmed', 'unconfirmed', 'unknown', 'none'] as const;

/** The reason the collector records when an ad offer was gone from the look taken after the tap. */
export const OFFER_DISAPPEARED = 'offer_disappeared';

/** The effect word of a battle ad whose only proof is that the offer was gone after the tap (no balance read). */
export const OFFER_GONE = 'offer_gone';

/** An effect code of the server, or the one word this build adds for OFFER_GONE. */
export type ActionEffectWord = (typeof ACTION_EFFECTS)[number] | typeof OFFER_GONE;

/**
 * Words only, no new decision: the server records that execution as `unconfirmed` and names the reason;
 * it reads as "offer gone after tap", never as a confirmed effect.
 */
export function effectWord<E extends string>(effect: E, reason: unknown): E | typeof OFFER_GONE {
  return effect === 'unconfirmed' && reason === OFFER_DISAPPEARED ? OFFER_GONE : effect;
}

/** AC1: the descriptor field types this build can show and edit. */
export const ACTION_FIELD_TYPES = ['bool', 'number'] as const;

/**
 * A server code: `known` when this build reads it, `unreported` when absent or null (nothing was
 * sent, which is not a code this build fails to read), else `unrecognised` with the value as sent.
 */
export type ActionCode<K extends string> =
  | { readonly kind: 'known'; readonly code: K }
  | { readonly kind: 'unreported' }
  | { readonly kind: 'unrecognised'; readonly raw: unknown };

function actionCode<K extends string>(value: unknown, known: readonly K[]): ActionCode<K> {
  if (value === undefined || value === null) return { kind: 'unreported' };
  const found = known.find((candidate) => candidate === value);
  return found === undefined
    ? { kind: 'unrecognised', raw: value }
    : { kind: 'known', code: found };
}

/** AC1 (rule 6): a descriptor field's type as this build reads it. */
export function fieldType(field: ActionFieldView): ActionCode<(typeof ACTION_FIELD_TYPES)[number]> {
  return actionCode(field.type, ACTION_FIELD_TYPES);
}

/**
 * AC1: what the Next cell says for a current item at `now` (server seconds: the report's
 * `runtime.server_time` plus the time since the read arrived, F-050/F-055), from the server's
 * own decisions, in this order: switched off (`disabled`), then an open window (`running`, with
 * the seconds to `active.window_ends_at`), then the blocker (it comes before the state), then
 * `due`, then `waiting` (seconds to `next.due_at`, or not reported), then the other states. A
 * state that is absent or not one of `ACTION_STATES` is unrecognised. Seconds are presentation
 * only (brief section 6 rule 1).
 */
export type ActionNext =
  | { readonly kind: 'off' }
  | { readonly kind: 'running'; readonly endsInSec: number | null }
  | { readonly kind: 'blocker'; readonly code: unknown }
  | { readonly kind: 'due' }
  | { readonly kind: 'in'; readonly sec: number }
  | { readonly kind: 'not_reported' }
  | { readonly kind: 'state'; readonly code: 'yielded' | 'blocked' | 'unknown' }
  | { readonly kind: 'unrecognised'; readonly raw: unknown };

export function actionNext(item: ActionItemView, now: number): ActionNext {
  const state = actionCode(item.state, ACTION_STATES);
  if (state.kind !== 'known') return { kind: 'unrecognised', raw: item.state };
  if (state.code === 'disabled') return { kind: 'off' };
  if (state.code === 'running')
    return {
      kind: 'running',
      endsInSec: item.windowEndsAt === null ? null : Math.max(0, item.windowEndsAt - now),
    };
  if (item.blocker !== null && item.blocker !== undefined)
    return { kind: 'blocker', code: item.blocker };
  if (state.code === 'due') return { kind: 'due' };
  if (state.code === 'waiting')
    return item.dueAt === null
      ? { kind: 'not_reported' }
      : { kind: 'in', sec: Math.max(0, item.dueAt - now) };
  return { kind: 'state', code: state.code };
}

/** AC1 (rule 2): the last result of an item: its result and effect codes, read as sent. */
export interface ActionOutcome {
  readonly result: ActionCode<(typeof ACTION_RESULTS)[number]>;
  readonly effect: ActionCode<ActionEffectWord>;
}

export function actionOutcome(last: ActionLastView): ActionOutcome {
  const effect = actionCode(last.effect, ACTION_EFFECTS);
  return {
    result: actionCode(last.result, ACTION_RESULTS),
    effect:
      effect.kind === 'known'
        ? { kind: 'known', code: effectWord(effect.code, last.reason) }
        : effect,
  };
}

/**
 * AC1/AC3 (rule 6): the first code of an item that this build does not recognise, in the order
 * state (absent counts: a state is required), field type, last result, last effect; or
 * undefined. Each cell shows its own code; this one only disables the item's controls
 * (`action_code_unrecognised`). An absent result or effect is unreported, not unrecognised.
 */
export function unrecognisedCode(item: ActionItemView): { readonly raw: unknown } | undefined {
  if (actionCode(item.state, ACTION_STATES).kind !== 'known') return { raw: item.state };
  const field = item.fields.find((candidate) => fieldType(candidate).kind !== 'known');
  if (field !== undefined) return { raw: field.type };
  if (item.last === null) return undefined;
  const outcome = actionOutcome(item.last);
  if (outcome.result.kind === 'unrecognised') return { raw: outcome.result.raw };
  if (outcome.effect.kind === 'unrecognised') return { raw: outcome.effect.raw };
  return undefined;
}

/** AC3: why an item offers no Run now before the battle-wide gate is asked. */
export type ItemRunNowRefusal =
  | {
      readonly kind: 'code';
      readonly code:
        'control_path_unpublished' | 'action_code_unrecognised' | 'run_now_unsupported';
    }
  | { readonly kind: 'server'; readonly reason: string };

/**
 * AC3: an item's own refusal of Run now, or null: its controls cannot be addressed
 * (`control_path_unpublished`, gap G2), it reports a code this build does not recognise, or its
 * descriptor does not offer Run now (`run_now` other than `true`: a reason string is shown as
 * sent, anything else is `run_now_unsupported`).
 */
export function itemRunNowRefusal(item: ActionItemView): ItemRunNowRefusal | null {
  if (item.key === null) return { kind: 'code', code: 'control_path_unpublished' };
  if (unrecognisedCode(item) !== undefined)
    return { kind: 'code', code: 'action_code_unrecognised' };
  if (item.runNow === true) return null;
  const reason = id(item.runNow);
  return reason === undefined
    ? { kind: 'code', code: 'run_now_unsupported' }
    : { kind: 'server', reason };
}

/** `actionGate` reasons about the whole battle read: they come before every other reason. */
const BATTLE_GATE_REASONS: readonly string[] = [
  'report_unavailable',
  'battle_not_active',
  'no_tracked_battle',
];

/**
 * AC3/AC5: the one reason a This battle Run now or Try once button is disabled, or null when it
 * is offered. In order: the battle-wide `actionGate` reasons, then the readings' block
 * (`actionControlsBlocked`; `reading` is null for the S5 compatibility rows, which publish no
 * readings), then the item's own refusal (`server_reason` when the descriptor sent its own
 * words), then any other gate reason, then `sending` while this button's request is in flight.
 */
export function runNowReason(input: {
  readonly gate: string | null;
  readonly reading: ActionsReading | null;
  readonly refusal: ItemRunNowRefusal | null;
  readonly sending: boolean;
}): string | null {
  const { gate, reading, refusal, sending } = input;
  if (gate !== null && BATTLE_GATE_REASONS.includes(gate)) return gate;
  const blocked = reading === null ? null : actionControlsBlocked(reading);
  if (blocked !== null) return blocked;
  if (refusal !== null) return refusal.kind === 'code' ? refusal.code : 'server_reason';
  return gate ?? (sending ? 'sending' : null);
}
/**
 * AC1: the fields of an item's Settings: every descriptor field except its switch (`enabled`),
 * whose value the item's own `enabled` reports (brief section 3.2), in descriptor order.
 */
export function settingFields(item: ActionItemView): readonly ActionFieldView[] {
  return item.fields.filter((field) => field.path !== SWITCH_FIELD);
}

/** The descriptor field the item's `enabled` mirrors. */
const SWITCH_FIELD = 'enabled';

/**
 * AC1: the value in force of an item's field: `values.<path>` (B2), else the battle's
 * `effective.routines.<key>.<path>` (gap G1, the source the editors read), else undefined.
 */
export function fieldValue(item: ActionItemView, battle: unknown, path: string): unknown {
  if (hasOwn(item.values, path)) return own(item.values, path);
  return item.key === null ? undefined : ownPath(battle, 'effective', 'routines', item.key, path);
}
