import { screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { own, list } from '../../../../src/core/read.ts';
import {
  checkField,
  settingsModule,
  settingsView,
  settleSettingsSave,
} from '../../../../src/core/settingsFields.ts';
import { selectEmulatorSettings } from '../../../../src/store/selectors/emulatorSettings.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import { createMemoryStorage } from '../../../support/fakeTransport.ts';
import { renderPage } from '../dashboard/harness.tsx';
import { emulatorConfigBefore } from '../../../support/fake-backend/emulators-config-fixtures.mjs';

const recorded = (name: string) => dashboardPagesCase(name);
const saved = recorded('settings_modules.saved_instance_defaults');
const switchSave = recorded('settings_modules.saved_active_instance');
const request = own(saved.request.body, 'values');

function moduleOf(body: unknown, id: string) {
  return list(own(body, 'modules'))?.find((module) => own(module, 'id') === id);
}

function fieldLabel(path: string): string {
  const field = list(own(moduleOf(saved.body, 'instance-defaults'), 'fields'))?.find(
    (field) => own(field, 'path') === path,
  );
  const label = own(field, 'label');
  if (typeof label !== 'string') throw new Error(`Missing recorded label ${path}`);
  return label;
}

async function open(
  options: {
    refusal?: string;
    lost?: boolean;
    unchanged?: boolean;
    initial?: unknown;
    saveCase?: string;
    readAfter?: string;
    storage?: ReturnType<typeof createMemoryStorage>;
  } = {},
) {
  window.location.hash = '#emulators';
  let settings: unknown = options.initial ?? emulatorConfigBefore().body;
  let config: unknown = recorded(
    'settings_switch_records.read_config_after_switching_back_every_record_as_it_was',
  ).body;
  const page = await renderPage({
    ...(options.storage === undefined ? {} : { storage: options.storage }),
    before: (fake) => {
      fake.route('GET', '/api/config', () => ({ status: 200, body: config }));
      fake.route('GET', '/api/settings', () => ({ status: 200, body: settings }));
      fake.route('POST', '/api/settings', (req) => {
        if (options.refusal !== undefined) {
          const refused = recorded(options.refusal);
          return { status: refused.status, body: refused.body };
        }
        const isSwitch = own(JSON.parse(req.body ?? '{}'), 'module') === 'active-instance';
        if (isSwitch) {
          if (!options.unchanged) {
            settings = recorded('settings_modules.read_after_saved_active_instance').body;
            config = recorded('settings_modules.read_config_after_saved_active_instance').body;
          }
        } else
          settings = recorded(
            options.readAfter ?? 'settings_modules.read_after_saved_instance_defaults',
          ).body;
        return options.lost
          ? 'hold'
          : {
              status: 200,
              body: isSwitch
                ? switchSave.body
                : recorded(options.saveCase ?? 'settings_modules.saved_instance_defaults').body,
            };
      });
    },
  });
  await page.advance(1_000);
  return {
    ...page,
    setConfig: (body: unknown) => {
      config = body;
    },
    setSettings: (body: unknown) => {
      settings = body;
    },
  };
}

const form = (module: string) => {
  const element = document.querySelector<HTMLElement>(`[data-module="${module}"]`);
  if (element === null) throw new Error(`Missing settings module ${module}`);
  return within(element);
};

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

it('C15 context uses its containing heading and one field scope description', async () => {
  const page = await open();
  const selected = document.querySelector<HTMLElement>('[data-region="emulatorsContext"]');
  if (selected === null) throw new Error('Missing selected emulator region');
  const context = within(selected);
  expect(context.getAllByRole('heading')).toHaveLength(1);
  expect(context.getByRole('group', { name: 'Selected emulator — page context' })).toBeVisible();
  expect(context.getByRole('combobox')).toHaveAccessibleName('Selected emulator');
  expect(context.queryByText('Choose the emulator every page shows')).toBeNull();
  expect(context.queryByText(/^Choosing another emulator changes/u)).toBeNull();
  expect(selected.querySelector('details')).toBeNull();
  await page.user.hover(
    context.getByRole('button', { name: 'About Selected emulator — page context' }),
  );
  expect(screen.getByRole('tooltip')).toHaveTextContent(/Drafts stay with their original targets/u);
  expect(page.fake.writes()).toEqual([]);
  page.data.stop();
});

it('C15-1 C15-2 C15-3 defaults save exactly the recorded body and read back without device actions', async () => {
  const page = await open();
  const defaults = form('instance-defaults');
  await page.user.clear(defaults.getByRole('textbox', { name: fieldLabel('label') }));
  await page.user.type(
    defaults.getByRole('textbox', { name: fieldLabel('label') }),
    String(own(request, 'label')),
  );
  await page.user.selectOptions(
    defaults.getByRole('combobox', { name: fieldLabel('preset') }),
    String(own(request, 'preset')),
  );
  await page.user.click(defaults.getByRole('checkbox', { name: fieldLabel('autostart') }));
  await page.user.click(defaults.getByRole('button', { name: /^Save$/u }));
  await page.advance(3_000);
  expect(postBodies(page.fake, '/api/settings')).toEqual([saved.request.body]);
  expect(page.fake.writes().map((req) => req.path)).toEqual(['/api/settings']);
  expect(defaults.getByRole('textbox', { name: fieldLabel('label') })).toHaveValue(
    String(own(request, 'label')),
  );
  const autostart = defaults.getByRole('checkbox', { name: fieldLabel('autostart') });
  expect(defaults.queryByText(/Takes effect when the tray next starts/u)).toBeNull();
  await page.user.hover(
    within(autostart.closest<HTMLElement>('[data-field]') ?? defaults.getByRole('group')).getByRole(
      'button',
      { name: `About ${fieldLabel('autostart')}` },
    ),
  );
  expect(screen.getByRole('tooltip')).toHaveTextContent(
    'Takes effect when the tray next starts; saving starts nothing now.',
  );
  page.data.stop();
});

it('C15-1 stale refusal preserves raw whitespace and the recorded server reason', async () => {
  // The recorded label is Stale; add surrounding whitespace to prove raw retention
  // while reusing its unchanged, genuine revision-conflict response.
  const name = 'settings_modules.stale_revision_instance_defaults';
  const page = await open({ refusal: name });
  const defaults = form('instance-defaults');
  const input = defaults.getByRole('textbox', { name: fieldLabel('label') });
  await page.user.clear(input);
  await page.user.type(input, '  Stale  ');
  await page.user.click(defaults.getByRole('button', { name: /^Save$/u }));
  await page.advance(0);
  expect(input).toHaveValue('  Stale  ');
  expect(defaults.getByText(String(own(recorded(name).body, 'error')))).toBeVisible();
  expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
  page.data.stop();
});

it('C15-1 a late settings read preserves newer typing and a reload keeps the original draft', async () => {
  const storage = createMemoryStorage();
  const page = await open({ storage });
  page.fake.route('GET', '/api/settings', () => 'hold');
  page.data.commands.checkSettings();
  await page.advance(0);
  const read = page.fake.next('/api/settings', 'GET');
  await page.user.clear(form('instance-defaults').getByRole('textbox'));
  await page.user.type(form('instance-defaults').getByRole('textbox'), '  newest draft  ');
  read.respond(200, emulatorConfigBefore().body);
  await page.advance(0);
  expect(form('instance-defaults').getByRole('textbox')).toHaveValue('  newest draft  ');
  page.data.stop();
  page.view.unmount();
  const reloaded = await open({ storage });
  expect(form('instance-defaults').getByRole('textbox')).toHaveValue('  newest draft  ');
  expect(reloaded.fake.writes()).toEqual([]);
  reloaded.data.stop();
});

it('C15-4 switch cancel sends nothing and restores focus; confirmation writes once and config adopts beta', async () => {
  const page = await open();
  const selection = form('active-instance');
  await page.user.selectOptions(selection.getByRole('combobox'), 'other');
  const save = selection.getByRole('button', { name: /^Save$/u });
  await page.user.click(save);
  const dialog = within(document.querySelector('dialog[open]') ?? document.body);
  await page.user.click(dialog.getByRole('button', { name: /^Cancel$/u }));
  expect(save).toHaveFocus();
  expect(page.fake.writes()).toEqual([]);
  await page.user.click(save);
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(5_000);
  expect(postBodies(page.fake, '/api/settings')).toEqual([switchSave.request.body]);
  expect(page.data.store.getSnapshot().context).toMatchObject({
    instance: 'other',
    account: 'beta',
    profile: 'theirs',
  });
  const record = page.data.store
    .getSnapshot()
    .intents.find((entry) => entry.meta.module === 'active-instance');
  expect(record?.context).toMatchObject({ instance: 'main', account: 'alpha', profile: 'mine' });
  expect(record && page.data.store.getSnapshot().readSettlements[record.id]?.settlement).toBe(
    'applied',
  );
  page.data.stop();
});

it('C15-4 only config naming the intended new instance settles; settings, unchanged and unrelated config do not', () => {
  const context = { instance: 'main', account: 'alpha', profile: 'mine' };
  const sent = { module: 'active-instance', values: { active_instance: 'other' } };
  expect(
    settleSettingsSave(
      recorded('settings_modules.read_after_saved_active_instance').body,
      sent,
      context,
    ),
  ).toBeNull();
  expect(
    settleSettingsSave(
      recorded('settings_switch_records.read_config_after_switching_back_every_record_as_it_was')
        .body,
      sent,
      context,
    ),
  ).toBeNull();
  expect(
    settleSettingsSave(
      recorded('settings_modules.read_config_after_saved_active_instance').body,
      sent,
      context,
    ),
  ).toBe('applied');
});

it('C15-2 C-03 fallback is an explicit server option; an unlisted saved preset retains its reason', async () => {
  const page = await open();
  const defaults = form('instance-defaults');
  const picker = defaults.getByRole('combobox');
  const metadata = settingsModule(settingsView(emulatorConfigBefore().body), 'instance-defaults');
  const field = metadata?.fields.find((field) => field.path === 'preset');
  expect(field?.options.some((option) => option.value === '')).toBe(true);
  expect(
    within(picker)
      .getAllByRole('option')
      .map((option) => option.getAttribute('value')),
  ).toEqual(field?.options.map((option) => option.value));
  const read = recorded(
    'settings_modules_more.read_with_an_unlisted_saved_instance_defaults_preset',
  ).body;
  const preset = settingsModule(settingsView(read), 'instance-defaults')?.fields.find(
    (field) => field.path === 'preset',
  );
  if (preset === undefined) throw new Error('Missing recorded preset field');
  page.setSettings(read);
  page.data.commands.checkSettings();
  await page.advance(0);
  expect(defaults.getByRole('combobox')).toHaveValue(String(preset.value));
  expect(defaults.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true');
  expect(page.fake.writes()).toEqual([]);
  page.data.stop();
});

it('C15-2 C15-3 C-03 explicit machine fallback and autostart off round-trip independently of the label', async () => {
  const off = recorded(
    'settings_modules_more.saved_instance_defaults_with_fallback_and_autostart_off',
  );
  const page = await open({
    initial: recorded('settings_modules.read_after_saved_instance_defaults').body,
    saveCase: 'settings_modules_more.saved_instance_defaults_with_fallback_and_autostart_off',
    readAfter:
      'settings_modules_more.read_after_saved_instance_defaults_with_fallback_and_autostart_off',
  });
  const defaults = form('instance-defaults');
  await page.user.selectOptions(defaults.getByRole('combobox'), '');
  await page.user.click(defaults.getByRole('checkbox'));
  await page.user.click(defaults.getByRole('button', { name: /^Save$/u }));
  await page.advance(1_000);
  expect(postBodies(page.fake, '/api/settings')).toEqual([off.request.body]);
  expect(defaults.getByRole('combobox')).toHaveValue('');
  expect(defaults.getByRole('checkbox')).not.toBeChecked();
  expect(defaults.getByRole('textbox')).toHaveValue(String(own(request, 'label')));
  page.data.stop();
});

it('C15-1 C15-2 C15-3 C-03 changing label and autostart omits the untouched unlisted preset and readback keeps it', async () => {
  const saveCase =
    'settings_modules_more.instance_defaults_saved_without_the_preset_field_keeps_the_unlisted_preset';
  const partial = recorded(saveCase);
  const page = await open({
    initial: recorded('settings_modules_more.read_with_an_unlisted_saved_instance_defaults_preset')
      .body,
    saveCase,
    readAfter:
      'settings_modules_more.read_after_the_partial_instance_save_the_unlisted_preset_remains',
  });
  const defaults = form('instance-defaults');
  expect(defaults.getByRole('combobox')).toHaveValue('ghost');
  await page.user.clear(defaults.getByRole('textbox'));
  await page.user.type(defaults.getByRole('textbox'), 'Tower');
  await page.user.click(defaults.getByRole('checkbox'));
  await page.user.click(defaults.getByRole('button', { name: /^Save$/u }));
  await page.advance(3_000);
  expect(postBodies(page.fake, '/api/settings')).toEqual([partial.request.body]);
  expect(defaults.getByRole('combobox')).toHaveValue('ghost');
  expect(defaults.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true');
  expect(defaults.getByRole('textbox')).toHaveValue('Tower');
  expect(defaults.getByRole('checkbox')).toBeChecked();
  page.data.stop();
});

it.each([
  [
    'unlisted',
    'read_after_the_partial_instance_save_the_unlisted_preset_remains',
    'instance_defaults_choosing_the_machine_fallback_replaces_the_unlisted_preset',
    'read_after_the_unlisted_instance_preset_was_replaced',
  ],
])(
  'C15-2 C-03 %s preset changes only after explicitly choosing machine fallback',
  async (_state, initial, saveCase, readAfter) => {
    const prefix = 'settings_modules_more.';
    const page = await open({
      initial: recorded(prefix + initial).body,
      saveCase: prefix + saveCase,
      readAfter: prefix + readAfter,
    });
    const defaults = form('instance-defaults');
    expect(defaults.getByRole('combobox')).not.toHaveValue('');
    expect(page.fake.writes()).toEqual([]);
    await page.user.selectOptions(defaults.getByRole('combobox'), '');
    await page.user.click(defaults.getByRole('button', { name: /^Save$/u }));
    await page.advance(3_000);
    expect(postBodies(page.fake, '/api/settings')).toEqual([
      recorded(prefix + saveCase).request.body,
    ]);
    expect(defaults.getByRole('combobox')).toHaveValue('');
    expect(defaults.getByRole('combobox')).not.toHaveAttribute('aria-invalid');
    page.data.stop();
  },
);

it.each([
  ['C15-1', 'invalid_string_instance_defaults', 'label'],
  ['C15-2', 'invalid_enum_instance_defaults', 'preset'],
  ['C15-3', 'invalid_boolean_instance_defaults', 'autostart'],
  ['C15-4', 'invalid_enum_active_instance', 'active_instance'],
])('%s recorded %s is refused without coercing its raw value', (_row, name, path) => {
  const invalid = recorded(`settings_modules.${name}`);
  const module = settingsModule(
    settingsView(emulatorConfigBefore().body),
    String(own(invalid.request.body, 'module')),
  );
  const field = module?.fields.find((field) => field.path === path);
  if (field === undefined) throw new Error(`Missing recorded field ${path}`);
  // Deliberately bypass the DOM's string/boolean boundary to replay a recorded invalid type.
  const raw = own(own(invalid.request.body, 'values'), path);
  expect(checkField(field, { [path]: raw } as Record<string, string | boolean>)).toMatchObject({
    kind: 'invalid',
  });
  expect(invalid.status).toBe(400);
});

it('C15-4 reviewed context changing before confirm sends nothing', async () => {
  const page = await open();
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  await page.user.click(form('active-instance').getByRole('button', { name: /^Save$/u }));
  page.setConfig(recorded('settings_modules.read_config_after_saved_active_instance').body);
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(0);
  expect(page.fake.writes()).toEqual([]);
  expect(page.data.store.getSnapshot().notices).toContainEqual(
    expect.objectContaining({ reason: 'context_changed' }),
  );
  page.data.stop();
});

it('C15-4 reviewed revision changing before confirm sends nothing and preserves the draft', async () => {
  const page = await open();
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  await page.user.click(form('active-instance').getByRole('button', { name: /^Save$/u }));
  page.setSettings(recorded('settings_modules.read_after_saved_instance_defaults').body);
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(0);
  expect(page.fake.writes()).toEqual([]);
  expect(form('active-instance').getByRole('combobox')).toHaveValue('other');
  page.data.stop();
});

it('C15-4 recorded stale switch refusal preserves the reviewed selection and sends once', async () => {
  // Reuse the genuine stale-switch refusal for the forward switch. Its recorded
  // stale request selected main after a prior switch; the refusal contract is the same.
  const name = 'settings_modules.stale_revision_active_instance';
  const page = await open({ refusal: name });
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  await page.user.click(form('active-instance').getByRole('button', { name: /^Save$/u }));
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(1_000);
  expect(form('active-instance').getByRole('combobox')).toHaveValue('other');
  expect(
    form('active-instance').getByText(String(own(recorded(name).body, 'error'))),
  ).toBeVisible();
  expect(postBodies(page.fake, '/api/settings')).toEqual([switchSave.request.body]);
  expect(page.data.store.getSnapshot().context?.instance).toBe('main');
  page.data.stop();
});

it('C15-4 a successful settings receipt alone stays unresolved until config verifies the transition', async () => {
  const page = await open({ unchanged: true });
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  await page.user.click(form('active-instance').getByRole('button', { name: /^Save$/u }));
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(1_000);
  const record = page.data.store
    .getSnapshot()
    .intents.find((entry) => entry.meta.module === 'active-instance');
  expect(record?.phase).toBe('unconfirmed');
  expect(selectEmulatorSettings(page.data.store.getSnapshot()).active?.outcome).toMatchObject({
    code: 'unknown',
    acceptOffered: true,
  });
  expect(page.data.store.getSnapshot().context?.instance).toBe('main');
  expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
  page.data.stop();
});

it('C15-4 lost answer is settled by later config; unsaved defaults remain only in the original context', async () => {
  const page = await open({ lost: true });
  await page.user.clear(form('instance-defaults').getByRole('textbox'));
  await page.user.type(form('instance-defaults').getByRole('textbox'), 'original draft');
  const old = selectEmulatorSettings(page.data.store.getSnapshot()).defaults?.scope;
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  await page.user.click(form('active-instance').getByRole('button', { name: /^Save$/u }));
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(0);
  page.fake.next('/api/settings', 'POST').lose();
  await page.advance(2_000);
  const state = page.data.store.getSnapshot();
  expect(state.context).toMatchObject({ instance: 'other', account: 'beta' });
  expect(old === null || old === undefined ? null : state.runDrafts[old]?.['label']).toBe(
    'original draft',
  );
  expect(form('instance-defaults').getByRole('textbox')).not.toHaveValue('original draft');
  expect(
    state.intents.find((entry) => entry.meta.module === 'active-instance')?.readSettlement,
  ).toBe('applied');
  expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
  page.data.stop();
});

it('C15-4 sparse account switch and switch back use config-only readback and preserve every account record', () => {
  const forward = recorded('settings_switch_records.saved_active_instance_between_sparse_accounts');
  const back = recorded(
    'settings_switch_records.saved_active_instance_back_to_the_first_sparse_account',
  );
  const switched = recorded('settings_switch_records.read_config_after_the_sparse_switch');
  const restored = recorded(
    'settings_switch_records.read_config_after_switching_back_every_record_as_it_was',
  );
  expect(own(switched.body, 'accounts')).toEqual(own(restored.body, 'accounts'));
  expect(own(switched.body, 'instances')).toEqual(own(restored.body, 'instances'));
  const sent = (body: unknown) => ({
    module: 'active-instance',
    values: own(body, 'values') as Record<string, unknown>,
  });
  expect(
    settleSettingsSave(switched.body, sent(forward.request.body), {
      instance: 'main',
      account: 'alpha',
      profile: 'mine',
    }),
  ).toBe('applied');
  expect(
    settleSettingsSave(restored.body, sent(back.request.body), {
      instance: 'other',
      account: 'beta',
      profile: 'theirs',
    }),
  ).toBe('applied');
  expect(
    settleSettingsSave(
      recorded('settings_switch_records.read_after_the_sparse_switch').body,
      sent(forward.request.body),
      { instance: 'main', account: 'alpha', profile: 'mine' },
    ),
  ).toBeNull();
});

it('C15-1 failed settings read keeps the draft with a visible reason and refuses a save', async () => {
  const page = await open();
  await page.user.clear(form('instance-defaults').getByRole('textbox'));
  await page.user.type(form('instance-defaults').getByRole('textbox'), 'keep me');
  const failure = recorded('settings.saved_then_the_answer_failed_500');
  // Reuse the recorded server failure on the settings GET to exercise stale-read UI;
  // the recording itself is a POST failure, and its body is unchanged here.
  page.fake.route('GET', '/api/settings', () => ({ status: failure.status, body: failure.body }));
  page.data.commands.checkSettings();
  await page.advance(0);
  expect(form('instance-defaults').getByRole('textbox')).toHaveValue('keep me');
  expect(selectEmulatorSettings(page.data.store.getSnapshot()).defaults?.save).toMatchObject({
    enabled: false,
  });
  expect(
    page.view.getByText(
      'The last settings read failed. Your edits are kept; read the settings again before saving.',
    ),
  ).toBeVisible();
  page.data.stop();
});

it('C15-4 unchanged config keeps a lost answer Unknown; fresh readable config permits separate acceptance without resend', async () => {
  const page = await open({ lost: true, unchanged: true });
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  await page.user.click(form('active-instance').getByRole('button', { name: /^Save$/u }));
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(0);
  page.fake.next('/api/settings', 'POST').lose();
  await page.advance(1_000);
  const vm = selectEmulatorSettings(page.data.store.getSnapshot());
  expect(vm.active?.outcome).toMatchObject({ code: 'unknown', acceptOffered: true });
  expect(vm.active?.save).toMatchObject({ enabled: false, reason: 'save_pending' });
  await page.user.click(
    form('active-instance').getByRole('button', { name: 'Continue from the saved values as read' }),
  );
  await page.advance(1_000);
  expect(selectEmulatorSettings(page.data.store.getSnapshot()).active?.outcome).toMatchObject({
    code: 'unknown',
    accepted: true,
  });
  expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
  page.data.stop();
});

it('C15-4 Read saved settings again recovers a failed config read without another write', async () => {
  const page = await open();
  let configReadable = false;
  // Transport fault only: reuse the recorded failure body, then recover with the
  // genuine new-context config recording. No success response is manufactured.
  const failed = recorded('settings.saved_then_the_answer_failed_500');
  // The confirmation's independent context check still needs its original context.
  // Fail only poller config reads after the settings POST has been recorded.
  page.fake.route('GET', '/api/config', () =>
    page.fake.writes().length === 0
      ? recorded('settings_switch_records.read_config_after_switching_back_every_record_as_it_was')
      : configReadable
        ? recorded('settings_modules.read_config_after_saved_active_instance')
        : { status: failed.status, body: failed.body },
  );
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  await page.user.click(form('active-instance').getByRole('button', { name: /^Save$/u }));
  await page.user.click(
    within(document.querySelector('dialog[open]') ?? document.body).getByRole('button', {
      name: 'Use emulator',
    }),
  );
  await page.advance(0);
  expect(selectEmulatorSettings(page.data.store.getSnapshot()).active?.outcome).toMatchObject({
    code: 'unknown',
    acceptOffered: false,
  });
  configReadable = true;
  await page.user.click(page.view.getByRole('button', { name: 'Read saved settings again' }));
  await page.advance(0);
  expect(page.data.store.getSnapshot().context).toMatchObject({
    instance: 'other',
    account: 'beta',
  });
  expect(selectEmulatorSettings(page.data.store.getSnapshot()).active?.outcome?.code).toBe(
    'applied',
  );
  expect(postBodies(page.fake, '/api/settings')).toEqual([switchSave.request.body]);
  page.data.stop();
});

it('C15-1 C15-4 a pending defaults save visibly blocks a separate switch', async () => {
  const page = await open({ lost: true });
  await page.user.clear(form('instance-defaults').getByRole('textbox'));
  await page.user.type(form('instance-defaults').getByRole('textbox'), 'waiting');
  await page.user.click(form('instance-defaults').getByRole('button', { name: /^Save$/u }));
  await page.user.selectOptions(form('active-instance').getByRole('combobox'), 'other');
  const active = selectEmulatorSettings(page.data.store.getSnapshot()).active;
  expect(active?.save).toMatchObject({ enabled: false, reason: 'save_pending' });
  expect(form('active-instance').getByRole('button', { name: /^Save$/u })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(
    form('active-instance').getByText('Waiting until the last save is read back'),
  ).toBeVisible();
  expect(postBodies(page.fake, '/api/settings')).toHaveLength(1);
  page.data.stop();
});

it('C15-1 C15-2 C15-3 C15-4 catalogue text and axe cover the shared forms', async () => {
  const page = await open();
  form('instance-defaults');
  form('active-instance');
  expect(textOutsideCatalogue(page.view.container)).toEqual([]);
  vi.useRealTimers();
  expect(await axe(page.view.container)).toHaveNoViolations();
  page.data.stop();
});
