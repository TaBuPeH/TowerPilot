import { describe, expect, it } from 'vitest';
import { selectFallback } from '../../../src/store/selectors/fallback.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { dashboardBody } from '../../support/dashboardFixture.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';
import { storeHarness } from '../../support/storeHarness.ts';

/**
 * F4-01 (spec 3.7 INV-3 applied to every fallback, X9): a fallback keeps the
 * Stop targets the page could not make working - drawn disabled with their
 * reason, never given a command - beside the always-enabled Pause and Turn
 * input off. Working targets stay enabled with their exact command.
 */

const X9_ROW = { pid: 9009, operation_id: 7 };

function collecting() {
  const harvest = sampleRecord('status.collecting');
  const operation = harvest['operation'] as Record<string, unknown>;
  return { harvest, id: String(operation['id']), revision: Number(operation['revision']) };
}

function revocationsEnabled(fallback: ReturnType<typeof selectFallback>) {
  expect(fallback.pause).toMatchObject({ shown: true, enabled: true });
  expect(fallback.turnInputOff).toMatchObject({ shown: true, enabled: true });
}

describe('F4-01 INV-3 a fallback keeps incomplete Stop targets disabled with their reason', () => {
  it('F4-01 X9 a malformed linked row (operation_id 7): the fallback Stop is disabled, "operation_unidentified", PID 9009, no command', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(sample('status.off'), { processes: [X9_ROW] }));
    const fallback = selectFallback(h.state());
    revocationsEnabled(fallback);
    expect(fallback.stops).toEqual([
      expect.objectContaining({
        kind: 'disabled',
        shown: true,
        enabled: false,
        reason: 'operation_unidentified',
        operation: null,
        pid: 9009,
        command: null,
      }),
    ]);
  });

  it('F4-01 X9 warm: a later 502 or an HTTP-200 status_failed read keeps the disabled target and still never a command', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(sample('status.off'), { processes: [X9_ROW] }));
    h.fail('status');
    const disabled = {
      kind: 'disabled',
      enabled: false,
      reason: 'operation_unidentified',
      pid: 9009,
      command: null,
    };
    revocationsEnabled(selectFallback(h.state()));
    expect(selectFallback(h.state()).stops).toEqual([expect.objectContaining(disabled)]);
    // A read that answers but cannot list the processes: only the retained record has it.
    h.read('status', statusBody(sample('status.status_failed')));
    const fallback = selectFallback(h.state());
    revocationsEnabled(fallback);
    expect(fallback.stops).toEqual([expect.objectContaining({ ...disabled, lastKnown: true })]);
  });

  it('F4-01 an untracked runner with created null: the fallback kill is disabled, "created_missing", never a kill command', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', dashboardBody('status.processes_untracked_created_null'));
    const fallback = selectFallback(h.state());
    revocationsEnabled(fallback);
    expect(fallback.stops).toEqual([
      expect.objectContaining({
        kind: 'disabled',
        enabled: false,
        reason: 'created_missing',
        pid: 4501,
        command: null,
      }),
    ]);
    h.fail('status');
    expect(selectFallback(h.state()).stops).toEqual([
      expect.objectContaining({ reason: 'created_missing', pid: 4501, command: null }),
    ]);
  });

  it('F4-01 a holder with no valid revision: the fallback Stop is disabled "waiting_for_fresh_status" with its id, no command', () => {
    const harvest = sampleRecord('status.stopping');
    const operation = harvest['operation'] as Record<string, unknown>;
    const id = String(operation['id']);
    operation['revision'] = 'x';
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    const fallback = selectFallback(h.state());
    revocationsEnabled(fallback);
    expect(fallback.stops).toEqual([
      expect.objectContaining({
        kind: 'disabled',
        enabled: false,
        reason: 'waiting_for_fresh_status',
        operation: id,
        command: null,
      }),
    ]);
  });

  it('F4-01 control: a valid operation beside the X9 row keeps its enabled Stop with the exact command', () => {
    const { harvest, id, revision } = collecting();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest, { processes: [X9_ROW] }));
    const stops = selectFallback(h.state()).stops;
    expect(stops).toHaveLength(2);
    expect(stops).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: 'cancel',
          shown: true,
          enabled: true,
          command: { kind: 'cancel', operationId: id, expectedRevision: revision, epoch: 0 },
        }),
        expect.objectContaining({
          kind: 'disabled',
          reason: 'operation_unidentified',
          command: null,
        }),
      ]),
    );
  });

  it('F4-01 one control per target: a working Stop for the id replaces its disabled record, and a lost revision never disables it', () => {
    const { harvest, id, revision } = collecting();
    const broken = structuredClone(harvest);
    (broken['operation'] as Record<string, unknown>)['revision'] = 'x';
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(broken));
    expect(selectFallback(h.state()).stops.map((stop) => stop.kind)).toEqual(['disabled']);
    h.read('status', statusBody(harvest));
    const command = { kind: 'cancel', operationId: id, expectedRevision: revision, epoch: 0 };
    expect(selectFallback(h.state()).stops).toEqual([
      expect.objectContaining({ kind: 'cancel', enabled: true, command }),
    ]);
    // A later read that loses the revision does not take the known Stop away (S-050).
    h.read('status', statusBody(broken));
    expect(selectFallback(h.state()).stops).toEqual([
      expect.objectContaining({ kind: 'cancel', enabled: true, command }),
    ]);
  });

  it('F4-01 a complete read that no longer lists PID 9009 removes the disabled target (D-4)', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(sample('status.off'), { processes: [X9_ROW] }));
    h.read('status', statusBody(sample('status.off')));
    expect(selectFallback(h.state()).stops).toEqual([]);
    expect(h.state().lastKnownStops).toEqual([]);
  });
});
