import { expect, it } from 'vitest';
import { own, ownPath, finite } from '../../../src/core/read.ts';
import { dashboardBody, dashboardCaseNames } from '../../support/dashboardFixture.ts';

const fixtureBattleRows: Readonly<Record<string, string>> = {
  commands_controls_applied_with_overrides_action_failed: 'B5',
  commands_controls_expired_action_second_key_joined: 'B6',
  commands_controls_pending_no_path_applied_action_started: 'B6',
  commands_controls_pending_some_paths_applied_action_completed: 'B6',
  commands_controls_rejected_action_rejected: 'B6',
  commands_controls_requested_action_requested: 'B6',
  legacy_shopping_plan: 'B5',
  null_after_reconcile_closed_record: 'B2',
  null_after_the_driver_ended_the_record: 'B2',
  null_no_driver: 'B2',
  prepared: 'B3',
  replaced_generation: 'B3',
  runtime_ad_orbit_shopping: 'B5',
  stale_report: 'B7',
  waiting_for_game_state_blocker: 'B5',
};
it('F-049 fixture active case list is exhaustive', () => {
  expect(dashboardCaseNames('battles_active')).toEqual(Object.keys(fixtureBattleRows).sort());
});
it.each(Object.entries(fixtureBattleRows))(
  'B1 B2 B3 B5 B6 B7 fixture %s uses row %s and its own identity',
  (name, row) => {
    const body = dashboardBody(`battles_active.${name}`);
    const rawBattle = own(body, 'battle');
    const identity = battleIdentity(rawBattle, finite(own(body, 'server_time')) ?? NaN);
    const read: ContextRead = { kind: 'answered', sameContext: true, body };
    expect(battleView(read, identity.kind === 'known' ? identity.value : null).row).toBe(row);
    expect(battleView({ ...read, sameContext: false }, null).row).toBe('B1');
    if (rawBattle === null) {
      expect(identity.kind).toBe('unknown');
      expect(battleView(read, null).line).toBe('no_tracked_battle');
    } else {
      expect(identity.kind).toBe('known');
      if (identity.kind !== 'known') throw new Error('fixture battle identity missing');
      expect(identity.value).toEqual({
        id: own(rawBattle, 'id'),
        generation: own(rawBattle, 'generation'),
        operationId: own(rawBattle, 'operation_id'),
      });
      if (row === 'B5') {
        expect(battleView(read, identity.value, 16).row).toBe('B7');
        expect(battleView(read, { ...identity.value, generation: 'previous' }).row).toBe('B7');
      }
    }
  },
);
it('B4 fixture active battle with a not-yet-published runtime waits for the first report', () => {
  const body = dashboardBody('battles_active.runtime_ad_orbit_shopping');
  const raw = own(body, 'battle');
  const identity = battleIdentity(raw, 1800000000);
  if (identity.kind !== 'known') throw new Error('fixture identity missing');
  const read: ContextRead = {
    kind: 'answered',
    sameContext: true,
    body: {
      battle: {
        id: identity.value.id,
        generation: identity.value.generation,
        operation_id: identity.value.operationId,
        status: own(raw, 'status'),
        runtime: {},
        control_revision: own(raw, 'control_revision'),
        applied_revision: own(raw, 'applied_revision'),
      },
      server_time: own(body, 'server_time'),
    },
  };
  expect(battleView(read, identity.value).row).toBe('B4');
  expect(ownPath(body, 'battle', 'runtime', 'server_time')).toBeTypeOf('number');
});
it('F-049 fixture replaced generation replaces the retained identity even while prepared', () => {
  const old = battleIdentity(
    own(
      dashboardBody('battles_active.commands_controls_expired_action_second_key_joined'),
      'battle',
    ),
    1800000012,
  );
  const replacement = battleIdentity(
    own(dashboardBody('battles_active.replaced_generation'), 'battle'),
    1800000012,
  );
  expect(old.kind).toBe('known');
  expect(replacement.kind).toBe('known');
  if (old.kind === 'known' && replacement.kind === 'known') {
    expect(replacement.value.generation).not.toBe(old.value.generation);
  }
});
import {
  battleIdentity,
  battleView,
  freshBattleReport,
  type BattleIdentity,
} from '../../../src/core/battle.ts';
import type { ContextRead } from '../../../src/core/connection.ts';

// Constructed control/battles.py public/active; runtime/battle_session.py publish.
const shown: BattleIdentity = { id: 'b', generation: 'g', operationId: 'op' };
const battle = (patch: object = {}) => ({
  id: 'b',
  generation: 'g',
  operation_id: 'op',
  status: 'active',
  control_revision: 2,
  applied_revision: 2,
  runtime: { server_time: 100 },
  ...patch,
});
const answer = (b: unknown, server_time: unknown = 100): ContextRead => ({
  kind: 'answered',
  sameContext: true,
  body: { battle: b, server_time },
});

it('B1 failed and foreign-context answers cannot describe the shown battle', () => {
  for (const read of [
    null,
    { kind: 'failed' },
    { kind: 'answered', sameContext: false, body: { battle: battle(), server_time: 100 } },
  ] satisfies ContextRead[])
    expect(battleView(read, shown).row).toBe('B1');
});
it.each(['manual battle never adopted', 'driver record closed by reconcile'])(
  'B2 %s is only No tracked battle',
  () => {
    expect(battleView(answer(null), shown)).toMatchObject({
      row: 'B2',
      word: null,
      line: 'no_tracked_battle',
      evidence: 'record',
    });
  },
);
it('B3 prepared precedes missing runtime and never reports Running', () => {
  expect(battleView(answer(battle({ status: 'prepared', runtime: {} })), shown)).toMatchObject({
    row: 'B3',
    word: 'waiting',
  });
});
it('B4 active without a publication waits for its first report', () => {
  for (const runtime of [{}, { server_time: null }, undefined])
    expect(battleView(answer(battle({ runtime })), shown).row).toBe('B4');
});
it('B5 equal revisions need EV-9 at the inclusive 15-second boundary', () => {
  expect(battleView(answer(battle(), 115), shown)).toMatchObject({
    row: 'B5',
    word: 'running',
    evidence: 'EV-9',
  });
});
it('B6 fresh unapplied revisions wait for automation', () => {
  expect(battleView(answer(battle({ applied_revision: 1 })), shown)).toMatchObject({
    row: 'B6',
    word: 'waiting',
    evidence: 'EV-9',
  });
});
it('B7 stale future nonfinite malformed and replaced reports stay Unknown', () => {
  for (const now of [115.001, 99, NaN, Infinity, '100'])
    expect(battleView(answer(battle(), now), shown).row).toBe('B7');
  for (const patch of [
    { id: 'other' },
    { generation: 'new' },
    { operation_id: 'other' },
    { runtime: { server_time: NaN } },
    { applied_revision: 3 },
    { applied_revision: '2' },
    { status: 'interrupted' },
  ])
    expect(battleView(answer(battle(patch)), shown).row).toBe('B7');
  expect(battleView(answer(battle()), shown, 15.001).row).toBe('B7');
  expect(freshBattleReport(answer(battle()), shown, -1).kind).toBe('unknown');
});
it('F-049 identity changes invalidate retained routine outcomes and inherited ids never match', () => {
  expect(battleIdentity(battle(), 100)).toEqual({ kind: 'known', value: shown, observedAt: 100 });
  expect(battleIdentity(battle({ generation: 'new' }), 100)).not.toEqual(
    battleIdentity(battle(), 100),
  );
  expect(battleIdentity(Object.create(battle()), 100).kind).toBe('unknown');
  expect(battleIdentity(battle(), NaN).kind).toBe('unknown');
});
