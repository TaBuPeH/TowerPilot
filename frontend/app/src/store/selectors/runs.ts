import {
  actionControlsBlocked,
  actionsReading,
  actionsView,
  COMPAT_FIELDS,
  hasLiveBounds,
  liveConstraints,
  liveFieldOrder,
  orderedFields,
} from '../../core/actions.ts';
import { battleIdentity, battleServerNow, freshBattleReport } from '../../core/battle.ts';
import { blueprintOf } from '../../core/gemSource.ts';
import { entries, finite, own, ownPath, text } from '../../core/read.ts';
import { readiness, runDefined, type ReadinessView } from '../../core/readiness.ts';
import {
  controlGroups,
  liveControls,
  liveFieldNullable,
  liveMark,
  liveOverrideLeaves,
  liveResetGate,
  liveResetKey,
  liveSaveGate,
  reviewDraft,
  savedSaveGate,
  type ControlGroup,
  type DraftValues,
  type InvalidReason,
  type LiveFieldDescription,
  type LiveMark,
  type LiveOverrideLeaf,
  type SaveRequest,
} from '../../core/runControls.ts';
import {
  adGemsDetails,
  floatingRefusal,
  liveChangeProgress,
  routineEventsQuery,
  type LiveChangeProgress,
  type AdCollection,
  type AdGemsDetails,
} from '../../core/routines.ts';
import { runtimeHash } from '../../core/runtime.ts';
import { opaqueToken } from '../../core/tokens.ts';
import type { StateWord } from '../../core/vocabulary.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { ResetLiveCommand, SaveLiveCommand, SaveRunCommand } from '../commands.ts';
import { refusedSaveReadback, settledByRead } from '../fences.ts';
import {
  contextIntents,
  contextScopeKey,
  hasConfirmedContext,
  unresolvedIntent,
} from '../intents.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState, IntentView, ReadRecord } from '../types.ts';
import { commandVM, type CommandVM } from '../viewModels.ts';
import { runControlsPath } from './gemSlot.ts';
import { selectOutcomes, type OutcomeLineVM } from './outcomes.ts';
import { liveBattleRun, runsPageRun } from './readPlan.ts';

/**
 * The Runs page (brief backend/logs/reviews/ui-pages/runs-brief.md): the run
 * picker, the saved settings of the shown run and the live settings of the
 * tracked battle, each as an editor with its own draft and save (SR-1). Every
 * decision is core's (core/runControls.ts); this file joins reads, drafts and
 * write records into view models.
 */
export interface RunFieldVM {
  readonly path: string;
  readonly routine: string;
  readonly field: string;
  readonly kind: 'toggle' | 'number';
  /** The value the read reports (null: not reported). */
  readonly value: boolean | number | null;
  /** What the field shows: the person's draft when there is one, else the reported value. */
  readonly shown: string | boolean | null;
  /** The draft differs from the reported value (or is invalid). */
  readonly edited: boolean;
  readonly invalid: InvalidReason | null;
  readonly min: number | null;
  readonly max: number | null;
  /** Live rows only: changed for this battle, waiting to apply, or as saved. */
  readonly mark: LiveMark | null;
  /** A2-7 AC2: the descriptor's label (live rows of a described item), else null (copy label). */
  readonly label: ServerText<'name'> | null;
  /** A2-7 AC2: the unit after a number: seconds (copy), none, or the descriptor's unit as sent. */
  readonly unit: RunUnitVM;
}

export type RunUnitVM =
  | { readonly kind: 'seconds' }
  | { readonly kind: 'none' }
  | { readonly kind: 'other'; readonly text: ServerText<'name'> };

/** A2-7 AC2: a listed control whose descriptor type the page does not know (never editable). */
export interface UnrecognisedFieldVM {
  readonly path: string;
  readonly field: string;
  readonly label: ServerText<'name'> | null;
  readonly type: ServerText<'id'> | null;
}

/** A2-7 AC2: one cell of a row in field order: an editable field or an unrecognised one. */
export type RunCellVM =
  | { readonly kind: 'field'; readonly field: RunFieldVM }
  | { readonly kind: 'unrecognised'; readonly field: UnrecognisedFieldVM };

export interface RunGroupVM {
  readonly routine: string;
  /** A2-7 AC2: the projection's label of the item, else null (copy name). */
  readonly label: ServerText<'name'> | null;
  readonly fields: readonly RunFieldVM[];
  /** A2-7 AC2: `fields` and the unrecognised fields together, in the routine's field order. */
  readonly cells: readonly RunCellVM[];
}

/** A2-7 AC2 (gap G2): an item whose controls the battle does not address; read-only. */
export interface UnaddressableVM {
  readonly id: string;
  readonly instance: string;
  readonly label: ServerText<'name'> | null;
  readonly reason: 'control_path_unpublished';
}

export interface EditorVM<C> {
  /** The draft's scope (store `runDrafts` key). */
  readonly scope: string;
  readonly groups: readonly RunGroupVM[];
  /**
   * A2-7 AC2: where field labels come from. `copy`: catalogue labels, named once by the column
   * header (the saved editor, Q5, and a battle without a projection); `descriptor`: some row
   * carries descriptor labels, so every field shows its own.
   */
  readonly labels: 'copy' | 'descriptor';
  /** A2-7 AC2: items of the battle that cannot be edited here (empty for the saved editor). */
  readonly unaddressable: readonly UnaddressableVM[];
  readonly save: CommandVM<C>;
  /** Discard is offered while the scope holds any draft. */
  readonly discard: boolean;
  /** How many fields the save would change, and how many are invalid. */
  readonly changeCount: number;
  readonly invalidCount: number;
  /**
   * F6-11 RL1: the live editor's overrides of this battle, each with its reset to the
   * battle's starting value (empty for the saved editor and for a battle with none).
   */
  readonly resets: readonly LiveResetVM[];
  /**
   * F6-11 RL2: every override of this battle reset in one request (null for each path), when
   * it has two or more; null otherwise (one override uses its own reset).
   */
  readonly resetAll: LiveResetAllVM | null;
}

/** One override of a reset, with the projection's names when the battle sends them (A2-7 AC2). */
export type LiveOverrideVM =
  | (Extract<LiveOverrideLeaf, { readonly kind: 'routine' }> & OverrideNamesVM)
  | Extract<LiveOverrideLeaf, { readonly kind: 'shopping_plan' }>;

/** The item and field labels of an override (null: the catalogue's names apply). */
export interface OverrideNamesVM {
  readonly routineLabel: ServerText<'name'> | null;
  readonly label: ServerText<'name'> | null;
}

export interface LiveResetAllVM {
  readonly items: readonly LiveOverrideVM[];
  /** The target the reset would be sent to (core liveResetKey), or null while it is not offered. */
  readonly target: string | null;
  readonly reset: CommandVM<ResetLiveCommand>;
}

export interface LiveResetVM extends OverrideNamesVM {
  readonly path: string;
  readonly routine: string;
  readonly field: string;
  readonly reset: CommandVM<ResetLiveCommand>;
  /** The target the reset would be sent to (core liveResetKey), or null while it is not offered. */
  readonly target: string | null;
}

/**
 * What an editor's newest save says (RUN-05 to RUN-07, RUN-10): RunOutcome
 * maps the code to catalogue words and renders only the actions offered here.
 */
export type RunOutcomeCode =
  | 'sending'
  | 'saved'
  | 'saved_partly'
  | 'not_saved'
  | 'not_saved_changed'
  | 'unknown_save'
  | 'unconfirmed_save'
  | 'applied'
  | 'not_applied'
  | 'live_recorded'
  | 'earlier_recorded'
  | 'live_not_recorded'
  | 'live_unknown'
  | 'live_unconfirmed'
  | 'live_settled'
  | 'live_applied'
  | 'live_not_applied';

export interface RunOutcomeVM {
  /** The write record's line (id, status word, answer age, the server's message). */
  readonly line: OutcomeLineVM;
  readonly code: RunOutcomeCode;
  /** The line's status word: the record's, except Failed for a change its battle did not apply. */
  readonly word: StateWord | null;
  /**
   * SR-8: a saved-run save whose outcome is unknown keeps its line (the
   * editor's Check saved values is its way out); any other answered line may
   * be dismissed. Dismissing hides the line only: the record and its fence stay.
   */
  readonly dismissOffered: boolean;
  /** RUN-10: "Send same request again" (identical body and key), only for an unknown live change. */
  readonly resendOffered: boolean;
  /**
   * An unknown saved-run save after a read of its run made after its answer (or its loss):
   * the person may continue from that read ("accept the current state"); the outcome stays
   * unknown and nothing is resent. True once accepted: `accepted`.
   */
  readonly acceptOffered: boolean;
  readonly accepted: boolean;
  /**
   * F6R-04: a live change made to a battle the page no longer shows (it ended or was
   * replaced): its line names that battle and offers a read of it by id. Null otherwise.
   */
  readonly earlier: {
    readonly label: ServerText<'name'> | null;
    /** "Check that battle" (a GET of that battle by id) while the outcome is unknown. */
    readonly checkOffered: boolean;
  } | null;
}

export interface RunChoiceVM {
  readonly key: string;
  readonly name: ServerText<'id'> | null;
  readonly label: ServerText<'name'> | null;
}

export interface SavedSectionVM {
  readonly runs: readonly RunChoiceVM[];
  /** Combo is listed by /api/runs but has no controls of its own (RUN-01). */
  readonly comboListed: boolean;
  readonly run: string | null;
  /**
   * S-081: false when the run this page was opened on is not among the profile's runs
   * (or the runs are not read yet): the page says so and never shows another run instead.
   */
  readonly runListed: boolean;
  /** Config and the run catalogue have established the draft scope. */
  readonly scopeReady: boolean;
  readonly catalogueUnavailable: boolean;
  readonly catalogueError: ServerText<'message'> | null;
  /** The run's key as server text, for a run the list does not hold. */
  readonly runName: ServerText<'name'> | null;
  readonly runLabel: ServerText<'name'> | null;
  readonly readiness: ReadinessView['line'] | null;
  /** The shown run's read: not read yet, failed or refused, or answered. */
  readonly read: 'not_read' | 'unavailable' | 'ready';
  readonly readError: ServerText<'message'> | null;
  readonly readAgeSec: number | null;
  /** The driver's own scope notice (`notice`), when the read sends one. */
  readonly notice: ServerText<'message'> | null;
  readonly editor: EditorVM<SaveRunCommand> | null;
  /** The newest save of exactly this run in this context (F6R-03), or null. */
  readonly outcome: RunOutcomeVM | null;
}

export interface LiveSectionVM {
  /** No battle tracked, the battle read missing or failed, or a tracked battle. */
  readonly state: 'none' | 'unavailable' | 'tracked';
  readonly label: ServerText<'name'> | null;
  readonly wave: number | null;
  readonly active: boolean;
  /** The run whose saved bounds the live numbers are checked against (RUN-11), when read. */
  readonly boundsKnown: boolean;
  readonly editor: EditorVM<SaveLiveCommand> | null;
  /**
   * F6R-04: live changes made to battles the page no longer shows, while their outcome is
   * unknown, after a person's resend of them, or once a read settled them (until dismissed).
   * Their drafts stay with their own battle: none is shown in this battle's editor.
   */
  readonly earlier: readonly RunOutcomeVM[];
  /** The newest live change of exactly this battle and generation (F6R-03), or null. */
  readonly outcome: RunOutcomeVM | null;
  /** F6-11 RL7: each reported routine's details (empty without a tracked battle). */
  readonly details: readonly RoutineDetailsVM[];
  /** The battle report is not fresh: the details are the last known ones. */
  readonly detailsLastKnown: boolean;
}

/** An Ad gems time: the server's date (seconds) and how long ago or how soon, from the server clock. */
export interface TimeVM {
  readonly at: number;
  /** Seconds since `at` (negative: still to come), or null without a server clock. */
  readonly sinceSec: number | null;
}

export interface AdDetailsVM {
  readonly visibility: 'visible' | 'absent' | 'unknown';
  readonly visibilityReason: ServerText<'id'> | null;
  readonly observationStale: boolean;
  readonly firstSeen: TimeVM | null;
  readonly appearance: {
    readonly after: TimeVM;
    readonly by: TimeVM;
    readonly gapSec: number;
  } | null;
  readonly lastObserved: TimeVM | null;
  readonly collection:
    | { readonly kind: 'off' }
    | { readonly kind: 'window'; readonly responseDue: TimeVM | null; readonly windowEnds: TimeVM }
    | { readonly kind: 'scheduled'; readonly nextDue: TimeVM | null };
  readonly nextCheck: TimeVM | null;
  readonly failedAttempt: {
    readonly reason: ServerText<'id'> | null;
    readonly at: TimeVM | null;
  } | null;
  readonly counters: AdGemsDetails['counters'];
  readonly durations: AdGemsDetails['durations'];
}

/** F6-11 RL7: one routine's details in the live section (spec F-062, F-063). */
export interface RoutineDetailsVM {
  readonly routine: string;
  /** The routine's name as sent, shown beside "Other routine" when the page has no word for it. */
  readonly code: ServerText<'id'> | null;
  /** The server's own `runtime.routines.<routine>` object, as sent (diagnostic body). */
  readonly raw: ServerText<'detail'> | null;
  readonly ad: AdDetailsVM | null;
  /** Floating gems only: its latest refusal, or null when none is recorded. */
  readonly refusal:
    { readonly reason: ServerText<'id'> | null; readonly at: TimeVM | null } | null | undefined;
  /** Runtime page events of this battle (and offer), `#runtime?...`. */
  readonly inspectHash: string;
}

export interface RunsPageVM {
  readonly saved: SavedSectionVM;
  readonly live: LiveSectionVM;
  readonly epoch: number;
}

/** The draft scope of a run's saved settings in this context (SR-2). */
export function savedScope(state: DashboardState, run: string): string {
  return `saved:${contextScopeKey(state.context)}:${run}`;
}

/** The draft scope of a battle's live settings (SR-2: never carried to another battle). */
export function liveScope(battleId: string, generation: string): string {
  return `live:${battleId}:${generation}`;
}

/**
 * The save records of one editor in this context, oldest first: a saved run's
 * `run:<blueprint>`, or a battle's `live:<battle>` of one generation (a live
 * change is never carried to another battle or generation, SR-2).
 */
function editorRecords(
  state: DashboardState,
  target: string,
  generation: string | null,
): readonly IntentView[] {
  return contextIntents(state).filter(
    (record) =>
      record.target === target && (generation === null || record.meta.generation === generation),
  );
}

/**
 * The newest of an editor's saves (SR-4, SR-8): in flight, unknown with no
 * read settling it, answered after `readStartedSeq` (the target not read
 * since), or none. A dismissed record still counts: dismissing hides a line,
 * never its fence.
 */
function requestOn(
  state: DashboardState,
  records: readonly IntentView[],
  readStartedSeq: number,
): SaveRequest {
  const latest = records.filter((record) => record.supersededBySeq === null).at(-1);
  if (latest === undefined) return 'none';
  if (latest.phase === 'sending') return 'sending';
  if (unresolvedIntent(latest))
    return settledByRead(state, latest) === null && latest.accepted !== true
      ? 'unresolved'
      : 'none';
  return latest.answerSeq !== null && latest.answerSeq >= readStartedSeq ? 'refreshing' : 'none';
}

/** F6-11-IR-01: the newest answered battle read made after the change was sent, about its battle. */
function liveProgress(state: DashboardState, record: IntentView): LiveChangeProgress {
  const read = state.reads.battlesActive.lastAnswered;
  const { battleId, generation, operationId } = record.meta;
  if (
    read === null ||
    read.startedSeq <= record.sentSeq ||
    battleId === undefined ||
    generation === undefined ||
    operationId === undefined
  )
    return 'not_listed';
  return liveChangeProgress(
    contextReadOf(read),
    { id: battleId, generation, operationId },
    record.answer?.body,
    opaqueToken(record.key),
    record.meta.resetPaths,
  );
}

function outcomeCode(
  state: DashboardState,
  record: IntentView,
  line: OutcomeLineVM,
  earlier: boolean,
): RunOutcomeCode {
  if (line.phase === 'sending') return 'sending';
  // F6-11-IR-01: a recorded live change (its answer, or a later read listing its key) follows
  // what a later read of its own battle says its command did.
  if (
    line.kind === 'live_controls' &&
    (line.outcome === 'recorded' || line.settledByRead === 'recorded')
  ) {
    const progress = liveProgress(state, record);
    if (progress === 'applied') return 'live_applied';
    if (progress === 'not_applied') return 'live_not_applied';
  }
  if (line.settledByRead === 'applied') return 'applied';
  if (line.settledByRead === 'not_applied') return 'not_applied';
  if (line.settledByRead === 'recorded') return 'live_settled';
  switch (line.outcome) {
    case 'saved':
      return 'saved';
    case 'saved_partly':
      return 'saved_partly';
    case 'refused':
      // F6R-03: a conflict whose later read shows someone else's newer save says so.
      return refusedSaveReadback(state, record)?.readback === 'changed'
        ? 'not_saved_changed'
        : 'not_saved';
    case 'recorded':
      return earlier ? 'earlier_recorded' : 'live_recorded';
    case 'not_recorded':
      return 'live_not_recorded';
    default:
      // No reply at all, or a reply that proves nothing (a text 500, command_outcome_unknown).
      if (line.kind === 'live_controls')
        return line.outcome === 'no_answer' ? 'live_unknown' : 'live_unconfirmed';
      return line.outcome === 'no_answer' ? 'unknown_save' : 'unconfirmed_save';
  }
}

/** The outcome is not known: no read settled it yet (SR-8). */
const UNKNOWN_CODES: readonly RunOutcomeCode[] = [
  'unknown_save',
  'unconfirmed_save',
  'live_unknown',
  'live_unconfirmed',
];

/**
 * The newest current, undismissed line among an editor's own records (F6R-03:
 * never another run's, battle's or generation's).
 */
function editorOutcome(
  state: DashboardState,
  lines: readonly OutcomeLineVM[],
  records: readonly IntentView[],
): RunOutcomeVM | null {
  const ids = new Set(records.map((record) => record.id));
  const line = lines.filter((entry) => ids.has(entry.id) && entry.current).at(-1);
  const record = records.find((entry) => entry.id === line?.id);
  return line === undefined || record === undefined ? null : outcomeVM(state, record, line, null);
}

function outcomeVM(
  state: DashboardState,
  record: IntentView,
  line: OutcomeLineVM,
  earlier: { readonly label: ServerText<'name'> | null } | null,
): RunOutcomeVM {
  const code = outcomeCode(state, record, line, earlier !== null);
  const unknownSave = code === 'unknown_save' || code === 'unconfirmed_save';
  const accepted = record.accepted === true;
  const point = Math.max(record.sentSeq, record.answerSeq ?? record.lostSeq ?? 0);
  const readSince = Object.values(state.runControls).some(
    (read) =>
      read.startedSeq > point &&
      read.outcome.kind === 'answered' &&
      own(read.outcome.body, 'blueprint') === record.meta.blueprint,
  );
  return {
    line,
    code,
    word: code === 'live_not_applied' ? 'failed' : line.word,
    dismissOffered: code !== 'sending' && (!unknownSave || accepted),
    acceptOffered: unknownSave && !accepted && record.kind === 'run_save' && readSince,
    accepted: unknownSave && accepted,
    resendOffered:
      line.resendOffered && line.kind === 'live_controls' && UNKNOWN_CODES.includes(code),
    earlier: earlier === null ? null : { ...earlier, checkOffered: UNKNOWN_CODES.includes(code) },
  };
}

/**
 * F6R-04: the live changes of this context made to a battle other than the one shown
 * (none shown, another battle, another generation), while they still matter: unknown,
 * resent by a person, or settled by a read; oldest first, dismissed ones left out.
 */
function earlierOutcomes(
  state: DashboardState,
  lines: readonly OutcomeLineVM[],
  shown: { readonly id: string; readonly generation: string } | null,
): readonly RunOutcomeVM[] {
  return contextIntents(state).flatMap((record): RunOutcomeVM[] => {
    if (record.kind !== 'live_controls') return [];
    if (
      shown !== null &&
      record.meta.battleId === shown.id &&
      record.meta.generation === shown.generation
    )
      return [];
    const line = lines.find((entry) => entry.id === record.id && entry.current);
    if (line === undefined) return [];
    if (!unresolvedIntent(record) && record.sends === 1 && line.settledByRead === null) return [];
    return [outcomeVM(state, record, line, { label: fromServer('name', record.meta.battleLabel) })];
  });
}

/**
 * The typed values a save submits, per changed path (F6R-09): what a
 * confirmation of exactly those values may clear from the draft (SR-2).
 */
function submittedDraft(
  draft: DraftValues,
  changes: Readonly<Record<string, boolean | number>>,
): DraftValues {
  return Object.fromEntries(
    Object.keys(changes).flatMap((path) => {
      const value = draft[path];
      return value === undefined ? [] : [[path, value] as const];
    }),
  );
}

/** What a described live editor knows beyond the fields (A2-7 AC2); the saved editor has none. */
interface Described {
  readonly description: (path: string) => LiveFieldDescription | undefined;
  readonly itemLabel: (routine: string) => unknown;
  readonly unrecognised: (routine: string) => readonly UnrecognisedFieldVM[];
}

const UNDESCRIBED: Described = {
  description: () => undefined,
  itemLabel: () => undefined,
  unrecognised: () => [],
};

function unitVM(
  field: ControlGroup['fields'][number],
  description: LiveFieldDescription | undefined,
): RunUnitVM {
  if (field.kind !== 'number') return { kind: 'none' };
  // A field no descriptor describes is in seconds, as every run control was before A2-7.
  const unit = description?.unit ?? { kind: 'seconds' };
  if (unit.kind !== 'other') return unit;
  const text = fromServer('name', unit.text);
  return text === null ? { kind: 'none' } : { kind: 'other', text };
}

function fieldCell(field: RunFieldVM): RunCellVM {
  return { kind: 'field', field };
}

function unrecognisedCell(field: UnrecognisedFieldVM): RunCellVM {
  return { kind: 'unrecognised', field };
}

function fieldsVM(
  groups: readonly ControlGroup[],
  draft: DraftValues,
  invalid: Readonly<Record<string, InvalidReason>>,
  changes: Readonly<Record<string, boolean | number>>,
  mark: (field: ControlGroup['fields'][number]) => LiveMark | null,
  order: (routine: string) => readonly string[],
  described: Described = UNDESCRIBED,
): readonly RunGroupVM[] {
  return groups.map((group) => {
    // H9: each routine's fields in its field order (descriptor or compatibility), then the rest.
    const fields = orderedFields(group.fields, order(group.routine)).map((field): RunFieldVM => {
      const typed = draft[field.path];
      const description = described.description(field.path);
      return {
        path: field.path,
        routine: field.routine,
        field: field.field,
        kind: field.kind,
        value: field.value,
        shown:
          typed !== undefined
            ? typed
            : field.kind === 'number'
              ? field.value === null
                ? ''
                : String(field.value)
              : typeof field.value === 'boolean'
                ? field.value
                : null,
        edited: Object.hasOwn(changes, field.path) || Object.hasOwn(invalid, field.path),
        invalid: invalid[field.path] ?? null,
        min: field.min,
        max: field.max,
        mark: mark(field),
        label: fromServer('name', description?.label),
        unit: unitVM(field, description),
      };
    });
    const entries: { readonly field: string; readonly cell: RunCellVM }[] = [
      ...fields.map((field) => ({ field: field.field, cell: fieldCell(field) })),
      ...described.unrecognised(group.routine).map((field) => ({
        field: field.field,
        cell: unrecognisedCell(field),
      })),
    ];
    const cells = orderedFields(entries, order(group.routine)).map((entry) => entry.cell);
    return {
      routine: group.routine,
      label: fromServer('name', described.itemLabel(group.routine)),
      fields,
      cells,
    };
  });
}

function answered(read: ReadRecord | null): boolean {
  return read !== null && read.outcome.kind === 'answered' && read.outcome.status === 200;
}

function savedSection(state: DashboardState, lines: readonly OutcomeLineVM[]): SavedSectionVM {
  const runsBody = answeredBody(state, 'runs');
  const runs = entries(own(runsBody, 'runs')).flatMap(([key, run]): RunChoiceVM[] =>
    !runDefined(run) || key === 'combo'
      ? []
      : [{ key, name: fromServer('id', key), label: fromServer('name', own(run, 'label')) }],
  );
  const wanted = runsPageRun(state);
  // A battle names its run without the `bp_` of /api/runs (dashboard.py strips it): one run.
  const listed =
    wanted === null
      ? null
      : (runs.find((choice) => choice.key === wanted || choice.key === `bp_${wanted}`)?.key ??
        wanted);
  const run = listed === 'combo' ? (runs[0]?.key ?? null) : listed;
  const read = run === null ? null : (state.runControls[runControlsPath(run)] ?? null);
  const records = run === null ? [] : editorRecords(state, `run:${blueprintOf(run)}`, null);
  const catalogueReady = answered(state.reads.runs.lastAnswered);
  const catalogueRead = state.reads.runs.latest;
  const runListed = run !== null && runs.some((choice) => choice.key === run);
  const base = {
    runs,
    comboListed: entries(own(runsBody, 'runs')).some(([key]) => key === 'combo'),
    run,
    runListed,
    scopeReady: hasConfirmedContext(state) && catalogueReady,
    catalogueUnavailable: !catalogueReady && catalogueRead !== null,
    catalogueError:
      !catalogueReady && catalogueRead?.outcome.kind === 'answered'
        ? fromServer('message', own(catalogueRead.outcome.body, 'error'))
        : null,
    runName: run === null ? null : fromServer('name', run),
    runLabel: fromServer('name', ownPath(runsBody, 'runs', run ?? '', 'label')),
    readiness: run === null ? null : readiness(ownPath(runsBody, 'readiness', run)).line,
    readAgeSec: read === null ? null : readAgeSec(state, read),
    outcome: editorOutcome(state, lines, records),
  };
  if (run === null || read === null || !base.scopeReady)
    return { ...base, read: 'not_read', readError: null, notice: null, editor: null };
  const body = read.outcome.kind === 'answered' ? read.outcome.body : undefined;
  if (!answered(read)) {
    return {
      ...base,
      read: 'unavailable',
      readError: fromServer('message', own(body, 'error')),
      notice: null,
      editor: null,
    };
  }
  const groups = controlGroups(
    ownPath(body, 'capabilities', 'controls'),
    own(body, 'settings'),
    own(body, 'constraints'),
  );
  const scope = savedScope(state, run);
  const draft = state.runDrafts[scope] ?? {};
  const review = reviewDraft(groups, draft);
  const context = contextReadOf(read);
  const request = requestOn(state, records, read.startedSeq);
  const gate = savedSaveGate(
    context?.kind === 'answered' ? { body: context.body, sameContext: context.sameContext } : null,
    review,
    request,
  );
  return {
    ...base,
    read: 'ready',
    readError: null,
    notice: fromServer('message', own(body, 'notice')),
    editor: {
      scope,
      groups: fieldsVM(
        groups,
        draft,
        review.invalid,
        review.changes,
        () => null,
        () => COMPAT_FIELDS,
      ),
      // Root decision Q5: the saved editor keeps the catalogue labels until B12.
      labels: 'copy',
      unaddressable: [],
      save: gate.enabled
        ? commandVM<SaveRunCommand>(true, true, null, false, {
            epoch: state.epoch,
            scope,
            blueprint: gate.command.blueprint,
            context: gate.command.context,
            expectedRevision: gate.command.expectedRevision,
            changes: gate.command.changes,
            draft: submittedDraft(draft, gate.command.changes),
          })
        : commandVM<SaveRunCommand>(true, false, gate.reason, request === 'sending', null),
      discard: Object.keys(draft).length > 0,
      changeCount: Object.keys(review.changes).length,
      invalidCount: Object.keys(review.invalid).length,
      resets: [],
      resetAll: null,
    },
  };
}

function liveSection(state: DashboardState, lines: readonly OutcomeLineVM[]): LiveSectionVM {
  const read = latestRead(state, 'battlesActive');
  const last = state.reads.battlesActive.lastAnswered;
  const body = last !== null && last.outcome.kind === 'answered' ? last.outcome.body : undefined;
  const battle = own(body, 'battle');
  if (read === null || (read.outcome.kind === 'failed' && last === null))
    return {
      state: 'unavailable',
      label: null,
      wave: null,
      active: false,
      boundsKnown: false,
      editor: null,
      outcome: null,
      earlier: earlierOutcomes(state, lines, null),
      details: [],
      detailsLastKnown: false,
    };
  if (battle === null || battle === undefined)
    return {
      state: 'none',
      label: null,
      wave: null,
      active: false,
      boundsKnown: false,
      editor: null,
      outcome: null,
      earlier: earlierOutcomes(state, lines, null),
      details: [],
      detailsLastKnown: false,
    };
  const identity = battleIdentity(battle, last === null ? NaN : last.arrivedAtMs / 1000);
  const shown = identity.kind === 'known' ? identity.value : null;
  const records = shown === null ? [] : editorRecords(state, `live:${shown.id}`, shown.generation);
  const base = {
    state: 'tracked' as const,
    label: fromServer('name', own(battle, 'label')),
    wave:
      finite(ownPath(battle, 'runtime', 'observed_wave')) ?? finite(own(battle, 'wave')) ?? null,
    active: own(battle, 'status') === 'active',
    outcome: editorOutcome(state, lines, records),
    earlier: earlierOutcomes(state, lines, shown),
  };
  const boundsRun = liveBattleRun(state);
  const boundsRead = boundsRun === null ? undefined : state.runControls[runControlsPath(boundsRun)];
  const boundsBody =
    boundsRead !== undefined &&
    answered(boundsRead) &&
    boundsRead.outcome.kind === 'answered' &&
    contextReadOf(boundsRead)?.kind === 'answered'
      ? boundsRead.outcome.body
      : undefined;
  const elapsedDetails = last === null ? 0 : readAgeSec(state, last);
  const now = battleServerNow(body, elapsedDetails);
  const timeAt = (at: number): TimeVM => ({
    at,
    sinceSec: now === null ? null : Math.floor(now - at),
  });
  const time = (at: number | null): TimeVM | null => (at === null ? null : timeAt(at));
  const collection = (c: AdCollection): AdDetailsVM['collection'] =>
    c.kind === 'off'
      ? c
      : c.kind === 'window'
        ? { kind: 'window', responseDue: time(c.responseDueAt), windowEnds: timeAt(c.windowEndsAt) }
        : { kind: 'scheduled', nextDue: time(c.nextDueAt) };
  const routines = ownPath(battle, 'runtime', 'routines');
  const details: RoutineDetailsVM[] = entries(routines).map(([routine, raw]) => {
    const ad = routine === 'ad_gems' ? adGemsDetails(raw, now) : null;
    const refusal = routine === 'floating_gems' ? floatingRefusal(raw) : undefined;
    return {
      routine,
      code: fromServer('id', routine),
      raw: fromServer('detail', JSON.stringify(raw, null, 2)),
      ad:
        ad === null
          ? null
          : {
              visibility: ad.visibility,
              visibilityReason: fromServer('id', ad.visibilityReason),
              observationStale: ad.observationStale,
              firstSeen: time(ad.firstSeenAt),
              appearance:
                ad.appearance === null
                  ? null
                  : {
                      after: timeAt(ad.appearance.afterAt),
                      by: timeAt(ad.appearance.byAt),
                      gapSec: ad.appearance.gapSec,
                    },
              lastObserved: time(ad.lastObservedAt),
              collection: collection(ad.collection),
              nextCheck: time(ad.nextCheckAt),
              failedAttempt:
                ad.failedAttempt === null
                  ? null
                  : {
                      reason: fromServer('id', ad.failedAttempt.reason),
                      at: time(ad.failedAttempt.observedAt),
                    },
              counters: ad.counters,
              durations: ad.durations,
            },
      refusal:
        refusal === undefined
          ? undefined
          : refusal === null
            ? null
            : { reason: fromServer('id', refusal.reason), at: time(refusal.observedAt) },
      inspectHash: runtimeHash(routineEventsQuery(battle, raw)),
    };
  });
  const detailsFresh =
    freshBattleReport(contextReadOf(last), shown, elapsedDetails).kind === 'known';
  // Review F6-11-R1-02: a newest battle read that failed makes every retained value last known,
  // however young the retained report is.
  const newestFailed = read.outcome.kind === 'failed';
  const withDetails = {
    ...base,
    details,
    detailsLastKnown: details.length > 0 && (newestFailed || !detailsFresh),
  };
  if (shown === null)
    return { ...withDetails, boundsKnown: boundsBody !== undefined, editor: null };
  const actions = actionsView(battle);
  // A2-7 AC2: the fields of each described routine are its item's live descriptor fields, of the
  // descriptor's type; R2 (H10): their bounds are the descriptor's, else the saved run's.
  const controls = liveControls(
    ownPath(battle, 'capabilities', 'controls'),
    own(battle, 'effective'),
    liveConstraints(actions, own(boundsBody, 'constraints')),
    actions,
  );
  const unrecognised = controls.unrecognised.map((field) => ({
    routine: field.routine,
    vm: {
      path: field.path,
      field: field.field,
      label: fromServer('name', field.label),
      type: fromServer('id', field.type),
    },
  }));
  // A routine whose every described field is unrecognised still gets its row.
  const groups = [
    ...controls.groups,
    ...[...new Set(unrecognised.map((field) => field.routine))]
      .filter((routine) => !controls.groups.some((group) => group.routine === routine))
      .map((routine) => ({ routine, fields: [] })),
  ];
  const described: Described = {
    description: (path) => controls.descriptions.get(path),
    itemLabel: (routine) => controls.itemLabels.get(routine),
    unrecognised: (routine) =>
      unrecognised.filter((field) => field.routine === routine).map((field) => field.vm),
  };
  const scope = liveScope(shown.id, shown.generation);
  const draft = state.runDrafts[scope] ?? {};
  const review = reviewDraft(groups, draft);
  const elapsed = last === null ? 0 : readAgeSec(state, last);
  const fresh = freshBattleReport(contextReadOf(last), shown, elapsed).kind === 'known';
  // Root decision Q2 (AC5 rule of core/actions): old, unreadable or foreign action readings
  // close live changes with their reason.
  const blocked = actionControlsBlocked(actionsReading(body, elapsed));
  const request = requestOn(state, records, last?.startedSeq ?? 0);
  // SQ5 (core/runControls.rules.md): a live write waits for the config read that confirms
  // the page context. A battle read can answer first; the confirming read then starts a new
  // epoch that drops it, so a save or reset offered before then would vanish under the
  // person's hand (an open reset confirmation closed by itself).
  const contextReady = hasConfirmedContext(state);
  const gate = liveSaveGate(battle, fresh, review, request, blocked);
  // F6-11 RL1/RL2: the applied overrides of this battle, and the reset of some of them (one
  // path, or every path at once), bound to the target it would be sent to.
  const changed = groups.flatMap((group) =>
    group.fields.filter((field) => liveMark(battle, field) === 'changed'),
  );
  // A2-7 AC2: a reset sends null, so only a field its descriptor marks nullable may be reset.
  const leaves = liveOverrideLeaves(battle).filter(
    (leaf) => leaf.kind !== 'routine' || liveFieldNullable(actions, leaf.routine, leaf.field),
  );
  const names = (routine: string, field: string): OverrideNamesVM => ({
    routineLabel: fromServer('name', controls.itemLabels.get(routine)),
    label: fromServer('name', controls.descriptions.get(`routines.${routine}.${field}`)?.label),
  });
  const resetOf = (paths: readonly string[]) => {
    const reset = liveResetGate(battle, fresh, paths, request, blocked);
    return contextReady && reset.enabled
      ? {
          target: liveResetKey(reset.command),
          reset: commandVM<ResetLiveCommand>(true, true, null, false, {
            epoch: state.epoch,
            ...reset.command,
            battleLabel: text(own(battle, 'label')) ?? null,
          }),
        }
      : {
          target: null,
          reset: commandVM<ResetLiveCommand>(
            true,
            false,
            reset.enabled ? 'context_unknown' : reset.reason,

            request === 'sending',
            null,
          ),
        };
  };
  return {
    ...withDetails,
    // R2: the bounds are known from the saved run or from the projection's descriptors.
    boundsKnown: boundsBody !== undefined || hasLiveBounds(actions),
    editor: {
      scope,
      groups: fieldsVM(
        groups,
        draft,
        review.invalid,
        review.changes,
        (field) => liveMark(battle, field),
        (routine) => liveFieldOrder(actions, routine),
        described,
      ),
      labels: controls.descriptions.size > 0 || unrecognised.length > 0 ? 'descriptor' : 'copy',
      unaddressable: controls.unaddressable.map((item) => ({
        id: item.id,
        instance: item.instance,
        label: fromServer('name', item.label),
        reason: 'control_path_unpublished',
      })),
      save:
        contextReady && gate.enabled
          ? commandVM<SaveLiveCommand>(true, true, null, false, {
              epoch: state.epoch,
              scope,
              ...gate.command,
              draft: submittedDraft(draft, gate.command.changes),
              battleLabel: text(own(battle, 'label')) ?? null,
            })
          : commandVM<SaveLiveCommand>(
              true,
              false,
              gate.enabled ? 'context_unknown' : gate.reason,
              request === 'sending',
              null,
            ),
      discard: Object.keys(draft).length > 0,
      changeCount: Object.keys(review.changes).length,
      invalidCount: Object.keys(review.invalid).length,
      resets: changed.map((field) => ({
        path: field.path,
        routine: field.routine,
        field: field.field,
        ...names(field.routine, field.field),
        ...(liveFieldNullable(actions, field.routine, field.field)
          ? resetOf([field.path])
          : {
              target: null,
              reset: commandVM<ResetLiveCommand>(true, false, 'not_nullable', false, null),
            }),
      })),
      resetAll:
        leaves.length < 2
          ? null
          : {
              items: leaves.map((leaf) =>
                leaf.kind === 'routine' ? { ...leaf, ...names(leaf.routine, leaf.field) } : leaf,
              ),
              ...resetOf(leaves.map((leaf) => leaf.path)),
            },
    },
  };
}

export function selectRunsPage(state: DashboardState): RunsPageVM {
  const lines = selectOutcomes(state).lines.filter((line) => line.slot === 'runs');
  return {
    saved: savedSection(state, lines),
    live: liveSection(state, lines),
    epoch: state.epoch,
  };
}
