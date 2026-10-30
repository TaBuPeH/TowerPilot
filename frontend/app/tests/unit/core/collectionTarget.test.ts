import { expect, it } from 'vitest';
import { sentPatch, settleProfilePatch } from '../../../src/core/profilePatch.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

it('PE4 inactive settlement requires the original target and an advanced revision', () => {
  const saved = dashboardPagesCase('profile_target.inactive_gather_label_saved');
  const patch = sentPatch(saved.request.body);
  if (patch === null) throw new Error('recorded gathering label patch missing');
  const receipt = saved.body as { profile: unknown; revision: string };
  // Modified recorded source: the generator records this intermediate state only as a
  // patch receipt. Keep the recorded source envelope and its saved profile/revision.
  const read = {
    ...(dashboardPagesCase('profile_target.same_account_inactive_target_source').body as object),
    profile: receipt.profile,
    revision: receipt.revision,
  };
  expect(settleProfilePatch(read, patch)).toBe('applied');
  expect(settleProfilePatch({ ...read, name: 'mine' }, patch)).toBeNull();
  // Modified case: hold the sent value at the starting revision to prove that values
  // alone cannot satisfy the brief's required advanced-revision evidence.
  expect(settleProfilePatch({ ...read, revision: patch.revision }, patch)).toBeNull();
  expect(
    settleProfilePatch({ ...read, revision: patch.revision }, { ...patch, revision: null }),
  ).toBe('applied');
});
