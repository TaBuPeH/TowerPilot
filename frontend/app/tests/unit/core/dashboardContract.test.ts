import { describe, expect, it } from 'vitest';
import { automationSlot } from '../../../src/core/automation.ts';
import { configContext, tapsSetting } from '../../../src/core/config.ts';
import { collectionGuard, setupGuard } from '../../../src/core/guards.ts';
import { classifyInputAnswer } from '../../../src/core/outcomes/input.ts';
import { scanFresh } from '../../../src/core/scan.ts';
import {
  DASHBOARD_FIXTURE_SHA256,
  dashboardBody,
  dashboardCase,
  dashboardCaseNames,
} from '../../support/dashboardFixture.ts';
import { sampleRecord } from '../../support/fixture.ts';
import { contextOf, rec } from '../../support/core.ts';

/**
 * Core decisions over the real Dashboard contract fixture
 * (backend/tests/fixtures/dashboard_contract_v1.json, spec 10.3): the status
 * root members other than harvest, the config reads and the unknown-outcome
 * writes the root-owned core modules judge. Status bodies carry no harvest
 * (it has its own fixture), so each is joined with `S:status.off`, whose
 * server time is the same fixed clock (1800000000).
 */
function withHarvest(statusCase: string): Record<string, unknown> {
  return { ...rec(dashboardBody(`status.${statusCase}`)), harvest: sampleRecord('status.off') };
}

function slot(statusCase: string) {
  return automationSlot({
    status: contextOf(withHarvest(statusCase)),
    extraClaims: [],
    lastIdleAgeSec: null,
  });
}

describe('the Dashboard contract fixture the core tests read', () => {
  it('is the landed fixture (17002FA1...) with the status and config cases these tests cover', () => {
    expect(DASHBOARD_FIXTURE_SHA256).toBe(
      'a1adee857b2810076eb125970c5dd7f0e0d5439cb16e3e3560ef8c46e593c66c',
    );
    expect(dashboardCaseNames('config')).toEqual(['taps_false', 'taps_missing', 'taps_true']);
    expect(dashboardCaseNames('status')).toEqual(
      expect.arrayContaining([
        'guards_clear',
        'guards_records_unreadable',
        'guards_setup_and_collection_blocking_the_same_operation',
        'guards_setup_blocking_collection_released_by_handoff',
        'process_refresh_error',
        'process_refresh_stale',
        'processes_empty',
        'processes_linked_collector',
        'processes_two_rows_same_runner',
        'processes_untracked_created_null',
      ]),
    );
  });
});

describe('S-100 the real root guards, judged separately', () => {
  const guards = (name: string) => {
    const body = rec(dashboardBody(`status.${name}`));
    return {
      start: setupGuard(body['setup_battle_guard'], 1).kind,
      collect: collectionGuard(body['collection_guard'], 1).kind,
    };
  };

  it('S-100 guards_clear permits Start and Collect', () => {
    expect(guards('guards_clear')).toEqual({ start: 'permit', collect: 'permit' });
  });

  it('S-100 a recorded handoff releases Collect while Start stays blocked', () => {
    expect(guards('guards_setup_blocking_collection_released_by_handoff')).toEqual({
      start: 'refused',
      collect: 'permit',
    });
  });

  it.each(['guards_setup_and_collection_blocking_the_same_operation', 'guards_records_unreadable'])(
    'S-100 %s refuses both, each by its own guard and code',
    (name) => {
      const body = rec(dashboardBody(`status.${name}`));
      const start = setupGuard(body['setup_battle_guard'], 1);
      const collect = collectionGuard(body['collection_guard'], 1);
      expect([start.kind, collect.kind]).toEqual(['refused', 'refused']);
      if (start.kind === 'refused' && collect.kind === 'refused') {
        expect(start.refusal).toMatchObject({
          kind: 'blocks',
          code: rec(body['setup_battle_guard'])['code'],
        });
        expect(collect.refusal).toMatchObject({
          kind: 'blocks',
          code: rec(body['collection_guard'])['code'],
        });
      }
    },
  );
});

describe('S-023 the real stale and failed scans', () => {
  it.each(['process_refresh_stale', 'process_refresh_error'])('S-023 %s is not fresh', (name) => {
    expect(
      scanFresh(rec(dashboardBody(`status.${name}`))['process_refresh'], 1_800_000_000).kind,
    ).toBe('unknown');
  });

  it('S-023 control: processes_empty carries a fresh scan', () => {
    expect(
      scanFresh(rec(dashboardBody('status.processes_empty'))['process_refresh'], 1_800_000_000)
        .kind,
    ).toBe('known');
  });
});

describe('S-060 and S-061 Stop targets of the real process rows (INV-3)', () => {
  it('S-060 an untracked row with created null has a disabled Stop with its reason', () => {
    const rows = rec(dashboardBody('status.processes_untracked_created_null'))[
      'processes'
    ] as Record<string, unknown>[];
    expect(slot('processes_untracked_created_null').rows.map((row) => row.stop)).toEqual([
      { kind: 'disabled', reason: 'created_missing', operationId: null, pid: rows[0]?.['pid'] },
    ]);
  });

  it('S-060 two rows of the same runner each kill their own exact pid and created', () => {
    const rows = rec(dashboardBody('status.processes_two_rows_same_runner'))['processes'] as Record<
      string,
      unknown
    >[];
    expect(slot('processes_two_rows_same_runner').rows.map((row) => row.stop)).toEqual(
      rows.map((row) => ({ kind: 'kill', pid: row['pid'], created: row['created'] })),
    );
  });

  it('S-061 a linked collector row stops through its operation with its revision', () => {
    const row = (
      rec(dashboardBody('status.processes_linked_collector'))['processes'] as Record<
        string,
        unknown
      >[]
    )[0];
    expect(slot('processes_linked_collector').rows.map((entry) => entry.stop)).toEqual([
      {
        kind: 'cancel',
        operationId: row?.['operation_id'],
        expectedRevision: row?.['operation_revision'],
      },
    ]);
  });

  it('S-027 a stale scan keeps the rows Unknown ("last listed"), never Running', () => {
    const body = { ...withHarvest('process_refresh_stale') };
    body['processes'] = rec(dashboardBody('status.processes_two_rows_same_runner'))['processes'];
    const view = automationSlot({ status: contextOf(body), extraClaims: [], lastIdleAgeSec: null });
    expect(view.rows.map((row) => [row.seen.row, row.seen.word])).toEqual([
      ['A4', 'unknown'],
      ['A4', 'unknown'],
    ]);
  });
});

describe('S-006 the real config reads: the Setting and the Pause context (I1, INV-1)', () => {
  const read = (name: string) => ({
    startedSeq: 1,
    outcome: { kind: 'answered' as const, body: dashboardBody(`config.${name}`) },
  });

  it('S-006 taps_true and taps_false give the Setting; taps_missing is I1 unknown', () => {
    expect(tapsSetting(read('taps_true'))).toMatchObject({ kind: 'known', allowTaps: true });
    expect(tapsSetting(read('taps_false'))).toMatchObject({ kind: 'known', allowTaps: false });
    expect(tapsSetting(read('taps_missing'))).toEqual({
      kind: 'unknown',
      reason: 'allow_taps_invalid',
    });
  });

  it('S-006 the Pause context is the ACTIVE instance and its serial, never another emulator (OD-13)', () => {
    expect(configContext(dashboardBody('config.taps_true'))).toEqual({
      instance: 'main',
      serial: 'fake:1',
    });
    expect(configContext(dashboardBody('config.taps_missing'))).toEqual({
      instance: 'other',
      serial: 'fake:2',
    });
  });
});

describe('S-010 the real unknown outcome of Allow game input', () => {
  it("S-010 Flask's default 500 after the revocation cleared reads unknown, never enabled or refused", () => {
    const answer = dashboardCase(
      'write_unknown_outcomes.allow_input_cleared_the_revocation_then_the_save_raised',
    );
    expect(answer.request).toMatchObject({
      method: 'POST',
      path: '/api/automation-access',
      body: { enabled: true },
    });
    expect(classifyInputAnswer(answer, 'enable')).toMatchObject({ kind: 'unknown' });
  });
});
