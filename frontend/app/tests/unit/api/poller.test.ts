import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  backoffMs,
  createPoller,
  POLICIES,
  POLLED_RESOURCES,
  type PolledResource,
  type ReadArrival,
  type ReadPlan,
} from '../../../src/api/poller.ts';
import { createSequence } from '../../../src/api/sequence.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { bootDashboard, flush, fence } from '../../support/dataLayer.ts';
import { createFakeTransport, createFakeVisibility } from '../../support/fakeTransport.ts';
import { sample, statusBody } from '../../support/fixture.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

/**
 * The poller of spec 7.2 (F-108, P-01 to P-06): single flight per resource,
 * cadence per mode, hidden-tab backoff, failure backoff, the 5000 ms GET
 * timeout, forced reads, and the context fence; and S-012: status reads are
 * accepted while writes are in flight.
 */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
it('T1 T2 F6T-R1-04 failed settings reads retain the recorded HTTP body for a visible stale-read reason', async () => {
  const { fake, poller, arrivals } = harness({ settings: { path: '/api/settings', mode: 'idle' } });
  const recorded = dashboardPagesCase('settings.profile_unreadable_yaml_400');
  fake.route('GET', '/api/settings', () => ({ status: recorded.status, body: recorded.body }));
  poller.start();
  await flush();
  expect(arrivals.find((arrival) => arrival.resource === 'settings')?.outcome).toMatchObject({
    kind: 'failed',
    reason: 'http_400',
    status: 400,
    body: recorded.body,
  });
  poller.stop();
});
afterEach(() => {
  vi.useRealTimers();
});

function harness(plans: Partial<Record<PolledResource, ReadPlan>>, hidden = false) {
  const fake = createFakeTransport();
  const visibility = createFakeVisibility(hidden);
  const arrivals: ReadArrival[] = [];
  let epoch = 0;
  const clock: number[] = [];
  const poller = createPoller({
    transport: fake.transport,
    sequence: createSequence(),
    epoch: () => epoch,
    plan: (resource) => plans[resource] ?? { path: null, mode: 'off' },
    deliver: (arrival) => {
      arrivals.push(arrival);
    },
    visibility,
    onClock: () => {
      clock.push(Date.now());
    },
  });
  return {
    fake,
    visibility,
    arrivals,
    clock,
    poller,
    setEpoch: (value: number) => {
      epoch = value;
    },
  };
}

const statusOk = { status: 200, body: statusBody(sample('status.off')) };
const reads = (h: ReturnType<typeof harness>, path = '/api/status') =>
  h.fake.requests
    .filter((entry) => entry.path === path)
    .map((entry) => entry.at - 1_800_000_000_000);

describe('cadence and single flight (spec 7.2)', () => {
  it('reads status every 2500 ms idle and every 500 ms active, measured from the end of the read', async () => {
    const plan: ReadPlan = { path: '/api/status', mode: 'idle' };
    const h = harness({ status: plan });
    h.fake.route('GET', '/api/status', () => statusOk);
    h.poller.start();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(reads(h)).toEqual([0, 2500, 5000, 7500, 10000]);
    const active = harness({ status: { path: '/api/status', mode: 'active' } });
    active.fake.route('GET', '/api/status', () => statusOk);
    active.poller.start();
    await vi.advanceTimersByTimeAsync(2000);
    expect(reads(active).length).toBe(5);
    h.poller.stop();
    active.poller.stop();
  });

  it('never starts a second read of a resource while one is in flight; a forced read queues once behind it', async () => {
    const h = harness({ status: { path: '/api/status', mode: 'active' } });
    h.poller.start();
    await flush();
    h.poller.read('status');
    h.poller.read('status');
    await vi.advanceTimersByTimeAsync(4000);
    expect(reads(h)).toEqual([0]);
    h.fake.next('/api/status').respond(200, statusOk.body);
    await flush();
    expect(reads(h)).toEqual([0, 4000]);
    expect(h.arrivals).toHaveLength(1);
    h.poller.stop();
  });

  it('times a GET out after 5000 ms and backs off min(30000, 500 * 2^min(6, n)), first retry 1000 ms', async () => {
    const h = harness({ status: { path: '/api/status', mode: 'active' } });
    h.fake.route('GET', '/api/status', () => 'hold');
    h.poller.start();
    await vi.advanceTimersByTimeAsync(5000);
    expect(h.arrivals.map((arrival) => arrival.outcome)).toEqual([
      { kind: 'failed', reason: 'timeout', status: null },
    ]);
    expect([1, 2, 3, 4, 5, 6, 7].map((n) => backoffMs('status', n))).toEqual([
      1000, 2000, 4000, 8000, 16000, 30000, 30000,
    ]);
    expect([1, 2, 6, 9].map((n) => backoffMs('schedule', n))).toEqual([2000, 4000, 60000, 60000]);
    expect(backoffMs('none', 3)).toBeNull();
    h.fake.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
    await vi.advanceTimersByTimeAsync(1000);
    expect(reads(h)).toEqual([0, 6000]);
    await vi.advanceTimersByTimeAsync(2000);
    expect(reads(h)).toEqual([0, 6000, 8000]);
    await vi.advanceTimersByTimeAsync(4000);
    expect(reads(h)).toEqual([0, 6000, 8000, 12000]);
    h.fake.route('GET', '/api/status', () => statusOk);
    await vi.advanceTimersByTimeAsync(8000);
    await vi.advanceTimersByTimeAsync(500);
    expect(reads(h).slice(-2)).toEqual([20000, 20500]);
    h.poller.stop();
  });

  it('reads trigger-only resources on entry and on demand, and never retries a workspace read', async () => {
    const h = harness({ config: { path: '/api/config', mode: 'off' } });
    h.fake.route('GET', '/api/config', () => ({ status: 500, body: 'boom' }));
    h.poller.start();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(reads(h, '/api/config')).toEqual([0]);
    h.poller.read('config');
    await flush();
    expect(reads(h, '/api/config')).toEqual([0, 120000]);
    h.poller.stop();
  });

  it('lists every polled resource with a policy', () => {
    expect(Object.keys(POLICIES).sort()).toEqual([...POLLED_RESOURCES].sort());
  });
});

describe('hidden tab (spec 7.2, F-108)', () => {
  it('slows status and calibration to 10 s, stops battle reads and the clock, and forces one read when visible again', async () => {
    const h = harness({
      status: { path: '/api/status', mode: 'idle' },
      calibrate: { path: '/api/calibrate/status', mode: 'idle' },
      battlesActive: { path: '/api/battles/active', mode: 'active' },
    });
    for (const path of ['/api/status', '/api/calibrate/status', '/api/battles/active']) {
      h.fake.route('GET', path, () => ({ status: 200, body: {} }));
    }
    h.poller.start();
    await vi.advanceTimersByTimeAsync(3000);
    h.visibility.set(true);
    const hiddenAt = 3000;
    const clockBefore = h.clock.length;
    await vi.advanceTimersByTimeAsync(30_000);
    const hiddenStatus = reads(h).filter((at) => at > hiddenAt);
    expect(hiddenStatus).toEqual([12500, 22500, 32500]);
    expect(reads(h, '/api/calibrate/status').filter((at) => at > hiddenAt)).toEqual([
      12500, 22500, 32500,
    ]);
    expect(reads(h, '/api/battles/active').filter((at) => at > hiddenAt)).toEqual([]);
    expect(h.clock.length).toBe(clockBefore);
    h.visibility.set(false);
    await flush();
    expect(reads(h).at(-1)).toBe(33000);
    expect(reads(h, '/api/battles/active').at(-1)).toBe(33000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.clock.length).toBe(clockBefore + 1);
    h.poller.stop();
  });

  it('keeps the failure backoff while hidden, never faster than 10 s', async () => {
    const h = harness({ status: { path: '/api/status', mode: 'active' } }, true);
    h.fake.route('GET', '/api/status', () => ({ status: 503, body: 'down' }));
    h.poller.start();
    await vi.advanceTimersByTimeAsync(60_000);
    // Backoff 1, 2, 4, 8 s are raised to the 10 s hidden interval; the fifth failure waits 16 s.
    expect(reads(h)).toEqual([0, 10000, 20000, 30000, 40000, 56000]);
    h.poller.stop();
  });
});

describe('context fence (S-014, spec 7.2)', () => {
  it('drops a late answer from an older epoch and reads again in the new one', async () => {
    const h = harness({ status: { path: '/api/status', mode: 'idle' } });
    h.poller.start();
    await flush();
    const old = h.fake.next('/api/status');
    h.setEpoch(1);
    old.respond(200, statusOk.body);
    await flush();
    expect(h.arrivals).toEqual([]);
    h.poller.contextChanged();
    await flush();
    h.fake.next('/api/status').respond(200, statusOk.body);
    await flush();
    expect(h.arrivals.map((arrival) => arrival.epoch)).toEqual([1]);
    h.poller.stop();
  });

  it('a context change aborts every flight: their answers are never delivered', async () => {
    const h = harness({ status: { path: '/api/status', mode: 'idle' } });
    h.poller.start();
    await flush();
    const old = h.fake.held[0];
    h.setEpoch(1);
    h.poller.contextChanged();
    await flush();
    expect(old?.settled()).toBe(true);
    expect(h.arrivals).toEqual([]);
    expect(reads(h)).toEqual([0, 0]);
    h.poller.stop();
  });
});

describe('S-012 status reads are accepted while any write is in flight', () => {
  it('S-012 a held Stop: later reads render at once, and the released answer changes only the Stop line', async () => {
    let harvest: unknown = sample('status.collecting');
    const { fake, data } = bootDashboard({ status: () => statusBody(harvest) });
    await flush();
    await data.commands.stop({
      ...fence(data),
      kind: 'cancel',
      operationId: 'op-1',
      expectedRevision: 2,
    });
    await data.commands.pause();
    harvest = sample('status.off');
    await vi.advanceTimersByTimeAsync(600);
    expect(selectGemSlot(data.store.getSnapshot()).seen.word).toBe('idle');
    expect(fake.writes()).toHaveLength(2);
    data.stop();
  });
});

describe('plan-requested retry for a read without a policy backoff', () => {
  it('a failed config read is read again on the schedule backoff only while its plan asks for retry', async () => {
    const plans: Partial<Record<PolledResource, ReadPlan>> = {
      config: { path: '/api/config', mode: 'off', retry: 'schedule' },
    };
    const h = harness(plans);
    let fail = true;
    h.fake.route('GET', '/api/config', () =>
      fail ? { status: 502, body: 'Bad gateway' } : { status: 200, body: {} },
    );
    h.poller.start();
    await vi.advanceTimersByTimeAsync(6_500);
    // Entry read, then 2000 ms and 4000 ms after each failed read ended.
    expect(reads(h, '/api/config')).toEqual([0, 2000, 6000]);
    fail = false;
    await vi.advanceTimersByTimeAsync(8_000);
    expect(reads(h, '/api/config')).toEqual([0, 2000, 6000, 14000]);
    // Answered: config has no cadence of its own, so nothing more is read.
    await vi.advanceTimersByTimeAsync(120_000);
    expect(reads(h, '/api/config')).toHaveLength(4);
    h.poller.stop();
  });

  it('without a plan retry a failed config read is not read again', async () => {
    const h = harness({ config: { path: '/api/config', mode: 'off' } });
    h.fake.route('GET', '/api/config', () => ({ status: 502, body: 'Bad gateway' }));
    h.poller.start();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(reads(h, '/api/config')).toEqual([0]);
    h.poller.stop();
  });
});
