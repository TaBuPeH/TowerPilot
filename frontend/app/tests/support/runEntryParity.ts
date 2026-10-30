import { entries, own } from '../../src/core/read.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from './dashboardFixture.ts';

export const parityCase = (name: string) => dashboardPagesCase(`run_entry_patch_parity.${name}`);

/** Intermediate GETs are not recorded: use a recorded receipt's exact profile/revision
 * in the recorded source envelope. Never simulate a profile mutation. */
export function paritySourceBefore(entry: DashboardCase): DashboardCase {
  const revision = own(entry.request.body, 'expected_revision');
  const sources = dashboardPagesCaseNames('run_entry_patch_parity').map(parityCase);
  const found = sources.find(
    (item) => own(item.body, 'revision') === revision && own(item.body, 'profile') !== undefined,
  );
  const envelope = parityCase('read_before_the_label_edits');
  if (found !== undefined)
    return {
      ...envelope,
      body: {
        ...Object.fromEntries(entries(envelope.body)),
        profile: own(found.body, 'profile'),
        revision,
      },
    };
  throw new Error(`Missing source at ${String(revision)}`);
}
