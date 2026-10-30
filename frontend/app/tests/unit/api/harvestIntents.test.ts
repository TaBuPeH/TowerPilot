import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  contextFromHarvest,
  pauseClick,
  resolvePauseContext,
  sendPause,
  sendTurnOn,
  clickGet,
} from '../../../src/api/intents/harvest.ts';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { configReadOf } from '../../../src/store/reads.ts';
import {
  E0_CONTEXT,
  e0Config,
  flush,
  machineHarness,
  postBodies,
} from '../../support/dataLayer.ts';
import { counter } from '../../support/fakeTransport.ts';
import { sample, sampleNames, sampleRecord } from '../../support/fixture.ts';

/** Pause and Turn on bodies and answers (S-002, S-015, INV-1, OD-13). */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

const E0_PAUSE = { enabled: false, expected_context: { instance: 'main', serial: 'fake:1' } };

describe('S-002 the Pause body depends only on the context', () => {
  it('S-002 the body is {enabled:false, expected_context:{instance, serial}, idempotency_key}: no revision, no preset', () => {
    const { fake, machine } = machineHarness();
    sendPause(machine, E0_CONTEXT, counter('key'));
    expect(postBodies(fake)).toEqual([{ ...E0_PAUSE, idempotency_key: 'key-0001' }]);
  });

  it('S-002 with harvest omitted or a status whose projection fails, the context comes from config and the body is the same', () => {
    const config = configReadOf({
      resource: 'config',
      path: '/api/config',
      epoch: 0,
      startedSeq: 1,
      arrivedSeq: 2,
      startedAtMs: 0,
      arrivedAtMs: 0,
      outcome: { kind: 'answered', status: 200, body: e0Config(), contract: 'exact', issues: [] },
    });
    for (const harvest of [undefined, null, { version: 2 }, sample('status.status_failed')]) {
      const known = resolvePauseContext(config, harvest, null);
      const { fake, machine } = machineHarness();
      if (known === null) throw new Error('no context');
      sendPause(machine, known, counter('key'));
      expect(postBodies(fake)).toEqual([{ ...E0_PAUSE, idempotency_key: 'key-0001' }]);
    }
  });

  it('S-002 the context comes from harvest.identity of a readable status, then the session record (INV-1 order)', () => {
    const harvest = sample('status.collecting');
    expect(resolvePauseContext(null, harvest, null)).toEqual(contextFromHarvest(harvest));
    expect(resolvePauseContext(null, undefined, E0_CONTEXT)).toBe(E0_CONTEXT);
    expect(resolvePauseContext(null, undefined, null)).toBeNull();
  });

  it('S-002 INV-1: a click while the Pause is in flight sends nothing new; while it is unknown it sends the same request', async () => {
    const { fake, machine } = machineHarness();
    const newKey = counter('key');
    expect(sendPause(machine, E0_CONTEXT, newKey).kind).toBe('sent');
    expect(sendPause(machine, E0_CONTEXT, newKey).kind).toBe('in_flight');
    fake.next('/api/harvest').lose();
    await flush();
    expect(sendPause(machine, E0_CONTEXT, newKey).kind).toBe('resent');
    expect(fake.writes().map((entry) => entry.body)).toEqual([
      fake.writes()[0]?.body,
      fake.writes()[0]?.body,
    ]);
  });

  it('S-002 X2a: no context known, the click reads config and sends the E0 body (one POST)', async () => {
    const { fake, machine } = machineHarness({ context: null });
    fake.route('GET', '/api/config', () => ({ status: 200, body: e0Config() }));
    const result = await pauseClick(
      {
        machine,
        newKey: counter('key'),
        readConfig: clickGet(fake.transport, 'config'),
        readHarvest: clickGet(fake.transport, 'harvest'),
        currentContext: () => null,
      },
      null,
    );
    expect(result.kind).toBe('sent');
    expect(postBodies(fake)).toEqual([{ ...E0_PAUSE, idempotency_key: 'key-0001' }]);
  });

  it('S-002 X2a via GET /api/harvest when config does not name the emulator', async () => {
    const { fake, machine } = machineHarness({ context: null });
    fake.route('GET', '/api/config', () => ({ status: 502, body: 'Bad gateway' }));
    fake.route('GET', '/api/harvest', () => ({
      status: 200,
      body: { ok: true, harvest: sample('status.off') },
    }));
    const result = await pauseClick(
      {
        machine,
        newKey: counter('key'),
        readConfig: clickGet(fake.transport, 'config'),
        readHarvest: clickGet(fake.transport, 'harvest'),
        currentContext: () => null,
      },
      null,
    );
    expect(result.kind).toBe('sent');
    expect(postBodies(fake)).toHaveLength(1);
  });

  it('S-002 X2b: neither click read names the emulator: no POST to /api/harvest, nothing invented', async () => {
    const { fake, machine } = machineHarness({ context: null });
    fake.route('GET', '/api/config', () => ({ status: 502, body: 'Bad gateway' }));
    fake.route('GET', '/api/harvest', () => ({ status: 500, body: 'boom' }));
    const result = await pauseClick(
      {
        machine,
        newKey: counter('key'),
        readConfig: clickGet(fake.transport, 'config'),
        readHarvest: clickGet(fake.transport, 'harvest'),
        currentContext: () => null,
      },
      null,
    );
    expect(result).toEqual({ kind: 'not_sent', reason: 'context_not_named' });
    expect(fake.writes()).toEqual([]);
    const timedOut = machineHarness({ context: null });
    timedOut.fake.route('GET', '/api/config', () => 'hold');
    timedOut.fake.route('GET', '/api/harvest', () => 'hold');
    const pending = pauseClick(
      {
        machine: timedOut.machine,
        newKey: counter('key'),
        readConfig: clickGet(timedOut.fake.transport, 'config'),
        readHarvest: clickGet(timedOut.fake.transport, 'harvest'),
        currentContext: () => null,
      },
      null,
    );
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await pending).toEqual({ kind: 'not_sent', reason: 'context_not_named' });
    expect(timedOut.fake.writes()).toEqual([]);
  });
});

describe('S-015 Turn on is a compare-and-set', () => {
  it('S-015 the body carries the preset, the revision the person saw, all four context fields and a frozen key', () => {
    const { fake, machine } = machineHarness();
    sendTurnOn(
      machine,
      { preset: 'bp_farm', expectedRevision: 7, context: E0_CONTEXT },
      counter('key'),
    );
    expect(postBodies(fake)).toEqual([
      {
        enabled: true,
        preset: 'bp_farm',
        expected_revision: 7,
        expected_context: { instance: 'main', account: null, serial: 'fake:1', profile: 'mine' },
        idempotency_key: 'key-0001',
      },
    ]);
  });

  it.each(sampleNames('post_harvest'))(
    'S-015 every post_harvest answer to Turn on settles by its class: %s',
    (name) => {
      const answer = sampleRecord(`post_harvest.${name}`);
      const phase = settledPhase(
        'turn_on',
        { status: Number(answer['status']), body: answer['body'], contract: 'exact', issues: [] },
        { expectedRevision: 4 },
      );
      const expected: Record<string, string> = {
        enable_ok: 'confirmed',
        enable_replayed: 'confirmed',
        harvest_revision_changed: 'refused',
        context_changed: 'refused',
        policy_invalid: 'refused',
        idempotency_key_reused: 'refused',
        invalid_request: 'refused',
        action_lock: 'refused',
        outcome_unknown: 'unconfirmed',
      };
      expect(phase).toBe(expected[name] ?? (name.startsWith('off') ? 'unconfirmed' : 'refused'));
    },
  );
});
