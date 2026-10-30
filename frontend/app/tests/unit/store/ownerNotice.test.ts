import { describe, expect, it } from 'vitest';
import { serverTextContent } from '../../../src/copy/server.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';

/** The shared owner notice (spec 7.5; S-049, S-070). */
const CLASSES: Readonly<Record<string, string | null>> = {
  action_lock: null,
  collector_running: 'operation_owner',
  collector_running_manual: 'operation_owner',
  collector_stopping: 'operation_owner',
  device_owned_collector: 'operation_owner',
  device_owned_lease: 'lease_holder',
  device_owned_pre_spawn: 'operation_owner',
  device_owned_process: 'process_owner',
  device_owned_run: 'operation_owner',
  device_owner_unreadable: 'owner_unreadable',
  reserved_fields: null,
  setup_battle_unresolved: null,
};

describe('S-070 one shared handler renders every owner refusal', () => {
  it.each(sampleNames('refusals'))('S-070 refusals.%s, whichever write it answered', (name) => {
    for (const kind of ['start', 'reconnect', 'input_on', 'handoff'] as const) {
      const h = storeHarness();
      h.intent({ id: 'w', kind });
      const answer = sampleRecord(`refusals.${name}`);
      h.answer('w', answerOf(answer), 'refused');
      const notice = selectOutcomes(h.state()).ownerNotice;
      const expected = CLASSES[name];
      if (expected === null || expected === undefined) {
        expect(notice).toBeNull();
        continue;
      }
      expect(notice?.noticeClass).toBe(expected);
      const recovery = (answer['body'] as Record<string, unknown>)['recovery'] as
        Record<string, unknown> | null | undefined;
      const action = recovery?.['action'];
      expect(notice?.recovery.kind).toBe(
        action === 'pause_harvesting'
          ? 'pause'
          : action === 'stop_operation'
            ? 'stop'
            : expected === 'owner_unreadable'
              ? 'retry'
              : 'none',
      );
      expect(notice?.stopped).toBeNull();
      expect(notice?.canRetry).toBe(false);
    }
  });

  it('S-070 the notice names the owner as server text and never says free until a later D-4 read proves it', () => {
    const h = storeHarness();
    h.intent({ id: 'w', kind: 'reconnect' });
    h.answer('w', answerOf(sampleRecord('refusals.device_owned_collector')), 'refused');
    const notice = selectOutcomes(h.state()).ownerNotice;
    expect(notice?.operation === null ? null : serverTextContent(notice?.operation ?? fail())).toBe(
      (
        (sampleRecord('refusals.device_owned_collector')['body'] as Record<string, unknown>)[
          'owner'
        ] as Record<string, unknown>
      )['operation_id'],
    );
    h.read('status', statusBody(sample('status.status_failed')));
    expect(selectOutcomes(h.state()).ownerNotice).toMatchObject({ canRetry: false });
    expect(selectOutcomes(h.state()).ownerNotice?.free?.kind).toBe('unknown');
    h.read('status', statusBody(sample('status.off')));
    const resolved = selectOutcomes(h.state()).ownerNotice;
    expect(resolved?.stopped?.kind).toBe('known');
    expect(resolved?.free?.kind).toBe('known');
    expect(resolved?.canRetry).toBe(true);
  });

  it('S-070 control: a 400 invalid_request and the guard refusal are not owner notices', () => {
    for (const name of ['reserved_fields', 'setup_battle_unresolved']) {
      const h = storeHarness();
      h.intent({ id: 'w', kind: 'start' });
      h.answer('w', answerOf(sampleRecord(`refusals.${name}`)), 'refused');
      expect(selectOutcomes(h.state()).ownerNotice).toBeNull();
      expect(selectOutcomes(h.state()).lines[0]).toMatchObject({ phase: 'refused' });
    }
  });
});

function fail(): never {
  throw new Error('missing');
}

describe('S-049 a refused input enable goes to the shared owner notice', () => {
  it('S-049 enable_refused_owner: the switch is drawn from the saved setting (off), the notice names the owner', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'on', kind: 'input_on' });
    h.answer('on', answerOf(sampleRecord('automation_access.enable_refused_owner')), 'refused');
    expect(selectInputSlot(h.state()).setting).toEqual({ known: true, allowed: false });
    expect(selectOutcomes(h.state()).ownerNotice?.noticeClass).toBe('operation_owner');
  });

  it('S-049 control: enable_ok and a later config read with allow_taps true show the switch on', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'on', kind: 'input_on' });
    h.answer('on', answerOf(sampleRecord('automation_access.enable_ok')));
    h.read('config', e0Config(true));
    expect(selectInputSlot(h.state()).setting).toEqual({ known: true, allowed: true });
    expect(selectOutcomes(h.state()).ownerNotice).toBeNull();
  });
});
