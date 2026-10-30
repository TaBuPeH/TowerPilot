import { assertProfilePatchContexts } from '../../../support/profilePatchAssertions.ts';
import { withRecordedFence } from '../../../support/profilePatchFenced.ts';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, describe, expect, it } from 'vitest';
import { createScheduleApi } from '../../../../src/api/schedule.ts';
import { entries, list, own, ownPath } from '../../../../src/core/read.ts';
import { SchedulePage } from '../../../../src/features/schedule/SchedulePage.tsx';
import { createScheduleStore, currentSchedule } from '../../../../src/store/schedule.ts';
import { scheduleDraft, schedulePlan } from '../../../../src/store/scheduleDraft.ts';
import { selectSchedule } from '../../../../src/store/selectors/schedule.ts';
import { initialDashboardState } from '../../../../src/store/dashboardStore.ts';
import { familySchemas } from '../../../../src/contract/generated/dashboard_contract_pages_v1_schedule.0709b095.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../../scripts/contract-fixture.mjs';
import {
  createFakeTransport as fakeTransport,
  createMemoryStorage as memoryStorage,
} from '../../../support/fakeTransport.ts';
import { dashboardCase, type DashboardCase } from '../../../support/dashboardFixture.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { renderRegion } from '../dashboard/harness.tsx';

const CASES = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');
function fixture(reference: string): DashboardCase {
  const [family = '', key = ''] = reference.split('.');
  const raw = ownPath(CASES, family, key);
  if (raw === undefined) return dashboardCase(reference);
  return withRecordedFence(structuredClone(raw) as DashboardCase, context);
}
const context = { instance: 'main', account: null, serial: 'fake:1', profile: 'mine' };
const cleanups: (() => void)[] = [];
afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
});

async function change(action: () => unknown) {
  await act(async () => {
    await action();
  });
}
async function setup(
  options: {
    profile?: string;
    status?: string;
    save?: string;
    windowMs?: number;
    storage?: ReturnType<typeof memoryStorage>;
    mount?: boolean;
  } = {},
) {
  const fake = fakeTransport();
  let profile = options.profile ?? 'profile_patch.read_before_the_first_save';
  fake.route('GET', '/api/config', () => fixture('config_state.read_before_the_save'));
  fake.route('GET', '/api/profile-src/mine', () => fixture(profile));
  fake.route('GET', '/api/schedule/status?instance=main', () =>
    fixture(options.status ?? 'schedule_plan.observed_under_the_active_profile'),
  );
  fake.route('POST', '/api/profile-patch', () =>
    options.save === 'hold'
      ? 'hold'
      : fixture(options.save ?? 'profile_patch.multi_save_first_request_saved'),
  );
  const api = createScheduleApi(
    fake.transport,
    options.storage ?? memoryStorage(),
    options.windowMs,
  );
  const schedule = createScheduleStore(api);
  cleanups.push(schedule.dispose);
  cleanups.push(() => {
    assertProfilePatchContexts(api.machine);
  });
  await change(() => {
    schedule.setTarget(context, 1);
  });
  if (options.mount !== false)
    renderRegion(initialDashboardState(), <SchedulePage schedule={schedule} />);
  const entry = () => currentSchedule(schedule.getSnapshot());
  const block = () => entry()?.draft?.days[entry()?.day ?? '']?.[0];
  return {
    fake,
    api,
    schedule,
    entry,
    block,
    readback: (ref: string) => {
      profile = ref;
    },
  };
}
function firstCount() {
  return screen.getAllByRole('textbox', {
    name: 'Runs/day (blank = unlimited)',
  })[0] as HTMLInputElement;
}

describe('Schedule Wanted rows', () => {
  it('S1 existing day plans preserve another day’s dirty raw input', async () => {
    await setup();
    fireEvent.change(firstCount(), { target: { value: '3.50' } });
    fireEvent.click(screen.getByRole('button', { name: 'tourney_day' }));
    expect(firstCount()).toHaveValue('1');
    fireEvent.click(screen.getByRole('button', { name: 'farm_day' }));
    expect(firstCount()).toHaveValue('3.50');
  });
  it.each([
    ['schedule_plan.profile_without_a_plan_has_no_plan_member', 'This saved profile has no plan.'],
    ['schedule_plan.profile_with_an_empty_plan_is_a_validator_problem', 'This plan has no blocks.'],
  ])('S1 missing and invalid empty plan stay distinct: %s', async (profile, expected) => {
    const s = await setup({ profile, mount: false });
    // The empty-plan route explicitly names empty_plan, so target the recorded profile itself.
    if (profile.includes('empty_plan'))
      s.schedule.update((state) => ({
        ...state,
        entries: Object.fromEntries(
          Object.entries(state.entries).map(([key, entry]) => [
            key,
            {
              ...entry,
              source: fixture(profile).body,
              draft: scheduleDraft(own(fixture(profile).body, 'profile')),
            },
          ]),
        ),
      }));
    renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
    expect(screen.getByText(new RegExp(expected))).toBeVisible();
  });
  it('S1 no profile differs from a missing saved plan and sends no mutation', async () => {
    const s = await setup({ mount: false });
    await change(() => {
      s.schedule.setTarget({ ...context, profile: null }, 2);
    });
    renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
    expect(screen.getByText(/No active profile/)).toBeVisible();
    expect(s.fake.writes()).toHaveLength(0);
  });
  it.each([
    ['-1', 'count_negative'],
    ['0', 'count_zero'],
    ['1.5', 'count_fraction'],
    ['1.9999999999999999999', 'count_text'],
    ['NaN', 'count_text'],
    ['Infinity', 'count_text'],
    ['oops', 'count_text'],
  ])('S2 invalid count %s is retained through canonical refusal', async (raw, refusal) => {
    const s = await setup({ save: `profile_patch.${refusal}` });
    fireEvent.change(firstCount(), { target: { value: raw } });
    await change(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }));
    });
    expect(firstCount()).toHaveValue(raw);
    expect(screen.getByText('Schedule save refused. Your draft is kept.')).toBeVisible();
    expect(s.fake.writes()).toHaveLength(1);
    expect(
      list(ownPath(JSON.parse(s.fake.writes()[0]?.body ?? '{}'), 'value', 'days', 'farm_day'))?.[0],
    ).toHaveProperty('count');
    if (raw.includes('.'))
      expect(
        own(
          list(
            ownPath(JSON.parse(s.fake.writes()[0]?.body ?? '{}'), 'value', 'days', 'farm_day'),
          )?.[0],
          'count',
        ),
      ).toBe(raw);
  });
  it('S2 blank count intentionally omits only count; keyboard move/delete/add preserve fields and focus', async () => {
    const s = await setup();
    const user = userEvent.setup();
    fireEvent.change(firstCount(), { target: { value: '' } });
    const blocks = screen.getAllByRole('group', { name: /^Block / });
    await user.click(within(blocks[1] as HTMLElement).getByRole('button', { name: 'Move up' }));
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(screen.getAllByRole('combobox', { name: 'Saved run' })[0]).toHaveFocus();
    expect(screen.getAllByRole('combobox', { name: 'Saved run' })[0]).toHaveValue('farm');
    within(screen.getAllByRole('group', { name: /^Block / })[0] as HTMLElement)
      .getByRole('button', { name: 'Delete block' })
      .focus();
    await user.keyboard('{Enter}');
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(screen.getAllByRole('combobox', { name: 'Saved run' })[0]).toHaveFocus();
    const draft = s.entry()?.draft;
    if (draft === undefined || draft === null) throw new Error('No draft');
    const plan = schedulePlan(draft);
    expect(ownPath(plan, 'days', 'farm_day')).toEqual([
      { after: '08:00', block: 'shards', blueprint: 'shards' },
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Delete block' }));
    expect(screen.getByText(/This day has no blocks/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Add block' }));
    expect(screen.getAllByRole('group', { name: /^Block / })).toHaveLength(1);
  });
  it.each([
    ['tomorrow', 'time_text'],
    ['25:00', 'time_hour_out_of_range'],
    ['20:00', 'window_closes_before_it_opens'],
  ])('S2 malformed/backward window %s stays raw after refusal', async (raw, refusal) => {
    await setup({ save: `profile_patch.${refusal}` });
    fireEvent.change(screen.getAllByRole('textbox', { name: 'From (HH:MM)' })[0] as HTMLElement, {
      target: { value: raw },
    });
    await change(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }));
    });
    expect(screen.getAllByRole('textbox', { name: 'From (HH:MM)' })[0]).toHaveValue(raw);
    expect(screen.getByText('Schedule save refused. Your draft is kept.')).toBeVisible();
  });
  it('S3 unknown week mapping keeps the draft; all day and week edits use a single revisioned request', async () => {
    const s = await setup({ save: 'profile_patch.week_names_an_unknown_day_plan' });
    fireEvent.click(screen.getByText('Edit weekday mapping', { exact: true }));
    fireEvent.change(screen.getByLabelText('Sunday'), { target: { value: 'no_such_day' } });
    fireEvent.change(firstCount(), { target: { value: '60' } });
    await change(() => s.schedule.save());
    expect(screen.getByLabelText('Sunday')).toHaveValue('no_such_day');
    expect(screen.getByLabelText('Sunday')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Sunday')).toHaveAccessibleDescription(
      /plan.week.sunday: unknown day plan/,
    );
    expect(screen.getByLabelText('Monday')).not.toHaveAttribute('aria-invalid');
    const request: unknown = JSON.parse(s.fake.writes()[0]?.body ?? '{}');
    expect(own(request, 'path')).toEqual(['plan']);
    expect(ownPath((ownPath(request, 'value', 'days', 'farm_day') as unknown[])[0], 'count')).toBe(
      60,
    );
    expect(ownPath(request, 'value', 'week', 'sunday')).toBe('no_such_day');
    expect(own(request, 'expected_revision')).toBe(
      own(fixture('profile_patch.read_before_the_first_save').body, 'revision'),
    );
  });
  it('S4 focused final character is captured and newer typing survives a delayed answer', async () => {
    const s = await setup({ save: 'hold' });
    const input = firstCount();
    input.focus();
    fireEvent.change(input, { target: { value: '60' } });
    await change(async () => {
      // F6-11-P2-01: the second click arrives while the first one's context check runs.
      const first = s.schedule.save();
      expect(await s.schedule.save()).toBe('blocked');
      expect(await first).toBe('sent');
    });
    expect(input).toHaveFocus();
    expect(s.fake.writes()).toHaveLength(1);
    expect(
      own(
        list(
          ownPath(JSON.parse(s.fake.writes()[0]?.body ?? '{}'), 'value', 'days', 'farm_day'),
        )?.[0],
        'count',
      ),
    ).toBe(60);
    fireEvent.change(input, { target: { value: '600' } });
    const held = s.fake.held.find((h) => h.request.method === 'POST');
    await change(() => {
      held?.respond(200, fixture('profile_patch.multi_save_first_request_saved').body);
    });
    expect(input).toHaveValue('600');
    expect(s.entry()?.dirty).toBe(true);
  });
  it.each([false, true])(
    'S3 S4 F6-SCHEDULE-01 a read started during saving cannot replace the accepted source (newer typing: %s)',
    async (newerTyping) => {
      const s = await setup({ save: 'hold' });
      s.schedule.mapWeek('sunday', 'tourney_day');
      // F6-11-P2-01: Save sends after its click-time config read.
      await change(() => s.schedule.save());
      s.fake.route('GET', '/api/profile-src/mine', () => 'hold');
      const reported = fixture('schedule_status.available_no_winner');
      s.fake.route('GET', '/api/schedule/status?instance=main', () => reported);
      const reading = s.schedule.refresh();
      await waitFor(() => {
        expect(s.fake.held.some((h) => h.request.method === 'GET')).toBe(true);
      });
      if (newerTyping) fireEvent.change(firstCount(), { target: { value: '100.00' } });
      const saved = fixture('profile_patch.multi_save_first_request_saved');
      await change(() => {
        s.fake.next('/api/profile-patch', 'POST').respond(saved.status, saved.body);
      });
      const revision = own(saved.body, 'revision');
      expect(own(s.entry()?.source, 'revision')).toBe(revision);
      expect(s.entry()?.dirty).toBe(newerTyping);
      const old = fixture('profile_patch.read_before_the_first_save');
      await change(async () => {
        s.fake.next('/api/profile-src/mine', 'GET').respond(old.status, old.body);
        await reading;
      });
      expect(s.entry()?.draft?.week['sunday']).toBe('tourney_day');
      expect(own(s.entry()?.source, 'revision')).toBe(revision);
      expect(s.entry()?.baseRevision).toBe(revision);
      expect(s.entry()?.dirty).toBe(newerTyping);
      expect(s.entry()?.busy).toBe(false);
      expect(s.entry()?.checked).toBe(false);
      expect(s.entry()?.observed).toEqual(reported.body);
      expect(firstCount()).toHaveValue(newerTyping ? '100.00' : '100');
      s.fake.route('GET', '/api/profile-src/mine', () =>
        fixture('profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save'),
      );
      await change(() => s.schedule.refresh());
      expect(s.entry()?.checked).toBe(true);
      expect(s.entry()?.draft?.week['sunday']).toBe('tourney_day');
      expect(s.fake.writes()).toHaveLength(1);
    },
  );
  it.each([
    'profile_src_validator_unavailable_is_reported_not_hidden',
    'profile_src_with_problems',
    'profile_src_with_validator_warnings',
  ])(
    'S4 F6-SCHEDULE-02 successful validation replaces obsolete source metadata: %s',
    async (profile) => {
      const s = await setup({ profile: `profile_reads.${profile}` });
      const previous = selectSchedule(s.schedule.getSnapshot());
      expect(previous.problems.length + previous.warnings.length).toBeGreaterThan(0);
      await change(async () => {
        s.schedule.mapWeek('sunday', 'tourney_day');
        await s.schedule.save();
      });
      expect(s.entry()?.source).toEqual(
        fixture('profile_patch.multi_save_first_request_saved').body,
      );
      const saved = selectSchedule(s.schedule.getSnapshot());
      expect(saved.problems).toEqual([]);
      expect(saved.warnings).toEqual([]);
      // This fixture's extra day plan is absent from the save answer: retain that partial draft.
      expect(s.entry()?.dirty).toBe(profile === 'profile_src_with_validator_warnings');
      expect(s.fake.writes()).toHaveLength(1);
    },
  );
  it('S4 F6-SCHEDULE-02 correcting a saved invalid count clears its earlier problem without a read', async () => {
    const s = await setup({ save: 'profile_patch.multi_save_second_request_saved', mount: false });
    const entry = s.entry();
    if (entry?.draft == null) throw new Error('No draft');
    const badDraft = {
      ...entry.draft,
      days: {
        ...entry.draft.days,
        farm_day: (entry.draft.days['farm_day'] ?? []).map((block, i) =>
          i === 0 ? { ...block, count: '0' } : block,
        ),
      },
    };
    // Supplemental source-state stress: the exact recorded count-zero problem,
    // with the invalid draft at the store boundary; no backend response is invented.
    await change(() => {
      s.schedule.update((state) => ({
        ...state,
        entries: {
          ...state.entries,
          [state.key ?? '']: {
            ...entry,
            draft: badDraft,
            source: {
              ...Object.fromEntries(entries(entry.source)),
              problems: own(fixture('profile_patch.count_zero').body, 'problems'),
              profile: {
                ...Object.fromEntries(entries(own(entry.source, 'profile'))),
                plan: schedulePlan(badDraft),
              },
            },
          },
        },
      }));
    });
    renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
    expect(screen.getByText(/must be an integer >= 1, got 0/)).toBeVisible();
    fireEvent.change(firstCount(), { target: { value: '60' } });
    await change(async () => {
      s.schedule.mapWeek('sunday', 'tourney_day');
      await s.schedule.save();
    });
    expect(firstCount()).toHaveValue('60');
    expect(s.entry()?.dirty).toBe(false);
    expect(screen.queryByText(/must be an integer >= 1, got 0/)).toBeNull();
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('S4 F6C-R1-02 a lost save is never resent; an unchanged original-target read keeps it Unknown until the person reconciles', async () => {
    const s = await setup({ save: 'hold' });
    fireEvent.change(firstCount(), { target: { value: '60' } });
    await change(() => s.schedule.save());
    await change(() => {
      s.fake.held.find((h) => h.request.method === 'POST')?.lose();
    });
    expect(screen.getByText(/Schedule save outcome unknown/)).toBeVisible();
    await act(async () => {
      await s.schedule.refresh();
    });
    // An unchanged revision cannot prove the save failed: it may still be written.
    expect(screen.queryByText(/starting revision unchanged/)).toBeNull();
    expect(screen.getByText(/Schedule save outcome unknown/)).toBeVisible();
    expect(s.entry()?.intent?.readSettlement).toBeNull();
    expect(s.entry()?.intent?.phase).toBe('unconfirmed');
    expect(firstCount()).toHaveValue('60');
    expect(screen.getByRole('button', { name: 'Save schedule' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await change(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Use read revision; keep my draft' }));
    });
    expect(screen.getByText(/Schedule save outcome unknown/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Save schedule' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(s.fake.writes()).toHaveLength(1);
    expect(s.api.machine.resend(s.entry()?.intent?.id ?? '')).toBe('not_resendable');
  });
  it.each([false, true])(
    'S4 applied readback preserves newer raw typing through reload (reload before read: %s)',
    async (reloadFirst) => {
      const storage = memoryStorage();
      const s = await setup({ save: 'hold', storage, mount: false });
      s.schedule.mapWeek('sunday', 'tourney_day');
      await change(() => s.schedule.save());
      s.schedule.edit(s.block()?.key ?? '', 'count', '100.00');
      await change(() => {
        s.fake.next('/api/profile-patch', 'POST').lose();
      });
      s.readback('profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save');
      if (reloadFirst) s.schedule.dispose();
      const reading = reloadFirst
        ? await setup({
            storage,
            mount: false,
            profile: 'profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save',
          })
        : s;
      await change(() => reading.schedule.refresh());
      expect(reading.entry()?.intent?.readSettlement).toBe('applied');
      expect(reading.block()?.count).toBe('100.00');
      expect(reading.entry()?.dirty).toBe(true);
      reading.schedule.dispose();
      const restored = await setup({
        storage,
        mount: false,
        profile: 'profile_patch.read_after_the_stale_refusal_holds_the_other_clients_save',
      });
      expect(restored.block()?.count).toBe('100.00');
      expect(restored.entry()?.dirty).toBe(true);
      expect(restored.fake.writes()).toHaveLength(0);
      expect(s.fake.writes()).toHaveLength(1);
    },
  );
  it('S4 conflict requires read and explicit reconciliation; partial-save readback retains all unsaved work', async () => {
    const s = await setup({ save: 'profile_patch.stale_revision_409' });
    fireEvent.change(firstCount(), { target: { value: '66' } });
    await change(() => s.schedule.save());
    expect(screen.getByRole('button', { name: 'Save schedule' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    s.readback('profile_patch.read_after_a_partial_batch_holds_the_first_two');
    await act(async () => {
      await s.schedule.refresh();
    });
    expect(firstCount()).toHaveValue('66');
    expect(ownPath(s.entry()?.source, 'profile', 'plan', 'week', 'sunday')).toBe('tourney_day');
    await change(() => {
      s.schedule.reconcile();
    });
    expect(screen.getByRole('button', { name: 'Save schedule' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('S5 navigation reload keeps raw draft and focus under its exact target, never under another profile', async () => {
    const storage = memoryStorage();
    const s = await setup({ storage, mount: false });
    const key = s.block()?.key ?? '';
    s.schedule.edit(key, 'count', '1.00');
    s.schedule.rememberFocus('saved-focus');
    s.schedule.dispose();
    const restored = await setup({ storage, mount: false });
    expect(restored.block()?.count).toBe('1.00');
    expect(restored.entry()?.focus).toBe('saved-focus');
    restored.schedule.setTarget({ ...context, profile: 'second' }, 2);
    expect(currentSchedule(restored.schedule.getSnapshot())?.dirty).toBe(false);
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('S4 a timed-out save is read, never replayed; a different read revision needs explicit reconciliation', async () => {
    const s = await setup({ save: 'hold', windowMs: 10 });
    fireEvent.change(firstCount(), { target: { value: '70' } });
    await change(() => s.schedule.save());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(s.entry()?.intent?.phase).toBe('unconfirmed');
    s.readback('profile_patch.read_after_a_partial_batch_holds_the_first_two');
    await act(async () => {
      await s.schedule.refresh();
    });
    expect(screen.getByRole('button', { name: 'Save schedule' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    const reconcile = screen.getByRole('button', { name: 'Use read revision; keep my draft' });
    expect(reconcile).toBeEnabled();
    await change(() => {
      fireEvent.click(reconcile);
    });
    expect(firstCount()).toHaveValue('70');
    expect(screen.getByRole('button', { name: 'Save schedule' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(s.fake.writes()).toHaveLength(1);
    expect(s.api.machine.resend(s.entry()?.intent?.id ?? '')).toBe('not_resendable');
  });
  it('S4 a delayed answer and failed reads never move a draft into another target', async () => {
    const s = await setup({ save: 'hold', mount: false });
    const key = s.block()?.key ?? '';
    s.schedule.edit(key, 'count', '70');
    await change(() => s.schedule.save());
    const pending = s.fake.held.find((item) => item.request.method === 'POST');
    s.schedule.setTarget({ ...context, profile: 'second' }, 2);
    await change(() => {
      pending?.respond(200, fixture('profile_patch.multi_save_first_request_saved').body);
    });
    expect(s.entry()?.draft).toBeNull();
    expect(s.entry()?.dirty).toBe(false);
    await change(() => {
      s.schedule.setTarget(context, 3);
    });
    expect(s.block()?.count).toBe('70');
    expect(s.entry()?.dirty).toBe(true);
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('S4 Discard confirms, can be cancelled, and restores saved values without a request', async () => {
    const s = await setup();
    const user = userEvent.setup();
    fireEvent.change(firstCount(), { target: { value: 'raw-draft' } });
    await user.click(screen.getByRole('button', { name: 'Discard schedule draft' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
    expect(firstCount()).toHaveValue('raw-draft');
    await user.click(screen.getByRole('button', { name: 'Discard schedule draft' }));
    await user.click(within(dialog).getByRole('button', { name: 'Discard schedule draft' }));
    expect(firstCount()).toHaveValue('100');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('S4 discard confirmation belongs to the target and epoch that opened it', async () => {
    const s = await setup();
    fireEvent.change(firstCount(), { target: { value: 'target-a-draft' } });
    await change(() => {
      s.schedule.setTarget({ ...context, serial: 'other:1' }, 2);
    });
    const secondKey = s.schedule.getSnapshot().key ?? '';
    const firstEntry = Object.values(s.schedule.getSnapshot().entries).find(
      (e) => e.draft !== null,
    );
    if (firstEntry === undefined) throw new Error('No first target draft');
    await change(() => {
      // A cached draft for a second target; a failed context read must not discard it.
      s.schedule.update((state) => ({
        ...state,
        entries: { ...state.entries, [secondKey]: firstEntry },
      }));
      s.schedule.edit(s.block()?.key ?? '', 'count', 'target-b-draft');
      s.schedule.setTarget(context, 3);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Discard schedule draft' }));
    const confirm = within(screen.getByRole('dialog')).getByRole('button', {
      name: 'Discard schedule draft',
    });
    await change(() => {
      s.schedule.setTarget({ ...context, serial: 'other:1' }, 4);
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(confirm);
    expect(s.block()?.count).toBe('target-b-draft');
    await change(() => {
      s.schedule.setTarget(context, 5);
    });
    expect(firstCount()).toHaveValue('target-a-draft');
    fireEvent.click(screen.getByRole('button', { name: 'Discard schedule draft' }));
    await change(() => {
      s.schedule.setTarget(context, 6);
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(firstCount()).toHaveValue('target-a-draft');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('S6 a failed observation read keeps the previous winner visibly stale', async () => {
    const s = await setup();
    s.fake.route('GET', '/api/schedule/status?instance=main', () => 'hold');
    const reading = s.schedule.refresh();
    await waitFor(() => {
      expect(
        s.fake.held.find((item) => item.request.path.startsWith('/api/schedule/status')),
      ).toBeDefined();
    });
    const pending = s.fake.held.find((item) =>
      item.request.path.startsWith('/api/schedule/status'),
    );
    pending?.lose();
    await act(async () => {
      await reading;
    });
    const vm = selectSchedule(s.schedule.getSnapshot());
    expect(vm.observationState).toBe('stale');
    expect(vm.winner).toBeNull();
    expect(vm.previousWinner).toBe('Farm');
  });
  it('S4 a failed target read keeps raw work and blocks saving the cached target', async () => {
    const s = await setup();
    fireEvent.change(firstCount(), { target: { value: '77' } });
    s.fake.route('GET', '/api/config', () => 'hold');
    const reading = s.schedule.refresh();
    await change(() => {
      s.fake.held.find((item) => item.request.path === '/api/config')?.lose();
    });
    await reading;
    expect(firstCount()).toHaveValue('77');
    expect(screen.getByRole('button', { name: 'Save schedule' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(await s.schedule.save()).toBe('blocked');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it.each([
    'available_no_winner',
    'scheduler_not_observed',
    'scheduler_not_running',
    'scheduler_observation_stale',
    'unknown_emulator',
  ])('S6 recorded scheduler state %s never invents per-block quota or start', async (status) => {
    const s = await setup({ status: `schedule_status.${status}` });
    const vm = selectSchedule(s.schedule.getSnapshot(), s.entry()?.observedAtMs ?? Date.now());
    expect(vm.observationState).not.toBeNull();
    expect(vm.nextCheck).toBeNull();
    for (const label of ['Remaining quota', 'Next start']) {
      const facts = screen.getAllByText(label, { selector: 'dt' });
      expect(facts).toHaveLength(2);
      for (const fact of facts) {
        expect(fact.nextElementSibling).toHaveTextContent(/^Not reported$/u);
        expect(fact.nextElementSibling).toBeVisible();
      }
    }
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('S6 winner, observed time and server timezone use only the report; clock proposal promises no start', async () => {
    const s = await setup();
    const vm = selectSchedule(s.schedule.getSnapshot(), s.entry()?.observedAtMs ?? Date.now());
    expect(vm.winner).toBe('Farm');
    expect(vm.timezone).toBe('UTC');
    expect(vm.nextCheck).toBe('2027-01-16T00:00:00+00:00');
    expect(screen.getByText('1/15/2027, 1:00:00 AM')).toBeVisible();
    expect(screen.getByText('1/15/2027, 5:00:00 PM')).toBeVisible();
    expect(screen.getByText('Times shown in America/Denver.')).toBeVisible();
    expect(screen.getByText('2027-01-16T00:00:00+00:00', { exact: true })).not.toBeVisible();
    fireEvent.click(screen.getByText('Exact observation times', { exact: true }));
    expect(screen.getByText('2027-01-16T00:00:00+00:00', { exact: true })).toBeVisible();
    expect(screen.getByText('1800000000', { exact: true })).toBeVisible();
    expect(screen.getByText(/Handoffs wait for a natural run boundary/)).toBeVisible();
    expect(
      selectSchedule(s.schedule.getSnapshot(), (s.entry()?.observedAtMs ?? Date.now()) + 92000)
        .observationState,
    ).toBe('stale');
    expect(textOutsideCatalogue(document.body)).toEqual([]);
    expect(await axe(document.body)).toHaveNoViolations();
  });
  it('S6 summarizes revision agreement and retains both full revisions in Compare', async () => {
    const s = await setup();
    expect(screen.getByText('Saved and draft starting revisions match')).toBeVisible();
    const revisionLabel = screen.getByText('Saved profile revision', { selector: 'dt' });
    expect(revisionLabel.nextElementSibling).not.toBeVisible();
    fireEvent.click(screen.getByText('Compare the last read saved plan'));
    expect(revisionLabel.nextElementSibling).toBeVisible();
    expect(revisionLabel.nextElementSibling).toHaveTextContent(
      s.entry()?.baseRevision ?? 'missing',
    );
    expect(
      screen.getByText('Draft starting revision', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent(s.entry()?.baseRevision ?? 'missing');
    const entry = s.entry();
    if (entry === null) throw new Error('No schedule entry');
    // Supplemental UI state: revision identity differs without changing either saved plan or draft.
    await change(() => {
      s.schedule.update((state) => ({
        ...state,
        entries: {
          ...state.entries,
          [state.key ?? '']: { ...entry, baseRevision: 'different-read-revision' },
        },
      }));
    });
    expect(screen.getByText('Saved and draft starting revisions differ')).toBeVisible();
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('S3 summarizes Default and explicit weekdays while keeping the mapping editor closed', async () => {
    const s = await setup();
    const mapping = screen.getByRole('region', { name: 'Weekday mapping' });
    expect(
      within(mapping).getByText('Default', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent('farm_day');
    expect(
      within(mapping).getByText('Saturday', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent('tourney_day');
    expect(
      within(mapping).getByText('Other weekdays', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent('Use Default');
    expect(screen.getByLabelText('Sunday')).not.toBeVisible();
    await change(() => {
      s.schedule.mapWeek('sunday', 'tourney_day');
    });
    expect(
      within(mapping).getByText('Sunday', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent('tourney_day');
    await change(() => {
      s.schedule.mapWeek('sunday', 'entered-but-not-listed');
    });
    expect(
      within(mapping).getByText('Other entered value — open the mapping editor'),
    ).toBeVisible();
    fireEvent.click(screen.getByText('Edit weekday mapping'));
    expect(screen.getByLabelText('Sunday')).toHaveValue('entered-but-not-listed');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('S2 keeps cross-midnight and whole-plan removal consequences visible without Why', async () => {
    const s = await setup();
    expect(
      screen.getByText('Split a cross-midnight window into blocks on both days.'),
    ).toBeVisible();
    expect(
      screen.getByText(
        'Deletes all day plans and weekday mapping. Legacy rules apply on the next scheduler load.',
      ),
    ).toBeVisible();
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('S2 S6 generated contracts cover backend overlap/exclusive-end/midnight/daily-cap/tournament cases', () => {
    for (const [family, key] of [
      ['schedule_plan', 'overlapping_windows_the_first_listed_wins'],
      ['schedule_plan', 'window_closed_at_its_exclusive_end_falls_to_the_next'],
      ['schedule_plan', 'window_written_in_both_days_the_half_covering_now_wins'],
      ['profile_patch', 'tournament_count_raised'],
      ['profile_patch', 'two_tournament_blocks_in_one_day'],
      ['profile_patch', 'count_zero'],
    ]) {
      const found = fixture(`${family}.${key}`);
      expect(
        familySchemas[`cases.${family}`]?.safeParse({ status: found.status, body: found.body })
          .success,
      ).toBe(true);
    }
  });
});

describe('F6-11 RL8 start a day plan (backend/logs/reviews/ui-pages/f6-11-runs-leftovers-brief.md)', () => {
  const NO_PLAN = 'schedule_plan.profile_without_a_plan_has_no_plan_member';
  const CREATED = 'profile_patch.a_plan_added_to_a_profile_that_has_none';
  const FIRST_PLAN = {
    days: { every_day: [{ block: 'coin', blueprint: 'farm' }] },
    week: { default: 'every_day' },
  };
  async function startPlan(s: Awaited<ReturnType<typeof setup>>) {
    await change(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Start a day plan' }));
    });
    expect(s.entry()?.draft).not.toBeNull();
  }
  async function saveFirstPlan() {
    const kind = screen.getByRole('textbox', { name: 'Block kind' });
    fireEvent.change(kind, { target: { value: 'coin' } });
    await change(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }));
    });
  }

  it('RL8 a profile without a plan offers Start a day plan with its runs; starting sends nothing', async () => {
    const s = await setup({ profile: NO_PLAN });
    const run = screen.getByRole('combobox', { name: 'Run for the first block' });
    expect(
      within(run)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['farm', 'farm_deep', 'shards', 'tourney']);
    // The description is help beside the action; what a saved plan does stays on the page.
    expect(screen.getByRole('button', { name: 'About Start a day plan' })).toBeInTheDocument();
    expect(screen.getByText('A saved plan replaces the legacy rules.')).toBeVisible();
    await startPlan(s);
    expect(s.fake.writes()).toHaveLength(0);
    expect(s.entry()?.dirty).toBe(true);
    expect(s.entry()?.draft?.week).toEqual({ default: 'every_day' });
    expect(s.block()).toMatchObject({ blueprint: 'farm', block: '', after: '', count: '' });
    expect(screen.queryByRole('button', { name: 'Start a day plan' })).toBeNull();
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Block kind' }));
    });
  });

  it('RL8 the chosen run is the first block; Save sends the whole plan once with the read revision', async () => {
    const s = await setup({ profile: NO_PLAN, save: CREATED });
    fireEvent.change(screen.getByRole('combobox', { name: 'Run for the first block' }), {
      target: { value: 'shards' },
    });
    await startPlan(s);
    expect(s.block()?.blueprint).toBe('shards');
    fireEvent.change(screen.getByRole('combobox', { name: 'Saved run' }), {
      target: { value: 'farm' },
    });
    await saveFirstPlan();
    const writes = s.fake.writes();
    expect(writes).toHaveLength(1);
    const recorded = fixture(CREATED).request.body as Record<string, unknown>;
    const sent = JSON.parse(writes[0]?.body ?? '{}') as Record<string, unknown>;
    // The recorded creation was sent from this same read (revision ...4e): same target,
    // revision and path; the value is the plan this page drafted.
    expect(Object.keys(sent).sort()).toEqual(Object.keys(recorded).sort());
    expect(sent).toEqual({ ...recorded, value: FIRST_PLAN });
  });

  it('RL8 the recorded creation answer holds another plan: the draft is kept for reconciling', async () => {
    const s = await setup({ profile: NO_PLAN, save: CREATED });
    await startPlan(s);
    await saveFirstPlan();
    expect(s.fake.writes()).toHaveLength(1);
    expect(
      screen.getByText(
        'The saved answer differs from the submitted plan. Your draft is kept; read the saved profile before reconciling.',
      ),
    ).toBeVisible();
    expect(s.block()?.block).toBe('coin');
  });

  it('RL8 an answer holding the sent plan saves it; the start offer is gone', async () => {
    const s = await setup({ profile: NO_PLAN });
    // Modified recorded case: the recorded creation answer holds the generator's own plan;
    // here its profile holds the plan this page sent (no recorded case sends this plan).
    const answer = fixture(CREATED);
    const body = answer.body as { profile: Record<string, unknown> };
    body.profile['plan'] = FIRST_PLAN;
    s.fake.route('POST', '/api/profile-patch', () => answer);
    await startPlan(s);
    await saveFirstPlan();
    expect(
      screen.getByText('Saved schedule. This is not confirmation of scheduler reload.'),
    ).toBeVisible();
    expect(s.entry()?.dirty).toBe(false);
    expect(screen.queryByRole('button', { name: 'Start a day plan' })).toBeNull();
  });

  it('RL8 a refused first plan keeps the draft and its typing', async () => {
    const s = await setup({ profile: NO_PLAN, save: 'profile_patch.plan_emptied' });
    await startPlan(s);
    await saveFirstPlan();
    expect(screen.getByText('Schedule save refused. Your draft is kept.')).toBeVisible();
    expect(s.block()?.block).toBe('coin');
    expect(s.fake.writes()).toHaveLength(1);
  });

  it('RL8 a reload keeps the started draft instead of offering a new start', async () => {
    const storage = memoryStorage();
    const first = await setup({ profile: NO_PLAN, storage });
    await startPlan(first);
    fireEvent.change(screen.getByRole('textbox', { name: 'Block kind' }), {
      target: { value: 'coin' },
    });
    for (const cleanup of cleanups.splice(0)) cleanup();
    document.body.innerHTML = '';
    const second = await setup({ profile: NO_PLAN, storage });
    expect(second.block()).toMatchObject({ blueprint: 'farm', block: 'coin' });
    expect(screen.queryByRole('button', { name: 'Start a day plan' })).toBeNull();
    expect(second.fake.writes()).toHaveLength(0);
  });

  it('RL8 a profile with no runs explains that a block needs a run', async () => {
    const s = await setup({ profile: NO_PLAN, mount: false });
    // Modified recorded case: no recorded profile without a plan also has no runs.
    s.schedule.update((state) => ({
      ...state,
      entries: Object.fromEntries(
        Object.entries(state.entries).map(([key, entry]) => {
          const body = structuredClone(fixture(NO_PLAN).body) as {
            profile: Record<string, unknown>;
          };
          body.profile['blueprints'] = {};
          return [key, { ...entry, source: body }];
        }),
      ),
    }));
    renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
    expect(
      screen.getByText('Add a run in Runs first: every plan block names a run.'),
    ).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Start a day plan' })).toBeNull();
  });

  it('RL8 the start offer uses catalogue words and passes axe', async () => {
    await setup({ profile: NO_PLAN });
    expect(textOutsideCatalogue(document.body)).toEqual([]);
    expect(await axe(document.body)).toHaveNoViolations();
  });
});

describe('F6-11 RL8 remove the whole plan (pages fixture plan_section_delete)', () => {
  const BEFORE = 'plan_section_delete.read_before_the_plan_section_was_deleted';
  const DELETED = 'plan_section_delete.saved_the_whole_plan_section_deleted';
  const remove = () => screen.getByRole('button', { name: 'Remove the day plan' });
  const dialog = () => {
    const found = document.querySelector<HTMLElement>('dialog[open]');
    if (found === null) throw new Error('no open dialog');
    return found;
  };
  async function confirmRemove() {
    await change(() => {
      fireEvent.click(remove());
    });
    await change(() => {
      fireEvent.click(within(dialog()).getByRole('button', { name: 'Remove day plan' }));
    });
  }

  it('RL8 Cancel sends nothing; a confirmed removal sends the recorded delete once and the page offers a new start', async () => {
    const s = await setup({ profile: BEFORE, save: DELETED });
    await change(() => {
      fireEvent.click(remove());
    });
    expect(within(dialog()).getByText(/The scheduler then uses its legacy rules/u)).toBeVisible();
    await change(() => {
      fireEvent.click(within(dialog()).getByRole('button', { name: 'Keep editing' }));
    });
    expect(s.fake.writes()).toHaveLength(0);
    await confirmRemove();
    expect(s.fake.writes()).toHaveLength(1);
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '{}')).toEqual(fixture(DELETED).request.body);
    expect(
      within(screen.getByRole('status', { name: 'Profile validation and save details' })).getByText(
        /^Day plan removed\./u,
      ),
    ).toBeVisible();
    expect(screen.getByText(/This saved profile has no plan/u)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Start a day plan' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Remove the day plan' })).toBeNull();
    expect(s.entry()?.draft).toBeNull();
  });

  it('RL8 unsaved changes block the removal with their reason; nothing is sent', async () => {
    const s = await setup({ profile: BEFORE, save: DELETED });
    fireEvent.change(firstCount(), { target: { value: '7' } });
    expect(remove()).toHaveAttribute('aria-disabled', 'true');
    expect(remove()).toHaveAccessibleDescription('Save or discard your changes first.');
    await change(() => {
      fireEvent.click(remove());
    });
    expect(document.querySelector('dialog[open]')).toBeNull();
    expect(s.fake.writes()).toHaveLength(0);
  });

  it('RL8 a stale revision refuses the removal: the plan stays and nothing is resent', async () => {
    const s = await setup({
      profile: BEFORE,
      save: 'plan_section_delete.plan_section_delete_stale_revision_409',
    });
    await confirmRemove();
    expect(
      screen.getByText('Day plan removal refused. The saved plan is unchanged.'),
    ).toBeVisible();
    expect(s.entry()?.draft).not.toBeNull();
    expect(s.fake.writes()).toHaveLength(1);
  });

  it('RL8 a lost removal stays Unknown on an unchanged read and is settled only by a read without the plan; never resent', async () => {
    const s = await setup({ profile: BEFORE, save: 'hold' });
    await confirmRemove();
    await change(() => {
      s.fake.held.find((h) => h.request.method === 'POST')?.lose();
    });
    expect(screen.getByText(/Day plan removal outcome unknown/u)).toBeVisible();
    await act(async () => {
      await s.schedule.refresh();
    });
    expect(screen.getByText(/Day plan removal outcome unknown/u)).toBeVisible();
    expect(s.entry()?.intent?.readSettlement).toBeNull();
    s.readback('plan_section_delete.read_after_the_plan_section_was_deleted');
    await act(async () => {
      await s.schedule.refresh();
    });
    expect(
      within(screen.getByRole('status', { name: 'Profile validation and save details' })).getByText(
        'Readback confirms the day plan is removed.',
      ),
    ).toBeVisible();
    expect(s.entry()?.draft).toBeNull();
    expect(screen.getByRole('button', { name: 'Start a day plan' })).toBeVisible();
    expect(s.fake.writes()).toHaveLength(1);
  });

  it('RL8 a lost removal survives a reload; the read without the plan then settles it with nothing left to edit or save', async () => {
    const storage = memoryStorage();
    const first = await setup({ profile: BEFORE, save: 'hold', storage });
    await confirmRemove();
    await change(() => {
      first.fake.held.find((h) => h.request.method === 'POST')?.lose();
    });
    for (const cleanup of cleanups.splice(0)) cleanup();
    document.body.innerHTML = '';
    const second = await setup({
      profile: 'plan_section_delete.read_after_the_plan_section_was_deleted',
      storage,
    });
    await act(async () => {
      await second.schedule.refresh();
    });
    expect(
      within(screen.getByRole('status', { name: 'Profile validation and save details' })).getByText(
        'Readback confirms the day plan is removed.',
      ),
    ).toBeVisible();
    expect(second.entry()?.draft).toBeNull();
    expect(second.entry()?.dirty).toBe(false);
    expect(second.fake.writes()).toHaveLength(0);
  });

  it('RL8 an open confirmation closes and sends nothing when a read brings another revision', async () => {
    const s = await setup({ profile: BEFORE, save: DELETED });
    await change(() => {
      fireEvent.click(remove());
    });
    expect(dialog()).toBeVisible();
    s.readback('plan_section_delete.read_after_the_stale_refusal_the_plan_section_remains');
    await act(async () => {
      await s.schedule.refresh();
    });
    expect(document.querySelector('dialog[open]')).toBeNull();
    expect(s.fake.writes()).toHaveLength(0);
  });

  /** Every text the app's one live region announces from now on. */
  function announcements() {
    const region = document.querySelector('[aria-live]');
    if (region === null) throw new Error('no live region');
    const announced: string[] = [];
    const observer = new MutationObserver(() => {
      const said = region.textContent;
      if (said !== '' && announced.at(-1) !== said) announced.push(said);
    });
    observer.observe(region, { childList: true, subtree: true, characterData: true });
    cleanups.push(() => {
      observer.disconnect();
    });
    return announced;
  }
  const flush = () => change(() => new Promise((resolve) => setTimeout(resolve, 0)));

  it.each([
    // Recorded configurations naming another account and profile, and another active profile.
    ['context_fence.read_config_after_another_client_switched_the_account'],
    ['profile_target.read_config_after_editor_active_profile_switch'],
  ])(
    'RL8 F6-11-P2-01 a removal whose click-time config read names another context (%s) sends nothing and keeps the plan',
    async (switched) => {
      const s = await setup({ profile: BEFORE, save: DELETED });
      s.fake.route('GET', '/api/config', () => 'hold');
      await confirmRemove();
      expect(s.entry()?.checking).toBe(true);
      expect(remove()).toHaveAttribute('aria-disabled', 'true');
      const answer = fixture(switched);
      await change(() => {
        s.fake.next('/api/config', 'GET').respond(answer.status, answer.body);
      });
      await flush();
      expect(s.fake.writes()).toHaveLength(0);
      expect(s.entry()?.notSent).toBe('context_changed');
      expect(
        screen.getByText(/^Not sent: the emulator, account or profile changed/u, { selector: 'p' }),
      ).toBeVisible();
      expect(s.entry()?.draft).not.toBeNull();
      expect(s.entry()?.intent).toBeNull();
    },
  );

  it('RL8 F6-11-P2-01 a removal whose click-time config read gets no answer sends nothing', async () => {
    const s = await setup({ profile: BEFORE, save: DELETED });
    s.fake.route('GET', '/api/config', () => 'hold');
    await confirmRemove();
    await change(() => {
      s.fake.next('/api/config', 'GET').lose();
    });
    await flush();
    expect(s.fake.writes()).toHaveLength(0);
    expect(
      screen.getByText(
        /^Not sent: the selected emulator, account and profile could not be confirmed/u,
        {
          selector: 'p',
        },
      ),
    ).toBeVisible();
    expect(s.entry()?.draft).not.toBeNull();
    // A new person action sends it once the context is confirmed again.
    s.fake.route('GET', '/api/config', () => fixture('config_state.read_before_the_save'));
    await confirmRemove();
    await flush();
    expect(s.fake.writes()).toHaveLength(1);
  });

  it('RL8 F6-11-P2-01 a save whose click-time config read names another active profile sends nothing and keeps the draft', async () => {
    const s = await setup();
    fireEvent.change(firstCount(), { target: { value: '7' } });
    s.fake.route('GET', '/api/config', () =>
      fixture('profile_target.read_config_after_editor_active_profile_switch'),
    );
    await change(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }));
    });
    await flush();
    expect(s.fake.writes()).toHaveLength(0);
    expect(s.entry()?.notSent).toBe('context_changed');
    expect(firstCount()).toHaveValue('7');
    expect(s.entry()?.dirty).toBe(true);
  });

  it('RL8 F6-11-P2-03 typing after a lost removal survives the read that settles it', async () => {
    const s = await setup({ profile: BEFORE, save: 'hold' });
    await confirmRemove();
    await change(() => {
      s.fake.held.find((h) => h.request.method === 'POST')?.lose();
    });
    fireEvent.change(firstCount(), { target: { value: '7' } });
    s.readback('plan_section_delete.read_after_the_plan_section_was_deleted');
    await act(async () => {
      await s.schedule.refresh();
    });
    expect(s.entry()?.intent?.readSettlement).toBe('applied');
    expect(s.entry()?.dirty).toBe(true);
    expect(s.entry()?.draft).not.toBeNull();
    expect(firstCount()).toHaveValue('7');
    expect(s.fake.writes()).toHaveLength(1);
  });

  it('RL8 F6-11-P2-03 typing after a lost removal survives a reload and the read that settles it', async () => {
    const storage = memoryStorage();
    const first = await setup({ profile: BEFORE, save: 'hold', storage });
    await confirmRemove();
    await change(() => {
      first.fake.held.find((h) => h.request.method === 'POST')?.lose();
    });
    fireEvent.change(firstCount(), { target: { value: '7' } });
    for (const cleanup of cleanups.splice(0)) cleanup();
    document.body.innerHTML = '';
    const second = await setup({
      profile: 'plan_section_delete.read_after_the_plan_section_was_deleted',
      storage,
    });
    await act(async () => {
      await second.schedule.refresh();
    });
    expect(second.entry()?.intent?.readSettlement).toBe('applied');
    expect(second.entry()?.dirty).toBe(true);
    expect(second.block()?.count).toBe('7');
    expect(firstCount()).toHaveValue('7');
    expect(second.fake.writes()).toHaveLength(0);
  });

  it('RL8 F6-11-P2-04 a held removal announces only that it was requested; its answer announces the removal', async () => {
    const s = await setup({ profile: BEFORE, save: 'hold' });
    const announced = announcements();
    await confirmRemove();
    await flush();
    expect(s.fake.writes()).toHaveLength(1);
    expect(announced.some((said) => said.startsWith('Day plan removed'))).toBe(false);
    expect(announced.at(-1)).toBe(
      'Removal of the day plan requested. Waiting for its answer; nothing is confirmed yet.',
    );
    const deleted = fixture(DELETED);
    await change(() => {
      s.fake.next('/api/profile-patch', 'POST').respond(deleted.status, deleted.body);
    });
    await flush();
    expect(announced.at(-1)).toMatch(/^Day plan removed\./u);
  });

  it('RL8 F6-11-P2-04 a stale-refused removal never announces that the plan was removed', async () => {
    const s = await setup({
      profile: BEFORE,
      save: 'plan_section_delete.plan_section_delete_stale_revision_409',
    });
    const announced = announcements();
    await confirmRemove();
    await flush();
    expect(s.fake.writes()).toHaveLength(1);
    expect(announced.some((said) => said.startsWith('Day plan removed'))).toBe(false);
    expect(
      within(screen.getByRole('alert', { name: 'Profile validation and save details' })).getByText(
        'Day plan removal refused. The saved plan is unchanged.',
      ),
    ).toBeVisible();
  });

  it('RL8 the removal and its confirmation use catalogue words and pass axe', async () => {
    await setup({ profile: BEFORE, save: DELETED });
    await change(() => {
      fireEvent.click(remove());
    });
    expect(textOutsideCatalogue(document.body)).toEqual([]);
    expect(await axe(document.body)).toHaveNoViolations();
  });
});
