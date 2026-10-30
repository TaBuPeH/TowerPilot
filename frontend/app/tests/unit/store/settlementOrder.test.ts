import { describe, expect, it } from 'vitest';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectFallback } from '../../../src/store/selectors/fallback.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';

/**
 * The order of reads and answers (B-01, R8-02, R8-05, R8-07): an answer is
 * newer evidence than any read started before it, a receipt older than a read
 * never becomes current again, a record bound to no context is evidence for
 * none, and every Stop surface sends the highest revision of exactly its id.
 */
function collecting() {
  const harvest = sampleRecord('status.collecting');
  const operation = harvest['operation'] as Record<string, unknown>;
  return { harvest, id: String(operation['id']), revision: Number(operation['revision']) };
}

/** A real Stop answer of the loaded fixture, for operation `id`. */
function stopAnswer(name: string, id: string, revision?: number) {
  const answer = sampleRecord(`stop_operation.cancel.${name}`);
  const body = answer['body'] as Record<string, unknown>;
  const operation = body['operation'] as Record<string, unknown>;
  body['operation'] = { ...operation, id, ...(revision === undefined ? {} : { revision }) };
  return answerOf(answer);
}

function phaseOf(answer: { readonly status: number; readonly body: unknown }, id: string) {
  return settledPhase('stop', { ...answer, contract: 'exact', issues: [] }, { operationId: id });
}

function stopLine(h: ReturnType<typeof storeHarness>) {
  return selectOutcomes(h.state()).lines.find((line) => line.kind === 'stop');
}

describe('B-01 an answered Stop is judged against its answer, not its send', () => {
  it.each(['unverified_lease_held', 'unverified_lease_unreadable', 'stopping'])(
    'S-020 a fresh Off read completed while the Stop was in flight, then the %s answer: no stopped settlement',
    (name) => {
      const { harvest, id, revision } = collecting();
      const h = storeHarness({ config: e0Config(false) });
      h.read('status', statusBody(harvest));
      h.intent({
        id: 'stop',
        kind: 'stop',
        target: `operation:${id}`,
        meta: { operationId: id, expectedRevision: revision },
      });
      // Started after the send, finished before the answer: it proves nothing about the answer.
      h.read('status', statusBody(sample('status.off')));
      expect(stopLine(h)?.settledByRead).toBe('stopped');
      const answer = stopAnswer(name, id);
      h.answer('stop', answer, phaseOf(answer, id));
      const line = stopLine(h);
      expect(line?.outcome).toBe(name === 'stopping' ? 'stopping' : 'unverified');
      expect(line?.settledByRead).toBeNull();
      expect(h.state().readSettlements).toEqual({});
    },
  );

  it('S-020 control: a qualifying read started after the answer settles it as stopped', () => {
    const { harvest, id, revision } = collecting();
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    h.intent({
      id: 'stop',
      kind: 'stop',
      target: `operation:${id}`,
      meta: { operationId: id, expectedRevision: revision },
    });
    const answer = stopAnswer('unverified_lease_unreadable', id);
    h.answer('stop', answer, phaseOf(answer, id));
    expect(stopLine(h)?.settledByRead).toBeNull();
    h.read('status', statusBody(sample('status.off')));
    expect(stopLine(h)?.settledByRead).toBe('stopped');
    // Kept once proven: a later failed read does not undo it.
    h.fail('status');
    expect(stopLine(h)?.settledByRead).toBe('stopped');
  });
});

/** A sample with `desired.revision` set. */
function rev(path: string, revision: number) {
  const value = sampleRecord(path);
  const desired = path.startsWith('status.')
    ? (value['desired'] as Record<string, unknown>)
    : ((value['body'] as Record<string, unknown>)['desired'] as Record<string, unknown>);
  desired['revision'] = revision;
  return value;
}

describe('R8-05 a late receipt never overrides a newer read that arrived first', () => {
  it('S-013 Turn on at 4, a read showing Off at 6, then the late turned_on at 5: history at once and after a failed read', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(rev('status.off', 4)));
    h.intent({ id: 'on', kind: 'turn_on', key: 'k-on', meta: { expectedRevision: 4 } });
    h.read('status', statusBody(rev('status.off', 6)));
    h.answer('on', answerOf(rev('post_harvest.enable_ok', 5)));
    const line = () => selectOutcomes(h.state()).lines.find((entry) => entry.kind === 'turn_on');
    expect(line()).toMatchObject({ outcome: 'turned_on', current: false });
    expect(selectGemSlot(h.state()).choice.word).toBe('off');
    h.fail('status');
    expect(line()?.current).toBe(false);
    expect(h.state().highestRevision).toBe(6);
  });

  it('S-013 control: the same receipt with no newer read is current', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(rev('status.off', 4)));
    h.intent({ id: 'on', kind: 'turn_on', key: 'k-on', meta: { expectedRevision: 4 } });
    h.answer('on', answerOf(rev('post_harvest.enable_ok', 5)));
    expect(selectOutcomes(h.state()).lines[0]).toMatchObject({
      outcome: 'turned_on',
      current: true,
    });
  });
});

describe('R8-02 a record made before the page knew a context is evidence for none', () => {
  function inputOff(context: 'bound' | 'cold') {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'off', kind: 'input_off', ...(context === 'cold' ? { context: null } : {}) });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    return h;
  }

  it('S-014 a cold Turn input off whose proving answer arrives in a named context: no EV-7, line shown unbound', () => {
    const h = inputOff('cold');
    const slot = selectInputSlot(h.state());
    // Only the read's own evidence: no automation uses input (EV-6), never "input stopped" (EV-7).
    expect(slot.seen).toMatchObject({ row: 'I5', evidence: 'EV-6' });
    expect(slot.seen.evidence).not.toBe('EV-7');
    expect(selectOutcomes(h.state()).lines).toMatchObject([
      { kind: 'input_off', bound: false, outcome: 'input_stopped' },
    ]);
  });

  it('S-014 control: the same answer made in this context is EV-7', () => {
    const slot = selectInputSlot(inputOff('bound').state());
    expect(slot.seen).toMatchObject({ row: 'I4', word: 'idle', evidence: 'EV-7' });
  });
});

describe('R8-07 every Stop surface sends the highest revision of exactly its id', () => {
  it('S-050 revisions 2, 3, 7 and 9 from reads, Stop answers and a retained Stop; another id at 99', () => {
    const { harvest, id } = collecting();
    const other = '0000ffff-0000-4000-8000-000000000000';
    const h = storeHarness({ config: e0Config(false) });
    // A read at 9: the retained Stop keeps 9 when later reads name lower revisions.
    const operation = harvest['operation'] as Record<string, unknown>;
    h.read('status', statusBody({ ...harvest, operation: { ...operation, revision: 9 } }));
    h.read('status', statusBody({ ...harvest, operation: { ...operation, revision: 2 } }));
    // An answer at 3 for this id, and one at 99 for another id.
    h.intent({ id: 'stop-3', kind: 'stop', target: `operation:${id}`, meta: { operationId: id } });
    const three = stopAnswer('stopping', id, 3);
    h.answer('stop-3', three, phaseOf(three, id));
    h.intent({
      id: 'stop-99',
      kind: 'stop',
      target: `operation:${other}`,
      meta: { operationId: other },
    });
    const ninetyNine = stopAnswer('stopping', other, 99);
    h.answer('stop-99', ninetyNine, phaseOf(ninetyNine, other));
    // A not-applied answer naming this id at 7 (a conflict).
    h.intent({ id: 'stop-7', kind: 'stop', target: `operation:${id}`, meta: { operationId: id } });
    const seven = stopAnswer('stopping', id, 7);
    h.answer('stop-7', seven, phaseOf(seven, id));
    h.read('status', statusBody({ ...harvest, operation: { ...operation, revision: 2 } }));
    const command = { kind: 'cancel', operationId: id, expectedRevision: 9, epoch: 0 };
    const row = selectAutomationSlot(h.state()).rows.find((entry) => entry.stop.kind === 'cancel');
    expect(row?.stop).toMatchObject({ shown: true, enabled: true, command });
    expect(
      selectFallback(h.state()).stops.find((stop) => stop.key === `operation:${id}`),
    ).toMatchObject({ shown: true, enabled: true, command });
    // "Stop again" of a stopping answer (the operation still cancelling) sends the same.
    for (const lineId of ['stop-3', 'stop-7']) {
      expect(selectOutcomes(h.state()).lines.find((line) => line.id === lineId)?.stopAgain).toEqual(
        command,
      );
    }
    for (const stop of selectFallback(h.state()).stops) {
      if (stop.command?.kind === 'cancel' && stop.command.operationId === id) {
        expect(stop.command.expectedRevision).toBe(9);
      }
    }
  });
});
