import { describe, expect, it } from 'vitest';
import type { ConfigRead } from '../../../src/core/config.ts';
import { gameInputSeen, inputOffOffered, type GameInputFacts } from '../../../src/core/input.ts';
import {
  configRead,
  freshScan,
  sample,
  sampleRecord,
  serverTimeOf,
  statusBody,
} from '../../support/fixture.ts';
import { readOf } from '../../support/core.ts';

function facts(body: unknown, config: ConfigRead = configRead(false)): GameInputFacts {
  return {
    config,
    status: readOf(body),
    enables: [],
    turnOff: null,
    leaseHolders: [],
    retainedHolder: false,
  };
}

function offered(f: GameInputFacts) {
  return inputOffOffered(f, gameInputSeen(f));
}

describe('S-006 Turn input off shown with a plain reason whenever input may be on or unknown', () => {
  it('S-006 worker_unverified with taps off: shown, "automation may still be running; input revocation not confirmed"', () => {
    expect(offered(facts(statusBody(sample('status.worker_unverified'))))).toBe(
      'automation_may_run',
    );
  });

  const harvestH = serverTimeOf(sample('status.off'));
  const cases: [string, unknown, ConfigRead?][] = [
    ['harvest omitted', { processes: [], process_refresh: freshScan(harvestH) }],
    ['harvest status_failed', statusBody(sample('status.status_failed'))],
    ['harvest version 2', statusBody({ ...sampleRecord('status.off'), version: 2 })],
    [
      'a fresh-labelled scan with age 180',
      statusBody(sample('status.off'), {
        scan: { state: 'fresh', error: null, age_sec: 180, last_success_at: harvestH - 180 },
      }),
    ],
    ['a config without allow_taps', statusBody(sample('status.off')), configRead(undefined)],
  ];

  it.each(cases)('S-006 %s: shown with a reason', (_label, body, config) => {
    const reason = offered(facts(body, config));
    expect(reason).not.toBeNull();
  });

  it("S-006 a server with no harvest status at all says it doesn't report whether input is revoked", () => {
    expect(offered(facts({ processes: [], process_refresh: freshScan(harvestH) }))).toBe(
      'harvest_not_reported',
    );
  });

  it('S-006 an enable pending or not confirmed by a read keeps it (S-048)', () => {
    const f = {
      ...facts(statusBody(sample('status.input_revoked'))),
      enables: [{ sentSeq: 1, settled: false }],
    };
    expect(offered(f)).toBe('enable_unresolved');
  });

  it('S-006 the Setting Allowed always shows it, as the ordinary way to turn input off', () => {
    expect(offered(facts(statusBody(sample('status.input_revoked')), configRead(true)))).toBe(
      'taps_allowed',
    );
  });

  it('S-006 control: Off with input_revoked true, nothing recorded, fresh scan, taps off: no extra button', () => {
    const f = facts(statusBody(sample('status.input_revoked')));
    expect(gameInputSeen(f)).toMatchObject({ row: 'I5', word: 'idle', evidence: 'EV-6' });
    expect(offered(f)).toBeNull();
  });

  it('S-006 listed automation keeps it even with input_revoked true', () => {
    const body = statusBody(sample('status.input_revoked'), {
      processes: [{ pid: 4101, created: 1, runner: 'orchestrator' }],
    });
    expect(offered(facts(body))).toBe('automation_listed');
  });
});
