import { chipOf, type Chip, type ChipDecision, type RecordDecision } from '../core/vocabulary.ts';

/**
 * Building blocks of the view models (spec 7.3): numbers, codes, `Fact`s,
 * core-made chips and `ServerText` only. No field is catalogue wording
 * (store/ cannot import copy/catalogue.ts) and no field is raw server data: a
 * server string a component shows is made a `ServerText` in a selector
 * (src/store/selectors/ only, spec 6.3), and the feature layer maps every code
 * to its words.
 */

/**
 * A chip of a section 3.6 table, exactly as core decided it (A-01): row, word,
 * line code, evidence id, params and, for a confirmed state, the
 * core/evidence.ts claim. Only core makes one (core/vocabulary.ts `chipOf`).
 */
export type ChipVM = Chip;

/** A core decision as a chip (a record decision's raw record is not carried). */
export function chipVM(decision: ChipDecision | RecordDecision): ChipVM {
  return chipOf(decision);
}

/** A control: whether it is drawn, whether it works, why not, and what one click sends. */
export interface CommandVM<C> {
  readonly shown: boolean;
  readonly enabled: boolean;
  /** Why it is hidden or disabled (a code), or null. */
  readonly reason: string | null;
  /** A request of this control is in flight now. */
  readonly sending: boolean;
  /** What one click sends; null when the click sends nothing of its own (Pause, Turn input off). */
  readonly command: C | null;
}

export function commandVM<C>(
  shown: boolean,
  enabled: boolean,
  reason: string | null,
  sending: boolean,
  command: C | null,
): CommandVM<C> {
  return Object.freeze({ shown, enabled, reason, sending, command });
}
