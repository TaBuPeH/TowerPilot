import type { AbilitiesApi } from '../api/abilities.ts';
import type { PageContext } from '../api/context.ts';
import type { ApiPath } from '../api/transport.ts';
import { editingTarget, type EditingTarget } from '../core/editingTarget.ts';
import { STARTER_PROFILE } from '../core/profiles.ts';
import { entries, own, ownPath, record, text } from '../core/read.ts';
import { profilePatchAnswer, sentPatch, settleProfilePatch } from '../core/profilePatch.ts';
import {
  settingsContext,
  settingsView,
  settingsModule,
  settingsSaveAnswer,
  settleSettingsSave,
} from '../core/settingsFields.ts';
import { createStore, viewEqual, type Store } from './store.ts';
import {
  abilityNode,
  abilityValue,
  invalidAbilityNumbers,
  reconcileAbilityNode,
  abilityRules,
  nodeAt,
  restoreAbilityNode,
  setAbilityNode,
  specNode,
  type AbilityNode,
  type AbilityPath,
} from './abilitiesDraft.ts';

export type AbilityFamily = 'uw_policies' | 'rescue_policies';
export type AbilityOutcome =
  | 'checking'
  | 'sending'
  | 'saved'
  | 'unknown'
  | 'refused'
  | 'conflict'
  | 'contextChanged'
  | 'readApplied'
  | 'readUnchanged'
  | 'notSent';
interface Submission {
  readonly node?: AbilityNode | undefined;
  readonly path: ApiPath;
  readonly body: Readonly<Record<string, unknown>>;
  readonly revision: string;
  readonly original: unknown;
  readonly value: unknown;
  readonly version: number;
  readonly deleted: boolean;
  readonly dirty: boolean;
  readonly isNew: boolean;
}
export interface AbilityEditor {
  readonly id: string;
  readonly family: AbilityFamily | 'global';
  readonly node: AbilityNode;
  readonly original: unknown;
  readonly revision: string | null;
  readonly dirty: boolean;
  readonly version: number;
  readonly review: boolean;
  readonly reviewed: boolean;
  readonly stash: Readonly<Record<string, AbilityNode>>;
  readonly outcome: AbilityOutcome | null;
  readonly submission: Submission | null;
  readonly detail: unknown;
  readonly blockedReason?:
    'context_changed' | 'context_unconfirmed' | 'target_missing' | 'target_unconfirmed' | undefined;
  readonly isNew: boolean;
  readonly deleting: boolean;
}
export interface AbilitiesTarget {
  readonly source: unknown;
  readonly settings: unknown;
  readonly vocab: unknown;
  readonly editors: Readonly<Record<string, AbilityEditor>>;
  readonly selected: string | null;
  readonly newIds: Readonly<Record<AbilityFamily, string>>;
  readonly busy: boolean;
  readonly error: boolean;
  readonly sourceError?: unknown;
  readonly readAt: number | null;
  readonly settled: number;
}
export interface AbilitiesState {
  readonly context: PageContext | null;
  readonly editing: EditingTarget;
  readonly epoch: number;
  readonly key: string | null;
  readonly targets: Readonly<Record<string, AbilitiesTarget>>;
}
export interface AbilitiesStore extends Store<AbilitiesState> {
  readonly setTarget: (context: PageContext | null, epoch: number, target?: EditingTarget) => void;
  readonly refresh: () => Promise<void>;
  readonly select: (key: string) => void;
  readonly newId: (family: AbilityFamily, value: string) => void;
  readonly optionalRange: (path: AbilityPath, enabled: boolean) => void;
  readonly edit: (path: AbilityPath, node: AbilityNode) => void;
  readonly switchMode: (path: AbilityPath, mode: string) => void;
  readonly rulePart: (index: number, part: 'when' | 'do', name: string) => void;
  readonly rule: (action: 'add' | 'up' | 'down' | 'delete', index?: number) => void;
  readonly create: (family: AbilityFamily, id: string, copy: boolean) => void;
  readonly review: (value: boolean) => void;
  readonly cancel: () => void;
  readonly save: (deleting?: boolean) => void;
  readonly reconcile: () => void;
  readonly dispose: () => void;
}
const emptyTarget = (): AbilitiesTarget => ({
  source: undefined,
  settings: undefined,
  vocab: undefined,
  editors: {},
  selected: null,
  newIds: { uw_policies: '', rescue_policies: '' },
  busy: false,
  error: false,
  readAt: null,
  settled: 0,
});
export const abilityKey = (family: string, id: string) => `${family}/${id}`;
export const currentAbilities = (s: AbilitiesState): AbilitiesTarget | null =>
  s.key === null ? null : (s.targets[s.key] ?? null);
export const currentAbility = (s: AbilitiesState): AbilityEditor | null => {
  const t = currentAbilities(s);
  const entry = t?.selected == null ? null : (t.editors[t.selected] ?? null);
  return s.editing.inactive && entry?.family === 'global' ? null : entry;
};
export const sourceRevision = (value: unknown) => text(own(value, 'revision')) ?? null;
export const abilityModule = (settings: unknown) =>
  settingsModule(settingsView(settings), 'global_abilities');
export const globalAbilityValue = (settings: unknown) =>
  abilityModule(settings)?.fields.find((field) => field.path === 'demon_mode_always')?.value;
function savedValue(t: AbilitiesTarget, e: AbilityEditor): unknown {
  return e.family === 'global'
    ? globalAbilityValue(t.settings)
    : ownPath(t.source, 'profile', 'policies', e.family, e.id);
}
export function abilityConsumers(t: AbilitiesTarget, e: AbilityEditor): readonly string[] {
  const slot = e.family === 'uw_policies' ? 'uw' : 'rescue';
  return entries(ownPath(t.source, 'profile', 'blueprints'))
    .filter(([, bp]) =>
      e.family === 'global'
        ? typeof ownPath(bp, 'policies', 'rescue') === 'string'
        : ownPath(bp, 'policies', slot) === e.id,
    )
    .map(([key]) => key);
}
const settingsMatch = (body: unknown, context: PageContext) =>
  ownPath(body, 'context', 'instance') === context.instance &&
  ownPath(body, 'context', 'account') === context.account &&
  ownPath(body, 'context', 'profile') === context.profile;
const unresolvedAbility = (e: AbilityEditor) =>
  e.outcome === 'checking' || e.outcome === 'sending' || e.outcome === 'unknown';
const refusalOutcome = (
  answer: { readonly body: unknown; readonly status: number } | null | undefined,
) =>
  own(answer?.body, 'code') === 'context_changed'
    ? 'contextChanged'
    : answer?.status === 409
      ? 'conflict'
      : 'refused';
const pending = (t: AbilitiesTarget) => Object.values(t.editors).some(unresolvedAbility);
export function abilityReadOnly(s: AbilitiesState): boolean {
  const e = currentAbility(s);
  return (
    s.editing.blocked !== null ||
    s.editing.name === STARTER_PROFILE ||
    s.editing.name?.endsWith('.draft') === true ||
    (e?.family === 'global' &&
      (s.editing.inactive ||
        own(abilityModule(currentAbilities(s)?.settings), 'editable') !== true))
  );
}
export function canSaveAbility(s: AbilitiesState, now: number, deleting = false): boolean {
  const t = currentAbilities(s),
    e = currentAbility(s);
  if (
    t === null ||
    e === null ||
    s.context === null ||
    s.editing.name === null ||
    abilityReadOnly(s) ||
    t.busy ||
    t.error ||
    t.readAt === null ||
    now - t.readAt > 30_000 ||
    pending(t) ||
    e.revision === null ||
    e.revision !== sourceRevision(e.family === 'global' ? t.settings : t.source) ||
    own(t.source, 'validator_error') != null
  )
    return false;
  if (e.family === 'global')
    return !s.editing.inactive && own(abilityModule(t.settings), 'editable') === true && e.dirty;
  if (deleting)
    return !e.isNew && savedValue(t, e) !== undefined && abilityConsumers(t, e).length === 0;
  return (
    e.dirty &&
    (!e.review || e.reviewed) &&
    !invalidAbilityNumbers(e.node) &&
    (e.isNew ? savedValue(t, e) === undefined : savedValue(t, e) !== undefined)
  );
}
/** An advanced revision proves a still-pending old CAS cannot newly overwrite this read.
 * A person may explicitly adopt that read without claiming the old write's outcome. */
export function canReconcileAbility(s: AbilitiesState, now: number): boolean {
  const t = currentAbilities(s),
    e = currentAbility(s);
  if (t === null || e === null || t.busy || t.error || t.readAt === null || now - t.readAt > 30_000)
    return false;
  if (
    Object.values(t.editors).some(
      (entry) =>
        entry.outcome === 'checking' ||
        entry.outcome === 'sending' ||
        (entry.outcome === 'unknown' && entry !== e),
    )
  )
    return false;
  const revision = sourceRevision(e.family === 'global' ? t.settings : t.source);
  // An unchanged revision cannot exclude a commit still to come. Keep its frozen
  // submission; only a newer CAS revision can safely be adopted explicitly.
  return (
    revision !== null &&
    (e.outcome !== 'unknown' || (e.submission !== null && revision !== e.submission.revision))
  );
}
function editor(
  family: AbilityEditor['family'],
  id: string,
  value: unknown,
  revision: string | null,
  isNew = false,
): AbilityEditor {
  return {
    family,
    id,
    node: abilityNode(value),
    original: value,
    revision,
    dirty: isNew,
    version: 0,
    review: false,
    reviewed: false,
    stash: {},
    outcome: null,
    submission: null,
    detail: undefined,
    isNew,
    deleting: false,
  };
}

/** Abilities owns raw drafts, target fences and explicit configuration saves. The
 * backend alone owns policy validity, binding readiness and actual ability timing. */
export function createAbilitiesStore(
  api: AbilitiesApi,
  now: () => number = Date.now,
): AbilitiesStore {
  const store = createStore<AbilitiesState>({
    context: null,
    editing: editingTarget(null, undefined, null),
    key: null,
    epoch: 0,
    targets: {},
  });
  let controller: AbortController | null = null;
  let sequence = 0;
  const change = (key: string, transform: (t: AbilitiesTarget) => AbilitiesTarget) => {
    store.update((s) => ({
      ...s,
      targets: { ...s.targets, [key]: transform(s.targets[key] ?? emptyTarget()) },
    }));
    const t = store.getSnapshot().targets[key];
    if (t !== undefined) api.keep(key, { selected: t.selected, editors: t.editors });
  };
  const changeEditor = (transform: (e: AbilityEditor) => AbilityEditor) => {
    const key = store.getSnapshot().key;
    if (key === null) return;
    change(key, (t) => {
      const name = t.selected,
        e = name === null ? undefined : t.editors[name];
      return name === null || e === undefined
        ? t
        : { ...t, editors: { ...t.editors, [name]: transform(e) } };
    });
  };
  const edited = (e: AbilityEditor, node: AbilityNode, review = e.review): AbilityEditor => ({
    ...e,
    node,
    dirty: true,
    version: e.version + 1,
    review,
    reviewed: false,
  });
  const sync = () => {
    // A refusal describes the exact frozen submission, even after its runtime epoch
    // changed. Apply it before considering read settlement; successful answers still
    // pass the current-epoch checks below.
    for (const [key, target] of Object.entries(store.getSnapshot().targets)) {
      for (const [name, e] of Object.entries(target.editors)) {
        const sub = e.submission;
        const refusal = sub === null ? null : api.profileRefusal(key, sub.body);
        if (
          sub === null ||
          refusal === null ||
          (e.outcome === refusalOutcome(refusal.answer) && e.detail === refusal.answer?.body)
        )
          continue;
        change(key, (current) => ({
          ...current,
          readAt: null,
          settled: ++sequence,
          editors: {
            ...current.editors,
            [name]: {
              ...e,
              outcome: refusalOutcome(refusal.answer),
              detail: refusal.answer?.body,
              original: sub.original,
              revision: sub.revision,
              dirty: e.dirty || sub.dirty || e.outcome === 'readApplied',
              isNew: sub.isNew,
              deleting: false,
            },
          },
        }));
      }
    }
    const s = store.getSnapshot(),
      t = currentAbilities(s);
    if (s.key === null || t === null) return;
    for (const intent of api.machine.snapshot()) {
      if (
        intent.epoch !== s.epoch ||
        !intent.target.startsWith(`abilities:${s.key}:`) ||
        intent.supersededBySeq !== null
      )
        continue;
      const found = Object.entries(t.editors).find(
        ([, e]) =>
          e.submission !== null &&
          JSON.stringify(e.submission.body) === intent.request.body &&
          (e.outcome === 'sending' || e.outcome === 'unknown' || e.outcome === 'readApplied'),
      );
      if (found === undefined) continue;
      const [name, e] = found,
        sub = e.submission;
      if (
        sub === null ||
        intent.phase === 'sending' ||
        (intent.phase === 'unconfirmed' && e.outcome !== 'sending')
      )
        continue;
      const body = intent.answer?.body,
        revision = sourceRevision(body);
      const value =
        e.family === 'global'
          ? globalAbilityValue(body)
          : ownPath(body, 'profile', 'policies', e.family, e.id);
      const latest = sourceRevision(e.family === 'global' ? t.settings : t.source);
      const receipt =
        intent.answer === null
          ? null
          : e.family === 'global'
            ? settingsSaveAnswer(intent.answer, {
                module: 'global_abilities',
                revision: sub.revision,
                values: { demon_mode_always: sub.value },
              })
            : profilePatchAnswer(intent.answer);
      const validSuccess =
        (receipt?.kind === 'saved' || receipt?.kind === 'done') &&
        revision !== null &&
        (e.family === 'global'
          ? settingsMatch(
              body,
              s.context ?? { instance: '', serial: '', account: null, profile: null },
            )
          : record(own(body, 'profile')) !== undefined) &&
        (sub.deleted ? value === undefined : viewEqual(value, sub.value));
      const newerRead = latest !== sub.revision && latest !== revision;
      const refused = receipt?.kind === 'refused';
      const outcome: AbilityOutcome = validSuccess
        ? newerRead
          ? 'conflict'
          : 'saved'
        : refused
          ? refusalOutcome(intent.answer)
          : 'unknown';
      const key = s.key;
      change(key, (current) => ({
        ...current,
        readAt: null,
        settled: ++sequence,
        ...(outcome === 'saved'
          ? e.family === 'global'
            ? { settings: body }
            : {
                source: {
                  ...Object.fromEntries(entries(current.source)),
                  ...Object.fromEntries(entries(body)),
                },
              }
          : {}),
        editors: {
          ...current.editors,
          [name]: {
            ...(current.editors[name] ?? e),
            outcome,
            detail: body,
            ...(outcome === 'saved'
              ? {
                  original: sub.deleted ? undefined : sub.value,
                  revision,
                  isNew: sub.deleted && e.version !== sub.version,
                  dirty: e.version !== sub.version,
                  deleting: sub.deleted && e.version === sub.version,
                  // Keep deletion provenance for newer edits that now create this policy.
                  submission:
                    sub.deleted && e.version !== sub.version ? { ...sub, node: undefined } : null,
                }
              : {}),
          },
        },
      }));
    }
  };
  const unsubscribe = api.machine.subscribe(sync);
  const refresh = async () => {
    const s = store.getSnapshot(),
      { key, context, epoch, editing } = s;
    if (
      key === null ||
      context === null ||
      editing.name === null ||
      editing.blocked !== null ||
      controller !== null
    )
      return;
    const started = ++sequence,
      request = new AbortController();
    controller = request;
    const timeout = setTimeout(() => {
      request.abort();
    }, 10_000);
    change(key, (t) => ({ ...t, busy: true, error: false }));
    const first = await api.config(request.signal);
    const good = first.kind === 'answered' && api.configMatches(first.answer.body, context);
    const reads = good
      ? await Promise.all([
          api.profile(editing.name, request.signal),
          editing.inactive ? Promise.resolve(null) : api.settings(request.signal),
          api.vocab(request.signal),
        ])
      : null;
    const last = good ? await api.config(request.signal) : null;
    clearTimeout(timeout);
    if (
      controller !== request ||
      store.getSnapshot().key !== key ||
      store.getSnapshot().epoch !== epoch ||
      store.getSnapshot().editing.generation !== editing.generation
    )
      return;
    controller = null;
    const [profile, settings, vocab] = reads ?? [];
    const current = store.getSnapshot().targets[key];
    if (current !== undefined && current.settled > started) {
      change(key, (t) => ({ ...t, busy: false }));
      return;
    }
    const valid =
      good &&
      last?.kind === 'answered' &&
      api.configMatches(last.answer.body, context) &&
      own(last.answer.body, 'config_revision') === own(first.answer.body, 'config_revision') &&
      profile?.kind === 'answered' &&
      own(profile.answer.body, 'ok') === true &&
      own(profile.answer.body, 'name') === editing.name &&
      sourceRevision(profile.answer.body) !== null &&
      vocab?.kind === 'answered' &&
      own(vocab.answer.body, 'ok') === true &&
      record(own(vocab.answer.body, 'vocab')) !== undefined;
    if (!valid) {
      change(key, (t) => ({
        ...t,
        busy: false,
        error: true,
        sourceError: own(profile?.answer?.body, 'error'),
      }));
      return;
    }
    const source = profile.answer.body,
      settingsBody =
        settings?.kind === 'answered' &&
        own(settings.answer.body, 'ok') === true &&
        settingsMatch(settings.answer.body, context) &&
        sourceRevision(settings.answer.body) !== null
          ? settings.answer.body
          : undefined,
      vocabulary = own(vocab.answer.body, 'vocab');
    change(key, (t) => {
      const target = {
        ...t,
        source,
        settings: settingsBody,
        vocab: vocabulary,
        busy: false,
        error: false,
        sourceError: undefined,
        readAt: now(),
      };
      const editors: Record<string, AbilityEditor> = { ...t.editors };
      const awaitingAnswer = (e: AbilityEditor) =>
        e.submission !== null && api.awaitingAnswer(key, e.submission.body);
      const snapshots: [AbilityEditor['family'], string, unknown, string | null][] = [];
      if (!editing.inactive && settingsBody !== undefined)
        snapshots.push([
          'global',
          'demon_mode_always',
          globalAbilityValue(settingsBody),
          sourceRevision(settingsBody),
        ]);
      for (const family of ['uw_policies', 'rescue_policies'] as const)
        for (const [id, value] of entries(ownPath(source, 'profile', 'policies', family)))
          snapshots.push([family, id, value, sourceRevision(source)]);
      for (const [family, id, value, revision] of snapshots) {
        const name = abilityKey(family, id),
          e = editors[name];
        if (
          e === undefined ||
          (!e.dirty &&
            !unresolvedAbility(e) &&
            !['refused', 'conflict', 'contextChanged'].includes(e.outcome ?? '') &&
            !awaitingAnswer(e))
        ) {
          const fresh = editor(family, id, value, revision);
          const merged = e === undefined ? fresh.node : reconcileAbilityNode(e.node, value);
          editors[name] =
            e === undefined
              ? fresh
              : {
                  ...fresh,
                  node: merged ?? e.node,
                  stash: e.stash,
                };
          if (e !== undefined && merged === null)
            editors[name] = {
              ...e,
              outcome: 'conflict',
              dirty: true,
              review: true,
              reviewed: false,
            };
        }
      }
      for (const [name, e] of Object.entries(editors)) {
        if (
          !e.isNew &&
          !e.dirty &&
          !unresolvedAbility(e) &&
          e.outcome !== 'contextChanged' &&
          !awaitingAnswer(e) &&
          !(e.family === 'global' && settingsBody === undefined) &&
          savedValue(target, e) === undefined
        ) {
          Reflect.deleteProperty(editors, name);
          continue;
        }
        const sub = e.submission;
        if (
          sub === null ||
          (e.outcome !== 'unknown' && !(e.outcome === 'readApplied' && !awaitingAnswer(e)))
        )
          continue;
        const value = savedValue(target, e),
          revision = sourceRevision(e.family === 'global' ? settingsBody : source);
        const sent = sentPatch(sub.body);
        const sentContext = settingsContext(own(sub.body, 'context'));
        // Shared rule (review F6C-R1-02, R1-05): a read only proves the values arrived, for
        // the context the save named; an unchanged read keeps the save unknown (it may still
        // commit), so its submission stays journaled.
        const settlement =
          e.family === 'global'
            ? sentContext === null
              ? null
              : settleSettingsSave(
                  settingsBody,
                  {
                    module: 'global_abilities',
                    revision: sub.revision,
                    values: { demon_mode_always: sub.value },
                  },
                  sentContext,
                )
            : sent === null
              ? null
              : settleProfilePatch(source, sent);
        if (settlement === 'applied')
          editors[name] = {
            ...e,
            outcome: 'readApplied',
            ...(awaitingAnswer(e)
              ? {}
              : {
                  original: value,
                  revision,
                  dirty: e.version !== sub.version,
                  deleting: sub.deleted && e.version === sub.version,
                  isNew: sub.deleted && e.version !== sub.version,
                  submission:
                    sub.deleted && e.version !== sub.version ? { ...sub, node: undefined } : null,
                }),
            detail: undefined,
          };
      }
      return {
        ...target,
        editors,
        selected:
          (t.selected !== null &&
          Object.hasOwn(editors, t.selected) &&
          !(editing.inactive && editors[t.selected]?.family === 'global')
            ? t.selected
            : null) ??
          snapshots.map(([f, id]) => abilityKey(f, id)).find((n) => n.startsWith('uw_policies/')) ??
          null,
      };
    });
  };
  const save = async (deleting = false) => {
    const s = store.getSnapshot(),
      t = currentAbilities(s),
      e = currentAbility(s);
    if (
      !canSaveAbility(s, now(), deleting) ||
      s.context === null ||
      t === null ||
      e === null ||
      e.revision === null
    )
      return;
    const value = abilityValue(e.node),
      path: ApiPath = e.family === 'global' ? '/api/settings' : '/api/profile-patch';
    const body =
      e.family === 'global'
        ? {
            context: own(t.settings, 'context'),
            revision: e.revision,
            module: 'global_abilities',
            values: { demon_mode_always: value },
          }
        : {
            name: s.editing.name,
            expected_context: api.expectedContext(s.context),
            expected_revision: e.revision,
            path: ['policies', e.family, e.id],
            ...(deleting ? { op: 'delete' } : { value }),
          };
    const key = s.key,
      name = t.selected;
    if (key === null || name === null || s.editing.name === null) return;
    const updateSubmitted = (transform: (entry: AbilityEditor) => AbilityEditor) => {
      change(key, (current) => {
        const entry = current.editors[name];
        return entry === undefined
          ? current
          : {
              ...current,
              editors: { ...current.editors, [name]: transform(entry) },
            };
      });
    };
    if (e.family !== 'global') {
      updateSubmitted((current) => ({
        ...current,
        outcome: 'checking',
        detail: undefined,
        blockedReason: undefined,
      }));
      const read = s.editing.inactive ? null : await api.readClickContext();
      const checked = s.editing.inactive
        ? await api.checkTarget(s.context, s.editing.name)
        : read === null
          ? 'context_unconfirmed'
          : api.targetKey(read) === api.targetKey(s.context)
            ? 'same'
            : 'context_changed';
      const latest = store.getSnapshot();
      if (
        latest.epoch !== s.epoch ||
        latest.key !== key ||
        latest.editing.generation !== s.editing.generation ||
        latest.editing.blocked !== null
      ) {
        // setTarget already marked this check not sent. Do not overwrite a
        // newer explicit save after a person leaves and returns to this file.
        return;
      }
      if (checked !== 'same') {
        updateSubmitted((current) => ({
          ...current,
          outcome: 'notSent',
          detail: undefined,
          blockedReason: checked,
        }));
        return;
      }
    }
    const sent = api.send(s.context, path, body, key);
    if (sent.kind !== 'sent' && sent.kind !== 'resent') {
      updateSubmitted((current) => ({ ...current, outcome: 'notSent', detail: undefined }));
      return;
    }
    updateSubmitted((current) => ({
      ...current,
      outcome: 'sending',
      detail: undefined,
      blockedReason: undefined,
      submission: {
        node: e.node,
        path,
        body,
        revision: e.revision ?? '',
        original: e.original,
        value,
        version: e.version,
        deleted: deleting,
        dirty: e.dirty,
        isNew: e.isNew,
      },
    }));
    sync();
  };

  return {
    ...store,
    refresh,
    setTarget: (context, epoch, supplied) => {
      const editing = supplied ?? editingTarget(context, undefined, null);
      const key =
        context === null || editing.name === null
          ? null
          : api.editTargetScope(context, editing.name);
      const before = store.getSnapshot();
      if (
        key === before.key &&
        epoch === before.epoch &&
        editing.generation === before.editing.generation &&
        editing.blocked === before.editing.blocked
      )
        return;
      controller?.abort();
      controller = null;
      const old = store.getSnapshot().key;
      if (old !== null)
        change(old, (t) => ({
          ...t,
          busy: false,
          readAt: null,
          editors: Object.fromEntries(
            Object.entries(t.editors).map(([name, e]) => [
              name,
              e.outcome === 'sending'
                ? { ...e, outcome: 'unknown' }
                : e.outcome === 'checking'
                  ? { ...e, outcome: 'notSent', blockedReason: 'context_changed' }
                  : e,
            ]),
          ),
        }));
      store.update((s) => ({ ...s, context, key, epoch, editing }));
      api.machine.setContext(context, epoch);
      if (key === null) return;
      if (store.getSnapshot().targets[key] === undefined) {
        const saved = api.load(key),
          editors: Record<string, AbilityEditor> = {};
        for (const [name, raw] of entries(own(saved, 'editors'))) {
          if (own(raw, 'deleting') === true && own(raw, 'dirty') !== true) continue;
          const node = restoreAbilityNode(own(raw, 'node')),
            family = own(raw, 'family'),
            id = text(own(raw, 'id'));
          if (
            node === null ||
            id === undefined ||
            !['global', 'uw_policies', 'rescue_policies'].includes(
              typeof family === 'string' ? family : '',
            )
          )
            continue;
          const f =
            family === 'global'
              ? 'global'
              : family === 'uw_policies'
                ? 'uw_policies'
                : 'rescue_policies';
          const sub = own(raw, 'submission'),
            subBody = record(own(sub, 'body')),
            subRevision = text(own(sub, 'revision')),
            subPath = own(sub, 'path'),
            version = own(raw, 'version'),
            savedOutcome = text(own(raw, 'outcome')),
            unresolved =
              savedOutcome === 'sending' ||
              savedOutcome === 'unknown' ||
              savedOutcome === 'readApplied',
            deletedDraft =
              own(raw, 'dirty') === true &&
              own(sub, 'deleted') === true &&
              (savedOutcome === 'saved' || savedOutcome === 'readApplied');
          const refusal = api.profileRefusal(key, subBody);
          const restored = editor(
            f,
            id,
            own(raw, 'original'),
            text(own(raw, 'revision')) ?? null,
            own(raw, 'isNew') === true ||
              (own(raw, 'deleting') === true && own(raw, 'dirty') === true),
          );
          const stash = Object.fromEntries(
            entries(own(raw, 'stash')).flatMap(([key, value]) => {
              const restoredNode = restoreAbilityNode(value);
              return restoredNode === null ? [] : [[key, restoredNode]];
            }),
          );
          editors[name] = {
            ...restored,
            node,
            dirty: own(raw, 'dirty') === true,
            version: typeof version === 'number' ? version : 0,
            review: own(raw, 'review') === true,
            reviewed: false,
            stash,
            ...((unresolved || deletedDraft || refusal !== null) &&
            subBody !== undefined &&
            subRevision !== undefined &&
            (subPath === '/api/settings' || subPath === '/api/profile-patch')
              ? {
                  outcome:
                    refusal !== null
                      ? refusalOutcome(refusal.answer)
                      : unresolved
                        ? 'unknown'
                        : savedOutcome === 'readApplied'
                          ? 'readApplied'
                          : 'saved',
                  detail: refusal?.answer?.body,
                  submission: {
                    node: restoreAbilityNode(own(sub, 'node')) ?? undefined,
                    path: subPath,
                    body: Object.fromEntries(entries(subBody)),
                    revision: subRevision,
                    original: own(sub, 'original'),
                    value: own(sub, 'value'),
                    version:
                      typeof own(sub, 'version') === 'number' ? Number(own(sub, 'version')) : 0,
                    deleted: own(sub, 'deleted') === true,
                    dirty: own(sub, 'dirty') === true,
                    isNew:
                      own(sub, 'isNew') === true ||
                      (own(sub, 'isNew') === undefined && own(raw, 'isNew') === true),
                  },
                }
              : {}),
          };
        }
        change(key, () => ({
          ...emptyTarget(),
          editors,
          newIds: {
            uw_policies: api.loadNewId(key, 'uw_policies'),
            rescue_policies: api.loadNewId(key, 'rescue_policies'),
          },
          selected: text(own(saved, 'selected')) ?? null,
        }));
      }
      void refresh();
    },
    select: (name) => {
      const key = store.getSnapshot().key;
      if (key !== null)
        change(key, (t) => (Object.hasOwn(t.editors, name) ? { ...t, selected: name } : t));
    },
    newId: (family, value) => {
      const key = store.getSnapshot().key;
      if (key === null) return;
      change(key, (t) => ({ ...t, newIds: { ...t.newIds, [family]: value } }));
      api.keepNewId(key, family, value);
    },
    optionalRange: (path, enabled) => {
      changeEditor((e) => {
        const slot = `optional:${path.join('/')}`,
          current = nodeAt(e.node, path);
        const stash = !enabled && current !== undefined ? { ...e.stash, [slot]: current } : e.stash;
        return {
          ...edited(
            e,
            setAbilityNode(
              e.node,
              path,
              enabled ? (stash[slot] ?? specNode({ type: 'list' })) : abilityNode(null),
            ),
            true,
          ),
          stash,
        };
      });
    },
    edit: (path, node) => {
      changeEditor((e) => edited(e, setAbilityNode(e.node, path, node)));
    },
    switchMode: (path, mode) => {
      changeEditor((e) => {
        const current = nodeAt(e.node, path),
          value = current === undefined ? undefined : abilityValue(current);
        const oldMode =
          path[0] === 'arm'
            ? value === 'always'
              ? 'always'
              : own(value, 'on') === 'second_wind'
                ? 'second_wind'
                : 'unspecified'
            : (text(own(value, 'mode')) ?? 'absent');
        const slot = path.join('/');
        const stash = { ...e.stash, [`${slot}:${oldMode}`]: current ?? abilityNode({}) };
        const next =
          stash[`${slot}:${mode}`] ??
          abilityNode(
            path[0] === 'arm' ? (mode === 'always' ? 'always' : { on: 'second_wind' }) : { mode },
          );
        return {
          ...edited(
            e,
            setAbilityNode(
              e.node,
              path,
              mode === 'absent' || mode === 'unspecified' ? undefined : next,
            ),
            true,
          ),
          stash,
        };
      });
    },
    rulePart: (index, part, name) => {
      changeEditor((e) => {
        const t = currentAbilities(store.getSnapshot()),
          spec = ownPath(
            t?.vocab,
            part === 'when' ? 'rule_triggers' : 'rule_actions',
            'fields',
            name,
          );
        if (
          spec === undefined ||
          ['switch_cards', 'surrender_retry', 'stop_after_run', 'death_screen'].includes(name)
        )
          return e;
        const rules = nodeAt(e.node, ['rules']);
        const old = nodeAt(e.node, ['rules', index, part]),
          slot = `rule:${rules?.kind === 'array' ? (rules.items[index]?.key ?? index) : index}:${part}`,
          oldName = entries(old === undefined ? undefined : abilityValue(old))[0]?.[0] ?? 'absent';
        const stash = { ...e.stash, [`${slot}:${oldName}`]: old ?? abilityNode({}) };
        const remembered = stash[`${slot}:${name}`];
        const value =
          remembered ??
          (part === 'when' && name === 'bar'
            ? abilityNode({ bar: '' })
            : abilityNode({ [name]: own(spec, 'type') === 'bool' ? true : {} }));
        const next =
          remembered === undefined && (own(spec, 'type') === 'int' || own(spec, 'type') === 'list')
            ? setAbilityNode(value, [name], specNode(spec))
            : value;
        return { ...edited(e, setAbilityNode(e.node, ['rules', index, part], next), true), stash };
      });
    },
    rule: (action, index) => {
      changeEditor((e) => {
        const rules = abilityRules(e.node);
        if (rules === null) return e;
        const items = [...rules.items];
        if (action === 'add') {
          const created = abilityNode([{}]);
          if (created.kind === 'array') items.push(...created.items);
        } else if (index !== undefined && index >= 0 && index < items.length) {
          if (action === 'delete') items.splice(index, 1);
          else {
            const to = index + (action === 'up' ? -1 : 1);
            const item = items[index];
            if (to >= 0 && to < items.length && item !== undefined) {
              items.splice(index, 1);
              items.splice(to, 0, item);
            }
          }
        }
        return edited(e, setAbilityNode(e.node, ['rules'], { kind: 'array', items }));
      });
    },
    create: (family, id, copy) => {
      const s = store.getSnapshot(),
        t = currentAbilities(s),
        key = s.key;
      if (
        t === null ||
        key === null ||
        !/^[-\w]+$/.test(id) ||
        Object.hasOwn(t.editors, abilityKey(family, id))
      )
        return;
      const selected = currentAbility(s);
      const value =
        copy && selected?.family === family
          ? abilityValue(selected.node)
          : family === 'uw_policies'
            ? { baseline: {} }
            : { arm: 'always', rules: [] };
      const created = editor(family, id, value, sourceRevision(t.source), true),
        e =
          copy && selected?.family === family
            ? { ...created, node: selected.node, stash: selected.stash }
            : created,
        name = abilityKey(family, id);
      change(key, (current) => ({
        ...current,
        editors: { ...current.editors, [name]: e },
        selected: name,
      }));
    },
    review: (reviewed) => {
      changeEditor((e) => ({ ...e, reviewed }));
    },
    cancel: () => {
      const s = store.getSnapshot(),
        t = currentAbilities(s),
        e = currentAbility(s),
        key = s.key,
        name = t?.selected;
      if (
        t === null ||
        e === null ||
        key === null ||
        name == null ||
        e.outcome === 'checking' ||
        e.outcome === 'sending' ||
        e.outcome === 'unknown'
      )
        return;
      if (e.isNew) {
        change(key, (current) => {
          const editors = { ...current.editors };
          Reflect.deleteProperty(editors, name);
          return {
            ...current,
            editors,
            selected: Object.keys(editors).find((k) => k.startsWith(e.family)) ?? null,
          };
        });
        return;
      }
      changeEditor((current) =>
        editor(
          current.family,
          current.id,
          savedValue(t, current),
          sourceRevision(current.family === 'global' ? t.settings : t.source),
        ),
      );
    },
    save: (deleting = false) => {
      void save(deleting);
    },
    reconcile: () => {
      const s = store.getSnapshot(),
        t = currentAbilities(s);
      if (t === null || !canReconcileAbility(s, now())) return;
      changeEditor((e) => {
        const retained =
          e.outcome === 'contextChanged' ||
          (s.key !== null && e.submission !== null && api.awaitingAnswer(s.key, e.submission.body));
        return {
          ...e,
          revision: sourceRevision(e.family === 'global' ? t.settings : t.source),
          original: savedValue(t, e),
          outcome: retained ? e.outcome : null,
          submission: retained ? e.submission : null,
          detail: retained ? e.detail : undefined,
          reviewed: false,
        };
      });
    },
    dispose: () => {
      controller?.abort();
      unsubscribe();
      api.machine.dispose();
    },
  };
}
