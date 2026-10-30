import { confirmStopped, type Confirmed } from './evidence.ts';
import type { Fact } from './facts.ts';
import type { GateRead } from './gate.ts';
import { harvestView, type ReadableHarvest } from './readability.ts';

/**
 * The person's harvesting choice: D-7 Known On, D-8 Off confirmed, the Pause
 * control (S-001, INV-1) and the choice pill (S-032, S-033, S-034). The
 * choice is stored, not observed: it never becomes a chip word (spec 3.2).
 */

/** D-7: the highest acknowledged or read On revision, and where in the request sequence it was learned. */
export interface KnownOn {
  readonly revision: number;
  readonly learnedSeq: number;
}

/** One way an On was learned: an acknowledged Turn on (200, choice on, revision k) or a readable read with choice On. */
export interface OnLearned {
  readonly revision: number;
  readonly learnedSeq: number;
}

/** D-7: keep the highest k (the earliest point it was learned on a tie); null when no On is known. */
export function knownOn(learned: readonly OnLearned[]): KnownOn | null {
  let best: KnownOn | null = null;
  for (const item of learned) {
    if (!Number.isSafeInteger(item.revision) || item.revision < 0) {
      continue;
    }
    if (
      best === null ||
      item.revision > best.revision ||
      (item.revision === best.revision && item.learnedSeq < best.learnedSeq)
    ) {
      best = { revision: item.revision, learnedSeq: item.learnedSeq };
    }
  }
  return best;
}

/** D-7 from a readable read: its On revision, or null when the choice is not On. */
export function onFromRead(harvest: ReadableHarvest, readSeq: number): OnLearned | null {
  const view = harvestView(harvest);
  return view.choice === 'on' ? { revision: view.revision, learnedSeq: readSeq } : null;
}

/**
 * A Turn on whose outcome is not known yet (sending, lost or timed out),
 * with the `expected_revision` it was sent with (null when it had none).
 */
export interface UnsettledOn {
  readonly expectedRevision: number | null;
}

/**
 * D-8 Off confirmed: a readable read (D-1) with choice `off` or `unset`, an
 * integer revision strictly greater than every Known On k, and every On whose
 * outcome is not known settled by this read's revision being greater than its
 * `expected_revision` (enable is a compare-and-set, so it can no longer apply;
 * backend `Harvest.set_choice`, spec 5.1 D-8).
 */
export function offConfirmed(
  harvest: Fact<ReadableHarvest>,
  known: KnownOn | null,
  unsettled: readonly UnsettledOn[],
): boolean {
  if (harvest.kind === 'unknown') {
    return false;
  }
  const view = harvestView(harvest.value);
  if (view.choice === 'on') {
    return false;
  }
  if (known !== null && view.revision <= known.revision) {
    return false;
  }
  return unsettled.every(
    (on) => on.expectedRevision !== null && view.revision > on.expectedRevision,
  );
}

/**
 * S-001 / INV-1: Pause is shown whenever On may be in effect or the choice is
 * unknown, and hidden only when D-8 holds. With no status read at all it is
 * shown (a fallback never evaluates D-8 and always shows it, S-091).
 */
export function pauseOffered(
  harvest: Fact<ReadableHarvest> | null,
  known: KnownOn | null,
  unsettled: readonly UnsettledOn[],
): boolean {
  return harvest === null || !offConfirmed(harvest, known, unsettled);
}

/** The choice pill's value (spec 3.2): On, Off, Not set or Unknown. */
export type ChoiceWord = 'on' | 'off' | 'not_set' | 'unknown';

export interface ChoiceView {
  readonly word: ChoiceWord;
  /** S-033: an Off that is not strictly newer than a Known On is shown qualified. */
  readonly qualifier: 'from_before_turn_on' | null;
  /** The D-1 reason when the choice is unknown (S-032). */
  readonly reason: string | null;
}

/**
 * The choice pill (S-032, S-033, S-034): a malformed or contradictory record
 * reads "choice unknown"; after a Known On an Off that is not strictly newer
 * is stale and qualified. Computed from D-1 alone, never from what is seen.
 */
export function choiceView(harvest: Fact<ReadableHarvest>, known: KnownOn | null): ChoiceView {
  if (harvest.kind === 'unknown') {
    return { word: 'unknown', qualifier: null, reason: harvest.reason };
  }
  const view = harvestView(harvest.value);
  if (view.choice === 'on') {
    return { word: 'on', qualifier: null, reason: null };
  }
  const stale = known !== null && view.revision <= known.revision;
  return {
    word: view.choice === 'off' ? 'off' : 'not_set',
    qualifier: stale ? 'from_before_turn_on' : null,
    reason: null,
  };
}

/** What a Pause answer's `stopping` list says now, judged in a D-4 read (S-020, S-025). */
export type PauseSettlement =
  | { readonly kind: 'stopped'; readonly claims: readonly Confirmed<'stopped'>[] }
  | { readonly kind: 'still_stopping'; readonly operationIds: readonly string[] }
  | { readonly kind: 'not_confirmed'; readonly reason: 'choice_not_off' };

/**
 * A Pause confirmation needs choice Off or unset in the SAME D-4 read (S-025)
 * and every collector the answer named as stopping gone by D-5 (S-020). The
 * caller passes a D-4 read started after the answer (core/gate.ts
 * `evidenceGate(read, answerSeq)`); anything less is "not confirmed".
 */
export function settlePause(stopping: readonly string[], gate: GateRead): PauseSettlement {
  if (gate.view.choice === 'on') {
    return { kind: 'not_confirmed', reason: 'choice_not_off' };
  }
  const claims: Confirmed<'stopped'>[] = [];
  const present: string[] = [];
  for (const operationId of stopping) {
    const stopped = confirmStopped({ operationId, pid: null }, gate);
    if (stopped.kind === 'known') {
      claims.push(stopped.value);
    } else {
      present.push(operationId);
    }
  }
  return present.length > 0
    ? { kind: 'still_stopping', operationIds: present }
    : { kind: 'stopped', claims };
}
