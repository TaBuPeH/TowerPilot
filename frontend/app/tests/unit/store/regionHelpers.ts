import { expect } from 'vitest';
import { entries, list, record } from '../../../src/core/read.ts';
import { observeServerText } from '../../../src/copy/server.ts';
import { initialDashboardState, reduceDashboard } from '../../../src/store/dashboardStore.ts';
import type {
  DashboardState,
  IntentView,
  ReadRecord,
  ResourceId,
} from '../../../src/store/types.ts';
import { dashboardCase } from '../../support/dashboardFixture.ts';
import { apiPath } from '../../support/storeHarness.ts';

export function regionState(): DashboardState {
  return initialDashboardState({
    nowMs: 10000,
    context: { instance: 'main', serial: 'fake:1', account: null, profile: 'mine' },
  });
}

export function addBody(
  state: DashboardState,
  resource: ResourceId,
  body: unknown,
  options: { readonly path?: string; readonly atMs?: number; readonly status?: number } = {},
): DashboardState {
  const atMs = options.atMs ?? state.nowMs;
  const read: ReadRecord = {
    resource,
    path: apiPath(options.path ?? `/api/${resource}`),
    epoch: state.epoch,
    startedSeq: atMs,
    arrivedSeq: atMs + 1,
    startedAtMs: atMs - 1,
    arrivedAtMs: atMs,
    outcome: {
      kind: 'answered',
      status: options.status ?? 200,
      body,
      contract: 'exact',
      issues: [],
    },
  };
  return reduceDashboard(state, { type: 'read', read });
}

export function addCase(state: DashboardState, resource: ResourceId, name: string): DashboardState {
  const c = dashboardCase(name);
  return c.status >= 500
    ? addFailure(state, resource)
    : addBody(state, resource, c.body, { path: c.request.path, status: c.status });
}

export function addFailure(
  state: DashboardState,
  resource: ResourceId,
  path = `/api/${resource}`,
): DashboardState {
  return reduceDashboard(state, {
    type: 'read',
    read: {
      resource,
      path: apiPath(path),
      epoch: state.epoch,
      startedSeq: state.nowMs + 2,
      arrivedSeq: state.nowMs + 3,
      startedAtMs: state.nowMs,
      arrivedAtMs: state.nowMs,
      outcome: { kind: 'failed', reason: 'http_502', status: 502 },
    },
  });
}

export function tick(state: DashboardState, elapsedSec: number): DashboardState {
  return reduceDashboard(state, { type: 'clock', nowMs: state.nowMs + elapsedSec * 1000 });
}

export function intent(state: DashboardState, patch: Partial<IntentView> = {}): IntentView {
  return {
    id: 'intent-1',
    kind: 'run_now',
    target: 'battle',
    context: state.context,
    key: 'key-1',
    meta: {},
    firstSentSeq: 1,
    sentSeq: 1,
    sends: 1,
    phase: 'unconfirmed',
    answer: null,
    answerSeq: null,
    answeredAtMs: null,
    lostReason: 'network',
    readSettlement: null,
    supersededBySeq: null,
    dismissed: false,
    restored: false,
    ...patch,
  };
}

/** Every text fromServer made while these tests ran. */
const madeByFromServer = new Set<string>();
observeServerText((text) => {
  madeByFromServer.add(text);
});

/** Reject every original wire object/array, non-finite number and unbranded display string. */
export function expectDetachedVM(vm: unknown, ...wire: readonly unknown[]): void {
  const rawObjects = new Set<object>();
  function remember(value: unknown): void {
    if (typeof value !== 'object' || value === null || rawObjects.has(value)) return;
    rawObjects.add(value);
    for (const child of list(value) ?? entries(value).map(([, child]) => child)) remember(child);
  }
  wire.forEach(remember);
  function visit(value: unknown): void {
    if (value === null || typeof value === 'boolean') return;
    if (typeof value === 'number') {
      expect(Number.isFinite(value)).toBe(true);
      return;
    }
    if (typeof value === 'string') {
      // A code, or server text that fromServer made (ServerText is a branded string).
      if (!/^[A-Za-z0-9_][A-Za-z0-9_.:/?=%-]*$/u.test(value)) {
        expect(madeByFromServer.has(value), `display string not made by fromServer: ${value}`).toBe(
          true,
        );
      }
      return;
    }
    expect(typeof value).toBe('object');
    if (typeof value !== 'object') throw new Error('Invalid VM value');
    expect(rawObjects.has(value)).toBe(false);
    const children = list(value);
    if (children !== undefined) {
      children.forEach(visit);
      return;
    }
    if (record(value) !== undefined) {
      entries(value).forEach(([, child]) => {
        visit(child);
      });
      return;
    }
    // Any other object (a class instance) is not view-model data.
    expect.fail(`a view model holds a non-plain object: ${Object.prototype.toString.call(value)}`);
  }
  visit(vm);
}

export function expectStateVM(vm: unknown, state: DashboardState): void {
  expectDetachedVM(
    vm,
    ...Object.values(state.reads).flatMap((slot) =>
      [slot.latest, slot.lastAnswered].flatMap((read) =>
        read?.outcome.kind === 'answered' ? [read.outcome.body] : [],
      ),
    ),
    ...Object.values(state.runControls).flatMap((read) =>
      read.outcome.kind === 'answered' ? [read.outcome.body] : [],
    ),
  );
}
