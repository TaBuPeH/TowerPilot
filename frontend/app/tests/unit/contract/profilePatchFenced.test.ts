// @vitest-environment node
import { expect, it } from 'vitest';
import { ENDPOINTS, parseAnswer } from '../../../src/api/wire.ts';
import { dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';
import { fencedCase } from '../../support/profilePatchFenced.ts';
it.each(dashboardPagesCaseNames('profile_patch_fenced'))(
  'F6-14b contract profile_patch_fenced.%s is exact on its endpoint',
  (name) => {
    const found = fencedCase(name);
    const endpoint =
      found.request.method === 'POST' ? ENDPOINTS.profilePatch : ENDPOINTS.profileSource;
    expect(parseAnswer(endpoint, found.status, JSON.stringify(found.body)).contract).toBe('exact');
  },
);
