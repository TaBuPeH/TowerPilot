import { required as must } from '../../../support/required.ts';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, expect, it } from 'vitest';
import { createScheduleApi } from '../../../../src/api/schedule.ts';
import { contextFromConfig } from '../../../../src/api/context.ts';
import { editingTarget, editTargetScope } from '../../../../src/core/editingTarget.ts';
import { profileFilesView } from '../../../../src/core/profiles.ts';
import { own } from '../../../../src/core/read.ts';
import { SchedulePage } from '../../../../src/features/schedule/SchedulePage.tsx';
import { createScheduleStore, currentSchedule } from '../../../../src/store/schedule.ts';
import { initialDashboardState } from '../../../../src/store/dashboardStore.ts';
import { scheduleDraft } from '../../../../src/store/scheduleDraft.ts';
import { selectSchedule } from '../../../../src/store/selectors/schedule.ts';
import {
  dashboardCase,
  dashboardPagesCase as recorded,
} from '../../../support/dashboardFixture.ts';
import { createFakeTransport, createMemoryStorage } from '../../../support/fakeTransport.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { renderRegion } from '../dashboard/harness.tsx';
const fixture = (name: string) => recorded(name.includes('.') ? name : `profile_target.${name}`);
const runtime = must(contextFromConfig(fixture('inactive_editor_context_before_save').body));
const files = profileFilesView(fixture('same_account_inactive_target_listed').body);
const target = (generation = 1) => editingTarget(runtime, { name: 'second', generation }, files);
const cleanups: (() => void)[] = [];
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => {
    cleanup();
  });
});
async function change(action: () => unknown) {
  await act(async () => {
    await action();
  });
}
async function setup(storage = createMemoryStorage()) {
  const fake = createFakeTransport();
  let config = 'inactive_editor_context_before_save';
  let source = 'same_account_inactive_target_source';
  let save = 'inactive_plan_saved_while_runner_listed';
  fake.route('GET', '/api/config', () => fixture(config));
  fake.route('GET', '/api/profile-files', () => fixture('same_account_inactive_target_listed'));
  fake.route('GET', '/api/profile-src/second', () => fixture(source));
  fake.route('GET', '/api/profile-src/mine', () =>
    recorded('profile_patch.read_before_the_first_save'),
  );
  fake.route('GET', '/api/schedule/status?instance=main', () =>
    dashboardCase('schedule_status.scheduler_not_observed'),
  );
  fake.route('POST', '/api/profile-patch', () => fixture(save));
  const api = createScheduleApi(fake.transport, storage);
  const schedule = createScheduleStore(api);
  cleanups.push(schedule.dispose);
  await change(() => {
    schedule.setTarget(runtime, 1, target());
  });
  const entry = () => must(currentSchedule(schedule.getSnapshot()));
  const draftSentPlan = () => {
    // Use the recorded whole-plan value to isolate targeting/fencing from field serialization.
    const draft = scheduleDraft({
      plan: own(fixture('inactive_plan_saved_while_runner_listed').request.body, 'value'),
    });
    schedule.update((s) => ({
      ...s,
      entries: {
        ...s.entries,
        [must(s.key)]: {
          ...entry(),
          draft,
          day: 'spare_day',
          dirty: true,
          version: entry().version + 1,
        },
      },
    }));
  };
  return {
    fake,
    api,
    schedule,
    entry,
    draftSentPlan,
    config: (name: string) => {
      config = name;
    },
    source: (name: string) => {
      source = name;
    },
    save: (name: string) => {
      save = name;
    },
  };
}
it('PE3 inactive-plan removal keeps activation and legacy-rule consequences visible', async () => {
  const s = await setup();
  renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
  expect(
    screen.getByText(
      'Deletes all day plans and weekday mapping from this profile. Legacy rules apply if activated and loaded; the active scheduler is unchanged.',
    ),
  ).toBeVisible();
  expect(s.fake.writes()).toEqual([]);
});
it('PE3 reads second and saves the exact recorded fenced plan while a runner is listed', async () => {
  const s = await setup();
  expect(own(s.entry().source, 'name')).toBe('second');
  s.draftSentPlan();
  await change(() => s.schedule.save());
  expect(s.fake.writes().map((r) => JSON.parse(must(r.body)) as unknown)).toEqual([
    fixture('inactive_plan_saved_while_runner_listed').request.body,
  ]);
  expect(s.entry().dirty).toBe(false);
  expect(s.schedule.getSnapshot().key).toBe(editTargetScope(runtime, 'second'));
});
it.each([
  'read_config_after_editor_active_profile_switch',
  'read_config_after_editor_instance_switch',
  'read_config_after_editor_serial_change',
])('PE3 click-time %s sends nothing and preserves second draft', async (name) => {
  const s = await setup();
  s.draftSentPlan();
  s.config(name);
  await change(async () => {
    expect(await s.schedule.save()).toBe('context_changed');
  });
  expect(s.fake.writes()).toEqual([]);
  expect(s.entry().dirty).toBe(true);
});
it.each([
  [
    'inactive_target_written_then_answer_failed',
    'inactive_target_after_written_then_answer_failed',
    'readApplied',
  ],
  ['inactive_target_write_failed', 'inactive_target_after_write_failed', 'unknown'],
])(
  'PE3 %s settles only from a later second read holding the sent plan',
  async (save, read, expected) => {
    const s = await setup();
    s.draftSentPlan();
    s.save(save);
    await change(() => s.schedule.save());
    expect(selectSchedule(s.schedule.getSnapshot()).outcome).toBe('unknown');
    s.source(read);
    await change(() => s.schedule.refresh());
    expect(selectSchedule(s.schedule.getSnapshot()).outcome).toBe(expected);
    expect(s.fake.writes()).toHaveLength(1);
  },
);
it.each([
  'inactive_target_stale_revision',
  'inactive_target_deleted_before_patch',
  'inactive_target_starter_refused',
  'inactive_target_draft_refused',
])('PE3 %s keeps the draft and renders recorded server text', async (name) => {
  const s = await setup();
  s.draftSentPlan();
  s.save(name);
  await change(() => s.schedule.save());
  renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
  expect(screen.getByText(String(own(fixture(name).body, 'error')))).toBeVisible();
  expect(s.entry().dirty).toBe(true);
  if (name === 'inactive_target_deleted_before_patch') {
    s.source('inactive_target_after_deletion');
    await change(() => s.schedule.refresh());
    expect(selectSchedule(s.schedule.getSnapshot()).canSave).toBe(false);
    expect(own(s.entry().source, 'name')).toBe('second');
  }
});
it('PE3 target switches and reload restore only the original target draft; catalogue and axe', async () => {
  const storage = createMemoryStorage();
  const s = await setup(storage);
  const block = must(must(must(s.entry().draft).days[s.entry().day])[0]);
  await change(() => {
    s.schedule.edit(block.key, 'count', '77');
  });
  await change(() => {
    s.schedule.setTarget(runtime, 1, editingTarget(runtime, undefined, null));
  });
  expect(s.entry().dirty).toBe(false);
  await change(() => {
    s.schedule.setTarget(runtime, 1, target(3));
  });
  expect(must(must(must(s.entry().draft).days[s.entry().day])[0]).count).toBe('77');
  const restored = await setup(storage);
  expect(must(must(must(restored.entry().draft).days[restored.entry().day])[0]).count).toBe('77');
  const {
    view: { container },
  } = renderRegion(initialDashboardState(), <SchedulePage schedule={restored.schedule} />);
  await waitFor(() => {
    expect(textOutsideCatalogue(container)).toEqual([]);
  });
  expect((await axe(container)).violations).toEqual([]);
});
it.each([
  ['context_unconfirmed', '/api/config', 'config_state.file_corrupt'],
  ['target_unconfirmed', '/api/profile-files', 'config_state.file_corrupt'],
  ['target_missing', '/api/profile-files', 'profile_target.inactive_target_list_after_deletion'],
])('PE3 %s sends nothing and shows the catalogue reason', async (expected, path, name) => {
  const s = await setup();
  s.draftSentPlan();
  s.fake.route('GET', path, () => recorded(name));
  await change(async () => {
    expect(await s.schedule.save()).toBe(expected);
  });
  const { view } = renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Not sent:');
  expect(textOutsideCatalogue(view.container)).toEqual([]);
  expect(s.fake.writes()).toHaveLength(0);
  expect(s.entry().dirty).toBe(true);
});
it('PE3 a pick-return-pick during the click-time read invalidates the generation', async () => {
  const s = await setup();
  s.draftSentPlan();
  s.fake.route('GET', '/api/profile-files', () => 'hold');
  const sending = s.schedule.save();
  await change(() => {
    s.schedule.setTarget(runtime, 1, target(3));
  });
  await change(() => {
    s.fake
      .next('/api/profile-files')
      .respond(200, fixture('same_account_inactive_target_listed').body);
  });
  expect(await sending).toBe('blocked');
  expect(s.fake.writes()).toEqual([]);
  expect(s.entry().notSent).toBe('context_changed');
  expect(s.entry().dirty).toBe(true);
});
it('PE3 a valid second target remains editable with no active profile', async () => {
  const s = await setup();
  const unbound = must(contextFromConfig(fixture('read_after_unbind_account_context').body));
  s.config('read_after_unbind_account_context');
  await change(() => {
    s.schedule.setTarget(
      unbound,
      2,
      editingTarget(unbound, { name: 'second', generation: 2 }, files),
    );
  });
  expect(own(s.entry().source, 'name')).toBe('second');
  s.draftSentPlan();
  expect(selectSchedule(s.schedule.getSnapshot()).noProfile).toBe(false);
  expect(selectSchedule(s.schedule.getSnapshot()).canSave).toBe(true);
});
it('PE3 F6-14c whole-plan delete uses the recording and reads second removed with mine untouched', async () => {
  const s = await setup();
  s.source('profile_target_more.read_before_the_inactive_plan_is_removed');
  await change(s.schedule.refresh);
  s.save('profile_target_more.inactive_plan_deleted');
  await change(() => s.schedule.removePlan(must(s.entry().baseRevision)));
  expect(JSON.parse(must(must(s.fake.writes()[0]).body))).toEqual(
    fixture('profile_target_more.inactive_plan_deleted').request.body,
  );
  expect(selectSchedule(s.schedule.getSnapshot()).outcome).toBe('removed');
  s.source('profile_target_more.read_after_the_inactive_plan_was_removed');
  await change(s.schedule.refresh);
  expect(selectSchedule(s.schedule.getSnapshot()).planMissing).toBe(true);
  s.fake.route('GET', '/api/profile-src/mine', () =>
    fixture('profile_target_more.read_the_active_profile_after_the_inactive_plan_was_removed'),
  );
  await change(() => {
    s.schedule.setTarget(runtime, 1);
  });
  expect(s.entry().source).toEqual(
    fixture('profile_target_more.read_the_active_profile_after_the_inactive_plan_was_removed').body,
  );
  expect(s.fake.writes()).toHaveLength(1);
});

it.each([
  [
    'inactive_plan_delete_stale_revision_409',
    'read_before_the_inactive_plan_is_removed',
    'read_after_the_stale_delete_the_plan_remains',
  ],
  [
    'inactive_plan_delete_refused_after_account_switch',
    'read_after_the_stale_delete_the_plan_remains',
    'read_after_the_stale_delete_the_plan_remains',
  ],
])(
  'PE3 F6-14c %s keeps the plan and refusal through a fresh read',
  async (answer, before, after) => {
    const s = await setup();
    s.source(`profile_target_more.${before}`);
    await change(s.schedule.refresh);
    s.save(`profile_target_more.${answer}`);
    await change(() => s.schedule.removePlan(must(s.entry().baseRevision)));
    expect(JSON.parse(must(s.fake.writes()[0]?.body))).toEqual(
      fixture(`profile_target_more.${answer}`).request.body,
    );
    expect(s.entry().intent?.phase).toBe('refused');
    s.source(`profile_target_more.${after}`);
    await change(s.schedule.refresh);
    expect(s.entry().intent?.phase).toBe('refused');
    expect(selectSchedule(s.schedule.getSnapshot()).hasDraftPlan).toBe(true);
  },
);

it('PE3 F6-14c absent plan offers no delete and partial week or days deletes are never offered', async () => {
  const s = await setup();
  s.source('profile_target_more.read_after_the_inactive_plan_was_removed');
  await change(s.schedule.refresh);
  await change(() => s.schedule.removePlan(must(s.entry().baseRevision)));
  renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
  expect(screen.queryByRole('button', { name: /Remove.*plan/i })).toBeNull();
  expect(s.fake.writes()).toEqual([]);
  const absent = fixture(
    'profile_target_more.inactive_plan_delete_when_the_section_is_already_gone',
  );
  expect(own(absent.body, 'revision')).toBe(own(absent.request.body, 'expected_revision'));
  expect(
    fixture('profile_target_more.inactive_plan_week_deleted_is_refused_a_plan_with_no_blocks')
      .status,
  ).toBe(400);
});

it('PE3 F6-14c C-01 stale plan deletion stays refused after matching absence and reload', async () => {
  const storage = createMemoryStorage();
  const s = await setup(storage);
  s.source('profile_target_more.read_before_the_inactive_plan_is_removed');
  await change(s.schedule.refresh);
  s.save('profile_target_more.inactive_plan_delete_stale_revision_409');
  const draft = s.entry().draft;
  await change(() => s.schedule.removePlan(must(s.entry().baseRevision)));
  expect(JSON.parse(must(s.fake.writes()[0]?.body))).toEqual(
    fixture('profile_target_more.inactive_plan_delete_stale_revision_409').request.body,
  );
  // Later source models another client independently removing the plan at the recorded
  // advanced revision: absence must not reclassify the answered stale refusal.
  const matching = fixture('profile_target_more.read_after_the_inactive_plan_was_removed');
  const check = async (current: Awaited<ReturnType<typeof setup>>) => {
    current.fake.route('GET', '/api/profile-src/second', () => matching);
    await change(current.schedule.refresh);
    expect(current.entry().intent).toMatchObject({ phase: 'refused', readSettlement: null });
    expect(selectSchedule(current.schedule.getSnapshot()).outcome).toBe('removeRefused');
    expect(current.entry().draft).toEqual(draft);
  };
  await check(s);
  expect(s.fake.writes()).toHaveLength(1);
  s.schedule.dispose();
  const restored = await setup(storage);
  await check(restored);
  expect(restored.fake.writes()).toEqual([]);
});
it('PE3 a late second answer updates second only, preserving active mine draft', async () => {
  const s = await setup();
  s.draftSentPlan();
  s.fake.route('POST', '/api/profile-patch', () => 'hold');
  await change(() => s.schedule.save());
  const secondKey = must(s.schedule.getSnapshot().key);
  await change(() => {
    s.schedule.setTarget(runtime, 1);
  });
  const block = must(must(must(s.entry().draft).days[s.entry().day])[0]);
  await change(() => {
    s.schedule.edit(block.key, 'count', '88');
  });
  await change(() => {
    s.fake
      .next('/api/profile-patch')
      .respond(200, fixture('inactive_plan_saved_while_runner_listed').body);
  });
  expect(must(must(must(s.entry().draft).days[s.entry().day])[0]).count).toBe('88');
  expect(s.entry().dirty).toBe(true);
  expect(must(s.schedule.getSnapshot().entries[secondKey]).dirty).toBe(false);
});
it.each(['active', 'instance', 'serial', 'account'])(
  'PE3 %s switch never moves second draft',
  async (kind) => {
    const s = await setup();
    const block = must(must(must(s.entry().draft).days[s.entry().day])[0]);
    await change(() => {
      s.schedule.edit(block.key, 'count', '66');
    });
    const changed =
      kind === 'account'
        ? must(contextFromConfig(recorded('config_state.effective_view_of_the_other_account').body))
        : must(
            contextFromConfig(
              fixture(
                `read_config_after_editor_${kind === 'active' ? 'active_profile_switch' : kind === 'instance' ? 'instance_switch' : 'serial_change'}`,
              ).body,
            ),
          );
    await change(() => {
      s.schedule.setTarget(changed, 2);
    });
    if (kind === 'active') {
      // Activation changes the runtime fence, not the identity of this same edited file.
      expect(s.schedule.getSnapshot().key).toBe(editTargetScope(runtime, 'second'));
      expect(s.entry().dirty).toBe(true);
    } else expect(s.schedule.getSnapshot().key).not.toBe(editTargetScope(runtime, 'second'));
    await change(() => {
      s.schedule.setTarget(runtime, 3, target(3));
    });
    expect(must(must(must(s.entry().draft).days[s.entry().day])[0]).count).toBe('66');
  },
);

it.each([
  'inactive_target_source_that_cannot_be_parsed_400',
  'inactive_target_source_that_is_not_a_mapping_400',
])('PE3 F6-14c %s shows the server error and blocks writes', async (name) => {
  const s = await setup();
  const failed = fixture(`profile_target_more.${name}`);
  s.fake.route('GET', '/api/profile-src/second', () => failed);
  await change(s.schedule.refresh);
  renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
  expect(
    screen.getByText(String(own(failed.body, 'error')), { normalizer: (value) => value }),
  ).toBeVisible();
  expect(selectSchedule(s.schedule.getSnapshot()).canSave).toBe(false);
  expect(s.fake.writes()).toEqual([]);
});

it('PE3 a successful save after activating second supersedes its older inactive refusal', async () => {
  const s = await setup();
  s.draftSentPlan();
  s.save('inactive_target_stale_revision');
  await change(() => s.schedule.save());
  expect(s.entry().reconcile).toBe(true);
  s.source('inactive_target_after_written_then_answer_failed');
  await change(() => s.schedule.refresh());
  await change(() => {
    s.schedule.reconcile();
  });
  expect(s.entry().reconcile).toBe(false);
  s.config('read_config_after_editor_active_profile_switch');
  const active = must(
    contextFromConfig(fixture('read_config_after_editor_active_profile_switch').body),
  );
  await change(() => {
    s.schedule.setTarget(active, 2);
  });
  s.save('inactive_plan_saved_while_runner_listed');
  await change(() => s.schedule.save());
  expect(s.entry().intent?.phase).toBe('confirmed');
  expect(s.entry().reconcile).toBe(false);
});

it('PE3 removing an inactive plan explains that the active scheduler is unchanged', async () => {
  const s = await setup();
  renderRegion(initialDashboardState(), <SchedulePage schedule={s.schedule} />);
  fireEvent.click(screen.getByRole('button', { name: 'Remove the day plan' }));
  expect(screen.getByRole('dialog')).toHaveTextContent('The active scheduler is unchanged.');
});

it('PE3 activating the editing target clears the old active scheduler evidence while its read is pending', async () => {
  const s = await setup();
  s.fake.route('GET', '/api/schedule/status?instance=main', () => {
    const answer = recorded('schedule_plan.observed_under_the_active_profile');
    // Modified recorded scheduler observation: profile_target has no scheduler-status case.
    // Bind this recorded winner to the recorded alpha runtime to test evidence isolation.
    return { ...answer, body: { ...(answer.body as Record<string, unknown>), context: runtime } };
  });
  await change(() => s.schedule.refresh());
  expect(selectSchedule(s.schedule.getSnapshot()).winner).not.toBeNull();
  s.draftSentPlan();
  s.fake.route('GET', '/api/config', () => 'hold');
  const active = must(
    contextFromConfig(fixture('read_config_after_editor_active_profile_switch').body),
  );
  const mismatches: string[] = [];
  const unsubscribe = s.schedule.subscribe(() => {
    const state = s.schedule.getSnapshot();
    if (state.context?.profile === 'second' && selectSchedule(state).winner !== null)
      mismatches.push('old winner under new active profile');
  });
  await change(() => {
    s.schedule.setTarget(
      active,
      2,
      editingTarget(active, { name: 'second', generation: 2 }, files),
    );
  });
  expect(selectSchedule(s.schedule.getSnapshot()).winner).toBeNull();
  expect(s.entry().observedAtMs).toBeNull();
  expect(s.entry().dirty).toBe(true);
  expect(mismatches).toEqual([]);
  unsubscribe();
});
