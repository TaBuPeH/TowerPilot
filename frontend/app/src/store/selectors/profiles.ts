import type { ProfileWrite } from '../../api/intents/profiles.ts';
import {
  accountsView,
  activationCheck,
  checkTier,
  destinationCheck,
  profileFilesView,
  profileSourceView,
  profileWriteAnswer,
  profileWriteKind,
  unbindUnresolved,
  setupState,
  STARTER_PROFILE,
  type ActivationCheck,
  type ProfileWriteKind,
  type SetupState,
  type TierCheck,
} from '../../core/profiles.ts';
import type { StateWord } from '../../core/vocabulary.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { ProfileWriteCommand } from '../commands.ts';
import { sameEmulator, settledByRead } from '../fences.ts';
import { contextScopeKey, unresolvedIntent } from '../intents.ts';
import { answeredBody, readAgeSec } from '../reads.ts';
import { own } from '../../core/read.ts';
import type { ContextView, DashboardState, IntentView, ReadRecord } from '../types.ts';
import { commandVM, type CommandVM } from '../viewModels.ts';

/**
 * The Profiles page (backend/logs/reviews/ui-pages/profiles-brief.md, P1 to
 * P5): the emulator's local account and its automation profile, each write's
 * outcome, and the forms as drafts kept under their own target (README rule 4:
 * a draft never moves to another account). Decisions are core's
 * (core/profiles.ts); this file joins reads, drafts and write records.
 */

/** Only the account picker spans accounts; all source-dependent drafts keep their full context. */
export function profilesScope(
  state: DashboardState,
  form: 'account' | 'create' | 'tier' | 'activate' | 'copy' | 'promote',
): string | null {
  const c = state.context;
  if (c === null) return null;
  return form === 'account'
    ? `profiles:${form}:${JSON.stringify([c.instance, c.serial])}`
    : `profiles:${form}:${contextScopeKey(c)}`;
}

export type ProfileOutcomeCode =
  'sending' | 'accepted' | 'done' | 'refused' | 'unknown' | 'applied';

export interface ProfileOutcomeVM {
  readonly id: string;
  readonly kind: ProfileWriteKind;
  readonly code: ProfileOutcomeCode;
  readonly word: StateWord | null;
  /** The account id, profile name or destination the write named. */
  readonly target: ServerText<'name'> | null;
  readonly maxTier: number | null;
  /** The server's `error` (and `code`) of a refusal, as sent. */
  readonly error: ServerText<'message'> | null;
  readonly refusalCode: ServerText<'id'> | null;
  readonly currentContext: Readonly<
    Record<'instance' | 'account' | 'profile' | 'serial', ServerText<'name'> | null>
  > | null;
  /** The validation problems the answer listed (a promotion saved with them, a refused activation). */
  readonly problems: readonly ServerText<'message'>[];
  readonly abilitiesVerified: boolean | null;
  readonly answeredAgeSec: number | null;
  /** Restored from this tab's session storage after a reload. */
  readonly restored: boolean;
  /** F6-14 PE1: an activation that named no profile (use no profile). */
  readonly unbind: boolean;
  /** Dismiss hides the line; the record and what it fences stay. */
  readonly dismissOffered: boolean;
}

export interface AccountChoiceVM {
  readonly key: string;
  readonly id: ServerText<'id'> | null;
  readonly label: ServerText<'name'> | null;
  /** What the picker shows: the label, or the identifier when the account has none. */
  readonly option: ServerText<'name'> | null;
  readonly active: boolean;
}

export interface AccountSectionVM {
  readonly read: 'not_read' | 'unavailable' | 'settling' | 'ready';
  readonly readAgeSec: number | null;
  readonly instance: ServerText<'id'> | null;
  readonly active: AccountChoiceVM | null;
  readonly accounts: readonly AccountChoiceVM[];
  /** The picker's value: the person's pick (a draft of this emulator), else the active account. */
  readonly pickScope: string | null;
  readonly pick: string | null;
  readonly select: CommandVM<ProfileWriteCommand>;
  /**
   * What stays with the current account when another is chosen (P1): unsaved edits of
   * this account and profile, and requests without an answer. More than none: the switch
   * asks first.
   */
  readonly keeps: { readonly edits: number; readonly unresolved: number };
  readonly create: {
    readonly scope: string | null;
    readonly id: string;
    readonly label: string;
    readonly importCurrent: boolean;
    /** P2: an import is offered only when this installation has personal settings. */
    readonly importOffered: boolean;
    readonly command: CommandVM<ProfileWriteCommand>;
    readonly edited: boolean;
  };
  readonly outcome: ProfileOutcomeVM | null;
}

export interface TierSectionVM {
  readonly current: number | null;
  /** An effective default from accounts, only when the readable source has no saved tier. */
  readonly defaultValue: number | null;
  /** Who set it: `operator` (entered here) or another source, as the active profile says. */
  readonly setBy: 'operator' | 'other' | null;
  readonly setByText: ServerText<'id'> | null;
  readonly scope: string | null;
  readonly shown: string;
  readonly edited: boolean;
  readonly invalid: Extract<TierCheck, { kind: 'invalid' }>['reason'] | null;
  readonly save: CommandVM<ProfileWriteCommand>;
  readonly outcome: ProfileOutcomeVM | null;
}

export interface ProfileRowVM {
  readonly key: string;
  readonly name: ServerText<'name'> | null;
  readonly kind: 'starter' | 'draft' | 'own';
  readonly active: boolean;
  readonly note: ServerText<'message'> | null;
  readonly mtime: number | null;
}

export interface ValidationVM {
  readonly name: ServerText<'name'> | null;
  readonly problems: readonly ServerText<'message'>[];
  readonly warnings: readonly ServerText<'message'>[];
  readonly validatorError: ServerText<'message'> | null;
  readonly tierSetBy: string | null;
}

export interface ProfilesSectionVM {
  readonly read: 'not_read' | 'unavailable' | 'settling' | 'ready';
  readonly active: ServerText<'name'> | null;
  /** The active profile's validation, from its source read (null: not read or unreadable). */
  readonly activeValidation: ValidationVM | null;
  readonly activeSourceError: ServerText<'message'> | null;
  readonly rows: readonly ProfileRowVM[];
  readonly activate: {
    readonly scope: string | null;
    readonly pick: string | null;
    /** The picked profile's check: not yet read, reading, or what its source read says. */
    readonly check: 'none' | 'checking' | ActivationCheck['kind'];
    readonly blockedReason: Extract<ActivationCheck, { kind: 'blocked' }>['reason'] | null;
    readonly validation: ValidationVM | null;
    readonly checkError: ServerText<'message'> | null;
    readonly command: CommandVM<ProfileWriteCommand>;
    readonly outcome: ProfileOutcomeVM | null;
  };
  /**
   * F6-14 PE1: "Use no profile": unbind the active profile (the scheduler runs its legacy
   * rules). Enabled only while a profile is active; null until the profile list is read.
   */
  readonly unbind: {
    readonly account: ServerText<'name'> | null;
    readonly instance: ServerText<'name'> | null;
    readonly active: ServerText<'name'> | null;
    readonly command: CommandVM<ProfileWriteCommand>;
    readonly outcome: ProfileOutcomeVM | null;
  } | null;
  readonly copy: {
    readonly scope: string | null;
    readonly from: string;
    readonly name: string;
    /** The person ticked Replace for exactly this source and destination. */
    readonly overwrite: boolean;
    /** What a Replace tick records: consent for this source and destination only (F6P-05). */
    readonly overwriteKey: string;
    /** The destination is listed: replacing it needs an explicit tick. */
    readonly exists: boolean;
    readonly sources: readonly ProfileRowVM[];
    readonly command: CommandVM<ProfileWriteCommand>;
  };
  readonly promote: {
    readonly scope: string | null;
    readonly draft: string;
    readonly name: string;
    readonly base: string;
    readonly overwrite: boolean;
    readonly overwriteKey: string;
    readonly exists: boolean;
    readonly drafts: readonly ProfileRowVM[];
    readonly bases: readonly ProfileRowVM[];
    readonly command: CommandVM<ProfileWriteCommand>;
  };
  /** The newest copy or promotion of this emulator. */
  readonly filesOutcome: ProfileOutcomeVM | null;
}

export interface ProfilesPageVM {
  readonly epoch: number;
  readonly context: ContextView | null;
  readonly profile: ServerText<'name'> | null;
  readonly account: AccountSectionVM;
  readonly tier: TierSectionVM;
  readonly setup: SetupState;
  readonly profiles: ProfilesSectionVM;
  /** The newest click that sent nothing on this page, and why (a code). */
  readonly notSent: string | null;
}

const NOT_SENT_REASONS: readonly string[] = [
  'profile_write_in_flight',
  'unbind_unresolved',
  'context_unconfirmed',
  'context_changed',
  'profile_not_valid',
  'destination_active',
];

/** The records of Profiles writes made for this emulator, oldest first. */
function profileRecords(state: DashboardState): readonly IntentView[] {
  return state.intents.filter(
    (record) => profileWriteKind(record.kind) && sameEmulator(state, record),
  );
}

/** The sequence point of the newest answer among `records` (0: none). */
function newestAnswer(records: readonly IntentView[]): number {
  return records.reduce((max, record) => Math.max(max, record.answerSeq ?? 0), 0);
}

/**
 * A read as evidence after the page's own writes (P1: a late read of the account
 * before the switch never replaces it): its state, and its body only when it started
 * after the newest answer of those writes.
 */
function readAfter(
  read: ReadRecord | null,
  last: ReadRecord | null,
  after: number,
): { readonly state: 'not_read' | 'unavailable' | 'settling' | 'ready'; readonly body: unknown } {
  if (read === null) return { state: 'not_read', body: undefined };
  const answered = last?.outcome.kind === 'answered' && last.outcome.status === 200 ? last : null;
  if (answered === null || answered.outcome.kind !== 'answered')
    return { state: read.outcome.kind === 'failed' ? 'unavailable' : 'not_read', body: undefined };
  if (answered.startedSeq <= after) return { state: 'settling', body: undefined };
  return { state: 'ready', body: answered.outcome.body };
}

function outcomeVM(state: DashboardState, record: IntentView | undefined): ProfileOutcomeVM | null {
  if (record === undefined || record.dismissed || !profileWriteKind(record.kind)) return null;
  const answer =
    record.answer === null ? null : profileWriteAnswer(record.kind, record.answer, record.meta);
  const settled = settledByRead(state, record);
  const code: ProfileOutcomeCode =
    record.phase === 'sending'
      ? 'sending'
      : settled === 'applied'
        ? 'applied'
        : answer?.kind === 'accepted'
          ? 'accepted'
          : answer?.kind === 'done'
            ? 'done'
            : answer?.kind === 'refused'
              ? 'refused'
              : 'unknown';
  const word: StateWord | null =
    code === 'sending' || code === 'accepted'
      ? 'waiting'
      : code === 'unknown'
        ? 'unknown'
        : // A refusal is the server's own answer; a later read that does not show the change
          // is not a block on anything, so it carries no state word (its line says it).
          code === 'refused'
          ? 'blocked'
          : null;
  const problems = answer?.kind === 'done' || answer?.kind === 'refused' ? answer.problems : [];
  const currentContext = own(record.answer?.body, 'current_context');
  return {
    id: record.id,
    kind: record.kind,
    code,
    word,
    target: fromServer('name', record.meta.name),
    maxTier: record.meta.maxTier ?? null,
    error: answer?.kind === 'refused' ? fromServer('message', answer.error) : null,
    refusalCode: answer?.kind === 'refused' ? fromServer('id', answer.code) : null,
    currentContext:
      answer?.kind === 'refused' && answer.code === 'context_changed'
        ? {
            instance: fromServer('name', own(currentContext, 'instance')),
            account: fromServer('name', own(currentContext, 'account')),
            profile: fromServer('name', own(currentContext, 'profile')),
            serial: fromServer('name', own(currentContext, 'serial')),
          }
        : null,
    problems: problems.flatMap((problem) => {
      const shown = fromServer('message', problem);
      return shown === null ? [] : [shown];
    }),
    abilitiesVerified: answer?.kind === 'done' ? answer.abilitiesVerified : null,
    answeredAgeSec:
      record.answeredAtMs === null
        ? null
        : Math.max(0, Math.floor((state.nowMs - record.answeredAtMs) / 1000)),
    restored: record.restored,
    unbind: record.meta.unbind === true,
    dismissOffered: record.phase !== 'sending',
  };
}

function newestOf(
  state: DashboardState,
  records: readonly IntentView[],
  kinds: readonly ProfileWriteKind[],
  unbind = false,
): ProfileOutcomeVM | null {
  return outcomeVM(
    state,
    records
      .filter(
        (record) =>
          kinds.some((kind) => kind === record.kind) && (record.meta.unbind === true) === unbind,
      )
      .at(-1),
  );
}

function validationVM(body: unknown): ValidationVM | null {
  const view = profileSourceView(body);
  if (view === null) return null;
  const texts = (values: readonly string[]) =>
    values.flatMap((value) => {
      const shown = fromServer('message', value);
      return shown === null ? [] : [shown];
    });
  return {
    name: fromServer('name', view.name),
    problems: texts(view.problems),
    warnings: texts(view.warnings),
    validatorError: fromServer('message', view.validatorError),
    tierSetBy: view.tierSetBy,
  };
}

function draftOf(
  state: DashboardState,
  scope: string | null,
): Readonly<Record<string, string | boolean>> {
  return scope === null ? {} : (state.runDrafts[scope] ?? {});
}

function textOf(
  draft: Readonly<Record<string, string | boolean>>,
  key: string,
  fallback = '',
): string {
  const value = draft[key];
  return typeof value === 'string' ? value : fallback;
}

function flagOf(draft: Readonly<Record<string, string | boolean>>, key: string): boolean {
  return draft[key] === true;
}

/**
 * F6P-05: a Replace tick is consent for one reviewed write, recorded as the key of its
 * source and destination; changing either leaves the old consent unmatched (a new tick).
 */
function consentKey(...parts: readonly string[]): string {
  return JSON.stringify(parts);
}

/** Only the given keys of a draft: what a write was made from, cleared once it is confirmed. */
function submitted(
  draft: Readonly<Record<string, string | boolean>>,
  keys: readonly string[],
): Readonly<Record<string, string | boolean>> {
  return Object.fromEntries(
    keys.flatMap((key) => {
      const value = draft[key];
      return value === undefined ? [] : [[key, value] as const];
    }),
  );
}

function command(
  state: DashboardState,
  write: ProfileWrite,
  draft: ProfileWriteCommand['draft'],
): ProfileWriteCommand | null {
  return state.context === null
    ? null
    : { epoch: state.epoch, context: state.context, action: write, draft };
}

function rowVM(
  file: ReturnType<typeof profileFilesView> extends infer V
    ? V extends { readonly files: readonly (infer F)[] }
      ? F
      : never
    : never,
): ProfileRowVM {
  return {
    key: file.name,
    name: fromServer('name', file.name),
    kind: file.starter ? 'starter' : file.draft ? 'draft' : 'own',
    active: file.active,
    note: fromServer('message', file.note),
    mtime: file.mtime,
  };
}

export function selectProfilesPage(state: DashboardState): ProfilesPageVM {
  const records = profileRecords(state);
  const sending = records.some((record) => record.phase === 'sending');
  const after = newestAnswer(records);
  const accountsRead = readAfter(
    state.reads.accounts.latest,
    state.reads.accounts.lastAnswered,
    after,
  );
  const filesRead = readAfter(
    state.reads.profileFiles.latest,
    state.reads.profileFiles.lastAnswered,
    after,
  );
  const accounts = accountsView(accountsRead.body);
  const files = profileFilesView(filesRead.body);
  const sourceBody = answeredBody(state, 'profileSource');
  const sourceRead = state.reads.profileSource.latest;
  const activeSource =
    sourceRead?.outcome.kind === 'answered' && sourceRead.outcome.status === 200
      ? sourceBody
      : undefined;
  const context = state.context;

  // P1: the account.
  const choices = (accounts?.accounts ?? []).map((entry) => ({
    key: entry.id,
    id: fromServer('id', entry.id),
    label: fromServer('name', entry.label),
    option: fromServer('name', entry.label ?? entry.id),
    active: entry.id === accounts?.active,
  }));
  const pickScope = profilesScope(state, 'account');
  const pickDraft = draftOf(state, pickScope);
  const pick = textOf(pickDraft, 'id', accounts?.active ?? '') || null;
  const selectReason =
    accounts === null
      ? 'accounts_not_read'
      : pick === null
        ? 'choose_account'
        : pick === accounts.active
          ? 'already_active'
          : sending
            ? 'write_in_flight'
            : null;
  const selectWrite: ProfileWrite | null =
    pick === null ? null : { kind: 'account_select', id: pick };
  const selectCommand =
    selectReason === null && selectWrite !== null ? command(state, selectWrite, null) : null;
  const ctxKey = context === null ? null : contextScopeKey(context);
  const edits = Object.entries(state.runDrafts).filter(
    ([scope, draft]) =>
      ctxKey !== null &&
      Object.keys(draft).length > 0 &&
      (scope.startsWith(`saved:${ctxKey}:`) ||
        ['create', 'tier', 'copy', 'promote'].some(
          (form) => scope === `profiles:${form}:${ctxKey}`,
        )),
  ).length;
  const unresolved = state.intents.filter(
    (record) =>
      unresolvedIntent(record) &&
      record.context !== null &&
      context !== null &&
      contextScopeKey(record.context) === contextScopeKey(context) &&
      settledByRead(state, record) === null,
  ).length;

  const createScope = profilesScope(state, 'create');
  const createDraft = draftOf(state, createScope);
  const createId = textOf(createDraft, 'id');
  const createLabel = textOf(createDraft, 'label');
  const importOffered = accounts?.hasPersonalSettings === true;
  const importCurrent = importOffered && flagOf(createDraft, 'import_current');
  const createReason =
    createId.trim() === ''
      ? 'enter_identifier'
      : sending
        ? 'write_in_flight'
        : accounts === null
          ? 'accounts_not_read'
          : null;
  const createCommand =
    createReason === null && createScope !== null
      ? command(
          state,
          {
            kind: 'account_create',
            id: createId.trim(),
            label: createLabel.trim() === '' ? null : createLabel.trim(),
            importCurrent,
          },
          { scope: createScope, values: submitted(createDraft, ['id', 'label', 'import_current']) },
        )
      : null;

  // P3: the tier.
  const parsedSource = profileSourceView(activeSource);
  const source = parsedSource?.name === context?.profile ? parsedSource : null;
  const tierScope = profilesScope(state, 'tier');
  const tierDraft = draftOf(state, tierScope);
  const current = source?.maxTier ?? null;
  const defaultValue = source !== null && current === null ? (accounts?.maxTier ?? null) : null;
  const typed = tierDraft['max_tier'];
  const initial = current ?? defaultValue;
  const shown = typeof typed === 'string' ? typed : initial === null ? '' : String(initial);
  const tierCheck = checkTier(shown);
  const tierEdited =
    typeof typed === 'string' && !(tierCheck.kind === 'valid' && tierCheck.value === current);
  const profile = context?.profile ?? null;
  const tierReason =
    profile === null
      ? 'no_profile'
      : profile === STARTER_PROFILE
        ? 'starter'
        : tierCheck.kind === 'invalid'
          ? 'invalid_value'
          : !tierEdited
            ? 'no_changes'
            : sending
              ? 'write_in_flight'
              : null;
  const tierCommand =
    tierReason === null && tierCheck.kind === 'valid' && tierScope !== null
      ? command(
          state,
          { kind: 'account_tier', maxTier: tierCheck.value },
          { scope: tierScope, values: submitted(tierDraft, ['max_tier']) },
        )
      : null;

  // P5: the profiles.
  const rows = (files?.files ?? []).map(rowVM);
  const fileNamed = (name: string) => files?.files.find((file) => file.name === name) ?? null;
  const activateScope = profilesScope(state, 'activate');
  const activatePick = textOf(draftOf(state, activateScope), 'name') || null;
  const checkRecord = state.profileCheck;
  const pickedCheck =
    activatePick === null || checkRecord?.name !== activatePick ? null : checkRecord;
  const pickedFile = activatePick === null ? null : fileNamed(activatePick);
  const check: ActivationCheck | null =
    pickedCheck === null ? null : activationCheck(pickedFile, profileSourceView(pickedCheck.body));
  const activateReason =
    activatePick === null
      ? 'choose_profile'
      : activatePick === files?.active
        ? 'already_active'
        : pickedFile?.draft === true
          ? 'draft'
          : check === null
            ? 'not_checked'
            : check.kind === 'blocked'
              ? check.reason
              : sending
                ? 'write_in_flight'
                : null;
  const activateCommand =
    activateReason === null && activatePick !== null
      ? command(state, { kind: 'profile_activate', name: activatePick }, null)
      : null;
  // F6-14 PE1: use no profile, only while one is active.
  const unbindReason = records.some((record) =>
    unbindUnresolved(record, settledByRead(state, record)),
  )
    ? 'unbind_unresolved'
    : context?.profile === null || context?.profile === undefined
      ? 'none_active'
      : sending
        ? 'write_in_flight'
        : null;
  const unbindCommand =
    unbindReason === null ? command(state, { kind: 'profile_activate', name: null }, null) : null;

  const copyScope = profilesScope(state, 'copy');
  const copyDraft = draftOf(state, copyScope);
  const copyFrom = textOf(copyDraft, 'from', STARTER_PROFILE);
  const copyName = textOf(copyDraft, 'name');
  const copyExists = fileNamed(copyName.trim()) !== null;
  const copyKey = consentKey('copy', copyFrom, copyName.trim());
  const copyOverwrite = copyExists && textOf(copyDraft, 'overwrite') === copyKey;
  const copyReason =
    files === null
      ? 'files_not_read'
      : copyName.trim() !== '' &&
          (fileNamed(copyFrom) === null || fileNamed(copyFrom)?.draft === true)
        ? 'choose_source'
        : (destinationCheck(copyName, files, copyOverwrite) ??
          (sending ? 'write_in_flight' : null));
  const copyCommand =
    copyReason === null && copyScope !== null
      ? command(
          state,
          {
            kind: 'profile_copy',
            from: copyFrom,
            name: copyName.trim(),
            overwrite: copyOverwrite,
            existed: copyExists,
          },
          { scope: copyScope, values: submitted(copyDraft, ['from', 'name', 'overwrite']) },
        )
      : null;

  const promoteScope = profilesScope(state, 'promote');
  const promoteDraft = draftOf(state, promoteScope);
  const drafts = (files?.files ?? []).filter((file) => file.draft);
  const promoteFrom = textOf(promoteDraft, 'draft', drafts[0]?.name ?? '');
  const promoteName = textOf(promoteDraft, 'name');
  const promoteBase = textOf(promoteDraft, 'base', STARTER_PROFILE);
  const promoteExists = fileNamed(promoteName.trim()) !== null;
  const promoteKey = consentKey('promote', promoteFrom, promoteName.trim(), promoteBase);
  const promoteOverwrite = promoteExists && textOf(promoteDraft, 'overwrite') === promoteKey;
  const draftFile = drafts.find((file) => file.name === promoteFrom) ?? null;
  const promoteReason =
    files === null
      ? 'files_not_read'
      : draftFile?.file === null || draftFile === null
        ? 'no_draft'
        : (destinationCheck(promoteName, files, promoteOverwrite) ??
          (sending ? 'write_in_flight' : null));
  const promoteCommand =
    promoteReason === null && promoteScope !== null && draftFile?.file != null
      ? command(
          state,
          {
            kind: 'profile_promote',
            draft: draftFile.file,
            name: promoteName.trim(),
            base: promoteBase,
            overwrite: promoteOverwrite,
            existed: promoteExists,
          },
          {
            scope: promoteScope,
            values: submitted(promoteDraft, ['draft', 'name', 'base', 'overwrite']),
          },
        )
      : null;

  const offered = (reason: string | null, next: ProfileWriteCommand | null) =>
    reason === null && next !== null
      ? commandVM(true, true, null, false, next)
      : commandVM<ProfileWriteCommand>(true, false, reason ?? 'context_unknown', sending, null);

  const notice = state.notices.find((entry) => entry.kind === 'command_not_sent');
  return {
    epoch: state.epoch,
    context,
    profile: fromServer('name', profile),
    account: {
      read: accountsRead.state,
      readAgeSec:
        state.reads.accounts.lastAnswered === null
          ? null
          : readAgeSec(state, state.reads.accounts.lastAnswered),
      instance: fromServer('id', accounts?.instance ?? context?.instance),
      active: choices.find((choice) => choice.active) ?? null,
      accounts: choices,
      pickScope,
      pick,
      select: offered(selectReason, selectCommand),
      keeps: { edits, unresolved },
      create: {
        scope: createScope,
        id: createId,
        label: createLabel,
        importCurrent,
        importOffered,
        command: offered(createReason, createCommand),
        edited: createId !== '' || createLabel !== '',
      },
      outcome: newestOf(state, records, ['account_select', 'account_create']),
    },
    tier: {
      current,
      defaultValue,
      setBy:
        source?.tierSetBy === null || source === null
          ? null
          : source.tierSetBy === 'operator'
            ? 'operator'
            : 'other',
      setByText: fromServer('id', source?.tierSetBy),
      scope: tierScope,
      shown,
      edited: tierEdited,
      invalid: tierCheck.kind === 'invalid' && typeof typed === 'string' ? tierCheck.reason : null,
      save: offered(tierReason, tierCommand),
      outcome: newestOf(
        state,
        records.filter(
          (record) =>
            record.context !== null &&
            context !== null &&
            contextScopeKey(record.context) === contextScopeKey(context),
        ),
        ['account_tier'],
      ),
    },
    setup: setupState({
      tools: answeredBody(state, 'wizardTools'),
      calibrate: answeredBody(state, 'calibrate'),
      runs: answeredBody(state, 'runs'),
    }),
    profiles: {
      read: filesRead.state,
      active: fromServer('name', files?.active ?? profile),
      activeValidation: validationVM(activeSource),
      activeSourceError:
        sourceRead?.outcome.kind === 'answered' && sourceRead.outcome.status !== 200
          ? fromServer('message', own(sourceRead.outcome.body, 'error'))
          : null,
      rows,
      activate: {
        scope: activateScope,
        pick: activatePick,
        check:
          activatePick === null
            ? 'none'
            : pickedCheck === null
              ? 'checking'
              : (check?.kind ?? 'checking'),
        blockedReason: check?.kind === 'blocked' ? check.reason : null,
        validation: pickedCheck === null ? null : validationVM(pickedCheck.body),
        checkError: fromServer('message', pickedCheck?.error),
        command: offered(activateReason, activateCommand),
        outcome: newestOf(state, records, ['profile_activate']),
      },
      unbind:
        files === null &&
        !records.some((record) => record.kind === 'profile_activate' && record.meta.unbind === true)
          ? null
          : {
              account: fromServer('name', unbindCommand?.context.account),
              instance: fromServer('name', unbindCommand?.context.instance),
              active: fromServer('name', context?.profile),
              command: offered(unbindReason, unbindCommand),
              outcome: newestOf(state, records, ['profile_activate'], true),
            },
      copy: {
        scope: copyScope,
        from: copyFrom,
        name: copyName,
        overwrite: copyOverwrite,
        overwriteKey: copyKey,
        exists: copyExists,
        sources: rows.filter((row) => row.kind !== 'draft'),
        command: offered(copyReason, copyCommand),
      },
      promote: {
        scope: promoteScope,
        draft: promoteFrom,
        name: promoteName,
        base: promoteBase,
        overwrite: promoteOverwrite,
        overwriteKey: promoteKey,
        exists: promoteExists,
        drafts: rows.filter((row) => row.kind === 'draft'),
        bases: rows.filter((row) => row.kind !== 'draft'),
        command: offered(promoteReason, promoteCommand),
      },
      filesOutcome: newestOf(state, records, ['profile_copy', 'profile_promote']),
    },
    notSent:
      notice !== undefined && NOT_SENT_REASONS.includes(notice.reason) ? notice.reason : null,
  };
}
