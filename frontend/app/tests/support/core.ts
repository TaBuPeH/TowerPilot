import { assessStatus, gateOf, type GateRead, type ReadAssessment } from '../../src/core/gate.ts';
import type { KnownLeaseHolder } from '../../src/core/lease.ts';
import type { StatusContext } from '../../src/core/statusContext.ts';
import type { TurnOnFacts } from '../../src/core/harvestView.ts';
import { answered, configRead } from './fixture.ts';

/** F-027: the prerequisites outside the status read, all in place (taps allowed, connected, no request). */
export const TURN_ON_READY: TurnOnFacts = {
  config: configRead(true),
  connection: 'connected',
  harvestRequest: 'none',
};

/** Shorthands the core tests share: a status body assessed, as a slot context, or through D-4. */

/** D-1, D-2 and D-3 of `body`, read as started at `startedSeq`. */
export function readOf(body: unknown, startedSeq = 2): ReadAssessment {
  return assessStatus(answered(body, startedSeq));
}

/** A cold page whose newest read is `body`. */
export function contextOf(
  body: unknown,
  options: { readonly leaseHolders?: readonly KnownLeaseHolder[] } = {},
): StatusContext {
  return { newest: readOf(body), lastGood: null, leaseHolders: options.leaseHolders ?? [] };
}

/** D-4 of `body`, failing the test with the reason when it does not pass. */
export function gateReadOf(body: unknown, after: number | null = null, startedSeq = 2): GateRead {
  const gate = gateOf(readOf(body, startedSeq), after);
  if (gate.kind === 'unknown') {
    throw new Error(`expected the read to pass D-4, it failed: ${gate.reason}`);
  }
  return gate.value;
}

/** Set (or with `undefined`, delete) the own property at `path` of a JSON value, in place. */
export function setPath(target: unknown, path: readonly (string | number)[], value: unknown): void {
  let current: unknown = target;
  for (const key of path.slice(0, -1)) {
    current = (current as Record<string | number, unknown>)[key];
  }
  const last = path[path.length - 1];
  if (last === undefined || typeof current !== 'object' || current === null) {
    throw new Error(`no container at ${path.join('.')}`);
  }
  if (value === undefined) {
    if (Array.isArray(current) && typeof last === 'number') {
      current.splice(last, 1);
    } else {
      Reflect.deleteProperty(current, last);
    }
    return;
  }
  (current as Record<string | number, unknown>)[last] = value;
}

/** `value` as a mutable record (tests build and mutate fixture copies). */
export function rec(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('not a record');
  }
  return value as Record<string, unknown>;
}
