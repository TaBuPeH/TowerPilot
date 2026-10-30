import type { ContextRead } from '../../core/connection.ts';
import { own, ownPath } from '../../core/read.ts';
import { readEndpoint } from '../client.ts';
import type { PageContext } from '../context.ts';
import type { Transport } from '../transport.ts';
import { ENDPOINTS, type Endpoint, type EndpointId } from '../wire.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * Start and Collect (POST /api/control start / start_gems; spec 7.4, F-040,
 * F-042, F-043, S-102, S-103). The body is `{action, preset, expected_context,
 * idempotency_key}` and nothing else: no private harvest field (S-103). A
 * Start first reads `GET /api/readiness?preset=` in the click (7.2: never
 * polled) and is sent only when the caller's gate (core/readiness.ts
 * `startGate`) permits that answer in the page context; the expected context
 * is then the readiness answer's own (instance, account, serial, rendering,
 * profile), which the gate checked against the page. A lost, 5xx or
 * `action_lock` answer leaves the record unconfirmed; only a person's resend
 * (same body and key) may repeat it.
 */
export const LAUNCH_TARGET = 'launch';

export interface ExpectedLaunchContext {
  readonly instance: string;
  readonly account: string | null;
  readonly serial: string;
  /** `instances.{instance}.rendering` as the server reports it (an object, or null). */
  readonly rendering: unknown;
  readonly profile: string | null;
}

function launch(
  machine: IntentMachine,
  action: 'start' | 'start_gems',
  preset: string,
  expected: ExpectedLaunchContext,
  context: PageContext,
  newKey: () => string,
): SendResult {
  return sendOnce(machine, LAUNCH_TARGET, context, () => {
    const key = newKey();
    return machine.send({
      kind: action,
      target: LAUNCH_TARGET,
      endpoint: 'control',
      path: '/api/control',
      body: {
        action,
        preset,
        expected_context: {
          instance: expected.instance,
          account: expected.account,
          serial: expected.serial,
          rendering: expected.rendering,
          profile: expected.profile,
        },
        idempotency_key: key,
      },
      key,
      meta: { preset },
      context,
      supersedes: [],
    });
  });
}

/** Collect (start_gems): the caller's gate (core `collectGate`) already permitted it. */
export function sendCollect(
  machine: IntentMachine,
  input: {
    readonly preset: string;
    readonly expected: ExpectedLaunchContext;
    readonly context: PageContext;
  },
  newKey: () => string,
): SendResult {
  return launch(machine, 'start_gems', input.preset, input.expected, input.context, newKey);
}

export interface StartClickDeps {
  readonly machine: IntentMachine;
  readonly transport: Transport;
  readonly newKey: () => string;
  /** The current context epoch: the readiness answer counts only when the epoch did not change. */
  readonly epoch: () => number;
  readonly endpoints?: Readonly<Record<EndpointId, Endpoint>>;
  readonly timeoutMs?: number;
}

export interface StartClickInput {
  readonly preset: string;
  readonly context: PageContext;
  /** core/readiness.ts `startGate` bound to the page's permit, context and run. */
  readonly gate: (readiness: ContextRead) => { readonly enabled: boolean; readonly reason: string };
}

/** One Start click: the readiness read, the gate, then one send (or nothing, with the reason). */
export async function startClick(
  deps: StartClickDeps,
  input: StartClickInput,
): Promise<SendResult> {
  const epoch = deps.epoch();
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, deps.timeoutMs ?? 5000);
  let read: ContextRead;
  try {
    const outcome = await readEndpoint(
      deps.transport,
      (deps.endpoints ?? ENDPOINTS).readiness,
      `/api/readiness?preset=${encodeURIComponent(input.preset)}`,
      controller.signal,
    );
    read =
      outcome.kind === 'answered'
        ? { kind: 'answered', body: outcome.answer.body, sameContext: deps.epoch() === epoch }
        : { kind: 'failed' };
  } finally {
    clearTimeout(timer);
  }
  const gate = input.gate(read);
  if (!gate.enabled || read.kind !== 'answered') {
    return { kind: 'not_sent', reason: gate.enabled ? 'readiness_unconfirmed' : gate.reason };
  }
  const context = own(read.body, 'context');
  const instance = own(context, 'instance');
  const serial = own(context, 'serial');
  const account = own(context, 'account');
  const profile = own(context, 'profile');
  if (
    typeof instance !== 'string' ||
    typeof serial !== 'string' ||
    (account !== null && typeof account !== 'string') ||
    (profile !== null && typeof profile !== 'string')
  ) {
    return { kind: 'not_sent', reason: 'readiness_context_unreadable' };
  }
  return launch(
    deps.machine,
    'start',
    input.preset,
    {
      instance,
      serial,
      account,
      profile,
      rendering: ownPath(read.body, 'context', 'rendering') ?? null,
    },
    input.context,
    deps.newKey,
  );
}
