import { expect, it, vi } from 'vitest';
import { battleIdentity } from '../../../src/core/battle.ts';
import { list, own, ownPath } from '../../../src/core/read.ts';
import {
  dashboardBody,
  dashboardCase,
  dashboardCaseNames,
} from '../../support/dashboardFixture.ts';

const fixtureActionCases = [
  'accepted_command_requested',
  'admitted_then_the_readback_failed_unknown',
  'admitted_then_the_success_diagnostic_raised_default_500',
  'changed_expected_revision_refused',
  'diagnostic_failed_identical_replay_answered_from_the_record',
  'different_action_while_one_is_open_refused',
  'identical_replay_answered_from_the_record',
  'identical_retry_after_the_battle_closed_expired_battle_ended',
  'identical_retry_answered_from_the_record_after_the_driver_ended',
  'live_controls_accepted_command_requested',
  'new_key_refused_the_driver_is_no_longer_active',
  'recorded_before_the_battle_closed',
  'recorded_before_the_driver_ended',
  'replaced_battle_refused',
  'replay_while_the_readback_still_fails_unknown',
  'same_key_different_payload_refused',
  'second_key_for_the_open_action_joins_it',
  'stale_generation_refused',
  'unknown_operation_refused',
  'unsupported_action_refused',
];
const fixtureRoutineGates: Readonly<Record<string, readonly string[]>> = {
  commands_controls_applied_with_overrides_action_failed: ['ready', 'ready', 'action_unsupported'],
  commands_controls_expired_action_second_key_joined: [
    'control_change_pending',
    'control_change_pending',
    'action_unsupported',
  ],
  commands_controls_pending_no_path_applied_action_started: [
    'control_change_pending',
    'control_change_pending',
    'action_unsupported',
  ],
  commands_controls_pending_some_paths_applied_action_completed: [
    'control_change_pending',
    'control_change_pending',
    'action_unsupported',
  ],
  commands_controls_rejected_action_rejected: [
    'control_change_pending',
    'control_change_pending',
    'action_unsupported',
  ],
  commands_controls_requested_action_requested: [
    'control_change_pending',
    'control_change_pending',
    'action_unsupported',
  ],
  legacy_shopping_plan: ['ready', 'ready', 'action_unsupported'],
  null_after_reconcile_closed_record: [
    'no_tracked_battle',
    'no_tracked_battle',
    'no_tracked_battle',
  ],
  null_after_the_driver_ended_the_record: [
    'no_tracked_battle',
    'no_tracked_battle',
    'no_tracked_battle',
  ],
  null_no_driver: ['no_tracked_battle', 'no_tracked_battle', 'no_tracked_battle'],
  prepared: ['battle_not_active', 'battle_not_active', 'battle_not_active'],
  replaced_generation: ['battle_not_active', 'battle_not_active', 'battle_not_active'],
  runtime_ad_orbit_shopping: ['ready', 'ready', 'ready'],
  stale_report: ['report_unavailable', 'report_unavailable', 'report_unavailable'],
  waiting_for_game_state_blocker: ['ready', 'ready', 'ready'],
};
it('F-055 F-056 F-059 fixture routine and action inventories are exhaustive', () => {
  expect(dashboardCaseNames('battle_actions')).toEqual(fixtureActionCases);
  expect(dashboardCaseNames('battles_active')).toEqual(Object.keys(fixtureRoutineGates).sort());
});
it.each(Object.entries(fixtureRoutineGates))(
  'F-055 F-056 fixture battles_active.%s uses raw runtime paths and action capabilities',
  (name, reasons) => {
    const body = dashboardBody(`battles_active.${name}`);
    const battle = own(body, 'battle');
    const identity = battleIdentity(battle, 1800000000);
    const shown = identity.kind === 'known' ? identity.value : null;
    const read: ContextRead = { kind: 'answered', body, sameContext: true };
    const routines = ['ad_gems', 'floating_gems', 'shopping'] as const;
    routines.forEach((routine, index) => {
      expect(actionGate(read, shown, routine, false).reason).toBe(reasons[index]);
      const timing = routineTiming(read, shown, routine);
      if (name === 'stale_report')
        expect(timing).toMatchObject({ line: 'old_report', word: 'unknown', nextInSec: null });
      if (['prepared', 'replaced_generation'].includes(name)) expect(timing.nextInSec).toBeNull();
      if (battle !== null && !['prepared', 'replaced_generation'].includes(name)) {
        const runtime = own(battle, 'runtime');
        const result = routineResult(runtime, routine);
        const raw = ownPath(runtime, 'routines', routine, 'last_outcome');
        expect(result.raw).toEqual(raw);
        expect(result.confirmed).toBe(own(raw, 'confirmed') === true);
      }
    });
    if (name === 'runtime_ad_orbit_shopping') {
      expect(routineTiming(read, shown, 'shopping').nextInSec).toBe(18);
      expect(routineTiming(read, shown, 'shopping', 1).nextInSec).toBe(17);
      expect(routineTiming(read, shown, 'ad_gems').windowEndsAt).toBe(
        ownPath(battle, 'runtime', 'routines', 'ad_gems', 'window_ends_at'),
      );
      expect(routineResult(own(battle, 'runtime'), 'ad_gems').decision.line).toBe(
        'claim_offer_gone',
      );
      expect(routineResult(own(battle, 'runtime'), 'floating_gems').decision.word).toBe('unknown');
      expect(shoppingView(body).warnings).toEqual([]);
    }
    if (name === 'legacy_shopping_plan') {
      expect(shoppingView(body).line).toBe('shopping_policy_needs_review');
      expect(shoppingView(body).warnings.map((warning) => warning.line)).toEqual([
        'reserve_not_protected',
        'purchase_quantity_unverified',
      ]);
    }
  },
);
it.each(fixtureActionCases)(
  'F-059 fixture battle_actions.%s settles only its own admitted action or definite refusal',
  (name) => {
    const c = dashboardCase(`battle_actions.${name}`);
    const result = runNowAnswer({ kind: 'answered', status: c.status, body: c.body });
    if (c.request.path.endsWith('/live-controls')) {
      expect(result.kind).toBe('unknown'); // The shared route's controls response is not a Run now action.
    } else if (c.status === 200) {
      expect(result.kind).toBe('recorded');
      if (result.kind !== 'recorded') throw new Error('fixture action not recorded');
      expect(result.command.state).toBe(ownPath(c.body, 'command', 'state'));
      if (name === 'identical_retry_after_the_battle_closed_expired_battle_ended')
        expect(result.command.decision.line).toBe('battle_record_closed');
      const identity = battleIdentity(own(c.body, 'battle'), 1800000000);
      const key = opaqueToken(own(c.request.body, 'idempotency_key'));
      if (identity.kind !== 'known' || key === null)
        throw new Error('fixture request identity missing');
      expect(
        settleRunNow({ kind: 'answered', body: c.body, sameContext: true }, identity.value, key)
          .kind,
      ).toBe('recorded');
      const unrelated = opaqueToken('another exact opaque key');
      if (unrelated === null) throw new Error('test key missing');
      expect(
        settleRunNow(
          { kind: 'answered', body: c.body, sameContext: true },
          identity.value,
          unrelated,
        ).kind,
      ).toBe('unknown');
      expect(list(ownPath(c.body, 'battle', 'commands'))?.length).toBeGreaterThan(0);
    } else if ([400, 404, 409].includes(c.status)) expect(result.kind).toBe('not_recorded');
    else
      expect(result).toMatchObject({
        kind: 'unknown',
        decision: { word: 'unknown', line: 'may_have_been_recorded' },
      });
  },
);
it('F-059 fixture unknown admission and identical replay share one recorded command', () => {
  const unknown = dashboardCase('battle_actions.admitted_then_the_readback_failed_unknown');
  const replay = dashboardCase('battle_actions.identical_replay_answered_from_the_record');
  expect(unknown.status).toBe(500);
  expect(own(unknown.body, 'code')).toBe('command_outcome_unknown');
  expect(ownPath(replay.body, 'command', 'id')).toBe(own(unknown.body, 'command_id'));
  expect(replay.request.body).toEqual(unknown.request.body);
  expect(list(ownPath(replay.body, 'battle', 'commands'))).toHaveLength(1);
  const html = dashboardCase(
    'battle_actions.admitted_then_the_success_diagnostic_raised_default_500',
  );
  expect(own(html.body, 'text')).toBeTypeOf('string');
  expect(runNowAnswer({ kind: 'answered', status: html.status, body: html.body }).kind).toBe(
    'unknown',
  );
  const refused = dashboardCase('battle_actions.same_key_different_payload_refused');
  expect(refused.status).toBe(409);
  expect(runNowAnswer({ kind: 'answered', status: refused.status, body: refused.body }).kind).toBe(
    'not_recorded',
  );
});
import {
  actionGate,
  routineResult,
  routineTiming,
  runNowAnswer,
  settleRunNow,
  shoppingView,
} from '../../../src/core/routines.ts';
import { opaqueToken } from '../../../src/core/tokens.ts';
import type { BattleIdentity } from '../../../src/core/battle.ts';
import type { ContextRead } from '../../../src/core/connection.ts';

// backend/control/battles.py public; backend/runtime/battle_session.py publish
// and _manual_progress; backend/dashboard.py api_battle_command.
const shown: BattleIdentity = { id: 'b', generation: 'g', operationId: 'op' };
const base = (patch: object = {}) => ({
  id: 'b',
  generation: 'g',
  operation_id: 'op',
  status: 'active',
  control_revision: 2,
  applied_revision: 2,
  capabilities: { actions: ['ad_gems', 'floating_gems', 'shopping'] },
  commands: [],
  runtime: {
    server_time: 100,
    routines: {
      shopping: {
        enabled: true,
        state: 'scheduled',
        next_due_at: 644,
        window_ends_at: null,
        server_time: 100,
      },
    },
  },
  ...patch,
});
const answer = (battle: unknown = base(), server_time = 100): ContextRead => ({
  kind: 'answered',
  sameContext: true,
  body: { battle, server_time },
});
it('F-050 prepared settings never produce a countdown or enable Run now', () => {
  expect(
    routineTiming(
      answer(
        base({
          status: 'prepared',
          effective: { routines: { shopping: { enabled: true, interval_sec: 90 } } },
        }),
      ),
      shown,
      'shopping',
    ),
  ).toMatchObject({
    row: 'F-050',
    nextInSec: null,
    word: null,
    line: 'on_when_wave_starts',
    savedIntervalSec: 90,
  });
  expect(
    routineTiming(
      answer(
        base({ status: 'prepared', effective: { routines: { shopping: { enabled: false } } } }),
      ),
      shown,
      'shopping',
    ).line,
  ).toBe('prepared_routine_off');
  expect(actionGate(answer(base({ status: 'prepared' })), shown, 'shopping', false).enabled).toBe(
    false,
  );
});

it('F-055 shifting the browser wall clock by a day changes no routine decision', () => {
  const clock = vi.spyOn(Date, 'now').mockReturnValue(100_000);
  const first = routineTiming(answer(), shown, 'shopping', 1);
  clock.mockReturnValue(86_500_000);
  expect(routineTiming(answer(), shown, 'shopping', 1)).toEqual(first);
});
it('F-055 timing uses runtime.server_time and monotonic elapsed only; shopping needs no manual flags', () => {
  expect(routineTiming(answer(), shown, 'shopping').nextInSec).toBe(544);
  expect(routineTiming(answer(), shown, 'shopping', 4).nextInSec).toBe(540);
  expect(routineTiming(answer(), shown, 'shopping', 15.001)).toMatchObject({
    word: 'unknown',
    line: 'old_report',
    nextInSec: null,
  });
});
it('F-055 a pause requires a window and a blocker remains raw', () => {
  const withRoutine = (routine: object) =>
    answer(base({ runtime: { server_time: 100, routines: { ad_gems: routine } } }));
  expect(
    routineTiming(
      withRoutine({ enabled: false, window_ends_at: 150, state: 'off' }),
      shown,
      'ad_gems',
    ),
  ).toMatchObject({ line: 'paused_until_window', windowEndsAt: 150 });
  expect(
    routineTiming(
      withRoutine({ enabled: false, window_ends_at: null, state: 'off' }),
      shown,
      'ad_gems',
    ).line,
  ).toBe('routine_off');
  expect(
    routineTiming(
      withRoutine({ enabled: true, blocker: 'waiting_for_game_state', state: 'scheduled' }),
      shown,
      'ad_gems',
    ),
  ).toMatchObject({ line: 'routine_blocker', blocker: 'waiting_for_game_state', word: 'waiting' });
});
it('F-056 enables only a supported action in a fresh fully applied active battle', () => {
  expect(actionGate(answer(), shown, 'shopping', false)).toEqual({
    enabled: true,
    reason: 'ready',
  });
  for (const patch of [
    { status: 'prepared' },
    { generation: 'new' },
    { applied_revision: 1 },
    { capabilities: { actions: ['ad_gems'] } },
    { commands: null },
  ])
    expect(actionGate(answer(base(patch)), shown, 'shopping', false).enabled).toBe(false);
  expect(actionGate(answer(), shown, 'shopping', true).reason).toBe('earlier_outcome_unknown');
  expect(actionGate(answer(null), shown, 'shopping', false).reason).toBe('no_tracked_battle');
  expect(actionGate(answer(base(), 116), shown, 'shopping', false).reason).toBe(
    'report_unavailable',
  );
});
it.each(['requested', 'started'])(
  'F-056 an open action in state %s blocks every routine',
  (state) => {
    const read = answer(
      base({ commands: [{ kind: 'action', state, payload: { action: 'ad_gems' } }] }),
    );
    for (const routine of ['ad_gems', 'floating_gems', 'shopping'] as const)
      expect(actionGate(read, shown, routine, false).reason).toBe('another_action_in_progress');
    expect(
      actionGate(
        answer(base({ commands: [{ kind: 'controls', state: 'requested' }] })),
        shown,
        'shopping',
        false,
      ).enabled,
    ).toBe(true);
  },
);
it('F-057 confirmation needs exactly true and does not expose gem totals', () => {
  for (const confirmed of [false, 'true', 1, undefined])
    expect(
      routineResult(
        { routines: { ad_gems: { last_outcome: { confirmed, reason: 'raw', gem_balance: 900 } } } },
        'ad_gems',
      ),
    ).toMatchObject({
      confirmed: false,
      decision: { word: 'unknown' },
      reason: 'raw',
      confirmedPurchases: null,
    });
  expect(
    routineResult(
      { routines: { floating_gems: { last_outcome: { confirmed: true, observed_at: 90 } } } },
      'floating_gems',
    ),
  ).toMatchObject({
    confirmed: true,
    decision: { line: 'floating_claim_confirmed', evidence: 'record' },
    observedAt: 90,
  });
  expect(
    routineResult({ routines: { ad_gems: { last_outcome: null } } }, 'ad_gems').decision.line,
  ).toBe('no_result_yet');
  expect(routineResult({ routines: { ad_gems: {} } }, 'ad_gems').decision.word).toBe('unknown');
  expect(routineResult({}, 'ad_gems').decision.word).toBe('unknown');
});
it('F-057 shopping takes its reason and purchase count from last_sweep', () => {
  expect(
    routineResult(
      {
        routines: {
          shopping: {
            last_outcome: {
              last_sweep: { confirmed_purchases: 3, reason: 'raw', outcome: 'confirmed' },
            },
          },
        },
      },
      'shopping',
    ),
  ).toMatchObject({ confirmed: false, confirmedPurchases: 3, reason: 'raw' });
});
it('F-059 200 records the actual command state; definite refusals record nothing', () => {
  expect(
    runNowAnswer({
      kind: 'answered',
      status: 200,
      body: { ok: true, command: { id: 'command', kind: 'action', state: 'started' } },
    }),
  ).toMatchObject({ kind: 'recorded', command: { decision: { row: 'K3' } } });
  for (const status of [400, 404, 409])
    expect(runNowAnswer({ kind: 'answered', status, body: { error: 'raw' } }).kind).toBe(
      'not_recorded',
    );
});
it('F-059 command_outcome_unknown default 500 malformed success and a lost answer may have recorded', () => {
  for (const body of [{ code: 'command_outcome_unknown' }, '<!doctype html>', null])
    expect(runNowAnswer({ kind: 'answered', status: 500, body })).toMatchObject({
      kind: 'unknown',
      decision: { word: 'unknown', line: 'may_have_been_recorded' },
    });
  expect(runNowAnswer({ kind: 'lost' }).kind).toBe('unknown');
  expect(runNowAnswer({ kind: 'answered', status: 200, body: { ok: true } }).kind).toBe('unknown');
});
it('F-059 only the exact opaque alias key in the same battle settles an uncertain request', () => {
  const key = opaqueToken('key any format');
  if (key === null) throw new Error('test key missing');
  const command = {
    id: 'c',
    kind: 'action',
    state: 'completed',
    battle_id: 'b',
    generation: 'g',
    operation_id: 'op',
    revision: 2,
    request_keys: ['first', 'key any format'],
  };
  expect(settleRunNow(answer(base({ commands: [command] })), shown, key).kind).toBe('recorded');
  for (const patch of [
    { request_keys: ['similar'] },
    { generation: 'other' },
    { kind: 'controls' },
    { request_keys: [{ toString: () => 'key any format' }] },
  ])
    expect(
      settleRunNow(answer(base({ commands: [{ ...command, ...patch }] })), shown, key).kind,
    ).toBe('unknown');
  expect(settleRunNow({ kind: 'failed' }, shown, key).kind).toBe('unknown');
});
it('F-066 raw version-2 plan and legacy review use battle.effective.shopping_plan', () => {
  expect(
    shoppingView({
      battle: { effective: { shopping_plan: { version: 2, cash_reserve: null, cadence: 'raw' } } },
    }),
  ).toMatchObject({ line: 'queue_for_this_battle', cashReserve: null, cadence: 'raw' });
  expect(shoppingView({ battle: { effective: { shopping_plan: { version: 1 } } } }).line).toBe(
    'shopping_policy_needs_review',
  );
});
it('F-067 preserves all ordered queue rows and looks up catalog as a list', () => {
  const queue = ['one', 'two', 'three', 'four'].map((id) => ({
    id,
    enabled: true,
    tap_limit: 3,
    weight: 2,
  }));
  const view = shoppingView({
    battle: { effective: { shopping_plan: { version: 2, queue } } },
    catalog: queue.map(({ id }) => ({ id, label: `raw ${id}` })),
  });
  expect(view.queue.map(({ label }) => label)).toEqual([
    'raw one',
    'raw two',
    'raw three',
    'raw four',
  ]);
  expect(shoppingView({}).queue).toEqual([]);
});
it('F-068 shopping decision and confirmation stay raw and time means reported time', () => {
  const last_outcome = {
    decision: { reason: 'raw' },
    pending_confirmation: { upgrade: 'a' },
    last_confirmation: { outcome: 'confirmed' },
  };
  expect(
    shoppingView({
      battle: { runtime: { routines: { shopping: { server_time: 100, last_outcome } } } },
    }),
  ).toMatchObject({
    reportedAt: 100,
    decision: last_outcome.decision,
    pendingConfirmation: last_outcome.pending_confirmation,
    lastConfirmation: last_outcome.last_confirmation,
  });
});
it('F-069 unsupported reserve protection is Blocked', () => {
  expect(
    shoppingView({
      battle: {
        runtime: { routines: { shopping: { last_outcome: { reserve_supported: false } } } },
      },
    }).warnings,
  ).toMatchObject([{ word: 'blocked', line: 'reserve_not_protected' }]);
});
it('F-069 unverified purchase quantity is Waiting independently of reserve support', () => {
  for (const reserve_supported of [true, undefined])
    expect(
      shoppingView({
        battle: {
          runtime: {
            routines: {
              shopping: {
                last_outcome: { reserve_supported, quantity_observation_supported: false },
              },
            },
          },
        },
      }).warnings,
    ).toMatchObject([{ word: 'waiting', line: 'purchase_quantity_unverified' }]);
  expect(
    shoppingView({
      battle: {
        runtime: {
          routines: {
            shopping: {
              last_outcome: { reserve_supported: false, quantity_observation_supported: false },
            },
          },
        },
      },
    }).warnings,
  ).toHaveLength(2);
});
