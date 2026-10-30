import { FIXTURE_PATH, loadFixture } from '../../scripts/contract-fixture.mjs';
import type { ConfigRead } from '../../src/core/config.ts';
import type { StatusRead } from '../../src/core/gate.ts';

/**
 * The contract fixture as the core tests read it: the file contract-fixture.mjs
 * loads (the checkout's r6 fixture, or the file TP_CONTRACT_FIXTURE names, spec
 * 10.4). Samples are returned as fresh deep copies, so a test may mutate one
 * without touching another. Sample paths are dotted, e.g. `status.collecting`
 * or `stop_operation.cancel.stopping`.
 */
const FIXTURE: unknown = JSON.parse(JSON.stringify(loadFixture()));

export const FIXTURE_FILE: string = FIXTURE_PATH;

function lookup(path: string): unknown {
  let current: unknown = FIXTURE;
  for (const key of path.split('.')) {
    if (typeof current !== 'object' || current === null || !Object.hasOwn(current, key)) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

/** True when the loaded fixture has the sample (an `S4:` or `S6:` sample exists in one fixture only). */
export function hasSample(path: string): boolean {
  return lookup(path) !== undefined;
}

/** A deep copy of the sample at `path`; throws naming the path when the fixture lacks it. */
export function sample(path: string): unknown {
  const found = lookup(path);
  if (found === undefined) {
    throw new Error(`The contract fixture ${FIXTURE_PATH} has no sample ${path}`);
  }
  return JSON.parse(JSON.stringify(found)) as unknown;
}

/** A deep copy of a sample that must be a plain object, typed for mutation in tests. */
export function sampleRecord(path: string): Record<string, unknown> {
  const found = sample(path);
  if (typeof found !== 'object' || found === null || Array.isArray(found)) {
    throw new Error(`The sample ${path} is not an object`);
  }
  return found as Record<string, unknown>;
}

/** The sample names of a family, e.g. `status` or `stop_operation.cancel`. */
export function sampleNames(family: string): readonly string[] {
  const found = lookup(family);
  if (typeof found !== 'object' || found === null) {
    throw new Error(`The contract fixture ${FIXTURE_PATH} has no family ${family}`);
  }
  return Object.keys(found).sort();
}

/** D-2 fresh `process_refresh` for a body whose `harvest.server_time` is `serverTime`. */
export function freshScan(serverTime: number, ageSec = 0): Record<string, unknown> {
  return { state: 'fresh', error: null, age_sec: ageSec, last_success_at: serverTime - ageSec };
}

/** The server time of a harvest sample (all fixture samples share one). */
export function serverTimeOf(harvest: unknown): number {
  const value =
    typeof harvest === 'object' && harvest !== null && Object.hasOwn(harvest, 'server_time')
      ? (harvest as Record<string, unknown>)['server_time']
      : undefined;
  return typeof value === 'number' ? value : 1_800_000_000;
}

/** S(harvest, processes, scan) of the safety rules: a `/api/status` body; fresh scan and `[]` by default. */
export function statusBody(
  harvest: unknown,
  options: { readonly processes?: unknown; readonly scan?: unknown } = {},
): Record<string, unknown> {
  return {
    harvest,
    processes: Object.hasOwn(options, 'processes') ? options.processes : [],
    process_refresh: Object.hasOwn(options, 'scan')
      ? options.scan
      : freshScan(serverTimeOf(harvest)),
  };
}

/** An answered status read started at `startedSeq`. */
export function answered(body: unknown, startedSeq = 1): StatusRead {
  return { startedSeq, outcome: { kind: 'answered', body } };
}

/** A failed status read started at `startedSeq`. */
export function failed(reason = 'http_502', startedSeq = 1): StatusRead {
  return { startedSeq, outcome: { kind: 'failed', reason } };
}

/** A config read naming instance `main`, serial `fake:1` and the given `allow_taps` (E0 of spec 9.1). */
export function configRead(allowTaps: unknown, startedSeq = 1): ConfigRead {
  const instance: Record<string, unknown> = { serial: 'fake:1' };
  if (allowTaps !== undefined) {
    instance['allow_taps'] = allowTaps;
  }
  return {
    startedSeq,
    outcome: { kind: 'answered', body: { active_instance: 'main', instances: { main: instance } } },
  };
}
