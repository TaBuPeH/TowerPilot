import { describe, expect, it } from 'vitest';
import { ownPath } from '../../../src/core/read.ts';
import { serverTextContent, type ServerText } from '../../../src/copy/server.ts';
import { sampleNames } from '../../support/fixture.ts';
import { stopAnswerLine } from '../../support/stopAnswerLine.ts';
import { requireR6Fixture } from './requireR6.ts';

requireR6Fixture();

/**
 * B-03 on the r6 contract: the paired ended replies stay distinct, and every
 * held, unreadable and partial reply keeps its per-request effects, holders,
 * reason and conflict through parse, core and store.
 */
const text = (value: ServerText | null | undefined) =>
  value === null || value === undefined ? null : serverTextContent(value);

describe('B-03 r6 Stop outcomes through parse, core and store', () => {
  it('S-052 ended and ended_after_termination stay distinct: signalled [] vs the pids this Stop signalled', () => {
    const ended = stopAnswerLine('cancel.ended');
    const terminated = stopAnswerLine('cancel.ended_after_termination');
    expect(ended.wire.contract).toBe('exact');
    expect(terminated.wire.contract).toBe('exact');
    expect(ended.line.detail).toMatchObject({
      kind: 'stop',
      class: 'ended',
      endedAttribution: 'unattributed',
      effects: { signalled: [], requested: true, paused: false, released: false, family: null },
    });
    expect(terminated.line.detail).toMatchObject({
      kind: 'stop',
      class: 'ended',
      endedAttribution: 'this_stop_signalled',
      effects: {
        signalled: ownPath(terminated.wire.body, 'stop', 'by_this_request', 'signalled'),
        family: 'stopped',
      },
    });
    if (terminated.line.detail.kind !== 'stop') throw new Error('a Stop detail');
    expect(terminated.line.detail.effects?.signalled.length).toBeGreaterThan(0);
    expect(ended.line.detail).not.toEqual(terminated.line.detail);
  });

  it('S-053 unverified_lease_held names each holder with its pid and role', () => {
    const { wire, line } = stopAnswerLine('cancel.unverified_lease_held');
    const holders = ownPath(wire.body, 'stop', 'holder', 'lease', 'holders');
    expect(Array.isArray(holders) && holders.length > 0).toBe(true);
    if (line.detail.kind !== 'stop') throw new Error('a Stop detail');
    expect(line.detail.holders.map((holder) => [holder.pid, text(holder.role)])).toEqual(
      (holders as Record<string, unknown>[]).map((holder) => [holder['pid'], holder['role']]),
    );
    expect(line.detail).toMatchObject({
      class: 'unverified',
      unverifiedReason: 'worker_not_verifiable_lease_held',
      holderForeign: true,
      turnInputOff: true,
    });
  });

  it.each(['unverified_lease_held_after_stop', 'unverified_lease_unreadable_after_stop'])(
    'S-053 %s keeps the effects of this request and offers Turn input off',
    (name) => {
      const { wire, line } = stopAnswerLine(`cancel.${name}`);
      expect(line.detail).toMatchObject({
        kind: 'stop',
        class: 'unverified',
        unverifiedReason: ownPath(wire.body, 'stop', 'reason'),
        turnInputOff: true,
        effects: ownPath(wire.body, 'stop', 'by_this_request'),
      });
    },
  );

  it('S-055 stop_outcome_partial: the effects, the conflict as sent, the current operation and Stop again', () => {
    const { wire, operationId, line } = stopAnswerLine('conflicts.stop_outcome_partial');
    expect(wire.contract).toBe('exact');
    expect(line.outcome).toBe('partial');
    if (line.detail.kind !== 'stop' || line.detail.class !== 'partial') {
      throw new Error('a partial Stop detail');
    }
    expect(text(line.detail.conflict)).toBe(ownPath(wire.body, 'conflict'));
    expect(line.detail.effects).toEqual(ownPath(wire.body, 'stop', 'by_this_request'));
    expect(line.detail.operation).toMatchObject({
      status: ownPath(wire.body, 'operation', 'status'),
      revision: ownPath(wire.body, 'operation', 'revision'),
    });
    expect(text(line.error)).toBe(ownPath(wire.body, 'error'));
    expect(line.stopAgain).toEqual({
      kind: 'cancel',
      operationId,
      expectedRevision: ownPath(wire.body, 'operation', 'revision'),
      epoch: 0,
    });
  });

  it.each(
    sampleNames('stop_operation.conflicts').filter((name) => name !== 'stop_outcome_partial'),
  )('S-054 conflicts.%s is a not-applied Stop with its code', (name) => {
    const { line } = stopAnswerLine(`conflicts.${name}`);
    expect(line).toMatchObject({ outcome: 'not_applied', phase: 'refused' });
    expect(line.detail).toMatchObject({ kind: 'stop', class: 'not_applied', code: name });
  });
});
