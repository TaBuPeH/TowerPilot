import { pauseContext, type ConfigRead } from '../../core/config.ts';
import { id, own } from '../../core/read.ts';
import { readEndpoint } from '../client.ts';
import { contextFromConfig, type PageContext } from '../context.ts';
import type { Transport } from '../transport.ts';
import { ENDPOINTS, type Endpoint, type EndpointId } from '../wire.ts';
import type { IntentMachine } from './machine.ts';
import { latestOn, sendOnce, type SendResult } from './send.ts';
import type { IntentRecord } from './types.ts';

/**
 * Pause and Turn on (POST /api/harvest; spec 7.4, INV-1, S-001 to S-004, S-015).
 * Both share the target `harvest`, so a Pause replaces a pending Turn on
 * (S-003) and a Turn on replaces an older Pause's line.
 */
export const HARVEST_TARGET = 'harvest';

/** The full page context a harvest record names in `identity` (instance and serial required). */
export function contextFromHarvest(harvest: unknown): PageContext | null {
  const identity = own(harvest, 'identity');
  const instance = id(own(identity, 'instance'));
  const serial = id(own(identity, 'serial'));
  if (instance === undefined || serial === undefined) return null;
  return Object.freeze({
    instance,
    serial,
    account: id(own(identity, 'account')) ?? null,
    profile: id(own(identity, 'profile')) ?? null,
  });
}

/**
 * Where a Pause click takes its context from (INV-1, OD-13, amendment A-1),
 * in core's order: the latest config read, `harvest.identity` of the latest
 * readable status, the context this browser session recorded; null means no
 * read and no record names it, so the click must ask the server.
 */
export function resolvePauseContext(
  latestConfig: ConfigRead | null,
  latestReadableHarvest: unknown,
  sessionRecorded: PageContext | null,
): PageContext | null {
  const decision = pauseContext(latestConfig, latestReadableHarvest, sessionRecorded);
  if (decision.kind === 'ask_server') return null;
  switch (decision.source) {
    case 'config':
      return latestConfig?.outcome.kind === 'answered'
        ? contextFromConfig(latestConfig.outcome.body)
        : null;
    case 'status':
      return contextFromHarvest(latestReadableHarvest);
    case 'session':
      return sessionRecorded;
  }
}

/** S-002: the Pause body depends only on the context: no revision, no preset, no projection. */
export function sendPause(
  machine: IntentMachine,
  context: PageContext,
  newKey: () => string,
): SendResult {
  const current = latestOn(machine, HARVEST_TARGET, context);
  if (current?.kind === 'pause') {
    // INV-1: one request in flight; an unknown Pause is sent again unchanged.
    if (current.phase === 'sending') return { kind: 'in_flight', record: current };
    if (current.phase === 'unconfirmed' && machine.resend(current.id) === null) {
      const resent = machine.snapshot().find((entry) => entry.id === current.id);
      if (resent !== undefined) return { kind: 'resent', record: resent };
    }
  }
  const key = newKey();
  return {
    kind: 'sent',
    record: machine.send({
      kind: 'pause',
      target: HARVEST_TARGET,
      endpoint: 'harvestWrite',
      path: '/api/harvest',
      body: {
        enabled: false,
        expected_context: { instance: context.instance, serial: context.serial },
        idempotency_key: key,
      },
      key,
      meta: {},
      context,
      supersedes: [],
    }),
  };
}

/** One GET of a click (a body, or undefined when it did not answer), with the 5000 ms timeout. */
export function clickGet(
  transport: Transport,
  endpoint: 'config' | 'harvest',
  endpoints: Readonly<Record<EndpointId, Endpoint>> = ENDPOINTS,
  timeoutMs = 5000,
): () => Promise<unknown> {
  return async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
    }, timeoutMs);
    try {
      const outcome = await readEndpoint(
        transport,
        endpoints[endpoint],
        endpoint === 'config' ? '/api/config' : '/api/harvest',
        controller.signal,
      );
      return outcome.kind === 'answered' ? outcome.answer.body : undefined;
    } finally {
      clearTimeout(timer);
    }
  };
}

export interface PauseClickDeps {
  readonly machine: IntentMachine;
  readonly newKey: () => string;
  /** The click's GET /api/config (app/wiring.ts: the poller's single-flight config read). */
  readonly readConfig: () => Promise<unknown>;
  /** The click's GET /api/harvest. */
  readonly readHarvest: () => Promise<unknown>;
  /** The page's context now (after the click's reads), or null when none is known. */
  readonly currentContext: () => PageContext | null;
}

/**
 * One Pause click (INV-1). With a known context it sends (or, per INV-1,
 * sends nothing new or the same request again). With none, the click itself
 * reads `GET /api/config` and then `GET /api/harvest`, and sends as soon as one
 * of them names the instance and serial; when neither does it sends nothing
 * (X2b: "Pause not sent: the server didn't name the active emulator"). A
 * context is never invented and never taken from another emulator: when the
 * page meanwhile knows a context naming another emulator than the click's
 * read, nothing is sent (S-014).
 */
export async function pauseClick(
  deps: PauseClickDeps,
  known: PageContext | null,
): Promise<SendResult> {
  if (known !== null) return sendPause(deps.machine, known, deps.newKey);
  const named = (candidate: PageContext): SendResult => {
    const current = deps.currentContext();
    if (current === null) return sendPause(deps.machine, candidate, deps.newKey);
    if (current.instance !== candidate.instance || current.serial !== candidate.serial) {
      return { kind: 'not_sent', reason: 'context_changed' };
    }
    return sendPause(deps.machine, current, deps.newKey);
  };
  const config = contextFromConfig(await deps.readConfig());
  if (config !== null) return named(config);
  const fromHarvest = contextFromHarvest(own(await deps.readHarvest(), 'harvest'));
  if (fromHarvest !== null) return named(fromHarvest);
  return { kind: 'not_sent', reason: 'context_not_named' };
}

export interface TurnOnInput {
  /** The validated source's preset (`harvest.source.preset`, S-104). */
  readonly preset: string;
  /** The revision of the read the person saw (S-015, D-1). */
  readonly expectedRevision: number;
  readonly context: PageContext;
}

/** S-015: Turn on is a compare-and-set with all four context fields and a key frozen at the click. */
export function sendTurnOn(
  machine: IntentMachine,
  input: TurnOnInput,
  newKey: () => string,
): SendResult {
  return sendOnce(machine, HARVEST_TARGET, input.context, (): IntentRecord => {
    const key = newKey();
    return machine.send({
      kind: 'turn_on',
      target: HARVEST_TARGET,
      endpoint: 'harvestWrite',
      path: '/api/harvest',
      body: {
        enabled: true,
        preset: input.preset,
        expected_revision: input.expectedRevision,
        expected_context: {
          instance: input.context.instance,
          account: input.context.account,
          serial: input.context.serial,
          profile: input.context.profile,
        },
        idempotency_key: key,
      },
      key,
      meta: { preset: input.preset, expectedRevision: input.expectedRevision },
      context: input.context,
      supersedes: [],
    });
  });
}
