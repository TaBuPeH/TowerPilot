import { startFields, observeFields, type StartPlan } from '../../core/recognition.ts';
import { expectedContext, type PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';
import type { ExpectedLaunchContext } from './start.ts';

/**
 * The Recognition page's writes (screens brief R1 to R8): the calibrator's launches (start,
 * observe), the apply of its discoveries and its Stop. One record per click, the body and
 * idempotency key frozen at the click; nothing is ever resent on its own. A lost launch is
 * settled by a later status read naming an operation with the request's key; a lost apply
 * or stop by the operation's own record (store/fences.ts).
 */

/** One launch at a time in a context: a start and an observe share this target. */
export const RECOGNITION_LAUNCH_TARGET = 'recognition:launch';

function expectedBody(expected: ExpectedLaunchContext) {
  return {
    instance: expected.instance,
    account: expected.account,
    serial: expected.serial,
    rendering: expected.rendering,
    profile: expected.profile,
  };
}

/** POST /api/calibrate/start `{expected_context, idempotency_key, ...plan fields}`. */
export function sendCalibrateStart(
  machine: IntentMachine,
  plan: StartPlan,
  expected: ExpectedLaunchContext,
  context: PageContext,
  newKey: () => string,
): SendResult {
  return sendOnce(machine, RECOGNITION_LAUNCH_TARGET, context, () => {
    const key = newKey();
    return machine.send({
      kind: 'calibrate_start',
      target: RECOGNITION_LAUNCH_TARGET,
      endpoint: 'calibrateStart',
      path: '/api/calibrate/start',
      body: {
        ...startFields(plan),
        expected_context: expectedBody(expected),
        idempotency_key: key,
      },
      key,
      meta: {
        action: plan.kind,
        ...(plan.kind === 'prepare' ? { preset: plan.run } : {}),
      },
      context,
      supersedes: [],
    });
  });
}

/** POST /api/calibrate/observe `{expected_context, idempotency_key, watch?, selected_run?}`: never taps. */
export function sendCalibrateObserve(
  machine: IntentMachine,
  input: { readonly watch: number | null; readonly run: string | null },
  expected: ExpectedLaunchContext,
  context: PageContext,
  newKey: () => string,
): SendResult {
  return sendOnce(machine, RECOGNITION_LAUNCH_TARGET, context, () => {
    const key = newKey();
    return machine.send({
      kind: 'calibrate_observe',
      target: RECOGNITION_LAUNCH_TARGET,
      endpoint: 'calibrateObserve',
      path: '/api/calibrate/observe',
      body: {
        ...observeFields(input.watch, input.run),
        expected_context: expectedBody(expected),
        idempotency_key: key,
      },
      key,
      meta: {
        action: input.watch === null ? 'capture' : 'watch',
        ...(input.run === null ? {} : { preset: input.run }),
      },
      context,
      supersedes: [],
    });
  });
}

export type ApplyTarget =
  | { readonly kind: 'operation'; readonly operationId: string; readonly expectedRevision: number }
  /** Observations saved by no tracked operation (legacy); the server refuses tracked ones. */
  | { readonly kind: 'saved' };

/** Both apply forms carry the four-field fence read for this click (never rendering). */
export function sendCalibrateApply(
  machine: IntentMachine,
  apply: ApplyTarget,
  expected: PageContext,
  context: PageContext,
  evidence?: Readonly<Record<string, unknown>>,
): SendResult {
  const target =
    apply.kind === 'operation'
      ? `recognition:apply:${apply.operationId}`
      : 'recognition:apply:saved';
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'calibrate_apply',
      target,
      endpoint: 'calibrateApply',
      path: '/api/calibrate/apply',
      body: {
        ...(apply.kind === 'operation'
          ? { operation_id: apply.operationId, expected_revision: apply.expectedRevision }
          : { saved_observations: true }),
        expected_context: expectedContext(expected),
      },
      key: null,
      meta:
        apply.kind === 'operation'
          ? { operationId: apply.operationId, expectedRevision: apply.expectedRevision }
          : { action: 'saved', ...(evidence === undefined ? {} : { values: evidence }) },
      context,
      supersedes: [],
    }),
  );
}

/** POST /api/calibrate/stop `{operation_id, expected_revision}` (C6). */
export function sendCalibrateStop(
  machine: IntentMachine,
  stop: { readonly operationId: string; readonly expectedRevision: number },
  context: PageContext,
): SendResult {
  const target = `recognition:stop:${stop.operationId}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'calibrate_stop',
      target,
      endpoint: 'calibrateStop',
      path: '/api/calibrate/stop',
      body: { operation_id: stop.operationId, expected_revision: stop.expectedRevision },
      key: null,
      meta: { operationId: stop.operationId, expectedRevision: stop.expectedRevision },
      context,
      supersedes: [],
    }),
  );
}
