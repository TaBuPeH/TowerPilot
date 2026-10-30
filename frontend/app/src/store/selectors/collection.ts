import {
  choreNames,
  choreRows,
  choresValue,
  collectionSource,
  copiedPolicy,
  policyIdCheck,
  policyModules,
  REWARDS_MODULE,
  type CollectionSource,
} from '../../core/collectionPolicies.ts';
import { settingsView } from '../../core/settingsFields.ts';
import { editTargetScope } from '../../core/editingTarget.ts';
import { gatherDefaults } from '../../core/runTemplates.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { Fenced } from '../commands.ts';
import {
  editorSource,
  editingSourceResource,
  editingTargetVM,
  selectEditingTarget,
} from './editingTarget.ts';
import { readAgeSec } from '../reads.ts';
import type { ContextView, DashboardState, ReadRecord } from '../types.ts';
import { commandVM, type CommandVM } from '../viewModels.ts';
import {
  saveOutcome,
  settingsMatchContext,
  settingsModuleVM,
  settingsSavePending,
  type SaveOutcomeVM,
  type SettingsModuleVM,
} from './settingsModule.ts';

/**
 * The Collection page (collection brief C1 to C6): the profile-wide rewards module, the
 * gathering policies with the runs that use each, the selected policy's three settings
 * modules (gathering, ad gems, orbit), its label, a copy, a delete of an unused policy,
 * and the between-run chores. Saved choices only; nothing here claims a reward, a quota
 * or a chore result.
 */

/** A profile patch as its view model offered it (C4 to C6). */
export interface ProfilePatchCommand extends Fenced {
  readonly context: ContextView;
  /** Collection-only target data; other users of the shared patch command keep their contract. */
  readonly profileName?: string;
  readonly targetGeneration?: number;
  /** The write target: one outcome line per policy action or for the chores. */
  readonly target: string;
  readonly path: readonly string[];
  readonly remove: boolean;
  readonly value: unknown;
  /** The profile revision the patch is made from (the source read's). */
  readonly revision: string;
  readonly draft: {
    readonly scope: string;
    readonly values: Readonly<Record<string, string | boolean>>;
  } | null;
}

export type ReadState = 'not_read' | 'unavailable' | 'settling' | 'ready' | 'other_context';

export interface PolicyRowVM {
  readonly id: string;
  readonly idText: ServerText<'id'> | null;
  readonly label: ServerText<'name'> | null;
  /** What names it in a list or a picker: its label, else its identifier. */
  readonly name: ServerText<'name'> | null;
  readonly usedBy: readonly ServerText<'id'>[];
  /** The run keys that use it (links to their saved settings in Runs). */
  readonly usedByKeys: readonly string[];
  readonly selected: boolean;
}

export interface ChoreVM {
  readonly name: string;
  readonly nameText: ServerText<'id'> | null;
  readonly enabled: boolean;
  readonly saved: boolean;
  readonly unknown: boolean;
  readonly changed: boolean;
}

export interface PolicyEditorVM {
  readonly id: string;
  readonly idText: ServerText<'id'> | null;
  readonly label: ServerText<'name'> | null;
  readonly usedByKeys: readonly string[];
  readonly gather: SettingsModuleVM | null;
  readonly ad: SettingsModuleVM | null;
  readonly orbit: SettingsModuleVM | null;
  readonly usedBy: readonly ServerText<'id'>[];
  readonly labelScope: string | null;
  readonly labelDraft: string;
  /** The name was edited (Cancel restores the saved name). */
  readonly labelDirty: boolean;
  readonly pin: Readonly<Record<string, string>>;
  readonly saveLabel: CommandVM<ProfilePatchCommand>;
  readonly labelOutcome: SaveOutcomeVM | null;
  readonly remove: CommandVM<ProfilePatchCommand>;
  readonly removeOutcome: SaveOutcomeVM | null;
}

export interface CopyFormVM {
  readonly scope: string | null;
  readonly from: string;
  readonly id: string;
  readonly label: string;
  /**
   * F6C-R1-03: the source to record with the first edit of a draft that has none yet, so
   * later navigation to another policy never changes what the draft copies.
   */
  readonly pin: Readonly<Record<string, string>>;
  /** Something was typed or chosen (Cancel clears the form). */
  readonly dirty: boolean;
  readonly idReason: string | null;
  readonly save: CommandVM<ProfilePatchCommand>;
  readonly outcome: SaveOutcomeVM | null;
}

export interface CollectionPageVM {
  readonly epoch: number;
  readonly context: ContextView | null;
  readonly profile: ServerText<'name'> | null;
  readonly inactive: boolean;
  readonly settingsRead: ReadState;
  readonly sourceRead: ReadState;
  readonly settingsAgeSec: number | null;
  readonly pending: boolean;
  readonly rewards: SettingsModuleVM | null;
  readonly policies: readonly PolicyRowVM[];
  readonly editor: PolicyEditorVM | null;
  readonly deletedOutcomes: readonly {
    readonly id: ServerText<'id'> | null;
    readonly outcome: SaveOutcomeVM;
  }[];
  readonly copy: CopyFormVM;
  readonly create: {
    readonly choices: readonly { readonly key: string; readonly label: ServerText<'name'> }[];
    readonly scope: string | null;
    readonly template: string;
    readonly id: string;
    readonly pin: Readonly<Record<string, string>>;
    readonly dirty: boolean;
    readonly idReason: string | null;
    readonly save: CommandVM<ProfilePatchCommand>;
    readonly outcome: SaveOutcomeVM | null;
  };
  readonly missing: readonly {
    readonly run: ServerText<'id'> | null;
    readonly policy: ServerText<'id'> | null;
  }[];
  readonly problems: readonly ServerText<'message'>[];
  readonly validatorError: ServerText<'message'> | null;
  readonly chores: {
    readonly pin: Readonly<Record<string, string>>;
    readonly scope: string | null;
    readonly rows: readonly ChoreVM[];
    readonly read: ReadState;
    readonly save: CommandVM<ProfilePatchCommand>;
    readonly outcome: SaveOutcomeVM | null;
  };
  /** A command not sent at the click, and why (the click-time context check). */
  readonly notSent: string | null;
}

const NOT_SENT_REASONS: readonly string[] = [
  'context_unconfirmed',
  'context_changed',
  'collection_write_in_flight',
  'target_changed',
  'target_missing',
  'target_unconfirmed',
];

function readState(slot: {
  readonly latest: ReadRecord | null;
  readonly lastAnswered: ReadRecord | null;
}): ReadState {
  if (slot.lastAnswered !== null && slot.lastAnswered.outcome.kind === 'answered') {
    return slot.lastAnswered.outcome.status === 200 ? 'ready' : 'unavailable';
  }
  if (slot.latest === null) return 'not_read';
  return slot.latest.outcome.kind === 'failed' ? 'unavailable' : 'not_read';
}

function answered200(slot: { readonly lastAnswered: ReadRecord | null }): unknown {
  const read = slot.lastAnswered;
  return read !== null && read.outcome.kind === 'answered' && read.outcome.status === 200
    ? read.outcome.body
    : undefined;
}

/** The draft scope of a Collection form in a context. */
export function collectionScope(
  context: ContextView | null,
  form: string,
  name: string | null = context?.profile ?? null,
): string | null {
  return context === null || name === null
    ? null
    : `collection:${form}:${editTargetScope(context, name)}`;
}

function draftOf(
  state: DashboardState,
  scope: string | null,
): Readonly<Record<string, string | boolean>> {
  return scope === null ? {} : (state.runDrafts[scope] ?? {});
}

function textOf(draft: Readonly<Record<string, string | boolean>>, key: string, fallback = '') {
  const value = draft[key];
  return typeof value === 'string' ? value : fallback;
}

function ids(values: readonly string[]): readonly ServerText<'id'>[] {
  return values.flatMap((value) => {
    const shown = fromServer('id', value);
    return shown === null ? [] : [shown];
  });
}

/** The patch target of a policy action or the chores (one outcome line each). */
export function patchTarget(
  context: ContextView,
  what: string,
  name: string = context.profile ?? '',
): string {
  return `patch:${what}:${editTargetScope(context, name)}`;
}

export function selectCollectionTargetName(state: DashboardState) {
  return editingTargetVM(selectEditingTarget(state)).name;
}

/** The same save fence governs Collection's offered actions and its final send check. */
export function collectionSavePending(state: DashboardState): boolean {
  return settingsSavePending(
    state,
    selectEditingTarget(state).inactive ? ['targetSource'] : ['settings', 'profileSource'],
  );
}

export function selectCollectionPage(
  state: DashboardState,
  selected: string | null,
): CollectionPageVM {
  const context = state.context;
  const target = selectEditingTarget(state);
  const settingsBody = answered200(state.reads.settings);
  const view = settingsView(settingsBody);
  const sourceRecord = editorSource(state);
  const sourceBody =
    sourceRecord?.outcome.kind === 'answered' ? sourceRecord.outcome.body : undefined;
  const read = collectionSource(sourceBody);
  // Target-source reads never substitute the active profile's values while loading.
  const source: CollectionSource | null =
    read !== null && context !== null && read.profile === target.name ? read : null;
  const pending = collectionSavePending(state);
  const sourceState = readState(state.reads[editingSourceResource(target)]);
  const sourceRead = source !== null ? 'ready' : sourceState === 'ready' ? 'not_read' : sourceState;
  const settingsRead: ReadState =
    view === null
      ? readState(state.reads.settings)
      : settingsMatchContext(view, context)
        ? 'ready'
        : 'other_context';
  const rewards = settingsModuleVM(state, view, REWARDS_MODULE, { pending });
  // Profile patches are offered only where the server lets this profile be edited.
  const profileEditable = target.inactive
    ? target.blocked === null && source !== null && source.validatorError === null
    : rewards !== null && rewards.readOnly === null;
  const scopeOf = (form: string) => collectionScope(context, form, target.name);
  // An inactive draft's starting revision survives polls, target switches and reloads.
  const pinOf = (draft: Readonly<Record<string, string | boolean>>) =>
    target.inactive && draft['base_revision'] === undefined && source?.revision != null
      ? { base_revision: source.revision }
      : {};
  const editableReason = (): string | null =>
    context === null || !state.contextConfirmed
      ? 'context_unknown'
      : target.blocked !== null
        ? target.blocked
        : source?.revision == null
          ? 'source_not_read'
          : !profileEditable
            ? 'read_only'
            : pending
              ? 'save_pending'
              : null;
  const command = (
    what: string,
    path: readonly string[],
    remove: boolean,
    value: unknown,
    draft: ProfilePatchCommand['draft'],
  ): ProfilePatchCommand | null =>
    context === null || source?.revision == null || target.name === null
      ? null
      : {
          epoch: state.epoch,
          context,
          profileName: target.name,
          targetGeneration: state.editTargetGeneration,
          target: patchTarget(context, what, target.name),
          path,
          remove,
          value,
          revision:
            typeof draft?.values['base_revision'] === 'string'
              ? draft.values['base_revision']
              : source.revision,
          draft,
        };
  const offered = (reason: string | null, next: ProfilePatchCommand | null) =>
    reason === null && next !== null
      ? commandVM(true, true, null, false, next)
      : commandVM<ProfilePatchCommand>(true, false, reason ?? 'context_unknown', pending, null);
  const outcomeOf = (what: string) =>
    context === null || target.name === null
      ? null
      : saveOutcome(state, patchTarget(context, what, target.name));

  const policies = source?.policies ?? [];
  const chosen = policies.find((policy) => policy.id === selected) ?? policies[0] ?? null;
  const rows = policies.map((policy) => ({
    id: policy.id,
    idText: fromServer('id', policy.id),
    label: fromServer('name', policy.label),
    name: fromServer('name', policy.label ?? policy.id),
    usedBy: ids(policy.usedBy),
    usedByKeys: policy.usedBy,
    selected: policy.id === chosen?.id,
  }));

  let editor: PolicyEditorVM | null = null;
  if (chosen !== null) {
    const modules = policyModules(chosen.id);
    const labelScope = scopeOf(`label:${chosen.id}`);
    const labelDraftValues = draftOf(state, labelScope);
    const labelDraft = textOf(labelDraftValues, 'label', chosen.label ?? '');
    const labelChanged = labelDraft.trim() !== (chosen.label ?? '');
    const labelReason =
      editableReason() ??
      (labelDraft.trim() === '' ? 'enter_label' : !labelChanged ? 'no_changes' : null);
    const removeReason = editableReason() ?? (chosen.usedBy.length > 0 ? 'policy_in_use' : null);
    editor = {
      id: chosen.id,
      idText: fromServer('id', chosen.id),
      label: fromServer('name', chosen.label),
      usedByKeys: chosen.usedBy,
      gather: settingsModuleVM(state, view, modules.gather, { pending }),
      ad: settingsModuleVM(state, view, modules.ad, { pending }),
      orbit: settingsModuleVM(state, view, modules.orbit, { pending }),
      usedBy: ids(chosen.usedBy),
      labelScope,
      labelDraft,
      labelDirty: Object.keys(labelDraftValues).length > 0,
      pin: pinOf(labelDraftValues),
      saveLabel: offered(
        labelReason,
        labelReason === null && labelScope !== null
          ? command(
              `label:${chosen.id}`,
              ['policies', 'gather', chosen.id, 'label'],
              false,
              labelDraft.trim(),
              { scope: labelScope, values: { ...labelDraftValues, label: labelDraft } },
            )
          : null,
      ),
      labelOutcome: outcomeOf(`label:${chosen.id}`),
      remove: offered(
        removeReason,
        removeReason === null
          ? command(`delete:${chosen.id}`, ['policies', 'gather', chosen.id], true, undefined, null)
          : null,
      ),
      removeOutcome: outcomeOf(`delete:${chosen.id}`),
    };
  }

  const copyScope = scopeOf('copy');
  const copyDraft = draftOf(state, copyScope);
  const copyFrom = textOf(copyDraft, 'from', chosen?.id ?? '');
  const copySource = policies.find((policy) => policy.id === copyFrom) ?? null;
  const copyId = textOf(copyDraft, 'id');
  const copyLabel = textOf(copyDraft, 'label');
  const idReason =
    source === null
      ? null
      : policyIdCheck(copyId, source, target.inactive ? undefined : settingsBody);
  const copyReason =
    editableReason() ??
    (policies.length === 0
      ? 'no_policy_to_copy'
      : copySource === null
        ? 'choose_source'
        : idReason);
  const copy: CopyFormVM = {
    scope: copyScope,
    from: copyFrom,
    id: copyId,
    label: copyLabel,
    pin: {
      ...pinOf(copyDraft),
      ...(copyDraft['from'] === undefined && copyFrom !== '' ? { from: copyFrom } : {}),
    },
    dirty: Object.keys(copyDraft).length > 0,
    idReason,
    save: offered(
      copyReason,
      copyReason === null && copySource !== null && copyScope !== null
        ? command(
            'copy',
            ['policies', 'gather', copyId.trim()],
            false,
            copiedPolicy(copySource, copyLabel),
            { scope: copyScope, values: copyDraft },
          )
        : null,
    ),
    outcome: outcomeOf('copy'),
  };

  const defaults = gatherDefaults(answered200(state.reads.runTemplates));
  const choices = defaults.flatMap((entry) => {
    const label = fromServer('name', `${entry.templateLabel} / ${entry.ref}`);
    return label === null ? [] : [{ key: JSON.stringify([entry.templateId, entry.ref]), label }];
  });
  const createScope = scopeOf('create');
  const createDraft = draftOf(state, createScope);
  const template = textOf(createDraft, 'template', choices[0]?.key ?? '');
  const templateDefault = defaults.find(
    (entry) => JSON.stringify([entry.templateId, entry.ref]) === template,
  );
  const createId = textOf(createDraft, 'id');
  const createIdReason =
    source === null
      ? null
      : policyIdCheck(createId, source, target.inactive ? undefined : settingsBody);
  const createReason =
    editableReason() ?? (templateDefault === undefined ? 'template_not_read' : createIdReason);
  const create: CollectionPageVM['create'] = {
    choices,
    scope: createScope,
    template,
    id: createId,
    pin: {
      ...pinOf(createDraft),
      ...(createDraft['template'] === undefined && template !== '' ? { template } : {}),
    },
    dirty: Object.keys(createDraft).length > 0,
    idReason: createIdReason,
    save: offered(
      createReason,
      createReason === null && templateDefault !== undefined && createScope !== null
        ? command('create', ['policies', 'gather', createId.trim()], false, templateDefault.value, {
            scope: createScope,
            values: createDraft,
          })
        : null,
    ),
    outcome: outcomeOf('create'),
  };

  const choreScope = scopeOf('chores');
  const choreDraft = draftOf(state, choreScope);
  const names = choreNames(answered200(state.reads.vocab));
  const choreList = choreRows(source, names).map((row): ChoreVM => {
    const drafted = choreDraft[row.name];
    const enabled = typeof drafted === 'boolean' && !row.unknown ? drafted : row.enabled;
    return {
      name: row.name,
      nameText: fromServer('id', row.name),
      enabled,
      saved: row.enabled,
      unknown: row.unknown,
      changed: enabled !== row.enabled,
    };
  });
  const switches = Object.fromEntries(
    choreList.filter((row) => row.changed).map((row) => [row.name, row.enabled]),
  );
  const choresReason =
    editableReason() ??
    (names === null ? 'vocab_not_read' : Object.keys(switches).length === 0 ? 'no_changes' : null);
  const notice = state.notices.find((entry) => entry.kind === 'command_not_sent');
  return {
    epoch: state.epoch,
    context,
    profile: fromServer('name', context?.profile),
    inactive: target.inactive,
    settingsRead,
    sourceRead,
    settingsAgeSec:
      state.reads.settings.lastAnswered === null
        ? null
        : readAgeSec(state, state.reads.settings.lastAnswered),
    pending,
    rewards,
    policies: rows,
    editor,
    deletedOutcomes: [
      ...new Set(
        state.intents.flatMap((intent) => {
          const path = intent.meta.patchPath;
          const id = path?.[2];
          return intent.kind === 'profile_patch' &&
            intent.meta.patchRemove === true &&
            intent.meta.name === target.name &&
            path?.[0] === 'policies' &&
            path[1] === 'gather' &&
            id !== undefined &&
            !policies.some((policy) => policy.id === id)
            ? [id]
            : [];
        }),
      ),
    ].flatMap((id) => {
      const outcome = outcomeOf(`delete:${id}`);
      return outcome === null ? [] : [{ id: fromServer('id', id), outcome }];
    }),
    copy,
    create,
    missing: (source?.missing ?? []).map((entry) => ({
      run: fromServer('id', entry.run),
      policy: fromServer('id', entry.policy),
    })),
    problems: (source?.problems ?? []).flatMap((problem) => {
      const shown = fromServer('message', problem);
      return shown === null ? [] : [shown];
    }),
    validatorError: fromServer('message', source?.validatorError),
    chores: {
      pin: pinOf(choreDraft),
      scope: choreScope,
      rows: choreList,
      read: names === null ? readState(state.reads.vocab) : sourceRead,
      save: offered(
        choresReason,
        choresReason === null && choreScope !== null
          ? command('chores', ['policies', 'chores'], false, choresValue(source, switches), {
              scope: choreScope,
              values: Object.fromEntries(Object.entries(choreDraft)),
            })
          : null,
      ),
      outcome: outcomeOf('chores'),
    },
    notSent:
      notice !== undefined && NOT_SENT_REASONS.includes(notice.reason) ? notice.reason : null,
  };
}
