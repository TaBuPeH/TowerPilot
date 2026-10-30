import { expect, it } from 'vitest';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { storeHarness } from '../../../support/storeHarness.ts';

it('F6P-R2-01 an older matching source cannot confirm a later tier request; a fresh original-profile source can', () => {
  const h = storeHarness();
  const source = dashboardPagesCase(
    'accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance',
  ).body;
  h.read('profileSource', source);
  h.intent({
    id: 'tier',
    kind: 'account_tier',
    meta: { maxTier: 12 },
    phase: 'unconfirmed',
    lostReason: 'network',
  });
  h.read('accounts', dashboardPagesCase('accounts_write.read_after_the_tier').body);
  expect(h.state().readSettlements['tier']).toBeUndefined();
  h.read('profileSource', source);
  expect(h.state().readSettlements['tier']?.settlement).toBe('applied');
});
