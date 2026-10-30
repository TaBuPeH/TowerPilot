import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendHandoff } from '../../../src/api/intents/handoff.ts';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { handoffTarget } from '../../../src/core/needs.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import {
  bootDashboard,
  E0_CONTEXT,
  flush,
  machineHarness,
  postBodies,
  fence,
} from '../../support/dataLayer.ts';
import { dashboardCase, dashboardCaseNames } from '../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../support/fixture.ts';

/** S-101: the collection handoff is sent only from a person's explicit confirmation; never automatically. */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

const CONFIRMATION = {
  operationId: 'op-48',
  setupBattleId: 'battle-7a0002',
  evidenceRevision: 'rev-1d',
  attestation: 'new_manual_battle',
} as const;

describe('S-101 the collection handoff', () => {
  it('S-101 the body is bound to the confirmed target and context, attestation new_manual_battle', () => {
    const { fake, machine } = machineHarness();
    sendHandoff(machine, CONFIRMATION, E0_CONTEXT);
    expect(postBodies(fake)).toEqual([
      {
        operation_id: 'op-48',
        setup_battle_id: 'battle-7a0002',
        evidence_revision: 'rev-1d',
        expected_context: { instance: 'main', account: null, serial: 'fake:1' },
        attestation: 'new_manual_battle',
      },
    ]);
  });

  it('S-101 no read, poll, render or retry posts it, whatever the guards say', async () => {
    const guarded = dashboardCase(
      'status.guards_setup_and_collection_blocking_the_same_operation',
    ).body;
    const { fake, data } = bootDashboard({
      status: () => ({ ...(guarded as object), ...statusBody(sample('status.setup_guard_block')) }),
    });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fake.writes()).toEqual([]);
    data.stop();
  });

  it('S-101 an unknown outcome is resent only with a new dialog confirmation', async () => {
    const { fake, machine } = machineHarness();
    const sent = sendHandoff(machine, CONFIRMATION, E0_CONTEXT);
    fake.next('/api/setup-battle/collection-handoff').respond(500, '<!doctype html>');
    await flush();
    if (sent.kind !== 'sent') throw new Error('not sent');
    expect(machine.snapshot()[0]?.phase).toBe('unconfirmed');
    expect(machine.resend(sent.record.id)).toBe('needs_confirmation');
    expect(fake.writes()).toHaveLength(1);
    expect(machine.resend(sent.record.id, { confirmedAgain: true })).toBeNull();
    expect(fake.writes().map((entry) => entry.body)).toEqual([
      fake.writes()[0]?.body,
      fake.writes()[0]?.body,
    ]);
  });

  it.each(dashboardCaseNames('handoff'))(
    'S-101 the answer %s settles its record by its class',
    (name) => {
      const recorded = dashboardCase(`handoff.${name}`);
      const phase = settledPhase(
        'handoff',
        { status: recorded.status, body: recorded.body, contract: 'exact', issues: [] },
        {},
      );
      expect(phase).toBe(recorded.status === 200 ? 'confirmed' : 'refused');
    },
  );

  it('S-101 a lost handoff answer is settled by a later guard read naming the same operation and evidence', async () => {
    const released = dashboardCase('status.guards_setup_blocking_collection_released_by_handoff')
      .body as Record<string, unknown>;
    const setup = released['setup_battle_guard'] as Record<string, unknown>;
    // Before the handoff both guards block on the same setup battle: the target on screen.
    const blocking = {
      ...released,
      collection_guard: { ...setup, scope: 'collection' },
      harvest: sample('status.off'),
    };
    const target = handoffTarget(blocking, 0);
    if (target === null) throw new Error('the blocking status must name a handoff target');
    let body: unknown = blocking;
    const { fake, data } = bootDashboard({ status: () => body });
    await flush();
    await data.commands.handoff({ ...fence(data), ...target, attestation: 'new_manual_battle' });
    fake.last('/api/setup-battle/collection-handoff').lose();
    await flush();
    body = released;
    await vi.advanceTimersByTimeAsync(3000);
    // The answer can no longer arrive (lost), so the read's settlement resolves the record for
    // good: no resend is offered, here or after a reload (R8-06).
    expect(selectOutcomes(data.store.getSnapshot()).lines[0]).toMatchObject({
      kind: 'handoff',
      phase: 'confirmed',
      settledByRead: 'recorded',
      resendOffered: false,
    });
    expect(fake.writes()).toHaveLength(1);
    data.stop();
  });
});
