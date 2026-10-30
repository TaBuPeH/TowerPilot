import { expect, it } from 'vitest';
import { entries, own } from '../../../src/core/read.ts';
import { settledByRead } from '../../../src/store/fences.ts';
import { saveOutcome } from '../../../src/store/selectors/settingsModule.ts';
import { dashboardPagesCase as recorded } from '../../support/dashboardFixture.ts';
import { moreCase } from '../../support/profileTargetMore.ts';
import { storeHarness } from '../../support/storeHarness.ts';

const context = { instance: 'main', serial: 'fake:1', account: 'alpha', profile: 'mine' };

it.each(['mine', 'second'] as const)(
  'PE6 F6-14c C-01 every definite patch refusal stays refused on matching reads of %s',
  (name) => {
    for (const caseName of [
      'inactive_run_entry_stale_revision_409',
      'inactive_run_entry_refused_after_account_switch',
      'inactive_tier_zero_is_refused',
      'inactive_patch_of_a_target_that_cannot_be_parsed_is_the_parser_error_400',
    ]) {
      const answer = moreCase(caseName);
      const h = storeHarness({ context });
      h.intent({
        id: 'patch',
        kind: 'profile_patch',
        target: 'entry',
        meta: { name, baseRevision: 'before', patchPath: ['tier'], patchValue: 3 },
      });
      h.answer('patch', answer, 'refused');
      // Modified source: a separate client saved exactly the requested value at another
      // revision. This tests eligibility independently of the refusal's original path.
      for (let read = 0; read < 2; read++) {
        h.read(name === 'mine' ? 'profileSource' : 'targetSource', {
          name,
          revision: `advanced-${read}`,
          profile: { tier: 3 },
        });
        const item = h.state().intents[0];
        if (item === undefined) throw new Error('Missing patch');
        expect(settledByRead(h.state(), item)).toBeNull();
        expect(saveOutcome(h.state(), 'entry')?.code).toBe(
          caseName.includes('account_switch') ? 'context_changed' : 'refused',
        );
      }
    }
  },
);

it('PE6 F6-14c C-01 settings saves retain their intentional conflict-applied readback', () => {
  const answer = recorded('settings.identical_resend_after_a_committed_save_is_refused_as_stale');
  const sent = answer.request.body;
  const h = storeHarness({ context: { ...context, account: null } });
  const item = h.intent({
    id: 'settings',
    kind: 'settings_save',
    target: 'settings',
    meta: {
      module: String(own(sent, 'module')),
      baseRevision: String(own(sent, 'revision')),
      values: Object.fromEntries(entries(own(sent, 'values'))),
    },
  });
  h.answer(item.id, answer, 'refused');
  h.read('settings', recorded('settings.read_after_every_module_was_saved').body);
  const current = h.state().intents[0];
  if (current === undefined) throw new Error('Missing settings save');
  expect(settledByRead(h.state(), current)).toBe('applied');
  expect(saveOutcome(h.state(), 'settings')?.code).toBe('conflict_applied');
});
