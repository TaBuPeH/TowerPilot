import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WRITE_WINDOW_MS } from '../../../src/api/intents/machine.ts';
import { sendTurnOn } from '../../../src/api/intents/harvest.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import {
  bootDashboard,
  flush,
  machineHarness,
  E0_CONTEXT,
  fence,
} from '../../support/dataLayer.ts';
import { counter } from '../../support/fakeTransport.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';

/**
 * Spec 7.4 and ADR rule 3 on the write-intent machine: nothing is posted
 * automatically (S-010) and every write is bounded by the 15 s window, with a
 * newer request on the same target replacing the older record (S-011).
 */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('S-010 nothing is ever posted automatically', () => {
  it('S-010 a dropped Turn on answer: zero POSTs across 60 s of status polls, then Resend sends the identical bytes', async () => {
    const { fake, data } = bootDashboard();
    await flush();
    await vi.advanceTimersByTimeAsync(3000);
    await data.commands.turnOn({ ...fence(data), preset: 'bp_farm', expectedRevision: 4 });
    expect(fake.writes()).toHaveLength(1);
    const first = fake.writes()[0];
    fake.next('/api/harvest', 'POST').lose();
    await flush();
    const statusReadsBefore = fake.requests.filter((entry) => entry.path === '/api/status').length;
    await vi.advanceTimersByTimeAsync(60_000);
    const statusReads = fake.requests.filter((entry) => entry.path === '/api/status').length;
    expect(statusReads - statusReadsBefore).toBeGreaterThanOrEqual(20);
    expect(fake.writes()).toHaveLength(1);
    const line = selectOutcomes(data.store.getSnapshot()).lines.find(
      (entry) => entry.kind === 'turn_on',
    );
    expect(line).toMatchObject({ phase: 'unconfirmed', lost: 'network', resendOffered: true });
    await data.commands.resend(line?.id ?? '');
    expect(fake.writes()).toHaveLength(2);
    expect(fake.writes()[1]?.body).toBe(first?.body);
    expect(JSON.parse(first?.body ?? '{}')).toMatchObject({ idempotency_key: 'key-0001' });
    data.stop();
  });

  it('S-010 500 harvest_outcome_unknown keeps the key: unconfirmed, a resend reuses it, enable_replayed settles as the same receipt', async () => {
    const { fake, machine } = machineHarness();
    const sent = sendTurnOn(
      machine,
      { preset: 'bp_farm', expectedRevision: 4, context: E0_CONTEXT },
      counter('key'),
    );
    const unknown = sampleRecord('post_harvest.outcome_unknown');
    fake.next('/api/harvest').respond(Number(unknown['status']), unknown['body']);
    await flush();
    const record = machine.snapshot()[0];
    expect(record).toMatchObject({ phase: 'unconfirmed', key: 'key-0001' });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fake.writes()).toHaveLength(1);
    if (sent.kind !== 'sent') throw new Error('not sent');
    expect(machine.resend(sent.record.id)).toBeNull();
    expect(fake.writes()[1]?.body).toBe(fake.writes()[0]?.body);
    const replayed = sampleRecord('post_harvest.enable_replayed');
    fake.next('/api/harvest').respond(Number(replayed['status']), replayed['body']);
    await flush();
    expect(machine.snapshot()[0]).toMatchObject({ phase: 'confirmed', sends: 2 });
  });

  it('S-010 a restored, a timed-out and an unknown record are never sent by a timer, a read or a restore', async () => {
    const { fake, data } = bootDashboard({ status: () => statusBody(sample('status.collecting')) });
    await flush();
    await data.commands.turnInputOff();
    await data.commands.pause();
    await data.commands.stop({
      ...fence(data),
      kind: 'cancel',
      operationId: 'op-1',
      expectedRevision: 2,
    });
    expect(fake.writes()).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(WRITE_WINDOW_MS + 60_000);
    expect(fake.writes()).toHaveLength(3);
    const lines = selectOutcomes(data.store.getSnapshot()).lines;
    expect(lines.map((line) => [line.kind, line.phase, line.lost])).toEqual([
      ['input_off', 'unconfirmed', 'window_expired'],
      ['pause', 'unconfirmed', 'window_expired'],
      ['stop', 'unconfirmed', 'window_expired'],
    ]);
    data.stop();
  });

  it('S-010 Discard (dismiss) clears the line without posting; diagnostics are the only other POST', async () => {
    const { fake, data } = bootDashboard();
    await flush();
    await data.commands.turnOn({ ...fence(data), preset: 'bp_farm', expectedRevision: 4 });
    fake.next('/api/harvest', 'POST').lose();
    await flush();
    const line = selectOutcomes(data.store.getSnapshot()).lines[0];
    data.commands.dismiss(line?.id ?? '');
    expect(selectOutcomes(data.store.getSnapshot()).lines).toEqual([]);
    expect(fake.writes()).toHaveLength(1);
    expect(
      data.diagnostics.report('fault-1', { kind: 'api_error', page: 'dashboard', message: 'x' }),
    ).toBe(true);
    expect(fake.requests.filter((entry) => entry.method === 'POST')).toHaveLength(2);
    expect(fake.writes()).toHaveLength(1);
    data.stop();
  });
});

describe('S-011 every write is bounded by D-10; a newer request on the same target replaces the older one', () => {
  it('S-011 held Stop, Turn on and input writes become unconfirmed at 15 s while status polls continue', async () => {
    const { fake, data } = bootDashboard({ status: () => statusBody(sample('status.off')) });
    await flush();
    await data.commands.stop({
      ...fence(data),
      kind: 'cancel',
      operationId: 'op-1',
      expectedRevision: 2,
    });
    await data.commands.turnOn({ ...fence(data), preset: 'bp_farm', expectedRevision: 4 });
    await data.commands.allowInput();
    const reads = () => fake.requests.filter((entry) => entry.path === '/api/status').length;
    const before = reads();
    await vi.advanceTimersByTimeAsync(WRITE_WINDOW_MS - 1);
    expect(selectOutcomes(data.store.getSnapshot()).lines.map((line) => line.phase)).toEqual([
      'sending',
      'sending',
      'sending',
    ]);
    await vi.advanceTimersByTimeAsync(1500);
    expect(
      selectOutcomes(data.store.getSnapshot()).lines.map((line) => [line.phase, line.lost]),
    ).toEqual([
      ['unconfirmed', 'window_expired'],
      ['unconfirmed', 'window_expired'],
      ['unconfirmed', 'window_expired'],
    ]);
    expect(reads() - before).toBeGreaterThan(5);
    data.stop();
  });

  it('S-011 a late answer after a newer request on the same target changes nothing visible', async () => {
    const { fake, data } = bootDashboard({ status: () => statusBody(sample('status.off')) });
    await flush();
    await data.commands.turnOn({ ...fence(data), preset: 'bp_farm', expectedRevision: 4 });
    await vi.advanceTimersByTimeAsync(WRITE_WINDOW_MS + 1500);
    await data.commands.pause();
    const pause = fake.last('/api/harvest');
    expect(JSON.parse(pause.request.body ?? '{}')).toMatchObject({ enabled: false });
    const stopped = sampleRecord('post_harvest.off_ok_stopped');
    pause.respond(Number(stopped['status']), stopped['body']);
    await flush();
    const before = selectOutcomes(data.store.getSnapshot()).lines;
    const enable = sampleRecord('post_harvest.enable_ok');
    fake.next('/api/harvest').respond(Number(enable['status']), enable['body']);
    await flush();
    const after = selectOutcomes(data.store.getSnapshot()).lines;
    const pauseBefore = before.find((line) => line.kind === 'pause');
    const pauseAfter = after.find((line) => line.kind === 'pause');
    expect(pauseAfter).toEqual(pauseBefore);
    expect(after.find((line) => line.kind === 'turn_on')).toMatchObject({
      current: false,
      resendOffered: false,
    });
    data.stop();
  });

  it('S-011 control: an answer at 5 s shows its outcome normally', async () => {
    const { fake, machine } = machineHarness();
    sendTurnOn(
      machine,
      { preset: 'bp_farm', expectedRevision: 4, context: E0_CONTEXT },
      counter('key'),
    );
    await vi.advanceTimersByTimeAsync(5000);
    const enable = sampleRecord('post_harvest.enable_ok');
    fake.next('/api/harvest').respond(Number(enable['status']), enable['body']);
    await flush();
    expect(machine.snapshot()[0]).toMatchObject({ phase: 'confirmed', lostReason: null });
  });
});

describe('F6R-01 an answer counts for the attempt it belongs to', () => {
  function sentTurnOn() {
    const harness = machineHarness();
    const sent = sendTurnOn(
      harness.machine,
      { preset: 'bp_farm', expectedRevision: 4, context: E0_CONTEXT },
      counter('key'),
    );
    if (sent.kind !== 'sent') throw new Error('not sent');
    return { ...harness, id: sent.record.id };
  }
  const refusal = () => sampleRecord('post_harvest.harvest_revision_changed');

  it('S-010 F6R-01 a refused resend keeps the request unknown, its key and its resend; the refusal is kept beside it', async () => {
    const { fake, machine, id } = sentTurnOn();
    fake.next('/api/harvest').lose();
    await flush();
    expect(machine.resend(id)).toBeNull();
    fake.next('/api/harvest').respond(Number(refusal()['status']), refusal()['body']);
    await flush();
    expect(machine.snapshot()[0]).toMatchObject({
      phase: 'unconfirmed',
      key: 'key-0001',
      sends: 2,
      attemptRefusal: { status: Number(refusal()['status']) },
    });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fake.writes()).toHaveLength(2);
    expect(machine.resend(id)).toBeNull();
    expect(fake.writes()).toHaveLength(3);
    expect(fake.writes()[2]?.body).toBe(fake.writes()[0]?.body);
  });

  it('F6R-01 the first attempt refused after a resend was sent settles nothing: the resend decides', async () => {
    const { fake, machine, id } = sentTurnOn();
    await vi.advanceTimersByTimeAsync(WRITE_WINDOW_MS + 1);
    expect(machine.snapshot()[0]?.phase).toBe('unconfirmed');
    expect(machine.resend(id)).toBeNull();
    // The first attempt's late refusal: it proves only that attempt applied nothing.
    fake.next('/api/harvest').respond(Number(refusal()['status']), refusal()['body']);
    await flush();
    expect(machine.snapshot()[0]).toMatchObject({ phase: 'sending', sends: 2 });
    // The resend's receipt settles the request.
    const receipt = sampleRecord('post_harvest.enable_ok');
    fake.next('/api/harvest').respond(Number(receipt['status']), receipt['body']);
    await flush();
    expect(machine.snapshot()[0]).toMatchObject({ phase: 'confirmed', attemptRefusal: null });
  });

  it('F6R-01 control: the refusal of a request sent once is a refusal', async () => {
    const { fake, machine } = sentTurnOn();
    fake.next('/api/harvest').respond(Number(refusal()['status']), refusal()['body']);
    await flush();
    expect(machine.snapshot()[0]?.phase).toBe('refused');
  });
});
