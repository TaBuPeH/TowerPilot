import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendKill } from '../../../src/api/intents/kill.ts';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { bootDashboard, E0_CONTEXT, flush, machineHarness } from '../../support/dataLayer.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../support/fixture.ts';

/** S-060: kill sends the exact pid and created of the row; never resent. */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('S-060 kill of an untracked runner', () => {
  it('S-060 the row with created posts its exact pid and created; the row with created null has a disabled Stop', async () => {
    const rows = sample('stop_operation.processes') as Record<string, unknown>[];
    const { fake, data } = bootDashboard({
      status: () => statusBody(sample('status.off'), { processes: rows }),
    });
    await flush();
    const stops = selectAutomationSlot(data.store.getSnapshot()).rows.map((row) => row.stop);
    expect(stops.map((stop) => [stop.kind, stop.enabled, stop.reason])).toEqual([
      ['kill', true, null],
      ['disabled', false, 'created_missing'],
    ]);
    const command = stops[0]?.command;
    if (command === null || command === undefined) throw new Error('no kill');
    await data.commands.stop(command);
    expect(fake.writes().map((entry) => JSON.parse(entry.body ?? '{}') as unknown)).toEqual([
      { action: 'kill', pid: rows[0]?.['pid'], created: rows[0]?.['created'] },
    ]);
    data.stop();
  });

  it('S-060 a kill without a created value sends nothing', () => {
    const { fake, machine } = machineHarness();
    expect(sendKill(machine, { pid: 4242, created: Number.NaN }, E0_CONTEXT)).toEqual({
      kind: 'not_sent',
      reason: 'target_incomplete',
    });
    expect(fake.writes()).toEqual([]);
  });

  it.each(sampleNames('stop_operation.kill'))(
    'S-060 the answer %s settles without any resend',
    async (name) => {
      const { fake, machine } = machineHarness();
      const sent = sendKill(machine, { pid: 4242, created: 1790000000.5 }, E0_CONTEXT);
      const answer = sampleRecord(`stop_operation.kill.${name}`);
      fake.next('/api/control').respond(Number(answer['status']), answer['body']);
      await flush();
      const expected = Number(answer['status']) === 200 ? 'confirmed' : 'refused';
      expect(machine.snapshot()[0]?.phase).toBe(expected);
      expect(
        settledPhase(
          'kill',
          { status: Number(answer['status']), body: answer['body'], contract: 'exact', issues: [] },
          {},
        ),
      ).toBe(expected);
      if (sent.kind !== 'sent') throw new Error('not sent');
      expect(machine.resend(sent.record.id)).toBe('not_resendable');
      expect(fake.writes()).toHaveLength(1);
    },
  );

  it('S-060 a 5xx or lost kill answer stays unconfirmed ("may still be running") and is never resent', async () => {
    const { fake, machine } = machineHarness();
    const sent = sendKill(machine, { pid: 4242, created: 1790000000.5 }, E0_CONTEXT);
    fake.next('/api/control').respond(500, { ok: false, error: 'process did not exit' });
    await flush();
    expect(machine.snapshot()[0]?.phase).toBe('unconfirmed');
    if (sent.kind !== 'sent') throw new Error('not sent');
    expect(machine.resend(sent.record.id)).toBe('not_resendable');
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fake.writes()).toHaveLength(1);
  });
});
