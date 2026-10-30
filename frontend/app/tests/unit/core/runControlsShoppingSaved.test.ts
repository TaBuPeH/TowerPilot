import { expect, it } from 'vitest';
import { entries, own, ownPath } from '../../../src/core/read.ts';
import { savedSaveAnswer, settleSavedSave } from '../../../src/core/runControls.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const sentCase = sample('shopping_weighted_cadence_order_saved');
const sent = {
  blueprint: String(own(sentCase.request.body, 'blueprint')),
  profileRevision: String(own(sentCase.request.body, 'expected_revision')),
  changes: Object.fromEntries(entries(own(sentCase.request.body, 'changes'))),
};

it('SQ12 saved unsupported-run refusal is the recorded server refusal, never a receipt', () => {
  const refused = sample('refused_the_shopping_plan_the_run_does_not_support');
  expect(
    savedSaveAnswer(refused, {
      blueprint: String(own(refused.request.body, 'blueprint')),
      profileRevision: String(own(refused.request.body, 'expected_revision')),
      changes: Object.fromEntries(entries(own(refused.request.body, 'changes'))),
    }),
  ).toEqual({ kind: 'refused', code: own(refused.body, 'code'), conflict: false });
});

it('SQ7 SQ12 saved receipt holds every sent plan value, including order and hidden values', () => {
  expect(savedSaveAnswer(sentCase, sent).kind).toBe('saved');
  // Alter one member of the recorded receipt to prove a mismatching 200 is not confirmation.
  const wrong = {
    ...Object.fromEntries(entries(sentCase.body)),
    settings: {
      ...Object.fromEntries(entries(own(sentCase.body, 'settings'))),
      shopping_plan: {
        ...Object.fromEntries(entries(ownPath(sentCase.body, 'settings', 'shopping_plan'))),
        cash_reserve: 1,
      },
    },
  };
  expect(savedSaveAnswer({ status: 200, body: wrong }, sent)).toEqual({ kind: 'unknown' });
});

it('SQ12 saved text 500 remains Unknown until a later run read holds the full sent plan', () => {
  const failed = sample('shopping_save_reply_failed');
  const unknownSent = {
    blueprint: String(own(failed.request.body, 'blueprint')),
    profileRevision: String(own(failed.request.body, 'expected_revision')),
    changes: Object.fromEntries(entries(own(failed.request.body, 'changes'))),
  };
  expect(savedSaveAnswer(failed, unknownSent)).toEqual({ kind: 'unknown' });
  expect(
    settleSavedSave(sample('shopping_read_before_the_unanswered_save').body, unknownSent),
  ).toBeNull();
  expect(settleSavedSave(sample('shopping_read_after_unknown_save').body, unknownSent)).toBe(
    'applied',
  );
  expect(
    settleSavedSave(sample('shopping_other_run_after_private_save').body, unknownSent),
  ).toBeNull();
  expect(
    savedSaveAnswer(
      sample('shopping_identical_resend_after_the_unknown_save_is_refused_stale'),
      unknownSent,
    ).kind,
  ).toBe('refused');
  expect(savedSaveAnswer(failed, unknownSent)).toEqual({ kind: 'unknown' });
});
