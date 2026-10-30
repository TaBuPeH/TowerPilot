import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { contextFromConfig, createContextTracker, sameContext } from '../../../src/api/context.ts';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { bootDashboard, e0Config, flush, fence } from '../../support/dataLayer.ts';
import { createMemoryStorage } from '../../support/fakeTransport.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';

/**
 * S-014: requests and reads are bound to the context they were made in. A
 * context change discards pending feedback, never resends into the new
 * context, and ignores answers and reads from the old one.
 */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('the context tracker', () => {
  it('names the context from config only (instance, serial, account, profile), never invented', () => {
    expect(contextFromConfig(e0Config())).toEqual({
      instance: 'main',
      serial: 'fake:1',
      account: null,
      profile: 'mine',
    });
    expect(contextFromConfig({ instances: { main: { serial: 'x' } } })).toBeNull();
    expect(contextFromConfig({ active_instance: 'main', instances: {} })).toBeNull();
  });

  it('records the context in session storage and starts a new epoch only for a different one', () => {
    const storage = createMemoryStorage();
    const tracker = createContextTracker(storage);
    expect(tracker.epoch()).toBe(0);
    // R8-01: the first context named is an epoch of its own: reads from before it are dropped.
    expect(tracker.observeConfig(e0Config())).toMatchObject({ kind: 'established', epoch: 1 });
    expect(tracker.observeConfig(e0Config())).toEqual({ kind: 'unchanged' });
    expect(tracker.epoch()).toBe(1);
    expect(tracker.observeConfig(e0Config(false, 'other', 'fake:2'))).toMatchObject({
      kind: 'changed',
      epoch: 2,
    });
    const reloaded = createContextTracker(storage);
    expect(
      sameContext(
        reloaded.sessionRecorded(),
        contextFromConfig(e0Config(false, 'other', 'fake:2')),
      ),
    ).toBe(true);
  });
});

describe('S-014 a context change fences reads and writes', () => {
  it('S-014 a Turn on held in context A, released after the switch to B: nothing about it in B, no Resend in B', async () => {
    let config = e0Config();
    const { fake, data } = bootDashboard({
      config: () => config,
      status: () => statusBody(sample('status.off')),
    });
    await flush();
    await data.commands.turnOn({ ...fence(data), preset: 'bp_farm', expectedRevision: 4 });
    const held = fake.last('/api/harvest');
    config = e0Config(false, 'other', 'fake:2');
    data.poller.read('config');
    await flush();
    expect(data.tracker.epoch()).toBe(2);
    const enable = sampleRecord('post_harvest.enable_ok');
    held.respond(Number(enable['status']), enable['body']);
    await flush();
    const state = data.store.getSnapshot();
    expect(selectOutcomes(state).lines).toEqual([]);
    expect(selectGemSlot(state).choice.qualifier).toBeNull();
    const recordId = data.machine.snapshot()[0]?.id ?? '';
    expect(data.machine.resend(recordId)).toBe('not_unknown');
    expect(fake.writes()).toHaveLength(1);
    data.stop();
  });

  it('S-014 a late status answer from the old context is discarded', async () => {
    let config = e0Config();
    const { fake, data } = bootDashboard({
      config: () => config,
      before: (transport) => {
        transport.route('GET', '/api/status', () => 'hold');
      },
    });
    await flush();
    const oldStatus = fake.next('/api/status');
    config = e0Config(false, 'other', 'fake:2');
    data.poller.read('config');
    await flush();
    oldStatus.respond(200, statusBody(sample('status.collecting')));
    await flush();
    expect(data.store.getSnapshot().reads.status.latest).toBeNull();
    expect(selectGemSlot(data.store.getSnapshot()).seen.word).toBe('unknown');
    data.stop();
  });

  it('S-014 a context_changed answer is a definite refusal ("re-read"), never retried', () => {
    const answer = sampleRecord('post_harvest.context_changed');
    expect(
      settledPhase(
        'turn_on',
        { status: Number(answer['status']), body: answer['body'], contract: 'exact', issues: [] },
        {},
      ),
    ).toBe('refused');
  });

  it('S-014 control: without a context change the same answer shows normally', async () => {
    // A coherent history: the page read Off at revision k, the Turn on applied at k + 1.
    const off = sampleRecord('status.off');
    const revision = Number((off['desired'] as Record<string, unknown>)['revision']) + 1;
    const enable = sampleRecord('post_harvest.enable_ok');
    ((enable['body'] as Record<string, unknown>)['desired'] as Record<string, unknown>)[
      'revision'
    ] = revision;
    let harvest: unknown = off;
    const { fake, data } = bootDashboard({ status: () => statusBody(harvest) });
    await flush();
    await data.commands.turnOn({ ...fence(data), preset: 'bp_farm', expectedRevision: 4 });
    // The server's later reads show the choice the answer reported.
    const on = sampleRecord('status.starting_due');
    (on['desired'] as Record<string, unknown>)['revision'] = revision;
    harvest = on;
    fake.last('/api/harvest').respond(Number(enable['status']), enable['body']);
    await flush();
    expect(selectOutcomes(data.store.getSnapshot()).lines).toMatchObject([
      { kind: 'turn_on', phase: 'confirmed', outcome: 'turned_on', current: true },
    ]);
    data.stop();
  });
});
