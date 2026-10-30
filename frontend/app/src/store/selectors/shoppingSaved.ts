import { blueprintOf } from '../../core/gemSource.ts';
import { contextsEqual } from '../../core/readiness.ts';
import { finite, id, own, ownPath, stringList } from '../../core/read.ts';
import {
  legacyConversion,
  newQueue,
  queuePlan,
  reviewQueue,
  shopCatalog,
  stagePlan,
  type QueueDraft,
} from '../../core/shoppingPlan.ts';
import { fromServer } from '../../copy/server.ts';
import type { SaveRunCommand } from '../commands.ts';
import { hasConfirmedContext } from '../intents.ts';
import { contextReadOf } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { commandVM } from '../viewModels.ts';
import { runControlsPath } from './gemSlot.ts';
import { savedScope, selectRunsPage } from './runs.ts';
import { queueRefusal, selectQueueEditor } from './shoppingQueue.ts';

const SCOPE_SUFFIX = ':shopping_plan';
/** A saved run's queue draft scope (its run's saved scope with this suffix). */
export const isSavedShoppingScope = (scope: string): boolean => scope.endsWith(SCOPE_SUFFIX);

/** SQ11: queue drafts share persistence with run drafts but never the routine editor's scope. */
export function selectShoppingSaved(state: DashboardState) {
  const contextReady = hasConfirmedContext(state);
  const saved = selectRunsPage(state).saved;
  const run = saved.run;
  const scope = run === null ? null : `${savedScope(state, run)}${SCOPE_SUFFIX}`;
  const draft: QueueDraft = scope === null ? {} : (state.runDrafts[scope] ?? {});
  const read = run === null ? undefined : state.runControls[runControlsPath(run)];
  const latestSucceeded = read?.outcome.kind === 'answered' && read.outcome.status === 200;
  const displayRead = latestSucceeded
    ? read
    : run === null
      ? undefined
      : state.runControlsLastAnswered[runControlsPath(run)];
  const context = displayRead === undefined ? null : contextReadOf(displayRead);
  const body = context?.kind === 'answered' ? context.body : undefined;
  const displayAvailable =
    context?.kind === 'answered' &&
    context.sameContext &&
    run !== null &&
    own(body, 'blueprint') === blueprintOf(run) &&
    contextsEqual(own(body, 'context'), state.context, ['instance', 'account', 'profile']);
  const available = displayAvailable && latestSucceeded && saved.read === 'ready';
  const supported =
    stringList(ownPath(body, 'capabilities', 'controls'))?.includes('shopping_plan') === true;
  const catalog = shopCatalog(own(body, 'catalog'));
  const base = { plan: queuePlan(ownPath(body, 'settings', 'shopping_plan')), catalog };
  const conversion = legacyConversion(own(body, 'legacy_conversion'));
  const reviewed = selectQueueEditor(base, draft);
  const currentInterval = finite(ownPath(body, 'settings', 'routines', 'shopping', 'interval_sec'));
  // Interval has one editor: the routine control. A staged creation/conversion may predate
  // a later routine save, so the full queue replacement carries that latest read's interval.
  const editor =
    reviewed.review.plan === null || currentInterval === undefined
      ? reviewed
      : {
          ...reviewed,
          review: {
            ...reviewed.review,
            plan: { ...reviewed.review.plan, interval_sec: currentInterval },
          },
        };
  const converted =
    conversion === null ? null : selectQueueEditor({ plan: conversion.plan, catalog }, {});
  const conversionReserve = reviewQueue(
    { plan: null, catalog },
    {
      ...(conversion === null ? {} : stagePlan(conversion.plan.raw)),
      reserve: typeof draft['reserve'] === 'string' ? draft['reserve'] : '',
    },
  ).reserve;
  const staged = typeof draft['base'] === 'string';
  const showEditor = base.plan !== null || staged;
  const upstream = saved.editor?.save;
  // The existing saved-run gate owns context, revision and pending/Unknown fences. Routine
  // validation and routine dirtiness do not decide this separate queue save.
  const fenceReason = upstream?.reason;
  const blocked =
    fenceReason !== null &&
    fenceReason !== undefined &&
    fenceReason !== 'invalid_value' &&
    fenceReason !== 'no_changes';
  const blueprint = id(own(body, 'blueprint'));
  const revision = id(own(body, 'profile_revision'));
  const reason = !contextReady
    ? 'context_unknown'
    : !available || !supported || blueprint === undefined || revision === undefined
      ? 'run_not_read'
      : currentInterval === undefined
        ? 'shopping_interval_unknown'
        : blocked
          ? fenceReason
          : editor.review.plan === null
            ? 'invalid_value'
            : !editor.review.changed
              ? 'no_changes'
              : null;
  const plan = editor.review.plan;
  const command: SaveRunCommand | null =
    reason !== null ||
    plan === null ||
    scope === null ||
    blueprint === undefined ||
    revision === undefined
      ? null
      : {
          epoch: state.epoch,
          scope,
          blueprint,
          context: own(body, 'context'),
          expectedRevision: revision,
          changes: { shopping_plan: plan },
          draft,
        };
  return {
    scope,
    contextReady,
    scopeReady: saved.scopeReady,
    catalogueUnavailable: saved.catalogueUnavailable,
    draft,
    refusal: queueRefusal(state, scope, saved.outcome),
    base,
    editor,
    available,
    displayAvailable,
    supported,
    showEditor,
    discard: Object.keys(draft).length > 0,
    save: commandVM(true, command !== null, reason, upstream?.sending ?? false, command),
    create: stagePlan(
      newQueue(
        catalog,
        finite(ownPath(body, 'settings', 'routines', 'shopping', 'interval_sec')) ?? null,
      ),
    ),
    conversion:
      conversion === null
        ? null
        : {
            // F6-12-C-02: the reviewed queue is staged only with the person's explicit cash
            // reserve (0 included), typed in the review and kept in this run's draft.
            draft: { ...stagePlan(conversion.plan.raw), reserve: conversionReserve.raw },
            reserveField: conversionReserve,
            warnings: conversion.warnings.map((warning) => fromServer('message', warning)),
            enabled: own(conversion.plan.raw, 'enabled') !== false,
            reserve: finite(own(conversion.plan.raw, 'cash_reserve')) ?? null,
            interval: finite(own(conversion.plan.raw, 'interval_sec')) ?? null,
            mode:
              converted?.mode.choices.find((choice) => choice.value === converted.mode.value) ??
              null,
            cadence:
              converted?.cadence.choices.find(
                (choice) => choice.value === converted.cadence.value,
              ) ?? null,
            entries: conversion.plan.queue.map((entry) => ({
              id: id(own(entry, 'id')) ?? '',
              label: fromServer(
                'name',
                catalog.find((item) => item.id === own(entry, 'id'))?.label ?? own(entry, 'id'),
              ),
              enabled: own(entry, 'enabled') !== false,
              cap: finite(own(entry, 'tap_limit')) ?? null,
              weight: finite(own(entry, 'weight')) ?? null,
              from: finite(own(entry, 'from_wave')) ?? null,
              until: finite(own(entry, 'until_wave')) ?? null,
            })),
            excluded: (stringList(own(conversion.plan.raw, 'excluded_upgrades')) ?? []).map(
              (upgrade) => ({
                id: upgrade,
                label: fromServer(
                  'name',
                  catalog.find((item) => item.id === upgrade)?.label ?? upgrade,
                ),
              }),
            ),
          },
  };
}
