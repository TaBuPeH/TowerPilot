import { describe, expect, it } from 'vitest';
import { initialDashboardState, reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import type { DashboardState, ReadRecord } from '../../../src/store/types.ts';
import { E0_CONTEXT, e0Config } from '../../support/dataLayer.ts';
import { sample, statusBody } from '../../support/fixture.ts';

/**
 * R9-02: the reducer's own context fence (S-014), tested at the reducer
 * boundary rather than through the poller's: a read of epoch A dispatched
 * after the page entered epoch B changes nothing; the same read stamped with
 * epoch B is applied (positive control).
 */
const OTHER = { ...E0_CONTEXT, instance: 'other', serial: 'fake:2' };

function enter(state: DashboardState, epoch: number, context: typeof E0_CONTEXT): DashboardState {
  return reduceDashboard(state, {
    type: 'context',
    context,
    epoch,
    confirmed: true,
    sessionContext: context,
  });
}

function read(
  state: DashboardState,
  epoch: number,
  resource: ReadRecord['resource'],
  body: unknown,
): ReadRecord {
  const seq = Object.keys(state.reads).length + state.intents.length + 10;
  return {
    resource,
    path: resource === 'config' ? '/api/config' : '/api/status',
    epoch,
    startedSeq: seq,
    arrivedSeq: seq + 1,
    startedAtMs: state.nowMs,
    arrivedAtMs: state.nowMs,
    outcome: { kind: 'answered', status: 200, body, contract: 'exact', issues: [] },
  };
}

/** Epoch B (2), named and confirmed by its own config read. */
function inEpochB(): DashboardState {
  let state = enter(initialDashboardState({ nowMs: 0 }), 1, E0_CONTEXT);
  state = enter(state, 2, OTHER);
  return reduceDashboard(state, {
    type: 'read',
    read: read(state, 2, 'config', e0Config(false, 'other', 'fake:2')),
  });
}

describe('R9-02 the reducer drops a read of an older epoch', () => {
  it('S-014 a collecting read of epoch A dispatched in epoch B leaves the snapshot and the view models unchanged', () => {
    const before = inEpochB();
    const after = reduceDashboard(before, {
      type: 'read',
      read: read(before, 1, 'status', statusBody(sample('status.collecting'))),
    });
    expect(after).toBe(before);
    expect(after.reads.status.latest).toBeNull();
    expect(after.lastKnownStops).toEqual([]);
    expect(selectGemSlot(after)).toEqual(selectGemSlot(before));
    expect(selectAutomationSlot(after)).toEqual(selectAutomationSlot(before));
    expect(selectGemSlot(after).seen.word).toBe('unknown');
  });

  it('S-014 control: the same read stamped with epoch B is applied', () => {
    const before = inEpochB();
    const after = reduceDashboard(before, {
      type: 'read',
      read: read(before, 2, 'status', statusBody(sample('status.collecting'))),
    });
    expect(after).not.toBe(before);
    expect(after.reads.status.latest?.epoch).toBe(2);
    expect(selectGemSlot(after).seen).toMatchObject({ word: 'running', evidence: 'EV-3' });
    expect(after.lastKnownStops).toHaveLength(1);
  });
});
