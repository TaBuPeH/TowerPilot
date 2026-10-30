import { describe, expect, it } from 'vitest';
import { classifyStopAnswer, stopAgainAfterRead } from '../../../src/core/outcomes/stop.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import {
  dashboardBody,
  dashboardCase,
  dashboardCaseNames,
} from '../../support/dashboardFixture.ts';
import { answered, sample, sampleNames, sampleRecord, statusBody } from '../../support/fixture.ts';

function object(raw: unknown): Record<string, unknown> {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw))
    throw new Error('object required');
  return raw as Record<string, unknown>;
}
function operationId(answer: unknown): string {
  const value = ownPath(answer, 'body', 'operation', 'id');
  if (typeof value !== 'string') throw new Error('operation id required');
  return value;
}
function stoppedAnswer(): Record<string, unknown> {
  return sampleRecord('stop_operation.cancel.stopped');
}
const nothing = { paused: false, requested: false, signalled: [], family: null, released: false };

describe('Stop answers', () => {
  it.each(sampleNames('stop_operation.cancel'))(
    'S-051 classifies cancel.%s by its contract state',
    (name) => {
      const answer = sample(`stop_operation.cancel.${name}`);
      const result = classifyStopAnswer(answer, operationId(answer));
      expect(result.kind).toBe(ownPath(answer, 'body', 'stop', 'state'));
      expect(result.operation?.id).toBe(operationId(answer));
      expect(result.operation?.revision).toBe(ownPath(answer, 'body', 'operation', 'revision'));
      expect(result.operation?.error).toEqual(ownPath(answer, 'body', 'operation', 'error'));
    },
  );

  it('S-051 active is still running and keeps Stop again in both fixture runs', () => {
    const answer = stoppedAnswer();
    const body = object(answer['body']);
    const operation = object(body['operation']);
    operation['status'] = 'running';
    body['stop'] = { state: 'active', reason: null };
    expect(classifyStopAnswer(answer, operationId(answer))).toMatchObject({
      kind: 'active',
      stopAgainRevision: operation['revision'],
    });
  });

  it.each([
    ['ok', false],
    ['operation', undefined],
    ['stop', undefined],
  ])('S-051 rejects missing or malformed body field %s', (field, value) => {
    const answer = stoppedAnswer();
    object(answer['body'])[field] = value;
    expect(classifyStopAnswer(answer, operationId(stoppedAnswer())).kind).toBe('unknown');
  });

  it.each([
    ['id', 'different-operation'],
    ['status', 'weird'],
    ['revision', 1.5],
    ['revision', -1],
    ['revision', '4'],
    ['revision', undefined],
  ])('S-051 rejects an operation with invalid %s = %j', (field, value) => {
    const answer = stoppedAnswer();
    const target = operationId(answer);
    object(own(answer['body'], 'operation'))[field] = value;
    expect(classifyStopAnswer(answer, target).kind).toBe('unknown');
  });

  it.each([
    { state: 'weird', reason: null },
    { state: 'stopped' },
    { state: 'stopped', reason: 'worker_not_verifiable_lease_free' },
    { state: 'unverified', reason: null },
    { state: 'unverified', reason: 'unknown_reason' },
    { state: 'active', reason: null },
  ])('S-051 rejects invalid state/reason or contradictory operation %j', (stop) => {
    const answer = stoppedAnswer();
    object(answer['body'])['stop'] = stop;
    expect(classifyStopAnswer(answer, operationId(answer)).kind).toBe('unknown');
  });

  it('S-052 ended without signals describes the operation without attributing an effect', () => {
    const answer = sample('stop_operation.cancel.ended');
    const result = classifyStopAnswer(answer, operationId(answer));
    expect(result).toMatchObject({ kind: 'ended', endedAttribution: 'unattributed' });
    expect(result.operation?.error).toEqual(ownPath(answer, 'body', 'operation', 'error'));
    expect(result.effects).toEqual(ownPath(answer, 'body', 'stop', 'by_this_request') ?? null);
  });

  it('S-052 ended with signals attributes the worker stop only from by_this_request', () => {
    const answer = sampleRecord('stop_operation.cancel.ended');
    const signalPid = own(sampleRecord('refusals.device_owned_process')['body'], 'owner');
    const effects = {
      ...nothing,
      requested: true,
      signalled: [own(signalPid, 'pid')],
      family: 'stopped',
    };
    object(own(answer['body'], 'stop'))['by_this_request'] = effects;
    expect(classifyStopAnswer(answer, operationId(answer))).toMatchObject({
      kind: 'ended',
      endedAttribution: 'this_stop_signalled',
      effects,
    });
  });

  it.each([
    undefined,
    {},
    { ...nothing, paused: 'true' },
    { ...nothing, signalled: ['4242'] },
    { ...nothing, signalled: [0] },
    { ...nothing, family: 'weird' },
    { ...nothing, requested: undefined },
  ])('S-052 missing or malformed effects attribute nothing %j', (effects) => {
    const answer = sampleRecord('stop_operation.cancel.ended');
    object(own(answer['body'], 'stop'))['by_this_request'] = effects;
    expect(classifyStopAnswer(answer, operationId(answer))).toMatchObject({
      kind: 'ended',
      endedAttribution: 'unattributed',
      effects: null,
    });
  });

  it.each(['unverified_lease_free', 'unverified_lease_held', 'unverified_lease_unreadable'])(
    'S-053 %s is unconfirmed and keeps recovery with the highest revision',
    (name) => {
      const answer = sample(`stop_operation.cancel.${name}`);
      const result = classifyStopAnswer(answer, operationId(answer), [
        { operationId: operationId(answer), revision: 19 },
      ]);
      expect(result).toMatchObject({
        kind: 'unverified',
        stopAgainRevision: 19,
        turnInputOff: name !== 'unverified_lease_free',
      });
      expect(result.reason).toBe(ownPath(answer, 'body', 'stop', 'reason'));
    },
  );

  it('S-053 names listed holders and no holder when the list is empty', () => {
    const answer = sampleRecord('stop_operation.cancel.unverified_lease_held');
    const holder = object(ownPath(answer, 'body', 'stop', 'holder'));
    const holderPid = ownPath(sample('refusals.device_owned_process'), 'body', 'owner', 'pid');
    holder['lease'] = { held: true, holders: [{ pid: holderPid, role: 'primary' }] };
    expect(classifyStopAnswer(answer, operationId(answer)).holders).toEqual([
      { pid: holderPid, role: 'primary' },
    ]);
    holder['lease'] = { held: true, holders: [] };
    expect(classifyStopAnswer(answer, operationId(answer))).toMatchObject({
      kind: 'unverified',
      holders: [],
    });
  });

  it('S-053 repeated unverified Stop becomes released only when the answer says so', () => {
    const first = sample('stop_operation.repeat.unverified_then_released.first');
    const second = sample('stop_operation.repeat.unverified_then_released.second');
    expect(classifyStopAnswer(first, operationId(first)).kind).toBe('unverified');
    expect(classifyStopAnswer(second, operationId(first)).kind).toBe('released');
  });

  it.each([
    'operation_changed',
    'operation_unknown',
    'operation_required',
    'not_cancellable',
    'stop_refused',
  ])('S-054 coded 409 %s proves not applied and keeps the same-id current operation', (code) => {
    const operation = {
      ...object(own(stoppedAnswer()['body'], 'operation')),
      status: 'running',
      revision: 7,
    };
    const result = classifyStopAnswer(
      { status: 409, body: { code, error: { toString: null }, operation } },
      operationId(stoppedAnswer()),
    );
    expect(result).toMatchObject({
      kind: 'not_applied',
      code,
      error: { toString: null },
      stopAgainRevision: 7,
    });
    expect(result.operation?.raw).toEqual(operation);
  });

  it('S-054 never restates another operation or offers a final operation Stop again', () => {
    const answer = stoppedAnswer();
    const operation = own(answer['body'], 'operation');
    expect(
      classifyStopAnswer(
        { status: 409, body: { code: 'operation_changed', operation } },
        'different-operation',
      ),
    ).toMatchObject({ kind: 'not_applied', operation: null, stopAgainRevision: null });
    expect(
      classifyStopAnswer(
        { status: 409, body: { code: 'operation_changed', operation } },
        operationId(answer),
      ),
    ).toMatchObject({ kind: 'not_applied', stopAgainRevision: null });
  });

  it('S-055 partial reports this request effects and conflict without confirming stopped', () => {
    const answer = stoppedAnswer();
    const operation = { ...object(own(answer['body'], 'operation')), status: 'running' };
    const effects = { ...nothing, paused: true };
    const result = classifyStopAnswer(
      {
        status: 409,
        body: {
          code: 'stop_outcome_partial',
          error: 'raw error',
          conflict: 'raw conflict',
          operation,
          stop: { state: 'active', reason: null, by_this_request: effects },
        },
      },
      operationId(answer),
    );
    expect(result).toMatchObject({
      kind: 'partial',
      conflict: 'raw conflict',
      error: 'raw error',
      effects,
      stopAgainRevision: own(operation, 'revision'),
    });
  });

  it.each([
    null,
    { kind: 'timeout' },
    { status: 409, body: {} },
    { status: 409, body: null },
    { status: 409, body: 'invalid JSON' },
    { status: 409, body: { error: 'Operation changed; refresh its status before trying again' } },
    { status: 409, body: { code: 'new_code' } },
    { status: 500, body: { code: 'operation_changed', error: 'error' } },
    { status: 502, body: null },
    { status: 503, body: {} },
    { status: 200, body: {} },
  ])('S-056 treats every unproven answer as unknown %j', (answer) => {
    expect(classifyStopAnswer(answer, operationId(stoppedAnswer()))).toMatchObject({
      kind: 'unknown',
      stopAgainRevision: null,
      effects: null,
    });
  });

  it('S-056 offers Stop again only after a later read shows this operation active', () => {
    const harvest = sampleRecord('status.collecting');
    const operation = object(harvest['operation']);
    const target = operation['id'];
    if (typeof target !== 'string') throw new Error('id required');
    operation['revision'] = 7;
    expect(stopAgainAfterRead(target, answered(statusBody(harvest), 2), 1)).toBe(7);
    expect(stopAgainAfterRead(target, answered(statusBody(harvest), 1), 1)).toBeNull();
    operation['status'] = 'cancelled';
    expect(stopAgainAfterRead(target, answered(statusBody(harvest), 2), 1)).toBeNull();
    expect(stopAgainAfterRead(target, answered(statusBody(sample('status.off')), 2), 1)).toBeNull();
    expect(
      stopAgainAfterRead(target, answered(statusBody(sample('status.status_failed')), 2), 1),
    ).toBeNull();
  });

  it('S-057 action_lock keeps Stop as a defensive not-handled answer', () => {
    expect(
      classifyStopAnswer(
        { status: 409, body: { code: 'action_lock' } },
        operationId(stoppedAnswer()),
      ),
    ).toMatchObject({ kind: 'action_lock', keepStop: true, effects: null });
  });

  it('S-095 ignores inherited Stop fields and never stringifies server values', () => {
    const answer = stoppedAnswer();
    const inherited: unknown = Object.create(object(answer['body']));
    expect(classifyStopAnswer({ status: 200, body: inherited }, operationId(answer)).kind).toBe(
      'unknown',
    );
    expect(
      classifyStopAnswer({ status: 409, body: { code: { toString: null } } }, operationId(answer))
        .kind,
    ).toBe('unknown');
  });
});

describe('Dashboard Stop unknown outcome', () => {
  it('S-056 locks write_unknown_outcomes coverage including the separate input-owned cases', () => {
    expect(dashboardCaseNames('write_unknown_outcomes')).toEqual([
      'allow_input_cleared_the_revocation_then_the_save_raised',
      'config_read_after_the_failed_allow_input',
      'handoff_identical_retry_created_false',
      'handoff_recorded_then_the_answer_failed',
      'operation_read_after_the_failed_stop',
      'stop_paused_harvesting_then_the_cancel_raised',
    ]);
  });
  it('S-056 real Stop 500 after pausing harvesting stays unknown even when the operation read succeeds', () => {
    const answer = dashboardCase(
      'write_unknown_outcomes.stop_paused_harvesting_then_the_cancel_raised',
    );
    const readBody = dashboardBody('write_unknown_outcomes.operation_read_after_the_failed_stop');
    const operation = own(readBody, 'operation');
    const target = own(operation, 'id');
    if (typeof target !== 'string') throw new Error('operation id required');
    expect(answer.request.path).toBe(`/api/operations/${target}/cancel`);
    expect(answer.status).toBe(500);
    expect(answer.body).toHaveProperty('text', expect.any(String));
    const result = classifyStopAnswer(answer, target);
    expect(result).toMatchObject({ kind: 'unknown', effects: null, stopAgainRevision: null });
    const harvest = sampleRecord('status.off');
    harvest['operation'] = operation;
    expect(stopAgainAfterRead(target, answered(statusBody(harvest), 2), 1)).toBe(
      own(operation, 'revision'),
    );
    expect(result.kind).toBe('unknown');
    expect(
      classifyStopAnswer(
        dashboardCase('write_unknown_outcomes.operation_read_after_the_failed_stop'),
        target,
      ).kind,
    ).toBe('unknown');
  });
});
