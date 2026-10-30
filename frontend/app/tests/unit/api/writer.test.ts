import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendPause, sendTurnOn } from '../../../src/api/intents/harvest.ts';
import { sendInputOff, sendInputOn } from '../../../src/api/intents/input.ts';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { sendStop } from '../../../src/api/intents/stop.ts';
import { E0_CONTEXT, flush, machineHarness } from '../../support/dataLayer.ts';
import { counter } from '../../support/fakeTransport.ts';
import { sampleRecord } from '../../support/fixture.ts';

/** S-004: Off and input-off are never queued behind other writes or the action lock. */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

function answerOf(path: string) {
  const answer = sampleRecord(path);
  return {
    status: Number(answer['status']),
    body: answer['body'],
    contract: 'exact' as const,
    issues: [],
  };
}

describe('S-004 revocations never wait', () => {
  it('S-004 with an unrelated write and a Turn on held, Pause and Turn input off both POST at once', () => {
    const { fake, machine } = machineHarness();
    const newKey = counter('key');
    sendStop(machine, { operationId: 'op-9', expectedRevision: 3 }, E0_CONTEXT);
    sendTurnOn(machine, { preset: 'bp_farm', expectedRevision: 4, context: E0_CONTEXT }, newKey);
    expect(fake.writes()).toHaveLength(2);
    expect(sendPause(machine, E0_CONTEXT, newKey).kind).toBe('sent');
    expect(sendInputOff(machine, E0_CONTEXT).kind).toBe('sent');
    expect(fake.writes().map((entry) => entry.path)).toEqual([
      '/api/operations/op-9/cancel',
      '/api/harvest',
      '/api/harvest',
      '/api/automation-access',
    ]);
  });

  it('S-004 Turn input off is sent while an enable is in flight, and again on every click', () => {
    const { fake, machine } = machineHarness();
    sendInputOn(machine, E0_CONTEXT);
    sendInputOff(machine, E0_CONTEXT);
    sendInputOff(machine, E0_CONTEXT);
    expect(fake.writes().map((entry) => entry.body)).toEqual([
      '{"enabled":true}',
      '{"enabled":false}',
      '{"enabled":false}',
    ]);
  });

  it('S-004 a 409 action_lock answer to Pause is "not applied": refused, and the next click sends a new Pause', async () => {
    const { fake, machine } = machineHarness();
    const newKey = counter('key');
    sendPause(machine, E0_CONTEXT, newKey);
    const lock = answerOf('post_harvest.action_lock');
    fake.next('/api/harvest').respond(lock.status, lock.body);
    await flush();
    expect(machine.snapshot()[0]?.phase).toBe('refused');
    expect(sendPause(machine, E0_CONTEXT, newKey).kind).toBe('sent');
    expect(JSON.parse(fake.writes()[1]?.body ?? '{}')).toMatchObject({
      idempotency_key: 'key-0002',
    });
  });

  it('S-004 off_during_action_lock (HTTP 200) is an ordinary applied Off, like off_ok_replayed', () => {
    expect(settledPhase('pause', answerOf('post_harvest.off_during_action_lock'), {})).toBe(
      'confirmed',
    );
    expect(settledPhase('pause', answerOf('post_harvest.off_ok_replayed'), {})).toBe('confirmed');
  });

  it('S-004 control: Turn on and input enable may be refused by the lock', () => {
    expect(settledPhase('turn_on', answerOf('post_harvest.action_lock'), {})).toBe('refused');
    expect(
      settledPhase('input_on', answerOf('automation_access.enable_during_action_lock'), {}),
    ).toBe('refused');
    expect(
      settledPhase('input_off', answerOf('automation_access.disable_during_action_lock'), {}),
    ).not.toBe('unconfirmed');
  });
});
