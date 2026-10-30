import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from './dashboardFixture.ts';

/** The pages fixture's collection_first_policy family (recorded in r12; no reconstructed answers). */
export const firstPolicyCaseNames = dashboardPagesCaseNames('collection_first_policy');

export function firstPolicyCase(name: string): DashboardCase {
  return dashboardPagesCase(`collection_first_policy.${name}`);
}
