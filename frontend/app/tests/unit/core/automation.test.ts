import { describe, expect, it } from 'vitest';
import { automationSlot, automationWord, holders } from '../../../src/core/automation.ts';
import { pauseOffered } from '../../../src/core/harvestChoice.ts';
import { gemSeen } from '../../../src/core/harvestView.ts';
import { answerRevisionClaims } from '../../../src/core/stopRevision.ts';
import {
  freshScan,
  sample,
  sampleRecord,
  serverTimeOf,
  statusBody,
} from '../../support/fixture.ts';
import { contextOf, readOf, rec, setPath } from '../../support/core.ts';

function slot(
  body: unknown,
  extraClaims: Parameters<typeof automationSlot>[0]['extraClaims'] = [],
) {
  return automationSlot({ status: contextOf(body), extraClaims, lastIdleAgeSec: null });
}

describe('S-027 idle needs positive evidence; retained or unverified workers read unknown', () => {
  const H = serverTimeOf(sample('status.off'));
  const cases: [string, () => unknown][] = [
    ['(a) worker_unverified, [], fresh', () => statusBody(sample('status.worker_unverified'))],
    [
      '(b) collecting with worker_alive null',
      () => {
        const harvest = sampleRecord('status.collecting');
        setPath(harvest, ['operation', 'worker_alive'], null);
        return statusBody(harvest);
      },
    ],
    ['(c) harvest omitted', () => ({ processes: [], process_refresh: freshScan(H) })],
    ['(c) harvest null', () => statusBody(null)],
    ['(c) harvest status_failed', () => statusBody(sample('status.status_failed'))],
    ['(c) harvest state_unreadable', () => statusBody(sample('status.state_unreadable'))],
    [
      '(c) version 2 with a retained operation',
      () => statusBody({ ...sampleRecord('status.stopping'), version: 2 }),
    ],
    [
      '(d) a fresh-labelled scan with age 180',
      () =>
        statusBody(sample('status.off'), {
          scan: { state: 'fresh', error: null, age_sec: 180, last_success_at: H - 180 },
        }),
    ],
  ];

  it.each(cases)(
    'S-027 %s reads state unknown, never idle or "no gem collection running"',
    (_label, build) => {
      const body = build();
      const view = slot(body);
      expect(automationWord(view).word).toBe('unknown');
      expect(gemSeen(contextOf(body)).word).not.toBe('idle');
      for (const row of view.rows) {
        expect(row.seen.word).not.toBe('idle');
      }
    },
  );

  it('S-027 a retained or unverified worker keeps its Stop and Pause while unknown', () => {
    const body = statusBody(sample('status.worker_unverified'));
    const view = slot(body);
    expect(view.rows.map((row) => row.stop.kind)).toEqual(['cancel']);
    expect(pauseOffered(readOf(body).harvest, null, [])).toBe(true);
  });

  it('S-027 a consistent but stale scan may say "last known idle, N s ago" (AS4)', () => {
    const body = statusBody(sample('status.off'), {
      scan: { state: 'stale', error: null, age_sec: 30, last_success_at: H - 30 },
    });
    const view = automationSlot({ status: contextOf(body), extraClaims: [], lastIdleAgeSec: 30 });
    expect(view.summary).toMatchObject({
      row: 'AS4',
      word: 'unknown',
      line: 'last_known_idle',
      params: { ageSec: 30 },
    });
  });

  it('S-027 control: status.off with [] and a fresh scan is idle (EV-5); a listed collector is running (EV-1)', () => {
    expect(slot(statusBody(sample('status.off'))).summary).toMatchObject({
      row: 'AS2',
      word: 'idle',
      evidence: 'EV-5',
    });
    const listed = slot(
      statusBody(sample('status.off'), {
        processes: [{ pid: 4101, created: 1.5, runner: 'gems' }],
      }),
    );
    expect(listed.rows.map((row) => [row.seen.row, row.seen.word, row.seen.evidence])).toEqual([
      ['A3', 'running', 'EV-1'],
    ]);
  });
});

describe('S-059 a retained holder always has a working Stop, and Pause while On may be in effect', () => {
  const refusal = sampleRecord('refusals.collector_stopping');

  function held(name: string, choice: 'off' | 'on', elapsed: number) {
    const harvest = sampleRecord(`status.${name}`);
    rec(harvest['desired'])['choice'] = choice;
    rec(harvest['desired'])['enabled'] = choice === 'on';
    harvest['input_revoked'] = true;
    harvest['server_time'] = (harvest['server_time'] as number) + elapsed;
    return statusBody(harvest);
  }

  it.each([
    ['stopping', 0],
    ['stopping', 3600],
    ['stopping', 86400],
    ['worker_unverified', 0],
    ['worker_unverified', 3600],
    ['worker_unverified', 86400],
  ] as const)(
    'S-059 %s with desired Off keeps a Stop with the newest revision at elapsed %i s',
    (name, elapsed) => {
      const body = held(name, 'off', elapsed);
      const view = slot(body, answerRevisionClaims(rec(refusal['body'])));
      const retained = holders(view);
      expect(retained).toHaveLength(1);
      const operation = rec(rec(rec(body)['harvest'])['operation']);
      const recovery = rec(rec(refusal['body'])['recovery'] ?? {});
      const newest = Math.max(
        operation['revision'] as number,
        recovery['operation_id'] === operation['id']
          ? (recovery['expected_revision'] as number)
          : -1,
      );
      expect(retained[0]?.stop).toEqual({
        kind: 'cancel',
        operationId: operation['id'],
        expectedRevision: newest,
      });
    },
  );

  it('S-059 control: with desired On, Pause is also present', () => {
    for (const name of ['stopping', 'worker_unverified']) {
      expect(pauseOffered(readOf(held(name, 'on', 0)).harvest, null, [])).toBe(true);
      expect(pauseOffered(readOf(held(name, 'off', 0)).harvest, null, [])).toBe(false);
    }
  });

  it('S-059 a holder with no valid revision anywhere keeps its row with Stop disabled "waiting for fresh status" (S-050)', () => {
    const harvest = sampleRecord('status.stopping');
    setPath(harvest, ['operation', 'revision'], 'x');
    const view = slot(statusBody(harvest));
    expect(holders(view)[0]?.stop).toMatchObject({
      kind: 'disabled',
      reason: 'waiting_for_fresh_status',
    });
  });
});
