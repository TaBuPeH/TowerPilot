import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { expect, it } from 'vitest';
import { HistoryPage } from '../../../../src/features/history/HistoryPage.tsx';
import { createHistoryStore } from '../../../../src/store/history.ts';
import { regionState } from '../../store/regionHelpers.ts';
import { dashboardCase } from '../../../support/dashboardFixture.ts';
import { historyCase, historyTarget, historyTransport } from '../../../support/historyFixture.ts';
import { objectOf, r17bBody, rowsOf, without } from '../../../support/historyExecutions.ts';
import { renderRegion } from '../dashboard/harness.tsx';

/**
 * A2-7 AC4: "Actions in this battle" in the History detail, over the landed r17b recordings
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json) read through the real reader and wire parser. A case changed
 * from its recording says why beside the change.
 */
const STARTED = 'action_executions.battle_detail_of_a_started_battle_lists_its_executions';
const ADOPTED =
  'action_executions.battle_detail_of_an_adopted_battle_lists_its_executions_covered_since_the_adoption';
const NEWER = 'action_executions.battle_detail_on_a_ledger_a_newer_build_wrote_is_not_interpreted';
const BEFORE = 'action_executions.battle_detail_recorded_before_the_action_store_existed';
const SETTLED = 'action_executions_more.battle_detail_after_the_refresher_settled_an_open_row';
const TRUNCATED = 'action_executions_more.battle_detail_truncated_at_fifty';
const CONTINUED =
  'action_executions_more.battle_detail_truncated_continued_through_the_generic_route';
const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });
};

/** Open the History page on one detail body (a recording, or a changed copy of one). */
async function open(detail: Record<string, unknown>, pick: RegExp = /Farm/) {
  const api = historyTransport();
  const store = createHistoryStore(api.reader, () => 1000);
  store.setTarget(historyTarget);
  const page = renderRegion(regionState(), <HistoryPage history={store} />);
  await act(async () => {
    api.body(0, { ...historyCase('history_list', 'first_page').body, battles: [detail['battle']] });
    await Promise.resolve();
  });
  fireEvent.click(screen.getByRole('button', { name: pick }));
  await act(async () => {
    api.body(1, detail);
    await Promise.resolve();
  });
  const section = screen.getByRole('region', { name: 'Actions in this battle' });
  return { api, store, page, section, within: within(section) };
}
const named = (section: HTMLElement, name: string) => {
  const item = within(section)
    .getAllByRole('listitem')
    .find((candidate) => within(candidate).queryByText(name, { exact: true }) !== null);
  if (item === undefined) throw new Error(`No execution named ${name}`);
  return within(item);
};
const fact = (
  scope: { getByText: (text: string, options: { exact: boolean }) => HTMLElement },
  label: string,
) => scope.getByText(label, { exact: true }).nextElementSibling;

it('AC4 a started battle lists its executions newest first with trigger, state, effect and duration', async () => {
  const { section, within: inside, store } = await open(r17bBody(STARTED));
  const items = inside.getAllByRole('listitem');
  expect(items.map((item) => item.querySelector('strong')?.textContent)).toEqual([
    'Floating gems',
    'Ad gems',
  ]);
  const ad = named(section, 'Ad gems');
  expect(fact(ad, 'Trigger')).toHaveTextContent('Automatic');
  expect(fact(ad, 'State')).toHaveTextContent('Succeeded');
  expect(fact(ad, 'Effect')).toHaveTextContent('Offer gone after tap');
  expect(fact(ad, 'Duration')).toHaveTextContent('4 s');
  expect(fact(ad, 'Coverage')).toHaveTextContent('Full record');
  expect(fact(ad, 'Action code')).toHaveTextContent('ad_gems');
  const floating = named(section, 'Floating gems');
  expect(fact(floating, 'State')).toHaveTextContent('Checked, nothing to do');
  expect(fact(floating, 'Effect')).toHaveTextContent('No game effect');
  expect(fact(floating, 'Occurrences in this entry')).toHaveTextContent('1');
  expect(fact(floating, 'First check')).toHaveTextContent(
    new Date(1800000520 * 1000).toISOString(),
  );
  expect(floating.queryByText('Duration', { exact: true })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Older executions' })).toBeNull();
  store.dispose();
});

it('AC4 an adopted battle says its coverage starts at the adoption', async () => {
  const { section, store } = await open(r17bBody(ADOPTED));
  expect(within(section).getAllByText('Since this automation adopted the battle')).toHaveLength(2);
  store.dispose();
});

it.each([
  [NEWER, /Written by a newer version of Tower Pilot, so it is not shown/],
  [BEFORE, /Recorded before action history existed/],
])('AC4 %s never reads as nothing ran', async (name, sentence) => {
  const { section, store } = await open(r17bBody(name));
  expect(within(section).getByText(sentence)).toBeVisible();
  expect(within(section).queryByText(/No action executions/)).toBeNull();
  expect(within(section).queryByRole('listitem')).toBeNull();
  store.dispose();
});

it.each(['setup_verified', 'active_adopted_partial_observation', 'ended_after_an_observation_gap'])(
  'AC4 the recorded history_detail %s answer says its actions were recorded before action history',
  async (name) => {
    const { section, store } = await open(historyCase('history_detail', name).body);
    expect(within(section).getByText(/Recorded before action history existed/)).toBeVisible();
    expect(within(section).queryByText(/No action executions/)).toBeNull();
    store.dispose();
  },
);

it('AC4 an answer with no executions member is not reported, and an empty list is a recorded empty history', async () => {
  // Modified recorded case: the executions member removed from a started-battle detail.
  const bare = without(r17bBody(STARTED), 'executions');
  const first = await open(bare);
  expect(within(first.section).getByText(/did not report action history/)).toBeVisible();
  first.store.dispose();
  document.body.innerHTML = '';
  // Modified recorded case: the items of a started-battle detail emptied.
  const second = await open({
    ...r17bBody(STARTED),
    executions: { items: [], reason: null, truncated: false },
  });
  expect(
    within(second.section).getByText('No action executions are recorded for this battle.'),
  ).toBeVisible();
  second.store.dispose();
});

it('AC4 an open row reads "open when read" with the read time, never "running now", with no effect or duration', async () => {
  // Modified recorded case: the open floating_gems row of read_an_open_row_before_the_refresher_settles_it
  // placed in the settled-row battle detail, which no recording holds an open row in.
  const open_ = r17bBody('action_executions_more.read_an_open_row_before_the_refresher_settles_it');
  const detail = r17bBody(SETTLED);
  const { section, store } = await (async () =>
    open({
      ...detail,
      executions: { items: open_['executions'], reason: null, truncated: false },
    }))();
  const row = named(section, 'Floating gems');
  expect(fact(row, 'State')).toHaveTextContent('Open when read');
  expect(fact(row, 'State')).toHaveTextContent('Read 1970-01-01T00:00:01.000Z');
  expect(fact(row, 'Finished')).toHaveTextContent('Not finished when read');
  expect(row.queryByText('Effect', { exact: true })).toBeNull();
  expect(row.queryByText('Duration', { exact: true })).toBeNull();
  expect(section.textContent).not.toMatch(/running|now\b/iu);
  store.dispose();
});

it('AC4 a row the refresher settled reads as stopped early, with no finish time, no duration and its reason under Why', async () => {
  const { section, store } = await open(r17bBody(SETTLED));
  const row = named(section, 'Floating gems');
  expect(fact(row, 'State')).toHaveTextContent('Stopped before it finished');
  expect(fact(row, 'Effect')).toHaveTextContent('Effect unknown');
  expect(fact(row, 'Finished')).toHaveTextContent('Not recorded: stopped before it finished');
  expect(fact(row, 'Duration')).toHaveTextContent('Not recorded');
  const why = row.getByText('Why').closest('details');
  expect(why).toHaveTextContent('operation_ended');
  store.dispose();
});

it('AC4 a Run now row, a failed row with an unconfirmed effect and a harvest_launch row read as recorded', async () => {
  const manual = rowsOf(
    objectOf(
      r17bBody('action_executions_more.battle_detail_lists_the_run_now_execution')['executions'],
    )['items'],
  );
  const older = rowsOf(
    r17bBody('action_executions.read_a_bare_start_time_returns_strictly_older_rows')['executions'],
  );
  const failed = older.find((row) => row['state'] === 'failed');
  const harvest = rowsOf(
    r17bBody('action_executions_more.read_harvest_launch_accepted')['executions'],
  );
  if (failed === undefined) throw new Error('No failed recording');
  // Modified recorded case: rows of three recordings joined in one started-battle detail, because
  // no recorded battle detail holds a failed row or a harvest_launch row.
  const { section, store } = await open({
    ...r17bBody(STARTED),
    executions: { items: [...manual, failed, ...harvest], reason: null, truncated: false },
  });
  const run = named(section, 'Ad gems');
  expect(fact(run, 'Trigger')).toHaveTextContent('Manual (Run now)');
  // The duration is the one readable duration of src/copy/format.ts, whole seconds: 23349.9999 ms reads 23 s.
  expect(fact(run, 'Duration')).toHaveTextContent('23 s');
  const bad = within(section)
    .getAllByRole('listitem')
    .find((item) => within(item).queryByText('Failed', { exact: true }) !== null);
  if (bad === undefined) throw new Error('No failed row');
  expect(fact(within(bad), 'Effect')).toHaveTextContent('Finished, effect not confirmed');
  expect(fact(within(bad), 'Duration')).toHaveTextContent('15 s');
  expect(within(bad).getByText('Why').closest('details')).toHaveTextContent('attempt_timeout');
  const launches = within(section).getAllByText('Harvest collector launch');
  expect(launches).toHaveLength(2);
  expect(within(section).getByText('Refused', { exact: true })).toBeVisible();
  expect(within(section).getByText('emulator_offline')).toBeInTheDocument();
  store.dispose();
});

it('AC4 a null label shows the action code in plain monospace; a word this build does not know shows as sent', async () => {
  // Modified recorded case: label null on both started-battle rows (no recording has a null
  // label), and a state, trigger and effect no recording carries on the first.
  const detail = r17bBody(STARTED);
  const items = rowsOf(objectOf(detail['executions'])['items']).map((item, index) => ({
    ...item,
    label: null,
    ...(index === 0
      ? {
          state: 'paused_by_future',
          trigger: 'schedule',
          outcome: { ...objectOf(item['outcome']), effect: 'partial' },
        }
      : {}),
  }));
  const { section, store } = await open({
    ...detail,
    executions: { items, reason: null, truncated: false },
  });
  const heads = within(section)
    .getAllByRole('listitem')
    .map((item) => item.querySelector('strong code'));
  expect(heads.map((head) => head?.textContent)).toEqual(['floating_gems', 'ad_gems']);
  expect(heads.every((head) => head?.classList.contains('tp-history__code'))).toBe(true);
  expect(within(section).queryByText('Action code')).toBeNull();
  const first = within(section).getAllByRole('listitem')[0];
  if (first === undefined) throw new Error('No row');
  expect(fact(within(first), 'State')).toHaveTextContent('Not recognised: paused_by_future');
  expect(fact(within(first), 'Trigger')).toHaveTextContent('Not recognised: schedule');
  expect(fact(within(first), 'Effect')).toHaveTextContent('Not recognised: partial');
  store.dispose();
});

it('AC4 a truncated battle says so and the older-executions button follows next_before to the end by keyboard', async () => {
  const { api, section, store, page } = await open(r17bBody(TRUNCATED));
  expect(within(section).getAllByRole('listitem')).toHaveLength(50);
  expect(
    within(section).getByText('Older executions exist. Only the newest ones are shown.'),
  ).toBeVisible();
  const button = within(section).getByRole('button', { name: 'Older executions' });
  button.focus();
  await userEvent.keyboard('{Enter}');
  expect(api.pending[2]?.request.path).toContain('battle_id=0000019e-0000-4000-8000-000000000000');
  expect(api.pending[2]?.request.path).toContain(
    `before=${encodeURIComponent('1800000110.0:00000000000000000000000000000065')}`,
  );
  expect(button).toHaveTextContent('Loading older executions…');
  expect(button).toHaveAttribute('aria-disabled', 'true');
  await userEvent.keyboard('{Enter}');
  expect(api.pending).toHaveLength(3);
  await act(async () => {
    api.body(2, r17bBody(CONTINUED));
    await Promise.resolve();
  });
  await flush();
  const items = within(section).getAllByRole('listitem');
  expect(items).toHaveLength(51);
  expect(within(section).queryByRole('button', { name: /older executions/iu })).toBeNull();
  expect(within(section).queryByText(/Only the newest ones/)).toBeNull();
  expect(
    within(section).getByText('All recorded executions for this battle are shown.'),
  ).toBeVisible();
  expect(items[50]).toHaveFocus();
  expect(screen.getByRole('status')).toHaveTextContent('1 older execution loaded');
  expect(await axe(page.view.container)).toHaveNoViolations();
  store.dispose();
}, 30_000);

it('AC4 a failed older read keeps the rows, announces why and the retry button keeps focus', async () => {
  const { api, section, store } = await open(r17bBody(TRUNCATED));
  const button = within(section).getByRole('button', { name: 'Older executions' });
  button.focus();
  await userEvent.keyboard('{Enter}');
  await act(async () => {
    api.fail(2);
    await Promise.resolve();
  });
  await flush();
  expect(within(section).getAllByRole('listitem')).toHaveLength(50);
  expect(button).toHaveFocus();
  expect(button).toHaveTextContent('Retry older executions');
  expect(within(section).getByText(/The server could not be reached/)).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('The server could not be reached.');
  store.dispose();
}, 30_000);

it('AC4 a truncated block with no cursor says older rows exist and offers no read', async () => {
  // Modified recorded case: next_before removed from battle_detail_truncated_at_fifty.
  const detail = r17bBody(TRUNCATED);
  const rest = without(objectOf(detail['executions']), 'next_before');
  const { section, store } = await open({ ...detail, executions: rest });
  expect(within(section).getByText(/server named no way to read them/)).toBeVisible();
  expect(within(section).queryByRole('button', { name: 'Older executions' })).toBeNull();
  store.dispose();
});

it('AC4 the section is a labelled region, its list has a name, and no Why sits inside a paragraph', async () => {
  const { section, page, store } = await open(r17bBody(STARTED));
  expect(within(section).getByRole('heading', { name: 'Actions in this battle' })).toBeVisible();
  expect(within(section).getByRole('list', { name: 'Recorded action executions' })).toBeVisible();
  expect(page.view.container.querySelectorAll('p details, p summary')).toHaveLength(0);
  expect(await axe(page.view.container)).toHaveNoViolations();
  store.dispose();
});

it('AC4 an older page with no rows ends the list, is announced and keeps focus on the completion note', async () => {
  const { api, section, store } = await open(r17bBody(TRUNCATED));
  fireEvent.click(within(section).getByRole('button', { name: 'Older executions' }));
  await act(async () => {
    // Modified recorded case: the recorded continuation with its one row removed (an empty last page).
    api.body(2, { ...r17bBody(CONTINUED), executions: [] });
    await Promise.resolve();
  });
  await flush();
  expect(within(section).getAllByRole('listitem')).toHaveLength(50);
  expect(within(section).queryByRole('button', { name: /older executions/iu })).toBeNull();
  const note = within(section).getByText('All recorded executions for this battle are shown.');
  expect(note).toHaveFocus();
  expect(screen.getByRole('status')).toHaveTextContent(
    'All recorded executions for this battle are shown.',
  );
  store.dispose();
}, 30_000);

it('AC4 an older page that carries a reason is refused, never shown as a complete history', async () => {
  const { api, section, store } = await open(r17bBody(TRUNCATED));
  fireEvent.click(within(section).getByRole('button', { name: 'Older executions' }));
  await act(async () => {
    // Modified recorded case: the continuation answered as a newer ledger answers it (empty, with a reason).
    api.body(2, { ...r17bBody(CONTINUED), executions: [], reason: 'newer_schema' });
    await Promise.resolve();
  });
  await flush();
  expect(within(section).getByText(/did not return a readable history result/)).toBeVisible();
  // The refusal keeps the recorded reason code under Why.
  expect(
    within(section)
      .getByText(/newer_schema/)
      .closest('details'),
  ).toHaveTextContent('Why');
  expect(
    within(section).queryByText('All recorded executions for this battle are shown.'),
  ).toBeNull();
  expect(within(section).getByRole('button', { name: 'Retry older executions' })).toBeVisible();
  store.dispose();
}, 30_000);

it('AC4 a duration of a minute or more reads like every other duration in the catalogue', async () => {
  // Modified recorded case: the 4 s duration of the Ad gems row of a started battle set to 65 s,
  // because no recording holds a duration over a minute.
  const detail = r17bBody(STARTED);
  const items = rowsOf(objectOf(detail['executions'])['items']).map((item) =>
    item['action'] === 'ad_gems' ? { ...item, duration_ms: 65000 } : item,
  );
  const { section, store } = await open({
    ...detail,
    executions: { items, reason: null, truncated: false },
  });
  expect(fact(named(section, 'Ad gems'), 'Duration')).toHaveTextContent('1 m 05 s');
  store.dispose();
});

it.each(['ended_commands_expired_battle_ended', 'interrupted_commands_expired_battle_ended'])(
  'AC4 the recorded battle_detail %s answer says its actions were recorded before action history',
  async (name) => {
    const detail = dashboardCase(`battle_detail.${name}`).body;
    if (typeof detail !== 'object' || detail === null) throw new Error('No recorded body');
    const body = objectOf(detail);
    const { section, store } = await open(
      body,
      new RegExp(String(objectOf(body['battle'])['label'])),
    );
    expect(within(section).getByText(/Recorded before action history existed/)).toBeVisible();
    expect(within(section).queryByText(/No action executions/)).toBeNull();
    expect(within(section).queryByRole('listitem')).toBeNull();
    store.dispose();
  },
);
