import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, expect, it } from 'vitest';
import { createEquipmentApi } from '../../../../src/api/equipment.ts';
import { contextFromConfig } from '../../../../src/api/context.ts';
import { entries, list, own, ownPath, record } from '../../../../src/core/read.ts';
import { settingsSaveAnswer, settleSettingsSave } from '../../../../src/core/settingsFields.ts';
import { EquipmentPage } from '../../../../src/features/equipment/EquipmentPage.tsx';
import {
  createEquipmentStore,
  currentEquipment,
  currentLoadout,
} from '../../../../src/store/equipment.ts';
import { initialDashboardState } from '../../../../src/store/dashboardStore.ts';
import { selectEquipment } from '../../../../src/store/selectors/equipment.ts';
import { settingsScope } from '../../../../src/store/selectors/settingsModule.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../../scripts/contract-fixture.mjs';
import { createFakeTransport, createMemoryStorage } from '../../../support/fakeTransport.ts';
import { renderRegion } from '../dashboard/harness.tsx';
import { addBody, regionState } from '../../store/regionHelpers.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import type { DashboardCase } from '../../../support/dashboardFixture.ts';

const cases = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');
function fixture(reference: string): DashboardCase {
  const [family = '', key = ''] = reference.split('.');
  const raw = ownPath(cases, family, key);
  if (raw === undefined) throw new Error(reference);
  return structuredClone(raw) as DashboardCase;
}
const cleanups: (() => void)[] = [];
afterEach(() => {
  cleanups.splice(0).forEach((dispose) => {
    dispose();
  });
});
async function change(action: () => void | Promise<void>) {
  await act(async () => {
    await action();
  });
}
async function setup(
  configRef = 'loadout_patch.read_before_the_first_save',
  saveRef = 'loadout_restore.saved_cards_restore',
  profileRef = 'ability_policies.read_before_the_first_save',
  // The intent machine's answer window (real time). 50 ms lets most cases reach their lost
  // answer quickly; a case that asserts a held send is still in flight needs a window the
  // assertion cannot outlive under a loaded machine.
  windowMs = 50,
) {
  const fake = createFakeTransport();
  let config = fixture(configRef);
  let answer: DashboardCase | 'hold' = fixture(saveRef);
  fake.route('GET', '/api/config', () => config);
  fake.route('GET', /\/api\/profile-src\//, () => fixture(profileRef));
  fake.route('POST', '/api/loadout-patch', () => answer);
  const context = contextFromConfig(config.body);
  if (context === null) throw new Error('No context');
  const storage = createMemoryStorage();
  const api = createEquipmentApi(fake.transport, storage, windowMs);
  const equipment = createEquipmentStore(api);
  cleanups.push(equipment.dispose);
  await change(() => {
    equipment.setTarget(context, 1);
  });
  await waitFor(() => {
    expect(currentEquipment(equipment.getSnapshot())?.busy).toBe(false);
  });
  const mounted = renderRegion(
    initialDashboardState({ context }),
    <EquipmentPage equipment={equipment} />,
  );
  await change(() => {
    equipment.select('plain');
  });
  return {
    equipment,
    api,
    fake,
    context,
    mounted,
    storage,
    entry: () => currentLoadout(equipment.getSnapshot()),
    config: (reference: string) => {
      config = fixture(reference);
    },
    answer: (reference: string) => {
      answer = reference === 'hold' ? 'hold' : fixture(reference);
    },
  };
}
const save = () => screen.getByRole('button', { name: 'Save selection' });

for (const row of ['C15-8', 'C15-9']) {
  it.each(['selection', 'refresh', 'preflight revision', 'new edit'])(
    `${row} C-02 held preflight fences changed %s and preserves the reserved submission and named draft`,
    async (race) => {
      const s = await setup(undefined, undefined, undefined, 60_000);
      const before = fixture('loadout_patch.read_before_the_first_save');
      const after = fixture('loadout_restore.read_after_saved_cards_restore');
      await change(() => {
        if (row === 'C15-8') s.equipment.editRestoreCards('no_card');
        else {
          s.equipment.addRestoreModule();
          s.equipment.editRestoreModule('draft:0', 'module', 'black_hole_digestor');
          s.equipment.editRestoreModule('draft:0', 'slot', 'assist');
        }
      });
      const draft = s.entry()?.draft;
      s.answer('hold');
      s.fake.route('GET', '/api/config', () => 'hold');
      await change(s.equipment.save);
      const held = s.fake.next('/api/config', 'GET');
      if (race === 'selection')
        await change(() => {
          s.equipment.select('spare');
        });
      else if (race === 'refresh') {
        s.fake.route('GET', '/api/config', () => after);
        await change(s.equipment.refresh);
      } else if (race === 'new edit')
        await change(() => {
          s.equipment.editRestoreCards('main_farm');
        });
      await change(() => {
        held.respond(200, race === 'preflight revision' ? after.body : before.body);
      });
      expect(s.fake.writes()).toHaveLength(race === 'new edit' ? 1 : 0);
      if (race === 'new edit') {
        expect(own(JSON.parse(s.fake.writes()[0]?.body ?? ''), 'body')).toEqual(
          row === 'C15-8'
            ? { cards_restore: 'no_card' }
            : { modules_restore: [['black_hole_digestor', 'assist']] },
        );
        expect(s.entry()?.outcome).toBe('sending');
      }
      await change(() => {
        s.equipment.select('plain');
      });
      expect(s.entry()?.outcome).not.toBe('checking');
      expect(s.entry()?.dirty).toBe(true);
      expect(s.entry()?.draft).toEqual(
        race === 'new edit' ? { ...draft, restoreCards: 'main_farm' } : draft,
      );
      expect(screen.getByLabelText('Result for this loadout and target')).toBeVisible();
    },
  );
}

it('C15-8 saves the whole reviewed body with config revision/context and settles only on a later positive read', async () => {
  const s = await setup();
  await change(() => {
    s.equipment.edit('cards', 'main_farm');
    s.equipment.editRestoreCards('no_card');
  });
  expect(save()).toBeEnabled();
  await change(s.equipment.save);
  await waitFor(() => {
    expect(s.entry()?.outcome).toBe('saved');
  });
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
    fixture('loadout_restore.saved_cards_restore').request.body,
  );
  expect(s.entry()?.intent?.answer?.contract).toBe('exact');
  s.config('loadout_restore.read_after_saved_cards_restore');
  await change(s.equipment.refresh);
  expect(s.entry()?.outcome).toBe('readApplied');
  expect(s.fake.writes()).toHaveLength(1);
  expect(textOutsideCatalogue(s.mounted.view.container)).toEqual([]);
  expect(await axe(s.mounted.view.container)).toHaveNoViolations();
});

it('C15-8 a lost restore answer keeps Unknown through unchanged reads, preserves newer typing and never resends', async () => {
  const s = await setup();
  s.answer('hold');
  await change(() => {
    s.equipment.edit('cards', 'main_farm');
    s.equipment.editRestoreCards('no_card');
    s.equipment.save();
  });
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  await change(() => {
    s.equipment.editRestoreCards('main_farm');
    s.fake.next('/api/loadout-patch', 'POST').lose();
  });
  await waitFor(() => {
    expect(s.entry()?.outcome).toBe('unknown');
  });
  await change(s.equipment.refresh);
  expect(s.entry()?.outcome).toBe('unknown');
  s.config('loadout_restore.read_after_saved_cards_restore');
  await change(s.equipment.refresh);
  expect(s.entry()?.outcome).toBe('readApplied');
  expect(s.entry()?.draft.restoreCards).toBe('main_farm');
  expect(s.entry()?.dirty).toBe(true);
  expect(s.fake.writes()).toHaveLength(1);
});

it('C15-8 C15-9 C-04 explicit acceptance keeps Unknown history separate from positive readback and sends nothing', async () => {
  const s = await setup();
  s.answer('hold');
  await change(() => {
    s.equipment.editRestoreCards('no_card');
    s.equipment.save();
  });
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  await change(() => {
    s.fake.next('/api/loadout-patch', 'POST').lose();
  });
  await change(s.equipment.refresh);
  expect(s.entry()?.outcome).toBe('unknown');
  await change(s.equipment.reconcile);
  expect(s.entry()?.outcome).toBe('acceptedCurrent');
  expect(s.entry()?.intent?.accepted).toBe(true);
  expect(s.entry()?.intent?.readSettlement).toBeNull();
  await change(s.equipment.refresh);
  expect(s.entry()?.outcome).toBe('acceptedCurrent');
  expect(s.entry()?.reconcile).toBe(false);
  expect(s.fake.writes()).toHaveLength(1);
});

it.each(['cards', 'modules'])(
  'C15-8 C15-9 recorded %s restore refusal is verbatim and keeps the exact raw draft',
  async (kind) => {
    const s = await setup(
      kind === 'cards'
        ? 'loadout_restore.read_after_saved_cards_restore'
        : 'loadout_restore.read_after_saved_modules_restore',
      `loadout_restore.refused_${kind}_restore`,
    );
    await change(() => {
      if (kind === 'cards') s.equipment.editRestoreCards('ghost_deck');
      else s.equipment.editRestoreModule('saved:0', 'module', 'ghost_module');
    });
    await change(s.equipment.save);
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('refused');
    });
    const recorded = fixture(`loadout_restore.refused_${kind}_restore`);
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(recorded.request.body);
    expect(
      screen.getAllByText(String(own(recorded.body, 'error')), { exact: true }).length,
    ).toBeGreaterThan(0);
    expect(s.entry()?.dirty).toBe(true);
    expect(s.entry()?.intent?.answer?.contract).toBe('exact');
  },
);

it('C15-9 r12f restore-only first save matches the recorded body, warning and read-after', async () => {
  const s = await setup(
    'settings_modules_more.read_before_saved_modules_restore',
    'settings_modules_more_2.saved_modules_restore_only',
  );
  const recorded = fixture('settings_modules_more_2.saved_modules_restore_only');
  await change(s.equipment.addRestoreModule);
  expect(save()).toHaveAttribute('aria-disabled', 'true');
  const row = screen.getByRole('group', { name: 'Entry 1' });
  const moduleInput = within(row).getByRole('textbox', { name: /Module name/ });
  const slotInput = within(row).getByRole('textbox', { name: /Slot/ });
  expect(moduleInput).toHaveAttribute('aria-invalid', 'true');
  expect(slotInput).toHaveAttribute('aria-invalid', 'true');
  const errorId = moduleInput.getAttribute('aria-describedby');
  expect(errorId).toBe(slotInput.getAttribute('aria-describedby'));
  expect(errorId === null ? null : document.getElementById(errorId)).toHaveTextContent(
    'Use exactly one non-blank module name and slot',
  );
  fireEvent.change(within(row).getByRole('textbox', { name: /Module name/ }), {
    target: { value: 'black_hole_digestor' },
  });
  fireEvent.change(within(row).getByRole('textbox', { name: /Slot/ }), {
    target: { value: 'assist' },
  });
  expect(save()).toBeEnabled();
  expect(moduleInput).not.toHaveAttribute('aria-invalid');
  expect(slotInput).not.toHaveAttribute('aria-invalid');
  await change(s.equipment.save);
  await waitFor(() => {
    expect(s.entry()?.outcome).toBe('saved');
  });
  const sent = JSON.parse(s.fake.writes()[0]?.body ?? '{}') as { body: unknown };
  expect(sent).toEqual(recorded.request.body);
  for (const warning of list(own(recorded.body, 'warnings')) ?? []) {
    expect(screen.getByText(String(warning), { exact: true })).toBeVisible();
  }
  expect(own(sent.body, 'modules')).toBeUndefined();
  s.config('settings_modules_more_2.read_after_saved_modules_restore_only');
  await change(s.equipment.refresh);
  expect(s.entry()?.outcome).toBe('readApplied');
  expect(moduleInput).toHaveValue('black_hole_digestor');
  expect(slotInput).toHaveValue('assist');
  expect(s.fake.writes()).toHaveLength(1);
});

it('C15-9 C-03 changing saved restore rows preserves every recorded manual field without conversion', async () => {
  const read = 'loadout_restore.read_after_saved_modules_restore';
  const s = await setup(read, 'loadout_restore.saved_modules_restore');
  s.answer('hold');
  await change(() => {
    s.equipment.removeRestoreModule('saved:0');
    s.equipment.addRestoreModule();
  });
  const row = screen.getByRole('group', { name: 'Entry 1' });
  fireEvent.change(within(row).getByRole('textbox', { name: /Module name/ }), {
    target: { value: 'black_hole_digestor' },
  });
  fireEvent.change(within(row).getByRole('textbox', { name: /Slot/ }), {
    target: { value: 'primary' },
  });
  expect(save()).toBeEnabled();
  await change(s.equipment.save);
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  const recorded = fixture('loadout_restore.saved_modules_restore');
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual({
    ...Object.fromEntries(entries(recorded.request.body)),
    expected_revision: own(fixture(read).body, 'config_revision'),
    body: {
      ...Object.fromEntries(entries(own(recorded.request.body, 'body'))),
      modules_restore: [['black_hole_digestor', 'primary']],
    },
  });
  await change(() => {
    s.fake.next('/api/loadout-patch', 'POST').lose();
  });
  expect(s.entry()?.outcome).toBe('unknown');
  expect(screen.getByText(/Manual equipment selections are preserved here/)).toBeVisible();
});

it('C15-8 C15-9 restore_loadout_unused disables changed and cleared restore values while ordinary unused saves remain possible', async () => {
  const s = await setup();
  await change(() => {
    s.equipment.select('spare');
    s.equipment.editRestoreCards('main_farm');
  });
  expect(save()).toHaveAttribute('aria-disabled', 'true');
  expect(
    screen.getAllByText(
      'Restore changes cannot be saved: use this loadout in a run of the active profile first.',
    ).length,
  ).toBeGreaterThan(0);
  await change(s.equipment.save);
  expect(s.fake.writes()).toHaveLength(0);
  await change(s.equipment.cancel);
  await change(() => {
    s.equipment.edit('cards', 'main_farm');
  });
  expect(save()).toBeEnabled();
  s.answer(
    'loadout_restore_eligibility.unused_loadout_saved_without_restore_values_is_unchanged_behaviour',
  );
  await change(s.equipment.save);
  await waitFor(() => {
    expect(s.entry()?.outcome).toBe('saved');
  });
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
    fixture(
      'loadout_restore_eligibility.unused_loadout_saved_without_restore_values_is_unchanged_behaviour',
    ).request.body,
  );
  s.config('loadout_restore_eligibility.read_after_the_clearing_refusal_the_stored_value_remains');
  await change(s.equipment.refresh);
  await change(() => {
    s.equipment.editRestoreCards('');
  });
  expect(save()).toHaveAttribute('aria-disabled', 'true');
});

it('C15-8 C-03 unchanged stored restore values do not block an ordinary unused-loadout save', async () => {
  const reference =
    'loadout_restore_eligibility.unused_loadout_saved_with_its_stored_restore_value_unchanged';
  const saved = fixture(reference);
  const s = await setup('settings_modules_more.read_before_the_clearing_refusal', reference);
  s.answer('hold');
  await change(() => {
    s.equipment.select('spare');
    s.equipment.edit('cards', 'no_card');
  });
  expect(save()).toBeEnabled();
  await change(s.equipment.save);
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual({
    ...Object.fromEntries(entries(saved.request.body)),
    expected_revision: own(
      fixture('settings_modules_more.read_before_the_clearing_refusal').body,
      'config_revision',
    ),
    body: { cards: 'no_card', cards_restore: 'ghost_deck' },
  });
  // Transport hold: there is no recorded receipt for this exact ordinary edit.
  // The stored restore value is preserved in the request; no success is invented.
});

it.each(['cards', 'modules'])(
  'C15-8 C15-9 F6-15-R1 empty blueprints disable %s restore Save with an associated visible reason and zero POSTs',
  async (kind) => {
    const family = 'settings_modules_more_2.';
    const s = await setup(
      family + 'read_config_after_the_refusals_nothing_changed',
      family + 'restore_refused_for_a_profile_with_an_empty_blueprints_mapping',
      family + 'profile_src_with_an_empty_blueprints_mapping',
    );
    await change(() => {
      if (kind === 'cards') s.equipment.editRestoreCards('no_card');
      else {
        s.equipment.addRestoreModule();
        s.equipment.editRestoreModule('draft:0', 'module', 'black_hole_digestor');
        s.equipment.editRestoreModule('draft:0', 'slot', 'assist');
      }
      s.equipment.edit('cards', 'main_farm');
    });
    expect(save()).toHaveAttribute('aria-disabled', 'true');
    const reasonId = save().getAttribute('aria-describedby');
    const reason = reasonId === null ? null : document.getElementById(reasonId);
    expect(reason).toBeVisible();
    expect(reason).toHaveTextContent(
      'Restore changes cannot be saved: use this loadout in a run of the active profile first.',
    );
    // settings_modules_more_2.restore_refused_for_a_profile_with_an_empty_blueprints_mapping
    // records restore_loadout_unused; use the existing catalogue reason locally.
    expect(
      own(
        fixture(family + 'restore_refused_for_a_profile_with_an_empty_blueprints_mapping').body,
        'code',
      ),
    ).toBe('restore_loadout_unused');
    fireEvent.click(save());
    await change(s.equipment.save);
    expect(s.fake.writes()).toHaveLength(0);
    expect(s.entry()?.dirty).toBe(true);
    expect(textOutsideCatalogue(s.mounted.view.container)).toEqual([]);
    await change(s.equipment.cancel);
    await change(() => {
      s.equipment.edit('cards', 'main_farm');
    });
    expect(save()).toBeEnabled();
    s.answer('hold'); // No recorded receipt for this ordinary edit: check the send only.
    await change(s.equipment.save);
    expect(s.fake.writes()).toHaveLength(1);
    expect(own(JSON.parse(s.fake.writes()[0]?.body ?? ''), 'body')).toEqual({ cards: 'main_farm' });
  },
);

it.each(['without_blueprints', 'with_blueprints_null', 'with_blueprints_a_list'])(
  'C15-8 C15-9 F6-15-R1 profile %s defers to the verbatim recorded refusal and retains the draft',
  async (shape) => {
    const family = 'settings_modules_more_2.';
    const reference = family + 'restore_refused_for_a_profile_' + shape;
    const s = await setup(
      family + 'read_config_after_the_refusals_nothing_changed',
      reference,
      family + 'profile_src_' + shape,
    );
    await change(() => {
      s.equipment.edit('cards', 'main_farm');
      s.equipment.editRestoreCards('no_card');
    });
    expect(save()).toBeEnabled();
    const draft = s.entry()?.draft;
    await change(s.equipment.save);
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('refused');
    });
    const recorded = fixture(reference);
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(recorded.request.body);
    expect(screen.getByText(String(own(recorded.body, 'error')), { exact: true })).toBeVisible();
    expect(s.entry()?.intent?.answer?.contract).toBe('exact');
    await change(s.equipment.refresh);
    expect(s.entry()?.outcome).toBe('refused');
    expect(s.entry()?.draft).toEqual(draft);
    expect(s.entry()?.dirty).toBe(true);
    expect(s.fake.writes()).toHaveLength(1);
  },
);

it('C15-8 r12f ordinary save without blueprints shows the recorded validator refusal and saves nothing', async () => {
  const family = 'settings_modules_more_2.';
  const reference = family + 'loadout_save_refused_for_a_profile_without_blueprints';
  const s = await setup(
    family + 'read_config_after_the_refusals_nothing_changed',
    reference,
    family + 'profile_src_without_blueprints',
  );
  await change(() => {
    s.equipment.edit('cards', 'no_card');
  });
  expect(save()).toBeEnabled();
  const draft = s.entry()?.draft;
  await change(s.equipment.save);
  await waitFor(() => {
    expect(s.entry()?.outcome).toBe('refused');
  });
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(fixture(reference).request.body);
  expect(
    screen.getAllByText(String(own(fixture(reference).body, 'error')), { exact: true })[0],
  ).toBeVisible();
  await change(s.equipment.refresh);
  expect(s.entry()?.outcome).toBe('refused');
  expect(s.entry()?.draft).toEqual(draft);
  expect(s.fake.writes()).toHaveLength(1);
});

it('C15-8 no active profile shows its catalogue reason without sending a save', async () => {
  const s = await setup(
    'loadout_restore_eligibility.read_after_the_no_profile_refusal_nothing_changed',
  );
  await change(() => {
    s.equipment.editRestoreCards('main_farm');
  });
  expect(save()).toHaveAttribute('aria-disabled', 'true');
  expect(
    screen.getAllByText(
      'Restore changes cannot be saved: activate a profile that uses this loadout first.',
    ).length,
  ).toBeGreaterThan(0);
  await change(s.equipment.save);
  expect(s.fake.writes()).toHaveLength(0);
});

it('C15-8 C15-9 stale revision is judged before the restore eligibility guard', async () => {
  const s = await setup();
  await change(() => {
    s.equipment.select('spare');
    s.equipment.editRestoreCards('main_farm');
  });
  s.config('loadout_restore.read_after_saved_cards_restore');
  await change(s.equipment.refresh);
  const vm = selectEquipment(s.equipment.getSnapshot(), Date.now());
  expect(vm.canSave).toBe(false);
  expect(vm.reconcile).toBe(true);
  expect(vm.restore.blocked).toBeNull();
  await change(s.equipment.save);
  expect(s.fake.writes()).toHaveLength(0);
  const stale = fixture(
    'loadout_restore_eligibility.stale_revision_is_judged_before_the_restore_guard',
  );
  expect(own(stale.body, 'code')).toBe('revision_conflict');
});

it('C15-6 C15-7 tournament form uses raw labelled list rows, explicit add/remove, catalogue copy and account/module draft scopes', async () => {
  const s = await setup();
  const recorded = fixture('settings.populated_account_every_module');
  const state = addBody(regionState(), 'settings', recorded.body);
  s.mounted.setState(state);
  s.mounted.commands.editDraft.mockImplementation((scope, path, value) => {
    s.mounted.store.dispatch({ type: 'draft_edit', scope, path, value });
  });
  const form = screen.getByRole('group', { name: 'Standing tournament deck changes' });
  const module = (list(own(recorded.body, 'modules')) ?? []).find(
    (module) => own(module, 'id') === 'tournament-cards',
  );
  const label = String(
    own(
      (list(own(module, 'fields')) ?? []).find((field) => own(field, 'path') === 'drop'),
      'label',
    ),
  );
  expect(within(form).getByRole('group', { name: label })).toBeVisible();
  const add = within(form).getByRole('button', { name: `Add entry ${label}` });
  fireEvent.click(add);
  const input = within(form).getByRole('textbox', { name: `${label} Entry 1` });
  fireEvent.change(input, { target: { value: ' unknown ' } });
  expect(input).toHaveValue(' unknown ');
  fireEvent.click(
    within(form).getByRole('button', {
      name: (name) => name.startsWith(`Remove entry ${label} Entry 1`),
    }),
  );
  expect(within(form).queryByRole('textbox')).toBeNull();
  expect(settingsScope(state.context, 'tournament-cards')).not.toBe(
    settingsScope({ ...s.context, account: 'another' }, 'tournament-cards'),
  );
  expect(settingsScope(state.context, 'tournament-cards')).not.toBe(
    settingsScope(state.context, 'loop'),
  );
  expect(textOutsideCatalogue(s.mounted.view.container)).toEqual([]);
  expect(await axe(s.mounted.view.container)).toHaveNoViolations();
});

it('C15-6 C15-7 r12f malformed saved members survive sibling editing until explicitly repaired or removed', async () => {
  const s = await setup();
  const recorded = fixture(
    'settings_modules_more_2.read_after_the_add_list_was_saved_the_malformed_drop_list_remains',
  );
  s.mounted.setState(
    addBody(
      { ...regionState(), context: { ...s.context, account: 'alpha' } },
      'settings',
      recorded.body,
    ),
  );
  s.mounted.commands.editDraft.mockImplementation((scope, path, value) => {
    s.mounted.store.dispatch({ type: 'draft_edit', scope, path, value });
  });
  const form = screen.getByRole('group', { name: 'Standing tournament deck changes' });
  const inputs = within(form).getAllByRole('textbox');
  expect(inputs[0]).toHaveValue('false');
  expect(inputs[1]).toHaveValue('7');
  fireEvent.change(inputs[2] ?? form, { target: { value: 'keep' } });
  expect(inputs[0]).toHaveValue('false');
  expect(inputs[1]).toHaveValue('7');
  expect(within(form).getByRole('button', { name: 'Save' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  for (let i = 0; i < 2; i += 1) {
    fireEvent.click(within(form).getAllByRole('button', { name: /^Remove entry/ })[0] ?? form);
  }
  fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
  const command = s.mounted.commands.saveSettings.mock.calls[0]?.[0];
  const request = fixture(
    'settings_modules_more_2.saved_tournament_cards_repairing_the_malformed_drop_list',
  ).request.body;
  expect(command).toMatchObject({
    module: own(request, 'module'),
    revision: own(request, 'revision'),
    values: own(request, 'values'),
    settingsContext: own(request, 'context'),
  });
});

it('C15-6 C15-7 recorded invalid list/stale errors remain verbatim; only later matching module/context reads settle saved lists', () => {
  const saved = fixture('settings_modules.saved_tournament_cards');
  const sent = record(saved.request.body);
  if (sent === undefined) throw new Error('request');
  const input = {
    module: 'tournament-cards',
    revision: String(own(sent, 'revision')),
    values: Object.fromEntries(entries(own(sent, 'values'))),
  };
  for (const key of ['invalid_string_list_tournament_cards', 'stale_revision_tournament_cards']) {
    const refusal = fixture(`settings_modules.${key}`);
    expect(settingsSaveAnswer(refusal, input)).toMatchObject({
      kind: 'refused',
      error: own(refusal.body, 'error'),
    });
  }
  expect(
    settleSettingsSave(fixture('settings_modules.read_after_saved_tournament_cards').body, input, {
      instance: 'main',
      account: 'alpha',
      profile: 'mine',
    }),
  ).toBe('applied');
  expect(
    settleSettingsSave(fixture('settings.populated_account_every_module').body, input, {
      instance: 'main',
      account: 'alpha',
      profile: 'mine',
    }),
  ).toBeNull();
});
