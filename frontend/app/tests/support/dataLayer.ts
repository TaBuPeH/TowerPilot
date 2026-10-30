import { vi } from 'vitest';
import { createDashboardData } from '../../src/app/wiring.ts';
import type { PageContext } from '../../src/api/context.ts';
import { createIntentMachine } from '../../src/api/intents/machine.ts';
import { createSequence } from '../../src/api/sequence.ts';
import type { KeyValueStorage } from '../../src/api/storage.ts';
import { dashboardBody } from './dashboardFixture.ts';
import {
  counter,
  createFakeTransport,
  createFakeVisibility,
  createMemoryStorage,
  type FakeTransport,
} from './fakeTransport.ts';
import { sample, statusBody } from './fixture.ts';

/** E0 of spec 9.1: instance main, serial fake:1, allow_taps false. */
export const E0_CONTEXT: PageContext = Object.freeze({
  instance: 'main',
  serial: 'fake:1',
  account: null,
  profile: 'mine',
});

export function e0Config(
  allowTaps: unknown = false,
  instance = 'main',
  serial = 'fake:1',
): unknown {
  const entry: Record<string, unknown> = { serial };
  if (allowTaps !== undefined) entry['allow_taps'] = allowTaps;
  return { active_instance: instance, active_profile: 'mine', instances: { [instance]: entry } };
}

/** A machine on a fake transport, in E0's context. */
export function machineHarness(
  options: {
    readonly storage?: ReturnType<typeof createMemoryStorage>;
    readonly context?: PageContext | null;
  } = {},
) {
  const fake = createFakeTransport();
  const storage = options.storage ?? createMemoryStorage();
  const sequence = createSequence();
  const machine = createIntentMachine({
    transport: fake.transport,
    sequence,
    storage,
    newId: counter('rec'),
    context: options.context === undefined ? E0_CONTEXT : options.context,
    epoch: 0,
  });
  return { fake, storage, sequence, machine };
}

export interface BootOptions {
  readonly status?: () => unknown;
  readonly config?: () => unknown;
  readonly storage?: KeyValueStorage & { readonly entries?: () => Record<string, string> };
  readonly hidden?: boolean;
  /** Leave the visibility option out, so the wiring's production default (the real document) is under test. */
  readonly documentVisibility?: boolean;
  readonly fake?: FakeTransport;
  /** Routes added before start (e.g. holding /api/status). */
  readonly before?: (fake: FakeTransport) => void;
  /** Idempotency keys (default `key-0001`, ...): a fixture's own key replays its recorded scenario. */
  readonly newKey?: () => string;
}

/** The wired data layer (app/wiring.ts) on a fake transport; call after vi.useFakeTimers(). */
export function bootDashboard(options: BootOptions = {}) {
  const fake = options.fake ?? createFakeTransport();
  const storage = options.storage ?? createMemoryStorage();
  fake.route('GET', '/api/config', () => ({ status: 200, body: options.config?.() ?? e0Config() }));
  fake.route('GET', '/api/status', () => ({
    status: 200,
    body: options.status?.() ?? statusBody(sample('status.off')),
  }));
  fake.route('GET', '/api/accounts', () => ({
    status: 200,
    body: dashboardBody('accounts.none_active'),
  }));
  fake.route('GET', '/api/runs', () => ({ status: 200, body: dashboardBody('runs.ready') }));
  fake.route('GET', '/api/calibrate/status', () => ({
    status: 200,
    body: dashboardBody('calibrate_status.idle'),
  }));
  fake.route('GET', '/api/battles/active', () => ({
    status: 200,
    body: dashboardBody('battles_active.null_no_driver'),
  }));
  fake.route('GET', /^\/api\/schedule\/status/, () => ({
    status: 200,
    body: dashboardBody('schedule_status.scheduler_not_observed'),
  }));
  fake.route('GET', /^\/api\/battles\?/, () => ({
    status: 200,
    body: dashboardBody('battles_history.empty'),
  }));
  fake.route('GET', '/api/wizard/tools', () => ({
    status: 200,
    body: dashboardBody('wizard_tools.installed'),
  }));
  fake.route('GET', '/api/wizard/state', () => ({
    status: 200,
    body: dashboardBody('wizard_state.empty'),
  }));
  fake.route('GET', '/api/wizard/emulators', () => ({
    status: 200,
    body: dashboardBody('wizard_emulators.instances_with_names_and_ports'),
  }));
  fake.route('GET', /^\/api\/run-controls/, () => ({
    status: 200,
    body: dashboardBody('run_controls.ad_gems_on_floating_off'),
  }));
  options.before?.(fake);
  const visibility = createFakeVisibility(options.hidden ?? false);
  const data = createDashboardData({
    transport: fake.transport,
    storage,
    ...(options.documentVisibility === true ? {} : { visibility }),
    newId: counter('rec'),
    newKey: options.newKey ?? counter('key'),
  });
  data.start();
  return { fake, data, visibility, storage };
}

/** The context epoch a command drawn from the current view model carries (S-014). */
export function fence(data: {
  readonly store: { readonly getSnapshot: () => { readonly epoch: number } };
}): {
  readonly epoch: number;
} {
  return { epoch: data.store.getSnapshot().epoch };
}

/** Let pending promise callbacks run (no timer advances). */
export async function flush(): Promise<void> {
  await vi.advanceTimersByTimeAsync(0);
}

/** The POST bodies of a fake transport, parsed. */
export function postBodies(fake: FakeTransport, path?: string): unknown[] {
  return fake
    .writes()
    .filter((entry) => path === undefined || entry.path === path)
    .map((entry) => JSON.parse(entry.body ?? 'null') as unknown);
}
