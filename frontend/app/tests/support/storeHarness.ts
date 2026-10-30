import { initialDashboardState, reduceDashboard } from '../../src/store/dashboardStore.ts';
import type {
  ContextView,
  DashboardState,
  IntentView,
  ReadRecord,
  ResourceId,
} from '../../src/store/types.ts';
import type { ApiPath } from '../../src/api/transport.ts';
import { E0_CONTEXT, e0Config } from './dataLayer.ts';

/** A test path as the poller types it; throws for anything outside /api/. */
export function apiPath(path: string): ApiPath {
  if (!path.startsWith('/api/')) throw new Error(`not an API path: ${path}`);
  return `/api/${path.slice('/api/'.length)}`;
}

/**
 * A store driven action by action (no timers, no transport): reads and write
 * records are placed at explicit request sequence points, so the "later read"
 * of every rule is exact.
 */
export interface StoreHarness {
  readonly state: () => DashboardState;
  /** Take the next sequence point. */
  readonly tick: () => number;
  readonly read: (
    resource: ResourceId,
    body: unknown,
    options?: { readonly path?: string },
  ) => ReadRecord;
  readonly fail: (resource: ResourceId, reason?: string) => ReadRecord;
  /** Add or replace a write record (by id). */
  readonly intent: (record: Partial<IntentView> & Pick<IntentView, 'id' | 'kind'>) => IntentView;
  readonly answer: (
    id: string,
    answer: { readonly status: number; readonly body: unknown },
    phase?: IntentView['phase'],
  ) => void;
  readonly clock: (nowMs: number) => void;
}

const PATHS: Readonly<Record<ResourceId, string>> = {
  status: '/api/status',
  calibrate: '/api/calibrate/status',
  calibrateOperation: '/api/calibrate/status?operation_id=00000001-0000-4000-8000-000000000000',
  battlesActive: '/api/battles/active',
  schedule: '/api/schedule/status?instance=main',
  battles: '/api/battles?limit=3&instance=main',
  config: '/api/config',
  accounts: '/api/accounts',
  runs: '/api/runs',
  wizardTools: '/api/wizard/tools',
  wizardState: '/api/wizard/state',
  wizardEmulators: '/api/wizard/emulators',
  runControls: '/api/run-controls?blueprint=farm',
  profileFiles: '/api/profile-files',
  profileSource: '/api/profile-src/mine',
  targetSource: '/api/profile-src/second',
  settings: '/api/settings',
  vocab: '/api/vocab',
  runTemplates: '/api/run-templates',
};

export function storeHarness(
  /** `config: null` reads no config; any other value is the config body read first. */
  options: { readonly context?: ContextView | null; readonly config?: unknown } = {},
): StoreHarness {
  const context = options.context === undefined ? E0_CONTEXT : options.context;
  let seq = 0;
  let state = initialDashboardState({ context, nowMs: 0 });
  state = reduceDashboard(state, {
    type: 'context',
    context,
    epoch: 0,
    confirmed: context !== null,
    sessionContext: context,
  });
  const tick = () => {
    seq += 1;
    return seq;
  };
  const read = (
    resource: ResourceId,
    outcome: ReadRecord['outcome'],
    path?: string,
  ): ReadRecord => {
    const startedSeq = tick();
    const arrivedSeq = tick();
    const record: ReadRecord = {
      resource,
      path: apiPath(path ?? PATHS[resource]),
      epoch: state.epoch,
      startedSeq,
      arrivedSeq,
      startedAtMs: state.nowMs,
      arrivedAtMs: state.nowMs,
      outcome,
    };
    state = reduceDashboard(state, { type: 'read', read: record });
    return record;
  };
  const harness: StoreHarness = {
    state: () => state,
    tick,
    read: (resource, body, readOptions = {}) =>
      read(
        resource,
        { kind: 'answered', status: 200, body, contract: 'exact', issues: [] },
        readOptions.path,
      ),
    fail: (resource, reason = 'http_502') =>
      read(resource, { kind: 'failed', reason, status: 502 }),
    intent: (partial) => {
      const sentSeq = partial.sentSeq ?? tick();
      const record: IntentView = {
        target:
          partial.kind === 'pause' || partial.kind === 'turn_on'
            ? 'harvest'
            : partial.kind === 'input_off' || partial.kind === 'input_on'
              ? 'input'
              : partial.kind,
        context,
        key: null,
        meta: {},
        firstSentSeq: sentSeq,
        sends: 1,
        phase: 'sending',
        answer: null,
        answerSeq: null,
        answeredAtMs: null,
        lostReason: null,
        readSettlement: null,
        supersededBySeq: null,
        dismissed: false,
        restored: false,
        ...partial,
        sentSeq,
      };
      state = reduceDashboard(state, {
        type: 'intents',
        records: [...state.intents.filter((entry) => entry.id !== record.id), record],
      });
      return record;
    },
    answer: (id, answer, phase = 'confirmed') => {
      const answerSeq = tick();
      state = reduceDashboard(state, {
        type: 'intents',
        records: state.intents.map((entry) =>
          entry.id === id
            ? {
                ...entry,
                answer,
                answerSeq,
                answeredAtMs: state.nowMs,
                phase,
                lostReason: null,
                readSettlement: null,
              }
            : entry,
        ),
      });
    },
    clock: (nowMs) => {
      state = reduceDashboard(state, { type: 'clock', nowMs });
    },
  };
  if (options.config !== null) harness.read('config', options.config ?? e0Config());
  return harness;
}

/** `{status, body}` of a fixture answer sample. */
export function answerOf(sampleValue: Record<string, unknown>): {
  readonly status: number;
  readonly body: unknown;
} {
  return { status: Number(sampleValue['status']), body: sampleValue['body'] };
}
