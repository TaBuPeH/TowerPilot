import { describe, expect, it } from 'vitest';
import { ownPath } from '../../../src/core/read.ts';
import { serverTextContent, type ServerText } from '../../../src/copy/server.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sampleNames } from '../../support/fixture.ts';
import { stopAnswerLine } from '../../support/stopAnswerLine.ts';
import { storeHarness } from '../../support/storeHarness.ts';

/**
 * B-03: the outcome line of a Stop keeps every distinction core's class
 * carries (S-051 to S-057): the matched operation with its status, revision
 * and error, the unverified reason, the holder, and whether Turn input off is
 * offered beside Stop again; server strings as ServerText. These samples
 * exist in both fixtures; the r6-only distinctions (per-request effects,
 * holders, conflicts) are in tests/unit/r6/stopOutcomeVM.r6.test.ts.
 */
const text = (value: ServerText | null | undefined) =>
  value === null || value === undefined ? null : serverTextContent(value);

describe('B-03 the Stop outcome view model keeps the classifier detail', () => {
  it.each([
    ['unverified_lease_held', 'worker_not_verifiable_lease_held', true],
    ['unverified_lease_unreadable', 'worker_not_verifiable_lease_unreadable', true],
    ['unverified_lease_free', 'worker_not_verifiable_lease_free', false],
  ] as const)(
    'S-053 cancel.%s: unverified (%s) with the operation; Turn input off offered: %s',
    (name, reason, turnInputOff) => {
      const { wire, operationId, line } = stopAnswerLine(`cancel.${name}`);
      expect(wire.contract).toBe('exact');
      expect(line).toMatchObject({ kind: 'stop', outcome: 'unverified', current: true });
      expect(line.detail).toMatchObject({
        kind: 'stop',
        class: 'unverified',
        unverifiedReason: reason,
        turnInputOff,
        endedAttribution: 'unattributed',
      });
      if (line.detail.kind !== 'stop') throw new Error('a Stop detail');
      expect(text(line.detail.operation?.id)).toBe(operationId);
      expect(line.detail.operation).toMatchObject({
        status: 'cancelling',
        revision: ownPath(wire.body, 'operation', 'revision'),
      });
      const foreign = ownPath(wire.body, 'stop', 'holder', 'foreign');
      expect(line.detail.holderForeign).toBe(typeof foreign === 'boolean' ? foreign : null);
    },
  );

  it('S-052 cancel.ended: ended, with the operation error code and message as sent', () => {
    const { wire, line } = stopAnswerLine('cancel.ended');
    expect(line.detail).toMatchObject({ kind: 'stop', class: 'ended', unverifiedReason: null });
    if (line.detail.kind !== 'stop') throw new Error('a Stop detail');
    expect(line.detail.operation?.status).toBe(ownPath(wire.body, 'operation', 'status'));
    const error = ownPath(wire.body, 'operation', 'error');
    expect(text(line.detail.operation?.errorCode)).toBe(
      typeof ownPath(error, 'code') === 'string' ? ownPath(error, 'code') : null,
    );
    expect(text(line.detail.operation?.errorMessage)).toBe(
      typeof ownPath(error, 'message') === 'string' ? ownPath(error, 'message') : null,
    );
  });

  it.each(
    ['stopped', 'stopping', 'released', 'active'].filter((name) =>
      sampleNames('stop_operation.cancel').includes(name),
    ),
  )('S-051 cancel.%s: its own class, never attributed to an ended operation', (name) => {
    const { line } = stopAnswerLine(`cancel.${name}`);
    expect(line.outcome).toBe(name);
    expect(line.detail).toMatchObject({
      kind: 'stop',
      class: name,
      endedAttribution: 'unattributed',
      unverifiedReason: null,
      turnInputOff: false,
    });
  });

  it('the answer is dated (its age by the store clock) and bound to this context', () => {
    const { line } = stopAnswerLine('cancel.stopping');
    expect(line.answeredAgeSec).toBe(0);
    expect(line.bound).toBe(true);
  });

  it('a Stop without an answer has no detail: in flight, then no answer', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.intent({ id: 'stop', kind: 'stop', meta: { operationId: 'op-1', expectedRevision: 1 } });
    expect(selectOutcomes(h.state()).lines[0]).toMatchObject({
      outcome: 'sending',
      detail: { kind: 'none' },
      answeredAgeSec: null,
    });
    h.intent({
      id: 'stop',
      kind: 'stop',
      meta: { operationId: 'op-1', expectedRevision: 1 },
      phase: 'unconfirmed',
      lostReason: 'network',
    });
    expect(selectOutcomes(h.state()).lines[0]).toMatchObject({
      outcome: 'no_answer',
      lost: 'network',
      detail: { kind: 'none' },
    });
  });
});
