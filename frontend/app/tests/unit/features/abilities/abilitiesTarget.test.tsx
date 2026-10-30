import { act, screen, waitFor } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, expect, it } from 'vitest';
import { createAbilitiesApi } from '../../../../src/api/abilities.ts';
import { contextFromConfig } from '../../../../src/api/context.ts';
import { editingTarget, editTargetScope } from '../../../../src/core/editingTarget.ts';
import { profileFilesView } from '../../../../src/core/profiles.ts';
import { own, ownPath, record } from '../../../../src/core/read.ts';
import {
  createAbilitiesStore,
  currentAbilities,
  currentAbility,
  canSaveAbility,
} from '../../../../src/store/abilities.ts';
import { abilityNode, abilityValue, nodeAt } from '../../../../src/store/abilitiesDraft.ts';
import { AbilitiesPage } from '../../../../src/features/abilities/AbilitiesPage.tsx';
import { initialDashboardState } from '../../../../src/store/dashboardStore.ts';
import { renderRegion } from '../dashboard/harness.tsx';
import { createFakeTransport, createMemoryStorage } from '../../../support/fakeTransport.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../../scripts/contract-fixture.mjs';
import type { DashboardCase } from '../../../support/dashboardFixture.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';

const cases = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');
function fixture(ref: string): DashboardCase {
  const [family = '', name = ''] = ref.split('.');
  return structuredClone(ownPath(cases, family, name)) as DashboardCase;
}
const pt = (name: string) => fixture(`profile_target.${name}`);
const cleanups: (() => void)[] = [];
afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
});
async function change(fn: () => void | Promise<void>) {
  await act(async () => {
    await fn();
  });
}
async function setup(storage = createMemoryStorage()) {
  const fake = createFakeTransport();
  let config = pt('inactive_editor_context_before_save');
  // Modified recorded case: expose the saved profile/revision preceding the UW save
  // through the source envelope, so its next request matches the recorded CAS exactly.
  let source: DashboardCase = {
    ...pt('same_account_inactive_target_source'),
    body: {
      ...record(pt('same_account_inactive_target_source').body),
      profile: own(pt('inactive_chores_saved').body, 'profile'),
      revision: own(pt('inactive_chores_saved').body, 'revision'),
    },
  };
  let answer: DashboardCase | 'hold' = pt('inactive_uw_policy_saved');
  fake.route('GET', '/api/config', () => config);
  fake.route('GET', '/api/profile-files', () => pt('same_account_inactive_target_listed'));
  fake.route('GET', /\/api\/profile-src\//, (request) =>
    request.path.endsWith('/mine')
      ? fixture('ability_policies.read_before_the_first_save')
      : source,
  );
  fake.route('GET', '/api/vocab', () => fixture('profile_reads.vocab_ok'));
  fake.route('GET', '/api/settings', () => fixture('settings.populated_account_every_module'));
  fake.route('POST', '/api/profile-patch', () => answer);
  const store = createAbilitiesStore(createAbilitiesApi(fake.transport, storage, 100));
  cleanups.push(store.dispose);
  const context = contextFromConfig(config.body);
  if (context === null) throw new Error('Recorded context missing');
  const target = editingTarget(
    context,
    { name: 'second', generation: 1 },
    profileFilesView(pt('same_account_inactive_target_listed').body),
  );
  await change(() => {
    store.setTarget(context, 1, target);
  });
  await waitFor(() => {
    expect(currentAbilities(store.getSnapshot())?.busy).toBe(false);
  });
  return {
    fake,
    store,
    context,
    target,
    storage,
    source: (value: DashboardCase) => {
      source = value;
    },
    config: (value: DashboardCase) => {
      config = value;
    },
    answer: (value: DashboardCase | 'hold') => {
      answer = value;
    },
  };
}

it('PE5 inactive UW and rescue creations send exactly the recorded fenced bodies', async () => {
  const s = await setup();
  expect(s.store.getSnapshot().key).toBe(editTargetScope(s.context, 'second'));
  for (const [family, id, ref] of [
    ['uw_policies', 'calm', 'inactive_uw_policy_saved'],
    ['rescue_policies', 'extra', 'inactive_rescue_policy_saved'],
  ] as const) {
    const recorded = pt(ref);
    s.answer(recorded);
    await change(() => {
      s.store.create(family, id, false);
      s.store.edit([], abilityNode(own(recorded.request.body, 'value')));
      s.store.save();
    });
    await waitFor(() => {
      expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes().at(-1)?.body ?? '')).toEqual(recorded.request.body);
    // Modified source envelope: saved patch data read back under its original target name.
    s.source({
      ...pt('same_account_inactive_target_source'),
      body: { ...record(pt('same_account_inactive_target_source').body), ...record(recorded.body) },
    });
    await change(s.store.refresh);
  }
  expect(s.fake.writes()).toHaveLength(2);
});

it('PE5 inactive policies read without active settings and keep Demon Mode disabled with its active label', async () => {
  const s = await setup();
  s.fake.route('GET', '/api/settings', () => 'hold'); // Active settings never answer; inactive reads must not request them.
  await change(s.store.refresh);
  await change(() => {
    s.store.edit(['label'], abilityNode('Target draft'));
  });
  expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(true);
  const mounted = renderRegion(initialDashboardState(), <AbilitiesPage abilities={s.store} />);
  expect(screen.getByRole('button', { name: 'Global Demon Mode timing' })).toBeDisabled();
  expect(screen.getByText('Return editing to the active profile to change these')).toBeVisible();
  expect(screen.getByTestId('abilities-active-only')).toHaveTextContent('Active profile:mine');
  expect(await axe(mounted.view.container)).toHaveNoViolations();
  expect(textOutsideCatalogue(mounted.view.container)).toEqual([]);
  expect(s.fake.requests.some((request) => request.path === '/api/settings')).toBe(false);
});

it('PE5 click-time context change keeps the draft and sends nothing', async () => {
  const s = await setup();
  s.config(pt('read_config_after_editor_serial_change'));
  await change(() => {
    s.store.edit(['label'], abilityNode('Retained'));
    s.store.save();
  });
  await waitFor(() => {
    expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('notSent');
  });
  expect(s.fake.writes()).toHaveLength(0);
  expect(
    own(abilityValue(currentAbility(s.store.getSnapshot())?.node ?? abilityNode(null)), 'label'),
  ).toBe('Retained');
});

it('PE5 switching targets restores only its own raw draft and new policy ID', async () => {
  const s = await setup();
  await change(() => {
    s.store.edit(['chain_lightning', 'pre_mark_waves', 0], { kind: 'number', raw: 'invalid' });
    s.store.newId('uw_policies', 'second_draft');
  });
  await change(() => {
    s.store.setTarget(s.context, 1, editingTarget(s.context, undefined, null));
  });
  await waitFor(() => {
    expect(currentAbilities(s.store.getSnapshot())?.busy).toBe(false);
  });
  await change(() => {
    s.store.setTarget(s.context, 1, { ...s.target, generation: 3 });
  });
  await waitFor(() => {
    expect(currentAbilities(s.store.getSnapshot())?.busy).toBe(false);
  });
  expect(
    nodeAt(currentAbility(s.store.getSnapshot())?.node ?? abilityNode(null), [
      'chain_lightning',
      'pre_mark_waves',
      0,
    ]),
  ).toEqual({ kind: 'number', raw: 'invalid' });
  expect(currentAbilities(s.store.getSnapshot())?.newIds.uw_policies).toBe('second_draft');
});

it('PE5 a target source remains editable when runtime has no active profile', async () => {
  const s = await setup();
  s.config(pt('read_after_unbind_account_context'));
  const runtime = contextFromConfig(pt('read_after_unbind_account_context').body);
  if (runtime === null) throw new Error('Missing unbound runtime');
  await change(() => {
    s.store.setTarget(runtime, 2, { ...s.target, active: null, generation: 2 });
  });
  await waitFor(() => {
    expect(currentAbilities(s.store.getSnapshot())?.busy).toBe(false);
  });
  await change(() => {
    s.store.edit(['label'], abilityNode('Unbound target draft'));
  });
  expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(true);
  expect(s.fake.writes()).toHaveLength(0);
});

it('PE5 a changed target generation during the click check never sends or transfers the draft', async () => {
  const s = await setup();
  s.fake.route('GET', '/api/profile-files', () => 'hold');
  await change(() => {
    s.store.edit(['label'], abilityNode('Second only'));
    s.store.save();
  });
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('checking');
  await change(() => {
    s.store.setTarget(s.context, 1, { ...s.target, generation: 2 });
  });
  await change(() => {
    const held = s.fake.next('/api/profile-files');
    held.respond(200, pt('same_account_inactive_target_listed').body);
  });
  expect(s.fake.writes()).toHaveLength(0);
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('notSent');
});

it('PE5 an older click check cannot clear a newer save after leaving and returning to the target', async () => {
  const s = await setup();
  s.fake.route('GET', '/api/profile-files', () => 'hold');
  s.answer('hold');
  await change(() => {
    s.store.edit(['label'], abilityNode('First click'));
    s.store.save();
  });
  const older = s.fake.next('/api/profile-files');
  await change(() => {
    s.store.setTarget(s.context, 1, { ...s.target, generation: 2 });
  });
  await waitFor(() => {
    expect(currentAbilities(s.store.getSnapshot())?.busy).toBe(false);
  });
  await change(() => {
    s.store.edit(['label'], abilityNode('New click'));
    s.store.save();
  });
  const newer = s.fake.last('/api/profile-files');
  await change(() => {
    newer.respond(200, pt('same_account_inactive_target_listed').body);
  });
  await waitFor(() => {
    expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('sending');
  });
  await change(() => {
    older.respond(200, pt('same_account_inactive_target_listed').body);
  });
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('sending');
  expect(s.fake.writes()).toHaveLength(1);
});

it('PE5 Unknown settles only from a later source read naming the sent target and holding its policy', async () => {
  const s = await setup();
  s.answer('hold');
  const saved = pt('inactive_uw_policy_saved');
  await change(() => {
    s.store.create('uw_policies', 'calm', false);
    s.store.edit([], abilityNode(own(saved.request.body, 'value')));
    s.store.save();
  });
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  await change(() => {
    s.fake.next('/api/profile-patch').lose();
  });
  await waitFor(() => {
    expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('unknown');
  });
  // Modified source: the same policy under another name cannot settle this submission.
  s.source({
    ...pt('same_account_inactive_target_source'),
    body: {
      ...record(pt('same_account_inactive_target_source').body),
      ...record(saved.body),
      name: 'mine',
    },
  });
  await change(s.store.refresh);
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('unknown');
  // Modified source envelope: the recorded saved data read later under the correct target.
  s.source({
    ...pt('same_account_inactive_target_source'),
    body: { ...record(pt('same_account_inactive_target_source').body), ...record(saved.body) },
  });
  await change(s.store.refresh);
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('readApplied');
  expect(s.fake.writes()).toHaveLength(1);
});

it('PE5 F6-14c unused UW and rescue delete exact fenced recordings and read back absent', async () => {
  const s = await setup();
  s.source(fixture('profile_target_more.read_before_the_inactive_policies_are_deleted'));
  await change(s.store.refresh);
  for (const [key, name] of [
    ['uw_policies/calm', 'inactive_unused_uw_policy_deleted'],
    ['rescue_policies/extra', 'inactive_unused_rescue_policy_deleted'],
  ]) {
    const answer = fixture(`profile_target_more.${name}`);
    s.answer(answer);
    await change(() => {
      s.store.select(key ?? '');
      s.store.save(true);
    });
    await waitFor(() => {
      expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes().at(-1)?.body ?? '')).toEqual(answer.request.body);
    // Read the exact patch result through the existing source envelope before the next edit.
    const envelope = fixture('profile_target_more.read_before_the_inactive_policies_are_deleted');
    s.source({ ...envelope, body: { ...record(envelope.body), ...record(answer.body) } });
    await change(s.store.refresh);
  }
  s.source(fixture('profile_target_more.read_after_the_unused_policies_were_deleted'));
  await change(s.store.refresh);
  expect(
    ownPath(
      currentAbilities(s.store.getSnapshot())?.source,
      'profile',
      'policies',
      'uw_policies',
      'calm',
    ),
  ).toBeUndefined();
  expect(
    ownPath(
      currentAbilities(s.store.getSnapshot())?.source,
      'profile',
      'policies',
      'rescue_policies',
      'extra',
    ),
  ).toBeUndefined();
});

it.each([
  'inactive_target_source_that_cannot_be_parsed_400',
  'inactive_target_source_that_is_not_a_mapping_400',
])('PE5 F6-14c %s shows the server error and blocks writes', async (name) => {
  const s = await setup();
  const failed = fixture(`profile_target_more.${name}`);
  s.source(failed);
  await change(s.store.refresh);
  renderRegion(initialDashboardState(), <AbilitiesPage abilities={s.store} />);
  expect(
    screen.getByText(String(own(failed.body, 'error')), { normalizer: (value) => value }),
  ).toBeVisible();
  expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
  expect(s.fake.writes()).toEqual([]);
});

it('PE5 F6-14c rescue rule order saves the recorded whole policy; subpath documents the same server rule', async () => {
  const s = await setup();
  s.source(fixture('profile_target_more.read_after_the_unused_policies_were_deleted'));
  await change(s.store.refresh);
  const saved = fixture(
    'profile_target_more.inactive_rescue_policy_saved_with_its_rules_in_another_order',
  );
  s.answer(saved);
  await change(() => {
    s.store.select('rescue_policies/wall_drain');
    s.store.rule('up', 2);
    s.store.rule('up', 1);
    s.store.save();
  });
  await waitFor(() => {
    expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('saved');
  });
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(saved.request.body);
  for (const name of [
    'read_after_the_inactive_rescue_rules_were_reordered_as_a_whole_policy',
    'read_after_the_inactive_rescue_rules_were_reordered_by_the_subpath',
  ]) {
    s.source(fixture(`profile_target_more.${name}`));
    await change(s.store.refresh);
    expect(currentAbilities(s.store.getSnapshot())?.source).toEqual(
      fixture(`profile_target_more.${name}`).body,
    );
  }
  expect(
    ownPath(
      fixture('profile_target_more.inactive_rescue_rules_reordered_by_the_rules_subpath').body,
      'profile',
    ),
  ).toEqual(
    own(
      fixture(
        'profile_target_more.read_after_the_inactive_rescue_rules_were_reordered_by_the_subpath',
      ).body,
      'profile',
    ),
  );
});

it.each(['uw', 'rescue'])(
  'PE5 F6-14c %s in-use delete refuses the exact inactive request and retains the policy',
  async (family) => {
    const s = await setup();
    const before = fixture('profile_target_more.read_before_the_inactive_policies_are_deleted');
    // Modified recording: hide consumers to model one added after this read; the unchanged
    // inactive server refusal is authoritative and the request still matches it exactly.
    const profile = record(own(before.body, 'profile'));
    if (profile !== undefined) Object.assign(profile, { blueprints: {} });
    s.source(before);
    await change(s.store.refresh);
    const answer = fixture(
      `profile_target_more.inactive_${family}_policy_deleted_while_a_run_uses_it_is_refused`,
    );
    s.answer(answer);
    await change(() => {
      s.store.select(family === 'uw' ? 'uw_policies/farm_cl' : 'rescue_policies/wall_drain');
      s.store.save(true);
    });
    await waitFor(() => {
      expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('refused');
    });
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(answer.request.body);
    renderRegion(initialDashboardState(), <AbilitiesPage abilities={s.store} />);
    expect(screen.getByRole('link', { name: String(own(answer.body, 'error')) })).toBeVisible();
    s.source(
      fixture('profile_target_more.read_after_the_in_use_deletes_were_refused_nothing_changed'),
    );
    await change(s.store.refresh);
    expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('refused');
    s.store.dispose();
    const restored = await setup(s.storage);
    expect(own(currentAbility(restored.store.getSnapshot())?.detail, 'error')).toBe(
      own(answer.body, 'error'),
    );
    expect(currentAbility(restored.store.getSnapshot())?.outcome).toBe('refused');
    expect(restored.fake.writes()).toEqual([]);
  },
);

it('PE5 refreshing during an inactive delete check preserves the pending request and blocks another save', async () => {
  const s = await setup();
  // Modified source envelope: the unused policy exists after the recorded create.
  s.source({
    ...pt('same_account_inactive_target_source'),
    body: {
      ...record(pt('same_account_inactive_target_source').body),
      ...record(pt('inactive_uw_policy_saved').body),
    },
  });
  await change(s.store.refresh);
  s.fake.route('GET', '/api/profile-files', () => 'hold');
  s.answer('hold');
  await change(() => {
    s.store.select('uw_policies/calm');
    s.store.save(true);
  });
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('checking');
  await change(s.store.refresh);
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('checking');
  expect(canSaveAbility(s.store.getSnapshot(), Date.now(), true)).toBe(false);
  expect(s.fake.writes()).toHaveLength(0);
  await change(() => {
    s.store.save(true);
  });
  expect(s.fake.held.filter((held) => held.request.path === '/api/profile-files')).toHaveLength(1);
  await change(() => {
    s.fake.next('/api/profile-files').respond(200, pt('same_account_inactive_target_listed').body);
  });
  expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('sending');
  expect(s.fake.writes()).toHaveLength(1);
});

it('PE5 inactive rescue ordering uses the recorded rule order in a whole target policy patch', async () => {
  const s = await setup();
  s.answer('hold');
  const ordered = fixture('ability_policies.rescue_rules_reordered').request.body;
  const source = pt('inactive_chores_saved').body;
  const before = ownPath(source, 'profile', 'policies', 'rescue_policies', 'wall_drain');
  await change(() => {
    s.store.select('rescue_policies/wall_drain');
    s.store.rule('up', 2);
    s.store.rule('up', 1);
    s.store.save();
  });
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  // No exact inactive order recording: transport the active case's rule order
  // using the existing Abilities whole-policy contract and inactive save fence.
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual({
    ...record(pt('inactive_rescue_policy_saved').request.body),
    expected_revision: own(source, 'revision'),
    path: ['policies', 'rescue_policies', 'wall_drain'],
    value: {
      ...Object.fromEntries(Object.entries(before as Record<string, unknown>)),
      rules: own(ordered, 'value'),
    },
  });
});

it.each([
  ['context_unconfirmed', '/api/config', fixture('config_state.file_corrupt')],
  // Modified recorded file-list read: fail its HTTP status to cover unreadable list evidence.
  [
    'target_unconfirmed',
    '/api/profile-files',
    { ...pt('same_account_inactive_target_listed'), status: 500 },
  ],
  ['target_missing', '/api/profile-files', pt('inactive_target_list_after_deletion')],
] as const)('PE5 click check %s keeps the draft without a POST', async (reason, path, reply) => {
  const s = await setup();
  // Modified recorded failures: make one independent click-time read unavailable.
  s.fake.route('GET', path, () => reply);
  await change(() => {
    s.store.edit(['label'], abilityNode('Kept'));
    s.store.save();
  });
  await waitFor(() => {
    expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('notSent');
  });
  expect(currentAbility(s.store.getSnapshot())?.blockedReason).toBe(reason);
  expect(s.fake.writes()).toHaveLength(0);
});

it('PE5 an in-use deletion refused by the server renders its exact problem as server text', async () => {
  const s = await setup();
  const original = pt('inactive_chores_saved').body;
  const profile = record(own(original, 'profile'));
  // Modified recorded source: simulate a local read before the consumer was added.
  // The refusal is the recorded server answer after that saved run uses farm_cl.
  s.source({
    ...pt('same_account_inactive_target_source'),
    body: {
      ...record(pt('same_account_inactive_target_source').body),
      ...record(original),
      profile: { ...profile, blueprints: {} },
    },
  });
  await change(s.store.refresh);
  const refused = fixture('ability_policies.uw_policy_deleted_while_a_run_uses_it');
  s.answer(refused);
  await change(() => {
    s.store.select('uw_policies/farm_cl');
    s.store.save(true);
  });
  await waitFor(() => {
    expect(currentAbility(s.store.getSnapshot())?.outcome).toBe('refused');
  });
  const mounted = renderRegion(initialDashboardState(), <AbilitiesPage abilities={s.store} />);
  const problem = String(own(refused.body, 'error'));
  expect(screen.getByRole('link', { name: problem })).toHaveTextContent(problem);
  expect(
    screen.getByRole('link', { name: problem }).querySelector('[data-server-text]'),
  ).not.toBeNull();
  expect(textOutsideCatalogue(mounted.view.container)).toEqual([]);
});
