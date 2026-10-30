import { describe, expect, it } from 'vitest';
import { initialDashboardState, reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { STATUS_EXPIRY_SEC } from '../../../src/store/reads.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import type { DashboardState, ReadRecord } from '../../../src/store/types.ts';
import { E0_CONTEXT, e0Config } from '../../support/dataLayer.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';
import { storeHarness } from '../../support/storeHarness.ts';

/**
 * B-05: Control observations expire by the age of the read that made them,
 * measured with the browser clock from the read's start (never a browser
 * time against a server time). An expired read is treated as a read that
 * did not answer: every observation Unknown with "last read {age}", while
 * Pause, Turn input off and the known Stops stay available; a newer read
 * restores them.
 */
function collecting() {
  const harvest = sampleRecord('status.collecting');
  const operation = harvest['operation'] as Record<string, unknown>;
  return { harvest, id: String(operation['id']), revision: Number(operation['revision']) };
}

describe('B-05 status observations expire by read age', () => {
  it('S-092 a collecting read older than the limit: Unknown, with Pause, Turn input off and Stop working', () => {
    const { harvest, id, revision } = collecting();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    expect(selectGemSlot(h.state()).seen.word).toBe('running');
    h.clock(STATUS_EXPIRY_SEC * 1000);
    expect(selectGemSlot(h.state()).seen.word).toBe('running');
    h.clock((STATUS_EXPIRY_SEC + 1) * 1000);
    const state = h.state();
    expect(selectGemSlot(state).seen.word).toBe('unknown');
    expect(selectAutomationSlot(state).word).toMatchObject({ word: 'unknown', row: 'A1' });
    expect(selectInputSlot(state).seen.word).not.toBe('idle');
    expect(selectGemSlot(state).pause).toMatchObject({ shown: true, enabled: true });
    expect(selectInputSlot(state).turnOff).toMatchObject({ shown: true, enabled: true });
    expect(selectAutomationSlot(state).stops).toEqual([
      expect.objectContaining({
        shown: true,
        enabled: true,
        command: { kind: 'cancel', operationId: id, expectedRevision: revision, epoch: 0 },
      }),
    ]);
    // The next read restores the observations.
    h.read('status', statusBody(harvest));
    expect(selectGemSlot(h.state()).seen).toMatchObject({ word: 'running', evidence: 'EV-3' });
  });

  it('S-006 an idle Off read older than the limit no longer hides Turn input off behind Idle', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(sample('status.off')));
    expect(selectInputSlot(h.state()).seen.word).toBe('idle');
    expect(selectInputSlot(h.state()).turnOff.shown).toBe(false);
    h.clock((STATUS_EXPIRY_SEC + 1) * 1000);
    expect(selectInputSlot(h.state()).seen.word).toBe('unknown');
    expect(selectInputSlot(h.state()).turnOff).toMatchObject({ shown: true, enabled: true });
    expect(selectAutomationSlot(h.state()).word.word).toBe('unknown');
  });

  it('S-006 a delayed answer is as old as the request that fetched it', () => {
    const at = (state: DashboardState, read: Omit<ReadRecord, 'epoch'>) =>
      reduceDashboard(state, { type: 'read', read: { ...read, epoch: state.epoch } });
    let state = reduceDashboard(initialDashboardState({ context: E0_CONTEXT, nowMs: 0 }), {
      type: 'context',
      context: E0_CONTEXT,
      epoch: 0,
      confirmed: true,
      sessionContext: E0_CONTEXT,
    });
    state = at(state, {
      resource: 'config',
      path: '/api/config',
      startedSeq: 1,
      arrivedSeq: 2,
      startedAtMs: 0,
      arrivedAtMs: 0,
      outcome: {
        kind: 'answered',
        status: 200,
        body: e0Config(false),
        contract: 'exact',
        issues: [],
      },
    });
    state = reduceDashboard(state, { type: 'clock', nowMs: (STATUS_EXPIRY_SEC + 2) * 1000 });
    const delayed = (startedAtMs: number) =>
      at(state, {
        resource: 'status',
        path: '/api/status',
        startedSeq: 3,
        arrivedSeq: 4,
        startedAtMs,
        arrivedAtMs: state.nowMs,
        outcome: {
          kind: 'answered',
          status: 200,
          body: statusBody(sample('status.collecting')),
          contract: 'exact',
          issues: [],
        },
      });
    expect(selectGemSlot(delayed(0)).seen.word).toBe('unknown');
    // Control: the same answer to a request started just now is current.
    expect(selectGemSlot(delayed(state.nowMs)).seen.word).toBe('running');
  });
});
