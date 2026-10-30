import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';
import { list, own, ownPath } from '../../../src/core/read.ts';
import { dashboardBody, dashboardCaseNames } from '../../support/dashboardFixture.ts';

const fixtureCommandFamilies: Readonly<Record<string, readonly string[]>> = {
  battles_active: [
    'commands_controls_applied_with_overrides_action_failed',
    'commands_controls_expired_action_second_key_joined',
    'commands_controls_pending_no_path_applied_action_started',
    'commands_controls_pending_some_paths_applied_action_completed',
    'commands_controls_rejected_action_rejected',
    'commands_controls_requested_action_requested',
    'legacy_shopping_plan',
    'null_after_reconcile_closed_record',
    'null_after_the_driver_ended_the_record',
    'null_no_driver',
    'prepared',
    'replaced_generation',
    'runtime_ad_orbit_shopping',
    'stale_report',
    'waiting_for_game_state_blocker',
  ],
  battle_detail: [
    'ended_commands_expired_battle_ended',
    'interrupted_commands_expired_battle_ended',
    'other_emulator',
    'unknown_battle',
  ],
  battle_actions: [
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
  ],
};
const fixtureCommandRows: Readonly<Record<string, string>> = {
  'controls:requested': 'K1',
  'controls:pending': 'K2',
  'controls:applied': 'K4',
  'controls:rejected': 'K5',
  'controls:expired': 'K6',
  'action:requested': 'K1',
  'action:started': 'K3',
  'action:completed': 'K4',
  'action:failed': 'K5',
  'action:rejected': 'K5',
  'action:expired': 'K6',
};
it('F-058 fixture command families and per-kind observed state pairs are exhaustive', () => {
  const seen = new Set<string>();
  for (const [family, names] of Object.entries(fixtureCommandFamilies)) {
    expect(dashboardCaseNames(family)).toEqual(names);
    for (const name of names) {
      const body = dashboardBody(`${family}.${name}`);
      const commands = [...(list(ownPath(body, 'battle', 'commands')) ?? [])];
      if (own(body, 'command') !== undefined) commands.push(own(body, 'command'));
      for (const row of commands) {
        const kind = own(row, 'kind');
        const state = own(row, 'state');
        if (typeof kind !== 'string' || typeof state !== 'string')
          throw new Error('fixture command kind/state missing');
        seen.add(`${kind}:${state}`);
      }
    }
  }
  expect([...seen].sort()).toEqual(Object.keys(fixtureCommandRows).sort());
});
for (const [family, names] of Object.entries(fixtureCommandFamilies)) {
  it.each(names)(`F-058 fixture ${family}.%s reads every command row`, (name) => {
    const body = dashboardBody(`${family}.${name}`);
    const rows = [...(list(ownPath(body, 'battle', 'commands')) ?? [])];
    if (own(body, 'command') !== undefined) rows.push(own(body, 'command'));
    for (const row of rows) {
      const kind = own(row, 'kind');
      const state = own(row, 'state');
      if (typeof kind !== 'string' || typeof state !== 'string')
        throw new Error('fixture command kind/state missing');
      const paths = list(own(row, 'applied_paths'));
      const expected =
        state === 'pending' && (paths?.length ?? 0) > 0
          ? 'K2a'
          : fixtureCommandRows[`${kind}:${state}`];
      const view = commandView(row);
      expect(view.decision.row).toBe(expected);
      expect(view.evidence).toEqual(own(row, 'evidence'));
      expect(view.reason).toEqual(own(row, 'reason'));
      expect(view.state).toBe(state);
      expect(view.revision).toBe(own(row, 'revision'));
      if (state === 'expired' && own(row, 'reason') === 'battle_ended')
        expect(view.decision.line).toBe('battle_record_closed');
      if (kind === 'action')
        expect(commandView({ kind: 'controls', state: 'completed' }).decision.row).toBe('K7');
    }
    // The response envelope is never itself a command, including 404/{text} bodies.
    expect(commandView(body).decision.row).toBe('K7');
  });
}
import {
  COMMAND_WIRE_STATES,
  TERMINAL_COMMAND_STATES,
  commandTerminal,
  commandView,
} from '../../../src/core/commands.ts';

// backend/towerpilot_workflows/battles.py _command/acknowledge/_finish/commands.
const command = (kind: string, state: unknown, patch: object = {}) => ({
  kind,
  state,
  applied_paths: [],
  ...patch,
});
it.each(['controls', 'action'])('K1 requested %s waits for pickup', (kind) => {
  expect(commandView(command(kind, 'requested')).decision.row).toBe('K1');
});
it('K2 pending controls with no applied paths waits to apply', () => {
  expect(commandView(command('controls', 'pending')).decision).toMatchObject({
    row: 'K2',
    word: 'waiting',
  });
});
it('K2a pending controls needs a nonempty proper subset of requested paths', () => {
  const payload = { routines: { ad_gems: { enabled: true }, rewards: { enabled: true } } };
  expect(
    commandView(
      command('controls', 'pending', { payload, applied_paths: ['routines.ad_gems.enabled'] }),
    ).decision,
  ).toMatchObject({ row: 'K2a', word: 'waiting', line: 'partly_applied' });
  for (const applied_paths of [
    null,
    'x',
    ['alien'],
    ['routines.ad_gems.enabled', 'routines.rewards.enabled'],
  ])
    expect(
      commandView(command('controls', 'pending', { payload, applied_paths })).decision.row,
    ).toBe('K7');
});
it('K3 started action waits for its result', () => {
  expect(commandView(command('action', 'started')).decision.row).toBe('K3');
});

it('K2a shopping_plan is one applied path, matching backend flatten semantics', () => {
  expect(
    commandView(
      command('controls', 'pending', {
        payload: {
          shopping_plan: { version: 2, queue: [] },
          routines: { rewards: { enabled: true } },
        },
        applied_paths: ['shopping_plan'],
      }),
    ).decision.row,
  ).toBe('K2a');
});
it.each([
  ['controls', 'applied'],
  ['action', 'completed'],
])('K4 %s %s is a dated record outcome with raw evidence', (kind, state) => {
  const evidence = { confirmed: true, gem_balance: 1000 };
  expect(commandView(command(kind, state, { evidence }))).toMatchObject({
    decision: { row: 'K4', word: null, evidence: 'record' },
    evidence,
  });
});
it.each([
  ['action', 'failed'],
  ['action', 'rejected'],
  ['controls', 'rejected'],
])('K5 %s %s is Failed', (kind, state) => {
  expect(commandView(command(kind, state)).decision).toMatchObject({ row: 'K5', word: 'failed' });
});
it.each(['action', 'controls'])(
  'K6 expired %s battle_ended means only its battle record closed',
  (kind) => {
    expect(
      commandView(command(kind, 'expired', { reason: 'battle_ended' })).decision,
    ).toMatchObject({ row: 'K6', line: 'battle_record_closed' });
    expect(commandView(command(kind, 'expired', { reason: 'other' })).decision.line).toBe(
      'command_expired',
    );
  },
);
it('K7 wrong-kind wire states labels coercions and prototype names never match', () => {
  for (const state of COMMAND_WIRE_STATES.action.filter(
    (state) => !COMMAND_WIRE_STATES.controls.includes(state),
  ))
    expect(commandView(command('controls', state)).decision.row).toBe('K7');
  for (const state of COMMAND_WIRE_STATES.controls.filter(
    (state) => !COMMAND_WIRE_STATES.action.includes(state),
  ))
    expect(commandView(command('action', state)).decision.row).toBe('K7');
  for (const state of ['Accepted', 'Executing', 'Finished', 'constructor', {}, undefined])
    expect(commandView(command('action', state)).decision.row).toBe('K7');
  expect(commandView(Object.create(command('action', 'completed'))).decision.row).toBe('K7');
  expect(commandTerminal(command('controls', 'completed'))).toBe(false);
});
it('F-058 wire-state table equals backend allowed states per kind and terminal states', () => {
  const source = readFileSync(resolve('../../backend/towerpilot_workflows/battles.py'), 'utf8');
  const allowed = /allowed = \(([^)]+)\) if command\['kind'\] == 'controls' else \(([^)]+)\)/u.exec(
    source,
  );
  expect(allowed).not.toBeNull();
  const strings = (value: string): string[] =>
    [...value.matchAll(/'([^']+)'/gu)].map((match) => match[1] ?? '');
  expect([...COMMAND_WIRE_STATES.controls].sort()).toEqual(
    ['requested', ...strings(allowed?.[1] ?? '')].sort(),
  );
  expect([...COMMAND_WIRE_STATES.action].sort()).toEqual(
    ['requested', ...strings(allowed?.[2] ?? '')].sort(),
  );
  expect(source).toMatch(/revision=battle\['control_revision'\], state='requested'/u);
  expect([...TERMINAL_COMMAND_STATES].sort()).toEqual(
    strings(/TERMINAL_COMMANDS = \(([^)]+)\)/u.exec(source)?.[1] ?? '').sort(),
  );
});
