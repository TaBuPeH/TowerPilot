import { describe, expect, it } from 'vitest';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectFallback } from '../../../src/store/selectors/fallback.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';

/** S-092: a known operation's Stop survives detail failures and unreadable reads (X3 to X6 inputs). */
function collectingOperation() {
  const harvest = sampleRecord('status.collecting');
  const operation = harvest['operation'] as Record<string, unknown>;
  return { harvest, id: String(operation['id']), revision: Number(operation['revision']) };
}

describe('S-092 the last known Stop per operation', () => {
  it('S-092 a malformed detail (cmdline {toString:null}) keeps a working Stop with the newest revision', () => {
    const { harvest, id, revision } = collectingOperation();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    const row = {
      pid: 4101,
      runner: 'gem_collector',
      created: 1790000000.5,
      operation_id: id,
      operation_revision: revision,
      cmdline: { toString: null },
    };
    h.read('status', statusBody(harvest, { processes: [row] }));
    const command = { kind: 'cancel', operationId: id, expectedRevision: revision, epoch: 0 };
    expect(selectFallback(h.state()).stops).toEqual([
      expect.objectContaining({ shown: true, enabled: true, command }),
    ]);
    expect(selectAutomationSlot(h.state()).rows[0]?.stop).toMatchObject({
      shown: true,
      enabled: true,
      lastKnown: false,
      command,
    });
  });

  it('S-092 X3: a warm 502 keeps the operation Stop (retained); every fallback has Pause and Turn input off', () => {
    const { harvest, id, revision } = collectingOperation();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    h.fail('status');
    const fallback = selectFallback(h.state());
    expect(fallback.pause).toMatchObject({ shown: true, enabled: true });
    expect(fallback.turnInputOff).toMatchObject({ shown: true, enabled: true });
    expect(fallback.stops).toEqual([
      expect.objectContaining({
        shown: true,
        enabled: true,
        command: { kind: 'cancel', operationId: id, expectedRevision: revision, epoch: 0 },
      }),
    ]);
  });

  it('S-092 X6: a cold first read builds the Stop from the validated minimal record', () => {
    const { harvest, id } = collectingOperation();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    expect(h.state().lastKnownStops.map((stop) => stop.key)).toEqual([`operation:${id}`]);
  });

  it('S-092 an unreadable or stale read keeps it; a D-4 read proving the operation gone removes it', () => {
    const { harvest } = collectingOperation();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    h.read('status', statusBody(sample('status.status_failed')));
    expect(h.state().lastKnownStops).toHaveLength(1);
    h.read(
      'status',
      statusBody(sample('status.off'), {
        scan: { state: 'fresh', error: null, age_sec: 180, last_success_at: 0 },
      }),
    );
    expect(h.state().lastKnownStops).toHaveLength(1);
    h.read('status', statusBody(sample('status.off')));
    expect(h.state().lastKnownStops).toEqual([]);
  });

  it('S-092 the newest revision wins, from a Stop answer or the read, never a lower one (S-050)', () => {
    const { harvest, id, revision } = collectingOperation();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    h.intent({
      id: 'stop',
      kind: 'stop',
      target: `operation:${id}`,
      meta: { operationId: id, expectedRevision: revision },
    });
    const answer = sampleRecord('stop_operation.cancel.stopping');
    const body = answer['body'] as Record<string, unknown>;
    body['operation'] = { ...(body['operation'] as object), id, revision: revision + 5 };
    h.answer('stop', answerOf(answer));
    h.read('status', statusBody(harvest));
    const command = { kind: 'cancel', operationId: id, expectedRevision: revision + 5, epoch: 0 };
    expect(selectFallback(h.state()).stops[0]).toMatchObject({
      shown: true,
      enabled: true,
      command,
    });
    expect(selectAutomationSlot(h.state()).stops[0]).toMatchObject({
      shown: true,
      enabled: true,
      command,
    });
  });
});

describe('S-092 B-02 the normal Automation slot keeps the last known Stop', () => {
  it.each(['status_failed', 'harvest_missing'])(
    'S-092 after an HTTP-200 %s read the normal slot shows the retained Stop, enabled, with the exact command',
    (name) => {
      const { harvest, id, revision } = collectingOperation();
      const h = storeHarness({ config: e0Config(false) });
      h.read('status', statusBody(harvest));
      const partial: Record<string, unknown> =
        name === 'status_failed' ? statusBody(sample('status.status_failed')) : statusBody(harvest);
      if (name === 'harvest_missing') delete partial['harvest'];
      h.read('status', partial);
      const slot = selectAutomationSlot(h.state());
      // The newest read proves nothing about the operation: no row of its own, an Unknown word.
      expect(slot.rows).toEqual([]);
      expect(slot.word.word).toBe('unknown');
      expect(slot.stops).toEqual([
        expect.objectContaining({
          key: `operation:${id}`,
          kind: 'cancel',
          shown: true,
          enabled: true,
          lastKnown: true,
          command: { kind: 'cancel', operationId: id, expectedRevision: revision, epoch: 0 },
        }),
      ]);
    },
  );

  it('S-092 a current row and a retained Stop for the same id are one Stop with the highest revision', () => {
    const { harvest, id, revision } = collectingOperation();
    const h = storeHarness({ config: e0Config(false) });
    const later = { ...(harvest['operation'] as object), revision: revision + 3 };
    h.read('status', statusBody({ ...harvest, operation: later }));
    // A newer read names the same operation with a lower revision: the retained one wins.
    h.read('status', statusBody(harvest));
    const slot = selectAutomationSlot(h.state());
    expect(slot.stops).toHaveLength(1);
    expect(slot.stops[0]).toMatchObject({
      lastKnown: false,
      shown: true,
      enabled: true,
      command: { kind: 'cancel', operationId: id, expectedRevision: revision + 3, epoch: 0 },
    });
  });

  it('S-092 control: a D-4 read proving the operation gone leaves no retained Stop', () => {
    const { harvest } = collectingOperation();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    h.read('status', statusBody(sample('status.off')));
    expect(selectAutomationSlot(h.state()).stops).toEqual([]);
  });
});
