import { expect, it } from 'vitest';
import { own, ownPath } from '../../../src/core/read.ts';
import { applyRecordedIn, savedApplyRecordedIn } from '../../../src/core/recognition.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

const body = (name: string) => dashboardPagesCase(`recognition_context_fence.${name}`).body;
const before = body('read_the_profile_after_the_malformed_requests_nothing_was_applied');
const applied = body('read_the_profile_after_the_matching_apply');
const calibration = body('read_the_calibration_whose_apply_the_page_must_settle_by_reading');
const observations = ownPath(calibration, 'state', 'player');

it('R-FENCE apply_by_operation_is_applied_and_the_answer_is_lost settles only its operation and sent revision', () => {
  const operation = own(calibration, 'operation');
  const operationId = own(operation, 'id');
  if (typeof operationId !== 'string') throw new Error('Fixture operation id required');
  expect(applyRecordedIn(calibration, operationId, 3)).toBe(true);
  expect(applyRecordedIn(calibration, operationId, 2)).toBe(false);
  expect(
    applyRecordedIn(
      body('read_the_calibration_after_the_fence_refusal_it_still_awaits_apply'),
      operationId,
      3,
    ),
  ).toBe(false);
});

it('R-FENCE apply_saved_composed_for_alpha_is_applied_and_the_answer_is_lost keeps Unknown on unchanged reads', () => {
  expect(savedApplyRecordedIn(before, observations, before)).toBe(false);
  expect(
    savedApplyRecordedIn(before, observations, body('read_the_profile_after_the_legacy_apply')),
  ).toBe(false);
  expect(savedApplyRecordedIn(before, observations, applied)).toBe(true);
  expect(savedApplyRecordedIn(applied, observations, applied)).toBe(false);
  expect(savedApplyRecordedIn(null, observations, applied)).toBe(false);
});
