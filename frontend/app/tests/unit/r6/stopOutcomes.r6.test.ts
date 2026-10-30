import { describe, expect, it } from 'vitest';
import { classifyStopAnswer } from '../../../src/core/outcomes/stop.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { answerRevisionClaims, stopRevision } from '../../../src/core/stopRevision.ts';
import { sample, sampleNames } from '../../support/fixture.ts';
import { requireR6Fixture } from './requireR6.ts';

requireR6Fixture();

function target(answer: unknown): string {
  const operationId = ownPath(answer, 'body', 'operation', 'id');
  if (typeof operationId !== 'string') throw new Error('operation id required');
  return operationId;
}
describe('r6 coded Stop contract', () => {
  it('S-052 ended_after_termination attributes the signals this request sent', () => {
    const answer = sample('stop_operation.cancel.ended_after_termination');
    const result = classifyStopAnswer(answer, target(answer));
    expect(result).toMatchObject({
      kind: 'ended',
      endedAttribution: 'this_stop_signalled',
      effects: ownPath(answer, 'body', 'stop', 'by_this_request'),
    });
    expect(result.effects?.signalled.length).toBeGreaterThan(0);
  });
  it.each(['unverified_lease_held_after_stop', 'unverified_lease_unreadable_after_stop'])(
    'S-053 %s remains unconfirmed despite signals and offers both revocations',
    (name) => {
      const answer = sample(`stop_operation.cancel.${name}`);
      const result = classifyStopAnswer(answer, target(answer));
      expect(result).toMatchObject({
        kind: 'unverified',
        reason: ownPath(answer, 'body', 'stop', 'reason'),
        effects: ownPath(answer, 'body', 'stop', 'by_this_request'),
        turnInputOff: true,
        stopAgainRevision: ownPath(answer, 'body', 'operation', 'revision'),
      });
      const holders = ownPath(answer, 'body', 'stop', 'holder', 'lease', 'holders');
      expect(result.holders).toEqual(holders ?? []);
    },
  );
  it.each(
    sampleNames('stop_operation.conflicts').filter((name) => name !== 'stop_outcome_partial'),
  )('S-054 classifies r6 conflicts.%s as a definite coded refusal', (name) => {
    const answer = sample(`stop_operation.conflicts.${name}`);
    const operationId = ownPath(answer, 'body', 'operation', 'id');
    const result = classifyStopAnswer(
      answer,
      typeof operationId === 'string'
        ? operationId
        : target(sample('stop_operation.cancel.stopping')),
    );
    expect(result).toMatchObject({
      kind: 'not_applied',
      code: name,
      error: ownPath(answer, 'body', 'error'),
    });
    expect(result.operation?.raw ?? null).toEqual(ownPath(answer, 'body', 'operation'));
  });
  it('S-055 r6 partial states exactly the effects, conflict and current revision', () => {
    const answer = sample('stop_operation.conflicts.stop_outcome_partial');
    expect(classifyStopAnswer(answer, target(answer))).toMatchObject({
      kind: 'partial',
      effects: ownPath(answer, 'body', 'stop', 'by_this_request'),
      conflict: ownPath(answer, 'body', 'conflict'),
      stopAgainRevision: ownPath(answer, 'body', 'operation', 'revision'),
    });
  });
  it('S-050 survivor repeat keeps the latest revision through the stale Stop conflict', () => {
    const family = 'stop_operation.repeat.held_after_stop_then_the_survivor_leaves';
    const first = sample(`${family}.first`);
    const stillHeld = sample(`${family}.still_held`);
    const stale = sample(`${family}.stale_stop`);
    const operationId = target(first);
    expect(target(stillHeld)).toBe(operationId);
    expect(target(stale)).toBe(operationId);
    expect(
      stopRevision(operationId, [
        ...answerRevisionClaims(own(first, 'body')),
        ...answerRevisionClaims(own(stillHeld, 'body')),
      ]),
    ).toBe(ownPath(first, 'body', 'operation', 'revision'));
    expect(
      stopRevision(operationId, [
        ...answerRevisionClaims(own(first, 'body')),
        ...answerRevisionClaims(own(stillHeld, 'body')),
        ...answerRevisionClaims(own(stale, 'body')),
      ]),
    ).toBe(ownPath(stale, 'body', 'operation', 'revision'));
    expect(classifyStopAnswer(stale, operationId)).toMatchObject({
      kind: 'not_applied',
      code: 'operation_changed',
      stopAgainRevision: null,
    });
  });
  it('S-053 survivor repeat remains held until a later path settles after_release', () => {
    const family = 'stop_operation.repeat.held_after_stop_then_the_survivor_leaves';
    for (const step of ['first', 'still_held']) {
      const answer = sample(`${family}.${step}`);
      expect(classifyStopAnswer(answer, target(answer))).toMatchObject({
        kind: 'unverified',
        reason: 'lease_held_after_stop',
        turnInputOff: true,
      });
    }
    const after = sample(`${family}.after_release`);
    const stale = sample(`${family}.stale_stop`);
    const operation = {
      ...(ownPath(stale, 'body', 'operation') as Record<string, unknown>),
      status: own(after, 'status'),
      error: own(after, 'error'),
    };
    const reply = { status: 200, body: { ok: true, operation, stop: own(after, 'stop') } };
    expect(classifyStopAnswer(reply, target(stale))).toMatchObject({
      kind: 'ended',
      endedAttribution: 'unattributed',
    });
  });
});
