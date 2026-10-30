import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * The Runs page's two saves (brief RUN-05 to RUN-08). Each is one record per
 * click with the body frozen at the click; nothing resends a saved-run save
 * (it carries no idempotency key: a later read of the run settles an unknown
 * outcome, store/fences.ts), and a live change is resent only by a person's
 * explicit "Send same request again" with the identical body and key.
 */
export interface SavedSaveInput {
  readonly blueprint: string;
  /** The context the run's read named; the server refuses a save made for another. */
  readonly context: unknown;
  readonly expectedRevision: string;
  readonly changes: Readonly<Record<string, unknown>>;
  /** F6R-02: the draft scope and the raw values typed for the sent paths. */
  readonly draftScope: string;
  readonly draft: Readonly<Record<string, string | boolean>>;
}

export function sendSavedSave(
  machine: IntentMachine,
  input: SavedSaveInput,
  context: PageContext | null,
): SendResult {
  const target = `run:${input.blueprint}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'run_save',
      target,
      endpoint: 'runControlsSave',
      path: '/api/run-controls',
      body: {
        blueprint: input.blueprint,
        context: input.context,
        expected_revision: input.expectedRevision,
        changes: input.changes,
      },
      key: null,
      meta: {
        blueprint: input.blueprint,
        profileRevision: input.expectedRevision,
        changes: input.changes,
        draftScope: input.draftScope,
        draft: input.draft,
      },
      context,
      supersedes: [],
    }),
  );
}

export interface LiveSaveInput {
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
  readonly changes: Readonly<Record<string, unknown>>;
  readonly draftScope: string;
  readonly draft: Readonly<Record<string, string | boolean>>;
  /** The battle's label as the read named it (F6R-04: identifies an earlier battle's line). */
  readonly battleLabel: string | null;
}

export interface LiveResetInput {
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
  readonly paths: readonly string[];
  readonly battleLabel: string | null;
}

/**
 * F6-11 RL1, RL2: remove this battle's overrides of `paths` (each sent as `null`, so the
 * battle's starting value applies again). The same route, target and resend rule as a
 * live save: one live change of a battle at a time, resent only by a person's click.
 */
export function sendLiveReset(
  machine: IntentMachine,
  input: LiveResetInput,
  context: PageContext | null,
  newKey: () => string,
): SendResult {
  const target = `live:${input.battleId}`;
  return sendOnce(machine, target, context, () => {
    const key = newKey();
    return machine.send({
      kind: 'live_controls',
      target,
      endpoint: 'liveControls',
      path: `/api/operations/${encodeURIComponent(input.operationId)}/live-controls`,
      body: {
        battle_id: input.battleId,
        generation: input.generation,
        expected_revision: input.expectedRevision,
        idempotency_key: key,
        changes: Object.fromEntries(input.paths.map((path) => [path, null])),
      },
      key,
      meta: {
        operationId: input.operationId,
        battleId: input.battleId,
        generation: input.generation,
        expectedRevision: input.expectedRevision,
        resetPaths: input.paths,
        ...(input.battleLabel === null ? {} : { battleLabel: input.battleLabel }),
      },
      context,
      supersedes: [],
    });
  });
}

export function sendLiveSave(
  machine: IntentMachine,
  input: LiveSaveInput,
  context: PageContext | null,
  newKey: () => string,
): SendResult {
  const target = `live:${input.battleId}`;
  return sendOnce(machine, target, context, () => {
    const key = newKey();
    return machine.send({
      kind: 'live_controls',
      target,
      endpoint: 'liveControls',
      path: `/api/operations/${encodeURIComponent(input.operationId)}/live-controls`,
      body: {
        battle_id: input.battleId,
        generation: input.generation,
        expected_revision: input.expectedRevision,
        idempotency_key: key,
        changes: input.changes,
      },
      key,
      meta: {
        operationId: input.operationId,
        battleId: input.battleId,
        generation: input.generation,
        expectedRevision: input.expectedRevision,
        changes: input.changes,
        draftScope: input.draftScope,
        draft: input.draft,
        ...(input.battleLabel === null ? {} : { battleLabel: input.battleLabel }),
      },
      context,
      supersedes: [],
    });
  });
}
