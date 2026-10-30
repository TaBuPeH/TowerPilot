import { expect, it } from 'vitest';
import {
  saveOutcome,
  settingsSavePending,
  settingsTarget,
} from '../../../../src/store/selectors/settingsModule.ts';
import { addBody, addFailure, intent, regionState } from '../../store/regionHelpers.ts';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';

function fixtureState() {
  const state = addBody(
    regionState(),
    'settings',
    dashboardPagesCase('settings.populated_account_every_module').body,
  );
  if (state.context === null) throw new Error('fixture context missing');
  const target = settingsTarget(state.context, 'tap');
  const lost = intent(state, {
    kind: 'settings_save',
    id: 'lost-settings',
    target,
    lostSeq: 2,
    accepted: false,
  });
  return { state: { ...state, intents: [lost] }, lost, target };
}
it('T1 T2 F6T-R1-01 the shared editor keeps an older unresolved record visible even when a newer legacy record would hide it', () => {
  const { state, lost, target } = fixtureState();
  const refusal = dashboardPagesCase('settings.stale_revision_another_client_saved_first');
  const newer = intent(state, {
    kind: 'settings_save',
    id: 'newer',
    target,
    phase: 'refused',
    answer: { status: refusal.status, body: refusal.body },
    sentSeq: 3,
    answerSeq: 4,
  });
  const legacy = { ...state, intents: [{ ...lost, dismissed: true }, newer] };
  expect(settingsSavePending(legacy, ['settings'])).toBe(true);
  expect(saveOutcome(legacy, target)).toMatchObject({
    id: lost.id,
    code: 'unknown',
    acceptOffered: true,
    dismissOffered: false,
  });
});
it('T1 T2 F6T-R1-01 acceptance needs a later successful same-context read; failure or another context never offers it', () => {
  const { state, target } = fixtureState();
  expect(saveOutcome(state, target)?.acceptOffered).toBe(true);
  expect(saveOutcome(addFailure(state, 'settings'), target)?.acceptOffered).toBe(false);
  const other = addBody(
    state,
    'settings',
    dashboardPagesCase('settings.read_after_the_switch_names_the_new_emulator').body,
  );
  expect(saveOutcome(other, target)?.acceptOffered).toBe(false);
  expect(settingsSavePending(other, ['settings'])).toBe(true);
});
