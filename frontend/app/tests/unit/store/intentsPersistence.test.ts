import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendPause, sendTurnOn } from '../../../src/api/intents/harvest.ts';
import {
  readStoredRecord,
  restoreUnresolved,
  STORAGE_PREFIX,
} from '../../../src/api/intents/persistence.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import {
  bootDashboard,
  E0_CONTEXT,
  e0Config,
  flush,
  machineHarness,
  fence,
} from '../../support/dataLayer.ts';
import { counter, createMemoryStorage } from '../../support/fakeTransport.ts';
import { sampleRecord } from '../../support/fixture.ts';

/**
 * Spec 7.6, OD-12, DoD item 13, ADR decision 5: unresolved write records live
 * in this tab's session storage per context, survive a reload and a return in
 * their context, are hidden in another context, and are never resent.
 */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('unresolved records survive a reload', () => {
  it('an in-flight Pause and an unknown Turn on are restored after a reload as unconfirmed, with no request sent', async () => {
    const storage = createMemoryStorage();
    const first = bootDashboard({ storage });
    await flush();
    await first.data.commands.turnOn({
      ...fence(first.data),
      preset: 'bp_farm',
      expectedRevision: 4,
    });
    first.fake.last('/api/harvest').lose();
    await flush();
    await first.data.commands.pause();
    expect(first.fake.writes()).toHaveLength(2);
    const pauseBody = first.fake.writes()[1]?.body;
    first.data.stop();
    expect(
      Object.keys(storage.entries()).filter((key) => key.startsWith(STORAGE_PREFIX)),
    ).toHaveLength(1);

    // The reload: a new page with the same tab storage.
    const second = bootDashboard({ storage });
    await flush();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(second.fake.writes()).toEqual([]);
    const lines = selectOutcomes(second.data.store.getSnapshot()).lines;
    expect(lines.map((line) => [line.kind, line.phase, line.lost, line.restored])).toEqual([
      ['turn_on', 'unconfirmed', 'network', true],
      ['pause', 'unconfirmed', 'page_reloaded', true],
    ]);
    expect(lines.find((line) => line.kind === 'turn_on')?.resendOffered).toBe(false);
    const pause = lines.find((line) => line.kind === 'pause');
    expect(pause?.resendOffered).toBe(true);
    // Only a person's explicit resend sends it again, byte for byte.
    await second.data.commands.resend(pause?.id ?? '');
    expect(second.fake.writes().map((entry) => entry.body)).toEqual([pauseBody]);
    second.data.stop();
  });

  it('a record restored in another context is hidden and cannot be resent; it reappears on return', async () => {
    const storage = createMemoryStorage();
    const first = bootDashboard({ storage });
    await flush();
    await first.data.commands.pause();
    first.data.stop();
    const other = bootDashboard({ storage, config: () => e0Config(false, 'other', 'fake:2') });
    await flush();
    expect(selectOutcomes(other.data.store.getSnapshot()).lines).toEqual([]);
    const id = other.data.machine.snapshot()[0]?.id ?? '';
    expect(other.data.machine.resend(id)).toBe('other_context');
    expect(other.fake.writes()).toEqual([]);
    other.data.stop();
    const back = bootDashboard({ storage });
    await flush();
    expect(selectOutcomes(back.data.store.getSnapshot()).lines.map((line) => line.kind)).toEqual([
      'pause',
    ]);
    expect(back.fake.writes()).toEqual([]);
    back.data.stop();
  });

  it('a settled record leaves storage; a dismissed unresolved one stays, marked dismissed; keys are per context', async () => {
    const { fake, machine, storage } = machineHarness();
    const sent = sendTurnOn(
      machine,
      { preset: 'bp_farm', expectedRevision: 4, context: E0_CONTEXT },
      counter('key'),
    );
    expect(Object.keys(storage.entries())).toEqual([
      `${STORAGE_PREFIX}${JSON.stringify(['main', 'fake:1', null, 'mine'])}`,
    ]);
    const enable = sampleRecord('post_harvest.enable_ok');
    fake.next('/api/harvest').respond(Number(enable['status']), enable['body']);
    await flush();
    expect(Object.keys(storage.entries())).toEqual([]);
    sendPause(machine, E0_CONTEXT, counter('pause'));
    expect(Object.keys(storage.entries())).toHaveLength(1);
    const pause = machine.snapshot().at(-1);
    machine.dismiss(pause?.id ?? '');
    // R9-01: Dismiss hides the line only (OD-10); the unresolved request stays until its outcome is known.
    expect(restoreUnresolved(storage)).toMatchObject([
      { id: pause?.id, kind: 'pause', dismissed: true, restored: true, phase: 'unconfirmed' },
    ]);
    const off = sampleRecord('post_harvest.off_ok_stopped');
    fake.next('/api/harvest').respond(Number(off['status']), off['body']);
    await flush();
    expect(Object.keys(storage.entries())).toEqual([]);
    expect(sent.kind).toBe('sent');
  });

  it('malformed stored records are skipped, never invented', () => {
    const { machine, storage } = machineHarness();
    sendPause(machine, E0_CONTEXT, counter('key'));
    const [key] = Object.keys(storage.entries());
    const stored = JSON.parse(storage.getItem(key ?? '') ?? '[]') as Record<string, unknown>[];
    const good = stored[0] ?? {};
    expect(readStoredRecord(good)).not.toBeNull();
    for (const broken of [
      { ...good, kind: 'launch_missiles' },
      { ...good, request: { ...(good['request'] as object), path: 'http://evil/api/harvest' } },
      { ...good, phase: 'confirmed' },
      { ...good, sends: 0 },
      { ...good, meta: { expectedRevision: -1 } },
      { ...good, context: { instance: 'main' } },
      null,
      'x',
    ]) {
      expect(readStoredRecord(broken)).toBeNull();
    }
    const tampered = createMemoryStorage({
      [key ?? '']: JSON.stringify([good, { ...good, id: 'other', kind: 'nope' }]),
      [`${STORAGE_PREFIX}not-json`]: '{',
      // A record filed under another context's key is not restored there.
      [`${STORAGE_PREFIX}${JSON.stringify(['other', 'fake:2', null, 'mine'])}`]: JSON.stringify([
        good,
      ]),
    });
    expect(restoreUnresolved(tampered).map((record) => record.id)).toEqual([good['id']]);
  });
});
