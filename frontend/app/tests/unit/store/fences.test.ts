import { describe, expect, it } from 'vitest';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sampleRecord, statusBody } from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';

/** Feedback fences: S-013 (request, context and choice sequence), S-046, S-047. */
function rev(path: string, revision: number, change?: (value: Record<string, unknown>) => void) {
  const value = sampleRecord(path);
  const desired = path.startsWith('status.')
    ? (value['desired'] as Record<string, unknown>)
    : ((value['body'] as Record<string, unknown>)['desired'] as Record<string, unknown>);
  desired['revision'] = revision;
  change?.(value);
  return value;
}

describe('S-013 feedback is fenced by request, context and choice sequence', () => {
  it('S-013 a held Turn on, then Turn input off (harvest paused), a later Off rev 6, then the old enable_ok rev 5: no current "Turned on"', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(rev('status.off', 4)));
    h.intent({ id: 'on', kind: 'turn_on', key: 'k-on', meta: { expectedRevision: 4 } });
    const offSeq = h.tick();
    h.intent({
      id: 'on',
      kind: 'turn_on',
      key: 'k-on',
      meta: { expectedRevision: 4 },
      supersededBySeq: offSeq,
    });
    h.intent({ id: 'input-off', kind: 'input_off', sentSeq: offSeq });
    h.answer('input-off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    h.read('status', statusBody(rev('status.off', 6)));
    h.answer('on', answerOf(rev('post_harvest.enable_ok', 5)));
    h.read('status', statusBody(rev('status.off', 6)));
    expect(
      selectOutcomes(h.state()).lines.filter((line) => line.kind === 'turn_on' && line.current),
    ).toEqual([]);
    expect(selectGemSlot(h.state()).choice.word).toBe('off');
  });

  it('S-013 a success older than a later read with a higher revision is history even without a newer request', () => {
    const h = storeHarness();
    h.read('status', statusBody(rev('status.off', 4)));
    h.intent({ id: 'on', kind: 'turn_on', key: 'k-on', meta: { expectedRevision: 4 } });
    h.answer('on', answerOf(rev('post_harvest.enable_ok', 5)));
    expect(selectOutcomes(h.state()).lines[0]?.current).toBe(true);
    h.read('status', statusBody(rev('status.off', 6)));
    expect(selectOutcomes(h.state()).lines[0]?.current).toBe(false);
  });

  it('S-013 a lost Turn on is settled by a later read: applied only with revision expected+1 and its key, else not applied', () => {
    const applied = storeHarness();
    applied.read('status', statusBody(rev('status.off', 4)));
    applied.intent({
      id: 'on',
      kind: 'turn_on',
      key: 'k-on',
      phase: 'unconfirmed',
      meta: { expectedRevision: 4 },
    });
    applied.read(
      'status',
      statusBody(
        rev('status.starting_due', 5, (value) => {
          (value['desired'] as Record<string, unknown>)['request_key'] = 'k-on';
        }),
      ),
    );
    expect(selectOutcomes(applied.state()).lines[0]).toMatchObject({
      settledByRead: 'applied',
      resendOffered: false,
    });
    const other = storeHarness();
    other.intent({
      id: 'on',
      kind: 'turn_on',
      key: 'k-on',
      phase: 'unconfirmed',
      meta: { expectedRevision: 4 },
    });
    other.read('status', statusBody(rev('status.off', 6)));
    expect(selectOutcomes(other.state()).lines[0]).toMatchObject({ settledByRead: 'not_applied' });
    const equal = storeHarness();
    equal.intent({
      id: 'on',
      kind: 'turn_on',
      key: 'k-on',
      phase: 'unconfirmed',
      meta: { expectedRevision: 4 },
    });
    equal.read('status', statusBody(rev('status.off', 4)));
    expect(selectOutcomes(equal.state()).lines[0]).toMatchObject({
      settledByRead: null,
      resendOffered: true,
    });
  });
});

describe('S-046 the input answer\'s "harvesting paused" claim belongs to its revision', () => {
  it('S-046 disable_stopping, then a later readable On at a higher revision: the paused claim is history', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'off', kind: 'input_off' });
    const answer = sampleRecord('automation_access.disable_stopping');
    h.answer('off', answerOf(answer));
    const claim = (
      ((answer['body'] as Record<string, unknown>)['revocation'] as Record<string, unknown>)[
        'harvest'
      ] as Record<string, unknown>
    )['revision'];
    expect(selectOutcomes(h.state()).lines[0]?.harvestPausedCurrent).toBe(true);
    h.read('status', statusBody(rev('status.starting_due', Number(claim) + 1)));
    expect(selectOutcomes(h.state()).lines[0]?.harvestPausedCurrent).toBe(false);
  });

  it('S-046 control: a later Off at the same revision keeps the claim current', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'off', kind: 'input_off' });
    const answer = sampleRecord('automation_access.disable_stopping');
    h.answer('off', answerOf(answer));
    const claim = (
      ((answer['body'] as Record<string, unknown>)['revocation'] as Record<string, unknown>)[
        'harvest'
      ] as Record<string, unknown>
    )['revision'];
    h.read('status', statusBody(rev('status.off', Number(claim))));
    expect(selectOutcomes(h.state()).lines[0]?.harvestPausedCurrent).toBe(true);
  });
});

describe('S-047 the input-off outcome becomes history once a newer input fact exists', () => {
  function green() {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'off', kind: 'input_off' });
    h.answer('off', answerOf(sampleRecord('automation_access.disable_input_stopped')));
    const revoked = sampleRecord('status.off');
    revoked['input_revoked'] = true;
    h.read('status', statusBody(revoked));
    h.read('config', e0Config(false));
    return h;
  }

  it('S-047 (a) a config read with allow_taps true and a later status without input_revoked: the green is gone', () => {
    const h = green();
    expect(selectInputSlot(h.state()).seen).toMatchObject({ row: 'I4', word: 'idle' });
    h.read('config', e0Config(true));
    const back = sampleRecord('status.collecting');
    h.read('status', statusBody(back));
    expect(selectInputSlot(h.state()).seen.word).not.toBe('idle');
    expect(selectOutcomes(h.state()).lines[0]?.current).toBe(false);
  });

  it('S-047 (b) an enable sent and held: the old green is gone at once, before its answer', () => {
    const h = green();
    h.intent({ id: 'on', kind: 'input_on' });
    expect(selectInputSlot(h.state()).seen.row).toBe('I2');
    expect(selectOutcomes(h.state()).lines.find((line) => line.kind === 'input_off')?.current).toBe(
      false,
    );
  });

  it('S-047 control: before any newer fact the green stays', () => {
    const h = green();
    expect(selectInputSlot(h.state()).seen).toMatchObject({ row: 'I4', evidence: 'EV-7' });
    expect(selectOutcomes(h.state()).lines[0]?.current).toBe(true);
  });
});
