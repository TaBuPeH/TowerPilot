import {
  runAddLoadouts,
  runAddOptions,
  runAddRefusal,
  runAddSource,
  runIdCheck,
  runRecipes,
} from '../../core/runAdd.ts';
import { id, own, text } from '../../core/read.ts';
import { fromServer } from '../../copy/server.ts';
import type { Fenced } from '../commands.ts';
import type { ContextView, DashboardState } from '../types.ts';
import { boundIntents, contextScopeKey } from '../intents.ts';
import { answeredBody } from '../reads.ts';
import type { DraftValues } from '../../core/runControls.ts';
import { settledByRead } from '../fences.ts';

export interface AddRunCommand extends Fenced {
  readonly context: ContextView;
  readonly runId: string;
  readonly revision: string;
  readonly template: string;
  readonly kind: string;
  readonly options: Readonly<Record<string, unknown>>;
  readonly scope: string;
  readonly draft: DraftValues;
}

export function selectRunsAdd(state: DashboardState) {
  const scope = `runs:add:${contextScopeKey(state.context)}`;
  const draft = state.runDrafts[scope] ?? {};
  const recipes = runRecipes(answeredBody(state, 'runTemplates'));
  const selected =
    typeof draft['template'] === 'string' ? draft['template'] : (recipes[0]?.id ?? '');
  const recipe = recipes.find((item) => item.id === selected);
  const source = state.reads.profileSource.latest;
  const profile = state.context?.profile;
  const sourceBody =
    source?.outcome.kind === 'answered' && source.outcome.status === 200
      ? source.outcome.body
      : undefined;
  const runId = typeof draft['run_id'] === 'string' ? draft['run_id'] : '';
  const records = boundIntents(state).filter((item) => item.kind === 'run_add');
  const record = records.at(-1) ?? null;
  const earlierUnknown = records.filter(
    (item) =>
      item.id !== record?.id && item.phase === 'unconfirmed' && settledByRead(state, item) === null,
  );

  const phase =
    record !== null && settledByRead(state, record) !== null
      ? 'confirmed'
      : (record?.phase ?? null);
  const pending = phase === 'sending' || (phase === 'unconfirmed' && record?.accepted !== true);
  const options = recipe === undefined ? null : runAddOptions(recipe, draft);
  const receipt = records
    .filter(
      (item) => item.phase === 'confirmed' && id(own(item.answer?.body, 'revision')) !== undefined,
    )
    .at(-1);
  const useReceipt = receipt !== undefined && (receipt.answerSeq ?? 0) > (source?.startedSeq ?? 0);
  const baseRevision = id(own(useReceipt ? receipt.answer?.body : sourceBody, 'revision')) ?? null;
  const revision =
    typeof draft['base_revision'] === 'string' ? draft['base_revision'] : baseRevision;
  const idSource = useReceipt ? receipt.answer?.body : sourceBody;
  const reason:
    'unavailable' | 'pending' | 'enter_id' | 'bad_id' | 'id_taken' | 'invalid_options' | null =
    !state.contextConfirmed ||
    profile === null ||
    profile === undefined ||
    recipe === undefined ||
    revision === null ||
    !runAddSource(sourceBody, profile) ||
    state.reads.runTemplates.latest?.outcome.kind !== 'answered' ||
    state.reads.config.latest?.outcome.kind !== 'answered'
      ? 'unavailable'
      : pending
        ? 'pending'
        : (runIdCheck(runId, idSource) ?? (options === null ? 'invalid_options' : null));
  const command: AddRunCommand | null =
    reason === null &&
    state.context !== null &&
    recipe !== undefined &&
    options !== null &&
    revision !== null
      ? {
          epoch: state.epoch,
          context: state.context,
          revision,
          runId,
          template: recipe.id,
          kind: recipe.kind,
          options,
          scope,
          draft,
        }
      : null;
  return {
    scope,
    draft,
    runId,
    selected,
    baseRevision,
    fields: recipe?.fields ?? [],
    reason,
    command,
    recipes: recipes.map((item) => ({ id: item.id, label: fromServer('name', item.label) })),
    loadouts: runAddLoadouts(answeredBody(state, 'config')).map((name) => ({
      id: name,
      label: fromServer('name', name),
    })),
    asIs: recipe?.kind === 'coin' && !recipe.fields.includes('dissonant_tab'),
    earlierUnknown: earlierUnknown.map((item) => ({
      id: item.id,
      run: fromServer('id', item.meta.blueprint ?? null),
    })),
    outcome:
      record === null || record.dismissed
        ? null
        : {
            id: record.id,
            phase: phase ?? record.phase,
            refusal: record.answer === null ? null : runAddRefusal(record.answer),
            accepted: record.accepted === true,
            notAddedAgain:
              own(record.answer?.body, 'code') === 'revision_conflict' &&
              earlierUnknown.some((item) => item.meta.blueprint === record.meta.blueprint),
            error: fromServer(
              'message',
              text(own(record.answer?.body, 'error')) ??
                text(own(record.answer?.body, 'text')) ??
                null,
            ),
            accept: phase === 'unconfirmed' && record.accepted !== true,
            profiles:
              record.answer?.status === 409 &&
              own(record.answer.body, 'code') !== 'revision_conflict' &&
              own(record.answer.body, 'code') !== 'context_changed',
            currentContext:
              own(record.answer?.body, 'code') === 'context_changed'
                ? fromServer('detail', JSON.stringify(own(record.answer?.body, 'current_context')))
                : null,
          },
  };
}
