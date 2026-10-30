import { describe, expect, it } from 'vitest';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { e0Config } from '../../support/dataLayer.ts';
import {
  sample,
  sampleNames,
  sampleRecord,
  serverTimeOf,
  statusBody,
} from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';
import { W_ROWS } from '../spec/specData.ts';

/** Control / Game input from the store (S-028, S-040, S-048; 9.1 W rows). */
describe('S-028 running, a heartbeat or worker_alive alone never proves collection or input', () => {
  it('S-028 a collecting sample with a 20 s old observation: not Collecting, Turn input off shown', () => {
    const harvest = sampleRecord('status.collecting');
    const battle = (harvest['operation'] as Record<string, unknown>)['battle'] as Record<
      string,
      unknown
    >;
    battle['observation_age_sec'] = 20;
    battle['published_at'] = serverTimeOf(harvest) - 20;
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(harvest));
    expect(selectGemSlot(h.state()).seen.word).not.toBe('running');
    expect(selectInputSlot(h.state()).turnOff).toMatchObject({ shown: true, enabled: true });
    expect(selectInputSlot(h.state()).seen.word).not.toBe('idle');
  });

  it('S-028 control: the unmodified sample collects', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(sample('status.collecting')));
    expect(selectGemSlot(h.state()).seen).toMatchObject({ word: 'running', evidence: 'EV-3' });
  });
});

describe('S-040 green "input stopped" comes only from a proving answer', () => {
  function afterAnswer(name: string) {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord(`automation_access.${name}`)));
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    return selectInputSlot(h.state()).seen;
  }

  it.each(sampleNames('automation_access').filter((name) => name.startsWith('disable')))(
    'S-040 %s',
    (name) => {
      const seen = afterAnswer(name);
      // S-040: input_stopped fenced and latched with every owner refused, or nothing_running with
      // every list empty (disable_during_action_lock is such an answer: the lock never blocks Off, S-004).
      const proving = [
        'disable_input_stopped',
        'disable_driver_input_refused',
        'disable_nothing_running',
        'disable_during_action_lock',
      ];
      if (proving.includes(name)) {
        expect(seen).toMatchObject({ row: 'I4', word: 'idle', evidence: 'EV-7' });
      } else {
        expect(seen.evidence).not.toBe('EV-7');
      }
    },
  );

  it('S-040 a mutated still_owned entry is never green', () => {
    for (const entry of [
      { input: 'refused' },
      { kind: 'process', pid: -1, input: 'refused' },
      null,
      [],
    ]) {
      const h = storeHarness({ config: e0Config(false) });
      h.intent({ id: 'off', kind: 'input_off' });
      const answer = sampleRecord('automation_access.disable_driver_input_refused');
      ((answer['body'] as Record<string, unknown>)['revocation'] as Record<string, unknown>)[
        'still_owned'
      ] = [entry];
      h.answer('off', answerOf(answer));
      const revoked = sampleRecord('status.off');
      revoked['input_revoked'] = true;
      h.read('status', statusBody(revoked));
      h.read('config', e0Config(false));
      expect(selectInputSlot(h.state()).seen.evidence).not.toBe('EV-7');
    }
  });
});

describe('S-048 the warm rows of 9.1 through the store', () => {
  function warm(enable: 'newer_pending' | 'earlier_unresolved') {
    const h = storeHarness({ config: e0Config(false) });
    if (enable === 'earlier_unresolved') {
      h.intent({ id: 'enable', kind: 'input_on', phase: 'unconfirmed', lostReason: 'network' });
    }
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    if (enable === 'newer_pending') h.intent({ id: 'enable', kind: 'input_on' });
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    return selectInputSlot(h.state());
  }

  it('S-048 W8: a newer Allow game input pending: Unknown (I2), Turn input off shown', () => {
    const row = W_ROWS.find((entry) => entry.id === 'W8');
    const slot = warm('newer_pending');
    expect(slot.seen).toMatchObject({ row: 'I2', word: 'unknown' });
    expect(slot.turnOff).toMatchObject({ shown: true, enabled: true });
    expect(row?.input).toContain('(I2)');
  });

  it('S-048 W9: an Allow game input sent before the Off still unresolved: Unknown (I2), never green', () => {
    const slot = warm('earlier_unresolved');
    expect(slot.seen).toMatchObject({ row: 'I2', word: 'unknown' });
    expect(slot.turnOff).toMatchObject({ shown: true, enabled: true });
  });
});
