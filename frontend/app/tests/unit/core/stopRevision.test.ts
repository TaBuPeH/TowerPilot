import { describe, expect, it } from 'vitest';
import {
  answerRevisionClaims,
  harvestRevisionClaims,
  recoveryClaims,
  statusRevisionClaims,
  stopRevision,
} from '../../../src/core/stopRevision.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';

function target(answer: unknown): string {
  const value = ownPath(answer, 'body', 'operation', 'id');
  if (typeof value !== 'string') throw new Error('operation id required');
  return value;
}
describe('Stop revision selection', () => {
  it('S-050 (a) a Stop answer advances a rendered revision while the next status is held', () => {
    const answer = sample('stop_operation.cancel.stopping');
    const operationId = target(answer);
    expect(
      stopRevision(operationId, [
        { operationId, revision: 2 },
        ...answerRevisionClaims(own(answer, 'body')),
      ]),
    ).toBe(3);
  });
  it('S-050 (b) process and owner recovery revisions beat older harvest and answer revisions', () => {
    const answer = sample('stop_operation.cancel.stopping');
    const operationId = target(answer);
    const harvest = sampleRecord('status.off');
    harvest['operation'] = { id: operationId, revision: 2 };
    const body = statusBody(harvest, {
      processes: [
        {
          pid: 9009,
          operation_id: operationId,
          operation_revision: 7,
          operation: { id: operationId, revision: 6 },
        },
      ],
    });
    expect(
      stopRevision(operationId, [
        ...statusRevisionClaims(body),
        ...answerRevisionClaims(own(answer, 'body')),
      ]),
    ).toBe(7);
    harvest['owner'] = {
      kind: 'operation',
      operation_id: operationId,
      pid: null,
      recovery: { action: 'stop_operation', operation_id: operationId, expected_revision: 9 },
    };
    expect(
      stopRevision(operationId, [
        ...statusRevisionClaims(statusBody(harvest)),
        ...statusRevisionClaims(body),
        ...answerRevisionClaims(own(answer, 'body')),
      ]),
    ).toBe(9);
  });
  it('S-050 (c) repeat stopping_then_stopped sends the first answer revision', () => {
    const first = sample('stop_operation.repeat.stopping_then_stopped.first');
    const second = sample('stop_operation.repeat.stopping_then_stopped.second');
    const operationId = target(first);
    expect(target(second)).toBe(operationId);
    expect(
      stopRevision(operationId, [
        ...answerRevisionClaims(own(first, 'body')),
        { operationId, revision: 2 },
      ]),
    ).toBe(ownPath(first, 'body', 'operation', 'revision'));
    expect(
      stopRevision(operationId, [
        ...answerRevisionClaims(own(first, 'body')),
        ...answerRevisionClaims(own(second, 'body')),
      ]),
    ).toBe(ownPath(second, 'body', 'operation', 'revision'));
  });
  it('S-050 control ignores a different id at revision 99 and invalid revisions', () => {
    const first = sample('stop_operation.cancel.stopping');
    const operationId = target(first);
    const otherId = target(sample('stop_operation.cancel.stopped'));
    expect(otherId).not.toBe(operationId);
    expect(
      stopRevision(operationId, [
        { operationId: otherId, revision: 99 },
        { operationId, revision: 3 },
        ...[undefined, null, false, -1, 1.5, '7', NaN, Infinity, Number.MAX_SAFE_INTEGER + 1].map(
          (revision) => ({ operationId, revision }),
        ),
      ]),
    ).toBe(3);
    expect(stopRevision(operationId, [{ operationId: otherId, revision: 99 }])).toBeNull();
  });
  it('S-050 every source competes by highest same-id revision including nested row and notice recovery', () => {
    const first = sample('stop_operation.cancel.stopping');
    const operationId = target(first);
    const harvest = {
      operation: { id: operationId, revision: 8 },
      owner: {
        recovery: { action: 'stop_operation', operation_id: operationId, expected_revision: 9 },
      },
    };
    const body = {
      harvest,
      processes: [
        {
          operation_id: operationId,
          operation_revision: 7,
          operation: { id: operationId, revision: 10 },
        },
      ],
    };
    const notice = {
      recovery: { action: 'stop_operation', operation_id: operationId, expected_revision: 11 },
    };
    expect(
      stopRevision(operationId, [
        ...harvestRevisionClaims(harvest),
        ...statusRevisionClaims(body),
        ...recoveryClaims(notice),
        ...answerRevisionClaims(own(first, 'body')),
        { operationId, revision: 12 },
      ]),
    ).toBe(12);
    expect(stopRevision(operationId, [{ operationId, revision: 0 }])).toBe(0);
  });
});
