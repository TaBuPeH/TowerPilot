import { actionControlsBlocked, actionsReading } from '../../core/actions.ts';
import { battleIdentity, freshBattleReport, type BattleIdentity } from '../../core/battle.ts';
import { own, ownPath, stringList, text } from '../../core/read.ts';
import { liveSaveGate, type SaveRequest } from '../../core/runControls.ts';
import { queuePlan, shopCatalog, type QueueDraft } from '../../core/shoppingPlan.ts';
import type { SaveLiveCommand } from '../commands.ts';
import { settledByRead } from '../fences.ts';
import {
  contextIntents,
  contextScopeKey,
  hasConfirmedContext,
  unresolvedIntent,
} from '../intents.ts';
import { contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { queueRefusal, selectQueueEditor } from './shoppingQueue.ts';
import { selectRunsPage } from './runs.ts';

/** SQ7: independent of routine drafts, fenced by battle and generation. */
export function shoppingLiveScope(
  state: Pick<DashboardState, 'context'>,
  shown: BattleIdentity,
): string {
  return `live:${JSON.stringify([contextScopeKey(state.context), shown.id, shown.operationId, shown.generation])}:shopping`;
}

export function selectShoppingLive(state: DashboardState) {
  const read = latestRead(state, 'battlesActive');
  const last = state.reads.battlesActive.lastAnswered;
  const body = last?.outcome.kind === 'answered' ? last.outcome.body : undefined;
  const battle = own(body, 'battle');
  const identity = battleIdentity(battle, last === null ? NaN : last.arrivedAtMs / 1000);
  const shown = identity.kind === 'known' ? identity.value : null;
  const scope = shown === null ? '' : shoppingLiveScope(state, shown);
  const draft: QueueDraft = state.runDrafts[scope] ?? {};
  const base = {
    plan: queuePlan(ownPath(battle, 'effective', 'shopping_plan')),
    catalog: shopCatalog(own(body, 'catalog')),
  };
  const editor = selectQueueEditor(base, draft);
  const advertised =
    base.plan !== null &&
    (stringList(ownPath(battle, 'capabilities', 'controls')) ?? []).includes('shopping_plan');
  // The draft scope names the page context: before the config read confirms it, typing would
  // land in a scope the page then stops showing, so the editor waits for the context.
  const contextKnown = hasConfirmedContext(state);
  const supported = advertised && contextKnown;
  const records = contextIntents(state).filter(
    (r) =>
      r.kind === 'live_controls' &&
      shown !== null &&
      r.meta.battleId === shown.id &&
      r.meta.generation === shown.generation &&
      r.meta.operationId === shown.operationId,
  );
  const latest = records.at(-1);
  const request: SaveRequest = records.some((r) => r.phase === 'sending')
    ? 'sending'
    : records.some((r) => unresolvedIntent(r) && settledByRead(state, r) === null)
      ? 'unresolved'
      : latest?.answerSeq !== null &&
          latest?.answerSeq !== undefined &&
          latest.answerSeq >= (read?.startedSeq ?? 0)
        ? 'refreshing'
        : 'none';
  const review = editor.review;
  const gate = liveSaveGate(
    battle,
    freshBattleReport(contextReadOf(read), shown, read === null ? 0 : readAgeSec(state, read))
      .kind === 'known',
    {
      changes: review.changed && review.plan !== null ? { shopping_plan: review.plan } : {},
      invalid: review.problems > 0 ? { shopping_plan: 'not_a_number' } : {},
    },
    request,
    actionControlsBlocked(actionsReading(body, last === null ? 0 : readAgeSec(state, last))),
  );
  const command: SaveLiveCommand | null =
    supported && gate.enabled
      ? {
          ...gate.command,
          epoch: state.epoch,
          scope,
          draft,
          battleLabel: text(own(battle, 'label')) ?? null,
        }
      : null;
  return {
    scope,
    draft,
    refusal: queueRefusal(state, scope, selectRunsPage(state).live.outcome),
    base,
    editor,
    supported,
    command,
    reason: !advertised
      ? 'unsupported'
      : !contextKnown
        ? 'context_unknown'
        : gate.enabled
          ? null
          : gate.reason,
    discard: Object.keys(draft).length > 0,
    routineEnabled:
      typeof ownPath(battle, 'effective', 'routines', 'shopping', 'enabled') === 'boolean'
        ? ownPath(battle, 'effective', 'routines', 'shopping', 'enabled') === true
        : null,
    routineInterval:
      typeof ownPath(battle, 'effective', 'routines', 'shopping', 'interval_sec') === 'number'
        ? ownPath(battle, 'effective', 'routines', 'shopping', 'interval_sec')
        : null,
  };
}
