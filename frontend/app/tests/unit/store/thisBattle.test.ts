import { expect, it } from 'vitest';
import { battleIdentity, battleView } from '../../../src/core/battle.ts';
import { commandView } from '../../../src/core/commands.ts';
import { list, own, ownPath } from '../../../src/core/read.ts';
import { actionGate, routineResult, routineTiming } from '../../../src/core/routines.ts';
import { serverTextContent } from '../../../src/copy/server.ts';
import { reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { selectThisBattle } from '../../../src/store/selectors/thisBattle.ts';
import { chipVM } from '../../../src/store/viewModels.ts';
import { dashboardBody, dashboardCaseNames } from '../../support/dashboardFixture.ts';
import {
  addBody,
  addCase,
  addFailure,
  expectStateVM,
  intent,
  regionState,
  tick,
} from './regionHelpers.ts';

it.each(dashboardCaseNames('battles_active'))(
  'wiring check: projects battle, routines and commands from %s exactly as core decides',
  (name) => {
    const body = dashboardBody(`battles_active.${name}`);
    const state = addBody(regionState(), 'battlesActive', body);
    const identity = battleIdentity(own(body, 'battle'), 10);
    const shown = identity.kind === 'known' ? identity.value : null;
    const read = { kind: 'answered', sameContext: true, body } satisfies Parameters<
      typeof battleView
    >[0];
    const vm = selectThisBattle(state);
    expect(vm.header).toEqual(chipVM(battleView(read, shown)));
    expect(vm.noTrackedBattle).toBe(own(body, 'battle') === null);
    expect(vm.commands).toMatchObject(
      (list(ownPath(body, 'battle', 'commands')) ?? []).map((command) =>
        chipVM(commandView(command).decision),
      ),
    );
    for (const row of vm.routines) {
      expect(row.timing).toEqual(chipVM(routineTiming(read, shown, row.routine)));
      expect(row.result.chip).toEqual(
        chipVM(routineResult(ownPath(body, 'battle', 'runtime'), row.routine).decision),
      );
      expect(row.runNow.enabled).toBe(actionGate(read, shown, row.routine, false).enabled);
    }
    expectStateVM(vm, state);
  },
);

it('B-06 literal: an action command started is Waiting (K3), only completed is Done (K4)', () => {
  const started = selectThisBattle(
    addCase(
      regionState(),
      'battlesActive',
      'battles_active.commands_controls_pending_no_path_applied_action_started',
    ),
  );
  // The fixture's commands in order: controls applied, action started, controls pending.
  expect(started.commands[1]).toMatchObject({ row: 'K3', word: 'waiting' });
  expect(started.commands[1]?.word).not.toBe('done');
  const completed = selectThisBattle(
    addCase(
      regionState(),
      'battlesActive',
      'battles_active.commands_controls_pending_some_paths_applied_action_completed',
    ),
  );
  // Controls applied, action completed, controls applied, controls pending.
  expect(completed.commands[1]).toMatchObject({ row: 'K4', evidence: 'record' });
  expect(started.commands[1]?.evidence).not.toBe('record');
});

it('counts down from arrival then expires; failures cannot retain a Running header', () => {
  const state = addCase(regionState(), 'battlesActive', 'battles_active.runtime_ad_orbit_shopping');
  const first = selectThisBattle(state);
  expect(first.header.row).toBe('B5');
  const later = selectThisBattle(tick(state, 1));
  for (const row of first.routines) {
    const next = later.routines.find((r) => r.routine === row.routine);
    if (row.nextInSec !== null) expect(next?.nextInSec).toBe(Math.max(0, row.nextInSec - 1));
  }
  expect(selectThisBattle(tick(state, 16)).header.row).toBe('B7');
  expect(selectThisBattle(addFailure(state, 'battlesActive')).header.row).toBe('B1');
  expect(selectThisBattle(regionState()).routines.every((r) => !r.runNow.enabled)).toBe(true);
});

it('preserves completed-action evidence and failed or rejected command reasons as ServerText', () => {
  const state = addCase(
    regionState(),
    'battlesActive',
    'battles_active.commands_controls_rejected_action_rejected',
  );
  const vm = selectThisBattle(state);
  const done = vm.commands.find((row) => row.row === 'K4' && row.result.confirmed);
  expect(done?.result.observedAt).toBe(1800000010);
  if (done?.result.reason === undefined || done.result.reason === null)
    throw new Error('Missing action evidence');
  expect(serverTextContent(done.result.reason)).toBe('offer_disappeared');
  const failures = vm.commands
    .filter((row) => row.row === 'K5')
    .map((row) => (row.reason === null ? null : serverTextContent(row.reason)));
  expect(failures).toEqual(['tap_refused', 'driver_refused', 'battle_not_visible']);
  expectStateVM(vm, state);
});

it('unknown Run now outcomes block only their current battle, generation, operation and action', () => {
  const state = addCase(regionState(), 'battlesActive', 'battles_active.runtime_ad_orbit_shopping');
  const command = selectThisBattle(state).routines[0]?.runNow.command;
  if (command === undefined || command === null) throw new Error('Missing fixture action');
  const record = intent(state, { meta: command });
  const withIntent = (patch: Parameters<typeof intent>[1] = {}) =>
    selectThisBattle(
      reduceDashboard(state, {
        type: 'intents',
        records: [{ ...record, ...patch }],
      }),
    );
  expect(withIntent().routines[0]?.runNow.reason).toBe('earlier_outcome_unknown');
  expect(withIntent().routines[1]?.runNow.enabled).toBe(true);
  expect(withIntent({ phase: 'sending' }).routines[0]?.runNow.sending).toBe(true);
  expect(withIntent({ phase: 'sending' }).routines[0]?.runNow.enabled).toBe(false);
  for (const meta of [
    { ...command, generation: 'old' },
    { ...command, battleId: 'old' },
    { ...command, operationId: 'old' },
  ])
    expect(withIntent({ meta }).routines[0]?.runNow.enabled).toBe(true);
  expect(
    withIntent({ context: { instance: 'other', serial: 'fake:2', account: null, profile: 'mine' } })
      .routines[0]?.runNow.enabled,
  ).toBe(true);
});

it('a changed generation drops the old Run now outcome and null battle hides the routine table', () => {
  let state = addCase(regionState(), 'battlesActive', 'battles_active.runtime_ad_orbit_shopping');
  const command = selectThisBattle(state).routines[0]?.runNow.command;
  if (command === undefined || command === null) throw new Error('Missing action');
  state = reduceDashboard(state, { type: 'intents', records: [intent(state, { meta: command })] });
  const changed = addCase(state, 'battlesActive', 'battles_active.replaced_generation');
  expect(selectThisBattle(changed).routines[0]?.runNow.reason).toBe('battle_not_active');
  const empty = selectThisBattle(
    addCase(changed, 'battlesActive', 'battles_active.null_no_driver'),
  );
  expect(empty).toMatchObject({
    noTrackedBattle: true,
    routines: [],
    header: { line: 'no_tracked_battle', word: null, evidence: 'record' },
  });
});
