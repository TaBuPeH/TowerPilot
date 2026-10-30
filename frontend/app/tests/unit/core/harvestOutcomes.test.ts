import { describe, expect, it } from 'vitest';
import { classifyHarvestAnswer, settleLostTurnOn } from '../../../src/core/outcomes/harvest.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { readHarvest } from '../../../src/core/readability.ts';
import { opaqueToken } from '../../../src/core/tokens.ts';
import { sample, sampleNames, sampleRecord } from '../../support/fixture.ts';

describe('harvest answers', () => {
  const expected: Record<string, string> = {
    action_lock: 'not_applied',
    context_changed: 'not_applied',
    enable_ok: 'turned_on',
    enable_replayed: 'turned_on',
    harvest_revision_changed: 'not_applied',
    idempotency_key_reused: 'not_applied',
    invalid_request: 'not_applied',
    off_during_action_lock: 'paused',
    off_ok_replayed: 'paused',
    off_ok_stopped: 'paused',
    off_ok_stopping: 'paused',
    outcome_unknown: 'unknown',
    policy_invalid: 'not_applied',
  };
  it.each(sampleNames('post_harvest'))('S-015 classifies post_harvest.%s', (name) => {
    const answer = sample(`post_harvest.${name}`);
    const result = classifyHarvestAnswer(answer, name.startsWith('off_') ? 'pause' : 'turn_on');
    expect(result.kind).toBe(expected[name]);
    if (result.kind === 'not_applied') {
      expect(result.error).toEqual(ownPath(answer, 'body', 'error'));
      expect(result.recovery).toBe(name === 'action_lock' ? 'try_again' : 'read');
    }
    if (result.kind === 'turned_on')
      expect(result.revision).toBe(ownPath(answer, 'body', 'desired', 'revision'));
  });

  it('S-004 keeps Pause on action_lock and accepts Off during the lock', () => {
    expect(classifyHarvestAnswer(sample('post_harvest.action_lock'), 'pause')).toMatchObject({
      kind: 'not_applied',
      recovery: 'try_again',
      keepPause: true,
    });
    expect(
      classifyHarvestAnswer(sample('post_harvest.off_during_action_lock'), 'pause'),
    ).toMatchObject({ kind: 'paused' });
  });

  it.each(sampleNames('post_harvest'))(
    'S-015 Turn on cannot treat an Off receipt as Known On: %s',
    (name) => {
      const result = classifyHarvestAnswer(sample(`post_harvest.${name}`), 'turn_on');
      expect(result.kind).toBe(name.startsWith('off_') ? 'unknown' : expected[name]);
    },
  );

  it('S-010 keeps the identical-resend key on an unknown outcome and accepts a replay receipt', () => {
    expect(classifyHarvestAnswer(sample('post_harvest.outcome_unknown'), 'turn_on')).toMatchObject({
      kind: 'unknown',
      keepKey: true,
    });
    expect(classifyHarvestAnswer(sample('post_harvest.enable_replayed'), 'turn_on')).toMatchObject({
      kind: 'turned_on',
      replayed: true,
    });
    expect(classifyHarvestAnswer(null, 'turn_on')).toMatchObject({
      kind: 'unknown',
      keepKey: true,
    });
  });

  it.each([null, {}, undefined, ['id'], [7], [null]])(
    'S-029 rejects malformed stopping %j',
    (stopping) => {
      const answer = sampleRecord('post_harvest.off_ok_stopping');
      const body = { ...(own(answer, 'body') as Record<string, unknown>) };
      body['stop'] = { requested: true, stopped: [], stopping, errors: [] };
      expect(classifyHarvestAnswer({ status: 200, body }, 'pause').kind).toBe(
        stopping instanceof Array && stopping.every((x) => typeof x === 'string')
          ? 'paused'
          : 'pause_unconfirmed',
      );
    },
  );

  it.each([
    undefined,
    {},
    { stopped: [], stopping: [], errors: [] },
    { requested: true, stopped: [1], stopping: [], errors: [] },
    { requested: true, stopped: [], stopping: [], errors: [{}] },
    { requested: true, stopped: [], stopping: [] },
  ])('S-029 requires requested and every string-id array %j', (stop) => {
    const body = {
      ...(own(sample('post_harvest.off_ok_stopping'), 'body') as Record<string, unknown>),
      stop,
    };
    expect(classifyHarvestAnswer({ status: 200, body }, 'pause').kind).toBe('pause_unconfirmed');
  });

  it.each(['off_ok_stopped', 'off_ok_stopping', 'off_ok_replayed'])(
    'S-029 preserves the receipt lists for %s',
    (name) => {
      const answer = sample(`post_harvest.${name}`);
      const result = classifyHarvestAnswer(answer, 'pause');
      expect(result.kind).toBe('paused');
      if (result.kind !== 'paused') throw new Error('receipt required');
      expect(result.stop.stopped).toEqual(ownPath(answer, 'body', 'stop', 'stopped'));
      expect(result.stop.stopping).toEqual(ownPath(answer, 'body', 'stop', 'stopping'));
      expect(result.stop.noCollectorWasRunning).toBe(name === 'off_ok_replayed');
    },
  );

  it('S-029 requested false cannot claim that no collector was running', () => {
    const body = {
      ...(own(sample('post_harvest.off_ok_replayed'), 'body') as Record<string, unknown>),
      stop: { requested: false, stopped: [], stopping: [], errors: [] },
    };
    expect(classifyHarvestAnswer({ status: 200, body }, 'pause')).toMatchObject({
      kind: 'paused',
      stop: { noCollectorWasRunning: false },
    });
  });

  it('S-015 settles a lost Turn on only by its next revision and equal opaque key', () => {
    const raw = sampleRecord('status.off');
    const key = opaqueToken('opaque / not a sortable key');
    if (key === null) throw new Error('key required');
    for (const [rev, requestKey, expectedResult] of [
      [4, 'opaque / not a sortable key', 'unknown'],
      [5, 'opaque / not a sortable key', 'applied'],
      [5, 'different', 'not_applied'],
      [6, 'opaque / not a sortable key', 'not_applied'],
      [3, 'opaque / not a sortable key', 'unknown'],
    ] as const) {
      const read = readHarvest({
        ...raw,
        desired: { choice: 'on', enabled: true, revision: rev, request_key: requestKey },
      });
      if (read.kind !== 'known') throw new Error('readable required');
      expect(settleLostTurnOn(read.value, 4, key)).toBe(expectedResult);
    }
  });

  it('S-095 never accepts inherited receipt fields or coerces raw messages', () => {
    const error = { toString: null };
    expect(
      classifyHarvestAnswer({ status: 409, body: { code: 'policy_invalid', error } }, 'turn_on'),
    ).toMatchObject({ error });
    const inherited: unknown = Object.create({ ok: true, desired: { choice: 'on', revision: 1 } });
    expect(classifyHarvestAnswer({ status: 200, body: inherited }, 'turn_on').kind).toBe('unknown');
  });
});
