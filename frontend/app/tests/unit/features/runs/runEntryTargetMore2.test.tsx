import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { entries, own, ownPath, stringList } from '../../../../src/core/read.ts';
import { atPath } from '../../../../src/core/profilePatch.ts';
import { selectRunEntry } from '../../../../src/store/selectors/runEntry.ts';
import {
  dashboardPagesCase as recorded,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { more2Case, more2Writes } from '../../../support/runEntryTargetMore2.ts';
import { renderPage } from '../dashboard/harness.tsx';

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  window.location.hash = '';
});

async function open(before: DashboardCase, write: DashboardCase, lost = false) {
  let source = before;
  const path = stringList(own(write.request.body, 'path')) ?? [];
  const key = path.slice(2).join('.');
  window.location.hash = `#runs/saved/${path[1]}`;
  const page = await renderPage({
    config: () => recorded('profile_target.inactive_editor_context_before_save').body,
    before: (fake) => {
      fake.route('GET', '/api/profile-files', () =>
        recorded('profile_target.same_account_inactive_target_listed'),
      );
      fake.route('GET', '/api/profile-src/second', () => source);
      // Modified identity only: identical fields let isolation detect accidental draft reuse.
      fake.route('GET', '/api/profile-src/mine', () => ({
        ...before,
        body: { ...Object.fromEntries(entries(before.body)), name: 'mine' },
      }));
      fake.route('GET', '/api/vocab', () => recorded('profile_reads.vocab_ok'));
      fake.route('POST', '/api/profile-patch', () => (lost ? 'hold' : write));
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
  const row = () => {
    const element = page.view.container.querySelector<HTMLElement>(`[data-entry-field="${key}"]`);
    if (element === null) throw new Error(`Missing ${key}`);
    return within(element);
  };
  const read = async (next: DashboardCase) => {
    source = next;
    act(() => {
      page.data.commands.checkRunEntry();
    });
    await page.advance(100);
  };
  const edit = async (raw: string) => {
    const input = row().queryByRole('combobox');
    if (input !== null) await page.user.selectOptions(input, raw);
    else {
      await page.user.clear(row().getByRole('textbox'));
      if (raw !== '') await page.user.type(row().getByRole('textbox'), raw);
    }
  };
  const save = async () => {
    const remove = own(write.request.body, 'op') === 'delete';
    if (remove && (key === 'label' || key === 'rides'))
      await page.user.click(
        row().getByRole('button', {
          name: key === 'label' ? 'Clear label' : 'Use the quest default',
        }),
      );
    else {
      await edit(remove ? '' : String(own(write.request.body, 'value')));
      await page.user.click(row().getByRole('button', { name: 'Save field' }));
    }
    await page.advance(100);
  };
  return { ...page, row, read, edit, save, key, path };
}

it.each(more2Writes)(
  'PE6 F6-14c r12f $name exact body and named advanced readback',
  async ({ before, write, after }) => {
    const page = await open(before, write);
    expect(own(before.body, 'revision')).toBe(own(write.request.body, 'expected_revision'));
    await page.save();
    expect(page.fake.writes().map((item) => JSON.parse(item.body ?? '{}') as unknown)).toEqual([
      write.request.body,
    ]);
    expect(page.row().queryByText('Saved')).toBeNull();
    await page.read(before);
    expect(page.row().queryByText('Saved')).toBeNull();
    // Modified evidence controls: right value with the old revision or wrong target cannot settle.
    await page.read({
      ...after,
      body: { ...Object.fromEntries(entries(after.body)), revision: own(before.body, 'revision') },
    });
    expect(page.row().queryByText('Saved')).toBeNull();
    await page.read({
      ...after,
      body: { ...Object.fromEntries(entries(after.body)), name: 'mine' },
    });
    expect(page.view.container.querySelector('[data-outcome="saved"]')).toBeNull();
    await page.read(after);
    expect(page.row().getByText('Saved')).toBeVisible();
    expect(own(after.body, 'name')).toBe('second');
    expect(own(after.body, 'revision')).not.toBe(own(before.body, 'revision'));
    expect(atPath(own(after.body, 'profile'), page.path)).toEqual(own(write.request.body, 'value'));
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each(more2Writes)(
  'PE6 F6-14c r12f $name lost reply stays Unknown on unchanged read',
  async ({ before, write, after }) => {
    const page = await open(before, write, true);
    await page.save();
    await act(async () => {
      page.fake.next('/api/profile-patch').lose();
      await Promise.resolve();
    });
    await page.advance(100);
    expect(page.row().getByText('Unknown')).toBeVisible();
    await page.read(before);
    expect(page.row().getByText('Unknown')).toBeVisible();
    expect(page.row().queryByText('Saved')).toBeNull();
    expect(page.fake.writes().map((item) => JSON.parse(item.body ?? '{}') as unknown)).toEqual([
      write.request.body,
    ]);
    await page.read(after);
    expect(page.view.container.querySelector('[data-outcome="applied"]')).not.toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each(more2Writes)(
  'PE6 F6-14c r12f $name drafts stay with their target',
  async ({ before, write }) => {
    const page = await open(before, write);
    const raw =
      page.key === 'label' ? 'Second draft' : own(write.request.body, 'op') === 'delete' ? '' : '2';
    await page.edit(raw);
    const field = () =>
      selectRunEntry(page.data.store.getSnapshot()).fields.find((item) => item.key === page.key);
    expect(field()).toMatchObject({ raw, dirty: true });
    const scope = field()?.scope;
    act(() => {
      page.data.commands.returnEditingToActive({ epoch: page.data.store.getSnapshot().epoch });
    });
    await page.advance(100);
    expect(field()?.scope).not.toBe(scope);
    expect(field()?.dirty).toBe(false);
    expect(field()?.raw).not.toBe(raw);
    await page.edit(page.key === 'label' ? 'Active draft' : '');
    act(() => {
      page.data.commands.pickEditingTarget({
        epoch: page.data.store.getSnapshot().epoch,
        name: 'second',
      });
    });
    await page.advance(100);
    expect(field()).toMatchObject({ scope, raw, dirty: true });
    expect(page.fake.writes()).toEqual([]);
  },
);

it('PE6 F6-14c r12f absent rescue reference offers no deletion', async () => {
  const before = more2Case('read_before_the_farm_rescue_policy_is_deleted_again');
  const write = more2Case('inactive_farm_rescue_policy_deleted_again_when_the_run_has_none');
  const page = await open(before, write);
  expect(
    ownPath(before.body, 'profile', 'blueprints', 'farm', 'policies', 'rescue'),
  ).toBeUndefined();
  expect(own(write.body, 'revision')).toBe(own(before.body, 'revision'));
  expect(page.row().getByRole('combobox')).toHaveValue('');
  expect(page.row().getByRole('button', { name: 'Save field' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(
    selectRunEntry(page.data.store.getSnapshot()).fields.find((item) => item.key === page.key),
  ).toMatchObject({ reason: 'unchanged', command: null });
  expect(page.fake.writes()).toEqual([]);
});
