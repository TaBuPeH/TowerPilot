import { describe, expect, it } from 'vitest';
import {
  assessRefusalNotice,
  classifyRefusal,
  leaseHolderResolved,
  refusalRecovery,
} from '../../../src/core/outcomes/refusal.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { answered, sample, sampleNames, sampleRecord, statusBody } from '../../support/fixture.ts';
import { dashboardCase, dashboardCaseNames } from '../../support/dashboardFixture.ts';

function body(name: string): Record<string, unknown> {
  return own(sample(`refusals.${name}`), 'body') as Record<string, unknown>;
}
function ownerId(raw: unknown): string {
  const value = ownPath(raw, 'owner', 'operation_id');
  if (typeof value !== 'string') throw new Error('operation id required');
  return value;
}
function leaseOwner(): Record<string, unknown> {
  return {
    ...(own(body('device_owned_lease'), 'owner') as Record<string, unknown>),
    lease: {
      held: true,
      holders: [{ pid: ownPath(body('device_owned_process'), 'owner', 'pid'), role: 'primary' }],
    },
  };
}

describe('shared refusal classification', () => {
  const classes: Record<string, string> = {
    action_lock: 'busy',
    collector_running: 'operation_owner',
    collector_running_manual: 'operation_owner',
    collector_stopping: 'operation_owner',
    device_owned_collector: 'operation_owner',
    device_owned_lease: 'lease_holder',
    device_owned_pre_spawn: 'operation_owner',
    device_owned_process: 'process_owner',
    device_owned_run: 'operation_owner',
    device_owner_unreadable: 'owner_unreadable',
    reserved_fields: 'not_owner_notice',
    setup_battle_unresolved: 'not_owner_notice',
  };
  it.each(sampleNames('refusals'))(
    'S-070 classifies refusals.%s from body shape with the exact server recovery',
    (name) => {
      const raw = body(name);
      const result = classifyRefusal(raw);
      expect(result.kind).toBe(classes[name]);
      expect(result.error).toEqual(raw['error']);
      if (result.kind === 'operation_owner') {
        expect(result.operationId).toBe(ownPath(raw, 'owner', 'operation_id'));
        expect(result.status).toBe(ownPath(raw, 'owner', 'status'));
        expect(result.recovery.kind).toBe(
          own(raw['recovery'], 'action') === 'pause_harvesting' ? 'pause' : 'stop',
        );
      }
      if (result.kind === 'process_owner') {
        expect(result.pid).toBe(ownPath(raw, 'owner', 'pid'));
        expect(result.script).toEqual(ownPath(raw, 'owner', 'script'));
        expect(result.foreign).toBe(true);
        expect(result.recovery.kind).toBe('none');
      }
    },
  );
  it('S-070 automation input enable uses the same body handler', () => {
    const raw = own(sample('automation_access.enable_refused_owner'), 'body');
    expect(classifyRefusal(raw)).toMatchObject({
      kind: 'operation_owner',
      operationId: ownPath(raw, 'owner', 'operation_id'),
    });
  });
  it('S-070 operation shape wins over code and recovery null never becomes retry', () => {
    const raw = body('device_owned_run');
    raw['code'] = 'invalid_request';
    raw['recovery'] = null;
    expect(classifyRefusal(raw)).toMatchObject({
      kind: 'operation_owner',
      recovery: { kind: 'none' },
    });
    expect(classifyRefusal({ code: 'device_owned' }).kind).toBe('not_owner_notice');
    expect(classifyRefusal({ code: 'action_lock', owner: null }).kind).toBe('not_owner_notice');
  });
  it('S-070 stop recovery uses the maximum same-id revision without inventing one', () => {
    const raw = body('device_owned_run');
    const operationId = ownerId(raw);
    expect(
      refusalRecovery(raw, [
        { operationId, revision: 20 },
        { operationId: 'another-id', revision: 99 },
      ]),
    ).toEqual({ kind: 'stop', operationId, revision: 20 });
    raw['recovery'] = { action: 'stop_operation', operation_id: operationId };
    expect(refusalRecovery(raw)).toEqual({
      kind: 'stop',
      operationId,
      revision: ownPath(raw, 'owner', 'recovery', 'expected_revision'),
    });
    raw['owner'] = { ...(own(raw, 'owner') as Record<string, unknown>), recovery: null };
    expect(refusalRecovery(raw)).toEqual({ kind: 'stop', operationId, revision: null });
    expect(refusalRecovery({ recovery: { action: 'retry' } })).toEqual({ kind: 'none' });
  });
  it('S-070 a newer same-id nested owner recovery revision beats the top-level recovery', () => {
    const raw = body('device_owned_run');
    const operationId = ownerId(raw);
    raw['owner'] = {
      ...(own(raw, 'owner') as Record<string, unknown>),
      recovery: { action: 'stop_operation', operation_id: operationId, expected_revision: 21 },
    };
    expect(refusalRecovery(raw)).toEqual({ kind: 'stop', operationId, revision: 21 });
    raw['owner'] = {
      ...(own(raw, 'owner') as Record<string, unknown>),
      recovery: {
        action: 'stop_operation',
        operation_id: 'another-operation',
        expected_revision: 99,
      },
    };
    expect(refusalRecovery(raw)).toEqual({
      kind: 'stop',
      operationId,
      revision: ownPath(raw, 'recovery', 'expected_revision'),
    });
  });
  it('S-071 pre-spawn names the accepted operation and its Stop with the newest revision', () => {
    const raw = body('device_owned_pre_spawn');
    const operationId = ownerId(raw);
    expect(classifyRefusal(raw, [{ operationId, revision: 8 }])).toMatchObject({
      kind: 'operation_owner',
      operationId,
      pid: null,
      status: 'accepted',
      starting: true,
      recovery: { kind: 'stop', operationId, revision: 8 },
    });
  });
  it('S-072 unreadable owner shows raw message and a plain retry only with no free claim', () => {
    const raw = body('device_owner_unreadable');
    const notice = classifyRefusal(raw);
    expect(notice).toEqual({
      kind: 'owner_unreadable',
      error: raw['error'],
      recovery: { kind: 'retry' },
    });
    const result = assessRefusalNotice(notice, answered(statusBody(sample('status.off')), 2), 1);
    expect(result.free.kind).toBe('unknown');
    expect(result.stopped.kind).toBe('unknown');
    expect(result.canRetry).toBe(false);
    expect(result.recovery.kind).toBe('retry');
    const error = { toString: null };
    expect(classifyRefusal({ ...raw, error })).toMatchObject({ error });
  });
  it('S-073 reports fixture holders with roles or an unidentified lease holder when absent', () => {
    const raw = body('device_owned_lease');
    const result = classifyRefusal(raw);
    expect(result.kind).toBe('lease_holder');
    if (result.kind !== 'lease_holder') throw new Error('lease holder required');
    const holders = ownPath(raw, 'owner', 'lease', 'holders');
    expect(result.holders).toEqual(holders ?? []);
    expect(result.unidentified).toBe(
      holders === undefined || (Array.isArray(holders) && holders.length === 0),
    );
    expect(result.recovery.kind).toBe('none');
  });
  it('S-073 a listed holder resolves only after a later D-4 read without its PID or lease owner', () => {
    const owner = leaseOwner();
    const off = sample('status.off');
    expect(leaseHolderResolved(owner, answered(statusBody(off), 2), 1)).toBe(true);
    expect(leaseHolderResolved(owner, answered(statusBody(off), 1), 1)).toBe(false);
    const holderPid = ownPath(owner, 'lease', 'holders');
    const holders = holderPid as readonly { pid: number }[];
    expect(
      leaseHolderResolved(
        owner,
        answered(statusBody(off, { processes: [{ pid: holders[0]?.pid }] }), 2),
        1,
      ),
    ).toBe(false);
    const leased = sampleRecord('status.off');
    leased['owner'] = owner;
    expect(leaseHolderResolved(owner, answered(statusBody(leased), 2), 1)).toBe(false);
    expect(
      leaseHolderResolved(owner, answered(statusBody(sample('status.status_failed')), 2), 1),
    ).toBe(false);
    expect(
      leaseHolderResolved(
        owner,
        answered(statusBody(off, { scan: { state: 'fresh', age_sec: 180 } }), 2),
        1,
      ),
    ).toBe(false);
  });
  it.each([undefined, [], [{}], [{ pid: '4242' }], [{ pid: 4242 }, {}]])(
    'S-073 absent empty or malformed holder list %j never proves gone',
    (holders) => {
      const owner = { ...leaseOwner(), lease: { held: true, holders } };
      const notice = classifyRefusal({ owner, recovery: null });
      expect(leaseHolderResolved(owner, answered(statusBody(sample('status.off')), 2), 1)).toBe(
        false,
      );
      expect(
        assessRefusalNotice(notice, answered(statusBody(sample('status.off')), 2), 1),
      ).toMatchObject({ free: { kind: 'unknown' }, canRetry: false });
    },
  );
  it('S-073 listed lease resolution supplies the unresolved-holder input for free claims', () => {
    const notice = classifyRefusal({ owner: leaseOwner() });
    const result = assessRefusalNotice(notice, answered(statusBody(sample('status.off')), 2), 1);
    expect(result.free.kind).toBe('known');
    expect(result.canRetry).toBe(true);
    expect(
      assessRefusalNotice(
        notice,
        answered(statusBody(sample('status.off')), 2),
        1,
        [],
        [
          {
            owner: { kind: 'process', pid: null, lease: { held: true, holders: [] } },
            learnedSeq: 1,
          },
        ],
      ).free.kind,
    ).toBe('unknown');
  });

  it('S-074 Pause recovery stays unresolved on unreadable desire and confirms with complete Off', () => {
    const notice = classifyRefusal(body('device_owned_collector'));
    expect(
      assessRefusalNotice(notice, answered(statusBody(sample('status.status_failed')), 2), 1),
    ).toMatchObject({ stopped: { kind: 'unknown' }, free: { kind: 'unknown' }, canRetry: false });
    expect(
      assessRefusalNotice(notice, answered(statusBody(sample('status.off')), 2), 1),
    ).toMatchObject({ stopped: { kind: 'known' }, free: { kind: 'known' }, canRetry: true });
    expect(
      assessRefusalNotice(notice, answered(statusBody(sample('status.starting_due')), 2), 1),
    ).toMatchObject({ stopped: { kind: 'unknown' }, canRetry: false, recovery: { kind: 'pause' } });
  });
  it('S-074 a replacing owner is named with its own Stop and never called free', () => {
    const notice = classifyRefusal(body('device_owned_collector'));
    const harvest = sampleRecord('status.owned_by_run_gathers');
    harvest['desired'] = own(sample('status.off'), 'desired');
    const replacement = own(harvest, 'owner');
    const operationId = own(replacement, 'operation_id');
    const pid = own(replacement, 'pid');
    const result = assessRefusalNotice(
      notice,
      answered(statusBody(harvest, { processes: [{ pid, operation_id: operationId }] }), 2),
      1,
    );
    expect(result.stopped.kind).toBe('known');
    expect(result.free.kind).toBe('unknown');
    expect(result.canRetry).toBe(false);
    expect(result.currentOwner).toMatchObject({
      kind: 'operation_owner',
      operationId,
      recovery: { kind: 'stop', operationId },
    });
    expect(result.recovery.kind).toBe('stop');
  });
  it.each(['stopping', 'worker_unverified'])(
    'S-074 retained Off %s uses current Stop recovery at every elapsed time',
    (name) => {
      const raw = body('collector_stopping');
      const notice = classifyRefusal(raw);
      const operationId = ownerId(raw);
      for (const elapsed of [0, 3600, 86400]) {
        const harvest = sampleRecord(`status.${name}`);
        const operation = {
          ...(own(harvest, 'operation') as Record<string, unknown>),
          id: operationId,
          revision: 9,
        };
        harvest['operation'] = operation;
        harvest['owner'] = null;
        harvest['desired'] = own(sample('status.off'), 'desired');
        harvest['input_revoked'] = true;
        const time = own(harvest, 'server_time');
        if (typeof time !== 'number') throw new Error('time required');
        harvest['server_time'] = time + elapsed;
        const result = assessRefusalNotice(notice, answered(statusBody(harvest), 2), 1);
        expect(result).toMatchObject({
          stopped: { kind: 'unknown' },
          free: { kind: 'unknown' },
          canRetry: false,
          recovery: { kind: 'stop', operationId, revision: 9 },
        });
      }
    },
  );
  it.each(['processes', 'harvest'])('S-074 absent %s never proves stopped free or retry', (key) => {
    const raw = Object.fromEntries(
      Object.entries(statusBody(sample('status.off'))).filter(([name]) => name !== key),
    );
    const notice = classifyRefusal(body('device_owned_collector'));
    expect(assessRefusalNotice(notice, answered(raw, 2), 1)).toMatchObject({
      stopped: { kind: 'unknown' },
      free: { kind: 'unknown' },
      canRetry: false,
    });
  });
  it('S-074 a retained managed collector with choice On keeps Pause recovery', () => {
    const raw = body('collector_stopping');
    const harvest = sampleRecord('status.stopping');
    harvest['operation'] = {
      ...(own(harvest, 'operation') as Record<string, unknown>),
      id: ownerId(raw),
    };
    expect(
      assessRefusalNotice(classifyRefusal(raw), answered(statusBody(harvest), 2), 1),
    ).toMatchObject({ canRetry: false, recovery: { kind: 'pause' } });
  });
  it('S-074 pre-spawn cannot resolve over an unidentified unlinked process', () => {
    const notice = classifyRefusal(body('device_owned_pre_spawn'));
    expect(
      assessRefusalNotice(
        notice,
        answered(statusBody(sample('status.off'), { processes: [{ pid: 9009 }] }), 2),
        1,
      ),
    ).toMatchObject({ stopped: { kind: 'unknown' }, canRetry: false });
  });
  it('S-095 inherited owner fields and non-string operation ids never match', () => {
    const owner: unknown = Object.create({ kind: 'operation', operation_id: 'inherited-id' });
    expect(classifyRefusal({ owner }).kind).toBe('not_owner_notice');
    expect(
      classifyRefusal({ owner: { kind: 'operation', operation_id: { toString: null } } }).kind,
    ).toBe('not_owner_notice');
  });
});

describe('Dashboard write refusal body shapes', () => {
  const families = ['control', 'control_unknown_outcomes', 'handoff', 'write_unknown_outcomes'];
  const cases = families
    .flatMap((family) => dashboardCaseNames(family).map((name) => `${family}.${name}`))
    .filter((path) => {
      const answer = dashboardCase(path);
      return answer.request.method !== 'GET' && answer.status >= 400;
    });
  it.each(cases)(
    'S-070 real %s uses body shape rather than an owner-looking code or error text',
    (path) => {
      const answer = dashboardCase(path);
      const notice = classifyRefusal(answer.body);
      const expected =
        path === 'control.action_lock'
          ? 'busy'
          : path === 'handoff.refused_device_owner_unreadable'
            ? 'owner_unreadable'
            : 'not_owner_notice';
      expect(notice.kind).toBe(expected);
      expect(notice.error).toEqual(own(answer.body, 'error'));
      expect(notice.recovery.kind).toBe(expected === 'owner_unreadable' ? 'retry' : 'none');
    },
  );
  it('S-072 handoff owner-unreadable gives a plain retry without Pause Stop or free', () => {
    const answer = dashboardCase('handoff.refused_device_owner_unreadable');
    expect(classifyRefusal(answer.body)).toEqual({
      kind: 'owner_unreadable',
      error: own(answer.body, 'error'),
      recovery: { kind: 'retry' },
    });
  });
  it('S-070 handoff device_owned without an owner record never invents a process owner from the message', () => {
    const answer = dashboardCase('handoff.refused_device_owned');
    expect(answer.body).toHaveProperty('code', 'device_owned');
    expect(answer.body).not.toHaveProperty('owner');
    expect(classifyRefusal(answer.body)).toMatchObject({
      kind: 'not_owner_notice',
      recovery: { kind: 'none' },
    });
  });
});
