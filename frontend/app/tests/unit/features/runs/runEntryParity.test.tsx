import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { entries, own, stringList } from '../../../../src/core/read.ts';
import { selectRunEntry } from '../../../../src/store/selectors/runEntry.ts';
import {
  dashboardPagesCase as recorded,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { parityCase, paritySourceBefore } from '../../../support/runEntryParity.ts';
import { moreCase, moreSourceBefore } from '../../../support/profileTargetMore.ts';
import { withRecordedFence, fencedRuntime } from '../../../support/profilePatchFenced.ts';
import { renderPage } from '../dashboard/harness.tsx';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';

it('RL9 clearing a label refreshes the active runs catalogue as well as the entry source', async () => {
  const page = await open(parityCase('label_deleted_the_run_id_shows'));
  const before = page.fake.requests.filter(
    (r) => r.method === 'GET' && r.path === '/api/runs',
  ).length;
  await page.user.click(page.row.getByRole('button', { name: 'Clear label' }));
  await page.advance(100);
  expect(
    page.fake.requests.filter((r) => r.method === 'GET' && r.path === '/api/runs').length,
  ).toBeGreaterThan(before);
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  window.location.hash = '';
});
const config = recorded('run_templates_add_fenced.read_config_the_page_context_comes_from').body;
async function open(entry: DashboardCase, lost = false) {
  const path = stringList(own(entry.request.body, 'path')) ?? [];
  let source = paritySourceBefore(entry);
  window.location.hash = `#runs/saved/${path[1]}`;
  const page = await renderPage({
    config: () => config,
    before: (fake) => {
      fake.route('GET', '/api/profile-src/mine', () => source);
      fake.route('GET', '/api/vocab', () => recorded('profile_reads.vocab_ok'));
      fake.route('POST', '/api/profile-patch', () => {
        if (entry.status === 200)
          source = {
            ...source,
            body: {
              ...Object.fromEntries(entries(source.body)),
              profile: own(entry.body, 'profile'),
              revision: own(entry.body, 'revision'),
            },
          };
        return lost ? 'hold' : entry;
      });
    },
  });
  await page.advance(100);
  const element = document.querySelector<HTMLElement>(`[data-entry-field="${path.at(-1)}"]`);
  if (element === null) throw new Error('missing entry field');
  return {
    ...page,
    row: within(element),
    field: path.at(-1),
    setSource: (next: DashboardCase) => {
      source = next;
    },
  };
}
const cases = dashboardPagesCaseNames('run_entry_patch_parity').filter((name) =>
  /^(label_deleted|label_delete_when|label_delete_refused|coin_run_switched|dissonance_run_|dissonant_tab_ultimate|saved_rides|saved_cycles|rides_deleted|rides_(zero|negative|fraction|text)|cycles_(zero|fraction|text))/.test(
    name,
  ),
);
it.each(cases)('RL9 RL12 RL13 exact fenced request and retained refusal: %s', async (name) => {
  const entry = parityCase(name),
    page = await open(entry);
  const value = own(entry.request.body, 'value');
  const original = page.row.queryByRole('textbox')?.getAttribute('value');
  if (own(entry.request.body, 'op') === 'delete' && page.field !== 'dissonant_tab')
    await page.user.click(
      page.row.getByRole('button', {
        name: page.field === 'label' ? 'Clear label' : 'Use the quest default',
      }),
    );
  else {
    const select = page.row.queryByRole('combobox');
    if (select !== null)
      await page.user.selectOptions(select, typeof value === 'string' ? value : '');
    else {
      const input = page.row.getByRole('textbox');
      await page.user.clear(input);
      await page.user.type(
        input,
        typeof value === 'string' ? JSON.stringify(value) : String(value),
      );
    }
    await page.user.click(page.row.getByRole('button', { name: 'Save field' }));
  }
  await page.advance(100);
  // Parity recordings predate expected_context. Add only that fence, compare every other key.
  expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
    withRecordedFence(entry, fencedRuntime).request.body,
  ]);
  if (entry.status === 400) {
    expect(page.row.getByRole('textbox')).toHaveValue(
      own(entry.request.body, 'op') === 'delete'
        ? original
        : typeof value === 'string'
          ? JSON.stringify(value)
          : String(value),
    );
    expect(document.querySelector('[data-refusal] [data-server-text="message"]')?.textContent).toBe(
      own(entry.body, 'error'),
    );
  }
  await page.advance(10_000);
  expect(page.fake.writes()).toHaveLength(1);
});

it('RL10 RL11 statistics is a notice and UW/rescue links name specific policies', async () => {
  const entry = parityCase('coin_run_switched_to_dissonance');
  const page = await open(entry);
  act(() => {
    page.data.commands.openRuns(true, 'farm_deep');
  });
  await page.advance(100);
  const root = document.querySelector<HTMLElement>('[data-run-entry]');
  if (root === null) throw new Error('missing entry');
  expect(root.querySelector('[data-entry-statistics] dt')).toHaveTextContent('Statistics');
  expect(root.querySelector('[data-entry-statistics] dd')).toHaveTextContent('Always on');
  expect(root.querySelector('[data-entry-statistics] input')).toBeNull();
  const fields = selectRunEntry(page.data.store.getSnapshot()).fields;
  expect(fields.find((f) => f.key === 'policies.uw')?.policyEditor).toEqual({
    kind: 'ability',
    family: 'uw_policies',
    policy: 'farm_cl',
  });
  expect(root.querySelector('[data-entry-field="policies.uw"] a')).toHaveAttribute(
    'href',
    '#abilities/uw_policies/farm_cl',
  );
  expect(root.querySelector('[data-entry-field="policies.rescue"] a')).toHaveAttribute(
    'href',
    '#abilities/rescue_policies/wall_drain',
  );
  expect(textOutsideCatalogue(root)).toEqual([]);
});

it.each(['coin_run_switched_to_dissonance', 'saved_rides', 'saved_cycles'])(
  'RL12 RL13 lost %s reply stays Unknown until a positive advanced read, without resend',
  async (name) => {
    const entry = parityCase(name);
    const page = await open(entry, true);
    const value = own(entry.request.body, 'value');
    const select = page.row.queryByRole('combobox');
    if (select !== null) await page.user.selectOptions(select, String(value));
    else {
      await page.user.clear(page.row.getByRole('textbox'));
      await page.user.type(page.row.getByRole('textbox'), String(value));
    }
    await page.user.click(page.row.getByRole('button', { name: 'Save field' }));
    await page.advance(100);
    page.setSource(paritySourceBefore(entry));
    await act(async () => {
      page.fake.next('/api/profile-patch').lose();
      await Promise.resolve();
    });
    await page.advance(100);
    expect(document.querySelector('[data-outcome="unknown"]')).not.toBeNull();
    const record = page.data.machine.snapshot().findLast((r) => r.kind === 'profile_patch');
    expect(page.data.machine.resend(record?.id ?? '')).toBe('not_resendable');
    const initial = paritySourceBefore(entry);
    // No intermediate GET exists at this receipt. Keep the exact receipt's
    // profile/revision in the recorded source envelope for positive read settlement.
    page.setSource({
      ...initial,
      body: {
        ...Object.fromEntries(entries(initial.body)),
        profile: own(entry.body, 'profile'),
        revision: own(entry.body, 'revision'),
      },
    });
    await page.user.click(
      within(document.querySelector('[data-run-entry]') ?? document.body).getByRole('button', {
        name: 'Read entry settings',
      }),
    );
    await page.advance(100);
    expect(document.querySelector('[data-outcome="applied"]')).not.toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it('PE6 F6-14c inactive dissonant_tab follows its recorded save', async () => {
  const save = moreCase('inactive_saved_dissonant_tab');
  let source = moreSourceBefore(save);
  window.location.hash = '#runs/saved/dissonance';
  const page = await renderPage({
    config: () => recorded('profile_target.inactive_editor_context_before_save').body,
    before: (fake) => {
      fake.route('POST', '/api/profile-patch', () => {
        // Intermediate GET: the exact recorded receipt inside its source envelope.
        source = {
          ...source,
          body: {
            ...Object.fromEntries(entries(source.body)),
            ...Object.fromEntries(entries(save.body)),
          },
        };
        return save;
      });
      fake.route('GET', '/api/profile-files', () =>
        recorded('profile_target.same_account_inactive_target_listed'),
      );
      fake.route('GET', '/api/profile-src/second', () => source);
      fake.route('GET', '/api/vocab', () => recorded('profile_reads.vocab_ok'));
    },
  });
  await page.advance(100);
  act(() => {
    page.data.commands.pickEditingTarget({
      epoch: page.data.store.getSnapshot().epoch,
      name: 'second',
    });
  });
  await page.advance(100);
  const element = document.querySelector<HTMLElement>('[data-entry-field="dissonant_tab"]');
  if (element === null) throw new Error('missing inactive field');
  const row = within(element);
  await page.user.selectOptions(row.getByRole('combobox'), 'defense');
  await page.user.click(row.getByRole('button', { name: 'Save field' }));
  await page.advance(100);
  expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
    save.request.body,
  ]);
  expect(row.getByText('Saved')).toBeVisible();
});

it('RL13 F6-11-P3-03 absent rides describes the disabled quest-default action with a visible reason', async () => {
  window.location.hash = '#runs/saved/quest_sm';
  const page = await renderPage({
    config: () => config,
    before: (fake) => {
      fake.route('GET', '/api/profile-src/mine', () =>
        recorded('run_entry_patch_parity.read_after_the_limit_refusals_nothing_changed'),
      );
      fake.route('GET', '/api/vocab', () => recorded('profile_reads.vocab_ok'));
    },
  });
  await page.advance(100);
  const row = document.querySelector<HTMLElement>('[data-entry-field="rides"]');
  if (row === null) throw new Error('missing rides field');
  const button = within(row).getByRole('button', { name: 'Use the quest default' });
  expect(button).toHaveAttribute('aria-disabled', 'true');
  const reason = document.getElementById(button.getAttribute('aria-describedby') ?? '');
  expect(reason).toBeVisible();
  expect(reason).toHaveTextContent('This quest already uses its default.');
  expect(textOutsideCatalogue(row)).toEqual([]);
  await page.user.click(button);
  expect(page.fake.writes()).toEqual([]);
});
