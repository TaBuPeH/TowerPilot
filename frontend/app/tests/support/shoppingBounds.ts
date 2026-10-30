import { entries, list, own, ownPath } from '../../src/core/read.ts';
import { entryKey, type QueueDraft } from '../../src/core/shoppingPlan.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from './dashboardFixture.ts';

export const bounds = (name: string) => dashboardPagesCase(`shopping_queue_bounds.${name}`);

/** Raw numeric editor fields for the exact plan recorded in a bounds POST. */
export function boundsDraft(name: string): QueueDraft {
  const plan = ownPath(bounds(name).request.body, 'changes', 'shopping_plan');
  const draft: Record<string, string | boolean> = {};
  for (const item of list(own(plan, 'queue')) ?? []) {
    const id = own(item, 'id');
    if (typeof id !== 'string') throw new Error(`missing bounds upgrade: ${name}`);
    for (const field of ['tap_limit', 'weight', 'from_wave', 'until_wave'] as const) {
      const value = own(item, field);
      if (value !== null && typeof value !== 'number')
        throw new Error(`bounds case is not a numeric editor draft: ${name}`);
      draft[entryKey(id, field)] = value === null ? '' : String(value);
    }
  }
  return draft;
}

/** Text/bool JSON values have no equivalent in the numeric text editors. Core tests cover them. */
export function numericBoundsCases(target: 'saved' | 'live'): readonly string[] {
  return dashboardPagesCaseNames('shopping_queue_bounds').filter((name) => {
    if (!name.startsWith(`${target}_`)) return false;
    const plan = ownPath(bounds(name).request.body, 'changes', 'shopping_plan');
    return (list(own(plan, 'queue')) ?? []).every((item) =>
      entries(item).every(
        ([key, value]) =>
          key === 'id' || key === 'enabled' || value === null || typeof value === 'number',
      ),
    );
  });
}
