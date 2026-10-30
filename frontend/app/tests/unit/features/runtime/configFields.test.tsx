import { act, screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fetchTransport } from '../../../../src/api/transport.ts';
import { dashboardPagesCase as pages } from '../../../support/dashboardFixture.ts';
import { renderPage } from '../dashboard/harness.tsx';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { selectRuntimeSettings } from '../../../../src/store/selectors/runtime.ts';
import { selectRunDefaults } from '../../../../src/store/selectors/runDefaults.ts';
import {
  settingsSaveAnswer,
  settingsModule,
  settingsView,
} from '../../../../src/core/settingsFields.ts';
import {
  RUNTIME_CONFIG_CONTEXT,
  RUNTIME_CONFIG_SAVES,
} from '../../../support/fake-backend/runtime-config-fixtures.mjs';

vi.mock('../../../../src/api/transport.ts', async (original) => ({
  ...(await original<typeof import('../../../../src/api/transport.ts')>()),
  fetchTransport: vi.fn(),
}));
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.history.replaceState(null, '', '#dashboard');
  vi.useRealTimers();
  vi.clearAllMocks();
});

type Module = keyof typeof RUNTIME_CONFIG_SAVES;
type SavedBody = {
  context: unknown;
  revision: string;
  module: string;
  values: Record<string, unknown>;
};
const rows: readonly { row: string; module: Module; field: string }[] = [
  { row: 'C15-5', module: 'run-defaults', field: 'preset' },
  { row: 'C15-10', module: 'loop-defaults', field: 'fps' },
  { row: 'C15-11', module: 'loop-defaults', field: 'dry_run' },
  { row: 'C15-12', module: 'missions-defaults', field: 'visit_delay_sec' },
  { row: 'C15-13', module: 'fleet-defaults', field: 'hold_window' },
  { row: 'C15-14', module: 'fleet-defaults', field: 'post_window' },
];
function box(module: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[data-module="${module}"]`);
  if (found === null) throw new Error(`Missing ${module}`);
  return found;
}
function part(module: string, key: string) {
  const found = box(module).querySelector<HTMLInputElement>(
    `[data-part="${key}"], [data-focus-key$=":${key}"]`,
  );
  if (found === null) throw new Error(`Missing ${module}.${key}`);
  return found;
}
const reply = (name: string) => {
  const found = pages(name);
  return { status: found.status, body: found.body };
};
async function openConfig(module: Module, initial?: string) {
  const transition = RUNTIME_CONFIG_SAVES[module];
  let read = initial ?? `settings_modules.${transition.before}`;
  window.history.replaceState(null, '', module === 'run-defaults' ? '#runs' : '#runtime');
  const page = await renderPage({
    before: (fake) => {
      vi.mocked(fetchTransport).mockImplementation(fake.transport);
      fake.route('GET', '/api/config', () => reply(RUNTIME_CONFIG_CONTEXT));
      fake.route('GET', '/api/settings', () => reply(read));
      fake.route('GET', '/api/runtime', () =>
        reply('runtime_state.idle_server_build_matches_the_source_on_disk'),
      );
      fake.route('GET', '/api/diagnostics/events', () =>
        reply('runtime_diagnostics.every_event_oldest_first_with_log_coordinates'),
      );
    },
  });
  await page.advance(1000);
  if (module === 'run-defaults') {
    const summary = document.querySelector<HTMLElement>('[data-run-defaults] > details > summary');
    if (summary === null) throw new Error('run defaults disclosure missing');
    await page.user.click(summary);
  }
  return {
    ...page,
    setRead: (name: string) => {
      read = name;
    },
  };
}
type Page = Awaited<ReturnType<typeof openConfig>>;
async function fillValues(page: Page, module: Module, overrides = false) {
  const saved = pages(
    overrides
      ? `settings_modules_more.saved_${RUNTIME_CONFIG_SAVES[module].suffix}_with_overrides`
      : `settings_modules.saved_${RUNTIME_CONFIG_SAVES[module].suffix}`,
  );
  const body = saved.request.body as SavedBody;
  for (const [key, value] of Object.entries(body.values)) {
    if (typeof value === 'boolean') {
      if (part(module, key).checked !== value) await page.user.click(part(module, key));
    } else if (module === 'run-defaults') {
      await page.user.selectOptions(within(box(module)).getByRole('combobox'), String(value));
    } else {
      const values = Array.isArray(value) ? value : [value];
      for (const [index, raw] of values.entries()) {
        const input = part(module, Array.isArray(value) ? `${key}#${index}` : key);
        await page.user.clear(input);
        await page.user.type(input, String(raw));
      }
    }
  }
  return saved;
}
const save = (module: string) => within(box(module)).getByRole('button', { name: 'Save' });

it.each(Object.keys(RUNTIME_CONFIG_SAVES) as Module[])(
  'C15-5 C15-10 C15-11 C15-12 C15-13 C15-14 C-03 %s saves genuine defaults over distinct instance overrides and keeps dirty override text',
  async (module) => {
    const suffix = RUNTIME_CONFIG_SAVES[module].suffix;
    const page = await openConfig(
      module,
      `settings_modules_more.read_before_saved_${suffix}_with_overrides`,
    );
    const originalPicker = page.data.store.getSnapshot().selectedRun;
    const override = module.replace('-defaults', '');
    const key =
      module === 'loop-defaults'
        ? 'fps'
        : module === 'missions-defaults'
          ? 'visit_delay_sec#0'
          : 'hold_window';
    if (module !== 'run-defaults') {
      await page.user.clear(part(override, key));
      await page.user.type(part(override, key), '123');
    }
    const saved = await fillValues(page, module, true);
    await page.user.click(save(module));
    expect(JSON.parse(page.fake.writes()[0]?.body ?? '{}')).toEqual(saved.request.body);
    page.setRead(`settings_modules_more.read_after_saved_${suffix}_with_overrides`);
    page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
    await page.advance(5000);
    expect(box(module)).toHaveTextContent('Saved');
    if (module !== 'run-defaults') expect(part(override, key)).toHaveValue('123');
    if (module === 'loop-defaults') expect(part('loop', 'dry_run')).toBeChecked();
    expect(page.data.store.getSnapshot().selectedRun).toBe(originalPicker);
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each(rows)(
  '$row old read and older save answer preserve newer $field input',
  async ({ module, field }) => {
    const page = await openConfig(module);
    const saved = await fillValues(page, module);
    await page.user.click(save(module));
    page.fake.route('GET', '/api/settings', () => 'hold');
    await page.user.click(screen.getByRole('button', { name: 'Read settings again' }));
    const old = page.fake.last('/api/settings', 'GET');
    const raw = (saved.request.body as SavedBody).values[field];
    const key = Array.isArray(raw) ? `${field}#0` : field;
    let newer: string | boolean;
    if (typeof raw === 'boolean') {
      newer = !raw;
      await page.user.click(part(module, key));
    } else if (module === 'run-defaults') {
      const options = within(box(module)).getAllByRole<HTMLOptionElement>('option');
      newer = options.find((option) => option.value !== raw)?.value ?? '';
      await page.user.selectOptions(within(box(module)).getByRole('combobox'), newer);
    } else {
      newer = String(Number(Array.isArray(raw) ? raw[0] : raw) + 1);
      await page.user.clear(part(module, key));
      await page.user.type(part(module, key), newer);
    }
    act(() => {
      old.respond(200, pages(`settings_modules.${RUNTIME_CONFIG_SAVES[module].before}`).body);
    });
    page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
    await page.advance();
    if (typeof newer === 'boolean') expect(part(module, key).checked).toBe(newer);
    else
      expect(
        module === 'run-defaults' ? within(box(module)).getByRole('combobox') : part(module, key),
      ).toHaveValue(newer);
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each(rows)(
  '$row context changes fence the captured Save and preserve its original draft',
  async ({ module }) => {
    const page = await openConfig(module);
    await fillValues(page, module);
    const state = page.data.store.getSnapshot();
    const vm =
      module === 'run-defaults'
        ? selectRunDefaults(state).vm
        : selectRuntimeSettings(state).modules.find((m) => m.id === module)?.vm;
    const command = vm?.save.command;
    if (command === undefined || command === null) throw new Error('missing command');
    const drafts = page.data.store.getSnapshot().runDrafts;
    page.fake.route('GET', '/api/config', () =>
      reply('settings_modules.read_config_after_saved_active_instance'),
    );
    await act(async () => {
      page.data.poller.read('config');
      await vi.advanceTimersByTimeAsync(1000);
    });
    await act(async () => {
      await page.data.commands.saveSettings(command);
    });
    expect(page.fake.writes()).toEqual([]);
    expect(page.data.store.getSnapshot().runDrafts).toEqual(drafts);
  },
);

it.each(rows)(
  '$row saves only $module with recorded fields/options and leaves other targets intact',
  async ({ module }) => {
    const page = await openConfig(module);
    const runBefore = page.data.store.getSnapshot().runsRun;
    const pickerBefore = page.data.store.getSnapshot().selectedRun;
    const override = module.replace('-defaults', '');
    if (module !== 'run-defaults') {
      await page.user.clear(
        part(
          override,
          module === 'loop-defaults'
            ? 'fps'
            : module === 'fleet-defaults'
              ? 'hold_window'
              : 'visit_delay_sec#0',
        ),
      );
      await page.user.type(
        part(
          override,
          module === 'loop-defaults'
            ? 'fps'
            : module === 'fleet-defaults'
              ? 'hold_window'
              : 'visit_delay_sec#0',
        ),
        '123',
      );
    }
    const saved = await fillValues(page, module);
    await page.user.click(save(module));
    expect(page.fake.writes()).toHaveLength(1);
    expect(JSON.parse(page.fake.writes()[0]?.body ?? '')).toEqual(saved.request.body);
    page.setRead(`settings_modules.read_after_saved_${RUNTIME_CONFIG_SAVES[module].suffix}`);
    page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
    await page.advance(5000);
    expect(box(module)).toHaveTextContent('Saved');
    expect(document.querySelector(`[data-settings-outcome-scope="${module}"]`)).toHaveTextContent(
      /Machine/,
    );
    if (module !== 'run-defaults')
      expect(
        part(
          override,
          module === 'loop-defaults'
            ? 'fps'
            : module === 'fleet-defaults'
              ? 'hold_window'
              : 'visit_delay_sec#0',
        ),
      ).toHaveValue('123');
    expect(page.data.store.getSnapshot().runsRun).toBe(runBefore);
    expect(page.data.store.getSnapshot().selectedRun).toBe(pickerBefore);
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each(rows)(
  '$row stale revision refusal keeps the draft and recorded reason without resend',
  async ({ module }) => {
    const page = await openConfig(module);
    await fillValues(page, module);
    const before = box(module).querySelector('input, select')?.getAttribute('data-focus-key');
    await page.user.click(save(module));
    const refused = pages(`settings_modules.stale_revision_${RUNTIME_CONFIG_SAVES[module].suffix}`);
    page.fake.next('/api/settings', 'POST').respond(refused.status, refused.body);
    await page.advance(10000);
    expect(box(module)).toHaveTextContent((refused.body as { error: string }).error);
    expect(box(module).querySelector('input, select')).toHaveAttribute('data-focus-key', before);
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each(Object.keys(RUNTIME_CONFIG_SAVES) as Module[])(
  'C15-5 C15-10 C15-12 C15-14 %s lost reply stays Unknown on unchanged read, positive readback settles, zero automatic resends',
  async (module) => {
    const page = await openConfig(module);
    await fillValues(page, module);
    await page.user.click(save(module));
    page.fake.next('/api/settings', 'POST').lose();
    await page.advance(5000);
    expect(box(module)).toHaveTextContent('Unknown');
    expect(save(module)).toHaveAttribute('aria-disabled', 'true');
    page.setRead(`settings_modules.read_after_saved_${RUNTIME_CONFIG_SAVES[module].suffix}`);
    await page.user.click(screen.getByRole('button', { name: 'Read settings again' }));
    await page.advance(5000);
    expect(box(module)).toHaveTextContent('A later read shows it saved');
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each(Object.keys(RUNTIME_CONFIG_SAVES) as Module[])(
  'C15-5 C15-10 C15-12 C15-14 %s explicit acceptance keeps Unknown history and requires human Save',
  async (module) => {
    const page = await openConfig(module);
    await fillValues(page, module);
    await page.user.click(save(module));
    page.fake.next('/api/settings', 'POST').lose();
    await page.advance(5000);
    await page.user.click(
      within(box(module)).getByRole('button', { name: 'Continue from the saved values as read' }),
    );
    expect(box(module)).toHaveTextContent('Unknown');
    expect(
      page.data.store.getSnapshot().intents.find((intent) => intent.kind === 'settings_save'),
    ).toMatchObject({ accepted: true });
    expect(page.fake.writes()).toHaveLength(1);
  },
);

it.each([
  ['C15-10', 'loop-defaults', 'fps', 'NaN'],
  ['C15-10', 'loop-defaults', 'fps', '-1'],
  ['C15-12', 'missions-defaults', 'visit_delay_sec#0', ''],
  ['C15-12', 'missions-defaults', 'visit_delay_sec#0', '999'],
  ['C15-13', 'fleet-defaults', 'hold_window', '1.5'],
  ['C15-14', 'fleet-defaults', 'post_window', '-1'],
] as const)(
  '%s raw invalid %s %s=%s stays visible and sends nothing',
  async (_row, module, key, value) => {
    const page = await openConfig(module);
    await page.user.clear(part(module, key));
    if (value !== '') await page.user.type(part(module, key), value);
    expect(part(module, key)).toHaveValue(value);
    expect(part(module, key)).toHaveAttribute('aria-invalid', 'true');
    await page.user.click(save(module));
    expect(page.fake.writes()).toEqual([]);
  },
);

it.each([
  ['C15-5', 'invalid_enum_run_defaults'],
  ['C15-10', 'invalid_number_loop_defaults'],
  ['C15-11', 'invalid_boolean_loop_defaults'],
  ['C15-12', 'invalid_range_missions_defaults'],
  ['C15-13 C15-14', 'invalid_integer_fleet_defaults'],
])('%s recorded invalid case %s is refused with its server reason', (_row, suffix) => {
  const found = pages(`settings_modules.${suffix}`);
  expect(
    settingsSaveAnswer({ status: found.status, body: found.body }, found.request.body as SavedBody),
  ).toMatchObject({ kind: 'refused', error: (found.body as { error: string }).error });
});

it('C15-10 an old read and save answer preserve newer typing and a dirty override', async () => {
  const page = await openConfig('loop-defaults');
  await fillValues(page, 'loop-defaults');
  await page.user.click(save('loop-defaults'));
  page.fake.route('GET', '/api/settings', () => 'hold');
  await page.user.click(screen.getByRole('button', { name: 'Read settings again' }));
  const old = page.fake.last('/api/settings', 'GET');
  await page.user.clear(part('loop-defaults', 'fps'));
  await page.user.type(part('loop-defaults', 'fps'), '4.5');
  await page.user.clear(part('loop', 'fps'));
  await page.user.type(part('loop', 'fps'), '7.5');
  act(() => {
    old.respond(200, pages('settings_modules.read_after_saved_tournament_cards').body);
  });
  const saved = pages('settings_modules.saved_loop_defaults');
  page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
  await page.advance();
  expect(part('loop-defaults', 'fps')).toHaveValue('4.5');
  expect(part('loop', 'fps')).toHaveValue('7.5');
  expect(page.fake.writes()).toHaveLength(1);
});

it('C15-10 inherited effective display refreshes from server metadata without creating an override', async () => {
  const page = await openConfig('loop-defaults');
  const saved = await fillValues(page, 'loop-defaults');
  await page.user.click(save('loop-defaults'));
  page.setRead('settings_modules.read_after_saved_loop_defaults');
  page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
  await page.advance(5000);
  expect(part('loop', 'fps')).toHaveValue('3.5');
  expect(page.fake.writes()).toHaveLength(1);
  expect(JSON.parse(page.fake.writes()[0]?.body ?? '') as unknown).toEqual(saved.request.body);
});

it('C15-5 C-03 an unlisted saved preset stays visible until a listed choice is explicitly saved', async () => {
  const initial = 'settings_modules_more.read_with_an_unlisted_saved_run_defaults_preset';
  const page = await openConfig('run-defaults', initial);
  const picker = within(box('run-defaults')).getByRole('combobox');
  expect(picker).toHaveValue('ghost');
  expect(picker).toHaveAttribute('aria-invalid', 'true');
  expect(save('run-defaults')).toHaveAttribute('aria-disabled', 'true');
  expect(box('run-defaults')).not.toHaveTextContent("An emulator's own default preset");
  await page.user.hover(
    within(box('run-defaults')).getByRole('button', {
      name: 'About Machine fallback · Run defaults',
    }),
  );
  expect(screen.getByRole('tooltip')).toHaveTextContent(
    "An emulator's own default preset takes precedence",
  );
  expect(page.fake.writes()).toEqual([]);
  const saved = pages(
    'settings_modules_more.run_defaults_choosing_a_listed_preset_replaces_the_unlisted_one',
  );
  await page.user.selectOptions(picker, 'coin_farm');
  await page.user.click(save('run-defaults'));
  expect(JSON.parse(page.fake.writes()[0]?.body ?? '')).toEqual(saved.request.body);
  page.setRead('settings_modules_more.read_after_the_unlisted_run_preset_was_replaced');
  page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
  await page.advance(5000);
  expect(picker).toHaveValue('coin_farm');
  expect(picker).not.toHaveAttribute('aria-invalid');
  expect(page.fake.writes()).toHaveLength(1);
});

it.each([
  'read_with_no_runnable_run_defaults_preset_options',
  'read_with_no_presets_in_the_configuration_the_options_are_empty',
  'read_with_a_placeholder_preset_in_the_configuration',
])('C15-5 C-03 %s never chooses a preset automatically or offers placeholders', async (read) => {
  const page = await openConfig('run-defaults', 'settings_modules_more.' + read);
  const metadata = settingsModule(
    settingsView(pages('settings_modules_more.' + read).body),
    'run-defaults',
  );
  const field = metadata?.fields[0];
  if (field === undefined) throw new Error('Missing recorded preset');
  const picker = within(box('run-defaults')).getByRole('combobox');
  expect(picker).toHaveValue(String(field.value));
  for (const option of field.options)
    expect(within(picker).getByRole('option', { name: option.label })).toHaveValue(option.value);
  expect(within(picker).queryByRole('option', { name: 'later' })).toBeNull();
  expect(save('run-defaults')).toHaveAttribute('aria-disabled', 'true');
  const reason = save('run-defaults').getAttribute('aria-describedby');
  expect(reason === null ? null : document.getElementById(reason)).toBeVisible();
  await page.user.click(save('run-defaults'));
  expect(page.fake.writes()).toEqual([]);
});

it.each(['loop-defaults', 'run-defaults'] as const)(
  'C15-5 C15-10 C15-11 C15-12 C15-13 C15-14 %s catalogue and axe',
  async (module) => {
    const page = await openConfig(module);
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  },
);

it('C15-10 C15-11 C15-12 C15-13 C15-14 defaults precede overrides with separate shared forms', async () => {
  window.history.replaceState(null, '', '#runtime');
  const page = await renderPage({
    before: (fake) => {
      vi.mocked(fetchTransport).mockImplementation(fake.transport);
      for (const [path, name] of [
        [
          '/api/config',
          'settings_switch_records.read_config_after_switching_back_every_record_as_it_was',
        ],
        ['/api/settings', 'settings_modules.read_after_saved_tournament_cards'],
        ['/api/runtime', 'runtime_state.idle_server_build_matches_the_source_on_disk'],
        [
          '/api/diagnostics/events',
          'runtime_diagnostics.every_event_oldest_first_with_log_coordinates',
        ],
      ])
        fake.route('GET', path ?? '', () => {
          const found = pages(name ?? '');
          return { status: found.status, body: found.body };
        });
    },
  });
  await page.advance(1000);
  expect(
    screen.getByRole('heading', { name: 'Machine defaults - shared fallback values' }),
  ).toBeVisible();
  expect(document.querySelectorAll('[data-runtime-module]')).toHaveLength(8);
  const root = document.querySelector<HTMLElement>('[data-runtime-module="loop-defaults"]');
  if (root === null) throw new Error('missing defaults');
  expect(within(root).getByRole('group')).toHaveAccessibleName(/Machine defaults/);
  expect(root.closest('[data-runtime-group="defaults"]')).toHaveTextContent(
    'Machine fallback · next time automation starts. Existing emulator overrides are kept.',
  );
  const config = pages(RUNTIME_CONFIG_CONTEXT).body as {
    active_instance: string;
    instances: Record<string, { label: string }>;
  };
  expect(
    screen.getByRole('heading', {
      name: `Overrides for ${config.instances[config.active_instance]?.label ?? ''} (${config.active_instance})`,
    }),
  ).toBeVisible();
});
