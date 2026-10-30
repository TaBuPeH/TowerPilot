import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TransportRequest } from '../../../src/api/transport.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import type { StopCommand } from '../../../src/store/commands.ts';
import { bootDashboard, e0Config, fence, flush, postBodies } from '../../support/dataLayer.ts';
import { dashboardCase } from '../../support/dashboardFixture.ts';
import {
  createFakeTransport,
  createMemoryStorage,
  type FakeTransport,
} from '../../support/fakeTransport.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';

/**
 * The wired data layer's fences (review R8-01, R8-03, R8-04, R8-06, B-01,
 * B-05): the first context is an epoch of its own, a click's reads share the
 * poller's single flight and its context, a command drawn in one context sends
 * nothing in another, a settlement a read proved survives failed reads and a
 * reload, a late answer outranks a read taken before it, and a resumed page
 * shows nothing older than the expiry as current.
 */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

/** A fake transport whose held requests ignore aborts, so a late answer really arrives. */
function deafTransport(): FakeTransport {
  const base = createFakeTransport();
  return {
    ...base,
    transport: (request: TransportRequest) =>
      base.transport(
        request.body === undefined
          ? { method: request.method, path: request.path }
          : { method: request.method, path: request.path, body: request.body },
      ),
  };
}

function collecting() {
  const harvest = sampleRecord('status.collecting');
  const operation = harvest['operation'] as Record<string, unknown>;
  return { harvest, id: String(operation['id']), revision: Number(operation['revision']) };
}

describe('R8-01 the first context starts an epoch of its own', () => {
  it('S-014 a status read started before the first config named a context is dropped when it answers late', async () => {
    const fake = deafTransport();
    let statusCalls = 0;
    const { data } = bootDashboard({
      fake,
      before: (transport) => {
        transport.route('GET', '/api/status', () => {
          statusCalls += 1;
          return statusCalls === 1
            ? 'hold'
            : { status: 200, body: statusBody(sample('status.off')) };
        });
      },
    });
    await flush();
    expect(data.store.getSnapshot()).toMatchObject({ epoch: 1, contextConfirmed: true });
    // The read of the old epoch answers after the context was named: dropped.
    fake.next('/api/status').respond(200, statusBody(sample('status.collecting')));
    await flush();
    const latest = data.store.getSnapshot().reads.status.latest;
    expect(latest?.epoch).toBe(1);
    expect(selectGemSlot(data.store.getSnapshot()).seen.word).not.toBe('running');
    data.stop();
  });

  it('S-014 the schedule and battle history are read as soon as the context names the instance', async () => {
    const { fake, data } = bootDashboard();
    await flush();
    const paths = fake.requests
      .filter((entry) => entry.method === 'GET')
      .map((entry) => entry.path);
    expect(paths).toContain('/api/schedule/status?instance=main');
    expect(
      paths.some((path) => path.startsWith('/api/battles?') && path.includes('instance=main')),
    ).toBe(true);
    data.stop();
  });
});

describe('R8-03 a Pause click reads through the poller and its context fence', () => {
  it('S-002 a Pause during the entry config read joins that read: no second config GET', async () => {
    let configCalls = 0;
    const { fake, data } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/config', () => {
          configCalls += 1;
          return configCalls === 1 ? 'hold' : { status: 200, body: e0Config() };
        });
        // No readable status names the emulator: the click must read config itself (INV-1).
        transport.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
      },
    });
    await flush();
    expect(configCalls).toBe(1);
    const click = data.commands.pause();
    await flush();
    expect(configCalls).toBe(1);
    expect(data.store.getSnapshot().context).toBeNull();
    fake.next('/api/config').respond(200, e0Config());
    await click;
    await flush();
    // The config answer that named the context is the click's own: one Pause, no harvest read.
    expect(
      fake.requests.filter((entry) => entry.method === 'GET' && entry.path === '/api/harvest'),
    ).toHaveLength(0);
    expect(postBodies(fake, '/api/harvest')).toEqual([
      {
        enabled: false,
        expected_context: { instance: 'main', serial: 'fake:1' },
        idempotency_key: 'key-0001',
      },
    ]);
    data.stop();
  });

  it('S-014 a click read released after the context changed sends nothing', async () => {
    let config: unknown = 'down';
    const { fake, data } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/config', () =>
          config === 'down' ? { status: 502, body: 'Bad gateway' } : { status: 200, body: config },
        );
        // No read names the emulator before the click (INV-1's first two sources are missing).
        transport.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
        transport.route('GET', '/api/harvest', () => 'hold');
      },
    });
    await flush();
    expect(data.store.getSnapshot().context).toBeNull();
    const click = data.commands.pause();
    await flush();
    // While the click waits for /api/harvest, the page learns another context.
    config = e0Config(false, 'other', 'fake:2');
    data.poller.read('config');
    await flush();
    expect(data.store.getSnapshot().context?.instance).toBe('other');
    // The held harvest read names the old emulator (main, fake:1).
    fake.next('/api/harvest').respond(200, { ok: true, harvest: sample('status.off') });
    await click;
    await flush();
    expect(fake.writes()).toEqual([]);
    expect(data.store.getSnapshot().notices).toMatchObject([
      { kind: 'pause_not_sent', reason: 'context_changed' },
    ]);
    data.stop();
  });
});

describe('R8-04 a command drawn in one context sends nothing in another', () => {
  const variants: readonly (readonly [string, () => unknown])[] = [
    ['instance', () => e0Config(false, 'other', 'fake:1')],
    ['serial', () => e0Config(false, 'main', 'fake:9')],
    [
      'account',
      () => ({
        active_instance: 'main',
        active_profile: 'mine',
        instances: { main: { serial: 'fake:1', allow_taps: false, account: 'second' } },
      }),
    ],
    ['profile', () => ({ ...(e0Config(false) as object), active_profile: 'theirs' })],
  ];

  it.each(variants)(
    'S-101 a Stop confirmed after the %s changed sends zero POSTs',
    async (_field, changed) => {
      const { harvest } = collecting();
      let config: unknown = e0Config(false);
      const { fake, data } = bootDashboard({
        config: () => config,
        status: () => statusBody(harvest),
      });
      await flush();
      const command = selectAutomationSlot(data.store.getSnapshot()).stops[0]?.command;
      expect(command).toMatchObject({ kind: 'cancel', epoch: 1 });
      config = changed();
      data.poller.read('config');
      await flush();
      expect(data.store.getSnapshot().epoch).toBe(2);
      await data.commands.stop(command as StopCommand);
      expect(fake.writes()).toEqual([]);
      expect(data.store.getSnapshot().notices).toMatchObject([
        { kind: 'command_not_sent', reason: 'context_changed' },
      ]);
      data.stop();
    },
  );

  it('S-101 control: the same Stop in its own context is sent once', async () => {
    const { harvest, id, revision } = collecting();
    const { fake, data } = bootDashboard({ status: () => statusBody(harvest) });
    await flush();
    const command = selectAutomationSlot(data.store.getSnapshot()).stops[0]?.command;
    await data.commands.stop(command as StopCommand);
    expect(postBodies(fake)).toEqual([{ expected_revision: revision }]);
    expect(fake.writes()[0]?.path).toBe(`/api/operations/${id}/cancel`);
    data.stop();
  });

  it('S-101 a handoff whose target the newest read no longer names sends nothing', async () => {
    const released = dashboardCase('status.guards_setup_blocking_collection_released_by_handoff')
      .body as Record<string, unknown>;
    const setup = released['setup_battle_guard'] as Record<string, unknown>;
    const { fake, data } = bootDashboard({
      status: () => ({ ...released, collection_guard: { ...setup, scope: 'collection' } }),
    });
    await flush();
    await data.commands.handoff({
      ...fence(data),
      operationId: String(setup['operation_id']),
      setupBattleId: String(setup['setup_battle_id']),
      evidenceRevision: 'f'.repeat(64),
      attestation: 'new_manual_battle',
    });
    expect(fake.writes()).toEqual([]);
    expect(data.store.getSnapshot().notices).toMatchObject([
      { kind: 'command_not_sent', reason: 'handoff_target_changed' },
    ]);
    data.stop();
  });
});

describe('R8-07 a Stop click sends the highest revision known at the click', () => {
  it('S-050 a Stop drawn at revision r, clicked after a read named r + 4, sends r + 4', async () => {
    const { harvest, id, revision } = collecting();
    const operation = harvest['operation'] as Record<string, unknown>;
    let body = statusBody(harvest);
    const { fake, data } = bootDashboard({ status: () => body });
    await flush();
    const command = selectAutomationSlot(data.store.getSnapshot()).stops[0]?.command;
    expect(command).toMatchObject({ expectedRevision: revision });
    body = statusBody({ ...harvest, operation: { ...operation, revision: revision + 4 } });
    await vi.advanceTimersByTimeAsync(3000);
    await data.commands.stop(command as StopCommand);
    expect(postBodies(fake)).toEqual([{ expected_revision: revision + 4 }]);
    expect(fake.writes()[0]?.path).toBe(`/api/operations/${id}/cancel`);
    data.stop();
  });
});

describe('R8-06 a settlement a read proved survives failed reads and a reload', () => {
  /** Turn on whose answer never comes; the read then settles it as `settlement`. */
  async function lostTurnOn(settlement: 'applied' | 'not_applied') {
    const storage = createMemoryStorage();
    const off = sampleRecord('status.off');
    const expected = Number((off['desired'] as Record<string, unknown>)['revision']);
    let status: unknown = statusBody(off);
    const first = bootDashboard({
      storage,
      before: (transport) => {
        transport.route('GET', '/api/status', () =>
          status === 'down' ? { status: 502, body: 'Bad gateway' } : { status: 200, body: status },
        );
      },
    });
    await flush();
    await first.data.commands.turnOn({
      ...fence(first.data),
      preset: 'bp_farm',
      expectedRevision: expected,
    });
    await vi.advanceTimersByTimeAsync(16_000);
    const record = first.data.machine.snapshot()[0];
    expect(record).toMatchObject({ phase: 'unconfirmed', lostReason: 'window_expired' });
    const on = sampleRecord('status.starting_due');
    const desired = on['desired'] as Record<string, unknown>;
    desired['revision'] = expected + 1;
    desired['request_key'] = settlement === 'applied' ? record?.key : 'someone-elses-key';
    status = statusBody(on);
    await vi.advanceTimersByTimeAsync(3000);
    const line = () => selectOutcomes(first.data.store.getSnapshot()).lines[0];
    expect(line()).toMatchObject({ settledByRead: settlement, resendOffered: false });
    status = 'down';
    await vi.advanceTimersByTimeAsync(10_000);
    expect(first.data.store.getSnapshot().reads.status.latest?.outcome.kind).toBe('failed');
    expect(line()).toMatchObject({ settledByRead: settlement, resendOffered: false });
    first.data.stop();
    return { storage, status: () => status, writes: first.fake.writes().length };
  }

  it.each(['applied', 'not_applied'] as const)(
    'S-010 a lost Turn on settled %s by a read stays settled after a failed read and a reload: no resend offered',
    async (settlement) => {
      const lost = await lostTurnOn(settlement);
      expect(lost.writes).toBe(1);
      const second = bootDashboard({
        storage: lost.storage,
        before: (transport) => {
          transport.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
        },
      });
      await flush();
      const line = selectOutcomes(second.data.store.getSnapshot()).lines[0];
      expect(line).toMatchObject({
        kind: 'turn_on',
        restored: true,
        settledByRead: settlement,
        resendOffered: false,
      });
      await second.data.commands.resend(line?.id ?? '');
      await vi.advanceTimersByTimeAsync(60_000);
      expect(second.fake.writes()).toEqual([]);
      second.data.stop();
    },
  );
});

describe('B-01 a late Stop answer outranks a read taken before it', () => {
  it('S-020 a Stop whose window passed is settled by an Off read, then its late unverified answer withdraws it', async () => {
    const { harvest, id } = collecting();
    let status: unknown = statusBody(harvest);
    const { fake, data } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/status', () =>
          status === 'down' ? { status: 502, body: 'Bad gateway' } : { status: 200, body: status },
        );
      },
    });
    await flush();
    const command = selectAutomationSlot(data.store.getSnapshot()).stops[0]?.command;
    await data.commands.stop(command as StopCommand);
    const held = fake.last(/\/cancel$/);
    status = statusBody(sample('status.off'));
    await vi.advanceTimersByTimeAsync(16_000);
    const line = () => selectOutcomes(data.store.getSnapshot()).lines[0];
    expect(line()).toMatchObject({ phase: 'unconfirmed', settledByRead: 'stopped' });
    const answer = sampleRecord('stop_operation.cancel.unverified_lease_held');
    const body = answer['body'] as Record<string, unknown>;
    body['operation'] = { ...(body['operation'] as object), id };
    // The reads after the answer fail: only the answer and the reads before it are known.
    status = 'down';
    held.respond(Number(answer['status']), body);
    await flush();
    expect(line()).toMatchObject({ outcome: 'unverified', settledByRead: null });
    expect(data.machine.snapshot()[0]?.readSettlement).toBeNull();
    expect(fake.writes()).toHaveLength(1);
    data.stop();
  });
});

describe('B-05 a resumed page shows nothing older than the expiry as current', () => {
  it('S-092 after a suspension the last read is Unknown until the forced read answers; Pause, Turn input off and Stop work throughout', async () => {
    const { harvest, id, revision } = collecting();
    let hold = false;
    const { fake, data, visibility } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/status', () =>
          hold ? 'hold' : { status: 200, body: statusBody(harvest) },
        );
      },
    });
    await flush();
    expect(selectGemSlot(data.store.getSnapshot()).seen.word).toBe('running');
    visibility.set(true);
    hold = true;
    // The page is suspended: no timer runs while a minute passes.
    vi.setSystemTime(Date.now() + 60_000);
    visibility.set(false);
    await flush();
    const state = data.store.getSnapshot();
    expect(selectGemSlot(state).seen.word).toBe('unknown');
    expect(selectGemSlot(state).pause).toMatchObject({ shown: true, enabled: true });
    expect(selectInputSlot(state).turnOff).toMatchObject({ shown: true, enabled: true });
    expect(selectAutomationSlot(state).stops[0]).toMatchObject({
      shown: true,
      enabled: true,
      command: { kind: 'cancel', operationId: id, expectedRevision: revision, epoch: 1 },
    });
    fake.last('/api/status').respond(200, statusBody(harvest));
    await flush();
    expect(selectGemSlot(data.store.getSnapshot()).seen.word).toBe('running');
    expect(fake.writes()).toEqual([]);
    data.stop();
  });
});
