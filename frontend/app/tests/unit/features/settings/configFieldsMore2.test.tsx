import { fireEvent, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { entries, own } from '../../../../src/core/read.ts';
import {
  reviewModule,
  settingsModule,
  settingsSaveAnswer,
  settingsView,
  settleSettingsSave,
} from '../../../../src/core/settingsFields.ts';
import { SettingsModuleForm } from '../../../../src/features/settings/SettingsModuleForm.tsx';
import { reduceDashboard } from '../../../../src/store/dashboardStore.ts';
import {
  settingsModuleVM,
  settingsScope,
  settingsTarget,
} from '../../../../src/store/selectors/settingsModule.ts';
import { dashboardPagesCase as recorded } from '../../../support/dashboardFixture.ts';
import { addBody, intent, regionState } from '../../store/regionHelpers.ts';
import { renderRegion } from '../dashboard/harness.tsx';

const family = 'settings_modules_more_2.';
const moduleId = 'tournament-cards';
function setup(key: string) {
  const before = recorded(family + key);
  const view = settingsView(before.body);
  if (view?.context == null) throw new Error('Missing recorded context');
  const context = { ...view.context, serial: 'fake:1' };
  const state = addBody({ ...regionState(), context }, 'settings', before.body);
  const scope = settingsScope(context, moduleId);
  if (scope === null) throw new Error('Missing scope');
  return { state, view, scope, context };
}

it('C15-6 C15-7 r12f malformed send-back refusal stays verbatim with raw members and unchanged readback', () => {
  const s = setup('read_with_malformed_tournament_cards');
  const refusal = recorded(
    family + 'tournament_cards_sent_back_with_the_malformed_entries_is_refused',
  );
  const values = Object.fromEntries(entries(own(refusal.request.body, 'values')));
  expect(settingsSaveAnswer(refusal, { module: moduleId, values })).toMatchObject({
    kind: 'refused',
    error: own(refusal.body, 'error'),
  });
  // Rehydrate the genuine refused request: the editor itself prevents resending
  // malformed members. No API body is changed to manufacture this historical state.
  const state = {
    ...s.state,
    intents: [
      intent(s.state, {
        kind: 'settings_save',
        target: settingsTarget(s.context, moduleId),
        meta: { module: moduleId, values },
        phase: 'refused',
        answer: refusal,
        answerSeq: 2,
        answeredAtMs: s.state.nowMs,
      }),
    ],
  };
  const after = recorded(family + 'read_after_the_refused_send_back_nothing_changed');
  expect(settingsModule(settingsView(after.body), moduleId)).toEqual(
    settingsModule(s.view, moduleId),
  );
  const next = addBody(state, 'settings', after.body, { atMs: state.nowMs + 10 });
  const vm = settingsModuleVM(next, settingsView(after.body), moduleId, { pending: false });
  if (vm === null) throw new Error('Missing module');
  const onSave = vi.fn();
  const form = renderRegion(
    next,
    <SettingsModuleForm
      vm={vm}
      onEdit={vi.fn()}
      onDiscard={vi.fn()}
      onSave={onSave}
      onDismiss={vi.fn()}
    />,
  );
  const root = within(form.view.container);
  expect(root.getByText(String(own(refusal.body, 'error')), { exact: true })).toBeVisible();
  expect(root.getAllByRole('textbox').map((node) => node.getAttribute('value'))).toEqual([
    'false',
    '7',
    'keep',
  ]);
  fireEvent.click(root.getByRole('button', { name: /^Save$/u }));
  expect(onSave).not.toHaveBeenCalled();
  expect(vm.outcome?.code).toBe('refused');
});

it('C15-6 C15-7 r12f sibling-only save omits malformed drop, read-after retains raw values and repeated repaired save keeps revision', () => {
  const s = setup('read_with_malformed_tournament_cards');
  const module = settingsModule(s.view, moduleId);
  if (module === null) throw new Error('Missing module');
  // Raw draft parts represent the same explicit add-row edit as the browser journey.
  const state = reduceDashboard(s.state, {
    type: 'draft_edit',
    scope: s.scope,
    path: 'add',
    value: true,
  });
  const edited = reduceDashboard(state, {
    type: 'draft_edit',
    scope: s.scope,
    path: 'add#list:0',
    value: 'golden_orb',
  });
  const review = reviewModule(module, edited.runDrafts[s.scope] ?? {});
  const saved = recorded(
    family + 'saved_tournament_cards_add_list_only_the_malformed_drop_list_stays',
  );
  expect(review).toEqual({ values: own(saved.request.body, 'values'), invalid: {} });
  const sent = {
    module: moduleId,
    revision: String(own(saved.request.body, 'revision')),
    values: Object.fromEntries(entries(own(saved.request.body, 'values'))),
  };
  expect(settingsSaveAnswer(saved, sent)).toMatchObject({ kind: 'saved' });
  const after = recorded(
    family + 'read_after_the_add_list_was_saved_the_malformed_drop_list_remains',
  );
  expect(settleSettingsSave(after.body, sent, s.context)).toBe('applied');
  expect(
    settingsModule(settingsView(after.body), moduleId)?.fields.find((f) => f.path === 'drop')
      ?.value,
  ).toEqual(
    own(
      own(
        recorded(family + 'tournament_cards_sent_back_with_the_malformed_entries_is_refused')
          .request.body,
        'values',
      ),
      'drop',
    ),
  );
  const repaired = recorded(family + 'saved_tournament_cards_repairing_the_malformed_drop_list');
  const repairedSent = {
    module: moduleId,
    revision: String(own(repaired.request.body, 'revision')),
    values: Object.fromEntries(entries(own(repaired.request.body, 'values'))),
  };
  expect(settingsSaveAnswer(repaired, repairedSent)).toMatchObject({ kind: 'saved' });
  const repairedRead = recorded(family + 'read_after_the_malformed_drop_list_was_repaired');
  expect(settleSettingsSave(repairedRead.body, repairedSent, s.context)).toBe('applied');
  const repeated = recorded(family + 'saved_the_repaired_lists_again_keeps_the_revision');
  expect(own(repeated.request.body, 'revision')).toBe(own(repairedRead.body, 'revision'));
  expect(own(repeated.body, 'revision')).toBe(own(repairedRead.body, 'revision'));
  expect(
    settingsSaveAnswer(repeated, {
      module: moduleId,
      values: Object.fromEntries(entries(own(repeated.request.body, 'values'))),
    }),
  ).toMatchObject({ kind: 'saved' });
});
