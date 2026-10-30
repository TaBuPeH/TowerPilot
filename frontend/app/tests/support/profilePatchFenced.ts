import type { PageContext } from '../../src/api/context.ts';
import { entries, own } from '../../src/core/read.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from './dashboardFixture.ts';

export const fencedCase = (name: string) => dashboardPagesCase(`profile_patch_fenced.${name}`);
export const fencedRuntime: PageContext = {
  instance: 'main',
  serial: 'fake:1',
  account: 'alpha',
  profile: 'mine',
};
/** Every pre-write source is itself a recorded GET, including the refusal's unchanged read. */
export function fencedSourceBefore(name: string): DashboardCase {
  const revision = own(fencedCase(name).request.body, 'expected_revision');
  const found = dashboardPagesCaseNames('profile_patch_fenced')
    .map(fencedCase)
    .find((entry) => entry.request.method === 'GET' && own(entry.body, 'revision') === revision);
  if (name === 'active_run_entry_field_deleted') {
    const prior = fencedCase('read_after_active_run_entry_label_saved');
    const profile = own(prior.body, 'profile');
    const blueprints = own(profile, 'blueprints');
    // Modified recorded source: the producer's unrecorded setup adds this exact perk-ban
    // list at revision 16b before the fenced delete (dashboard_contract_cases_profile_fenced.py).
    return {
      ...prior,
      body: {
        ...Object.fromEntries(entries(prior.body)),
        revision,
        profile: {
          ...Object.fromEntries(entries(profile)),
          blueprints: {
            ...Object.fromEntries(entries(blueprints)),
            farm: {
              ...Object.fromEntries(entries(own(blueprints, 'farm'))),
              perk_bans: ['coins, but tower max health'],
            },
          },
        },
      },
    };
  }
  if (found === undefined) throw new Error(`No recorded source before ${name}`);
  if (name === 'active_gather_copy_saved') {
    const profile = own(found.body, 'profile');
    const policies = own(profile, 'policies');
    // Modified recorded source: the recorded copy writes GATHER, while active mine's
    // all_on still has its original settings. Supply that recorded copy source so this
    // UI copy journey can send the producer's exact fenced value and revision.
    return {
      ...found,
      body: {
        ...Object.fromEntries(entries(found.body)),
        profile: {
          ...Object.fromEntries(entries(profile)),
          policies: {
            ...Object.fromEntries(entries(policies)),
            gather: {
              ...Object.fromEntries(entries(own(policies, 'gather'))),
              all_on: own(fencedCase(name).request.body, 'value'),
            },
          },
        },
      },
    };
  }
  return found;
}
/** Modified recorded request: legacy journeys keep their own account, value and CAS revision.
 * Add only the F6-14b runtime fence; exact new-family bodies have separate page coverage. */
export function withRecordedFence(found: DashboardCase, context: PageContext): DashboardCase {
  if (
    found.request.path !== '/api/profile-patch' ||
    own(found.request.body, 'expected_context') !== undefined
  )
    return found;
  return {
    ...found,
    request: {
      ...found.request,
      body: {
        ...Object.fromEntries(entries(found.request.body)),
        expected_context: context,
      },
    },
  };
}
