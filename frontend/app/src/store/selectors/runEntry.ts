import {
  checkEntry,
  entryNeedsChainLightning,
  entryDraftSnapshot,
  entryRaw,
  inactiveEntryRecorded,
  runEntryFields,
  runEntryRuns,
  type EntryReason,
} from '../../core/runEntry.ts';
import { id, own, ownPath } from '../../core/read.ts';
import { profilePatchAnswer } from '../../core/profilePatch.ts';
import { editTargetScope } from '../../core/editingTarget.ts';
import { fromServer } from '../../copy/server.ts';
import { boundIntents } from '../intents.ts';
import { answeredBody } from '../reads.ts';
import { settledByRead } from '../fences.ts';
import type { DashboardState } from '../types.ts';
import type { ProfilePatchCommand } from './collection.ts';
import { saveOutcome, settingsSavePending, type SaveOutcomeVM } from './settingsModule.ts';
import {
  editorSource,
  editingSourceResource,
  editingTargetVM,
  selectEditingTarget,
} from './editingTarget.ts';

export interface RunEntryCommand extends ProfilePatchCommand {
  readonly profile: string;
  readonly generation: number;
  readonly inactive: boolean;
}

/** RL3's saved entry fields. All parsing and legality belongs to core/runEntry. */
export function selectRunEntry(state: DashboardState) {
  const context = state.context;
  const target = selectEditingTarget(state);
  const sourceRead = editorSource(state);
  const slot = state.reads[editingSourceResource(target)];
  // Reuse the selection owner for history, including inactive-selection generation.
  // History supplies display only; sourceRead remains the sole current evidence.
  const displayRead = sourceRead ?? editorSource(state, 'lastAnswered');
  const source = displayRead?.outcome.kind === 'answered' ? displayRead.outcome.body : undefined;
  const runs = runEntryRuns(source);
  // An explicitly selected missing run stays named; never silently edit a different entry.
  const run = state.runsRun ?? runs[0]?.key ?? null;
  const revision = id(own(source, 'revision')) ?? null;
  const readable =
    sourceRead?.outcome.kind === 'answered' &&
    sourceRead.outcome.status === 200 &&
    own(source, 'name') === target.name &&
    revision !== null;
  const vocab = answeredBody(state, 'vocab');
  const records = boundIntents(state).filter(
    (record) => record.kind === 'profile_patch' && record.meta.name === target.name,
  );
  const unresolved = records.filter(
    (record) =>
      record.phase === 'unconfirmed' &&
      record.accepted !== true &&
      settledByRead(state, record) === null,
  );
  const newest = Math.max(
    0,
    ...records.map((record) => record.answerSeq ?? record.lostSeq ?? record.sentSeq),
  );
  const pending = target.inactive
    ? records.some(
        (record) =>
          record.phase === 'sending' ||
          (record.phase === 'confirmed' &&
            record.target.startsWith('runs:entry:') &&
            settledByRead(state, record) === null),
      ) ||
      (newest > 0 && (sourceRead === null || sourceRead.startedSeq <= newest))
    : settingsSavePending(state, ['profileSource']);
  const fields =
    run === null || context === null || target.name === null || own(source, 'name') !== target.name
      ? []
      : runEntryFields(source, run, answeredBody(state, 'config'), vocab).map((field) => {
          const scope = `runs:entry:${run}:${field.key}:${editTargetScope(context, target.name ?? '')}`;
          const draft = state.runDrafts[scope] ?? {};
          const marker = entryDraftSnapshot(draft['base_revision']);
          const raw = draft['value'] ?? marker?.raw ?? entryRaw(field);
          const remove =
            draft['remove'] === undefined ? (marker?.remove ?? false) : draft['remove'] === true;
          const base = marker?.revision ?? revision;
          const checked = checkEntry(field, raw, remove);
          const outcome = saveOutcome(state, scope);
          const record = records.findLast((item) => item.target === scope);
          // A receipt alone does not report Saved for an inactive run entry. The
          // shared fence owns the named, advanced read proof, including after loss.
          const outcomeVM: SaveOutcomeVM | null =
            outcome !== null &&
            target.inactive &&
            record?.phase === 'confirmed' &&
            record.answer !== null &&
            profilePatchAnswer(record.answer).kind === 'done'
              ? settledByRead(state, record) === 'applied'
                ? { ...outcome, code: 'saved', word: null }
                : { ...outcome, code: 'sending', word: 'waiting' }
              : outcome;
          const stale =
            record?.phase === 'refused' &&
            own(record.answer?.body, 'code') === 'revision_conflict' &&
            draft['continued_after'] !== record.id;
          const blocked: 'unavailable' | 'unknown' | 'pending' | 'revision' | null =
            !readable ||
            !state.contextConfirmed ||
            ownPath(vocab, 'vocab', 'blueprint_fields') === undefined
              ? 'unavailable'
              : unresolved.length > 0
                ? 'unknown'
                : pending
                  ? 'pending'
                  : stale || (base !== revision && Object.keys(draft).length > 0)
                    ? 'revision'
                    : null;
          const reason: typeof blocked | EntryReason | 'activeOnly' =
            target.inactive &&
            !inactiveEntryRecorded(field.key, checked.kind === 'valid' ? checked.remove : remove)
              ? 'activeOnly'
              : (blocked ?? (checked.kind === 'invalid' ? checked.reason : null));
          const command: RunEntryCommand | null =
            reason === null && base !== null && checked.kind === 'valid' && target.name !== null
              ? {
                  epoch: state.epoch,
                  context,
                  profile: target.name,
                  generation: target.generation,
                  inactive: target.inactive,
                  target: scope,
                  revision: base,
                  path: ['blueprints', run, ...field.key.split('.')],
                  remove: checked.remove,
                  value: checked.value,
                  draft: { scope, values: draft },
                }
              : null;
          // A newer read is necessary even for explicit acceptance. An unchanged read leaves the
          // journal Unknown; accepting it merely opens the gate for a new, explicit save.
          const after = Math.max(
            0,
            ...unresolved.map((item) => item.answerSeq ?? item.lostSeq ?? item.sentSeq),
            stale ? (record.answerSeq ?? record.sentSeq) : 0,
          );
          const canContinue =
            readable &&
            sourceRead.startedSeq > after &&
            (reason === 'unknown' || reason === 'revision');
          const continuation = canContinue
            ? {
                revision,
                ids: unresolved.map((item) => item.id),
                after: record?.id ?? '',
              }
            : null;
          const reference = id(field.saved);
          const policyEditor:
            | { readonly kind: 'gather'; readonly id: string }
            | {
                readonly kind: 'ability';
                readonly family: 'uw_policies' | 'rescue_policies';
                readonly policy: string;
              }
            | null =
            reference === undefined
              ? null
              : field.key === 'policies.gather'
                ? { kind: 'gather', id: reference }
                : field.key === 'policies.uw' || field.key === 'policies.rescue'
                  ? {
                      kind: 'ability',
                      family: field.key === 'policies.uw' ? 'uw_policies' : 'rescue_policies',
                      policy: reference,
                    }
                  : null;
          const clearChecked = checkEntry(field, raw, true);
          const clearReason: typeof reason | 'questDefault' =
            target.inactive && !inactiveEntryRecorded(field.key, true)
              ? 'activeOnly'
              : (blocked ??
                (clearChecked.kind === 'invalid'
                  ? field.key === 'rides' && clearChecked.reason === 'unchanged'
                    ? 'questDefault'
                    : clearChecked.reason
                  : null));
          return {
            key: field.key,
            readOnly: sourceRead === null && displayRead !== null,
            needsChainLightning:
              field.key === 'policies.uw' && entryNeedsChainLightning(source, raw),
            clearReason,
            clearCommand:
              (field.key === 'label' || field.key === 'rides') &&
              clearReason === null &&
              base !== null &&
              target.name !== null &&
              clearChecked.kind === 'valid'
                ? {
                    epoch: state.epoch,
                    context,
                    profile: target.name,
                    generation: target.generation,
                    inactive: target.inactive,
                    target: scope,
                    revision: base,
                    path: ['blueprints', run, field.key],
                    remove: true,
                    value: undefined,
                    draft: { scope, values: draft },
                  }
                : null,
            type: field.type,
            scope,
            raw,
            remove,
            revision,
            baseRevision: base,
            dirty: Object.keys(draft).length > 0,
            reason,
            command,
            continuation,
            policyEditor,
            choices: field.choices.map((choice) => ({
              value: choice.value,
              label: fromServer('name', choice.label),
            })),
            unlisted:
              field.type === 'select' &&
              typeof raw === 'string' &&
              raw !== '' &&
              !field.choices.some((choice) => choice.value === raw)
                ? fromServer('name', raw)
                : null,
            none: field.removable && field.type === 'select',
            outcome: outcomeVM === null ? null : { ...outcomeVM, acceptOffered: false },
            profiles:
              record?.phase === 'refused' &&
              record.answer?.status === 409 &&
              own(record.answer.body, 'code') !== 'revision_conflict',
          };
        });
  return {
    target: editingTargetVM(target),
    unlistedRun: runs.some((entry) => entry.key === run) ? null : fromServer('name', run),
    runs: runs.map((entry) => ({ key: entry.key, label: fromServer('name', entry.label) })),
    fields,
    readable,
    readFailure:
      displayRead !== null && sourceRead === null && slot.latest?.outcome.kind === 'failed'
        ? {
            network: slot.latest.outcome.reason === 'network',
          }
        : null,
    run,
    notSent: state.notices.some(
      (notice) =>
        notice.kind === 'command_not_sent' &&
        [
          'context_changed',
          'context_unconfirmed',
          'target_missing',
          'target_unconfirmed',
          'target_changed',
          'collection_write_in_flight',
        ].includes(notice.reason),
    ),
  };
}
