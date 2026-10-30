import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { entries, own, stringList } from '../../../../src/core/read.ts';
import { atPath, profilePatchAnswer } from '../../../../src/core/profilePatch.ts';
import { selectRunEntry } from '../../../../src/store/selectors/runEntry.ts';
import { entryDraftMarker } from '../../../../src/core/runEntry.ts';
import * as entryRules from '../../../../src/core/runEntry.ts';
import { settledByRead } from '../../../../src/store/fences.ts';
import {
  dashboardPagesCase as recorded,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { moreCase, moreNames, moreSourceBefore } from '../../../support/profileTargetMore.ts';
import { parityCase, paritySourceBefore } from '../../../support/runEntryParity.ts';
import { enterText, renderPage, type PageOptions } from '../dashboard/harness.tsx';

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.location.hash = '';
});

it('PE6 F6-14c C-01 stale refusal stays refused after matching advanced read and reload', async () => {
  const entry = moreCase('inactive_run_entry_stale_revision_409');
  const page = await open(entry);
  await save(page, entry);
  expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
    entry.request.body,
  ]);
  const later = moreCase('read_after_the_stale_run_entry_refusals_holds_the_other_clients_save');
  // Modified later source: another client independently saved the requested tier=3 at
  // an advanced revision. Matching values must not reverse an answered refusal.
  const body = structuredClone(later.body) as {
    revision: string;
    profile: { blueprints: { farm: { tier: number } } };
  };
  body.profile.blueprints.farm.tier = 3;
  expect(body.revision).not.toBe(own(entry.request.body, 'expected_revision'));
  const matching = { ...later, body };
  const check = (current: Awaited<ReturnType<typeof open>>) => {
    const state = current.data.store.getSnapshot();
    const record = state.intents.findLast((item) => item.kind === 'profile_patch');
    if (record === undefined) throw new Error('Missing refused patch');
    expect(record.phase).toBe('refused');
    expect(settledByRead(state, record)).toBeNull();
    expect(selectRunEntry(state).fields.find((item) => item.key === 'tier')).toMatchObject({
      raw: '3',
      dirty: true,
      outcome: { code: 'refused' },
    });
    expect(current.row.getByRole('textbox')).toHaveValue('3');
  };
  page.setSource(matching);
  act(() => {
    page.data.commands.checkRunEntry();
  });
  await page.advance(100);
  check(page);
  expect(page.fake.writes()).toHaveLength(1);
  page.view.unmount();
  page.data.stop();
  const restored = await open(entry, false, page.storage);
  restored.setSource(matching);
  act(() => {
    restored.data.commands.checkRunEntry();
  });
  await restored.advance(100);
  check(restored);
  expect(restored.fake.writes()).toEqual([]);
});

it.each(['label', 'rides'] as const)(
  'PE6 F6-14c C-02 explicit %s clear follows the delete owner and preserves a visible reason when blocked',
  async (key) => {
    const entry = moreCase('inactive_saved_tier');
    const quest = parityCase('saved_rides');
    // Modified recorded active quest envelope: name it second to inspect inactive gates only.
    const page =
      key === 'rides'
        ? await open(quest, false, undefined, {
            ...paritySourceBefore(quest),
            body: {
              ...Object.fromEntries(entries(paritySourceBefore(quest).body)),
              name: 'second',
            },
          })
        : await open(entry);
    const row = page.view.container.querySelector<HTMLElement>(`[data-entry-field="${key}"]`);
    if (row === null) throw new Error(`Missing ${key}`);
    const button = within(row).getByRole('button', {
      name: key === 'label' ? 'Clear label' : /quest default/i,
    });
    expect(button).not.toHaveAttribute('aria-disabled', 'true');
    const owner = entryRules.inactiveEntryRecorded;
    // Simulate an unrecorded operation to keep both explicit clears tied to the owner.
    const spy = vi
      .spyOn(entryRules, 'inactiveEntryRecorded')
      .mockImplementation((field, remove) =>
        field === key && remove ? false : owner(field, remove),
      );
    const field = selectRunEntry(page.data.store.getSnapshot()).fields.find(
      (item) => item.key === key,
    );
    expect(spy).toHaveBeenCalledWith(key, true);
    expect(field?.clearReason).toBe('activeOnly');
    expect(field?.clearCommand).toBeNull();
    await page.advance(1000);
    expect(button).toHaveAttribute('aria-disabled', 'true');
    const reason = document.getElementById(button.getAttribute('aria-describedby') ?? '');
    expect(reason).toBeVisible();
    expect(reason).toHaveTextContent('Return editing to the active profile');
    expect(page.fake.writes()).toEqual([]);
  },
);

async function open(
  entry: DashboardCase,
  lost = false,
  storage?: PageOptions['storage'],
  initial?: DashboardCase,
) {
  const path = stringList(own(entry.request.body, 'path')) ?? [];
  let source = initial ?? moreSourceBefore(entry);
  window.location.hash = `#runs/saved/${path[1] ?? 'farm'}`;
  const page = await renderPage({
    ...(storage === undefined ? {} : { storage }),
    config: () => recorded('profile_target.inactive_editor_context_before_save').body,
    before: (fake) => {
      fake.route('GET', '/api/profile-files', () =>
        recorded('profile_target.same_account_inactive_target_listed'),
      );
      fake.route('GET', '/api/profile-src/second', () => source);
      fake.route('GET', '/api/vocab', () => recorded('profile_reads.vocab_ok'));
      fake.route('POST', '/api/profile-patch', () => {
        if (entry.status === 200 && !lost)
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
  act(() => {
    page.data.commands.pickEditingTarget({
      epoch: page.data.store.getSnapshot().epoch,
      name: 'second',
    });
  });
  await page.advance(100);
  const key = path.slice(2).join('.');
  const element = page.view.container.querySelector<HTMLElement>(`[data-entry-field="${key}"]`);
  if (element === null) throw new Error(`Missing field ${key}`);
  return {
    ...page,
    row: within(element),
    key,
    setSource: (next: DashboardCase) => {
      source = next;
    },
  };
}

async function save(page: Awaited<ReturnType<typeof open>>, entry: DashboardCase) {
  const value = own(entry.request.body, 'value');
  if (own(entry.request.body, 'op') === 'delete' && page.key === 'perk_bans')
    await page.user.click(page.row.getByRole('checkbox'));
  else if (page.row.queryByRole('combobox') !== null)
    await page.user.selectOptions(
      page.row.getByRole('combobox'),
      own(entry.request.body, 'op') === 'delete' ? '' : String(value),
    );
  else if (page.row.queryByRole('checkbox') !== null && typeof value === 'boolean') {
    const input = page.row.getByRole('checkbox');
    if ((input as HTMLInputElement).checked !== value) await page.user.click(input);
  } else {
    const input = page.row.getByRole('textbox');
    await page.user.clear(input);
    const raw = Array.isArray(value)
      ? value.join('\n')
      : value === null
        ? ''
        : typeof value === 'number' || typeof value === 'string'
          ? String(value)
          : '';
    // A list arrives in one input event (see enterText); a single value is typed.
    if (raw !== '' && Array.isArray(value)) await enterText(page.user, input, raw);
    else if (raw !== '') await page.user.type(input, raw);
  }
  await page.user.click(page.row.getByRole('button', { name: 'Save field' }));
  await page.advance(100);
}

it.each(moreNames.filter((name) => name.startsWith('inactive_saved_')))(
  'PE6 F6-14c exact inactive field request and positive readback: %s',
  async (name) => {
    const entry = moreCase(name),
      page = await open(entry);
    await save(page, entry);
    expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
      entry.request.body,
    ]);
    expect(page.row.getByText('Saved')).toBeVisible();
    const field = selectRunEntry(page.data.store.getSnapshot()).fields.find(
      (item) => item.key === page.key,
    );
    expect(field?.outcome?.code).not.toBe('refused');
    const body = page.data.store.getSnapshot().reads.targetSource.latest?.outcome;
    expect(
      body?.kind === 'answered'
        ? atPath(own(body.body, 'profile'), stringList(own(entry.request.body, 'path')) ?? [])
        : undefined,
    ).toEqual(
      own(entry.request.body, 'op') === 'delete' ? undefined : own(entry.request.body, 'value'),
    );
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each([
  'inactive_run_entry_stale_revision_409',
  'inactive_run_entry_refused_after_account_switch',
  'inactive_perk_bans_thirty_one_entries_is_refused',
])(
  'PE6 F6-14c %s shows exact refusal, keeps raw draft and never becomes saved on read',
  async (name) => {
    const entry = moreCase(name),
      page = await open(entry);
    await save(page, entry);
    expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
      entry.request.body,
    ]);
    expect(
      page.row.getAllByText(String(own(entry.body, 'error')), { normalizer: (text) => text })[0],
    ).toBeVisible();
    expect(page.row.queryByText('Saved')).toBeNull();
    const raw =
      page.row.getByRole('textbox').getAttribute('value') ??
      page.row.getByRole('textbox').textContent;
    page.setSource(
      moreCase(
        name.includes('stale')
          ? 'read_after_the_stale_run_entry_refusals_holds_the_other_clients_save'
          : 'read_after_the_inactive_run_entry_refusals_nothing_changed',
      ),
    );
    act(() => {
      page.data.commands.checkRunEntry();
    });
    await page.advance(100);
    expect(page.row.queryByText('Saved')).toBeNull();
    expect(page.row.getByRole('textbox')).toHaveValue(raw);
    page.view.unmount();
    page.data.stop();
    const restored = await open(entry, false, page.storage);
    expect(
      restored.row.getAllByText(String(own(entry.body, 'error')), {
        normalizer: (text) => text,
      })[0],
    ).toBeVisible();
    expect(restored.row.getByRole('textbox')).toHaveValue(raw);
    expect(restored.fake.writes()).toEqual([]);
  },
);

it('PE6 F6-14c lost reply stays Unknown until the named target has an advanced revision and value', async () => {
  const entry = moreCase('inactive_saved_tier'),
    page = await open(entry, true);
  await save(page, entry);
  await act(async () => {
    page.fake.next('/api/profile-patch').lose();
    await Promise.resolve();
  });
  await page.advance(100);
  expect(page.row.getByText('Unknown')).toBeVisible();
  act(() => {
    page.data.commands.checkRunEntry();
  });
  await page.advance(100);
  expect(page.row.getByText('Unknown')).toBeVisible();
  // Intermediate read: exact successful receipt in the recorded source envelope.
  page.setSource({
    ...moreSourceBefore(entry),
    body: {
      ...Object.fromEntries(entries(moreSourceBefore(entry).body)),
      ...Object.fromEntries(entries(entry.body)),
    },
  });
  act(() => {
    page.data.commands.checkRunEntry();
  });
  await page.advance(100);
  expect(page.view.container.querySelector('[data-outcome="applied"]')).not.toBeNull();
  expect(page.fake.writes()).toHaveLength(1);
});

it.each(
  moreNames.filter(
    (name) =>
      moreCase(name).status === 400 &&
      (stringList(own(moreCase(name).request.body, 'path')) ?? [])[0] === 'blueprints',
  ),
)('PE6 F6-14c recorded validator boundary refuses without a saved classification: %s', (name) => {
  const entry = moreCase(name);
  expect(profilePatchAnswer(entry)).toMatchObject({
    kind: 'refused',
    error: own(entry.body, 'error'),
  });
});

it.each([
  'inactive_dissonant_tab_unknown_is_refused',
  'inactive_gathering_policy_unknown_is_refused',
  'inactive_gem_cap_negative_is_refused',
  'inactive_loadout_as_is_on_a_tournament_run_is_refused',
  'inactive_loadout_deleted_a_run_must_know_what_to_equip_is_refused',
  'inactive_loadout_unknown_equipment_is_refused',
  'inactive_tier_deleted_a_coin_run_needs_one_is_refused',
  'inactive_tier_text_is_refused',
  'inactive_tier_zero_is_refused',
  'inactive_run_entry_stale_revision_is_judged_before_the_values',
])(
  'PE6 F6-14c captured request %s renders verbatim refusal and retains its draft',
  async (name) => {
    const answer = moreCase(name),
      page = await open(answer),
      state = page.data.store.getSnapshot();
    const vm = selectRunEntry(state),
      field = vm.fields.find((item) => item.key === page.key),
      context = state.context;
    if (field === undefined || context === null) throw new Error('Recorded field/context missing');
    const path = stringList(own(answer.request.body, 'path')) ?? [],
      value = own(answer.request.body, 'value'),
      remove = own(answer.request.body, 'op') === 'delete';
    const raw = typeof value === 'string' || typeof value === 'number' ? String(value) : '';
    const revision = String(own(answer.request.body, 'expected_revision'));
    act(() => {
      page.data.commands.editDraft(
        field.scope,
        'base_revision',
        entryDraftMarker(revision, raw, remove),
      );
      page.data.commands.editDraft(field.scope, 'value', raw);
    });
    // Exercise the frozen intent boundary directly: local field guards need not offer these
    // invalid values. The server's unchanged recorded request/answer remains authoritative.
    await act(async () => {
      await page.data.commands.patchRunEntry({
        epoch: state.epoch,
        context,
        profile: 'second',
        generation: vm.target.generation,
        inactive: true,
        target: field.scope,
        path,
        revision,
        remove,
        value,
        draft: {
          scope: field.scope,
          values: page.data.store.getSnapshot().runDrafts[field.scope] ?? {},
        },
      });
    });
    await page.advance(100);
    expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
      answer.request.body,
    ]);
    expect(
      page.row.getAllByText(String(own(answer.body, 'error')), { normalizer: (text) => text })[0],
    ).toBeVisible();
    expect(
      selectRunEntry(page.data.store.getSnapshot()).fields.find((item) => item.key === page.key)
        ?.raw,
    ).toBe(raw);
    expect(page.row.queryByText('Saved')).toBeNull();
  },
);
