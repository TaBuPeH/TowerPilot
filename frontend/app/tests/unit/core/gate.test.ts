import { describe, expect, it } from 'vitest';
import { confirmStopped } from '../../../src/core/evidence.ts';
import { evidenceGate, gateOf } from '../../../src/core/gate.ts';
import { pauseOffered, settlePause } from '../../../src/core/harvestChoice.ts';
import {
  answered,
  failed,
  freshScan,
  sample,
  sampleRecord,
  serverTimeOf,
  statusBody,
} from '../../support/fixture.ts';
import { readOf, rec } from '../../support/core.ts';

const answer = sampleRecord('post_harvest.off_ok_stopping');
const X = (rec(rec(answer['body'])['stop'])['stopping'] as string[])[0] ?? '';
const ANSWER_SEQ = 4;

/** The S-020 later read: Off at a higher revision, nothing recorded, fresh scan. */
function laterOff(): Record<string, unknown> {
  const harvest = sampleRecord('status.off');
  rec(harvest['desired'])['revision'] = 99;
  return statusBody(harvest);
}

function settle(body: unknown, startedSeq = 5): string {
  const gate = gateOf(readOf(body, startedSeq), ANSWER_SEQ);
  return gate.kind === 'known' ? settlePause([X], gate.value).kind : 'not_confirmed';
}

describe('S-020 stopped, free, idle, input stopped and no collector only through D-4 (and D-5)', () => {
  it('S-020 a Pause answered off_ok_stopping is stopped by a later complete Off read', () => {
    expect(X).toMatch(/\S/);
    expect(settle(laterOff())).toBe('stopped');
  });

  it('S-020 a read that started before the answer arrived does not count', () => {
    expect(settle(laterOff(), ANSWER_SEQ)).toBe('not_confirmed');
    expect(settle(laterOff(), ANSWER_SEQ - 1)).toBe('not_confirmed');
  });

  it('S-020 control: X still in processes, harvest.operation or harvest.owner keeps "still stopping"', () => {
    const inProcesses = laterOff();
    inProcesses['processes'] = [{ pid: 4242, operation_id: X, operation_revision: 4 }];
    const inOperation = laterOff();
    rec(inOperation['harvest'])['operation'] = rec(
      rec(rec(answer['body'])['harvest'])['operation'],
    );
    const inOwner = laterOff();
    rec(inOwner['harvest'])['owner'] = {
      kind: 'operation',
      operation_id: X,
      pid: null,
      status: 'cancelling',
    };
    for (const body of [inProcesses, inOperation, inOwner]) {
      expect(settle(body)).toBe('still_stopping');
    }
  });

  it('S-020 one gate serves every claim: confirmStopped needs the same D-4 read', () => {
    const gate = evidenceGate(answered(laterOff(), 5), ANSWER_SEQ);
    expect(gate.kind).toBe('known');
    if (gate.kind === 'known') {
      expect(confirmStopped({ operationId: X, pid: null }, gate.value).kind).toBe('known');
    }
    expect(evidenceGate(failed('http_502', 5), ANSWER_SEQ).kind).toBe('unknown');
  });
});

describe('S-021 absent means unknown, never empty', () => {
  const cases: [string, () => unknown][] = [
    [
      '(a) processes omitted',
      () => {
        const b = laterOff();
        delete b['processes'];
        return b;
      },
    ],
    [
      '(b) processes null',
      () => {
        const b = laterOff();
        b['processes'] = null;
        return b;
      },
    ],
    [
      '(c) the harvest.operation key removed',
      () => {
        const b = laterOff();
        delete rec(b['harvest'])['operation'];
        return b;
      },
    ],
    [
      '(d) the harvest.owner key removed',
      () => {
        const b = laterOff();
        delete rec(b['harvest'])['owner'];
        return b;
      },
    ],
    [
      '(e) harvest status_failed with a fresh empty list',
      () => statusBody(sample('status.status_failed')),
    ],
    [
      '(f) harvest omitted, scan stale',
      () => ({
        processes: [],
        process_refresh: { ...freshScan(serverTimeOf(sample('status.off'))), state: 'stale' },
      }),
    ],
    ['(g) a 200 whose body is not an object', () => 'not an object'],
    ['(g) a 200 whose body is an array', () => []],
  ];

  it.each(cases)('S-021 %s: not confirmed stopped', (_label, build) => {
    expect(settle(build())).toBe('not_confirmed');
  });

  it.each(cases.slice(4))('S-021 %s: Pause stays (S-001)', (_label, build) => {
    expect(pauseOffered(readOf(build()).harvest, null, [])).toBe(true);
  });

  it('S-021 control: the complete read of S-020 confirms', () => {
    expect(settle(laterOff())).toBe('stopped');
  });
});
