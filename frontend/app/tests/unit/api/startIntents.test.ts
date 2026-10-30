import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { sendCollect, startClick } from '../../../src/api/intents/start.ts';
import type { ContextRead } from '../../../src/core/connection.ts';
import { E0_CONTEXT, flush, machineHarness, postBodies } from '../../support/dataLayer.ts';
import { dashboardCase, dashboardCaseNames } from '../../support/dashboardFixture.ts';
import { counter } from '../../support/fakeTransport.ts';
import { sampleRecord } from '../../support/fixture.ts';

/** S-103: Start and Collect send no private harvest fields and use a key frozen per click. */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

const EXPECTED = { ...E0_CONTEXT, rendering: null };

describe('S-103 Start and Collect', () => {
  it('S-103 Collect sends exactly {action, preset, expected_context, idempotency_key}: no harvest, no desire_revision', () => {
    const { fake, machine } = machineHarness();
    sendCollect(
      machine,
      { preset: 'bp_farm', expected: EXPECTED, context: E0_CONTEXT },
      counter('key'),
    );
    expect(postBodies(fake)).toEqual([
      {
        action: 'start_gems',
        preset: 'bp_farm',
        expected_context: {
          instance: 'main',
          account: null,
          serial: 'fake:1',
          rendering: null,
          profile: 'mine',
        },
        idempotency_key: 'key-0001',
      },
    ]);
  });

  it('S-103 Start reads readiness in the click and sends only what its gate permits, with the readiness context', async () => {
    const { fake, machine } = machineHarness();
    const ready = dashboardCase('readiness.ready_with_context');
    fake.route('GET', /^\/api\/readiness/, () => ({ status: ready.status, body: ready.body }));
    const seen: ContextRead[] = [];
    const result = await startClick(
      { machine, transport: fake.transport, newKey: counter('key'), epoch: () => 0 },
      {
        preset: 'bp_farm',
        context: E0_CONTEXT,
        gate: (read) => {
          seen.push(read);
          return { enabled: true, reason: 'ready' };
        },
      },
    );
    expect(result.kind).toBe('sent');
    expect(seen[0]).toMatchObject({ kind: 'answered', sameContext: true });
    const body = postBodies(fake)[0] as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      'action',
      'expected_context',
      'idempotency_key',
      'preset',
    ]);
    expect(body).toMatchObject({ action: 'start', preset: 'bp_farm', idempotency_key: 'key-0001' });
    expect(fake.requests.map((entry) => entry.path)).toEqual([
      '/api/readiness?preset=bp_farm',
      '/api/control',
    ]);
  });

  it('S-103 a gate that does not permit sends nothing; a failed readiness read sends nothing', async () => {
    const { fake, machine } = machineHarness();
    fake.route('GET', /^\/api\/readiness/, () => ({ status: 500, body: 'boom' }));
    const result = await startClick(
      { machine, transport: fake.transport, newKey: counter('key'), epoch: () => 0 },
      {
        preset: 'bp_farm',
        context: E0_CONTEXT,
        gate: () => ({ enabled: false, reason: 'readiness_unconfirmed' }),
      },
    );
    expect(result).toEqual({ kind: 'not_sent', reason: 'readiness_unconfirmed' });
    expect(fake.writes()).toEqual([]);
  });

  it('S-103 each click freezes a new key; a resend of an unknown outcome reuses it byte for byte', async () => {
    const { fake, machine } = machineHarness();
    const newKey = counter('key');
    const first = sendCollect(
      machine,
      { preset: 'bp_farm', expected: EXPECTED, context: E0_CONTEXT },
      newKey,
    );
    fake.next('/api/control').respond(500, '<!doctype html>');
    await flush();
    if (first.kind !== 'sent') throw new Error('not sent');
    expect(machine.resend(first.record.id)).toBeNull();
    expect(fake.writes()[1]?.body).toBe(fake.writes()[0]?.body);
    const accepted = dashboardCase('control.start_gems_accepted');
    fake.next('/api/control').respond(accepted.status, accepted.body);
    await flush();
    sendCollect(machine, { preset: 'bp_farm', expected: EXPECTED, context: E0_CONTEXT }, newKey);
    expect((postBodies(fake)[2] as Record<string, unknown>)['idempotency_key']).toBe('key-0002');
  });

  it('S-103 reserved_fields (400 invalid_request) is a refusal, never retried', async () => {
    const { fake, machine } = machineHarness();
    const sent = sendCollect(
      machine,
      { preset: 'bp_farm', expected: EXPECTED, context: E0_CONTEXT },
      counter('key'),
    );
    const refused = sampleRecord('refusals.reserved_fields');
    fake.next('/api/control').respond(Number(refused['status']), refused['body']);
    await flush();
    expect(machine.snapshot()[0]?.phase).toBe('refused');
    if (sent.kind !== 'sent') throw new Error('not sent');
    expect(machine.resend(sent.record.id)).toBe('not_unknown');
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fake.writes()).toHaveLength(1);
  });

  it.each(
    [...dashboardCaseNames('control'), ...dashboardCaseNames('control_unknown_outcomes')].filter(
      (name) => name.startsWith('start'),
    ),
  )(
    'S-103 the start answer %s settles by its class (unknown stays unconfirmed for a resend)',
    (name) => {
      const family = dashboardCaseNames('control').includes(name)
        ? 'control'
        : 'control_unknown_outcomes';
      const recorded = dashboardCase(`${family}.${name}`);
      const phase = settledPhase(
        'start',
        { status: recorded.status, body: recorded.body, contract: 'exact', issues: [] },
        {},
      );
      expect(phase).toBe(
        recorded.status === 200 ? 'confirmed' : recorded.status >= 500 ? 'unconfirmed' : 'refused',
      );
    },
  );
});
