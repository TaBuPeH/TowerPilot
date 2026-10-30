import { finite, own, ownPath, stringList, text } from '../../core/read.ts';
import {
  draftJson,
  libraryBase,
  librarySource,
  reviewLegacy,
  shoppingVocabulary,
  type LibraryPolicy,
} from '../../core/shoppingLibrary.ts';
import { newQueue, queuePlan, shopCatalog, type QueueDraft } from '../../core/shoppingPlan.ts';
import { runControlsPath } from './gemSlot.ts';
import { fromServer } from '../../copy/server.ts';
import { settledByRead } from '../fences.ts';
import { boundIntents, contextScopeKey, hasConfirmedContext } from '../intents.ts';
import { answeredBody } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import type { ProfilePatchCommand } from './collection.ts';
import { runsPageRun } from './readPlan.ts';
import { saveOutcome, settingsSavePending } from './settingsModule.ts';
import { selectQueueEditor } from './shoppingQueue.ts';

export type LibraryReason =
  | 'unavailable'
  | 'pending'
  | 'unknown'
  | 'revision'
  | 'invalid'
  | 'unchanged'
  | 'used'
  | 'id'
  | 'creation'
  | null;

/** Library scopes use the same persistence boundary as run drafts, one policy per context. */
export function libraryScope(state: DashboardState, policy: string): string {
  return `runs:shopping-library:${encodeURIComponent(policy)}:${contextScopeKey(state.context)}`;
}

export function selectShoppingLibrary(state: DashboardState) {
  const context = state.context;
  const contextReady = hasConfirmedContext(state);
  const slot = state.reads.profileSource;
  const read = librarySource(answeredBody(state, 'profileSource'));
  const source = context !== null && read?.name === context.profile ? read : null;
  // Retain last-known policies for inspection on failed reads, but never offer a write.
  const latest = slot.latest;
  const readable =
    source !== null &&
    latest?.outcome.kind === 'answered' &&
    latest.outcome.status === 200 &&
    contextReady;
  const run = runsPageRun(state);
  const runBody = run === null ? undefined : state.runControls[runControlsPath(run)];
  const body =
    runBody?.outcome.kind === 'answered' && runBody.outcome.status === 200
      ? runBody.outcome.body
      : undefined;
  const catalog = shopCatalog(own(body, 'catalog'));
  const vocabulary = shoppingVocabulary(answeredBody(state, 'vocab'));
  const records = boundIntents(state).filter((record) => record.kind === 'profile_patch');
  const unknown = records.filter(
    (record) =>
      record.phase === 'unconfirmed' &&
      record.accepted !== true &&
      settledByRead(state, record) === null,
  );
  const pending = settingsSavePending(state, ['profileSource']);
  const gate: LibraryReason = !readable
    ? 'unavailable'
    : unknown.length > 0
      ? 'unknown'
      : pending
        ? 'pending'
        : null;
  const revision = source?.revision ?? null;
  const conflictOf = (scope: string, draft: QueueDraft) => {
    const record = records.findLast((item) => item.target === scope);
    return record?.phase === 'refused' &&
      own(record.answer?.body, 'code') === 'revision_conflict' &&
      draft['library_continued_after'] !== record.id
      ? record
      : null;
  };
  const command = (
    scope: string,
    policy: string,
    value: unknown,
    remove: boolean,
    draft: QueueDraft,
    reason: LibraryReason,
  ): ProfilePatchCommand | null => {
    const baseRevision = text(draft['library_revision']) ?? revision;
    return reason !== null || context === null || baseRevision === null
      ? null
      : {
          epoch: state.epoch,
          context,
          target: scope,
          path: ['policies', 'shopping_lists', policy],
          remove,
          value,
          revision: baseRevision,
          draft: { scope, values: draft },
        };
  };
  const after = Math.max(
    0,
    ...unknown.map((record) => record.answerSeq ?? record.lostSeq ?? record.sentSeq),
  );
  const canResolve = readable && latest.startedSeq > after;
  const options = (values: readonly string[]) =>
    values.flatMap((value) => {
      const label = fromServer('name', value);
      return label === null ? [] : [{ value, label }];
    });
  const savedPolicies: LibraryPolicy[] = [...(source?.policies ?? [])];
  for (const [scope, draft] of Object.entries(state.runDrafts)) {
    if (
      !scope.startsWith('runs:shopping-library:') ||
      !scope.endsWith(`:${contextScopeKey(state.context)}`) ||
      scope === libraryScope(state, '$create')
    )
      continue;
    const id = text(draft['library_id']);
    if (id === undefined) continue;
    if (savedPolicies.some((policy) => policy.id === id) || draft['library_base'] === undefined)
      continue;
    savedPolicies.push({
      id,
      value: draftJson(draft['library_base']),
      usedBy: stringList(draftJson(draft['library_usage'])) ?? [],
    });
  }
  const policies = savedPolicies.map((policy) => {
    const scope = libraryScope(state, policy.id);
    const draft = state.runDrafts[scope] ?? {};
    const baseValue = libraryBase(policy.value, draft);
    const base = { plan: queuePlan(baseValue), catalog };
    const queue = selectQueueEditor(base, draft);
    const legacy = reviewLegacy(baseValue, draft);
    const dirty = Object.keys(draft).length > 0;
    const conflict = conflictOf(scope, draft);
    const stale =
      conflict !== null ||
      (text(draft['library_revision']) !== undefined && draft['library_revision'] !== revision);
    const reason: LibraryReason =
      gate ??
      (stale
        ? 'revision'
        : !dirty
          ? 'unchanged'
          : base.plan === null
            ? legacy.value === null || vocabulary === null
              ? 'invalid'
              : null
            : queue.review.plan === null
              ? 'invalid'
              : null);
    const value = base.plan === null ? legacy.value : queue.review.plan;
    const removeReason: LibraryReason =
      policy.usedBy.length > 0 ? 'used' : (gate ?? (stale ? 'revision' : null));
    const choices = (values: readonly string[], current: string) =>
      options([...new Set([...values, ...(current === '' ? [] : [current])])]);
    return {
      contextReady,
      id: policy.id,
      name: fromServer('name', policy.id),
      scope,
      draft,
      baseValue,
      base,
      queue,
      latestValue: policy.value,
      revision,
      usage: policy.usedBy.map((key) => ({ key, name: fromServer('name', key) })),
      observed: fromServer('detail', JSON.stringify(policy.value, null, 2)),
      dirty,
      reason,
      removeReason,
      outcome: saveOutcome(state, scope),
      save: command(scope, policy.id, value, false, draft, reason),
      remove: command(scope, policy.id, null, true, draft, removeReason),
      continuation:
        canResolve &&
        (conflict === null || latest.startedSeq > (conflict.answerSeq ?? conflict.sentSeq)) &&
        (stale || unknown.length > 0) &&
        revision !== null
          ? { revision, unknownIds: unknown.map((record) => record.id), after: conflict?.id ?? '' }
          : null,
      legacy:
        base.plan !== null || legacy.kind === null
          ? null
          : {
              ...legacy,
              vocabularyReady: vocabulary !== null,
              lines: legacy.lines.map((line, index) => ({
                ...line,
                first: index === 0,
                last: index === legacy.lines.length - 1,
                tabs: choices(vocabulary?.tabs ?? [], line.tab),
                modes: choices(vocabulary?.modes ?? [], line.mode),
                statChoices: options([...new Set([...(vocabulary?.stats ?? []), ...line.stats])]),
              })),
            },
    };
  });
  const createScope = libraryScope(state, '$create');
  const draft = state.runDrafts[createScope] ?? {};
  const destination = text(draft['id']) ?? '';
  const baseValue = libraryBase(undefined, draft);
  const base = { plan: queuePlan(baseValue), catalog };
  const queue = selectQueueEditor(base, draft);
  const copying = typeof draft['copy_from'] === 'string';
  const active = Object.hasOwn(draft, 'library_base');
  const conflict = conflictOf(createScope, draft);
  const stale =
    conflict !== null ||
    (text(draft['library_revision']) !== undefined && draft['library_revision'] !== revision);
  const reason: LibraryReason =
    gate ??
    (destination.trim() === '' || source?.policies.some((policy) => policy.id === destination)
      ? 'id'
      : stale
        ? 'revision'
        : !copying && queue.review.plan === null
          ? 'invalid'
          : null);
  const notice = state.notices.find((entry) => entry.kind === 'command_not_sent');
  const notSent: 'context_unconfirmed' | 'context_changed' | 'collection_write_in_flight' | null =
    notice?.reason === 'context_unconfirmed' ||
    notice?.reason === 'context_changed' ||
    notice?.reason === 'collection_write_in_flight'
      ? notice.reason
      : null;
  return {
    notSent,
    contextReady,
    profile: fromServer('name', context?.profile),
    readable,
    gate,
    unavailable: latest !== null && !readable,
    error: fromServer(
      'message',
      latest?.outcome.kind === 'answered' ? own(latest.outcome.body, 'error') : null,
    ),
    policies,
    earlier: [...new Set(records.map((record) => record.target))].flatMap((target) => {
      if (
        !target.startsWith('runs:shopping-library:') ||
        target === createScope ||
        policies.some((policy) => policy.scope === target)
      )
        return [];
      const record = records.findLast((item) => item.target === target);
      const policy = record?.meta.patchPath?.[2];
      const outcome = saveOutcome(state, target);
      return policy === undefined || outcome === null
        ? []
        : [{ policy, name: fromServer('name', policy), outcome }];
    }),
    newValue: newQueue(
      catalog,
      finite(ownPath(body, 'settings', 'routines', 'shopping', 'interval_sec')) ?? null,
    ),
    revision,
    create: {
      scope: createScope,
      draft,
      destination,
      active,
      copying,
      base,
      queue,
      reason,
      outcome: saveOutcome(state, createScope),
      continuation:
        canResolve &&
        (conflict === null || latest.startedSeq > (conflict.answerSeq ?? conflict.sentSeq)) &&
        (stale || unknown.length > 0) &&
        revision !== null
          ? { revision, unknownIds: unknown.map((record) => record.id), after: conflict?.id ?? '' }
          : null,
      save: command(
        createScope,
        destination,
        copying ? baseValue : queue.review.plan,
        false,
        draft,
        reason,
      ),
    },
  };
}

export type ShoppingLibraryVM = ReturnType<typeof selectShoppingLibrary>;
export type ShoppingLibraryPolicyVM = ShoppingLibraryVM['policies'][number];
