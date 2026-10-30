import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';
import type { DashboardCase } from './dashboardFixture.ts';

/** The genuine Profiles refusals of the pages fixture's context_fence family that the specs replay. */
const NAMES = [
  'account_tier_composed_for_alpha_is_refused_after_the_switch',
  'accounts_create_composed_for_alpha_is_refused_after_the_switch',
  'accounts_expected_context_null_is_refused',
  'accounts_select_composed_for_alpha_is_refused_after_the_switch',
  'copy_for_an_account_that_is_not_bound_is_refused',
  'profile_activate_composed_for_alpha_is_refused_after_the_switch',
  'profile_copy_composed_for_alpha_is_refused_after_the_switch',
  'profile_promote_composed_for_alpha_is_refused_after_the_switch',
  'stale_tier_while_a_runner_owns_the_device_is_told_the_context_changed',
  'the_same_request_resent_after_a_lost_answer_is_refused',
  'tier_expected_context_missing_the_serial',
  'tier_expected_context_not_an_object',
  'tier_expected_context_null_is_refused_not_read_as_absent',
  'tier_expected_context_serial_not_text',
  'tier_expected_context_with_an_unknown_key',
  'tier_when_the_configuration_names_an_unknown_account_is_refused',
] as const;

const fence = (
  loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture as {
    readonly cases: { readonly context_fence: Readonly<Record<string, DashboardCase>> };
  }
).cases.context_fence;

export const profilesContextCases: readonly (readonly [string, DashboardCase])[] = NAMES.map(
  (name) => {
    const found = fence[name];
    if (found === undefined)
      throw new Error(`Missing recorded Profiles context_fence case ${name}`);
    return [name, found] as const;
  },
);

export function profilesContextCase(name: string): DashboardCase {
  const found = profilesContextCases.find(([key]) => key === name)?.[1];
  if (found === undefined) throw new Error(`Missing recorded Profiles context_fence case ${name}`);
  return structuredClone(found);
}
