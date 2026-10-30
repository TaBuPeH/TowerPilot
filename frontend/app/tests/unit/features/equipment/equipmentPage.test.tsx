import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, describe, expect, it } from 'vitest';
import { createEquipmentApi } from '../../../../src/api/equipment.ts';
import {
  contextFromConfig,
  expectedContext,
  type PageContext,
} from '../../../../src/api/context.ts';
import { own, ownPath, record } from '../../../../src/core/read.ts';
import { EquipmentPage } from '../../../../src/features/equipment/EquipmentPage.tsx';
import {
  createEquipmentStore,
  currentEquipment,
  currentLoadout,
} from '../../../../src/store/equipment.ts';
import { equipmentBody } from '../../../../src/store/equipmentDraft.ts';
import { selectEquipment } from '../../../../src/store/selectors/equipment.ts';
import { initialDashboardState } from '../../../../src/store/dashboardStore.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../../scripts/contract-fixture.mjs';
import { EQUIPMENT_FIXTURE_PATH } from '../../../support/equipment-fixtures.mjs';
import { familySchemas } from '../../../../src/contract/generated/dashboard_contract_pages_v1_equipment.543d7f37.ts';
import { createFakeTransport, createMemoryStorage } from '../../../support/fakeTransport.ts';
import { renderRegion } from '../dashboard/harness.tsx';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import type { DashboardCase } from '../../../support/dashboardFixture.ts';

const CASES = own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases');
const SAMPLES = own(loadJsonWithIdentity(EQUIPMENT_FIXTURE_PATH).fixture, 'cases');
function fixture(reference: string): DashboardCase {
  const [family = '', key = ''] = reference.split('.');
  const raw = ownPath(family.startsWith('equipment_') ? SAMPLES : CASES, family, key);
  if (raw === undefined) throw new Error(`No real-route fixture ${reference}`);
  return structuredClone(raw) as DashboardCase;
}
function requestWithContext(reference: string, context: PageContext) {
  const body = record(fixture(reference).request.body);
  if (body === undefined) throw new Error(`Missing request body ${reference}`);
  return { ...body, expected_context: expectedContext(context) };
}
const cleanups: (() => void)[] = [];
afterEach(() => {
  for (const fn of cleanups.splice(0)) fn();
});
async function change(action: () => void | Promise<void>) {
  await act(async () => {
    await action();
  });
}
async function setup(
  options: {
    view?: string;
    config?: string;
    profile?: string;
    save?: string;
    storage?: ReturnType<typeof createMemoryStorage>;
    mount?: boolean;
    windowMs?: number;
  } = {},
) {
  const fake = createFakeTransport();
  let config =
    options.config ??
    (options.view === undefined
      ? 'loadout_patch.read_before_the_first_save'
      : `equipment_samples.${options.view}_config`);
  let profile =
    options.profile ??
    (options.view === undefined
      ? 'ability_policies.read_before_the_first_save'
      : `equipment_samples.${options.view}_profile`);
  let save = options.save ?? 'loadout_patch.global_preset_selected';
  fake.route('GET', '/api/config', () => (config === 'hold' ? 'hold' : fixture(config)));
  fake.route('GET', /\/api\/profile-src\//, () => (profile === 'hold' ? 'hold' : fixture(profile)));
  fake.route('POST', '/api/loadout-patch', () => (save === 'hold' ? 'hold' : fixture(save)));
  const storage = options.storage ?? createMemoryStorage();
  const api = createEquipmentApi(fake.transport, storage, options.windowMs ?? 100);
  const equipment = createEquipmentStore(api);
  cleanups.push(equipment.dispose);
  const context = contextFromConfig(fixture(config).body);
  if (context === null) throw new Error('Fixture context missing');
  await change(() => {
    equipment.setTarget(context, 1);
  });
  await waitFor(() => {
    expect(currentEquipment(equipment.getSnapshot())?.busy).toBe(false);
  });
  const mounted =
    options.mount === false
      ? null
      : renderRegion(initialDashboardState(), <EquipmentPage equipment={equipment} />);
  return {
    equipment,
    api,
    fake,
    storage,
    context,
    mounted,
    entry: () => currentLoadout(equipment.getSnapshot()),
    readback: (ref: string) => {
      config = ref;
    },
    source: (ref: string) => {
      profile = ref;
    },
    answer: (ref: string) => {
      save = ref;
    },
  };
}
const saveButton = () => screen.getByRole('button', { name: 'Save selection' });
const selectLoadout = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const editor = () => screen.getByRole('region', { name: /^Selected loadout/ });
function chooseGlobal() {
  fireEvent.change(screen.getByLabelText('Selection mode'), { target: { value: 'global' } });
  fireEvent.change(screen.getByLabelText('Global preset selection'), {
    target: { value: 'Farm Run' },
  });
}

describe('Equipment Wanted rows', () => {
  it('keeps exact revisions available for selection and preserves a non-color selected-loadout cue without writes', async () => {
    const s = await setup();
    selectLoadout('plain');
    const vm = selectEquipment(s.equipment.getSnapshot(), Date.now());
    const revisions = editor().querySelectorAll('.tp-equipment__revision');
    expect(revisions).toHaveLength(2);
    const user = userEvent.setup();
    for (const [index, expected] of [vm.revision, vm.base].entries()) {
      if (expected === null) throw new Error('Fixture has no revision');
      const disclosure = revisions[index];
      if (!(disclosure instanceof HTMLDetailsElement))
        throw new Error('Missing revision disclosure');
      expect(disclosure.open).toBe(false);
      const full = disclosure.querySelector('.tp-equipment__revision-full bdi');
      expect(full).toHaveTextContent(expected);
      expect(full).not.toBeVisible();
      await user.click(within(disclosure).getByText('Full value', { exact: true }));
      expect(full).toBeVisible();
      expect(full?.textContent).toBe(expected);
    }
    const selected = screen.getByRole('button', { name: 'plain' });
    expect(selected).toHaveAttribute('aria-pressed', 'true');
    const selectedRow = selected.closest('.tp-equipment__card');
    if (!(selectedRow instanceof HTMLElement)) throw new Error('Missing selected row');
    expect(within(selectedRow).getByText('Selected', { exact: true })).toBeVisible();
    expect(
      screen.getByText(
        'Leave untouched keeps the current game selection; it does not select None.',
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        'Legacy installation settings; separate from the saved-loadout target below.',
      ),
    ).toBeVisible();
    expect(s.fake.writes()).toHaveLength(0);
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });
  it('E4 r10 matching expected_context saves the exact captured request and reads the persisted selection', async () => {
    const s = await setup({
      config: 'equipment_samples.context_config',
      profile: 'equipment_samples.context_profile',
      save: 'equipment_samples.context_match_saved',
    });
    selectLoadout('plain');
    chooseGlobal();
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      fixture('equipment_samples.context_match_saved').request.body,
    );
    expect(s.entry()?.intent?.answer?.contract).toBe('exact');
    s.readback('equipment_samples.context_match_readback');
    await change(s.equipment.refresh);
    expect(s.entry()?.outcome).toBe('readApplied');
    expect(s.entry()?.dirty).toBe(false);
    expect(s.fake.writes()).toHaveLength(1);
  });
  it.each(['null', 'missing_fields', 'extra_field', 'wrong_value_type', 'not_an_object'])(
    'E4 r10 malformed expected_context %s is a definite refusal with the draft kept and no resend',
    async (name) => {
      const s = await setup({
        config: 'equipment_samples.context_config',
        profile: 'equipment_samples.context_profile',
        save: `equipment_samples.context_malformed_${name}`,
      });
      selectLoadout('plain');
      chooseGlobal();
      fireEvent.click(saveButton());
      await waitFor(() => {
        expect(s.entry()?.outcome).toBe('refused');
      });
      expect(s.entry()?.intent?.answer?.contract).toBe('exact');
      expect(own(s.entry()?.intent?.answer?.body, 'code')).toBe('invalid_request');
      expect(s.entry()?.draft.values.global_preset).toBe('Farm Run');
      expect(s.entry()?.dirty).toBe(true);
      expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
        requestWithContext('equipment_samples.context_match_saved', s.context),
      );
      await change(s.equipment.refresh);
      expect(s.entry()?.draft.values.global_preset).toBe('Farm Run');
      expect(s.fake.writes()).toHaveLength(1);
    },
  );
  it('E4 r10 a server context_changed after the click-time read shows Not saved and current context, retaining the draft through reload', async () => {
    const config = 'equipment_samples.context_changed_config';
    const profile = 'equipment_samples.context_changed_profile';
    const s = await setup({ config, profile, save: 'equipment_samples.context_changed_refused' });
    selectLoadout('plain');
    fireEvent.change(screen.getByLabelText('Selection mode'), { target: { value: 'category' } });
    fireEvent.change(screen.getByLabelText('Cards'), { target: { value: 'main_farm' } });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('contextChanged');
    });
    expect(s.entry()?.intent?.answer?.contract).toBe('exact');
    expect(screen.getByText('Not saved: the account or profile changed')).toBeVisible();
    expect(s.entry()?.refusalContext).toEqual(
      own(fixture('equipment_samples.context_changed_refused').body, 'current_context'),
    );
    expect(screen.getByText('Current context reported by the server')).toBeVisible();
    expect(s.entry()?.draft.values.cards).toBe('main_farm');
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      fixture('equipment_samples.context_changed_refused').request.body,
    );
    expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
    expect(s.fake.writes()).toHaveLength(1);
    s.mounted?.view.unmount();
    s.equipment.dispose();
    const restored = await setup({ config, profile, storage: s.storage });
    expect(restored.entry()?.draft.values.cards).toBe('main_farm');
    expect(restored.entry()?.outcome).toBe('contextChanged');
    expect(restored.fake.writes()).toHaveLength(0);
  });
  it('E4 r10 the common refusal with an explicit null current_context stays visible through reload without resending', async () => {
    const config = 'equipment_samples.context_changed_config';
    const profile = 'equipment_samples.context_changed_profile';
    // The backend recorded this common fence refusal on its tier route; keep that
    // provenance while exercising Equipment's handling of the shared response shape.
    const s = await setup({ config, profile, save: 'equipment_samples.context_unknown_refused' });
    selectLoadout('plain');
    fireEvent.change(screen.getByLabelText('Selection mode'), { target: { value: 'category' } });
    fireEvent.change(screen.getByLabelText('Cards'), { target: { value: 'main_farm' } });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('contextChanged');
    });
    expect(s.entry()?.intent?.answer?.contract).toBe('exact');
    expect(s.entry()?.refusalContext).toBeNull();
    expect(screen.getByText('Not saved: the account or profile changed')).toBeVisible();
    expect(screen.getByText('Current context reported by the server')).toBeVisible();
    expect(screen.getByText('null', { selector: '.tp-equipment__result *' })).toBeVisible();
    expect(s.entry()?.draft.values.cards).toBe('main_farm');
    expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
    expect(s.fake.writes()).toHaveLength(1);
    s.mounted?.view.unmount();
    s.equipment.dispose();
    const restored = await setup({ config, profile, storage: s.storage });
    expect(restored.entry()?.outcome).toBe('contextChanged');
    expect(restored.entry()?.draft.values.cards).toBe('main_farm');
    expect(screen.getByText('Current context reported by the server')).toBeVisible();
    expect(screen.getByText('null', { selector: '.tp-equipment__result *' })).toBeVisible();
    expect(restored.fake.writes()).toHaveLength(0);
  });
  it('E4 reserves one click-time submission through the context check and saves it while newer typing stays a draft', async () => {
    const s = await setup({ save: 'hold' });
    selectLoadout('plain');
    chooseGlobal();
    const before = s.fake.requests.filter((request) => request.path === '/api/config').length;
    s.readback('hold');
    fireEvent.click(saveButton());
    fireEvent.click(saveButton());
    expect(s.entry()?.outcome).toBe('checking');
    expect(s.fake.writes()).toHaveLength(0);
    expect(s.fake.requests.filter((request) => request.path === '/api/config')).toHaveLength(
      before + 1,
    );
    fireEvent.change(screen.getByLabelText('Global preset selection'), {
      target: { value: 'Tournament' },
    });
    await change(() => {
      const answer = fixture('loadout_patch.read_before_the_first_save');
      s.fake.next('/api/config').respond(answer.status, answer.body);
    });
    expect(s.fake.writes()).toHaveLength(1);
    expect(s.entry()?.outcome).toBe('sending');
    expect(s.entry()?.draft.values.global_preset).toBe('Tournament');
    expect(s.entry()?.submission?.body).toEqual({ global_preset: 'Farm Run' });
    expect(own(JSON.parse(s.fake.writes()[0]?.body ?? ''), 'body')).toEqual({
      global_preset: 'Farm Run',
    });
    screen.getByLabelText('Global preset selection').focus();
    await change(() => {
      const answer = fixture('loadout_patch.global_preset_selected');
      s.fake.next('/api/loadout-patch').respond(answer.status, answer.body);
    });
    expect(screen.getByText(/Selection saved in configuration/)).toBeVisible();
    expect(screen.getByLabelText('Global preset selection')).toHaveFocus();
    expect(screen.getByLabelText('Global preset selection')).toHaveValue('Tournament');
    expect(s.entry()?.dirty).toBe(true);
    expect(s.entry()?.original).toEqual({ global_preset: 'Farm Run' });
    expect(s.fake.writes()).toHaveLength(1);
  });
  it.each(['selection', 'revision'])(
    'E4 a changed %s during the click-time check cancels the reserved submission',
    async (changed) => {
      const s = await setup();
      selectLoadout('plain');
      chooseGlobal();
      s.readback('hold');
      fireEvent.click(saveButton());
      if (changed === 'selection') selectLoadout('spare');
      await change(() => {
        const answer = fixture(
          changed === 'revision'
            ? 'loadout_patch.read_after_the_saves_holds_every_loadout'
            : 'loadout_patch.read_before_the_first_save',
        );
        s.fake.next('/api/config').respond(answer.status, answer.body);
      });
      const reserved =
        s.equipment.getSnapshot().targets[s.api.targetKey(s.context)]?.editors['plain'];
      expect(reserved?.outcome).toBe(changed === 'selection' ? 'selectionChanged' : 'conflict');
      expect(reserved?.draft.values.global_preset).toBe('Farm Run');
      expect(s.fake.writes()).toHaveLength(0);
    },
  );
  it('E4 r10 a changed click-time context keeps the original raw draft, shows the current context, and sends nothing', async () => {
    const s = await setup();
    selectLoadout('plain');
    chooseGlobal();
    s.readback('config_state.effective_view_of_the_other_account');
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('contextChanged');
    });
    expect(screen.getByText('Not saved: the account or profile changed')).toBeVisible();
    expect(screen.getByText('Current context reported by the server')).toBeVisible();
    expect(s.entry()?.refusalContext).toEqual(
      contextFromConfig(fixture('config_state.effective_view_of_the_other_account').body),
    );
    expect(s.entry()?.draft.values.global_preset).toBe('Farm Run');
    expect(s.entry()?.dirty).toBe(true);
    expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E4 r10 an unreadable click-time context preserves the draft and sends nothing', async () => {
    const s = await setup();
    selectLoadout('plain');
    chooseGlobal();
    s.readback('config_state.file_missing_and_no_example_500');
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('contextUnconfirmed');
    });
    expect(s.entry()?.draft.values.global_preset).toBe('Farm Run');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E4 r10 changing the page context while the click-time read is pending prevents its POST', async () => {
    const s = await setup({ mount: false });
    s.readback('hold');
    await change(() => {
      s.equipment.select('plain');
      s.equipment.mode('global');
      s.equipment.edit('global_preset', 'Farm Run');
      s.equipment.save();
      s.equipment.setTarget(null, 2);
    });
    expect(s.fake.writes()).toHaveLength(0);
    const original =
      s.equipment.getSnapshot().targets[s.api.targetKey(s.context)]?.editors['plain'];
    expect(original?.draft.values.global_preset).toBe('Farm Run');
    expect(original?.outcome).toBe('contextChanged');
  });
  it('E4 r10 disposal during the click-time read cannot send a late POST', async () => {
    const s = await setup({ mount: false });
    s.readback('hold');
    await change(() => {
      s.equipment.select('plain');
      s.equipment.mode('global');
      s.equipment.edit('global_preset', 'Farm Run');
      s.equipment.save();
      s.equipment.dispose();
    });
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E4 F6-EQUIPMENT-01 a delayed save answer cannot roll back a newer saved configuration read', async () => {
    const s = await setup({ save: 'hold', mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.mode('global');
      s.equipment.edit('global_preset', 'Farm Run');
      s.equipment.save();
    });
    const newer = fixture('loadout_patch.read_after_the_saves_holds_every_loadout');
    s.readback('loadout_patch.read_after_the_saves_holds_every_loadout');
    await change(s.equipment.refresh);
    const draft = structuredClone(s.entry()?.draft);
    await change(() => {
      const answer = fixture('loadout_patch.global_preset_selected');
      s.fake.next('/api/loadout-patch').respond(answer.status, answer.body);
    });
    expect(currentEquipment(s.equipment.getSnapshot())?.config).toEqual(newer.body);
    expect(s.entry()?.outcome).toBe('conflict');
    expect(s.entry()?.reconcile).toBe(true);
    expect(s.entry()?.checked).toBe(false);
    expect(s.entry()?.draft).toEqual(draft);
    expect(selectEquipment(s.equipment.getSnapshot(), Date.now()).canSave).toBe(false);
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('E1 E2 E4 F6-EQUIPMENT-02 __proto__ is selectable, saves, and restores its own raw draft', async () => {
    const s = await setup({
      config: 'equipment_edge.noop_readback',
      profile: 'equipment_edge.profile',
      save: 'equipment_edge.proto_saved',
    });
    selectLoadout('__proto__');
    chooseGlobal();
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(
      Object.hasOwn(currentEquipment(s.equipment.getSnapshot())?.editors ?? {}, '__proto__'),
    ).toBe(true);
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      requestWithContext('equipment_edge.proto_saved', s.context),
    );
    fireEvent.change(screen.getByLabelText('Global preset selection'), {
      target: { value: 'Tournament' },
    });
    s.mounted?.view.unmount();
    s.equipment.dispose();
    const restored = await setup({
      config: 'equipment_edge.proto_readback',
      profile: 'equipment_edge.profile',
      storage: s.storage,
    });
    expect(
      Object.hasOwn(currentEquipment(restored.equipment.getSnapshot())?.editors ?? {}, '__proto__'),
    ).toBe(true);
    expect(currentEquipment(restored.equipment.getSnapshot())?.selected).toBe('__proto__');
    expect(restored.entry()?.draft.values.global_preset).toBe('Tournament');
    expect(restored.entry()?.dirty).toBe(true);
    expect(restored.fake.writes()).toHaveLength(0);
  });
  it('E2 E4 an acknowledged no-op save can keep its revision and settles by an unchanged read', async () => {
    const s = await setup({
      config: 'equipment_edge.readback',
      profile: 'equipment_edge.profile',
      save: 'equipment_edge.noop_saved',
    });
    selectLoadout('constructor');
    fireEvent.change(screen.getByLabelText('Global preset selection'), {
      target: { value: 'Farm Run' },
    });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      requestWithContext('equipment_edge.noop_saved', s.context),
    );
    s.readback('equipment_edge.noop_readback');
    await change(s.equipment.refresh);
    expect(s.entry()?.outcome).toBe('readApplied');
    expect(s.entry()?.dirty).toBe(false);
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('E2 E4 arbitrary user-owned names including constructor survive fixture drift and save explicitly', async () => {
    const s = await setup({
      config: 'equipment_edge.config',
      profile: 'equipment_edge.profile',
      save: 'equipment_edge.saved',
    });
    selectLoadout('constructor');
    chooseGlobal();
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(s.entry()?.intent?.answer?.contract).toBe('drift');
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      requestWithContext('equipment_edge.saved', s.context),
    );
    s.readback('equipment_edge.readback');
    await change(s.equipment.refresh);
    expect(s.entry()?.outcome).toBe('readApplied');
    expect(s.entry()?.dirty).toBe(false);
    selectLoadout('toString');
    expect(s.entry()?.version).toBe(0);
    expect(s.entry()?.draft.values.cards).toBe('');
  });
  it('E4 a read started before the save answer cannot overwrite its newer revision or draft', async () => {
    const s = await setup({ save: 'hold', mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.mode('global');
      s.equipment.edit('global_preset', 'Farm Run');
      s.equipment.save();
    });
    await waitFor(() => {
      expect(s.fake.writes()).toHaveLength(1);
    });
    await change(() => {
      s.equipment.edit('global_preset', 'Tournament');
    });
    s.readback('hold');
    let reading: Promise<void> | undefined;
    await change(() => {
      reading = s.equipment.refresh();
    });
    const answer = fixture('loadout_patch.global_preset_selected');
    await change(() => {
      s.fake.next('/api/loadout-patch').respond(answer.status, answer.body);
    });
    s.readback('loadout_patch.read_before_the_first_save');
    await change(async () => {
      const original = fixture('loadout_patch.read_before_the_first_save');
      s.fake.next('/api/config').respond(original.status, original.body);
      await reading;
    });
    expect(own(currentEquipment(s.equipment.getSnapshot())?.config, 'config_revision')).toBe(
      own(answer.body, 'config_revision'),
    );
    expect(s.entry()?.draft.values.global_preset).toBe('Tournament');
    expect(s.entry()?.dirty).toBe(true);
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('E4 changing emulator/account/profile keeps each raw draft under its original target', async () => {
    const s = await setup({ mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.edit('cards', '  raw-not-owned  ');
    });
    const other = contextFromConfig(
      fixture('config_state.effective_view_of_the_other_account').body,
    );
    if (other === null) throw new Error('Missing other fixture context');
    s.readback('config_state.effective_view_of_the_other_account');
    s.source('profile_reads.profile_src_of_the_other_accounts_profile_is_not_refused');
    await change(() => {
      s.equipment.setTarget(other, 2);
    });
    expect(s.entry()?.draft.values.cards).toBe('');
    expect(s.entry()?.dirty).toBe(false);
    s.readback('loadout_patch.read_before_the_first_save');
    s.source('ability_policies.read_before_the_first_save');
    await change(() => {
      s.equipment.setTarget(s.context, 3);
    });
    expect(s.entry()?.draft.values.cards).toBe('  raw-not-owned  ');
    expect(s.entry()?.dirty).toBe(true);
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E1 lists sorted global/category/manual/placeholder/untouched bodies and saved run consumers', async () => {
    const s = await setup({ view: 'mixed' });
    expect(
      selectEquipment(s.equipment.getSnapshot(), Date.now()).rows.map((row) => row.key),
    ).toEqual(['categories', 'global_choice', 'later', 'plain', 'spare', 'untouched']);
    expect(screen.getByText('Placeholder — equips nothing')).toBeVisible();
    selectLoadout('plain');
    expect(screen.getByLabelText('Selection mode')).toHaveValue('manual');
    expect(screen.getByRole('link', { name: 'farm_deep' })).toHaveAttribute(
      'href',
      '#runs/saved/farm_deep',
    );
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E1 empty saved loadouts show a specific empty state without creating one', async () => {
    const s = await setup({ view: 'empty' });
    expect(screen.getByText(/No saved loadouts/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Save selection' })).toBeNull();
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E1 E2 keeps an undiscovered saved name selected with a repair link and blocked save', async () => {
    const s = await setup({ view: 'missing' });
    selectLoadout('plain');
    expect(screen.getByLabelText('Cards')).toHaveValue('undiscovered_saved_choice');
    expect(screen.getByLabelText('Cards')).toHaveAttribute('aria-invalid', 'true');
    expect(within(editor()).getByRole('link', { name: 'Open Recognition' })).toHaveAttribute(
      'href',
      '#recognition',
    );
    expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E1 F6PE-E01 affected runs use blueprint labels, preserve long labels, and fall back to keys', async () => {
    const s = await setup({ view: 'long' });
    const state = s.equipment.getSnapshot();
    const targetKey = state.key;
    const target = currentEquipment(state);
    if (targetKey === null || target === null) throw new Error('Missing Equipment target');
    const profile: unknown = structuredClone(target.profile);
    const longLabel = 'Night coins with a long display label '.repeat(20).trim();
    for (const [key, label] of [
      ['farm_deep', 'Night coins'],
      ['x', longLabel],
      ['shards', ''],
    ] as const) {
      const blueprint = record(ownPath(profile, 'profile', 'blueprints', key));
      if (blueprint === undefined) throw new Error(`Missing blueprint ${key}`);
      Reflect.set(blueprint, 'label', label);
      Reflect.set(blueprint, 'name', 'Not the display label');
    }
    await change(() => {
      s.equipment.update((current) => ({
        ...current,
        targets: { ...current.targets, [targetKey]: { ...target, profile } },
      }));
    });
    expect(
      selectEquipment(s.equipment.getSnapshot(), Date.now()).rows.find((r) => r.key === 'plain')
        ?.consumers,
    ).toHaveLength(7);
    expect(screen.getByRole('link', { name: 'Night coins' })).toHaveAttribute(
      'href',
      '#runs/saved/farm_deep',
    );
    expect(screen.getByRole('link', { name: longLabel })).toHaveAttribute('href', '#runs/saved/x');
    expect(screen.getByRole('link', { name: 'shards' })).toHaveAttribute(
      'href',
      '#runs/saved/shards',
    );
    expect(screen.queryByRole('link', { name: 'Not the display label' })).toBeNull();
    expect(screen.getByRole('link', { name: 'unbreakable'.repeat(40) })).toBeVisible();
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E2 sends one explicit global save with the configuration revision and honest saved feedback', async () => {
    const s = await setup();
    selectLoadout('plain');
    chooseGlobal();
    expect(s.fake.writes()).toHaveLength(0);
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(JSON.parse(s.fake.writes()[0]?.body ?? '')).toEqual(
      requestWithContext('loadout_patch.global_preset_selected', s.context),
    );
    expect(screen.getByText(/Selection saved in configuration/)).toBeVisible();
    expect(s.entry()?.dirty).toBe(false);
    expect(s.api.machine.resend(s.entry()?.intent?.id ?? '')).toBe('not_resendable');
  });
  it('E2 saves every category explicitly and omits untouched categories from the whole body', async () => {
    const s = await setup({ view: 'manual', mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.mode('global');
      s.equipment.edit('global_preset', 'Farm Run');
      s.equipment.approveConversion(true);
    });
    const steps = [
      ['global', { global_preset: 'Farm Run' }],
      ['categories', { module_preset: 'Farm', guardian_preset: 'Farm' }],
      ['cards', { cards: 'main_farm' }],
      ['workshop_bots', { workshop_preset: 'Full Upgraded', bot_preset: 'Farm' }],
    ] as const;
    for (const [name, body] of steps) {
      s.answer(`equipment_samples.manual_${name}_saved`);
      await change(() => {
        s.equipment.mode(name === 'global' ? 'global' : 'category');
        for (const field of [
          'cards',
          'module_preset',
          'guardian_preset',
          'workshop_preset',
          'bot_preset',
        ] as const)
          s.equipment.edit(
            field,
            typeof own(body, field) === 'string' ? String(own(body, field)) : '',
          );
        if (name === 'global') s.equipment.approveConversion(true);
        s.equipment.save();
      });
      expect(s.entry()?.outcome).toBe('saved');
      expect(JSON.parse(s.fake.writes().at(-1)?.body ?? '')).toEqual(
        requestWithContext(`equipment_samples.manual_${name}_saved`, s.context),
      );
      // The backend's next click-time config must carry this save's recorded revision.
      s.readback(`equipment_samples.manual_${name}_readback`);
    }
    expect(s.fake.writes()).toHaveLength(4);
  });
  it('E2 all-empty and invalid raw names never insert a default or send', async () => {
    const s = await setup();
    selectLoadout('plain');
    await change(() => {
      s.equipment.edit('cards', '  not-owned\t');
    });
    expect(screen.getByLabelText('Cards')).toHaveValue('  not-owned\t');
    expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
    await change(() => {
      s.equipment.edit('cards', '');
      s.equipment.save();
    });
    expect(s.entry()?.draft.values.cards).toBe('');
    expect(s.fake.writes()).toHaveLength(0);
    expect(screen.getByText(/Choose at least one preset to save this loadout/)).toBeVisible();
  });
  it('E3 manual conversion requires removal review; Cancel and reopen retain every original field', async () => {
    const s = await setup({ view: 'manual', save: 'equipment_samples.manual_global_saved' });
    selectLoadout('plain');
    const original = structuredClone(s.entry()?.original);
    chooseGlobal();
    expect(screen.getByRole('heading', { name: 'Fields removed by this selection' })).toBeVisible();
    expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel edits' }));
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel edits' }),
    );
    expect(s.entry()?.original).toEqual(original);
    expect(s.entry()?.draft.mode).toBe('manual');
    selectLoadout('spare');
    selectLoadout('plain');
    expect(s.entry()?.original).toEqual(original);
    chooseGlobal();
    fireEvent.click(screen.getByLabelText('I reviewed the removed manual fields'));
    expect(saveButton()).not.toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByLabelText('I reviewed the removed manual fields'));
    expect(screen.getByLabelText('I reviewed the removed manual fields')).not.toBeChecked();
    expect(saveButton()).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByLabelText('I reviewed the removed manual fields'));
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('saved');
    });
    expect(own(JSON.parse(s.fake.writes()[0]?.body ?? ''), 'body')).toEqual({
      global_preset: 'Farm Run',
    });
  });
  it('E4 old answers retain newer typing and settle only their named loadout across navigation', async () => {
    const s = await setup({ save: 'hold' });
    selectLoadout('plain');
    chooseGlobal();
    fireEvent.click(saveButton());
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.fake.writes()).toHaveLength(1);
    });
    fireEvent.change(screen.getByLabelText('Global preset selection'), {
      target: { value: 'Tournament' },
    });
    selectLoadout('spare');
    await waitFor(() => {
      expect(s.fake.writes()).toHaveLength(1);
    });
    await change(() => {
      const answer = fixture('loadout_patch.global_preset_selected');
      s.fake.next('/api/loadout-patch').respond(answer.status, answer.body);
    });
    expect(s.entry()?.outcome).toBeNull();
    selectLoadout('plain');
    expect(screen.getByLabelText('Global preset selection')).toHaveValue('Tournament');
    expect(s.entry()?.dirty).toBe(true);
    expect(s.fake.writes()).toHaveLength(1);
    expect(own(own(s.entry(), 'submission'), 'body')).toEqual({ global_preset: 'Farm Run' });
  });
  it.each([
    'stale_revision_409',
    'action_lock_another_action_in_progress',
    'validator_unavailable_500',
    'write_failed_nothing_saved_500',
  ])('E4 %s retains the raw draft and offers a read without retry', async (name) => {
    const s = await setup({ save: `loadout_patch.${name}` });
    selectLoadout('plain');
    fireEvent.change(screen.getByLabelText('Cards'), { target: { value: 'main_farm' } });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).not.toBe('checking');
      expect(s.entry()?.outcome).not.toBe('sending');
    });
    expect(screen.getByLabelText('Cards')).toHaveValue('main_farm');
    expect(s.entry()?.dirty).toBe(true);
    expect(screen.getByRole('button', { name: 'Read saved result' })).toBeEnabled();
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('E4 persisted-write timeout, failed readback and final read preserve newer typing with no second POST', async () => {
    const s = await setup({ save: 'hold', windowMs: 20 });
    selectLoadout('plain');
    fireEvent.change(screen.getByLabelText('Cards'), { target: { value: 'main_farm' } });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(s.entry()?.outcome).toBe('unknown');
    });
    fireEvent.change(screen.getByLabelText('Cards'), { target: { value: 'no_card' } });
    s.readback('config_state.file_missing_and_no_example_500');
    await change(s.equipment.refresh);
    expect(currentEquipment(s.equipment.getSnapshot())?.readError).toBe(true);
    expect(s.entry()?.submission?.body).toEqual({ cards: 'main_farm' });
    s.readback('loadout_patch.read_after_an_error_that_followed_the_write_holds_the_change');
    await change(s.equipment.refresh);
    expect(s.entry()?.outcome).toBe('readApplied');
    expect(s.entry()?.dirty).toBe(true);
    expect(screen.getByLabelText('Cards')).toHaveValue('no_card');
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('E4 F6C-R1-02 a read with the original body and revision keeps a lost answer Unknown until the person reconciles', async () => {
    const s = await setup({ save: 'loadout_patch.write_failed_nothing_saved_500', mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.edit('cards', 'main_farm');
      s.equipment.save();
    });
    expect(s.entry()?.outcome).toBe('unknown');
    await change(s.equipment.refresh);
    // The unchanged read cannot prove the save failed: it may still be written.
    expect(s.entry()?.outcome).toBe('unknown');
    expect(s.entry()?.intent?.readSettlement).toBeNull();
    expect(s.entry()?.reconcile).toBe(true);
    expect(s.entry()?.dirty).toBe(true);
    await change(s.equipment.reconcile);
    expect(s.entry()?.reconcile).toBe(false);
    expect(s.entry()?.intent?.phase).toBe('unconfirmed');
    // Authoritative evidence still settles it later: a read holding the sent selection.
    s.readback('loadout_patch.read_after_an_error_that_followed_the_write_holds_the_change');
    await change(s.equipment.refresh);
    expect(s.entry()?.intent?.readSettlement).toBe('applied');
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('E4 drafts and immutable submission survive reload and the later read settles without resending', async () => {
    const storage = createMemoryStorage();
    const first = await setup({ storage, save: 'hold', mount: false });
    await change(() => {
      first.equipment.select('plain');
      first.equipment.edit('cards', 'main_farm');
      first.equipment.save();
    });
    await waitFor(() => {
      expect(first.fake.writes()).toHaveLength(1);
    });
    await change(() => {
      first.equipment.edit('cards', 'no_card');
    });
    first.equipment.dispose();
    const next = await setup({ storage, mount: false });
    expect(next.entry()?.draft.values.cards).toBe('no_card');
    expect(next.entry()?.submission?.body).toEqual({ cards: 'main_farm' });
    expect(next.fake.writes()).toHaveLength(0);
    // F6C-R1-02: an unchanged read after the reload keeps the held save Unknown.
    expect(next.entry()?.outcome).toBe('unknown');
    expect(next.entry()?.reconcile).toBe(true);
  });
  it('E4 context epochs fence a late accepted answer; drafts never transfer to another profile', async () => {
    const s = await setup({ save: 'hold', mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.mode('global');
      s.equipment.edit('global_preset', 'Farm Run');
      s.equipment.save();
    });
    await change(() => {
      s.equipment.setTarget(null, 2);
    });
    await change(() => {
      const answer = fixture('loadout_patch.global_preset_selected');
      s.fake.next('/api/loadout-patch').respond(answer.status, answer.body);
    });
    expect(currentLoadout(s.equipment.getSnapshot())).toBeNull();
    const key = s.api.targetKey(s.context);
    expect(s.equipment.getSnapshot().targets[key]?.editors['plain']?.outcome).toBe('unknown');
    await change(() => {
      s.equipment.setTarget(s.context, 3);
    });
    expect(s.entry()?.draft.values.global_preset).toBe('Farm Run');
    expect(s.entry()?.outcome).toBe('unknown');
  });
  it('E4 a conflicting later read requires explicit reconciliation and a separate save click', async () => {
    const s = await setup({ save: 'loadout_patch.stale_revision_409', mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.edit('cards', 'no_card');
      s.equipment.save();
    });
    s.readback('loadout_patch.read_after_an_error_that_followed_the_write_holds_the_change');
    await change(s.equipment.refresh);
    expect(s.entry()?.reconcile).toBe(true);
    await change(s.equipment.reconcile);
    expect(s.entry()?.draft.values.cards).toBe('no_card');
    expect(s.entry()?.reconcile).toBe(false);
    expect(s.fake.writes()).toHaveLength(1);
  });
  it('E4 stale, loading and target-mismatched reads block saving while retaining drafts', async () => {
    const s = await setup({ mount: false });
    await change(() => {
      s.equipment.select('plain');
      s.equipment.edit('cards', 'main_farm');
    });
    expect(selectEquipment(s.equipment.getSnapshot(), Date.now() + 31_000).canSave).toBe(false);
    s.readback('config_state.effective_view_of_the_other_account');
    await change(s.equipment.refresh);
    expect(currentEquipment(s.equipment.getSnapshot())?.readError).toBe(true);
    expect(s.entry()?.draft.values.cards).toBe('main_farm');
    expect(s.fake.writes()).toHaveLength(0);
  });
  it('E5 help and conversion/Cancel controls work by keyboard and keep focus; axe and copy are clean', async () => {
    const s = await setup({ view: 'manual' });
    selectLoadout('plain');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'About equipment selection' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent(/A global preset selects the game/);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    chooseGlobal();
    const cancel = screen.getByRole('button', { name: 'Cancel edits' });
    cancel.focus();
    await user.keyboard('{Enter}');
    expect(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Keep editing' }),
    ).toHaveFocus();
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
    await waitFor(() => {
      expect(cancel).toHaveFocus();
    });
    expect(await axe(document.body)).toHaveNoViolations();
    expect(textOutsideCatalogue(document.body)).toEqual([]);
    expect(
      equipmentBody(
        s.entry()?.draft ?? {
          restoreCards: null,
          restoreModules: null,
          mode: 'category',
          values: {
            global_preset: '',
            cards: '',
            module_preset: '',
            guardian_preset: '',
            workshop_preset: '',
            bot_preset: '',
          },
        },
        s.entry()?.original,
      ),
    ).toEqual({ global_preset: 'Farm Run' });
  });
  it.each(Object.entries(own(CASES, 'loadout_patch') ?? {}))(
    'E4 generated loadout contract strictly parses real route case %s',
    (name, value) => {
      const method = own(own(value, 'request'), 'method');
      const schema =
        method === 'POST'
          ? familySchemas['loadout_patch.POST']
          : familySchemas['loadout_patch.GET'];
      if (schema === undefined) throw new Error('Missing loadout schema');
      expect(
        schema.safeParse({ status: own(value, 'status'), body: own(value, 'body') }).success,
        name,
      ).toBe(true);
    },
  );
});
