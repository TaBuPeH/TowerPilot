import { entries, own } from '../../src/core/read.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from './dashboardFixture.ts';

export const moreCase = (name: string) => dashboardPagesCase(`profile_target_more.${name}`);
export const moreNames = dashboardPagesCaseNames('profile_target_more');

/** Intermediate GETs are not recorded. Wrap only an exact recorded receipt's
 * profile/revision in the family's source envelope; never synthesize profile edits. */
export function moreSourceBefore(entry: DashboardCase): DashboardCase {
  const revision = own(entry.request.body, 'expected_revision');
  const envelope = moreCase('read_before_the_first_inactive_run_entry_save');
  const found = moreNames
    .map(moreCase)
    .find(
      (item) => own(item.body, 'revision') === revision && own(item.body, 'profile') !== undefined,
    );
  if (found === undefined) throw new Error(`No recorded source for ${String(revision)}`);
  return {
    ...envelope,
    body: {
      ...Object.fromEntries(entries(envelope.body)),
      profile: own(found.body, 'profile'),
      revision,
    },
  };
}
