import {
  itemOfKey,
  type ActionControlBlock,
  type ActionFieldView,
  type ActionsView,
} from './actions.ts';
import { controlApplication } from './battle.ts';
import { sameJson } from './json.ts';
import { planHolds, queuePlan } from './shoppingPlan.ts';
import { entries, finite, id, own, ownPath, revision, stringList } from './read.ts';
import { typedDecimal } from './typedNumber.ts';

/**
 * Run controls (roadmap item 5; Runs page brief backend/logs/reviews/ui-pages/runs-brief.md):
 * the saved settings of one run (GET/POST /api/run-controls) and the live
 * settings of the tracked battle (POST /api/operations/{id}/live-controls) as
 * editable fields, a person's draft against them, and the gates of both saves.
 * The server names which controls exist (`capabilities.controls`, dotted
 * paths), their numeric bounds (`constraints`) and their values; this module
 * never adds a field the server did not name, never clamps or rounds a value,
 * and never turns an invalid draft into a different valid one.
 */

/** One editable control: `routines.<routine>.<field>`. */
export interface ControlField {
  readonly path: string;
  readonly routine: string;
  readonly field: string;
  /** A switch (the value is a boolean) or a number with the server's bounds. */
  readonly kind: 'toggle' | 'number';
  /** The value the read reports, or null when it does not report a usable one. */
  readonly value: boolean | number | null;
  readonly min: number | null;
  readonly max: number | null;
}

/** The fields of one routine, in the server's order. */
export interface ControlGroup {
  readonly routine: string;
  readonly fields: readonly ControlField[];
}

/** What a person typed or switched, per path: text for a number, a boolean for a switch. */
export type DraftValues = Readonly<Record<string, string | boolean>>;

export type InvalidReason = 'empty' | 'not_a_number' | 'below_min' | 'above_max' | 'not_editable';

export type DraftCheck =
  | { readonly kind: 'valid'; readonly value: boolean | number }
  | { readonly kind: 'invalid'; readonly reason: InvalidReason };

function splitPath(path: string): { readonly routine: string; readonly field: string } | null {
  const parts = path.split('.');
  return parts.length === 3 && parts[0] === 'routines' && parts[1] !== '' && parts[2] !== ''
    ? { routine: parts[1] ?? '', field: parts[2] ?? '' }
    : null;
}

/**
 * The fields of a settings object (`settings` of a run-controls read, or
 * `effective` of a battle) for the controls the server names, grouped by
 * routine in the order the server lists them. `shopping_plan` and anything not
 * shaped `routines.<routine>.<field>` is not a field here.
 */
export function controlGroups(
  controls: unknown,
  settings: unknown,
  constraints: unknown,
  /**
   * A2-7 AC2: the kind a describing source names for a path (`skip`: not a field here); when
   * it names none (undefined) the kind is read from the bounds and the value as before.
   */
  kindOf: (routine: string, field: string) => ControlField['kind'] | 'skip' | undefined = () =>
    undefined,
): readonly ControlGroup[] {
  const groups = new Map<string, ControlField[]>();
  for (const path of stringList(controls) ?? []) {
    const parts = splitPath(path);
    if (parts === null) continue;
    const named = kindOf(parts.routine, parts.field);
    if (named === 'skip') continue;
    const raw = ownPath(settings, 'routines', parts.routine, parts.field);
    const bounds = own(constraints, path);
    const min = finite(own(bounds, 'min')) ?? null;
    const max = finite(own(bounds, 'max')) ?? null;
    const isNumber =
      named === undefined
        ? bounds !== undefined || (typeof raw === 'number' && parts.field !== 'enabled')
        : named === 'number';
    const field: ControlField = {
      path,
      routine: parts.routine,
      field: parts.field,
      kind: isNumber ? 'number' : 'toggle',
      value: isNumber ? (finite(raw) ?? null) : typeof raw === 'boolean' ? raw : null,
      min,
      max,
    };
    const fields = groups.get(parts.routine) ?? [];
    fields.push(field);
    groups.set(parts.routine, fields);
  }
  return [...groups].map(([routine, fields]) => ({ routine, fields }));
}

/** A2-7 AC2: the unit a descriptor gives a number: seconds (`s`), none, or another unit as sent. */
export type ControlUnit =
  | { readonly kind: 'seconds' }
  | { readonly kind: 'none' }
  | { readonly kind: 'other'; readonly text: unknown };

/** What a live field's descriptor says beyond its kind and bounds (server text stays raw). */
export interface LiveFieldDescription {
  readonly label: unknown;
  readonly unit: ControlUnit;
  readonly nullable: boolean;
}

/** A listed control whose descriptor type the page does not know: shown, never editable. */
export interface UnrecognisedField {
  readonly path: string;
  readonly routine: string;
  readonly field: string;
  readonly type: unknown;
  readonly label: unknown;
}

/** An item whose controls cannot be addressed (brief gap G2: no key, not its action's only instance). */
export interface UnaddressableItem {
  readonly id: string;
  readonly instance: string;
  readonly label: unknown;
}

/** The live editor's fields of one battle (A2-7 AC2). */
export interface LiveControls {
  /** The editable fields, grouped by routine in the order `capabilities.controls` lists them. */
  readonly groups: readonly ControlGroup[];
  /** The descriptor of each described editable field, by control path. */
  readonly descriptions: ReadonlyMap<string, LiveFieldDescription>;
  /** The projection's label of each addressable item, by routine key. */
  readonly itemLabels: ReadonlyMap<string, unknown>;
  readonly unrecognised: readonly UnrecognisedField[];
  readonly unaddressable: readonly UnaddressableItem[];
}

/** The descriptor field of `routines.<routine>.<field>`, when an addressable item describes it. */
function describedField(
  view: ActionsView | null,
  routine: string,
  field: string,
): { readonly described: boolean; readonly field: ActionFieldView | undefined } {
  const item = itemOfKey(view, routine);
  return {
    described: item !== undefined,
    field: item?.fields.find((found) => found.path === field),
  };
}

function unitOf(unit: string | null): ControlUnit {
  if (unit === null) return { kind: 'none' };
  return unit === 's' ? { kind: 'seconds' } : { kind: 'other', text: unit };
}

/**
 * A2-7 AC2 (brief section 6 rule 4): the live fields of a battle. A path `capabilities.controls`
 * lists is a field only as before (no other path is ever offered); when an addressable item of
 * the projection describes its routine, the path is a field only if that item's descriptor
 * lists it as `live`, and its kind is the descriptor's `type` (`bool` a switch, `number` a
 * number; any other type is shown apart and never editable). Bounds come from `constraints`
 * (core/actions liveConstraints gives the descriptor's); values from `settings` (the battle's
 * `effective`, gap G1). Routines no item describes (the S4 gates) and a battle without a
 * projection keep the fields as `controlGroups` reads them; a routine whose only items have no
 * key offers no field (its read-only row says why).
 */
export function liveControls(
  controls: unknown,
  settings: unknown,
  constraints: unknown,
  view: ActionsView | null,
): LiveControls {
  const descriptions = new Map<string, LiveFieldDescription>();
  const unrecognised: UnrecognisedField[] = [];
  const groups = controlGroups(controls, settings, constraints, (routine, name) => {
    const { described, field } = describedField(view, routine, name);
    // An action whose every item names no addressable key is read-only (gap G2): none of its
    // listed controls is offered beside its read-only row.
    if (!described)
      return view?.items.some((item) => item.key === null && item.id === routine) === true
        ? 'skip'
        : undefined;
    if (field === undefined || !field.live) return 'skip';
    const path = `routines.${routine}.${name}`;
    if (field.type !== 'bool' && field.type !== 'number') {
      unrecognised.push({ path, routine, field: name, type: field.type, label: field.label });
      return 'skip';
    }
    descriptions.set(path, {
      label: field.label,
      unit: field.type === 'number' ? unitOf(field.unit) : { kind: 'none' },
      nullable: field.nullable,
    });
    return field.type === 'bool' ? 'toggle' : 'number';
  });
  const itemLabels = new Map<string, unknown>();
  const unaddressable: UnaddressableItem[] = [];
  for (const item of view?.items ?? []) {
    if (item.key === null)
      unaddressable.push({ id: item.id, instance: item.instance, label: item.label });
    else if (!itemLabels.has(item.key)) itemLabels.set(item.key, item.label);
  }
  return { groups, descriptions, itemLabels, unrecognised, unaddressable };
}

/**
 * A2-7 AC2: an override of `routines.<routine>.<field>` may be reset to the battle's starting
 * value (sent as null) unless an addressable item describes that field as not `nullable`.
 */
export function liveFieldNullable(
  view: ActionsView | null,
  routine: string,
  field: string,
): boolean {
  return describedField(view, routine, field).field?.nullable ?? true;
}

/** Judge one draft value against its field: never clamped, never rounded. */
export function checkDraft(field: ControlField, draft: string | boolean): DraftCheck {
  if (field.kind === 'toggle') {
    return typeof draft === 'boolean'
      ? { kind: 'valid', value: draft }
      : { kind: 'invalid', reason: 'not_a_number' };
  }
  if (typeof draft !== 'string') return { kind: 'invalid', reason: 'not_a_number' };
  const typed = typedDecimal(draft);
  if ('reason' in typed) return { kind: 'invalid', reason: typed.reason };
  // A run control is never negative: a typed minus is not a number here (unchanged rule).
  if (draft.trim().startsWith('-')) return { kind: 'invalid', reason: 'not_a_number' };
  const { value } = typed;
  if (field.min !== null && value < field.min) return { kind: 'invalid', reason: 'below_min' };
  if (field.max !== null && value > field.max) return { kind: 'invalid', reason: 'above_max' };
  return { kind: 'valid', value };
}

/** A draft judged against the fields: the changes it would send, and every invalid path. */
export interface DraftReview {
  /** Dotted path to the value to send, only where it differs from the read's value. */
  readonly changes: Readonly<Record<string, boolean | number>>;
  readonly invalid: Readonly<Record<string, InvalidReason>>;
}

export function reviewDraft(groups: readonly ControlGroup[], draft: DraftValues): DraftReview {
  const fields = new Map(groups.flatMap((group) => group.fields.map((f) => [f.path, f] as const)));
  const changes: Record<string, boolean | number> = {};
  const invalid: Record<string, InvalidReason> = {};
  for (const [path, value] of Object.entries(draft)) {
    const field = fields.get(path);
    if (field === undefined) {
      invalid[path] = 'not_editable';
      continue;
    }
    const checked = checkDraft(field, value);
    if (checked.kind === 'invalid') invalid[path] = checked.reason;
    else if (checked.value !== field.value) changes[path] = checked.value;
  }
  return { changes, invalid };
}

/**
 * The newest save of the same target: in flight, its outcome unknown (no read settled it),
 * answered but the target not read again since
 * (`refreshing`: a new save waits for the values and revision that answer
 * produced), or none of these.
 */
export type SaveRequest = 'none' | 'sending' | 'unresolved' | 'refreshing';

/** Why a save is not offered: the first reason, in this order. */
export type SavedSaveReason =
  | 'run_not_read'
  | 'read_other_context'
  | 'revision_unknown'
  | 'save_in_flight'
  | 'save_unresolved'
  | 'refreshing'
  | 'invalid_value'
  | 'no_changes';

/**
 * A2-7 AC2 (root decision Q2): a reason code of core/actions `actionControlsBlocked` that closes
 * live changes while the battle's action readings are old, unreadable or of another context.
 */
export type ActionsBlockReason = ActionControlBlock;

export type SaveGate<C> =
  | { readonly enabled: true; readonly command: C }
  | {
      readonly enabled: false;
      /** A `SavedSaveReason`, a `LiveSaveReason` or an `ActionsBlockReason` (all codes). */
      readonly reason: SavedSaveReason | LiveSaveReason | ActionsBlockReason;
    };

/** The body facts of a saved-settings save (POST /api/run-controls). */
export interface SavedSave {
  readonly blueprint: string;
  readonly context: unknown;
  readonly expectedRevision: string;
  readonly changes: Readonly<Record<string, boolean | number>>;
}

/**
 * The saved-settings save (brief RUN-05, RUN-06): the read of this run in this
 * context, its `profile_revision` as the compare-and-set, no earlier save of
 * this run in flight or with an unknown outcome, no invalid draft value and at
 * least one change. The context sent is the one the read names (the server
 * refuses a save made for another emulator, account or profile).
 */
export function savedSaveGate(
  read: { readonly body: unknown; readonly sameContext: boolean } | null,
  review: DraftReview,
  request: SaveRequest,
): SaveGate<SavedSave> {
  if (read === null) return { enabled: false, reason: 'run_not_read' };
  if (!read.sameContext) return { enabled: false, reason: 'read_other_context' };
  const blueprint = id(own(read.body, 'blueprint'));
  const expected = id(own(read.body, 'profile_revision'));
  const context = own(read.body, 'context');
  if (blueprint === undefined || expected === undefined || context === undefined)
    return { enabled: false, reason: 'revision_unknown' };
  if (request === 'sending') return { enabled: false, reason: 'save_in_flight' };
  if (request === 'unresolved') return { enabled: false, reason: 'save_unresolved' };
  if (request === 'refreshing') return { enabled: false, reason: 'refreshing' };
  if (Object.keys(review.invalid).length > 0) return { enabled: false, reason: 'invalid_value' };
  if (Object.keys(review.changes).length === 0) return { enabled: false, reason: 'no_changes' };
  return {
    enabled: true,
    command: { blueprint, context, expectedRevision: expected, changes: review.changes },
  };
}

/** Why a live save is not offered. */
export type LiveSaveReason =
  | 'no_battle'
  | 'battle_not_active'
  | 'report_stale'
  | 'change_pending'
  | 'revision_unknown'
  | 'save_in_flight'
  | 'save_unresolved'
  | 'refreshing'
  | 'invalid_value'
  | 'no_changes';

/** The body facts of a live save (POST /api/operations/{id}/live-controls). */
export interface LiveSave<C = Readonly<Record<string, boolean | number>>> {
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
  readonly changes: C;
}

/**
 * The live save (brief RUN-08): an active battle with a fresh report, its last
 * change applied (`applied_revision` equal to `control_revision`, so a new
 * change never races one the driver has not taken; an applied revision ahead
 * of the control one is unknown, never applied), `control_revision` as the
 * compare-and-set, no earlier live save of this battle in flight or unknown, no
 * invalid draft value and at least one change.
 */
export function liveSaveGate<C extends Readonly<Record<string, unknown>>>(
  battle: unknown,
  reportFresh: boolean,
  review: { readonly changes: C; readonly invalid: DraftReview['invalid'] },
  request: SaveRequest,
  /** AC2: the shared actions block (`actionControlsBlocked`), or null. */
  blocked: ActionsBlockReason | null = null,
): SaveGate<LiveSave<C>> {
  const target = liveTarget(battle, reportFresh, request, blocked);
  if (!target.enabled) return target;
  if (Object.keys(review.invalid).length > 0) return { enabled: false, reason: 'invalid_value' };
  if (Object.keys(review.changes).length === 0) return { enabled: false, reason: 'no_changes' };
  return { enabled: true, command: { ...target.command, changes: review.changes } };
}

/** The battle a live change acts on, and its compare-and-set revision. */
interface LiveTarget {
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
}

/** F6-11 RL1, RL2: a reset of live overrides; each path is sent as `null`. */
export interface LiveReset extends LiveTarget {
  readonly paths: readonly string[];
}

/** One applied override; a shopping plan is one leaf regardless of its nested settings. */
export type LiveOverrideLeaf =
  | {
      readonly kind: 'routine';
      readonly path: string;
      readonly routine: string;
      readonly field: string;
    }
  | { readonly kind: 'shopping_plan'; readonly path: 'shopping_plan' };

/**
 * RL2 F6-11-P2-05: every non-null override already applied to this battle,
 * independently of the fields capabilities.controls lets the editor render.
 * Empty routine mappings left behind by a reset contain no override leaves.
 */
export function liveOverrideLeaves(battle: unknown): readonly LiveOverrideLeaf[] {
  const overrides = own(battle, 'overrides');
  const applied = own(battle, 'applied_overrides');
  const leaves: LiveOverrideLeaf[] = [];
  for (const [routine, fields] of entries(own(overrides, 'routines'))) {
    for (const [field, value] of entries(fields)) {
      if (overrideMark(value, ownPath(applied, 'routines', routine, field)) === 'changed')
        leaves.push({ kind: 'routine', path: `routines.${routine}.${field}`, routine, field });
    }
  }
  const plan = own(overrides, 'shopping_plan');
  if (overrideMark(plan, own(applied, 'shopping_plan')) === 'changed')
    leaves.push({ kind: 'shopping_plan', path: 'shopping_plan' });
  return leaves;
}

/**
 * RL1, RL2 (spec F-060, Runs RUN-15): removing this battle's overrides of `paths` (the
 * battle's starting value applies again, `backend/towerpilot_workflows/battles.py`
 * `_set_overrides`). The same gate as a live save (RUN-09), and only paths this battle
 * overrides with that override applied (a `waiting` one is still in flight).
 */
export function liveResetGate(
  battle: unknown,
  reportFresh: boolean,
  paths: readonly string[],
  request: SaveRequest,
  /** AC2: the actions block (`actionControlsBlocked`), or null. */
  blocked: ActionsBlockReason | null = null,
): SaveGate<LiveReset> {
  const target = liveTarget(battle, reportFresh, request, blocked);
  if (!target.enabled) return target;
  if (paths.length === 0) return { enabled: false, reason: 'no_changes' };
  return { enabled: true, command: { ...target.command, paths: [...paths] } };
}

/**
 * F6-11 RL1 (review F6-11-R1-01): what a reset confirmation was shown for. A confirmation
 * applies only while the offered reset still has this key: the same operation, battle,
 * generation, control revision and paths.
 */
export function liveResetKey(reset: LiveReset): string {
  return JSON.stringify([
    reset.operationId,
    reset.battleId,
    reset.generation,
    reset.expectedRevision,
    reset.paths,
  ]);
}

function liveTarget(
  battle: unknown,
  reportFresh: boolean,
  request: SaveRequest,
  blocked: ActionsBlockReason | null,
): SaveGate<LiveTarget> {
  if (battle === null || battle === undefined) return { enabled: false, reason: 'no_battle' };
  if (own(battle, 'status') !== 'active') return { enabled: false, reason: 'battle_not_active' };
  const operationId = id(own(battle, 'operation_id'));
  const battleId = id(own(battle, 'id'));
  const generation = id(own(battle, 'generation'));
  const control = revision(own(battle, 'control_revision'));
  if (
    operationId === undefined ||
    battleId === undefined ||
    generation === undefined ||
    control === undefined
  )
    return { enabled: false, reason: 'revision_unknown' };
  if (request === 'sending') return { enabled: false, reason: 'save_in_flight' };
  if (request === 'unresolved') return { enabled: false, reason: 'save_unresolved' };
  if (request === 'refreshing') return { enabled: false, reason: 'refreshing' };
  if (!reportFresh) return { enabled: false, reason: 'report_stale' };
  // AC2 (root decision Q2): old, unreadable or foreign action readings close live changes.
  if (blocked !== null) return { enabled: false, reason: blocked };
  // RUN-09, SR-6: only `applied_revision == control_revision` (core/battle.ts B5) opens a change.
  const application = controlApplication(battle);
  if (application === 'pending') return { enabled: false, reason: 'change_pending' };
  if (application === 'unknown') return { enabled: false, reason: 'revision_unknown' };
  return {
    enabled: true,
    command: { operationId, battleId, generation, expectedRevision: control },
  };
}

/** How one live field stands against the saved run: inherited, changed for this battle, or waiting to apply. */
export type LiveMark = 'saved' | 'changed' | 'waiting';

function overrideMark(requested: unknown, applied: unknown): LiveMark {
  if (!sameJson(requested, applied)) return 'waiting';
  return requested === undefined || requested === null ? 'saved' : 'changed';
}

/**
 * A live field's mark: `waiting` while the requested override differs from the
 * applied one, `changed` while this battle overrides the path, else `saved`.
 */
export function liveMark(battle: unknown, field: ControlField): LiveMark {
  const requested = ownPath(own(battle, 'overrides'), 'routines', field.routine, field.field);
  const applied = ownPath(own(battle, 'applied_overrides'), 'routines', field.routine, field.field);
  return overrideMark(requested, applied);
}

/** The answer of a saved-settings save (dashboard.py api_run_controls POST). */
export type SavedSaveAnswer =
  | { readonly kind: 'saved'; readonly profileRevision: string }
  /**
   * Saved, but the settings the answer reports hold other values for some sent paths
   * (the server keeps shopping off for a run without a shopping queue): `kept` are the
   * sent paths it holds, the only ones a draft may drop (SR-2).
   */
  | {
      readonly kind: 'saved_partly';
      readonly profileRevision: string;
      readonly kept: readonly string[];
    }
  /**
   * Nothing was written by this request. `conflict` (a 409: the saved run or the context
   * changed, a read-only profile, the request lock) says only that: whether the saved
   * settings hold the values is a later read's to say (review F6R-03; savedReadback).
   */
  | { readonly kind: 'refused'; readonly code: unknown; readonly conflict: boolean }
  | { readonly kind: 'unknown' };

/** What a saved-run save sent: the run, the revision it was made against, the changes. */
export interface SentSave {
  readonly blueprint?: string | undefined;
  readonly profileRevision?: string | undefined;
  readonly changes?: Readonly<Record<string, unknown>> | undefined;
}

/**
 * The sent paths a settings object holds (`held`) and holds with another value
 * (`other`); null when one of them is not reported as a switch or a number.
 */
function heldPaths(
  settings: unknown,
  changes: Readonly<Record<string, unknown>>,
): { readonly held: readonly string[]; readonly other: readonly string[] } | null {
  const held: string[] = [];
  const other: string[] = [];
  for (const [path, value] of Object.entries(changes)) {
    if (path === 'shopping_plan') {
      const now = own(settings, path);
      if (queuePlan(now) === null || !planHolds(now, value)) return null;
      held.push(path);
      continue;
    }
    const parts = splitPath(path);
    const now =
      parts === null ? undefined : ownPath(settings, 'routines', parts.routine, parts.field);
    if (typeof now !== 'boolean' && typeof now !== 'number') return null;
    (now === value ? held : other).push(path);
  }
  return { held, other };
}

/**
 * 200 is a receipt only when it is the read of the same run with a new
 * `profile_revision` whose settings report every sent path: all of them with
 * the sent value is `saved`, some with another value `saved_partly` (the
 * server saved the run but kept those values). Anything less, a malformed 200
 * included, is unknown and keeps the draft (F6R-02). 400 (a refused value, an
 * unknown run, no own profile, a profile problem) and 409 (the saved run or the
 * context changed, a starter or draft profile, the request lock) are
 * `{ok:false, error}` before anything was written; every other answer (a text
 * 500 included: the write may or may not have happened) leaves it unknown.
 */
export function savedSaveAnswer(
  answer: { readonly status: number; readonly body: unknown },
  sent: SentSave,
): SavedSaveAnswer {
  const profileRevision = id(own(answer.body, 'profile_revision'));
  const { blueprint, changes } = sent;
  if (
    answer.status === 200 &&
    own(answer.body, 'ok') === true &&
    profileRevision !== undefined &&
    blueprint !== undefined &&
    own(answer.body, 'blueprint') === blueprint &&
    sent.profileRevision !== undefined &&
    profileRevision !== sent.profileRevision &&
    changes !== undefined
  ) {
    const paths = heldPaths(own(answer.body, 'settings'), changes);
    if (paths !== null && paths.other.length === 0) return { kind: 'saved', profileRevision };
    if (paths !== null) return { kind: 'saved_partly', profileRevision, kept: paths.held };
  }
  if ((answer.status === 400 || answer.status === 409) && own(answer.body, 'ok') === false)
    return { kind: 'refused', code: own(answer.body, 'code'), conflict: answer.status === 409 };
  return { kind: 'unknown' };
}

/**
 * What a read of the same run says about the values a save sent: `unchanged` (the
 * revision the save was made against: nothing was written since), `applied` (a newer
 * revision whose settings hold every sent value), `changed` (a newer revision without
 * all of them: someone else saved). Null: the read is not of that run.
 */
export function savedReadback(
  readBody: unknown,
  sent: {
    readonly blueprint: string;
    readonly profileRevision: string;
    readonly changes: Readonly<Record<string, unknown>>;
  },
): 'applied' | 'changed' | 'unchanged' | null {
  if (own(readBody, 'blueprint') !== sent.blueprint) return null;
  const now = id(own(readBody, 'profile_revision'));
  if (now === undefined) return null;
  if (now === sent.profileRevision) return 'unchanged';
  const paths = heldPaths(own(readBody, 'settings'), sent.changes);
  return paths !== null && paths.other.length === 0 ? 'applied' : 'changed';
}

/**
 * What a later read of the same run says about a save whose outcome is
 * unknown (brief RUN-07): a new revision whose settings hold every sent value
 * means the saved settings now have the changes (`applied`). Anything else
 * is null: an unchanged revision or values not (yet) there never prove the
 * save failed, because the original request may still commit (the shared rule
 * of Collection review F6C-R1-02). The save stays unknown, the draft stays,
 * and the person may continue from the read explicitly.
 */
export function settleSavedSave(
  readBody: unknown,
  sent: {
    readonly blueprint: string;
    readonly profileRevision: string;
    readonly changes: Readonly<Record<string, unknown>>;
  },
): 'applied' | null {
  return savedReadback(readBody, sent) === 'applied' ? 'applied' : null;
}
