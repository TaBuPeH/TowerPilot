import { within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { entries, own } from '../../../../src/core/read.ts';
import {
  reviewModule,
  settingsModule,
  settingsSaveAnswer,
  settingsView,
} from '../../../../src/core/settingsFields.ts';
import { SettingsModuleForm } from '../../../../src/features/settings/SettingsModuleForm.tsx';
import { reduceDashboard } from '../../../../src/store/dashboardStore.ts';
import {
  settingsModuleVM,
  settingsScope,
  settingsTarget,
} from '../../../../src/store/selectors/settingsModule.ts';
import {
  dashboardPagesCase as recorded,
  dashboardPagesCaseNames,
} from '../../../support/dashboardFixture.ts';
import { addBody, intent, regionState } from '../../store/regionHelpers.ts';
import { renderRegion } from '../dashboard/harness.tsx';

const family = 'settings_modules_more.';
const refusals = dashboardPagesCaseNames('settings_modules_more')
  .filter((name) => recorded(family + name).status === 400)
  .map((name) => ({ name }));

function predecessor(name: string): string {
  if (name.includes('unlisted'))
    return name.startsWith('instance_')
      ? 'read_with_an_unlisted_saved_instance_defaults_preset'
      : 'read_with_an_unlisted_saved_run_defaults_preset';
  if (name.includes('no_presets'))
    return 'read_with_no_presets_in_the_configuration_the_options_are_empty';
  if (name.includes('no_runnable')) return 'read_with_no_runnable_run_defaults_preset_options';
  return 'read_with_a_placeholder_preset_in_the_configuration';
}

function refusalReadback(name: string): string | null {
  if (name.includes('unlisted'))
    return name.startsWith('instance_')
      ? 'read_after_the_refused_unlisted_instance_preset_nothing_changed'
      : 'read_after_the_refused_unlisted_run_preset_nothing_changed';
  if (name.includes('no_runnable')) return 'read_after_the_empty_options_refusals_nothing_changed';
  // The no-presets world has no separate post-refusal read in this family.
  if (name.includes('no_presets')) return null;
  return 'read_after_the_refused_preset_saves_nothing_changed';
}

it.each(refusals)(
  'C15-2 C15-5 C-03 $name keeps the refused raw draft, verbatim server error and disabled Save',
  ({ name }) => {
    const refused = recorded(family + name);
    const before = recorded(family + predecessor(name));
    const view = settingsView(before.body);
    const context = view?.context;
    const module = own(refused.request.body, 'module');
    if (context == null || typeof module !== 'string') throw new Error('Missing recorded target');
    const targetContext = { ...context, serial: 'fake:1' };
    let state = addBody({ ...regionState(), context: targetContext }, 'settings', before.body);
    const scope = settingsScope(state.context, module);
    if (scope === null || state.context === null) throw new Error('Missing draft scope');
    for (const [path, value] of entries(own(refused.request.body, 'values'))) {
      if (typeof value !== 'string' && typeof value !== 'boolean')
        throw new Error('Unexpected recorded draft type');
      state = reduceDashboard(state, { type: 'draft_edit', scope, path, value });
    }
    const draft = state.runDrafts[scope];
    const fields = settingsModule(view, module);
    if (fields === null || draft === undefined) throw new Error('Missing recorded module');
    expect(reviewModule(fields, draft).invalid).toHaveProperty('preset');
    state = {
      ...state,
      intents: [
        intent(state, {
          kind: 'settings_save',
          target: settingsTarget(targetContext, module),
          meta: {
            module,
            values: Object.fromEntries(entries(own(refused.request.body, 'values'))),
          },
          phase: 'refused',
          answer: refused,
          answerSeq: 2,
          answeredAtMs: state.nowMs,
        }),
      ],
    };
    // Rehydrate the genuine refusal, including values a select cannot offer. This
    // exercises retained journals without manufacturing an API response or sending it.
    const vm = settingsModuleVM(state, view, module, { pending: false });
    if (vm === null) throw new Error('Missing shared form');
    const onSave = vi.fn();
    const form = renderRegion(
      state,
      <SettingsModuleForm
        vm={vm}
        onEdit={vi.fn()}
        onDiscard={vi.fn()}
        onSave={onSave}
        onDismiss={vi.fn()}
      />,
    );
    const root = within(form.view.container.querySelector('[data-module]') ?? form.view.container);
    expect(root.getByText(String(own(refused.body, 'error')), { exact: true })).toBeVisible();
    expect(root.getByRole('combobox')).toHaveValue(
      String(own(own(refused.request.body, 'values'), 'preset')),
    );
    const save = root.getByRole('button', { name: /^Save$/u });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    save.click();
    expect(onSave).not.toHaveBeenCalled();
    expect(state.runDrafts[scope]).toEqual(draft);
    const readback = refusalReadback(name);
    if (readback !== null) {
      const after = recorded(family + readback);
      const afterView = settingsView(after.body);
      expect(settingsModule(afterView, module)?.fields).toEqual(fields.fields);
      const next = addBody(state, 'settings', after.body, { atMs: state.nowMs + 10 });
      expect(next.runDrafts[scope]).toEqual(draft);
      expect(settingsModuleVM(next, afterView, module, { pending: false })?.outcome).toMatchObject({
        code: 'refused',
        error: own(refused.body, 'error'),
      });
    }
  },
);

it('C15-2 C-03 no runnable preset retains an already empty instance fallback; its recorded explicit save is accepted', () => {
  const before = recorded(family + 'read_with_no_runnable_run_defaults_preset_options');
  const module = settingsModule(settingsView(before.body), 'instance-defaults');
  const saved = recorded(
    family + 'instance_defaults_with_no_runnable_preset_still_saves_the_empty_value',
  );
  if (module === null) throw new Error('Missing instance defaults');
  expect(reviewModule(module, { preset: '' })).toEqual({ values: {}, invalid: {} });
  expect(
    settingsSaveAnswer(saved, { module: 'instance-defaults', values: { preset: '' } }),
  ).toMatchObject({ kind: 'saved' });
  expect(
    settingsModule(
      settingsView(
        recorded(family + 'read_after_the_empty_value_was_saved_with_no_runnable_preset').body,
      ),
      'instance-defaults',
    )?.fields.find((field) => field.path === 'preset')?.value,
  ).toBe('');
});

it('C15-6 C15-7 C-03 the recorded repeated empty-list save keeps its revision; the unchanged editor offers no resend', () => {
  const before = recorded(family + 'read_after_saved_tournament_cards_empty_lists');
  const module = settingsModule(settingsView(before.body), 'tournament-cards');
  const saved = recorded(family + 'saved_the_same_empty_lists_again_keeps_the_revision');
  if (module === null) throw new Error('Missing tournament lists');
  expect(own(saved.request.body, 'revision')).toBe(own(before.body, 'revision'));
  expect(own(saved.body, 'revision')).toBe(own(before.body, 'revision'));
  expect(reviewModule(module, {})).toEqual({ values: {}, invalid: {} });
  expect(
    settingsSaveAnswer(saved, { module: module.id, values: { drop: [], add: [] } }),
  ).toMatchObject({ kind: 'saved' });
});
