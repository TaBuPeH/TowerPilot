import { required } from '../../support/required.ts';
import { act, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createScheduleApi } from '../../../src/api/schedule.ts';
import { createAbilitiesApi } from '../../../src/api/abilities.ts';
import { readStoredRecord } from '../../../src/api/intents/persistence.ts';
import type { KeyValueStorage } from '../../../src/api/storage.ts';
import { contextFromConfig } from '../../../src/api/context.ts';
import { editingTarget, type EditingTarget } from '../../../src/core/editingTarget.ts';
import { profileFilesView } from '../../../src/core/profiles.ts';
import { entries, own, stringList } from '../../../src/core/read.ts';
import { SchedulePage } from '../../../src/features/schedule/SchedulePage.tsx';
import { AbilitiesPage } from '../../../src/features/abilities/AbilitiesPage.tsx';
import { createScheduleStore, currentSchedule } from '../../../src/store/schedule.ts';
import { scheduleDraft } from '../../../src/store/scheduleDraft.ts';
import { selectSchedule } from '../../../src/store/selectors/schedule.ts';
import {
  createAbilitiesStore,
  currentAbility,
  currentAbilities,
  abilityKey,
} from '../../../src/store/abilities.ts';
import { abilityNode, abilityValue } from '../../../src/store/abilitiesDraft.ts';
import { initialDashboardState } from '../../../src/store/dashboardStore.ts';
import { renderPage, renderRegion } from './dashboard/harness.tsx';
import { createFakeTransport, createMemoryStorage } from '../../support/fakeTransport.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  type DashboardCase,
} from '../../support/dashboardFixture.ts';
import { fencedCase, fencedRuntime, fencedSourceBefore } from '../../support/profilePatchFenced.ts';
import { textOutsideCatalogue } from '../copy/renderedText.ts';

// Each case renders a whole page; under the full parallel suite 5 s is not enough.
vi.setConfig({ testTimeout: 30_000 });

const PATCH = '/api/profile-patch';
const SOURCE = '/api/profile-src/mine';
const config = () => dashboardPagesCase('profile_target.inactive_editor_context_before_save');
const cleanups: (() => void)[] = [];
async function change(action: () => unknown) {
  await act(async () => {
    await action();
    await vi.advanceTimersByTimeAsync(0);
  });
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => {
    cleanup();
  });
  vi.useRealTimers();
  window.location.hash = '';
});
function transport(name: string) {
  const fake = createFakeTransport();
  let source = fencedSourceBefore(name);
  fake.route('GET', '/api/config', config);
  fake.route('GET', SOURCE, () => source);
  fake.route('GET', '/api/schedule/status?instance=main', () =>
    dashboardCase('schedule_status.scheduler_not_observed'),
  );
  fake.route('GET', '/api/vocab', () => dashboardPagesCase('profile_reads.vocab_ok'));
  fake.route('GET', '/api/settings', () =>
    dashboardPagesCase('settings.populated_account_every_module'),
  );
  fake.route('POST', PATCH, () => 'hold');
  return {
    fake,
    source: (value: DashboardCase) => {
      source = value;
    },
  };
}
async function schedulePage(name: string, storage = createMemoryStorage()) {
  const s = transport(name);
  const api = createScheduleApi(s.fake.transport, storage);
  const schedule = createScheduleStore(api);
  cleanups.push(schedule.dispose);
  await change(() => {
    schedule.setTarget(fencedRuntime, 1);
  });
  const view = renderRegion(initialDashboardState(), <SchedulePage schedule={schedule} />);
  const draft = () => {
    const entry = required(currentSchedule(schedule.getSnapshot()));
    const next = scheduleDraft({ plan: own(fencedCase(name).request.body, 'value') });
    schedule.update((state) => ({
      ...state,
      entries: {
        ...state.entries,
        [required(state.key)]: {
          ...entry,
          draft: next,
          day: 'spare_day',
          dirty: true,
          version: entry.version + 1,
        },
      },
    }));
  };
  return {
    ...s,
    storage,
    view,
    api,
    schedule,
    draft,
    entry: () => required(currentSchedule(schedule.getSnapshot())),
  };
}
it.each(['active_plan_saved', 'active_plan_deleted'])(
  'F6-14b Schedule active write sends exactly profile_patch_fenced.%s',
  async (name) => {
    const s = await schedulePage(name);
    if (name === 'active_plan_saved') await change(s.draft);
    await change(() =>
      name === 'active_plan_saved'
        ? s.schedule.save()
        : s.schedule.removePlan(required(s.entry().baseRevision)),
    );
    expect(JSON.parse(required(s.fake.writes()[0]?.body))).toEqual(fencedCase(name).request.body);
    expect(own(JSON.parse(required(s.fake.writes()[0]?.body)), 'expected_context')).toEqual(
      fencedRuntime,
    );
    if (name === 'active_plan_deleted')
      expect(JSON.parse(required(s.fake.writes()[0]?.body))).not.toHaveProperty('value');
    await change(() => {
      s.fake.next(PATCH).respond(fencedCase(name).status, fencedCase(name).body);
    });
    expect(s.fake.writes()).toHaveLength(1);
  },
);
it('F6-14b Schedule context_changed keeps the draft, shows current context and sends nothing more', async () => {
  const s = await schedulePage('active_plan_saved');
  await change(s.draft);
  await change(s.schedule.save);
  const before = s.entry().draft;
  await change(() => {
    s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  });
  expect(screen.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(screen.getByText(/"account": "beta"/u)).toHaveTextContent('theirs');
  expect(s.entry().draft).toEqual(before);
  expect(s.entry().dirty).toBe(true);
  expect(s.api.machine.resend(required(s.entry().intent).id)).toBe('not_resendable');
  await change(() => vi.advanceTimersByTimeAsync(60_000));
  expect(s.fake.writes()).toHaveLength(1);
});
it.each(['active_plan_saved', 'active_plan_deleted'])(
  'F6-14b Schedule read_after_%s settles an Unknown without resending',
  async (name) => {
    const s = await schedulePage(name);
    if (name === 'active_plan_saved') await change(s.draft);
    await change(() =>
      name === 'active_plan_saved'
        ? s.schedule.save()
        : s.schedule.removePlan(required(s.entry().baseRevision)),
    );
    await change(() => {
      s.fake.next(PATCH).lose();
    });
    expect(selectSchedule(s.schedule.getSnapshot()).outcome).toBe(
      name === 'active_plan_saved' ? 'unknown' : 'removeUnknown',
    );
    s.source(fencedCase(`read_after_${name}`));
    await change(s.schedule.refresh);
    expect(selectSchedule(s.schedule.getSnapshot()).outcome).toBe(
      name === 'active_plan_saved' ? 'readApplied' : 'readRemoved',
    );
    expect(s.fake.writes()).toHaveLength(1);
  },
);
async function abilitiesPage(
  name: string,
  storage = createMemoryStorage(),
  configCase = config(),
  target?: EditingTarget,
) {
  const s = transport(name);
  s.fake.route('GET', '/api/config', () => configCase);
  const api = createAbilitiesApi(s.fake.transport, storage);
  const store = createAbilitiesStore(api);
  cleanups.push(store.dispose);
  await change(() => {
    store.setTarget(required(contextFromConfig(configCase.body)), 1, target);
  });
  const view = renderRegion(initialDashboardState(), <AbilitiesPage abilities={store} />);
  const path = stringList(own(fencedCase(name).request.body, 'path')) ?? [];
  const family = path[1] === 'uw_policies' ? 'uw_policies' : 'rescue_policies';
  const id = required(path[2]);
  const deleting = own(fencedCase(name).request.body, 'op') === 'delete';
  const send = async () => {
    await change(() => {
      if (deleting) store.select(abilityKey(family, id));
      else {
        store.create(family, id, false);
        store.edit([], abilityNode(own(fencedCase(name).request.body, 'value')));
      }
      store.save(deleting);
    });
  };
  return {
    ...s,
    api,
    store,
    send,
    storage,
    view,
    entry: () => required(currentAbility(store.getSnapshot())),
  };
}
const abilitiesCases = [
  'active_uw_policy_saved',
  'active_uw_policy_deleted',
  'active_rescue_policy_saved',
  'active_rescue_policy_deleted',
];
it.each([
  ['UW', 'active_uw_policy_saved', 'changed'],
  ['UW', 'active_uw_policy_saved', 'unconfirmed'],
  ['rescue', 'active_rescue_policy_saved', 'changed'],
  ['rescue', 'active_rescue_policy_saved', 'unconfirmed'],
])(
  'F6-14b C-01 Abilities active %s keeps draft when click context is %s %s',
  async (_family, name, result) => {
    const s = await abilitiesPage(required(name));
    s.fake.route('GET', '/api/config', () =>
      result === 'changed'
        ? dashboardPagesCase('profile_target.read_config_after_editor_active_profile_switch')
        : 'hold',
    );
    await s.send();
    if (result === 'unconfirmed') await change(() => vi.advanceTimersByTimeAsync(15_000));
    expect(s.fake.writes()).toHaveLength(0);
    expect(s.entry().dirty).toBe(true);
    expect(abilityValue(s.entry().node)).toEqual(
      own(fencedCase(required(name)).request.body, 'value'),
    );
    expect(s.entry().outcome).toBe('notSent');
    expect(
      screen.getByText(
        result === 'changed'
          ? 'The runtime context or editing target changed. Nothing was sent; the draft is kept.'
          : 'The runtime context could not be confirmed. Nothing was sent; the draft is kept.',
      ),
    ).toBeVisible();
  },
);

it('F6-14b C-03 Schedule refusal survives reload and a matching read', async () => {
  const s = await schedulePage('active_plan_saved');
  await change(s.draft);
  await change(s.schedule.save);
  const draft = s.entry().draft;
  await change(() => {
    s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  });
  s.view.view.unmount();
  s.schedule.dispose();
  const again = await schedulePage('active_plan_saved', s.storage);
  again.source(fencedCase('read_after_active_plan_saved'));
  await change(again.schedule.refresh);
  expect(again.entry().intent?.phase).toBe('refused');
  expect(again.entry().draft).toEqual(draft);
  expect(again.entry().dirty).toBe(true);
  expect(screen.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(screen.getByText(/"account": "beta"/u)).toHaveTextContent('theirs');
  expect(again.api.machine.resend(required(again.entry().intent).id)).toBe('not_resendable');
  expect(again.fake.writes()).toHaveLength(0);
});

it.each(abilitiesCases)(
  'F6-14b C-03 Abilities refusal survives reload and a matching read for %s',
  async (name) => {
    const s = await abilitiesPage(name);
    await s.send();
    const draft = abilityValue(s.entry().node);
    const dirty = s.entry().dirty;
    // Modified recorded reply: the runtime fence precedes validation of both policy paths.
    await change(() => {
      s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
    });
    s.view.view.unmount();
    s.store.dispose();
    const again = await abilitiesPage(name, s.storage);
    again.source(fencedCase(`read_after_${name}`));
    await change(again.store.refresh);
    expect(again.entry().outcome).toBe('contextChanged');
    expect(abilityValue(again.entry().node)).toEqual(draft);
    expect(again.entry().dirty).toBe(dirty);
    await change(again.store.reconcile);
    expect(again.entry().outcome).toBe('contextChanged');
    expect(screen.getByText('Not saved: the account or profile changed')).toBeVisible();
    expect(screen.getByText(/"current_context"/u)).toHaveTextContent('beta');
    expect(again.fake.writes()).toHaveLength(0);
  },
);

it.each(['active_uw_policy_saved', 'active_rescue_policy_saved'])(
  'F6-14b C-03 Abilities late refusal stays refused across an epoch change for %s',
  async (name) => {
    const s = await abilitiesPage(name);
    await s.send();
    const draft = abilityValue(s.entry().node);
    await change(() => {
      s.store.setTarget(fencedRuntime, 2);
    });
    // Modified recorded reply: the runtime refusal is independent of the policy patch path.
    await change(() => {
      s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
    });
    s.source(fencedCase(`read_after_${name}`));
    await change(s.store.refresh);
    expect(s.entry().outcome).toBe('contextChanged');
    expect(s.entry().dirty).toBe(true);
    expect(abilityValue(s.entry().node)).toEqual(draft);
    expect(screen.getByText(/"current_context"/u)).toHaveTextContent('beta');
    expect(s.fake.writes()).toHaveLength(1);
  },
);

it('F6-14b C-03 Abilities only an explicit Save supersedes a restored refusal', async () => {
  const s = await abilitiesPage('active_uw_policy_saved');
  await s.send();
  // Modified recorded reply: the runtime refusal precedes validation of the UW path.
  await change(() => {
    s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  });
  s.view.view.unmount();
  s.store.dispose();
  const again = await abilitiesPage('active_uw_policy_saved', s.storage);
  expect(again.entry().outcome).toBe('contextChanged');
  expect(again.fake.writes()).toHaveLength(0);
  await change(() => {
    again.store.save();
  });
  expect(again.fake.writes()).toHaveLength(1);
  expect(JSON.parse(required(again.fake.writes()[0]?.body))).toEqual(
    fencedCase('active_uw_policy_saved').request.body,
  );
  await change(() => {
    again.fake.next(PATCH).respond(200, fencedCase('active_uw_policy_saved').body);
  });
  expect(again.entry().outcome).toBe('saved');
  again.view.view.unmount();
  again.store.dispose();
  const last = await abilitiesPage('active_uw_policy_saved', s.storage);
  expect(last.api.machine.snapshot().some((item) => item.phase === 'refused')).toBe(false);
  expect(last.fake.writes()).toHaveLength(0);
});

it.each([
  ['active_uw_policy_saved', 1],
  ['active_uw_policy_saved', 2],
  ['active_rescue_policy_saved', 1],
  ['active_rescue_policy_saved', 2],
  ['active_uw_policy_deleted', 1],
  ['active_uw_policy_deleted', 2],
  ['active_rescue_policy_deleted', 1],
  ['active_rescue_policy_deleted', 2],
] as const)(
  'F6-14b C-03 Abilities late refusal recovers %s after %s matching reads and reload',
  async (name, reads) => {
    const s = await abilitiesPage(name);
    await s.send();
    const draft = abilityValue(s.entry().node);
    // A refused deletion recovered after readback is an editable draft again.
    const dirty = true;
    await change(() => {
      s.store.setTarget(fencedRuntime, 2);
    });
    s.source(fencedCase(`read_after_${name}`));
    for (let count = 0; count < reads; count += 1) await change(s.store.refresh);
    // Modified recorded reply: the runtime refusal is independent of the policy patch path.
    await change(() => {
      s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
    });
    expect(s.entry().outcome).toBe('contextChanged');
    expect(s.entry().dirty).toBe(dirty);
    expect(abilityValue(s.entry().node)).toEqual(draft);
    s.view.view.unmount();
    s.store.dispose();
    const again = await abilitiesPage(name, s.storage);
    again.source(fencedCase(`read_after_${name}`));
    await change(again.store.refresh);
    expect(again.entry().outcome).toBe('contextChanged');
    expect(again.entry().dirty).toBe(dirty);
    expect(abilityValue(again.entry().node)).toEqual(draft);
    expect(screen.getByText(/"current_context"/u)).toHaveTextContent('beta');
    expect(again.fake.writes()).toHaveLength(0);
  },
);

it('F6-14b C-03 Abilities saving another policy preserves the original refusal through reload', async () => {
  const s = await abilitiesPage('active_uw_policy_saved');
  await s.send();
  const original = s.entry();
  // Modified recorded reply: the runtime refusal precedes validation of the UW path.
  await change(() => {
    s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  });
  await change(s.store.refresh);
  await change(() => {
    const body = fencedCase('active_rescue_policy_saved').request.body;
    s.store.create('rescue_policies', 'extra', false);
    s.store.edit([], abilityNode(own(body, 'value')));
    s.store.save();
  });
  expect(s.fake.writes()).toHaveLength(2);
  expect(JSON.parse(required(s.fake.writes()[1]?.body))).toEqual(
    fencedCase('active_rescue_policy_saved').request.body,
  );
  await change(() => {
    s.fake.next(PATCH).respond(200, fencedCase('active_rescue_policy_saved').body);
  });
  s.view.view.unmount();
  s.store.dispose();
  const again = await abilitiesPage('active_uw_policy_saved', s.storage);
  await change(() => {
    again.store.select(abilityKey(original.family, original.id));
  });
  expect(again.entry().outcome).toBe('contextChanged');
  expect(again.entry().dirty).toBe(true);
  expect(screen.getByText(/"current_context"/u)).toHaveTextContent('beta');
  expect(again.fake.writes()).toHaveLength(0);
});

it.each(['active_uw_policy_saved', 'active_rescue_policy_saved'])(
  'F6-14b C-03 Abilities revision adoption preserves the pending refusal draft for %s through reload',
  async (name) => {
    const s = await abilitiesPage(name);
    await s.send();
    const draft = abilityValue(s.entry().node);
    await change(() => {
      s.store.setTarget(fencedRuntime, 2);
    });
    const matched = fencedCase(`read_after_${name}`);
    s.source(matched);
    await change(s.store.refresh);
    // Modified recorded source: a different writer advances the revision while preserving
    // the policy value, exposing revision adoption before the held answer arrives.
    s.source({
      ...matched,
      body: { ...Object.fromEntries(entries(matched.body)), revision: 'f'.repeat(64) },
    });
    await change(s.store.refresh);
    await change(s.store.reconcile);
    // Modified recorded reply: the context refusal precedes validation of either policy path.
    await change(() => {
      s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
    });
    expect(s.entry().outcome).toBe('contextChanged');
    expect(s.entry().dirty).toBe(true);
    expect(abilityValue(s.entry().node)).toEqual(draft);
    s.view.view.unmount();
    s.store.dispose();
    const again = await abilitiesPage(name, s.storage);
    again.source(matched);
    await change(again.store.refresh);
    expect(again.entry().outcome).toBe('contextChanged');
    expect(again.entry().dirty).toBe(true);
    expect(abilityValue(again.entry().node)).toEqual(draft);
    expect(screen.getByText(/"current_context"/u)).toHaveTextContent('beta');
    expect(again.fake.writes()).toHaveLength(0);
  },
);

it.each(['active_uw_policy_saved', 'active_rescue_policy_saved'])(
  'F6-14b C-03 Abilities refusal follows its editing file across a binding change on reload for %s',
  async (name) => {
    const s = await abilitiesPage(name);
    await s.send();
    const draft = abilityValue(s.entry().node);
    // Modified recorded reply: the runtime refusal is independent of the policy patch path.
    await change(() => {
      s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
    });
    s.view.view.unmount();
    s.store.dispose();
    const switched = dashboardPagesCase(
      'profile_target.read_config_after_editor_active_profile_switch',
    );
    const context = required(contextFromConfig(switched.body));
    const target = editingTarget(
      context,
      { name: 'mine', generation: 2 },
      profileFilesView(
        dashboardPagesCase('profile_target.same_account_inactive_target_listed').body,
      ),
    );
    const again = await abilitiesPage(name, s.storage, switched, target);
    again.source(fencedCase(`read_after_${name}`));
    await change(again.store.refresh);
    expect(again.entry().outcome).toBe('contextChanged');
    expect(again.entry().dirty).toBe(true);
    expect(abilityValue(again.entry().node)).toEqual(draft);
    expect(screen.getByText(/"current_context"/u)).toHaveTextContent('beta');
    expect(again.fake.writes()).toHaveLength(0);
  },
);

it('F6-14b C-03 Collection journal validates the definite refusal before restoration', async () => {
  const page = await collectionPage('active_gather_label_saved');
  await page.send();
  // Modified recorded reply: the runtime fence precedes validation of the label path.
  page.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  await page.advance(1_000);
  const group = required(page.storage.entries?.());
  const raw: unknown = JSON.parse(
    required(Object.entries(group).find(([key]) => key.startsWith('tp.intents.v1:'))?.[1]),
  );
  const item = Object.fromEntries(entries(required(Array.isArray(raw) ? raw[0] : undefined)));
  const answer = Object.fromEntries(entries(own(item, 'answer')));
  const body = Object.fromEntries(entries(own(answer, 'body')));
  expect(readStoredRecord(item)?.phase).toBe('refused');
  for (const invalid of [
    { ...item, answer: { ...answer, status: 500 } },
    { ...item, answer: { ...answer, contract: 'drift' } },
    { ...item, answer: { ...answer, body: { ...body, current_context: {} } } },
    { ...item, kind: 'pause' },
    { ...item, readSettlement: 'applied' },
    { ...item, accepted: true },
    { ...item, request: { ...Object.fromEntries(entries(own(item, 'request'))), body: '{' } },
    { ...item, request: { ...Object.fromEntries(entries(own(item, 'request'))), body: '[]' } },
  ])
    expect(readStoredRecord(invalid)).toBeNull();
});

it.each([
  ['Collection', 'active_gather_label_saved', '#collection/policy/all_on', 'Name shown', 'Offline'],
  ['Runs entry', 'active_run_entry_label_saved', '#runs/saved/farm', 'Run label', 'Offline farm'],
])(
  'F6-14b C-03 %s refusal survives reload and a matching read',
  async (pageName, name, hash, label, value) => {
    const page =
      pageName === 'Collection'
        ? await collectionPage(required(name))
        : await runsPage(required(name));
    await page.send();
    // Modified recorded reply: the runtime fence precedes validation of every patch path.
    page.fake
      .next(PATCH)
      .respond(409, fencedCase('active_patch_refused_after_account_switch').body);
    await page.advance(1_000);
    const draft = page.data.store.getSnapshot().runDrafts;
    page.view.unmount();
    page.data.stop();
    const again = await dashboardPage(required(name), required(hash), page.storage);
    again.source(fencedCase(`read_after_${name}`));
    if (pageName === 'Collection') again.data.commands.checkCollection();
    else again.data.commands.checkRunEntry();
    await again.advance(1_000);
    expect(again.view.getByText('Not saved: the account or profile changed')).toBeVisible();
    expect(again.view.getByText(/"account": "beta"/u)).toHaveTextContent('theirs');
    expect(again.view.getByRole('textbox', { name: required(label) })).toHaveValue(value);
    expect(again.data.store.getSnapshot().runDrafts).toEqual(draft);
    const intent = required(
      again.data.machine.snapshot().find((item) => item.kind === 'profile_patch'),
    );
    expect(intent.phase).toBe('refused');
    expect(again.data.machine.resend(intent.id)).toBe('not_resendable');
    expect(again.fake.writes()).toHaveLength(0);
  },
);
it.each(abilitiesCases)(
  'F6-14b Abilities active write sends exactly profile_patch_fenced.%s',
  async (name) => {
    const s = await abilitiesPage(name);
    await s.send();
    expect(JSON.parse(required(s.fake.writes()[0]?.body))).toEqual(fencedCase(name).request.body);
    expect(own(JSON.parse(required(s.fake.writes()[0]?.body)), 'expected_context')).toEqual(
      fencedRuntime,
    );
    if (name.endsWith('deleted'))
      expect(JSON.parse(required(s.fake.writes()[0]?.body))).not.toHaveProperty('value');
    await change(() => {
      s.fake.next(PATCH).respond(fencedCase(name).status, fencedCase(name).body);
    });
    expect(s.entry().outcome).toBe('saved');
  },
);
it('F6-14b Abilities context_changed keeps the draft, shows current context and sends nothing more', async () => {
  const s = await abilitiesPage('active_uw_policy_saved');
  await s.send();
  const before = abilityValue(s.entry().node);
  // Modified recorded reply: the context fence precedes patch-path validation, so the plan
  // refusal applies to the UW submission without changing any server response field.
  await change(() => {
    s.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  });
  expect(s.entry().outcome).toBe('contextChanged');
  expect(screen.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(screen.getByText(/"current_context"/u)).toHaveTextContent('beta');
  expect(abilityValue(s.entry().node)).toEqual(before);
  expect(s.entry().dirty).toBe(true);
  await change(() => vi.advanceTimersByTimeAsync(60_000));
  expect(s.fake.writes()).toHaveLength(1);
});
it.each(abilitiesCases)(
  'F6-14b Abilities read_after_%s settles an Unknown without resending',
  async (name) => {
    const s = await abilitiesPage(name);
    await s.send();
    await change(() => {
      s.fake.next(PATCH).lose();
    });
    expect(s.entry().outcome).toBe('unknown');
    s.source(fencedCase(`read_after_${name}`));
    await change(s.store.refresh);
    expect(
      currentAbilities(s.store.getSnapshot())?.editors[abilityKey(s.entry().family, s.entry().id)]
        ?.outcome,
    ).toBe('readApplied');
    expect(s.fake.writes()).toHaveLength(1);
  },
);
async function dashboardPage(
  name: string,
  hash: string,
  storage: KeyValueStorage & {
    readonly entries?: () => Record<string, string>;
  } = createMemoryStorage(),
) {
  let source = fencedSourceBefore(name);
  window.location.hash = hash;
  const page = await renderPage({
    storage,
    before: (fake) => {
      fake.route('GET', '/api/config', config);
      fake.route('GET', SOURCE, () => source);
      fake.route('GET', '/api/profile-files', () =>
        dashboardPagesCase('profile_target.same_account_inactive_target_listed'),
      );
      fake.route('GET', '/api/vocab', () => dashboardPagesCase('profile_reads.vocab_ok'));
      fake.route('GET', '/api/settings', () => {
        const found = dashboardPagesCase('settings.populated_account_every_module');
        // Modified recorded settings: align the legacy installation metadata to the recorded
        // alpha runtime. Profile values and revision come from the unmodified fenced source.
        return {
          ...found,
          body: {
            ...Object.fromEntries(entries(found.body)),
            context: {
              ...Object.fromEntries(entries(own(found.body, 'context'))),
              account: 'alpha',
            },
          },
        };
      });
      fake.route('GET', '/api/run-templates', () =>
        dashboardPagesCase(
          'collection_first_policy.read_the_run_templates_that_carry_the_default_gathering_policies',
        ),
      );
    },
  });
  await page.advance(1_000);
  return {
    ...page,
    source: (value: DashboardCase) => {
      source = value;
    },
  };
}
async function collectionPage(name: string) {
  const page = await dashboardPage(
    name,
    `#collection/policy/${name === 'active_unused_gather_deleted' ? 'spare' : 'all_on'}`,
  );
  const send = async () => {
    if (name === 'active_gather_label_saved') {
      await page.user.type(page.view.getByRole('textbox', { name: 'Name shown' }), 'Offline');
      await page.user.click(page.view.getByRole('button', { name: /Save name/u }));
    } else if (name === 'active_gather_copy_saved') {
      await page.user.type(page.view.getByRole('textbox', { name: 'New identifier' }), 'spare');
      await page.user.click(page.view.getByRole('button', { name: /Save copy/u }));
    } else if (name === 'active_gather_template_created') {
      await page.user.selectOptions(
        page.view.getByRole('combobox', { name: 'Template gathering default' }),
        JSON.stringify(['farm', 'all_on']),
      );
      await page.user.type(
        page.view.getByRole('textbox', { name: 'New policy identifier' }),
        'fresh',
      );
      await page.user.click(page.view.getByRole('button', { name: /Save policy/u }));
    } else if (name === 'active_unused_gather_deleted') {
      await page.user.click(page.view.getByRole('button', { name: /Delete this policy/u }));
      await page.user.click(page.view.getByRole('button', { name: 'Delete' }));
    } else {
      await page.user.click(page.view.getByRole('checkbox', { name: 'Quest scan' }));
      await page.user.click(page.view.getByRole('button', { name: /Save chores/u }));
    }
    await page.advance();
  };
  return { ...page, send };
}
const collectionCases = [
  'active_gather_label_saved',
  'active_gather_copy_saved',
  'active_gather_template_created',
  'active_unused_gather_deleted',
  'active_chores_saved',
];
it.each(collectionCases)(
  'F6-14b Collection active write sends exactly profile_patch_fenced.%s',
  async (name) => {
    const page = await collectionPage(name);
    await page.send();
    expect(JSON.parse(required(page.fake.writes()[0]?.body))).toEqual(
      fencedCase(name).request.body,
    );
    expect(own(JSON.parse(required(page.fake.writes()[0]?.body)), 'expected_context')).toEqual(
      fencedRuntime,
    );
    if (name.endsWith('deleted'))
      expect(JSON.parse(required(page.fake.writes()[0]?.body))).not.toHaveProperty('value');
    const saved = fencedCase(name);
    page.source(fencedCase(`read_after_${name}`));
    page.fake.next(PATCH).respond(saved.status, saved.body);
    await page.advance(1_000);
    expect(page.view.container.querySelector('[data-outcome="saved"]')).toBeVisible();
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
  },
);
it('F6-14b Collection context_changed keeps the draft, shows current context and sends nothing more', async () => {
  const page = await collectionPage('active_gather_label_saved');
  await page.send();
  // Modified recorded reply: the shared context fence precedes validation for every patch path.
  page.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  await page.advance(1_000);
  expect(page.view.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(page.view.getByText(/"account": "beta"/u)).toHaveTextContent('theirs');
  expect(page.view.getByRole('textbox', { name: 'Name shown' })).toHaveValue('Offline');
  const draft = page.data.store.getSnapshot().runDrafts;
  // The account can return while another edit produces the same value. A later positive
  // read must never turn this definite context refusal into a saved request or clear its draft.
  page.source(fencedCase('read_after_active_gather_label_saved'));
  page.data.commands.checkCollection();
  await page.advance(1_000);
  expect(page.view.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(page.data.store.getSnapshot().runDrafts).toEqual(draft);
  expect(
    page.data.machine.resend(
      required(page.data.machine.snapshot().find((entry) => entry.kind === 'profile_patch')).id,
    ),
  ).toBe('not_resendable');
  await page.advance(60_000);
  expect(page.fake.writes()).toHaveLength(1);
});
it.each(collectionCases)(
  'F6-14b Collection read_after_%s settles an Unknown without resending',
  async (name) => {
    const page = await collectionPage(name);
    await page.send();
    page.fake.next(PATCH).lose();
    await page.advance(1_000);
    expect(page.view.container.querySelector('[data-outcome="unknown"]')).toBeVisible();
    page.source(fencedCase(`read_after_${name}`));
    page.data.commands.checkCollection();
    await page.advance(1_000);
    expect(page.view.container.querySelector('[data-outcome="applied"]')).toBeVisible();
    expect(page.fake.writes()).toHaveLength(1);
  },
);
async function runsPage(name: string) {
  const page = await dashboardPage(name, '#runs/saved/farm');
  const field = name === 'active_run_entry_label_saved' ? 'label' : 'perk_bans';
  const row = within(
    required(page.view.container.querySelector<HTMLElement>(`[data-entry-field="${field}"]`)),
  );
  const send = async () => {
    if (field === 'label') {
      await page.user.clear(row.getByLabelText('Run label', { exact: true }));
      await page.user.type(row.getByLabelText('Run label', { exact: true }), 'Offline farm');
    } else await page.user.click(row.getByLabelText('Leave the game\u2019s perk bans alone'));
    await page.user.click(row.getByRole('button', { name: 'Save field' }));
    await page.advance();
  };
  return { ...page, send, row };
}
const runsCases = ['active_run_entry_label_saved', 'active_run_entry_field_deleted'];
it.each(runsCases)(
  'F6-14b Runs entry active write sends exactly profile_patch_fenced.%s',
  async (name) => {
    const page = await runsPage(name);
    await page.send();
    expect(JSON.parse(required(page.fake.writes()[0]?.body))).toEqual(
      fencedCase(name).request.body,
    );
    expect(own(JSON.parse(required(page.fake.writes()[0]?.body)), 'expected_context')).toEqual(
      fencedRuntime,
    );
    if (name.endsWith('deleted'))
      expect(JSON.parse(required(page.fake.writes()[0]?.body))).not.toHaveProperty('value');
    const saved = fencedCase(name);
    page.source(fencedCase(`read_after_${name}`));
    page.fake.next(PATCH).respond(saved.status, saved.body);
    await page.advance(1_000);
    expect(page.view.container.querySelector('[data-outcome="saved"]')).toBeVisible();
  },
);
it('F6-14b Runs entry context_changed keeps the draft, shows current context and sends nothing more', async () => {
  const page = await runsPage('active_run_entry_label_saved');
  await page.send();
  // Modified recorded reply: the shared context fence precedes validation for every patch path.
  page.fake.next(PATCH).respond(409, fencedCase('active_patch_refused_after_account_switch').body);
  await page.advance(1_000);
  expect(page.row.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(page.row.getByText(/"account": "beta"/u)).toHaveTextContent('theirs');
  expect(page.row.getByLabelText('Run label', { exact: true })).toHaveValue('Offline farm');
  const draft = page.data.store.getSnapshot().runDrafts;
  // A definite context refusal stays refused even if the original account returns and a
  // later source happens to contain the proposed value from a different writer.
  page.source(fencedCase('read_after_active_run_entry_label_saved'));
  page.data.commands.checkRunEntry();
  await page.advance(1_000);
  expect(page.row.getByText('Not saved: the account or profile changed')).toBeVisible();
  expect(page.data.store.getSnapshot().runDrafts).toEqual(draft);
  expect(
    page.data.machine.resend(
      required(page.data.machine.snapshot().find((entry) => entry.kind === 'profile_patch')).id,
    ),
  ).toBe('not_resendable');
  await page.advance(60_000);
  expect(page.fake.writes()).toHaveLength(1);
});
it.each(runsCases)(
  'F6-14b Runs entry read_after_%s settles an Unknown without resending',
  async (name) => {
    const page = await runsPage(name);
    await page.send();
    page.fake.next(PATCH).lose();
    await page.advance(1_000);
    expect(page.view.container.querySelector('[data-outcome="unknown"]')).toBeVisible();
    page.source(fencedCase(`read_after_${name}`));
    page.data.commands.checkRunEntry();
    await page.advance(1_000);
    expect(page.view.container.querySelector('[data-outcome="applied"]')).toBeVisible();
    expect(page.fake.writes()).toHaveLength(1);
  },
);

// Ported from the independent review's held-answer reproductions. Each read contains
// the submitted value from a different writer before the original request answers.
const lateScenarios = [
  'refusal',
  'revision conflict',
  'validation',
  'newer typing',
  'success',
  'newer success',
] as const;
function lateRefusal(scenario: string) {
  return scenario === 'revision conflict'
    ? dashboardPagesCase('profile_patch.stale_revision_409')
    : scenario === 'validation'
      ? dashboardPagesCase('profile_patch.count_zero')
      : fencedCase('active_patch_refused_after_account_switch');
}
it.each(
  ['active_plan_saved', 'active_plan_deleted'].flatMap((name) =>
    lateScenarios.map((scenario) => ({ name, scenario })),
  ),
)(
  'F6-14b R2-01 Schedule $name matching read then late $scenario preserves recovery',
  async ({ name, scenario }) => {
    const s = await schedulePage(name);
    if (name.endsWith('saved')) await change(s.draft);
    const original = s.entry().draft;
    const revision = s.entry().baseRevision;
    await change(() =>
      name.endsWith('saved') ? s.schedule.save() : s.schedule.removePlan(required(revision)),
    );
    await change(() => vi.advanceTimersByTimeAsync(16_000));
    s.source(fencedCase('read_after_' + name));
    await change(s.schedule.refresh);
    // Reload before the answer: the original revision and raw proposal must be durable.
    const stored = required(
      Object.entries(s.storage.entries()).find(([key]) =>
        key.startsWith('tp.schedule.draft:'),
      )?.[1],
    );
    expect(own(JSON.parse(stored), 'draft')).toEqual(original);
    expect(own(JSON.parse(stored), 'revision')).toBe(revision);
    if (scenario.startsWith('newer'))
      await change(() => {
        s.schedule.mapWeek('monday', 'newer');
      });
    const expected = scenario.startsWith('newer') ? s.entry().draft : original;
    if (scenario.endsWith('success')) {
      await change(() => {
        s.fake.next(PATCH).respond(fencedCase(name).status, fencedCase(name).body);
      });
      expect(s.entry().dirty).toBe(scenario.startsWith('newer'));
      expect(
        own(
          JSON.parse(
            required(
              s.storage.getItem(
                Object.keys(s.storage.entries()).find((key) =>
                  key.startsWith('tp.schedule.draft:'),
                ) ?? '',
              ),
            ),
          ),
          'draft',
        ),
      ).toEqual(scenario.startsWith('newer') ? expected : undefined);
    } else {
      await change(() => {
        s.fake.next(PATCH).respond(lateRefusal(scenario).status, lateRefusal(scenario).body);
      });
      expect(s.entry().draft).toEqual(expected);
      expect(s.entry().dirty).toBe(true);
      expect(s.entry().baseRevision).toBe(revision);
      s.view.view.unmount();
      s.schedule.dispose();
      const again = await schedulePage(name, s.storage);
      expect(again.entry().intent?.phase).toBe('refused');
      expect(again.entry().draft).toEqual(expected);
      expect(again.entry().baseRevision).toBe(revision);
      expect(again.entry().dirty).toBe(true);
      expect(again.fake.writes()).toHaveLength(0);
    }
  },
);
const latePages = [
  { surface: 'Collection', name: 'active_gather_label_saved', hash: '#collection/policy/all_on' },
  { surface: 'Collection', name: 'active_unused_gather_deleted', hash: '#collection/policy/spare' },
  { surface: 'Runs entry', name: 'active_run_entry_label_saved', hash: '#runs/saved/farm' },
  { surface: 'Runs entry', name: 'active_run_entry_field_deleted', hash: '#runs/saved/farm' },
];
it.each(latePages.flatMap((page) => lateScenarios.map((scenario) => ({ ...page, scenario }))))(
  'F6-14b R2-02 $surface $name matching read then late $scenario preserves recovery',
  async ({ surface, name, hash, scenario }) => {
    const page = surface === 'Collection' ? await collectionPage(name) : await runsPage(name);
    if (name === 'active_unused_gather_deleted') {
      // A deletion also owns any raw values already entered in that editor.
      await page.user.type(page.view.getByRole('textbox', { name: 'Name shown' }), 'Unsent label');
    }
    await page.send();
    const before = page.data.store.getSnapshot().runDrafts;
    expect(Object.keys(before).length).toBeGreaterThan(0);
    await page.advance(16_000);
    page.source(fencedCase('read_after_' + name));
    if (surface === 'Collection') page.data.commands.checkCollection();
    else page.data.commands.checkRunEntry();
    await page.advance(1_000);
    expect(page.data.store.getSnapshot().runDrafts).toEqual(before);
    if (scenario.startsWith('newer')) {
      const scope = required(Object.keys(before)[0]);
      const path = required(
        Object.keys(required(before[scope])).find((key) => key !== 'base_revision'),
      );
      page.data.store.dispatch({ type: 'draft_edit', scope, path, value: 'newer typing' });
    }
    const expected = page.data.store.getSnapshot().runDrafts;
    if (scenario.endsWith('success')) {
      page.fake.next(PATCH).respond(fencedCase(name).status, fencedCase(name).body);
      await page.advance(1_000);
      const remaining = page.data.store.getSnapshot().runDrafts;
      if (scenario.startsWith('newer')) {
        for (const [scope, values] of Object.entries(expected))
          for (const [path, value] of Object.entries(values))
            if (path !== 'base_revision') expect(remaining[scope]?.[path]).toBe(value);
      } else expect(remaining).toEqual(name === 'active_unused_gather_deleted' ? before : {});
    } else {
      page.fake.next(PATCH).respond(lateRefusal(scenario).status, lateRefusal(scenario).body);
      await page.advance(1_000);
      expect(page.data.store.getSnapshot().runDrafts).toEqual(expected);
      page.view.unmount();
      page.data.stop();
      const again = await dashboardPage(name, hash, page.storage);
      expect(
        again.data.machine.snapshot().find((item) => item.kind === 'profile_patch')?.phase,
      ).toBe('refused');
      expect(again.data.store.getSnapshot().runDrafts).toEqual(expected);
      expect(again.fake.writes()).toHaveLength(0);
    }
  },
);
it.each(abilitiesCases.flatMap((name) => lateScenarios.map((scenario) => ({ name, scenario }))))(
  'F6-14b R2-03 Abilities $name matching read then late $scenario preserves recovery',
  async ({ name, scenario }) => {
    const s = await abilitiesPage(name);
    await s.send();
    const original = s.entry().node;
    const revision = s.entry().revision;
    await change(() => vi.advanceTimersByTimeAsync(16_000));
    s.source(fencedCase('read_after_' + name));
    await change(s.store.refresh);
    expect(s.entry().revision).toBe(revision);
    expect(s.entry().node).toEqual(original);
    if (scenario.startsWith('newer'))
      await change(() => {
        s.store.edit([], abilityNode({ baseline: { newer: true } }));
      });
    const expected = s.entry().node;
    if (scenario.endsWith('success')) {
      await change(() => {
        s.fake.next(PATCH).respond(fencedCase(name).status, fencedCase(name).body);
      });
      expect(s.entry().outcome).toBe('saved');
      expect(s.entry().dirty).toBe(scenario.startsWith('newer'));
      if (name.endsWith('deleted') && scenario.startsWith('newer'))
        expect(s.entry().submission?.node).toBeUndefined();
      else expect(s.entry().submission).toBeNull();
    } else {
      await change(() => {
        s.fake.next(PATCH).respond(lateRefusal(scenario).status, lateRefusal(scenario).body);
      });
      expect(s.entry().node).toEqual(expected);
      expect(s.entry().dirty).toBe(true);
      expect(s.entry().revision).toBe(revision);
      s.view.view.unmount();
      s.store.dispose();
      const again = await abilitiesPage(name, s.storage);
      expect(again.entry().node).toEqual(expected);
      expect(again.entry().dirty).toBe(true);
      expect(again.entry().revision).toBe(revision);
      expect(again.fake.writes()).toHaveLength(0);
    }
  },
);

it.each(['active_plan_saved', 'active_plan_deleted'])(
  'F6-14b R2-01 Schedule store %s reload before answer recovers raw draft and closes late interval',
  async (name) => {
    const s = transport(name);
    const storage = createMemoryStorage();
    const store = createScheduleStore(createScheduleApi(s.fake.transport, storage, 50));
    cleanups.push(store.dispose);
    await change(() => {
      store.setTarget(fencedRuntime, 1);
    });
    if (name.endsWith('saved')) {
      store.update((state) => ({
        ...state,
        entries: {
          ...state.entries,
          [required(state.key)]: {
            ...required(currentSchedule(state)),
            draft: scheduleDraft({ plan: own(fencedCase(name).request.body, 'value') }),
            dirty: true,
            version: 1,
          },
        },
      }));
    }
    const entry = required(currentSchedule(store.getSnapshot()));
    await change(() =>
      name.endsWith('saved') ? store.save() : store.removePlan(required(entry.baseRevision)),
    );
    await change(() => vi.advanceTimersByTimeAsync(51));
    s.source(fencedCase('read_after_' + name));
    await change(store.refresh);
    expect(required(currentSchedule(store.getSnapshot())).baseRevision).toBe(entry.baseRevision);
    const raw = required(
      Object.entries(storage.entries()).find(([key]) => key.startsWith('tp.schedule.draft:'))?.[1],
    );
    expect(own(JSON.parse(raw), 'draft')).toEqual(entry.draft);
    store.dispose();
    // Recreate the store before its automatic read: restoration starts with the raw copy.
    const again = createScheduleStore(createScheduleApi(s.fake.transport, storage, 50));
    cleanups.push(again.dispose);
    again.setTarget(fencedRuntime, 1);
    expect(required(currentSchedule(again.getSnapshot())).draft).toEqual(entry.draft);
    expect(required(currentSchedule(again.getSnapshot())).baseRevision).toBe(entry.baseRevision);
    await change(() => vi.advanceTimersByTimeAsync(0));
    expect(required(currentSchedule(again.getSnapshot())).dirty).toBe(false);
    expect(required(currentSchedule(again.getSnapshot())).recovery).toBeUndefined();
    expect(s.fake.writes()).toHaveLength(1);
  },
);
it.each(latePages)(
  'F6-14b R2-02 $surface $name reload before answer closes late interval without another write',
  async ({ surface, name, hash }) => {
    const page = surface === 'Collection' ? await collectionPage(name) : await runsPage(name);
    if (name === 'active_unused_gather_deleted')
      await page.user.type(page.view.getByRole('textbox', { name: 'Name shown' }), 'Unsent label');
    await page.send();
    const draft = page.data.store.getSnapshot().runDrafts;
    await page.advance(16_000);
    page.source(fencedCase('read_after_' + name));
    if (surface === 'Collection') page.data.commands.checkCollection();
    else page.data.commands.checkRunEntry();
    await page.advance(1_000);
    expect(page.data.store.getSnapshot().runDrafts).toEqual(draft);
    page.view.unmount();
    page.data.stop();
    const again = await dashboardPage(name, hash, page.storage);
    // Reload ends this browser's chance of receiving the answer. The retained positive
    // read may now release submitted values, but unrelated label input on Delete stays.
    expect(again.data.store.getSnapshot().runDrafts).toEqual(
      name === 'active_unused_gather_deleted' ? draft : {},
    );
    expect(again.fake.writes()).toHaveLength(0);
    expect(
      Object.keys(required(again.storage.entries?.())).some((key) =>
        key.startsWith('tp.intents.v1:'),
      ),
    ).toBe(false);
  },
);

it.each(abilitiesCases)(
  'F6-14b R2-03 Abilities %s reload before answer keeps raw draft then releases final read copy',
  async (name) => {
    const s = await abilitiesPage(name);
    await s.send();
    const node = s.entry().node;
    const revision = s.entry().revision;
    await change(() => vi.advanceTimersByTimeAsync(16_000));
    s.source(fencedCase('read_after_' + name));
    await change(s.store.refresh);
    expect(s.entry().revision).toBe(revision);
    expect(s.entry().submission?.node).toEqual(node);
    s.view.view.unmount();
    s.store.dispose();
    const again = await abilitiesPage(name, s.storage);
    expect(again.entry().node).toEqual(node);
    expect(again.entry().revision).toBe(revision);
    again.source(fencedCase('read_after_' + name));
    await change(again.store.refresh);
    expect(again.entry().submission).toBeNull();
    expect(again.entry().dirty).toBe(false);
    expect(again.fake.writes()).toHaveLength(0);
  },
);
