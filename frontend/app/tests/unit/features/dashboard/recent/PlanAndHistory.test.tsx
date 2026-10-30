import { fireEvent, screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { expect, it } from 'vitest';
import { PlanAndHistory } from '../../../../../src/features/dashboard/recent/PlanAndHistory.tsx';
import { selectRecent } from '../../../../../src/store/selectors/recent.ts';
import { addBody, addCase, addFailure, regionState, tick } from '../../../store/regionHelpers.ts';
import { dashboardCaseNames } from '../../../../support/dashboardFixture.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { renderRegion } from '../harness.tsx';

function recent(state = regionState()) {
  return renderRegion(state, <PlanAndHistory />);
}

function scheduleRow() {
  const row = screen.getByRole('heading', { name: 'Schedule' }).closest('li');
  if (row === null) throw new Error('Missing Schedule row');
  return within(row);
}

function historyRow() {
  const row = screen.getByRole('heading', { name: 'Last battle' }).closest('li');
  if (row === null) throw new Error('Missing Last battle row');
  return within(row);
}

async function check(container: HTMLElement) {
  expect(textOutsideCatalogue(container)).toEqual([]);
  expect(await axe(container)).toHaveNoViolations();
}

it('F-077: one Schedule line shows the winner, next local clock check and server-relative countdown with its Schedule link', async () => {
  const page = recent(
    addBody(regionState(), 'schedule', {
      availability: 'available',
      winner: { label: 'Farm' },
      server_time: 1800002880,
      next_clock_boundary: { at: 1800007200 },
      utc_offset: '+0400',
    }),
  );
  const row = scheduleRow();
  expect(row.getByText('Farm')).toHaveAttribute('data-server-text', 'name');
  expect(row.getByText('14:00')).toBeInTheDocument();
  expect(row.getByText('(in 1 h 12 m)')).toBeInTheDocument();
  expect(row.getByText('waits for the run to end')).toBeInTheDocument();
  expect(row.getByRole('link', { name: 'Schedule page' })).toHaveAttribute('href', '#schedule');
  expect(
    page.view.container.querySelectorAll('[data-region="recent"] > .tp-region__body > ul > li'),
  ).toHaveLength(2);
  expect(row.queryByText('Running')).not.toBeInTheDocument();
  await check(page.view.container);
});

it('F-077: a winner with no clock boundary says no clock check planned', async () => {
  const page = recent(
    addBody(regionState(), 'schedule', {
      availability: 'available',
      winner: { label: 'Farm' },
      next_clock_boundary: null,
    }),
  );
  expect(scheduleRow().getByText('no clock check planned')).toBeInTheDocument();
  expect(scheduleRow().queryByText(/next check/)).not.toBeInTheDocument();
  await check(page.view.container);
});

it.each([
  ['no_eligible_block', 'no block open'],
  ['no_blocks_today', 'no blocks today'],
])('F-077: available schedule without a winner maps %s to %s', async (reason, text) => {
  const page = recent(
    addBody(regionState(), 'schedule', { availability: 'available', winner: null, reason }),
  );
  expect(scheduleRow().getByText(text)).toBeInTheDocument();
  expect(scheduleRow().queryByText('Unknown')).not.toBeInTheDocument();
  await check(page.view.container);
});

const expectedSchedule: Readonly<Record<string, readonly [string | null, string]>> = {
  available_no_winner: [null, 'no blocks today'],
  available_with_winner_and_boundary: [null, 'waits for the run to end'],
  plan_unavailable: ['Blocked', "the day plan can't be read"],
  scheduler_not_observed: ['Unknown', 'Not seen running'],
  scheduler_not_running: ['Stopping', 'scheduler stopping'],
  scheduler_observation_stale: ['Unknown', 'Last seen'],
  scheduler_worker_unavailable: ['Unknown', 'scheduler process not confirmed alive'],
  unknown_emulator: ['Unknown', 'schedule not confirmed'],
};

it.each(dashboardCaseNames('schedule_status'))(
  'F-078: schedule fixture %s follows its decision-table wording',
  async (name) => {
    const page = recent(addCase(regionState(), 'schedule', `schedule_status.${name}`));
    const expected = expectedSchedule[name];
    if (expected === undefined) throw new Error(`Missing expectation for ${name}`);
    const [chip, text] = expected;
    if (chip !== null) expect(scheduleRow().getByText(chip)).toBeInTheDocument();
    expect(scheduleRow().getByText(text)).toBeInTheDocument();
    expect(page.view.container).not.toHaveTextContent('Not running');
    expect(page.view.container).not.toHaveTextContent('Stopped');
    if (name === 'scheduler_observation_stale') {
      expect(scheduleRow().getByText('Farm')).toBeInTheDocument();
      expect(scheduleRow().getByText('2 m ago')).toBeInTheDocument();
    }
    if (name === 'plan_unavailable') {
      fireEvent.click(scheduleRow().getByText('Why'));
      expect(page.view.container.querySelector('[data-server-text="message"]')).toHaveTextContent(
        "profile 'mine' has CONFIG['plan'] present but null",
      );
    }
    await check(page.view.container);
  },
);

it.each([
  ['accepted', 'Waiting', 'scheduler starting'],
  ['cancelling', 'Stopping', 'scheduler stopping'],
  ['applying', 'Unknown', 'scheduler run not confirmed running'],
  ['other', 'Unknown', 'scheduler run not confirmed running'],
])(
  'F-078: scheduler_not_running with operation %s shows %s and its qualified line',
  async (operation_status, chip, line) => {
    const page = recent(
      addBody(regionState(), 'schedule', { reason: 'scheduler_not_running', operation_status }),
    );
    expect(scheduleRow().getByText(chip)).toBeInTheDocument();
    expect(scheduleRow().getByText(line)).toBeInTheDocument();
    await check(page.view.container);
  },
);

it('F-078: a stale observation age advances from 120 to 150 seconds on a 30-second tick', async () => {
  const state = addCase(regionState(), 'schedule', 'schedule_status.scheduler_observation_stale');
  const page = recent(state);
  expect(page.store.getSnapshot().nowMs).toBe(10000);
  page.setState(tick(state, 30));
  expect(scheduleRow().getByText('2 m ago')).toBeInTheDocument();
  // The age formatter rounds to minutes, so verify its selector input as well.
  expect(selectRecent(page.store.getSnapshot()).schedule.params['ageSec']).toBe(150);
  await check(page.view.container);
});

it.each(['unchecked', 'cold failure', 'last known', 'other reason'])(
  'F-078: %s schedule information stays qualified',
  async (kind) => {
    const state =
      kind === 'last known'
        ? addFailure(
            tick(
              addCase(
                regionState(),
                'schedule',
                'schedule_status.available_with_winner_and_boundary',
              ),
              42,
            ),
            'schedule',
          )
        : kind === 'cold failure'
          ? addFailure(regionState(), 'schedule')
          : kind === 'other reason'
            ? addBody(regionState(), 'schedule', {
                reason: 'future_reason',
                error: 'Preserve this server message\nExactly as sent',
              })
            : regionState();
    const page = recent(state);
    expect(scheduleRow().getByText('Unknown')).toBeInTheDocument();
    expect(
      scheduleRow().getByText(
        kind === 'last known'
          ? 'Last known:'
          : kind === 'cold failure'
            ? 'schedule unavailable'
            : kind === 'other reason'
              ? 'schedule not confirmed'
              : 'not checked yet',
      ),
    ).toBeInTheDocument();
    if (kind === 'last known') {
      expect(scheduleRow().getByText('Farm')).toBeInTheDocument();
      expect(scheduleRow().getByText('last read 42 s ago')).toBeInTheDocument();
    }
    if (kind === 'other reason') {
      fireEvent.click(scheduleRow().getByText('Why'));
      expect(page.view.container.querySelector('[data-server-text="detail"]')).toHaveTextContent(
        'future_reason',
      );
      expect(page.view.container.querySelector('[data-server-text="message"]')?.textContent).toBe(
        'Preserve this server message\nExactly as sent',
      );
    }
    await check(page.view.container);
  },
);

it.each(dashboardCaseNames('battles_history').filter((name) => name !== 'empty'))(
  'F-079: history fixture %s shows the first non-active recorded battle with truthful end words and a history link',
  async (name) => {
    const page = recent(addCase(regionState(), 'battles', `battles_history.${name}`));
    const row = historyRow();
    expect(
      row.getByText(name.startsWith('ended') ? 'Ended' : 'tracking stopped, end not seen'),
    ).toBeInTheDocument();
    expect(
      row.getByText(
        name.startsWith('ended') ? '187' : name.startsWith('active_row') ? '77' : '123',
      ),
    ).toBeInTheDocument();
    expect(row.getByText('age not available')).toBeInTheDocument();
    expect(row.getByRole('link', { name: 'Run history' })).toHaveAttribute('href', '#history');
    expect(page.view.container).not.toHaveTextContent('Interrupted');
    fireEvent.click(row.getByText('Details'));
    expect(row.getByText(/Recorded end time:/)).toBeVisible();
    await check(page.view.container);
  },
);

it.each([
  ['ended', null, 52, 'Ended', '52'],
  ['interrupted', null, null, 'tracking stopped, end not seen', 'Not recorded'],
  ['future_status', 19, 8, 'tracking ended', '19'],
])(
  'F-079: %s preserves end meaning, wave fallback and unknown waves',
  async (status, final_wave, wave, end, shownWave) => {
    const state = addBody(regionState(), 'battles', {
      server_time: 1800000000,
      battles: [
        { status: 'active', label: 'Current' },
        { status: 'prepared', label: 'Prepared' },
        { status, label: 'Previous', ended_at: 1799999928, final_wave, wave },
      ],
    });
    const page = recent(state);
    expect(historyRow().getByText(end)).toBeInTheDocument();
    expect(historyRow().getByText(shownWave)).toBeInTheDocument();
    expect(historyRow().getByText('1 m ago')).toBeInTheDocument();
    expect(historyRow().queryByText('Current')).not.toBeInTheDocument();
    expect(historyRow().queryByText('Prepared')).not.toBeInTheDocument();
    expect(historyRow().queryByText('0')).not.toBeInTheDocument();
    if (status === 'future_status') {
      fireEvent.click(historyRow().getByText('Details'));
      expect(historyRow().getByText('future_status')).toHaveAttribute('data-server-text', 'detail');
    }
    await check(page.view.container);
  },
);

it.each(['unchecked', 'unavailable', 'empty', 'last known', 'only active'])(
  'F-080: %s history has an explicit read state and no gem total',
  async (kind) => {
    const state =
      kind === 'last known'
        ? addFailure(
            tick(
              addCase(
                regionState(),
                'battles',
                'battles_history.interrupted_driver_observation_lost',
              ),
              42,
            ),
            'battles',
          )
        : kind === 'unavailable'
          ? addFailure(regionState(), 'battles')
          : kind === 'empty'
            ? addCase(regionState(), 'battles', 'battles_history.empty')
            : kind === 'only active'
              ? addBody(regionState(), 'battles', {
                  battles: [{ status: 'active' }, { status: 'prepared' }],
                })
              : regionState();
    const page = recent(state);
    expect(
      historyRow().getByText(
        kind === 'last known'
          ? 'Last known:'
          : kind === 'empty'
            ? 'No battle recorded yet'
            : kind === 'unavailable'
              ? 'Unavailable'
              : kind === 'only active'
                ? 'No previous battle recorded'
                : 'Not checked yet',
      ),
    ).toBeInTheDocument();
    if (kind === 'last known') {
      expect(historyRow().getByText('tracking stopped, end not seen')).toBeInTheDocument();
      expect(historyRow().getByText('123')).toBeInTheDocument();
    }
    expect(page.view.container).not.toHaveTextContent(/gem total/i);
    await check(page.view.container);
  },
);

it('F-080: a poll preserves an open history Details and its focused summary', async () => {
  const state = addCase(regionState(), 'battles', 'battles_history.ended_with_final_wave');
  const page = recent(state);
  const why = historyRow().getByText('Details').closest('summary');
  if (why === null) throw new Error('Missing history Details summary');
  fireEvent.click(why);
  why.focus();
  page.setState(tick(state, 15));
  expect(why.closest('details')).toHaveAttribute('open');
  expect(why).toHaveFocus();
  await check(page.view.container);
});
