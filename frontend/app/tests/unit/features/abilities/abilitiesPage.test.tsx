import { assertProfilePatchContexts } from '../../../support/profilePatchAssertions.ts';
import { withRecordedFence } from '../../../support/profilePatchFenced.ts';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, describe, expect, it } from 'vitest';
import { createAbilitiesApi } from '../../../../src/api/abilities.ts';
import { contextFromConfig } from '../../../../src/api/context.ts';
import { entries, own, ownPath } from '../../../../src/core/read.ts';
import {
  createAbilitiesStore,
  currentAbilities,
  currentAbility,
  canSaveAbility,
  canReconcileAbility,
  abilityKey,
} from '../../../../src/store/abilities.ts';
import {
  abilityNode,
  abilityValue,
  nodeAt,
  ruleSupported,
  reconcileAbilityNode,
  setAbilityNode,
} from '../../../../src/store/abilitiesDraft.ts';
import { AbilitiesPage } from '../../../../src/features/abilities/AbilitiesPage.tsx';
import { initialDashboardState } from '../../../../src/store/dashboardStore.ts';
import { renderRegion } from '../dashboard/harness.tsx';
import { createFakeTransport, createMemoryStorage } from '../../../support/fakeTransport.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../../scripts/contract-fixture.mjs';
import { ABILITIES_FIXTURE_PATH } from '../../../support/abilities-fixtures.mjs';
import type { DashboardCase } from '../../../support/dashboardFixture.ts';

const PAGES = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');
const SAMPLES = own(loadJsonWithIdentity(ABILITIES_FIXTURE_PATH).fixture, 'cases');
function fixture(ref: string): DashboardCase {
  const [family = '', name = ''] = ref.split('.');
  const raw = ownPath(family === 'abilities_samples' ? SAMPLES : PAGES, family, name);
  if (raw === undefined) throw new Error(`Missing real-route answer ${ref}`);
  const found = structuredClone(raw) as DashboardCase;
  const view =
    family === 'abilities_samples'
      ? name.replace(/_(saved|before|after|config|settings|vocab).*$/u, '')
      : 'label';
  // Context comes from the corresponding recorded config, whose body is the wire value.
  const context = contextFromConfig(
    own(own(own(SAMPLES, 'abilities_samples'), `${view}_config`), 'body'),
  );
  return context === null ? found : withRecordedFence(found, context);
}
const cleanups: (() => void)[] = [];
afterEach(() => {
  for (const fn of cleanups.splice(0)) fn();
});
async function change(fn: () => void | Promise<void>) {
  await act(async () => {
    await fn();
  });
}
async function setup(
  options: {
    view?: string;
    profile?: string;
    settings?: string;
    vocab?: string;
    save?: string;
    storage?: ReturnType<typeof createMemoryStorage>;
    mount?: boolean;
    windowMs?: number;
  } = {},
) {
  const view = options.view ?? 'label',
    prefix = `abilities_samples.${view}`,
    fake = createFakeTransport();
  let config = `${prefix}_config`,
    source = options.profile ?? `${prefix}_before`,
    settings = options.settings ?? `${prefix}_settings`,
    save = options.save ?? `${prefix}_saved`;
  fake.route('GET', '/api/config', () => fixture(config));
  fake.route('GET', /\/api\/profile-src\//, () => (source === 'hold' ? 'hold' : fixture(source)));
  fake.route('GET', '/api/settings', () => fixture(settings));
  fake.route('GET', '/api/vocab', () => fixture(options.vocab ?? `${prefix}_vocab`));
  fake.route('POST', /\/api\/(profile-patch|settings)/, () =>
    save === 'hold' ? 'hold' : fixture(save),
  );
  const storage = options.storage ?? createMemoryStorage(),
    api = createAbilitiesApi(fake.transport, storage, options.windowMs ?? 200),
    store = createAbilitiesStore(api);
  cleanups.push(store.dispose);
  cleanups.push(() => {
    assertProfilePatchContexts(api.machine);
  });
  const context = contextFromConfig(fixture(`${prefix}_config`).body);
  if (context === null) throw new Error('No fixture context');
  await change(() => {
    store.setTarget(context, 1);
  });
  await waitFor(() => {
    expect(currentAbilities(store.getSnapshot())?.busy).toBe(false);
  });
  const mounted =
    options.mount === true
      ? renderRegion(initialDashboardState(), <AbilitiesPage abilities={store} />)
      : null;
  return {
    store,
    fake,
    storage,
    context,
    mounted,
    prefix,
    config: (ref: string) => {
      config = ref;
    },
    entry: () => currentAbility(store.getSnapshot()),
    source: (ref: string) => {
      source = ref;
    },
    settings: (ref: string) => {
      settings = ref;
    },
    answer: (ref: string) => {
      save = ref;
    },
  };
}
const rows = [
  ['A2', 'label'],
  ['A2', 'create_uw'],
  ['A2', 'copy_uw'],
  ['A2', 'create_rescue'],
  ['A3', 'uw_off'],
  ['A3', 'uw_unowned'],
  ['A3', 'cl_always'],
  ['A3', 'cl_fleet'],
  ['A3', 'cl_wave'],
  ['A3', 'cl_off'],
  ['A3', 'cl_reversed'],
  ['A3', 'cl_fractional'],
  ['A3', 'cl_optional_reversed'],
  ['A3', 'cl_optional_fractional'],
  ['A4', 'arm_always'],
  ['A4', 'arm_durations'],
  ['A4', 'arm_zero'],
  ['A4', 'arm_blank'],
  ['A4', 'end_sprint'],
  ['A5', 'reorder'],
  ['A5', 'rule_delete'],
  ['A5', 'mixed_bars'],
  ['A5', 'burst_wall_options'],
  ['A5', 'burst_wrong_gate'],
  ['A5', 'repeat_fast_watch'],
  ['A5', 'action_fire_refire_guard'],
  ...['bar', 'wall_collapse', 'fleet_mark', 'wave_at_least', 'wave_between', 'second_wind'].map(
    (k) => ['A5', `trigger_${k}`],
  ),
  ...['burst', 'fire', 'toggle_uw', 'cancel_sprint'].map((k) => ['A5', `action_${k}`]),
];
describe('Abilities Wanted rows, real-route answers', () => {
  for (const [row = '', view = ''] of rows)
    it(`${row} ${view} submits whole policy and preserves backend validation`, async () => {
      const s = await setup({ view }),
        answer = fixture(`${s.prefix}_saved`),
        body = answer.request.body;
      const path = own(body, 'path');
      // Paths are arrays; fixture parameters are transported without reimplementing policy rules.
      const parts = Array.isArray(path) ? path : [],
        f = parts[1] === 'uw_policies' ? 'uw_policies' : 'rescue_policies',
        id = String(parts[2]);
      await change(() => {
        if (id === 'new_plan') s.store.create(f, id, false);
        else s.store.select(abilityKey(f, id));
        s.store.edit([], abilityNode(own(body, 'value')));
        s.store.save();
      });
      await waitFor(() => {
        expect(s.entry()?.outcome).toBe(answer.status === 200 ? 'saved' : 'refused');
      });
      expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(body);
      expect(s.fake.writes()).toHaveLength(1);
      if (answer.status !== 200)
        expect(abilityValue(s.entry()?.node ?? abilityNode(null))).toEqual(own(body, 'value'));
      s.source(`${s.prefix}_after`);
      s.settings(`${s.prefix}_settings_after`);
      await change(s.store.refresh);
      expect(currentAbilities(s.store.getSnapshot())?.source).toEqual(
        fixture(`${s.prefix}_after`).body,
      );
    });
  it('A1 profile-scoped override uses its settings revision, not the profile revision', async () => {
    const s = await setup({ view: 'global' });
    await change(() => {
      s.store.select(abilityKey('global', 'demon_mode_always'));
      s.store.edit([], abilityNode(true));
      s.store.save();
    });
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      fixture(`${s.prefix}_saved`).request.body,
    );
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
  });
  for (const refusal of [
    'stale_revision_is_judged_before_the_values',
    'starter_profile_active_a_profile_module_is_refused',
  ])
    it(`A1 ${refusal} keeps the raw global choice`, async () => {
      const s = await setup({ view: 'global', save: `settings.${refusal}` });
      await change(() => {
        s.store.select(abilityKey('global', 'demon_mode_always'));
        s.store.edit([], abilityNode(true));
        s.store.save();
      });
      await waitFor(() => {
        expect(s.entry()?.outcome).toBe('conflict');
      });
      expect(abilityValue(s.entry()?.node ?? abilityNode(null))).toBe(true);
      expect(s.fake.writes()).toHaveLength(1);
    });
  it('A2 newer typing survives a late older save answer and duplicate clicks send once', async () => {
    const s = await setup({ save: 'hold', windowMs: 10_000 });
    await change(() => {
      s.store.edit(['label'], abilityNode('New label'));
      s.store.save();
      s.store.save();
      s.store.edit(['label'], abilityNode('Newer typing'));
    });
    const a = fixture(`${s.prefix}_saved`);
    await change(() => {
      s.fake.next('/api/profile-patch').respond(a.status, a.body);
    });
    expect(s.entry()?.outcome).toBe('saved');
    expect(s.entry()?.dirty).toBe(true);
    expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'label')).toBe('Newer typing');
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('A2 unknown save settles by a later original-target read and is never resent', async () => {
    const s = await setup({ save: 'hold' });
    await change(() => {
      s.store.edit(['label'], abilityNode('New label'));
      s.store.save();
    });
    await change(() => {
      s.fake.next('/api/profile-patch').lose();
    });
    expect(s.entry()?.outcome).toBe('unknown');
    s.source(`${s.prefix}_after`);
    s.settings(`${s.prefix}_settings_after`);
    await change(s.store.refresh);
    expect(s.entry()?.outcome).toBe('readApplied');
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('A2 F6C-R1-02 an unchanged read and revision adoption keep a lost save unknown until matching evidence', async () => {
    const s = await setup({ save: 'hold' });
    await change(() => {
      s.store.edit(['label'], abilityNode('New label'));
      s.store.save();
    });
    await change(() => {
      s.fake.next('/api/profile-patch').lose();
    });
    await change(s.store.refresh);
    // Shared rule (core/settingsFields, core/profilePatch): an unchanged read cannot exclude
    // a commit still to come. Explicit adoption cannot turn that unchanged read into proof.
    expect(s.entry()?.outcome).toBe('unknown');
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
    await change(s.store.reconcile);
    await change(() => {
      s.store.save();
    });
    expect(s.entry()?.outcome).toBe('unknown');
    expect(s.fake.writes()).toHaveLength(1);
    s.source(`${s.prefix}_after`);
    s.settings(`${s.prefix}_settings_after`);
    await change(s.store.refresh);
    expect(s.entry()?.outcome).toBe('readApplied');
  });
  it('A2 changed read leaves the old outcome unknown until explicit adoption of current revisions', async () => {
    const s = await setup({ save: 'hold' });
    await change(() => {
      s.store.edit(['label'], abilityNode('New label'));
      s.store.save();
    });
    await change(() => {
      s.fake.next('/api/profile-patch').lose();
    });
    s.source('ability_policies.read_after_the_saves_holds_every_change');
    await change(s.store.refresh);
    expect(s.entry()?.outcome).toBe('unknown');
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
    expect(canReconcileAbility(s.store.getSnapshot(), Date.now())).toBe(true);
    await change(s.store.reconcile);
    expect(s.entry()?.outcome).toBeNull();
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(true);
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('A2 reload keeps unresolved submission and raw text without sending again', async () => {
    const s = await setup({ save: 'hold' });
    await change(() => {
      s.store.edit(['chain_lightning', 'pre_mark_waves', 0], { kind: 'number', raw: '0007' });
      s.store.save();
      s.store.edit(['chain_lightning', 'pre_mark_waves', 0], { kind: 'number', raw: '-.0bad' });
    });
    s.store.dispose();
    const restored = await setup({ storage: s.storage });
    expect(
      nodeAt(restored.entry()?.node ?? abilityNode(null), ['chain_lightning', 'pre_mark_waves', 0]),
    ).toEqual({ kind: 'number', raw: '-.0bad' });
    expect(restored.fake.writes()).toHaveLength(0);
    expect(restored.entry()?.outcome).toBe('unknown');
  });
  it('A2 copying preserves malformed and padded raw numeric input', async () => {
    const s = await setup();
    await change(() => {
      s.store.edit(['chain_lightning', 'pre_mark_waves', 0], { kind: 'number', raw: '00bad' });
      s.store.create('uw_policies', 'raw_copy', true);
    });
    expect(
      nodeAt(s.entry()?.node ?? abilityNode(null), ['chain_lightning', 'pre_mark_waves', 0]),
    ).toEqual({ kind: 'number', raw: '00bad' });
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('A2 late older answer cannot overwrite a newer profile read', async () => {
    const s = await setup({ save: 'hold', windowMs: 10_000 });
    await change(() => {
      s.store.edit(['label'], abilityNode('New label'));
      s.store.save();
    });
    s.source('ability_policies.read_after_the_saves_holds_every_change');
    await change(s.store.refresh);
    const latest = currentAbilities(s.store.getSnapshot())?.source;
    const a = fixture(`${s.prefix}_saved`);
    await change(() => {
      s.fake.next('/api/profile-patch').respond(a.status, a.body);
    });
    expect(s.entry()?.outcome).toBe('conflict');
    expect(currentAbilities(s.store.getSnapshot())?.source).toEqual(latest);
  });
  it('A2 target change keeps original draft and settles the original submission on return', async () => {
    const s = await setup({ save: 'hold' });
    await change(() => {
      s.store.edit(['label'], abilityNode('New label'));
      s.store.save();
    });
    // The active save now awaits its click-time read. This case changes targets after
    // send; changing before that read completes is the separate no-send guard case.
    await waitFor(() => {
      expect(s.fake.writes()).toHaveLength(1);
    });
    await change(() => {
      s.store.setTarget({ ...s.context, account: 'another' }, 2);
    });
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
    s.source(`${s.prefix}_after`);
    s.settings(`${s.prefix}_settings_after`);
    await change(() => {
      s.store.setTarget(s.context, 3);
    });
    await waitFor(() => {
      expect(currentAbilities(s.store.getSnapshot())?.busy).toBe(false);
    });
    expect(s.entry()?.outcome).toBe('readApplied');
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('A2 deleting referenced policy is unavailable; explicit unused deletion is revisioned', async () => {
    const s = await setup({ view: 'delete' });
    expect(canSaveAbility(s.store.getSnapshot(), Date.now(), true)).toBe(false);
    await change(() => {
      s.store.select('uw_policies/unused');
      s.store.save(true);
    });
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      fixture(`${s.prefix}_saved`).request.body,
    );
  });
  for (const lost of [false, true])
    it(`A2 F6A-SOL-01 deletion ${lost ? 'readback' : 'answer'} keeps newer raw typing through navigation and reload`, async () => {
      const s = await setup({ view: 'delete', save: 'hold', windowMs: 10_000 });
      await change(() => {
        s.store.select('uw_policies/unused');
        s.store.save(true);
        s.store.edit(['label'], abilityNode('Typed after delete'));
        s.store.edit(['baseline', 'future_draft'], { kind: 'number', raw: '00bad' });
      });
      const answer = fixture(`${s.prefix}_saved`);
      await change(() => {
        const request = s.fake.next('/api/profile-patch');
        if (lost) request.lose();
        else request.respond(answer.status, answer.body);
      });
      s.source(`${s.prefix}_after`);
      s.settings(`${s.prefix}_settings_after`);
      await change(s.store.refresh);
      expect(s.entry()).toMatchObject({ dirty: true, deleting: false, isNew: true });
      expect(s.entry()?.submission?.deleted).toBe(true);
      await change(() => {
        s.store.select('uw_policies/farm_cl');
        s.store.select('uw_policies/unused');
      });
      const node = s.entry()?.node;
      s.store.dispose();
      const restored = await setup({
        view: 'delete',
        storage: s.storage,
        profile: `${s.prefix}_after`,
        settings: `${s.prefix}_settings_after`,
      });
      expect(restored.entry()?.node).toEqual(node);
      expect(restored.entry()).toMatchObject({ id: 'unused', dirty: true, isNew: true });
      expect(restored.entry()?.submission?.deleted).toBe(true);
      expect(restored.entry()?.outcome).toBe(lost ? 'readApplied' : 'saved');
      expect(restored.fake.writes()).toHaveLength(0);
      expect(s.fake.writes()).toHaveLength(1);
    });
  for (const view of ['cl_off', 'arm_always', 'action_cancel_sprint'])
    it(`A3 A4 A5 F6A-SOL-02 ${view} successful save and read keep hidden raw branches`, async () => {
      const s = await setup({ view });
      const body = fixture(`${s.prefix}_saved`).request.body;
      const path = own(body, 'path');
      if (!Array.isArray(path)) throw new Error('Missing policy path');
      const family = path[1] === 'uw_policies' ? 'uw_policies' : 'rescue_policies';
      const id = String(path[2]);
      await change(() => {
        if (id === 'new_plan') {
          s.store.create(family, id, false);
          s.store.edit([], abilityNode(own(body, 'value')));
        }
        s.store.select(`${family}/${id}`);
        if (view === 'cl_off') {
          s.store.edit(['chain_lightning', 'pre_mark_waves', 0], { kind: 'number', raw: '00bad' });
          s.store.switchMode(['chain_lightning'], 'off');
        } else if (view === 'arm_always') {
          s.store.edit(['arm', 'watch_sec'], { kind: 'number', raw: '0045' });
          s.store.switchMode(['arm'], 'always');
        } else {
          s.store.rulePart(0, 'when', 'bar');
          s.store.edit(['rules', 0, 'when', 'below'], { kind: 'number', raw: '00bad' });
          s.store.rulePart(0, 'when', 'wave_at_least');
          s.store.rulePart(0, 'do', 'fire');
          s.store.edit(['rules', 0, 'do', 'fire', 'throttle_sec'], {
            kind: 'number',
            raw: '00bad',
          });
          s.store.rulePart(0, 'do', 'cancel_sprint');
        }
        s.store.review(true);
        s.store.save();
      });
      await waitFor(() => {
        expect(s.entry()?.outcome).toBe('saved');
      });
      const stash = s.entry()?.stash;
      expect(Object.keys(stash ?? {})).not.toHaveLength(0);
      s.source(`${s.prefix}_after`);
      s.settings(`${s.prefix}_settings_after`);
      await change(s.store.refresh);
      expect(s.entry()?.stash).toEqual(stash);
      s.store.dispose();
      const restored = await setup({
        view,
        storage: s.storage,
        profile: `${s.prefix}_after`,
        settings: `${s.prefix}_settings_after`,
      });
      expect(restored.entry()?.stash).toEqual(stash);
      await change(() => {
        if (view === 'cl_off') restored.store.switchMode(['chain_lightning'], 'fleet_marks');
        else if (view === 'arm_always') restored.store.switchMode(['arm'], 'second_wind');
        else restored.store.rulePart(0, 'when', 'bar');
      });
      expect(
        nodeAt(
          restored.entry()?.node ?? abilityNode(null),
          view === 'cl_off'
            ? ['chain_lightning', 'pre_mark_waves', 0]
            : view === 'arm_always'
              ? ['arm', 'watch_sec']
              : ['rules', 0, 'when', 'below'],
        ),
      ).toEqual({ kind: 'number', raw: view === 'arm_always' ? '0045' : '00bad' });
      expect(restored.fake.writes()).toHaveLength(0);
      if (view === 'action_cancel_sprint') {
        await change(() => {
          restored.store.rulePart(0, 'do', 'fire');
        });
        expect(
          nodeAt(restored.entry()?.node ?? abilityNode(null), [
            'rules',
            0,
            'do',
            'fire',
            'throttle_sec',
          ]),
        ).toEqual({ kind: 'number', raw: '00bad' });
      }
    });
  it('A4 F6A-SOL-02 successful save read preserves padded active numeric text', async () => {
    const s = await setup({ view: 'arm_durations' });
    const body = fixture(`${s.prefix}_saved`).request.body;
    const value = own(body, 'value'),
      raw = `00${String(ownPath(value, 'arm', 'watch_sec'))}`;
    await change(() => {
      s.store.select('rescue_policies/wall_drain');
      s.store.edit([], abilityNode(value));
      s.store.edit(['arm', 'watch_sec'], { kind: 'number', raw });
      s.store.save();
    });
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    s.source(`${s.prefix}_after`);
    s.settings(`${s.prefix}_settings_after`);
    await change(s.store.refresh);
    expect(nodeAt(s.entry()?.node ?? abilityNode(null), ['arm', 'watch_sec'])).toEqual({
      kind: 'number',
      raw,
    });
  });
  for (const raw of ['', '0', '-1', '1.5', 'Infinity', 'NaN', '3wat'])
    it(`A3 raw wave text ${JSON.stringify(raw)} stays visible without clamping`, async () => {
      const s = await setup();
      await change(() => {
        s.store.edit(['chain_lightning', 'pre_mark_waves', 0], { kind: 'number', raw });
      });
      expect(
        nodeAt(s.entry()?.node ?? abilityNode(null), ['chain_lightning', 'pre_mark_waves', 0]),
      ).toEqual({ kind: 'number', raw });
      if (['', 'Infinity', 'NaN', '3wat'].includes(raw))
        expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
    });
  it('A4 mode review preserves the hidden branch and Cancel restores saved arming', async () => {
    const s = await setup({ view: 'arm_durations' });
    s.store.select('rescue_policies/wall_drain');
    const original = abilityValue(s.entry()?.node ?? abilityNode(null));
    await change(() => {
      s.store.edit(['arm', 'watch_sec'], { kind: 'number', raw: '0045' });
      s.store.switchMode(['arm'], 'always');
    });
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
    await change(() => {
      s.store.switchMode(['arm'], 'second_wind');
    });
    expect(nodeAt(s.entry()?.node ?? abilityNode(null), ['arm', 'watch_sec'])).toEqual({
      kind: 'number',
      raw: '0045',
    });
    await change(s.store.cancel);
    expect(abilityValue(s.entry()?.node ?? abilityNode(null))).toEqual(original);
  });
  it('A4 mode-hidden raw fields survive reload and return to their own branch', async () => {
    const s = await setup({ view: 'arm_durations' });
    await change(() => {
      s.store.select('rescue_policies/wall_drain');
      s.store.edit(['arm', 'watch_sec'], { kind: 'number', raw: '0045' });
      s.store.switchMode(['arm'], 'always');
    });
    s.store.dispose();
    const restored = await setup({ view: 'arm_durations', storage: s.storage });
    await change(() => {
      restored.store.switchMode(['arm'], 'second_wind');
    });
    expect(nodeAt(restored.entry()?.node ?? abilityNode(null), ['arm', 'watch_sec'])).toEqual({
      kind: 'number',
      raw: '0045',
    });
  });
  it('plan heading leads with its editable label and preserves the ID for deep links', async () => {
    const s = await setup({ mount: true });
    expect(
      screen.getByRole('heading', { level: 2, name: 'Farm choreography ID farm_cl' }),
    ).toHaveAttribute('id', 'abilities-editor');
    fireEvent.change(screen.getByRole('textbox', { name: 'Plan label' }), {
      target: { value: 'My future battle plan' },
    });
    expect(
      screen.getByRole('heading', { level: 2, name: 'My future battle plan ID farm_cl' }),
    ).toHaveAttribute('id', 'abilities-editor');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('readiness summary and Recognition remain visible with saved policy data closed', async () => {
    const s = await setup({ mount: true });
    const link = screen.getByRole('link', { name: 'Open Recognition' });
    expect(link).toBeVisible();
    expect(link).toHaveAttribute('href', '#recognition');
    expect(link.closest('details')).toBeNull();
    expect(screen.getByText('Not checked by this editor', { exact: true })).toBeVisible();
    expect(
      screen.getByText('Recorded ability verification', { exact: true }).nextElementSibling,
    ).toHaveTextContent('Yes');
    const summary = screen.getByText('Saved policy data', { selector: 'summary', exact: true });
    expect(summary.closest('details')).not.toHaveAttribute('open');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('A5 vocabulary supports all published triggers and safe actions with want_on', async () => {
    const s = await setup({ mount: true });
    fireEvent.change(screen.getByRole('combobox', { name: 'Saved plans' }), {
      target: { value: 'rescue_policies' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Wall drain' }));
    expect(screen.getByRole('combobox', { name: 'rule 1 condition' })).toHaveValue('bar');
    expect(screen.getByRole('combobox', { name: 'rule 1 action' })).toHaveValue('burst');
    const triggerOptions = [
      ...screen.getByRole<HTMLSelectElement>('combobox', { name: 'rule 1 condition' }).options,
    ]
      .map((o) => o.value)
      .filter(Boolean);
    expect(triggerOptions).toEqual(
      entries(own(currentAbilities(s.store.getSnapshot())?.vocab, 'rule_triggers')).length === 0
        ? []
        : Object.keys(
            ownPath(currentAbilities(s.store.getSnapshot())?.vocab, 'rule_triggers', 'fields') ??
              {},
          ),
    );
    expect(screen.queryByRole('option', { name: 'surrender retry' })).toBeNull();
    expect(screen.queryByRole('option', { name: 'switch cards' })).toBeNull();
  });
  it('A3 absent baseline choices display unspecified without adding false to the saved body', async () => {
    const s = await setup({ mount: true });
    expect(screen.getByRole('combobox', { name: 'smart missiles' })).toHaveValue('');
    expect(
      ownPath(abilityValue(s.entry()?.node ?? abilityNode(null)), 'baseline', 'smart_missiles'),
    ).toBeUndefined();
  });
  it('A5 blank scalar trigger remains editable until its numeric value is entered', async () => {
    const s = await setup({ mount: true });
    await change(() => {
      s.store.create('rescue_policies', 'new_plan', false);
      s.store.rule('add');
      s.store.rulePart(0, 'when', 'wave_at_least');
    });
    const input = screen.getByRole('textbox', { name: 'wave at least' });
    expect(input).toHaveValue('');
    fireEvent.change(input, { target: { value: '001000' } });
    expect(input).toHaveValue('001000');
    expect(screen.getByRole('combobox', { name: 'rule 1 condition' })).toHaveValue('wave_at_least');
  });
  it('A7 unknown, retired and surrender rules remain exact and only explicit deletion removes one', async () => {
    const s = await setup(),
      t = currentAbilities(s.store.getSnapshot()),
      vocab = t?.vocab;
    s.store.select('rescue_policies/wall_drain');
    for (const rule of [
      { when: { death_screen: true }, do: { stop_after_run: true } },
      { when: { moon: 'full' }, do: { dance: true } },
      { when: { wave_at_least: 5 }, do: { surrender_retry: true } },
    ]) {
      expect(ruleSupported(rule, vocab)).toBe(false);
      await change(() => {
        s.store.edit(
          ['rules'],
          abilityNode([rule, { when: { wave_at_least: 100 }, do: { cancel_sprint: true } }]),
        );
      });
      expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'rules')).toEqual([
        rule,
        { when: { wave_at_least: 100 }, do: { cancel_sprint: true } },
      ]);
      await change(() => {
        s.store.rule('delete', 0);
      });
      expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'rules')).toEqual([
        { when: { wave_at_least: 100 }, do: { cancel_sprint: true } },
      ]);
    }
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('A7 retired draft rules render read-only with no authoring or conversion', async () => {
    const s = await setup({ mount: true });
    await change(() => {
      s.store.select('rescue_policies/wall_drain');
      s.store.edit(
        ['rules'],
        abilityNode([{ when: { death_screen: true }, do: { stop_after_run: true } }]),
      );
    });
    expect(screen.getByText(/This rule is read-only/)).toBeVisible();
    expect(screen.queryByRole('combobox', { name: 'rule 1 action' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Delete rule 1' }));
    expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'rules')).toEqual([]);
    expect(s.fake.writes()).toHaveLength(0);
  });
  for (const rule of [
    'death_screen',
    { when: 'death_screen', do: { cancel_sprint: true } },
    { when: { wave_at_least: 5 }, do: 'cancel_sprint' },
    { when: { wave_at_least: 5 }, do: { fire: { button: 'demon_mode', future_option: 42 } } },
    { when: { second_wind: { state: 'open', future_option: 42 } }, do: { cancel_sprint: true } },
    { when: { wave_at_least: 5 }, do: { cancel_sprint: true }, future_option: true },
  ])
    it(`A7 F6A-SOL-03 unsupported shape ${JSON.stringify(rule)} is exact and read-only`, async () => {
      const s = await setup({ mount: true });
      await change(() => {
        s.store.select('rescue_policies/wall_drain');
        s.store.edit(['rules'], abilityNode([rule]));
      });
      expect(ruleSupported(rule, currentAbilities(s.store.getSnapshot())?.vocab)).toBe(false);
      expect(screen.getByText(/This rule is read-only/)).toBeVisible();
      expect(screen.queryByRole('combobox', { name: 'rule 1 condition' })).toBeNull();
      expect(screen.queryByRole('combobox', { name: 'rule 1 action' })).toBeNull();
      expect(
        screen.queryByRole('textbox', { name: 'Cooldown between repeats (seconds)' }),
      ).toBeNull();
      expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'rules')).toEqual([rule]);
      expect(s.fake.writes()).toHaveLength(0);
    });
  for (const view of ['cl_reversed', 'cl_fractional', 'mixed_bars', 'burst_wrong_gate'])
    it(`A3 A5 F6A-SOL-04 ${view} backend errors focus the summary and stay tied to submitted values`, async () => {
      const s = await setup({ view, mount: true });
      const answer = fixture(`${s.prefix}_saved`),
        body = answer.request.body;
      const path = own(body, 'path');
      if (!Array.isArray(path)) throw new Error('Missing policy path');
      await change(() => {
        s.store.select(`${String(path[1])}/${String(path[2])}`);
        s.store.edit([], abilityNode(own(body, 'value')));
        s.store.save();
      });
      const summary = screen.getByRole('region', { name: 'Backend validation problems' });
      expect(summary).toHaveFocus();
      fireEvent.click(summary.querySelector('a') ?? summary);
      if (view.startsWith('cl_')) {
        const group = screen.getByRole('group', { name: 'Turn on above wave' });
        const min = group.querySelector('input');
        expect(min).toHaveFocus();
        expect(min).toHaveAttribute('aria-invalid', 'true');
        expect(min).toHaveAccessibleDescription(expect.stringContaining('policies.uw_policies'));
        fireEvent.change(min ?? group, { target: { value: '500' } });
        expect(screen.queryByRole('region', { name: 'Backend validation problems' })).toBeNull();
      }
      expect(s.fake.writes()).toHaveLength(1);
    });
  it('A1 A2 A3 A4 A5 A7 axe and focus survive a read without remounting the draft', async () => {
    const s = await setup({ mount: true });
    const label = screen.getByRole('textbox', { name: 'Plan label' });
    label.focus();
    fireEvent.change(label, { target: { value: 'Local draft' } });
    await change(s.store.refresh);
    expect(label).toHaveFocus();
    expect(label).toHaveValue('Local draft');
    expect(await axe(s.mounted?.view.container ?? document.body)).toHaveNoViolations();
  });
  for (const ref of [
    'ability_policies.abilities_not_verified_is_an_advisory_not_a_problem',
    'ability_policies.no_wall_confirmed_is_an_advisory_not_a_problem',
    'ability_policies.abilities_the_account_does_not_own_are_bound_to_a_run_is_a_problem',
  ])
    it(`A4 ${ref} displays authoritative warnings and problems`, async () => {
      const s = await setup({ profile: ref, mount: true });
      const source = fixture(ref).body;
      for (const value of [
        ...(Array.isArray(own(source, 'problems')) ? (own(source, 'problems') as string[]) : []),
        ...(Array.isArray(own(source, 'warnings')) ? (own(source, 'warnings') as string[]) : []),
      ])
        expect(screen.getByText(value)).toBeVisible();
      expect(s.fake.writes()).toHaveLength(0);
    });
  it('A2 failed vocabulary or target read blocks save without inventing defaults', async () => {
    const s = await setup({ vocab: 'profile_reads.vocab_unavailable_is_http_200_with_ok_false' });
    expect(currentAbilities(s.store.getSnapshot())?.error).toBe(true);
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
  });
  for (const matching of [false, true])
    it(`A2 F6A-R1-01 held target roundtrip with ${matching ? 'matching' : 'unchanged'} read never invents a send`, async () => {
      const s = await setup({ save: 'hold', windowMs: 10_000 });
      await change(() => {
        s.store.edit(['label'], abilityNode('New label'));
        s.store.save();
      });
      const original = s.entry()?.submission;
      const beta = contextFromConfig(
        fixture('config_state.effective_view_of_the_other_account').body,
      );
      if (beta === null) throw new Error('Missing Beta context');
      s.config('config_state.effective_view_of_the_other_account');
      s.source('profile_reads.profile_src_of_the_other_accounts_profile_is_not_refused');
      s.settings('settings.other_accounts_profile_active_profile_modules_read_only');
      await change(() => {
        s.store.setTarget(beta, 2);
      });
      await waitFor(() => {
        expect(currentAbilities(s.store.getSnapshot())?.busy).toBe(false);
      });
      s.config(`${s.prefix}_config`);
      s.source(`${s.prefix}_${matching ? 'after' : 'before'}`);
      s.settings(`${s.prefix}_${matching ? 'settings_after' : 'settings'}`);
      await change(() => {
        s.store.setTarget(s.context, 3);
      });
      await waitFor(() => {
        expect(currentAbilities(s.store.getSnapshot())?.busy).toBe(false);
      });
      await change(() => {
        if (!matching) s.store.reconcile();
        s.store.edit(['label'], abilityNode('Newer never sent'));
        s.store.save();
      });
      // A provisional matching read keeps the original revision until its answer is final.
      expect(s.entry()?.outcome).toBe(matching ? 'readApplied' : 'unknown');
      expect(s.entry()?.submission).toEqual(original);
      expect(s.fake.writes()).toHaveLength(1);
      const answer = fixture(`${s.prefix}_saved`);
      await change(() => {
        s.fake.next('/api/profile-patch').respond(answer.status, answer.body);
      });
      s.source(`${s.prefix}_after`);
      s.settings(`${s.prefix}_settings_after`);
      await change(s.store.refresh);
      expect(s.entry()?.outcome).not.toBe('sending');
      await change(() => {
        s.store.save();
      });
      expect(s.fake.writes()).toHaveLength(2);
      expect(ownPath(JSON.parse(s.fake.writes()[1]?.body ?? ''), 'value', 'label')).toBe(
        'Newer never sent',
      );
    });
  for (const view of ['raw_rule_refresh', 'raw_number_refresh'])
    it(`A2 A5 F6A-R1-02 ${view} label-only read preserves raw nodes, hidden rule identity, order and reload`, async () => {
      const s = await setup({ view, save: `abilities_samples.${view}_committed` });
      const rule = view === 'raw_rule_refresh';
      await change(() => {
        if (rule) {
          s.store.select('rescue_policies/wall_drain');
          s.store.rulePart(2, 'when', 'wave_at_least');
          s.store.edit(['rules', 2, 'when', 'wave_at_least'], { kind: 'number', raw: '0007' });
          s.store.rulePart(2, 'when', 'wave_between');
          s.store.edit(['rules', 2, 'when', 'wave_between', 0], { kind: 'number', raw: '0010' });
          s.store.review(true);
        } else
          s.store.edit(['chain_lightning', 'pre_mark_waves', 0], { kind: 'number', raw: '0005' });
        s.store.save();
      });
      await waitFor(() => {
        expect(s.entry()?.outcome).toBe('saved');
      });
      s.source(`${s.prefix}_after`);
      s.settings(`${s.prefix}_settings_after`);
      await change(s.store.refresh);
      expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'label')).toBe(
        'External label',
      );
      if (rule) {
        s.source(`${s.prefix}_reordered`);
        await change(s.store.refresh);
        expect(
          nodeAt(s.entry()?.node ?? abilityNode(null), ['rules', 0, 'when', 'wave_between', 0]),
        ).toEqual({ kind: 'number', raw: '0010' });
      } else
        expect(
          nodeAt(s.entry()?.node ?? abilityNode(null), ['chain_lightning', 'pre_mark_waves', 0]),
        ).toEqual({ kind: 'number', raw: '0005' });
      s.store.dispose();
      const restored = await setup({
        view,
        storage: s.storage,
        profile: `${s.prefix}_${rule ? 'reordered' : 'after'}`,
        settings: `${s.prefix}_settings_after`,
      });
      if (rule) {
        await change(() => {
          restored.store.rulePart(0, 'when', 'wave_at_least');
        });
        expect(
          nodeAt(restored.entry()?.node ?? abilityNode(null), [
            'rules',
            0,
            'when',
            'wave_at_least',
          ]),
        ).toEqual({ kind: 'number', raw: '0007' });
      } else
        expect(
          nodeAt(restored.entry()?.node ?? abilityNode(null), [
            'chain_lightning',
            'pre_mark_waves',
            0,
          ]),
        ).toEqual({ kind: 'number', raw: '0005' });
      expect(restored.fake.writes()).toHaveLength(0);
    });
  it('A2 F6A-R1-03 unfinished identifiers persist only under their target and policy family', async () => {
    const s = await setup({ mount: true });
    const input = () => screen.getByRole('textbox', { name: 'New plan ID' });
    fireEvent.change(input(), { target: { value: 'unfinished_copy' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Saved plans' }), {
      target: { value: 'rescue_policies' },
    });
    expect(input()).toHaveValue('');
    fireEvent.change(input(), { target: { value: 'alpha_rescue' } });
    const beta = contextFromConfig(
      fixture('config_state.effective_view_of_the_other_account').body,
    );
    if (beta === null) throw new Error('Missing Beta');
    await change(() => {
      s.store.setTarget(beta, 2);
    });
    expect(input()).toHaveValue('');
    fireEvent.change(input(), { target: { value: 'beta_only' } });
    await change(() => {
      s.store.setTarget(s.context, 3);
    });
    expect(input()).toHaveValue('alpha_rescue');
    s.mounted?.view.unmount();
    s.store.dispose();
    const restored = await setup({ storage: s.storage, mount: true });
    expect(input()).toHaveValue('unfinished_copy');
    fireEvent.change(screen.getByRole('combobox', { name: 'Saved plans' }), {
      target: { value: 'rescue_policies' },
    });
    expect(input()).toHaveValue('alpha_rescue');
    await change(() => {
      restored.store.setTarget(beta, 4);
    });
    expect(input()).toHaveValue('beta_only');
    expect(restored.fake.writes()).toHaveLength(0);
  });
  it('A3 F6A-R1-04 optional threshold explicitly clears to null and restores exact bounds', async () => {
    const s = await setup({ view: 'cl_unset', mount: true });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Use explicit wave bounds' }));
    await change(() => {
      s.store.review(true);
      s.store.save();
    });
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      fixture(`${s.prefix}_saved`).request.body,
    );
    s.source(`${s.prefix}_after`);
    s.settings(`${s.prefix}_settings_after`);
    await change(s.store.refresh);
    expect(
      nodeAt(s.entry()?.node ?? abilityNode(null), ['chain_lightning', 'always_on_above']),
    ).toEqual(abilityNode(null));
    s.mounted?.view.unmount();
    s.store.dispose();
    const restored = await setup({ view: 'cl_restore', storage: s.storage, mount: true });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Use explicit wave bounds' }));
    await change(() => {
      restored.store.review(true);
      restored.store.save();
    });
    await waitFor(() => {
      expect(restored.entry()?.outcome).toBe('saved');
    });
    expect(JSON.parse(restored.fake.writes()[0]?.body ?? '')).toEqual(
      fixture(`${restored.prefix}_saved`).request.body,
    );
  });
  for (const view of ['rules_absent', 'rules_null'])
    it(`A5 F6A-R1-05 ${view} adds and saves the first rule explicitly`, async () => {
      const s = await setup({ view, mount: true });
      await change(() => {
        s.store.select('rescue_policies/wall_drain');
      });
      fireEvent.click(screen.getByRole('button', { name: /^Add rule$/ }));
      await change(() => {
        s.store.rulePart(0, 'when', 'wave_at_least');
        s.store.edit(['rules', 0, 'when', 'wave_at_least'], { kind: 'number', raw: '1000' });
        s.store.rulePart(0, 'do', 'cancel_sprint');
        s.store.review(true);
        s.store.save();
      });
      await waitFor(() => {
        expect(s.entry()?.outcome).toBe('saved');
      });
      expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
        fixture(`${s.prefix}_saved`).request.body,
      );
      s.source(`${s.prefix}_after`);
      s.settings(`${s.prefix}_settings_after`);
      await change(s.store.refresh);
      expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'rules')).toEqual(
        ownPath(fixture(`${s.prefix}_saved`).request.body, 'value', 'rules'),
      );
    });
  it('A3 F6A-R1-04 a partially entered explicit optional pair remains raw and unsavable', async () => {
    const s = await setup({ view: 'cl_restore' });
    await change(() => {
      s.store.optionalRange(['chain_lightning', 'always_on_above'], true);
      s.store.edit(['chain_lightning', 'always_on_above', 0], { kind: 'number', raw: '0005' });
      s.store.review(true);
    });
    expect(canSaveAbility(s.store.getSnapshot(), Date.now())).toBe(false);
    expect(
      nodeAt(s.entry()?.node ?? abilityNode(null), ['chain_lightning', 'always_on_above', 0]),
    ).toEqual({ kind: 'number', raw: '0005' });
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('A2 F6A-R1-02 partial pair refresh keeps unchanged raw members and conflicts on changed rule identities', () => {
    const node = setAbilityNode(abilityNode({ pair: [5, 25], label: 'old' }), ['pair', 0], {
      kind: 'number',
      raw: '0005',
    });
    const merged = reconcileAbilityNode(node, { pair: [5, 30], label: 'new' });
    expect(nodeAt(merged ?? abilityNode(null), ['pair', 0])).toEqual({
      kind: 'number',
      raw: '0005',
    });
    expect(abilityValue(merged ?? abilityNode(null))).toEqual({ pair: [5, 30], label: 'new' });
    expect(
      reconcileAbilityNode(
        abilityNode({ rules: [{ when: { wave_at_least: 5 }, do: { cancel_sprint: true } }] }),
        { rules: [{ when: { wave_at_least: 6 }, do: { cancel_sprint: true } }] },
      ),
    ).toBeNull();
  });
  it('A5 F6A-R1-05 malformed non-list rules remain exact and inspection-only', async () => {
    const s = await setup({ mount: true });
    await change(() => {
      s.store.select('rescue_policies/wall_drain');
      s.store.edit(['rules'], abilityNode({ future: true }));
    });
    expect(screen.queryByRole('button', { name: /^Add rule$/ })).toBeNull();
    expect(own(abilityValue(s.entry()?.node ?? abilityNode(null)), 'rules')).toEqual({
      future: true,
    });
    expect(s.fake.writes()).toHaveLength(0);
  });
});
