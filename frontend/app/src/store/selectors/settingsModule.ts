import {
  savedFieldProblem,
  checkField,
  fieldParts,
  reviewModule,
  savedPart,
  stringListRows,
  stringListSource,
  nextStringListKey,
  settingsModule,
  settingsSaveAnswer,
  settingsView,
  settingsDraftRevision,
  SETTINGS_BASE_REVISION,
  type FieldReason,
  type SettingsContext,
  type SettingsDraft,
  type SettingsField,
  type SettingsFieldType,
  type SettingsView,
} from '../../core/settingsFields.ts';
import { own } from '../../core/read.ts';
import { profilePatchAnswer } from '../../core/profilePatch.ts';
import type { StateWord } from '../../core/vocabulary.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import { settledByRead } from '../fences.ts';
import { boundIntents, contextScopeKey } from '../intents.ts';
import type { ContextView, DashboardState, IntentView } from '../types.ts';
import { commandVM, type CommandVM } from '../viewModels.ts';
import type { Fenced } from '../commands.ts';

/**
 * The shared editor of one typed settings module (`GET /api/settings`; collection brief
 * C1 to C4, abilities brief A1): the fields as the server describes them, the person's raw
 * draft beside the saved values, each field's reason when the draft is not valid, and the
 * one Save the module offers. Pages pass the settings read and the module id; the
 * component (features/settings/SettingsModuleForm.tsx) draws this view model.
 */

/** A settings module save as its view model offered it. */
export interface SettingsSaveCommand extends Fenced {
  readonly context: ContextView;
  /** The context the settings read named: sent back, the server refuses another. */
  readonly settingsContext: SettingsContext;
  readonly module: string;
  readonly revision: string;
  readonly values: Readonly<Record<string, unknown>>;
  readonly draft: { readonly scope: string; readonly values: SettingsDraft };
}

export interface SettingsPartVM {
  /** The draft key of this part (`<path>`, `<path>#0`, `<path>#1`). */
  readonly key: string;
  readonly raw: string | boolean;
  /** The person changed it (the draft holds it). */
  readonly edited: boolean;
}

export interface SettingsFieldVM {
  readonly listSource: { readonly key: string; readonly raw: string };
  readonly listRows: readonly {
    readonly key: string;
    readonly raw: string;
    readonly name: ServerText<'name'> | null;
    readonly malformed: boolean;
  }[];
  readonly nextListKey: string;
  readonly path: string;
  readonly type: SettingsFieldType | null;
  readonly label: ServerText<'name'> | null;
  readonly description: ServerText<'message'> | null;
  readonly unit: ServerText<'id'> | null;
  readonly min: number | null;
  readonly max: number | null;
  readonly whole: boolean;
  readonly options: readonly {
    readonly value: string;
    readonly label: ServerText<'name'> | null;
  }[];
  readonly parts: readonly SettingsPartVM[];
  /**
   * An enum's raw value (draft or saved) that is not one of its options (F6M-10): drawn as
   * its own option, so the control shows the raw value instead of silently another one.
   */
  readonly unlisted: ServerText<'name'> | null;
  /** Keep an empty unlisted value selected instead of the browser's first option. */
  readonly unlistedEmpty: boolean;
  readonly invalid: { readonly reason: FieldReason; readonly part: 0 | 1 | null } | null;
  /** The saved value differs from what the draft would send. */
  readonly changed: boolean;
}

/** Why a module cannot be edited here (the server marks it read-only). */
export type ReadOnlyReason = 'starter' | 'draft' | 'no_profile' | 'server';

export type SaveOutcomeCode =
  | 'sending'
  | 'saved'
  | 'refused'
  | 'context_changed'
  | 'unknown'
  | 'applied'
  | 'not_applied'
  | 'conflict_applied';

/** The newest save of one target: what it proved, the server's reason, and Dismiss. */
export interface SaveOutcomeVM {
  readonly id: string;
  readonly code: SaveOutcomeCode;
  readonly word: StateWord | null;
  readonly error: ServerText<'message'> | null;
  readonly currentContext: ServerText<'detail'> | null;
  readonly problems: readonly ServerText<'message'>[];
  readonly answeredAgeSec: number | null;
  readonly restored: boolean;
  readonly dismissOffered: boolean;
  readonly acceptOffered: boolean;
  readonly accepted: boolean;
}

export interface SettingsModuleVM {
  readonly id: string;
  readonly scope: string | null;
  readonly title: ServerText<'name'> | null;
  readonly description: ServerText<'message'> | null;
  readonly applyNotice: ServerText<'message'> | null;
  readonly readOnly: ReadOnlyReason | null;
  readonly fields: readonly SettingsFieldVM[];
  readonly dirty: boolean;
  readonly reconcile: { readonly key: string; readonly revision: string } | null;
  readonly save: CommandVM<SettingsSaveCommand>;
  readonly outcome: SaveOutcomeVM | null;
}

/**
 * One field of a settings module as the shared field component draws it: the server's
 * label, unit and bounds, each raw part (the draft's text, else the saved value), and its
 * reason when the draft is not valid. Pure: any page store can call it with its own draft.
 */
export function settingsFieldVM(field: SettingsField, draft: SettingsDraft): SettingsFieldVM {
  const keys = fieldParts(field);
  const parts = keys.map((key, index): SettingsPartVM => {
    const part = keys.length === 1 ? null : index === 0 ? 0 : 1;
    return { key, raw: draft[key] ?? savedPart(field, part), edited: draft[key] !== undefined };
  });
  const checked = checkField(field, draft);
  const edited = parts.some((part) => part.edited);
  const shown = parts[0]?.raw;
  const missingOption =
    field.type === 'enum' &&
    typeof shown === 'string' &&
    !field.options.some((option) => option.value === shown);
  const unlisted = missingOption && shown !== '' ? fromServer('name', shown) : null;
  const problem =
    checked.kind === 'invalid'
      ? { reason: checked.reason, part: checked.part }
      : edited
        ? null
        : savedFieldProblem(field);
  return {
    listSource: stringListSource(field, draft),
    listRows:
      field.type === 'string-list'
        ? stringListRows(field, draft).map((row) => ({
            key: row.key,
            raw:
              typeof row.raw === 'string'
                ? row.raw
                : row.raw === undefined
                  ? ''
                  : JSON.stringify(row.raw),
            name: fromServer(
              'name',
              typeof row.raw === 'string' ? row.raw : JSON.stringify(row.raw),
            ),
            malformed: typeof row.raw !== 'string',
          }))
        : [],
    nextListKey: nextStringListKey(field, draft),
    path: field.path,
    type: field.type,
    label: fromServer('name', field.label),
    description: fromServer('message', field.description),
    unit: fromServer('id', field.unit),
    min: field.min,
    max: field.max,
    whole: field.type === 'integer' || field.wholeNumbers,
    options: field.options.map((option) => ({
      value: option.value,
      label: fromServer('name', option.label),
    })),
    parts,
    unlisted,
    unlistedEmpty: missingOption && shown === '',
    invalid: problem,
    changed: checked.kind === 'valid',
  };
}

/** The draft scope of one module in one context: a draft never moves to another account. */
export function settingsScope(context: ContextView | null, moduleId: string): string | null {
  return context === null ? null : `settings:${moduleId}:${contextScopeKey(context)}`;
}

function draftOf(state: DashboardState, scope: string | null): SettingsDraft {
  return scope === null ? {} : (state.runDrafts[scope] ?? {});
}

function readOnlyReason(context: SettingsContext | null): ReadOnlyReason {
  const profile = context?.profile ?? null;
  if (profile === null) return 'no_profile';
  if (profile === 'default') return 'starter';
  if (profile.endsWith('.draft')) return 'draft';
  return 'server';
}

/** The settings read names the page's emulator, account and profile. */
export function settingsMatchContext(view: SettingsView | null, context: ContextView | null) {
  const named = view?.context ?? null;
  return (
    named !== null &&
    context !== null &&
    named.instance === context.instance &&
    named.account === context.account &&
    named.profile === context.profile
  );
}

/** Only the latest successful read in this epoch may authorize editing or saving. */
export function currentSettingsView(state: DashboardState): SettingsView | null {
  const read = state.reads.settings.latest;
  if (
    read === null ||
    read.epoch !== state.epoch ||
    read.outcome.kind !== 'answered' ||
    read.outcome.status !== 200
  )
    return null;
  const view = settingsView(read.outcome.body);
  return view?.revision !== null && settingsMatchContext(view, state.context) ? view : null;
}

/**
 * True while a save of the settings or the profile is in flight, or answered (or its
 * answer lost) but not read back yet: both revisions move with either save, so another
 * save waits for a settings read and a profile read started after the newest answer.
 * An Unknown settings save or profile patch stays fenced until positive readback or
 * explicit acceptance where its editor permits it; acceptance retains its Unknown record
 * and never resends. Profile patches require their own named source at an advanced
 * revision holding the sent value (settledByRead). Pages without a profile-source read
 * (Runtime and Emulators' preview settings) wait only for settings after known answers
 * (review F6M-04), but an Unknown profile patch still needs its editor's source readback
 * or an explicit reconciliation permitted there.
 */
export function settingsSavePending(
  state: DashboardState,
  reads: readonly ('settings' | 'profileSource' | 'targetSource')[] = ['settings', 'profileSource'],
): boolean {
  const records = boundIntents(state).filter(
    (record) => record.kind === 'settings_save' || record.kind === 'profile_patch',
  );
  if (records.some((record) => record.phase === 'sending')) return true;
  // F6T-R1-01: an unchanged read never settles a lost save. Only positive readback or
  // explicit acceptance opens this fence; the journal retains its Unknown record.
  if (records.some((record) => unresolvedSettingsSave(state, record))) return true;
  const newest = Math.max(
    0,
    ...records.map((record) => Math.max(record.sentSeq, record.answerSeq ?? record.lostSeq ?? 0)),
  );
  if (newest === 0) return false;
  return reads.some((resource) => {
    const read = state.reads[resource].lastAnswered;
    return read === null || read.startedSeq <= newest;
  });
}

function unresolvedSettingsSave(state: DashboardState, record: IntentView): boolean {
  return (
    (record.kind === 'settings_save' || record.kind === 'profile_patch') &&
    record.phase === 'unconfirmed' &&
    record.accepted !== true &&
    settledByRead(state, record) === null
  );
}

function sameTarget(record: IntentView, target: string): boolean {
  return record.target === target;
}

/** Keep an unresolved save visible before the newest target outcome, including legacy journals. */
export function saveOutcome(state: DashboardState, target: string): SaveOutcomeVM | null {
  const records = boundIntents(state).filter((entry) => sameTarget(entry, target));
  const unresolved = records.findLast((entry) => unresolvedSettingsSave(state, entry));
  const record = unresolved ?? records.at(-1);
  if (record === undefined || (record.dismissed && unresolved === undefined)) return null;
  const answer =
    record.answer === null
      ? null
      : record.kind === 'settings_save'
        ? settingsSaveAnswer(record.answer, record.meta)
        : profilePatchAnswer(record.answer);
  const settled = settledByRead(state, record);
  const code: SaveOutcomeCode =
    record.phase === 'sending'
      ? 'sending'
      : record.phase === 'refused' && settled === 'applied'
        ? 'conflict_applied'
        : settled === 'applied'
          ? 'applied'
          : settled === 'not_applied'
            ? 'not_applied'
            : answer?.kind === 'saved' || answer?.kind === 'done'
              ? 'saved'
              : answer?.kind === 'refused'
                ? answer.code === 'context_changed'
                  ? 'context_changed'
                  : 'refused'
                : 'unknown';
  const refusal = answer?.kind === 'refused' ? answer : null;
  const accepted = code === 'unknown' && record.accepted === true;
  const read = state.reads.settings.latest;
  const point = Math.max(record.sentSeq, record.answerSeq ?? record.lostSeq ?? 0);
  const view =
    read?.outcome.kind === 'answered' && read.outcome.status === 200
      ? settingsView(read.outcome.body)
      : null;
  const acceptOffered =
    code === 'unknown' &&
    record.kind === 'settings_save' &&
    !accepted &&
    read !== null &&
    read.startedSeq > point &&
    view !== null &&
    view.revision !== null &&
    settingsMatchContext(view, record.context);
  return {
    id: record.id,
    code,
    word:
      code === 'sending'
        ? 'waiting'
        : code === 'unknown'
          ? 'unknown'
          : code === 'refused' || code === 'context_changed'
            ? 'blocked'
            : null,
    error: fromServer('message', refusal?.error),
    currentContext:
      code === 'context_changed'
        ? fromServer('detail', JSON.stringify(own(record.answer?.body, 'current_context'), null, 2))
        : null,
    problems: (answer?.kind === 'refused' && record.kind === 'profile_patch'
      ? profilePatchProblems(record.answer)
      : []
    ).flatMap((problem) => {
      const shown = fromServer('message', problem);
      return shown === null ? [] : [shown];
    }),
    answeredAgeSec:
      record.answeredAtMs === null
        ? null
        : Math.max(0, Math.floor((state.nowMs - record.answeredAtMs) / 1000)),
    restored: record.restored,
    // An unknown outcome is kept until a read settles it: its line cannot be hidden.
    dismissOffered: code !== 'sending' && (code !== 'unknown' || accepted),
    acceptOffered,
    accepted,
  };
}

function profilePatchProblems(
  answer: { readonly status: number; readonly body: unknown } | null,
): readonly string[] {
  if (answer === null) return [];
  const classified = profilePatchAnswer(answer);
  return classified.kind === 'refused' ? classified.problems : [];
}

/** The write target of a settings module save in a context (one record line per module). */
export function settingsTarget(context: ContextView, moduleId: string): string {
  return `settings:${moduleId}:${contextScopeKey(context)}`;
}

export function settingsModuleVM(
  state: DashboardState,
  view: SettingsView | null,
  moduleId: string,
  options: { readonly pending: boolean },
): SettingsModuleVM | null {
  const context = state.context;
  const found = settingsModule(view, moduleId);
  if (found === null) return null;
  // F6C-R1-05: values read for another emulator, account or profile are never shown or
  // edited here; this context's outcome line and its kept draft stay (no Cancel offered).
  if (!settingsMatchContext(view, context))
    return {
      id: moduleId,
      scope: null,
      title: fromServer('name', found.title),
      description: fromServer('message', found.description),
      applyNotice: null,
      readOnly: null,
      fields: [],
      dirty: false,
      reconcile: null,
      save: commandVM<SettingsSaveCommand>(true, false, 'other_context', options.pending, null),
      outcome: context === null ? null : saveOutcome(state, settingsTarget(context, moduleId)),
    };
  const scope = found.editable ? settingsScope(context, moduleId) : null;
  const draft = draftOf(state, scope);
  const review = reviewModule(found, draft);
  const fields = found.fields.map((field) => settingsFieldVM(field, draft));
  const changedPaths = Object.keys(review.values);
  const invalid = Object.keys(review.invalid).length > 0;
  const revision = view?.revision ?? null;
  const settingsContext = view?.context ?? null;
  const dirty = Object.keys(draft).some((key) => key !== SETTINGS_BASE_REVISION);
  const baseRevision = settingsDraftRevision(draft);
  const current = currentSettingsView(state);
  const readable = current !== null && current.revision === revision;
  const pending = options.pending || settingsSavePending(state, ['settings']);
  const revisionChanged = dirty && (baseRevision === null || baseRevision !== revision);
  const reason = !found.editable
    ? 'read_only'
    : context === null || settingsContext === null || revision === null || !readable
      ? 'context_unknown'
      : pending
        ? 'save_pending'
        : revisionChanged
          ? 'revision_changed'
          : invalid
            ? 'invalid_value'
            : changedPaths.length === 0
              ? 'no_changes'
              : null;
  const sent = new Set(
    found.fields
      .filter((field) => review.values[field.path] !== undefined)
      .flatMap((field) => fieldParts(field, draft)),
  );
  const command: SettingsSaveCommand | null =
    reason === null && context !== null && settingsContext !== null && baseRevision !== null
      ? {
          epoch: state.epoch,
          context,
          settingsContext,
          module: moduleId,
          revision: baseRevision,
          values: review.values,
          draft: {
            scope: scope ?? '',
            values: Object.fromEntries(Object.entries(draft).filter(([key]) => sent.has(key))),
          },
        }
      : null;
  return {
    id: moduleId,
    scope,
    title: fromServer('name', found.title),
    description: fromServer('message', found.description),
    applyNotice: fromServer('message', found.applyNotice),
    readOnly: found.editable ? null : readOnlyReason(view?.context ?? null),
    fields,
    dirty,
    reconcile:
      readable && revisionChanged && !pending && revision !== null
        ? { key: SETTINGS_BASE_REVISION, revision }
        : null,
    save:
      command === null
        ? commandVM<SettingsSaveCommand>(true, false, reason, options.pending, null)
        : commandVM(true, true, null, false, command),
    outcome: context === null ? null : saveOutcome(state, settingsTarget(context, moduleId)),
  };
}
