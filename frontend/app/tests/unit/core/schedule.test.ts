import { expect, it } from 'vitest';
import {
  dashboardBody,
  dashboardCase,
  dashboardCaseNames,
} from '../../support/dashboardFixture.ts';

const fixtureScheduleRows: Readonly<Record<string, string>> = {
  available_no_winner: 'P5',
  available_with_winner_and_boundary: 'P4',
  plan_unavailable: 'P6',
  scheduler_not_observed: 'P7',
  scheduler_not_running: 'P8',
  scheduler_observation_stale: 'P10',
  scheduler_worker_unavailable: 'P9',
  unknown_emulator: 'P11',
};
it('P1 P2 P3 fixture schedule inventory and retained-context controls', () => {
  expect(dashboardCaseNames('schedule_status')).toEqual(Object.keys(fixtureScheduleRows).sort());
  expect(scheduleView(null).row).toBe('P1');
  const previous = {
    body: dashboardBody('schedule_status.available_with_winner_and_boundary'),
    sameContext: true,
    ageSec: 12,
  };
  expect(scheduleView({ kind: 'failed' }, previous)).toMatchObject({
    row: 'P2',
    previous: { row: 'P4' },
  });
  expect(scheduleView({ kind: 'failed' }, { ...previous, sameContext: false }).row).toBe('P3');
});
it.each(Object.entries(fixtureScheduleRows))('F-078 fixture %s follows %s', (name, row) => {
  const c = dashboardCase(`schedule_status.${name}`);
  // A received HTTP error body is still a read of the coded unavailable response.
  const view = scheduleView(answer(c.body));
  expect(view.row).toBe(row);
  if (row === 'P4') expect(view).toMatchObject({ evidence: 'EV-10', nextInSec: 7200 });
  if (row === 'P8') expect(view.word).toBe('stopping');
  if (row === 'P10') expect(view.params).toEqual({ ageSec: 120 });
  if (c.status !== 200) expect(view.word).toBe('unknown');
});
import { scheduleView } from '../../../src/core/schedule.ts';
import type { ContextRead } from '../../../src/core/connection.ts';

// Constructed backend/control/schedule_status.py read response.
const answer = (body: unknown): ContextRead => ({ kind: 'answered', sameContext: true, body });
const available = {
  availability: 'available',
  server_time: 100,
  reason: 'eligible',
  winner: { label: 'raw winner' },
  next_clock_boundary: { at: 160 },
};
it('P1 no read is unchecked', () => {
  expect(scheduleView(null).row).toBe('P1');
});
it('P2 failed reads retain only a previous same-context answer', () => {
  const view = scheduleView({ kind: 'failed' }, { body: available, sameContext: true, ageSec: 4 });
  expect(view).toMatchObject({
    row: 'P2',
    word: 'unknown',
    params: { ageSec: 4 },
    previous: { row: 'P4', winner: available.winner },
  });
});
it('P3 a failed or foreign-context read without retained context is unavailable', () => {
  expect(scheduleView({ kind: 'failed' }).row).toBe('P3');
  expect(
    scheduleView({ kind: 'failed' }, { body: available, sameContext: false, ageSec: 0 }).row,
  ).toBe('P3');
  expect(scheduleView({ kind: 'answered', body: available, sameContext: false }).row).toBe('P3');
});
it('P4 observed winner and clock boundary use only server time', () => {
  expect(scheduleView(answer(available))).toMatchObject({
    row: 'P4',
    word: null,
    evidence: 'EV-10',
    nextInSec: 60,
  });
  expect(scheduleView(answer({ ...available, next_clock_boundary: null }))).toMatchObject({
    row: 'P4',
    line: 'winner_no_clock_check',
    nextInSec: null,
  });
  expect(scheduleView(answer({ ...available, server_time: '100' })).nextInSec).toBeNull();
});
it.each(['no_eligible_block', 'no_blocks_today'])('P5 %s is an observed empty plan', (reason) => {
  expect(scheduleView(answer({ ...available, winner: null, reason }))).toMatchObject({
    row: 'P5',
    line: reason,
    evidence: 'EV-10',
  });
});
it('P6 unreadable plan is Blocked and its error remains raw', () => {
  const error = { unsafe: '<script>' };
  expect(scheduleView(answer({ reason: 'plan_unavailable', error }))).toMatchObject({
    row: 'P6',
    word: 'blocked',
    error,
  });
});
it('P7 no scheduler observation never claims Not running', () => {
  expect(scheduleView(answer({ reason: 'scheduler_not_observed' }))).toMatchObject({
    row: 'P7',
    word: 'unknown',
    line: 'not_seen_running',
  });
});
it.each([
  ['cancelling', 'stopping'],
  ['accepted', 'waiting'],
  ['applying', 'unknown'],
  ['running', 'unknown'],
  ['cancelled', 'unknown'],
  ['constructor', 'unknown'],
])('P8 scheduler_not_running with %s is %s', (operation_status, word) => {
  expect(scheduleView(answer({ reason: 'scheduler_not_running', operation_status }))).toMatchObject(
    { row: 'P8', word },
  );
});
it('P9 an unverifiable worker does not prove a stopped process', () => {
  expect(scheduleView(answer({ reason: 'scheduler_worker_unavailable' }))).toMatchObject({
    row: 'P9',
    word: 'unknown',
  });
});
it('P10 stale winner is a last-seen value with server-relative age', () => {
  expect(
    scheduleView(
      answer({
        reason: 'scheduler_observation_stale',
        server_time: 200,
        observed_at: 100,
        observed_winner: { label: 'previous' },
      }),
    ),
  ).toMatchObject({
    row: 'P10',
    word: 'unknown',
    params: { ageSec: 100 },
    observedWinner: { label: 'previous' },
  });
});
it('P11 unknown reasons and inherited availability never become observed', () => {
  for (const body of [
    { reason: 'constructor' },
    {},
    { availability: 'available', winner: null },
    Object.create(available) as unknown,
  ])
    expect(scheduleView(answer(body)).row).toBe('P11');
});
