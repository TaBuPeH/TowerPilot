import { render, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { configContext } from '../../../../src/core/config.ts';
import { entries, own } from '../../../../src/core/read.ts';
import { settingsView } from '../../../../src/core/settingsFields.ts';
import { SettingsModuleForm } from '../../../../src/features/settings/SettingsModuleForm.tsx';
import { reduceDashboard } from '../../../../src/store/dashboardStore.ts';
import { settingsModuleVM, settingsScope } from '../../../../src/store/selectors/settingsModule.ts';
import { addBody, addFailure, intent, regionState } from '../../store/regionHelpers.ts';
import { bootDashboard, postBodies } from '../../../support/dataLayer.ts';
import { dashboardPagesCase as recorded } from '../../../support/dashboardFixture.ts';
import { createMemoryStorage } from '../../../support/fakeTransport.ts';

const BASE = '#settings-base-revision';
const cases = [
  ['C15-1 C15-2 C15-3', 'instance-defaults', 'read_after_saved_run_defaults', 'instance_defaults'],
  ['C15-4', 'active-instance', 'read_after_saved_missions_defaults', 'active_instance'],
  ['C15-5', 'run-defaults', 'read_after_saved_instance_defaults', 'run_defaults'],
  ['C15-6 C15-7', 'tournament-cards', 'read_after_saved_run_defaults', 'tournament_cards'],
  ['C15-10 C15-11', 'loop-defaults', 'read_after_saved_tournament_cards', 'loop_defaults'],
  ['C15-12', 'missions-defaults', 'read_after_saved_loop_defaults', 'missions_defaults'],
  ['C15-13 C15-14', 'fleet-defaults', 'read_after_saved_missions_defaults', 'fleet_defaults'],
].map(([rows, module, before, suffix]) => ({ rows, module: module ?? '', before, suffix }));

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function setup(module: string, before: string, values: unknown) {
  const body = recorded(before).body;
  const context = settingsView(body)?.context;
  if (context === undefined || context === null) throw new Error('Missing recorded context');
  let state = addBody(
    { ...regionState(), context: { ...context, serial: 'fake:1' } },
    'settings',
    body,
  );
  const scope = settingsScope(state.context, module);
  if (scope === null) throw new Error('Missing scope');
  for (const [path, value] of entries(own(values, 'values'))) {
    // Turn recorded wire values into raw editor parts, without modifying any response.
    if (Array.isArray(value)) {
      const isList =
        settingsView(body)
          ?.modules.find((m) => m.id === module)
          ?.fields.find((f) => f.path === path)?.type === 'string-list';
      if (isList) state = reduceDashboard(state, { type: 'draft_edit', scope, path, value: true });
      for (const [index, item] of value.entries())
        state = reduceDashboard(state, {
          type: 'draft_edit',
          scope,
          path: `${path}#${isList ? 'list:' : ''}${String(index)}`,
          value: String(item),
        });
    } else
      state = reduceDashboard(state, {
        type: 'draft_edit',
        scope,
        path,
        value: typeof value === 'boolean' ? value : String(value),
      });
  }
  return { state, scope };
}

it.each(cases)(
  'C15 C-01 $rows $module same-context revision change retains raw draft and requires explicit reconciliation',
  ({ module, before, suffix }) => {
    const { state, scope } = setup(
      module,
      `settings_modules.${before}`,
      recorded(`settings_modules.stale_revision_${suffix}`).request.body,
    );
    const draft = state.runDrafts[scope];
    const changed = addBody(
      state,
      'settings',
      recorded('settings_modules.read_after_saved_fleet_defaults').body,
    );
    const vm = settingsModuleVM(
      changed,
      settingsView(
        changed.reads.settings.lastAnswered?.outcome.kind === 'answered'
          ? changed.reads.settings.lastAnswered.outcome.body
          : null,
      ),
      module,
      { pending: false },
    );
    if (vm === null) throw new Error('Missing module');
    expect(vm.save.enabled).toBe(false);
    expect(vm.save.command).toBeNull();
    expect(changed.runDrafts[scope]).toEqual(draft);
    const edit = vi.fn();
    const form = render(
      <SettingsModuleForm
        vm={vm}
        onEdit={edit}
        onDiscard={vi.fn()}
        onSave={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );
    expect(within(form.container).getByText(/revision.*changed|reconcile/iu)).toBeVisible();
    within(form.container)
      .getByRole('button', { name: 'Use the read revision; keep my draft' })
      .click();
    expect(edit).toHaveBeenCalledWith(
      scope,
      BASE,
      settingsView(recorded('settings_modules.read_after_saved_fleet_defaults').body)?.revision,
    );
    const reconciled = reduceDashboard(changed, {
      type: 'draft_edit',
      scope,
      path: BASE,
      value:
        settingsView(recorded('settings_modules.read_after_saved_fleet_defaults').body)?.revision ??
        '',
    });
    expect(reconciled.runDrafts[scope]).toEqual({
      ...draft,
      [BASE]: settingsView(recorded('settings_modules.read_after_saved_fleet_defaults').body)
        ?.revision,
    });
  },
);

it.each([
  'rewards',
  'gather:all_on',
  'ad:all_on',
  'orbit:all_on',
  'global_abilities',
  'tap',
  'loop',
  'missions',
  'fleet',
  'logging',
  'device-preview',
])('C15 C-01 C1 C2 C3 C4 A1 T1 T2 %s uses the shared revision fence', (module) => {
  const body = recorded('settings.populated_account_every_module').body;
  const field = settingsView(body)?.modules.find((m) => m.id === module)?.fields[0];
  if (field === undefined) throw new Error('Missing recorded field');
  // Edit a raw part only; both read responses are unchanged recordings.
  const { state, scope } = setup(module, 'settings.populated_account_every_module', {
    values: { [field.path]: field.type === 'boolean' ? field.value !== true : '031' },
  });
  const revision = settingsView(body)?.revision;
  const next = addBody(
    state,
    'settings',
    recorded('settings.read_after_every_module_was_saved').body,
  );
  expect(next.runDrafts[scope]?.[BASE]).toBe(revision);
  expect(
    settingsModuleVM(
      next,
      settingsView(recorded('settings.read_after_every_module_was_saved').body),
      module,
      { pending: false },
    )?.save,
  ).toMatchObject({ enabled: false, reason: 'revision_changed', command: null });
});

it('C15 C-01 C15-14 unreadable latest read and pending saves forbid reconciliation', () => {
  const { state, scope } = setup(
    'fleet-defaults',
    'settings_modules.read_after_saved_missions_defaults',
    recorded('settings_modules.stale_revision_fleet_defaults').request.body,
  );
  const body = recorded('settings_modules.read_after_saved_fleet_defaults').body;
  const next = addBody(state, 'settings', body);
  const failed = addFailure(next, 'settings');
  expect(
    settingsModuleVM(failed, settingsView(body), 'fleet-defaults', { pending: false }),
  ).toMatchObject({ reconcile: null, save: { enabled: false, reason: 'context_unknown' } });
  const pending = { ...next, intents: [intent(next, { kind: 'settings_save', phase: 'sending' })] };
  expect(
    settingsModuleVM(pending, settingsView(body), 'fleet-defaults', { pending: false }),
  ).toMatchObject({ reconcile: null, save: { enabled: false, reason: 'save_pending' } });
  expect(
    reduceDashboard(pending, {
      type: 'draft_edit',
      scope,
      path: BASE,
      value: settingsView(body)?.revision ?? '',
    }).runDrafts,
  ).toBe(pending.runDrafts);
});

it('C15 C-01 C15-14 legacy drafts without a revision require explicit reconciliation', () => {
  const { state, scope } = setup(
    'fleet-defaults',
    'settings_modules.read_after_saved_missions_defaults',
    {},
  );
  const legacy = { ...state, runDrafts: { [scope]: { hold_window: '031' } } };
  const edited = reduceDashboard(legacy, {
    type: 'draft_edit',
    scope,
    path: 'post_window',
    value: '012',
  });
  expect(edited.runDrafts[scope]?.[BASE]).toBeUndefined();
  expect(
    settingsModuleVM(
      edited,
      settingsView(recorded('settings_modules.read_after_saved_missions_defaults').body),
      'fleet-defaults',
      { pending: false },
    ),
  ).toMatchObject({ save: { enabled: false, reason: 'revision_changed' } });
});

async function boot(module = 'fleet-defaults', storage = createMemoryStorage()) {
  const before = 'settings_modules.read_after_saved_missions_defaults';
  const config = recorded(
    'settings_switch_records.read_config_after_switching_back_every_record_as_it_was',
  ).body;
  const page = bootDashboard({
    storage,
    config: () => config,
    before: (fake) => {
      fake.route('GET', '/api/settings', () => ({ status: 200, body: recorded(before).body }));
    },
  });
  page.data.commands.openRuntime(true);
  await vi.advanceTimersByTimeAsync(1000);
  page.data.commands.checkSettings();
  await vi.advanceTimersByTimeAsync(1);
  const scope = settingsScope(page.data.store.getSnapshot().context, module);
  if (scope === null || configContext(config) === null) throw new Error('Missing recorded context');
  page.data.commands.editDraft(scope, 'hold_window', '031');
  const command = settingsModuleVM(
    page.data.store.getSnapshot(),
    settingsView(recorded(before).body),
    module,
    { pending: false },
  )?.save.command;
  if (command === undefined || command === null) throw new Error('Missing command');
  return { ...page, scope, command };
}

it('C15 C-01 C15-13 C15-14 a captured stale command sends nothing', async () => {
  const page = await boot();
  page.fake.route('GET', '/api/settings', () => ({
    status: 200,
    body: recorded('settings_modules.read_after_saved_fleet_defaults').body,
  }));
  page.data.commands.checkSettings();
  await vi.advanceTimersByTimeAsync(1);
  await page.data.commands.saveSettings(page.command);
  expect(postBodies(page.fake, '/api/settings')).toEqual([]);
  expect(page.data.store.getSnapshot().runDrafts[page.scope]?.['hold_window']).toBe('031');
  page.data.stop();
});

it('C15 C-01 C15-13 C15-14 unreadable click-time settings read sends nothing', async () => {
  const page = await boot();
  page.fake.route('GET', '/api/settings', () => ({
    status: 500,
    body: recorded('settings.saved_then_the_answer_failed_500').body,
  }));
  await page.data.commands.saveSettings(page.command);
  expect(postBodies(page.fake, '/api/settings')).toEqual([]);
  expect(page.data.store.getSnapshot().runDrafts[page.scope]?.['hold_window']).toBe('031');
  page.data.stop();
});

it.each(['revision', 'pending', 'draft', 'unreadable-context'])(
  'C15 C-01 C15-13 C15-14 rechecks %s after a held preflight and sends nothing',
  async (change) => {
    const page = await boot();
    page.fake.route('GET', '/api/config', () => 'hold');
    const saving = page.data.commands.saveSettings(page.command);
    await vi.advanceTimersByTimeAsync(0);
    const held = page.fake.last('/api/config', 'GET');
    if (change === 'revision') {
      page.fake.route('GET', '/api/settings', () => ({
        status: 200,
        body: recorded('settings_modules.read_after_saved_fleet_defaults').body,
      }));
    } else if (change === 'pending') {
      const state = page.data.store.getSnapshot();
      page.data.store.dispatch({
        type: 'intents',
        records: [intent(state, { kind: 'settings_save', phase: 'sending' })],
      });
    } else if (change === 'draft') page.data.commands.editDraft(page.scope, 'hold_window', '0032');
    held.respond(
      change === 'unreadable-context' ? 500 : 200,
      change === 'unreadable-context'
        ? recorded('settings.saved_then_the_answer_failed_500').body
        : recorded(
            'settings_switch_records.read_config_after_switching_back_every_record_as_it_was',
          ).body,
    );
    await saving;
    expect(page.fake.writes()).toEqual([]);
    expect(page.data.store.getSnapshot().runDrafts[page.scope]?.['hold_window']).toBe(
      change === 'draft' ? '0032' : '031',
    );
    page.data.stop();
  },
);

it('C15 C-01 C15-13 C15-14 reload keeps the original base revision with raw input', async () => {
  const storage = createMemoryStorage();
  const page = await boot('fleet-defaults', storage);
  const revision = page.command.revision;
  page.data.stop();
  const next = await boot('fleet-defaults', storage);
  expect(next.data.store.getSnapshot().runDrafts[next.scope]?.[BASE]).toBe(revision);
  next.fake.route('GET', '/api/settings', () => ({
    status: 200,
    body: recorded('settings_modules.read_after_saved_fleet_defaults').body,
  }));
  next.data.commands.checkSettings();
  await vi.advanceTimersByTimeAsync(1);
  await next.data.commands.saveSettings(page.command);
  expect(next.fake.writes()).toEqual([]);
  next.data.stop();
});

it('C15-1 C-06 instance label input has no decimal inputMode', () => {
  const { state } = setup(
    'instance-defaults',
    'settings_modules.read_after_saved_fleet_defaults',
    {},
  );
  const vm = settingsModuleVM(
    state,
    settingsView(recorded('settings_modules.read_after_saved_fleet_defaults').body),
    'instance-defaults',
    { pending: false },
  );
  if (vm === null) throw new Error('Missing module');
  const form = render(
    <SettingsModuleForm
      vm={vm}
      onEdit={vi.fn()}
      onDiscard={vi.fn()}
      onSave={vi.fn()}
      onDismiss={vi.fn()}
    />,
  );
  expect(within(form.container).getByRole('textbox')).not.toHaveAttribute('inputmode');
});
