import { expect, it } from 'vitest';
import { entries, own, ownPath } from '../../../src/core/read.ts';
import { dashboardBody, dashboardCaseNames } from '../../support/dashboardFixture.ts';
import { sample } from '../../support/fixture.ts';

const fixtureStatusCases = [
  'device_state_device',
  'device_state_missing',
  'device_state_no_server_connect_refused',
  'device_state_no_server_connect_timed_out',
  'device_state_offline',
  'device_state_unauthorized',
  'device_state_unconfigured',
  'device_state_unknown',
  'guards_clear',
  'guards_records_unreadable',
  'guards_setup_and_collection_blocking_the_same_operation',
  'guards_setup_blocking_collection_released_by_handoff',
  'instance_other',
  'process_refresh_error',
  'process_refresh_stale',
  'processes_empty',
  'processes_linked_collector',
  'processes_two_rows_same_runner',
  'processes_untracked_created_null',
  'reconnect_gave_up',
  'reconnect_idle',
  'reconnect_trying_attempt_in_flight',
  'reconnect_trying_waiting_out_its_backoff',
  'stop_flag_false_after_the_lost_clear_flag_answer',
  'stop_flag_true_with_a_listed_run',
];
const fixtureCalibrationCases = [
  'failed_setup_unverified_final_screen',
  'idle',
  'restore_pending',
  'running_with_steps',
];
it('F-038 F-081 F-083 fixture status and calibration inventories are exhaustive', () => {
  expect(dashboardCaseNames('status')).toEqual(fixtureStatusCases);
  expect(dashboardCaseNames('calibrate_status')).toEqual(fixtureCalibrationCases);
});
it.each(fixtureStatusCases)(
  'F-033 F-038 F-081 fixture status.%s keeps each guard impact and scan evidence separate',
  (name) => {
    const body = dashboardBody(`status.${name}`);
    const view = guardImpact(
      own(body, 'setup_battle_guard'),
      own(body, 'collection_guard'),
      1800000000,
    );
    const setupBlocked = [
      'guards_records_unreadable',
      'guards_setup_and_collection_blocking_the_same_operation',
      'guards_setup_blocking_collection_released_by_handoff',
    ].includes(name);
    const collectionBlocked = [
      'guards_records_unreadable',
      'guards_setup_and_collection_blocking_the_same_operation',
    ].includes(name);
    expect(view.start).toBe(setupBlocked ? 'off' : 'permitted');
    expect(view.collection).toBe(
      collectionBlocked
        ? 'on_hold'
        : name === 'guards_setup_blocking_collection_released_by_handoff'
          ? 'confirmed_manual_battle'
          : 'permitted',
    );
    // Dashboard fixture intentionally omits harvest. Its companion harvest fixture
    // supplies the same-body clock needed by scanFresh; raw root fields stay unchanged.
    const complete = { ...Object.fromEntries(entries(body)), harvest: sample('status.off') };
    expect(handoffTarget(complete, 1800000000) !== null).toBe(
      name === 'guards_setup_and_collection_blocking_the_same_operation',
    );
    // Without the harvest clock the scan's freshness is unknown: no target (F4-R2-01).
    expect(handoffTarget(body, 1800000000)).toBeNull();
    const need = statusNeed(answer(complete));
    expect(need === null).toBe(!['process_refresh_stale', 'process_refresh_error'].includes(name));
    if (need !== null) expect(need.word).toBe('unknown');
    expect(view.setupMessage).toEqual(ownPath(body, 'setup_battle_guard', 'message'));
  },
);
it.each(fixtureCalibrationCases)(
  'F-082 F-083 fixture calibrate_status.%s preserves recorded setup evidence',
  (name) => {
    const body = dashboardBody(`calibrate_status.${name}`);
    const guards = dashboardBody('status.guards_setup_and_collection_blocking_the_same_operation');
    const view = setupOutcome(answer(body), guards, 1800000000);
    if (name === 'failed_setup_unverified_final_screen') {
      expect(view).toMatchObject({
        decision: { row: 'F-083', word: 'blocked', line: 'setup_failed' },
        impact: { start: 'off', collection: 'on_hold' },
      });
      expect(view?.setupBattle).toEqual(ownPath(body, 'state', 'setup_battle'));
      expect(view?.finalScreen).toEqual(ownPath(body, 'state', 'final_screen'));
    } else if (name === 'restore_pending')
      expect(view?.decision.line).toBe('equipment_not_restored');
    else expect(view).toBeNull();
  },
);
import {
  finishSetupNeed,
  guardImpact,
  handoffTarget,
  needsView,
  setupOutcome,
  statusNeed,
} from '../../../src/core/needs.ts';
import type { ContextRead } from '../../../src/core/connection.ts';

// backend/dashboard.py api_status/api_calibrate_status; backend/control/battles.py
// _setup_guard/_collection_release and record_collection_handoff.
const setup = {
  scope: 'run',
  blocks_launch: true,
  code: 'setup_battle_unresolved',
  operation_id: 'op',
  setup_battle_id: 'b',
  evidence_revision: 'rev',
  message: 'raw setup',
};
const collection = { ...setup, scope: 'collection', message: 'raw collection' };
const answer = (body: unknown): ContextRead => ({ kind: 'answered', sameContext: true, body });
it('F-038 per-guard impacts never borrow permission from the other guard', () => {
  expect(
    guardImpact(
      setup,
      { ...collection, blocks_launch: false, evidence: 'collection_handoff' },
      100,
    ),
  ).toMatchObject({ start: 'off', collection: 'confirmed_manual_battle', commonBlock: false });
  expect(guardImpact({ ...setup, blocks_launch: false }, collection, 100)).toMatchObject({
    start: 'permitted',
    collection: 'on_hold',
    commonBlock: false,
  });
  expect(guardImpact(undefined, undefined, 100)).toMatchObject({
    start: 'unconfirmed',
    collection: 'unconfirmed',
  });
  expect(guardImpact(setup, collection, 100).commonBlock).toBe(true);
  expect(guardImpact(setup, { ...collection, code: 'other' }, 100).commonBlock).toBe(false);
});
it('F-033 handoff offers only a target for the same unresolved operation with no listed process on a fresh scan', () => {
  const harvest = sample('status.off');
  const now = Number(own(harvest, 'server_time'));
  const fresh = { state: 'fresh', error: null, age_sec: 0, last_success_at: now };
  const body = {
    setup_battle_guard: setup,
    collection_guard: collection,
    processes: [],
    process_refresh: fresh,
    harvest,
  };
  expect(handoffTarget(body, 100)).toEqual({
    operationId: 'op',
    setupBattleId: 'b',
    evidenceRevision: 'rev',
  });
  for (const patch of [
    { processes: [{}] },
    { processes: undefined },
    // F4-R2-01: an empty list from a stale scan proves nothing.
    { process_refresh: { ...fresh, state: 'stale' } },
    { collection_guard: { ...collection, operation_id: 'other' } },
    { collection_guard: { ...collection, blocks_launch: false } },
    { setup_battle_guard: { ...setup, evidence_revision: '' } },
  ])
    expect(handoffTarget({ ...body, ...patch }, 100)).toBeNull();
  expect(
    handoffTarget({ ...body, setup_battle_guard: { ...setup, evidence_revision: 'new' } }, 100),
  ).not.toEqual(handoffTarget(body, 100));
});
it('F-081 unavailable status and stale discovery produce Unknown recovery items', () => {
  expect(statusNeed({ kind: 'failed' })).toMatchObject({ word: 'unknown', line: 'no_live_status' });
  expect(statusNeed(answer({ process_refresh: { state: 'stale' } }))).toMatchObject({
    word: 'unknown',
    line: 'process_discovery_unconfirmed',
  });
  expect(statusNeed(answer({ process_refresh: { state: 'fresh' } }))).toMatchObject({
    line: 'process_discovery_unconfirmed',
  });
  expect(
    statusNeed(
      answer({
        harvest: { server_time: 100 },
        process_refresh: { state: 'fresh', error: null, age_sec: 0, last_success_at: 100 },
      }),
    ),
  ).toBeNull();
  for (const process_refresh of [
    { state: 'fresh', age_sec: 7, last_success_at: 93 },
    { state: 'fresh', age_sec: 0, last_success_at: 90 },
    { state: 'fresh', error: 'raw', age_sec: 0, last_success_at: 100 },
  ])
    expect(statusNeed(answer({ harvest: { server_time: 100 }, process_refresh }))?.word).toBe(
      'unknown',
    );
});
it('F-082 unavailable calibration and restoration pending are separate cases', () => {
  expect(setupOutcome({ kind: 'failed' }, {}, 100)?.decision).toMatchObject({
    word: 'unknown',
    line: 'setup_status_unavailable',
  });
  expect(setupOutcome(answer({ restore_pending: true }), {}, 100)?.decision).toMatchObject({
    word: 'blocked',
    line: 'equipment_not_restored',
  });
});
it('F-083 failed setup reports each guard impact and leaves detector evidence raw', () => {
  const body = {
    operation: { id: 'op', status: 'failed', updated_at: 90, error: { message: 'raw error' } },
    state: {
      setup_battle: { operation_id: 'op', status: 'unresolved' },
      final_screen: { operation_id: 'op', verified: false, name: 'raw' },
    },
  };
  expect(
    setupOutcome(
      answer(body),
      {
        setup_battle_guard: setup,
        collection_guard: { ...collection, blocks_launch: false, evidence: 'collection_handoff' },
      },
      100,
    ),
  ).toMatchObject({
    decision: { word: 'blocked', line: 'setup_failed' },
    operationId: 'op',
    updatedAt: 90,
    finalScreen: body.state.final_screen,
    impact: { start: 'off', collection: 'confirmed_manual_battle' },
  });
  expect(
    setupOutcome(answer({ ...body, operation: { ...body.operation, id: 'other' } }), {}, 100)
      ?.finalScreen,
  ).toBeUndefined();
});
it('F-083 partial and closed setup records never claim free stopped or fine', () => {
  for (const status of ['cancelled', 'interrupted', 'succeeded'])
    expect(
      setupOutcome(
        answer({
          operation: { id: 'op', status },
          state: { final_screen: { operation_id: 'op', verified: false, name: 'raw' } },
        }),
        {},
        100,
      )?.decision,
    ).toMatchObject({ word: 'unknown', line: 'setup_partial' });
  expect(setupOutcome(answer({ operation: { id: 'op', status: 'running' } }), {}, 100)).toBeNull();
});
it('F-084 at most three items show until expansion and the empty state makes only a reported-issues claim', () => {
  expect(needsView([1, 2, 3, 4], false)).toEqual({
    count: 4,
    visible: [1, 2, 3],
    showAll: true,
    line: 'issues_reported',
  });
  expect(needsView([1, 2, 3, 4], true).visible).toEqual([1, 2, 3, 4]);
  expect(needsView([], false)).toMatchObject({ count: 0, line: 'no_unresolved_issues_reported' });
});
it('F-098 first missing setup step disappears only after a ready non-combo run exists', () => {
  const tools = { installed: true };
  const accounts = { active: 'a' };
  const config = { active_instance: 'main', instances: { main: { serial: 'fake:1' } } };
  const runs = { runs: { farm: { defined: true } }, readiness: { farm: { ready: true } } };
  expect(finishSetupNeed({}, accounts, config, runs)).toBe('tools');
  expect(finishSetupNeed(tools, {}, config, runs)).toBe('profile');
  expect(finishSetupNeed(tools, accounts, {}, runs)).toBe('connection');
  expect(finishSetupNeed(tools, accounts, config, {})).toBe('screens');
  expect(
    finishSetupNeed(tools, accounts, config, {
      runs: { combo: { defined: true } },
      readiness: { combo: { ready: true } },
    }),
  ).toBe('screens');
  expect(finishSetupNeed(tools, accounts, config, runs)).toBeNull();
  expect(
    finishSetupNeed(tools, accounts, config, {
      runs: { bp_farm: { kind: 'coin' } },
      readiness: { bp_farm: { ready: true } },
    }),
  ).toBeNull();
});
