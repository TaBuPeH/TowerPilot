import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { sendStop } from '../../../src/api/intents/stop.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectFallback } from '../../../src/store/selectors/fallback.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { bootDashboard, E0_CONTEXT, flush, machineHarness } from '../../support/dataLayer.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../support/fixture.ts';

/** S-058: one Stop in flight per operation, never blocking status, one sender for every Stop surface. */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('S-058 Stop', () => {
  it('S-058 a double click sends one POST {expected_revision} to /api/operations/{id}/cancel', () => {
    const { fake, machine } = machineHarness();
    expect(sendStop(machine, { operationId: 'op-1', expectedRevision: 2 }, E0_CONTEXT).kind).toBe(
      'sent',
    );
    expect(sendStop(machine, { operationId: 'op-1', expectedRevision: 2 }, E0_CONTEXT).kind).toBe(
      'in_flight',
    );
    expect(fake.writes().map((entry) => [entry.path, entry.body])).toEqual([
      ['/api/operations/op-1/cancel', '{"expected_revision":2}'],
    ]);
  });

  it('S-058 every Stop surface (the row, the fallback, an owner notice) sends through the one command', async () => {
    const { fake, data } = bootDashboard({ status: () => statusBody(sample('status.collecting')) });
    await flush();
    const state = data.store.getSnapshot();
    const row = selectAutomationSlot(state).rows.find((entry) => entry.stop.kind === 'cancel');
    const fallback = selectFallback(state).stops[0];
    expect(row?.stop.command).toEqual(fallback?.command);
    if (row?.stop.command === null || row?.stop.command === undefined) throw new Error('no Stop');
    await data.commands.stop(row.stop.command);
    if (fallback?.command === null || fallback?.command === undefined) throw new Error('no Stop');
    await data.commands.stop(fallback.command);
    expect(fake.writes()).toHaveLength(1);
    expect(selectAutomationSlot(data.store.getSnapshot()).rows[0]?.stop).toMatchObject({
      enabled: true,
      sending: true,
    });
    await vi.advanceTimersByTimeAsync(3000);
    expect(fake.requests.filter((entry) => entry.path === '/api/status').length).toBeGreaterThan(3);
    data.stop();
  });

  it.each(sampleNames('stop_operation.cancel'))(
    'S-058 a cancel answer settles the Stop record: %s',
    (name) => {
      const answer = sampleRecord(`stop_operation.cancel.${name}`);
      const operation = (answer['body'] as Record<string, unknown>)['operation'] as Record<
        string,
        unknown
      >;
      expect(
        settledPhase(
          'stop',
          { status: Number(answer['status']), body: answer['body'], contract: 'exact', issues: [] },
          { operationId: String(operation['id']) },
        ),
      ).toBe('confirmed');
    },
  );

  it('S-058 an unknown Stop outcome offers Stop again only while a later read shows the operation active', async () => {
    let harvest: unknown = sample('status.collecting');
    const { fake, data } = bootDashboard({ status: () => statusBody(harvest) });
    await flush();
    const command = selectAutomationSlot(data.store.getSnapshot()).rows[0]?.stop.command;
    if (command === null || command === undefined) throw new Error('no Stop');
    await data.commands.stop(command);
    fake.last(/\/cancel$/).respond(500, '<!doctype html>');
    await flush();
    await vi.advanceTimersByTimeAsync(600);
    const line = selectOutcomes(data.store.getSnapshot()).lines[0];
    expect(line).toMatchObject({ kind: 'stop', phase: 'unconfirmed', outcome: 'unknown' });
    expect(line?.stopAgain).toMatchObject({ kind: 'cancel' });
    harvest = sample('status.off');
    await vi.advanceTimersByTimeAsync(3000);
    const settled = selectOutcomes(data.store.getSnapshot()).lines[0];
    expect(settled).toMatchObject({ settledByRead: 'stopped', stopAgain: null });
    expect(fake.writes()).toHaveLength(1);
    data.stop();
  });
});
