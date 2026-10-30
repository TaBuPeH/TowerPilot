import { describe, expect, it } from 'vitest';
import { automationSlot } from '../../../src/core/automation.ts';
import { confirmFree, confirmStopped } from '../../../src/core/evidence.ts';
import { gateOf } from '../../../src/core/gate.ts';
import { settlePause } from '../../../src/core/harvestChoice.ts';
import { gameInputSeen, inputOffOffered } from '../../../src/core/input.ts';
import {
  ownerGone,
  ownerIdentity,
  readOwnerRecord,
  stopTarget,
  type OwnerIdentity,
} from '../../../src/core/ownership.ts';
import { configRead, sampleRecord, statusBody } from '../../support/fixture.ts';
import { contextOf, gateReadOf, readOf, rec } from '../../support/core.ts';

const answer = sampleRecord('post_harvest.off_ok_stopping');
const X = (rec(rec(answer['body'])['stop'])['stopping'] as string[])[0] ?? '';

function laterOff(): Record<string, unknown> {
  const harvest = sampleRecord('status.off');
  rec(harvest['desired'])['revision'] = 99;
  return statusBody(harvest);
}

function settle(body: unknown): string {
  const gate = gateOf(readOf(body, 5), 4);
  return gate.kind === 'known' ? settlePause([X], gate.value).kind : 'not_confirmed';
}

describe('S-022 one malformed ownership record makes the whole read unusable (D-3)', () => {
  const processes: [string, unknown][] = [
    ['[{}]', [{}]],
    ['[[]]', [[]]],
    ['[null]', [null]],
    ['[{pid:"9009"}]', [{ pid: '9009' }]],
    ['[{pid:0}]', [{ pid: 0 }]],
    ['[{pid:1.5}]', [{ pid: 1.5 }]],
    ['[{pid:9009, operation_id:7}]', [{ pid: 9009, operation_id: 7 }]],
    ['[{pid:9009, operation_id:""}]', [{ pid: 9009, operation_id: '' }]],
    [
      '[{pid:9009, operation_id:"x", operation_revision:null}]',
      [{ pid: 9009, operation_id: 'x', operation_revision: null }],
    ],
  ];
  const records: [string, 'operation' | 'owner', unknown][] = [
    ['harvest.operation {}', 'operation', {}],
    ['harvest.operation []', 'operation', []],
    ['harvest.operation "x"', 'operation', 'x'],
    ['harvest.owner {}', 'owner', {}],
    ['harvest.owner []', 'owner', []],
    ["harvest.owner {kind:'operation'}", 'owner', { kind: 'operation' }],
  ];

  it.each(processes)('S-022 processes %s: not confirmed stopped', (_label, value) => {
    const body = laterOff();
    body['processes'] = value;
    expect(settle(body)).toBe('not_confirmed');
  });

  it.each(records)('S-022 %s: not confirmed stopped', (_label, key, value) => {
    const body = laterOff();
    rec(body['harvest'])[key] = value;
    expect(settle(body)).toBe('not_confirmed');
  });

  it('S-022 control: explicit [], null, null confirms; X in harvest.operation stays unconfirmed', () => {
    expect(settle(laterOff())).toBe('stopped');
    const withX = laterOff();
    rec(withX['harvest'])['operation'] = rec(rec(rec(answer['body'])['harvest'])['operation']);
    expect(settle(withX)).toBe('still_stopping');
  });
});

describe('S-024 a PID match is judged by D-5', () => {
  const processOwner = readOwnerRecord(
    rec(sampleRecord('refusals.device_owned_process')['body'])['owner'],
  );
  const preSpawn = readOwnerRecord(
    rec(sampleRecord('refusals.device_owned_pre_spawn')['body'])['owner'],
  );

  function gone(owner: OwnerIdentity, processes: unknown, harvestOwner: unknown = null): boolean {
    const body = laterOff();
    body['processes'] = processes;
    rec(body['harvest'])['owner'] = harvestOwner;
    return ownerGone(owner, gateReadOf(body).ownership);
  }

  it('S-024 the fixture owners are D-3-valid (a process owner with its pid, a pre-spawn operation owner with pid null)', () => {
    expect(processOwner).toMatchObject({ kind: 'process' });
    expect(preSpawn).toMatchObject({ kind: 'operation', pid: null });
  });

  it('S-024 a process owner is kept by a row with its PID and ruled out by a different PID', () => {
    const owner: OwnerIdentity = { operationId: null, pid: 4242 };
    expect(gone(owner, [{ pid: 4242 }])).toBe(false);
    expect(gone(owner, [{ pid: 4243 }])).toBe(true);
  });

  it('S-024 an operation owner with pid null is gone when nothing records it', () => {
    if (preSpawn === null || preSpawn === undefined) throw new Error('fixture owner');
    expect(gone(ownerIdentity(preSpawn), [])).toBe(true);
    expect(gone(ownerIdentity(preSpawn), [{ pid: 5000 }])).toBe(false);
  });

  it('S-024 a row with the owner PID but another operation id is a different owner: X gone, Y named (S-026)', () => {
    const owner: OwnerIdentity = { operationId: 'X', pid: 4242 };
    const body = laterOff();
    body['processes'] = [{ pid: 4242, operation_id: 'Y' }];
    const gate = gateReadOf(body);
    expect(ownerGone(owner, gate.ownership)).toBe(true);
    expect(gate.ownership.processes.map((row) => row.operationId)).toEqual(['Y']);
    expect(confirmFree(gate, []).kind).toBe('unknown');
  });

  it('S-024 an owner with neither an id nor a PID is never gone', () => {
    expect(gone({ operationId: null, pid: null }, [])).toBe(false);
  });
});

describe('S-026 free means no owner of any kind; a replacing owner is named with its own recovery', () => {
  const refusal = sampleRecord('refusals.device_owned_collector');
  const collectorA = ownerIdentity(
    readOwnerRecord(rec(refusal['body'])['owner']) ?? { kind: 'process', pid: 1, raw: null },
  );
  const runB = rec(sampleRecord('status.owned_by_run_gathers')['owner']);

  it('S-026 A stopped while run B holds the emulator: "A has stopped; now used by B", never free', () => {
    const body = laterOff();
    body['processes'] = [{ pid: 7001, operation_id: runB['operation_id'], operation_revision: 2 }];
    rec(body['harvest'])['owner'] = runB;
    const gate = gateReadOf(body);
    expect(confirmStopped(collectorA, gate).kind).toBe('known');
    expect(confirmFree(gate, []).kind).toBe('unknown');
    const named = gate.ownership.owner;
    expect(named === null ? null : ownerIdentity(named)).toEqual({
      operationId: runB['operation_id'],
      pid: runB['pid'],
    });
    const view = automationSlot({ status: contextOf(body), extraClaims: [], lastIdleAgeSec: null });
    expect(view.rows.map((row) => row.stop)).toContainEqual({
      kind: 'cancel',
      operationId: runB['operation_id'],
      expectedRevision: 2,
    });
  });

  it('S-026 control: without B the emulator is free', () => {
    const gate = gateReadOf(laterOff());
    expect(confirmStopped(collectorA, gate).kind).toBe('known');
    expect(confirmFree(gate, []).kind).toBe('known');
  });

  it('S-026 a lease holder this page knows of keeps the emulator from being free (S-073)', () => {
    expect(
      confirmFree(gateReadOf(laterOff(), null, 3), [
        {
          owner: { kind: 'process', pid: null, lease: { held: true, holders: [] } },
          learnedSeq: 2,
        },
      ]).kind,
    ).toBe('unknown');
  });
});

describe('S-061 a linked row stops through its operation; malformed identity disables Stop', () => {
  it('S-061 row {pid:9009, operation_id:7}: Stop disabled with "can\'t identify the operation", Turn input off offered', () => {
    const row = { pid: 9009, operation_id: 7 };
    expect(stopTarget(row)).toEqual({
      kind: 'disabled',
      reason: 'operation_unidentified',
      pid: 9009,
    });
    const body = laterOff();
    body['processes'] = [row];
    const facts = {
      config: configRead(false),
      status: readOf(body),
      enables: [],
      turnOff: null,
      leaseHolders: [],
      retainedHolder: false,
    };
    expect(inputOffOffered(facts, gameInputSeen(facts))).not.toBeNull();
  });

  it('S-061 control: a valid linked row posts cancel for its operation with its revision', () => {
    const row = {
      pid: 9010,
      operation_id: 'op-1',
      operation_revision: 3,
      operation: { id: 'op-1', revision: 4 },
    };
    expect(stopTarget(row)).toEqual({ kind: 'operation', operationId: 'op-1', rowRevision: 4 });
    const body = laterOff();
    body['processes'] = [row];
    const view = automationSlot({ status: contextOf(body), extraClaims: [], lastIdleAgeSec: null });
    expect(view.rows.map((entry) => entry.stop)).toEqual([
      { kind: 'cancel', operationId: 'op-1', expectedRevision: 4 },
    ]);
  });

  it('S-061 a linked row with a valid id but a null revision still stops through its operation', () => {
    const row = { pid: 9012, operation_id: 'op-2', operation_revision: null };
    expect(stopTarget(row)).toEqual({ kind: 'operation', operationId: 'op-2', rowRevision: null });
  });

  it('S-061 a linked row whose operation names another id is malformed', () => {
    expect(stopTarget({ pid: 9011, operation_id: 'a', operation: { id: 'b' } })).toMatchObject({
      kind: 'disabled',
      reason: 'operation_unidentified',
    });
  });
});
