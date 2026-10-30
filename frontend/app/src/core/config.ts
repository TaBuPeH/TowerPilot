import { id, own, ownPath, record } from './read.ts';

/**
 * `/api/config` facts core needs: the active emulator's context (INV-1 Pause
 * body) and its game-input Setting (`instances.{instance}.allow_taps`, spec
 * 3.2). A config read has no server clock, so it is placed by the request
 * sequence point at which it was started.
 */
export interface ConfigRead {
  readonly startedSeq: number;
  readonly outcome:
    | { readonly kind: 'answered'; readonly body: unknown }
    | { readonly kind: 'failed'; readonly reason: string };
}

/** The game-input Setting of the active emulator, or why it can't be read (Game input I1). */
export type TapsSetting =
  | { readonly kind: 'known'; readonly allowTaps: boolean; readonly startedSeq: number }
  | { readonly kind: 'unknown'; readonly reason: string };

/** The context a Pause needs (INV-1, OD-13): instance and serial, both named by the server. */
export interface PauseContext {
  readonly instance: string;
  readonly serial: string;
}

/** The opaque configuration revision, or null when this is not a revisioned config read. */
export function configRevision(body: unknown): string | null {
  return id(own(body, 'config_revision')) ?? null;
}

function activeInstance(body: unknown): { readonly name: string; readonly record: unknown } | null {
  const name = id(own(body, 'active_instance'));
  if (name === undefined) {
    return null;
  }
  const instance = ownPath(body, 'instances', name);
  return record(instance) === undefined ? null : { name, record: instance };
}

/** I1: the Setting, or unknown when there is no read, the read failed, or `allow_taps` is missing or not a boolean. */
export function tapsSetting(read: ConfigRead | null): TapsSetting {
  if (read === null) {
    return { kind: 'unknown', reason: 'config_not_read' };
  }
  if (read.outcome.kind === 'failed') {
    return { kind: 'unknown', reason: 'config_read_failed' };
  }
  const active = activeInstance(read.outcome.body);
  if (active === null) {
    return { kind: 'unknown', reason: 'active_instance_unreadable' };
  }
  const taps = own(active.record, 'allow_taps');
  if (typeof taps !== 'boolean') {
    return { kind: 'unknown', reason: 'allow_taps_invalid' };
  }
  return { kind: 'known', allowTaps: taps, startedSeq: read.startedSeq };
}

/** The active emulator's instance and serial from a config body, or null (OD-13: never invented). */
export function configContext(body: unknown): PauseContext | null {
  const active = activeInstance(body);
  if (active === null) {
    return null;
  }
  const serial = id(own(active.record, 'serial'));
  return serial === undefined ? null : Object.freeze({ instance: active.name, serial });
}

/** The instance and serial a harvest record names in `identity`, or null. */
export function harvestContext(harvest: unknown): PauseContext | null {
  const identity = own(harvest, 'identity');
  const instance = id(own(identity, 'instance'));
  const serial = id(own(identity, 'serial'));
  return instance === undefined || serial === undefined
    ? null
    : Object.freeze({ instance, serial });
}

/** Where a Pause click takes its context from (INV-1, OD-13, amendment A-1 of the safety rules). */
export type PauseContextDecision =
  | {
      readonly kind: 'known';
      readonly context: PauseContext;
      readonly source: 'config' | 'status' | 'session';
    }
  /** No read and no session record names it: the click reads GET /api/config, then GET /api/harvest. */
  | { readonly kind: 'ask_server' };

/**
 * INV-1: the context comes, in order, from the latest `/api/config` read,
 * from `harvest.identity` of the latest readable status, or from the context
 * this browser session recorded; with none known the click asks the server.
 * Never invented, never taken from another emulator.
 */
export function pauseContext(
  latestConfig: ConfigRead | null,
  latestReadableHarvest: unknown,
  session: PauseContext | null,
): PauseContextDecision {
  if (latestConfig !== null && latestConfig.outcome.kind === 'answered') {
    const fromConfig = configContext(latestConfig.outcome.body);
    if (fromConfig !== null) {
      return { kind: 'known', context: fromConfig, source: 'config' };
    }
  }
  const fromStatus = harvestContext(latestReadableHarvest);
  if (fromStatus !== null) {
    return { kind: 'known', context: fromStatus, source: 'status' };
  }
  return session === null
    ? { kind: 'ask_server' }
    : { kind: 'known', context: session, source: 'session' };
}

/**
 * OD-13, rows X2a and X2b of spec 9.1: after the click's own reads, the Pause
 * is sent with the first context one of them names (config first, then
 * `GET /api/harvest`'s `harvest.identity`); null means neither named it, so
 * the click sends nothing and says so, beside an enabled Turn input off.
 * `undefined` stands for a read that did not answer.
 */
export function pauseContextFromClickReads(
  configBody: unknown,
  harvestBody: unknown,
): PauseContext | null {
  return configContext(configBody) ?? harvestContext(own(harvestBody, 'harvest'));
}
