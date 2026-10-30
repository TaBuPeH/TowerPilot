import { expect, it } from 'vitest';
import { dashboardBody, dashboardCaseNames } from '../../support/dashboardFixture.ts';

const fixtureHistory: Readonly<Record<string, number | null>> = {
  active_row_first_then_interrupted_rows: 77,
  empty: null,
  ended_final_wave_only_row: 187,
  ended_with_final_wave: 187,
  interrupted_driver_observation_lost: 123,
};
it('F-079 F-080 fixture history inventory is exhaustive', () => {
  expect(dashboardCaseNames('battles_history')).toEqual(Object.keys(fixtureHistory).sort());
});
it.each(Object.entries(fixtureHistory))(
  'F-079 F-080 fixture %s reports its recorded wave and tracking outcome',
  (name, wave) => {
    const body = dashboardBody(`battles_history.${name}`);
    const view = lastBattleView({ kind: 'answered', sameContext: true, body }, 1800000000);
    if (name === 'empty') expect(view.line).toBe('no_battle_recorded');
    else {
      expect(view.battle.kind).toBe('known');
      if (view.battle.kind !== 'known') throw new Error('fixture past battle missing');
      expect(view.battle.value.wave).toBe(wave);
      expect(view.battle.value.line).toBe(
        name.startsWith('ended') ? 'ended' : 'tracking_stopped_end_not_seen',
      );
      expect(
        lastBattleView({ kind: 'failed' }, 1800000010, {
          body,
          sameContext: true,
          observedAt: 1800000000,
        }).line,
      ).toBe('last_known');
    }
  },
);
import { lastBattleView } from '../../../src/core/lastBattle.ts';
import type { ContextRead } from '../../../src/core/connection.ts';

// backend/dashboard.py api_battle_history, backend/control/battles.py public;
// workflows/battles.py finish: only ended/interrupted are backend final statuses.
const answer = (battles: unknown): ContextRead => ({
  kind: 'answered',
  sameContext: true,
  body: { battles },
});
it('F-079 active and prepared rows are skipped before the latest past record', () => {
  expect(
    lastBattleView(
      answer([
        { status: 'active' },
        { status: 'prepared' },
        { status: 'ended', label: 'raw', final_wave: 99, wave: 88, ended_at: 90 },
      ]),
      100,
    ),
  ).toMatchObject({
    line: 'recorded',
    battle: { kind: 'known', value: { line: 'ended', wave: 99, endedAt: 90 } },
  });
});
it.each(['driver_operation_ended', 'driver_observation_lost'])(
  'F-079 interrupted %s means tracking stopped with end not seen',
  (end_reason) => {
    expect(
      lastBattleView(answer([{ status: 'interrupted', end_reason, wave: 88 }]), 100),
    ).toMatchObject({
      battle: {
        kind: 'known',
        value: { line: 'tracking_stopped_end_not_seen', endReason: end_reason, wave: 88 },
      },
    });
  },
);
it('F-079 unknown final statuses retain raw status and missing wave never becomes zero', () => {
  expect(lastBattleView(answer([{ status: 'fuzzed' }]), 100)).toMatchObject({
    battle: { kind: 'known', value: { line: 'tracking_ended', status: 'fuzzed', wave: null } },
  });
  expect(
    lastBattleView(answer([{ status: 'ended', final_wave: null, wave: 7 }]), 100),
  ).toMatchObject({ battle: { value: { wave: 7 } } });
});
it('F-080 distinguishes unchecked unavailable no recorded battle and active-only records', () => {
  expect(lastBattleView(null, 100).line).toBe('not_checked');
  expect(lastBattleView({ kind: 'failed' }, 100).line).toBe('unavailable');
  expect(lastBattleView(answer([]), 100).line).toBe('no_battle_recorded');
  expect(lastBattleView(answer([{ status: 'active' }]), 100).line).toBe('no_previous_battle');
  expect(lastBattleView(answer([null]), 100).line).toBe('unavailable');
});
it('F-080 only a previous same-context record can become Last known', () => {
  const previous = { body: { battles: [{ status: 'ended' }] }, observedAt: 90, sameContext: true };
  expect(lastBattleView({ kind: 'failed' }, 100, previous).line).toBe('last_known');
  expect(lastBattleView({ kind: 'failed' }, 100, { ...previous, sameContext: false }).line).toBe(
    'unavailable',
  );
});
