import { expect, it } from 'vitest';
import { lastBattleView } from '../../../src/core/lastBattle.ts';
import { scheduleView } from '../../../src/core/schedule.ts';
import { serverTextContent } from '../../../src/copy/server.ts';
import { reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { contextReadOf } from '../../../src/store/reads.ts';
import { selectRecent } from '../../../src/store/selectors/recent.ts';
import { chipVM } from '../../../src/store/viewModels.ts';
import { dashboardCaseNames } from '../../support/dashboardFixture.ts';
import { addBody, addCase, addFailure, expectStateVM, regionState, tick } from './regionHelpers.ts';

it.each(dashboardCaseNames('schedule_status'))(
  'schedule fixture %s follows the core table without wire records',
  (name) => {
    const state = addCase(regionState(), 'schedule', `schedule_status.${name}`);
    const vm = selectRecent(state);
    expect(vm.schedule).toMatchObject(
      chipVM(scheduleView(contextReadOf(state.reads.schedule.latest))),
    );
    if (name === 'available_with_winner_and_boundary') expect(vm.schedule.nextInSec).toBe(7200);
    if (name === 'scheduler_not_running') expect(vm.schedule.word).toBe('stopping');
    if (name === 'scheduler_observation_stale') expect(vm.schedule.params).toEqual({ ageSec: 120 });
    expectStateVM(vm, state);
  },
);

it.each(dashboardCaseNames('battles_history'))(
  'history fixture %s carries only its recorded facts',
  (name) => {
    const state = addCase(regionState(), 'battles', `battles_history.${name}`);
    const expected = lastBattleView(contextReadOf(state.reads.battles.latest), 10);
    const vm = selectRecent(state);
    expect(vm.lastBattle.line).toBe(expected.line);
    expect(vm.lastBattle.battle.kind).toBe(expected.battle.kind);
    if (vm.lastBattle.battle.kind === 'known' && expected.battle.kind === 'known') {
      expect(vm.lastBattle.battle.value.wave).toBe(expected.battle.value.wave);
      expect(vm.lastBattle.battle.value.line).toBe(expected.battle.value.line);
      expect(vm.lastBattle.battle.value.endedAt).toBe(expected.battle.value.endedAt);
    }
    expectStateVM(vm, state);
  },
);

it('failed schedule and history reads retain only qualified previous answers with their original ages', () => {
  let state = addCase(
    regionState(),
    'schedule',
    'schedule_status.available_with_winner_and_boundary',
  );
  state = addCase(state, 'battles', 'battles_history.interrupted_driver_observation_lost');
  state = addFailure(addFailure(tick(state, 42), 'schedule'), 'battles');
  const vm = selectRecent(state);
  expect(vm.schedule).toMatchObject({ row: 'P2', word: 'unknown', params: { ageSec: 42 } });
  expect(vm.schedule.previous?.row).toBe('P4');
  expect(vm.lastBattle.line).toBe('last_known');
  expect(vm.lastBattle.battle).toMatchObject({
    kind: 'known',
    observedAt: 10,
    value: { line: 'tracking_stopped_end_not_seen', wave: 123 },
  });
  expectStateVM(vm, state);
  const changed = reduceDashboard(state, {
    type: 'context',
    context: { instance: 'other', serial: 'fake:2', account: null, profile: 'mine' },
    epoch: 1,
    confirmed: true,
    sessionContext: null,
  });
  expect(selectRecent(changed)).toMatchObject({
    schedule: { row: 'P1', previous: null },
    lastBattle: { line: 'not_checked' },
  });
});

it('cold failures and malformed history stay unknown; absent waves never become zero', () => {
  const cold = addFailure(addFailure(regionState(), 'schedule'), 'battles');
  expect(selectRecent(cold)).toMatchObject({
    schedule: { row: 'P3' },
    lastBattle: { line: 'unavailable' },
  });
  const state = addBody(regionState(), 'battles', {
    battles: [{ status: 'alien', label: 'A raw label', blueprint: 'bp_farm' }],
  });
  const battle = selectRecent(state).lastBattle.battle;
  if (battle.kind !== 'known' || battle.value.label === null) throw new Error('Missing battle');
  expect(battle.value.wave).toBeNull();
  expect(battle.value.line).toBe('tracking_ended');
  expect(serverTextContent(battle.value.label)).toBe('A raw label');
  expectStateVM(selectRecent(state), state);
  expect(selectRecent(addBody(state, 'battles', { battles: [null] })).lastBattle.line).toBe(
    'unavailable',
  );
});

it('advances the observed schedule countdown by elapsed store time without computing eligibility', () => {
  const state = addCase(
    regionState(),
    'schedule',
    'schedule_status.available_with_winner_and_boundary',
  );
  expect(selectRecent(tick(state, 30)).schedule.nextInSec).toBe(7170);
  expect(selectRecent(tick(state, 8000)).schedule.nextInSec).toBe(0);
});
