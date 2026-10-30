import { describe, expect, it } from 'vitest';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sampleRecord, statusBody } from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';

/** The write records as the Control slots see them (S-003, S-005, S-048). */
function withRevision(path: string, revision: number): Record<string, unknown> {
  const value = sampleRecord(path);
  const desired = path.startsWith('status.')
    ? (value['desired'] as Record<string, unknown>)
    : ((value['body'] as Record<string, unknown>)['desired'] as Record<string, unknown>);
  desired['revision'] = revision;
  return value;
}

describe('S-003 a Pause sent while an On is pending supersedes it', () => {
  it('S-003 the late enable answer settles only its own record: no current "Turned on", the row shows Off, Pause follows D-8', () => {
    const h = storeHarness();
    h.read('status', statusBody(withRevision('status.off', 4)));
    h.intent({ id: 'on', kind: 'turn_on', key: 'key-on', meta: { expectedRevision: 4 } });
    const pauseSeq = h.tick();
    h.intent({
      id: 'on',
      kind: 'turn_on',
      key: 'key-on',
      meta: { expectedRevision: 4 },
      supersededBySeq: pauseSeq,
    });
    h.intent({ id: 'off', kind: 'pause', sentSeq: pauseSeq, key: 'key-off' });
    h.answer('off', answerOf(withRevision('post_harvest.off_ok_stopped', 6)));
    h.read('status', statusBody(withRevision('status.off', 6)));
    h.answer('on', answerOf(withRevision('post_harvest.enable_ok', 5)));
    h.read('status', statusBody(withRevision('status.off', 6)));
    const outcomes = selectOutcomes(h.state());
    expect(outcomes.lines.filter((line) => line.kind === 'turn_on' && line.current)).toEqual([]);
    expect(outcomes.lines.find((line) => line.kind === 'turn_on')).toMatchObject({
      outcome: 'turned_on',
      current: false,
      resendOffered: false,
    });
    const gem = selectGemSlot(h.state());
    expect(gem.choice.word).toBe('off');
    expect(gem.pause.shown).toBe(false);
  });

  it('S-003 control: an On with no newer Pause whose answer arrives shows its success', () => {
    const h = storeHarness();
    h.read('status', statusBody(withRevision('status.off', 4)));
    h.intent({ id: 'on', kind: 'turn_on', key: 'key-on', meta: { expectedRevision: 4 } });
    h.answer('on', answerOf(withRevision('post_harvest.enable_ok', 5)));
    expect(selectOutcomes(h.state()).lines).toMatchObject([
      { kind: 'turn_on', outcome: 'turned_on', current: true },
    ]);
    expect(selectGemSlot(h.state()).pause.shown).toBe(true);
  });
});

describe('S-005 turning input off overrides a pending enable', () => {
  it('S-005 the late enable answer cannot flip the setting or the turn-off line; Turn input off stays enabled throughout', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(sampleRecord('status.off')));
    const on = h.intent({ id: 'on', kind: 'input_on' });
    expect(selectInputSlot(h.state()).turnOff).toMatchObject({ shown: true, enabled: true });
    const offSeq = h.tick();
    h.intent({ ...on, supersededBySeq: offSeq });
    h.intent({ id: 'off', kind: 'input_off', sentSeq: offSeq });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    h.answer('on', answerOf(sampleRecord('automation_access.enable_ok')));
    const slot = selectInputSlot(h.state());
    expect(slot.setting).toEqual({ known: true, allowed: false });
    expect(slot.turnOff.enabled).toBe(true);
    const lines = selectOutcomes(h.state()).lines;
    expect(lines.find((line) => line.kind === 'input_on')).toMatchObject({ current: false });
    expect(lines.find((line) => line.kind === 'input_off')).toMatchObject({
      outcome: 'input_stopped',
      current: true,
    });
  });

  it('S-005 control: after both settle, the next config read decides the displayed setting', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('config', e0Config(true));
    expect(selectInputSlot(h.state()).setting).toEqual({ known: true, allowed: true });
  });
});

describe('S-048 an input Off confirms only earlier requests whose outcome is known', () => {
  function established(earlierEnable: boolean) {
    const h = storeHarness({ config: e0Config(false) });
    if (earlierEnable)
      h.intent({
        id: 'lost-enable',
        kind: 'input_on',
        phase: 'unconfirmed',
        lostReason: 'network',
      });
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    return h;
  }

  it('S-048 with an earlier enable lost, the Off answer never turns green: Unknown and Turn input off stay', () => {
    const slot = selectInputSlot(established(true).state());
    expect(slot.seen).toMatchObject({ row: 'I2', word: 'unknown' });
    expect(slot.turnOff).toMatchObject({ shown: true, enabled: true });
    expect(slot.offReason).toBe('enable_unresolved');
  });

  it('S-048 the fake server then applies the old enable: still never green', () => {
    const h = established(true);
    h.read('config', e0Config(true));
    const back = sampleRecord('status.off');
    back['input_revoked'] = false;
    h.read('status', statusBody(back));
    const slot = selectInputSlot(h.state());
    expect(slot.seen.word).not.toBe('idle');
    expect(slot.turnOff).toMatchObject({ shown: true, enabled: true });
  });

  it('S-048 control: an Off with no unresolved earlier enable is green (I4, EV-7)', () => {
    const slot = selectInputSlot(established(false).state());
    expect(slot.seen).toMatchObject({ row: 'I4', word: 'idle', evidence: 'EV-7' });
  });
});
