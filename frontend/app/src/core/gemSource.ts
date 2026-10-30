import type { ContextRead } from './connection.ts';
import { entries, finite, own, ownPath } from './read.ts';
import { contextsEqual, runDefined } from './readiness.ts';

/**
 * The "Gem settings from" picker of the Gem harvesting slot (F-025, F-026,
 * S-080, S-104): which saved runs it offers, which one it shows by default,
 * and the gem routines of the shown run. Codes and numbers only.
 */

/** A run the picker offers: defined and not the combo day plan (F-026). */
export interface GemSourceChoice {
  readonly key: string;
  /** The run's label as the server sent it (a selector makes it ServerText). */
  readonly label: unknown;
}

export function gemSourceChoices(runsBody: unknown): readonly GemSourceChoice[] {
  return entries(own(runsBody, 'runs'))
    .filter(([key, run]) => key !== 'combo' && own(run, 'kind') !== 'combo' && runDefined(run))
    .map(([key, run]) => ({ key, label: own(run, 'label') }));
}

/**
 * F-026's inventory order: the person's own pick, the desired source, the
 * resolved source, the Start picker's run, the first run. A value the list
 * does not offer is skipped.
 */
export function gemSourceDefault(
  choices: readonly GemSourceChoice[],
  candidates: readonly (string | null | undefined)[],
): string | null {
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && choices.some((choice) => choice.key === candidate)) {
      return candidate;
    }
  }
  return choices[0]?.key ?? null;
}

/** One gem routine of a saved run: on or off, and its interval when one is reported. */
export interface GemRoutinePolicy {
  readonly enabled: boolean;
  readonly intervalSec: number | null;
}

export interface GemPolicy {
  readonly adGems: GemRoutinePolicy | null;
  readonly floatingGems: GemRoutinePolicy | null;
}

function routine(value: unknown): GemRoutinePolicy | null {
  const enabled = own(value, 'enabled');
  if (typeof enabled !== 'boolean') return null;
  const interval = finite(own(value, 'interval_sec'));
  return { enabled, intervalSec: interval !== undefined && interval > 0 ? interval : null };
}

/** The gem routines of `harvest.source.policy` or of a run-controls `settings.routines`. */
export function gemPolicy(routines: unknown): GemPolicy | null {
  const adGems = routine(own(routines, 'ad_gems'));
  const floatingGems = routine(own(routines, 'floating_gems'));
  return adGems === null && floatingGems === null ? null : { adGems, floatingGems };
}

/** S-104: a source counts only with at least one gem routine on. */
export function policyCollects(policy: GemPolicy | null): boolean {
  return policy?.adGems?.enabled === true || policy?.floatingGems?.enabled === true;
}

/** The run-controls blueprint of a run key (backend/dashboard.py strips `bp_`). */
export function blueprintOf(run: string): string {
  return run.startsWith('bp_') ? run.slice(3) : run;
}

/**
 * F-111, S-014: a saved preview counts only when it answered for this run's
 * blueprint and the page's context (instance, account, profile).
 */
export function previewPolicy(
  preview: ContextRead,
  run: string,
  pageContext: unknown,
): GemPolicy | null {
  if (preview?.kind !== 'answered' || !preview.sameContext) return null;
  if (own(preview.body, 'blueprint') !== blueprintOf(run)) return null;
  if (!contextsEqual(own(preview.body, 'context'), pageContext, ['instance', 'account', 'profile']))
    return null;
  return gemPolicy(ownPath(preview.body, 'settings', 'routines'));
}
