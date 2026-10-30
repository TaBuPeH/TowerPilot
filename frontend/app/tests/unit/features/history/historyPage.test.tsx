import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { expect, it } from 'vitest';
import { HistoryPage } from '../../../../src/features/history/HistoryPage.tsx';
import { createHistoryStore } from '../../../../src/store/history.ts';
import { historyRow } from '../../../../src/store/selectors/history.ts';
import { ownPath } from '../../../../src/core/read.ts';
import { regionState } from '../../store/regionHelpers.ts';
import { historyCase, historyTarget, historyTransport } from '../../../support/historyFixture.ts';
import { renderRegion } from '../dashboard/harness.tsx';

async function harness(name: string) {
  const api = historyTransport();
  const store = createHistoryStore(api.reader, () => 1000);
  const answer = historyCase('history_detail', name);
  store.setTarget(historyTarget);
  const page = renderRegion(regionState(), <HistoryPage history={store} />);
  await act(async () => {
    api.body(0, { ...answer.body, battles: [answer.body['battle']] });
    await Promise.resolve();
  });
  const row = screen.getByRole('button', { name: /Unnamed battle|Farm/ });
  // Until a record is chosen the list has the page: no empty Selected record pane.
  expect(screen.queryByRole('heading', { name: 'Selected record' })).toBeNull();
  expect(screen.getByText('Choose a record to inspect its evidence.')).toBeVisible();
  // The whole record ID is in the document, never an ellipsised cut of it.
  const battle = answer.body['battle'] as Record<string, unknown>;
  expect(row.querySelector('.tp-history__record-id [data-server-text="id"]')?.textContent).toBe(
    String(battle['id']),
  );
  fireEvent.click(row);
  await act(async () => {
    api.answer(1, 'history_detail', name);
    await Promise.resolve();
  });
  return { api, store, page, row, answer };
}

it('H2: null values stay distinct from zero and unknown raw statuses remain visible', () => {
  const original = historyCase('history_detail', 'prepared_nothing_observed').body['battle'];
  expect(
    historyRow({ ...Object(original), tier: null, final_wave: null, started_at: null }),
  ).toMatchObject({ tier: null, finalWave: null, startedAt: null });
  expect(historyRow({ ...Object(original), tier: 0, final_wave: 0, started_at: 0 })).toMatchObject({
    tier: 0,
    finalWave: 0,
    startedAt: 0,
  });
  expect(historyRow({ ...Object(original), status: 'new_server_state' }).status).toBe(
    'new_server_state',
  );
});

it.each([
  'setup_verified',
  'setup_degraded_module_not_equipped',
  'active_adopted_partial_observation',
  'stats_capture_unverified_wrong_tier',
  'ended_after_an_observation_gap',
  'interrupted_before_any_observation_everything_unknown',
])('H3: truthful %s evidence and axe', async (name) => {
  const { page, store } = await harness(name);
  expect(screen.getByRole('heading', { name: 'Selected record' })).toHaveFocus();
  expect(screen.getByText(/These are sampled observations/)).toBeInTheDocument();
  const setup = screen.getByRole('region', { name: 'Setup evidence' });
  expect(setup).toHaveTextContent(
    name === 'setup_verified'
      ? 'verified'
      : name === 'setup_degraded_module_not_equipped'
        ? 'degraded'
        : 'unknown',
  );
  if (name === 'active_adopted_partial_observation')
    expect(screen.getByText(/Partial history:/)).toBeInTheDocument();
  if (name === 'stats_capture_unverified_wrong_tier')
    expect(
      within(screen.getByRole('region', { name: 'Stats capture' })).getByText('Unverified'),
    ).toBeInTheDocument();
  if (name.startsWith('interrupted_'))
    expect(screen.getByText(/does not establish defeat/)).toBeInTheDocument();
  expect(await axe(page.view.container)).toHaveNoViolations();
  store.dispose();
});

it.each([
  ['stats_capture_verified', 'Verified for this record', 'verified'],
  ['stats_capture_unverified_not_fresh', 'Unverified', 'captured_unverified'],
  ['stats_capture_unverified_not_structured', 'Unverified', 'captured_unverified'],
  ['stats_capture_unverified_wrong_tier', 'Unverified', 'captured_unverified'],
  ['stats_capture_of_an_adopted_battle_stays_unverified', 'Unverified', 'captured_unverified'],
  ['stats_capture_failed_no_artifact', 'Capture failed', 'failed'],
  ['stats_capture_failed_with_artifact', 'Capture failed', 'failed'],
  ['stats_capture_pending', 'Capture pending', 'pending'],
  ['stats_capture_missing_after_the_battle_ended', 'Missing / not recorded', null],
  ['stats_capture_missing_while_the_battle_is_live', 'Missing / not recorded', null],
] as const)(
  'H3 F6-HISTORY-01: %s preserves %s independently of verification',
  async (name, expected, recorded) => {
    const { store, page, answer } = await harness(name);
    const section = within(screen.getByRole('region', { name: 'Stats capture' }));
    expect(section.getByText(expected, { exact: true })).toBeVisible();
    expect(section.getByText('Recorded capture state').nextElementSibling).toHaveTextContent(
      recorded ?? 'Unknown / not recorded',
    );
    if (expected !== 'Unverified')
      expect(section.queryByText('Unverified', { exact: true })).toBeNull();
    for (const key of ['reason', 'error']) {
      const message = ownPath(answer.body, 'battle', 'runtime', 'stats_capture', key);
      if (typeof message === 'string')
        expect(section.getByText(message, { exact: true })).toBeVisible();
    }
    expect(await axe(page.view.container)).toHaveNoViolations();
    store.dispose();
  },
);

it.each([
  ['setup_verified', 'verified'],
  ['setup_degraded_module_not_equipped', 'degraded'],
  ['setup_unknown_not_verified_for_this_battle', 'unknown'],
] as const)('H3 F6-HISTORY-01 audit: %s preserves its setup state %s', async (name, expected) => {
  const { store } = await harness(name);
  const section = within(screen.getByRole('region', { name: 'Setup evidence' }));
  expect(section.getByText('Recorded setup state').nextElementSibling).toHaveTextContent(expected);
  store.dispose();
});

it.each([
  ['collection_sampled_confirmations_ended', ['2', '1', '2']],
  ['collection_same_confirmation_published_twice_counted_once', ['1', '0', '0']],
  ['collection_confirmation_older_than_the_battle_not_counted', ['0', '0', '0']],
  ['collection_confirmed_with_the_wrong_reason_not_counted', ['0', '0', '0']],
  ['collection_unconfirmed_outcomes_not_counted', ['0', '0', '0']],
  [
    'collection_evidence_of_another_generation_written_through_the_ledger',
    ['Unknown / not recorded', 'Unknown / not recorded', 'Unknown / not recorded'],
  ],
] as const)(
  'H3 F6-HISTORY-01 audit: %s preserves counted, not-counted and withheld collection evidence',
  async (name, expected) => {
    const { store } = await harness(name);
    const section = within(screen.getByRole('region', { name: 'Sampled collection evidence' }));
    for (const [index, label] of [
      'Recorded ad claims seen',
      'Recorded orbit balance confirmations',
      'Recorded orbit balance increase in this sample',
    ].entries()) {
      expect(section.getByText(label).nextElementSibling?.textContent).toBe(expected[index]);
    }
    store.dispose();
  },
);

it.each([
  'observation_gap_still_open',
  'observation_gap_closed_after_the_screen_returned',
  'ended_after_an_observation_gap',
])(
  'H3 F6-HISTORY-01 audit: %s preserves exact gap intervals and open-gap evidence',
  async (name) => {
    const { store, answer } = await harness(name);
    const section = within(screen.getByRole('region', { name: 'Observation gaps' }));
    expect(section.getByRole('region', { name: 'Recorded gap intervals' }).textContent).toBe(
      JSON.stringify(ownPath(answer.body, 'battle', 'runtime', 'observation_gaps'), null, 2),
    );
    const open = ownPath(answer.body, 'battle', 'runtime', 'observation_gap_started_at');
    expect(section.getByText('Open gap began').nextElementSibling?.textContent).toBe(
      typeof open === 'number' ? new Date(open * 1000).toISOString() : 'Unknown / not recorded',
    );
    store.dispose();
  },
);

it('H4: disclosure, focus and last-loaded detail survive refresh failure; keyboard Back restores the records anchor', async () => {
  const { api, page, store } = await harness('setup_verified');
  const snapshot = screen.getByText('Configuration snapshot').closest('summary');
  if (snapshot === null) throw new Error('Missing disclosure');
  fireEvent.click(snapshot);
  snapshot.focus();
  await act(async () => {
    store.refreshDetail();
    api.fail(2);
    await Promise.resolve();
  });
  expect(snapshot).toHaveFocus();
  expect(snapshot.closest('details')).toHaveAttribute('open');
  expect(screen.getAllByText('Last loaded 1970-01-01T00:00:01.000Z')).toHaveLength(2);
  const retry = screen.getByRole('button', { name: 'Retry detail' });
  retry.focus();
  await userEvent.keyboard('{Enter}');
  expect(retry).toHaveFocus();
  expect(api.pending).toHaveLength(4);
  await act(async () => {
    api.answer(3, 'history_detail', 'setup_verified');
    await Promise.resolve();
  });
  expect(screen.getByRole('button', { name: 'Refresh detail' })).toHaveFocus();
  const back = screen.getByRole('button', { name: 'Back to records' });
  back.focus();
  await userEvent.keyboard('{Enter}');
  expect(screen.getByRole('heading', { name: /^Records$/ })).toHaveFocus();
  expect(await axe(page.view.container)).toHaveNoViolations();
  store.dispose();
});

it('H4: Back while detail is delayed keeps focus on records after the answer arrives', async () => {
  const api = historyTransport();
  const store = createHistoryStore(api.reader);
  const detail = historyCase('history_detail', 'setup_verified');
  store.setTarget(historyTarget);
  renderRegion(regionState(), <HistoryPage history={store} />);
  await act(async () => {
    api.body(0, { ...detail.body, battles: [detail.body['battle']] });
    await Promise.resolve();
  });
  fireEvent.click(screen.getByRole('button', { name: /Farm/ }));
  expect(screen.getByRole('heading', { name: 'Selected record' })).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Back to records' }));
  const records = screen.getByRole('heading', { name: /^Records$/ });
  expect(records).toHaveFocus();
  await act(async () => {
    api.answer(1, 'history_detail', 'setup_verified');
    await Promise.resolve();
  });
  expect(records).toHaveFocus();
  expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull();
  store.dispose();
});
