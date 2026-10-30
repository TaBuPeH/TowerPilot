import type { ClaimState, Confirmed } from './evidence.ts';
import { record } from './read.ts';

/**
 * The page's state vocabulary as core decides it (spec 3.1, 3.6.1). Words and
 * evidence ids are codes; the feature layer maps each to catalogue text
 * (src/features/dashboard/words.ts, plan task R10), so no wording is chosen in
 * core.
 */

/** The eight status words (spec 3.1). No other state word exists. */
export type StateWord =
  'running' | 'waiting' | 'paused' | 'idle' | 'stopping' | 'unknown' | 'blocked' | 'failed';

export const STATE_WORDS: readonly StateWord[] = [
  'running',
  'waiting',
  'paused',
  'idle',
  'stopping',
  'unknown',
  'blocked',
  'failed',
];

/** The evidence register of spec 3.6.1. */
export type EvidenceCode =
  | 'EV-1'
  | 'EV-2'
  | 'EV-3'
  | 'EV-4'
  | 'EV-5'
  | 'EV-6'
  | 'EV-7'
  | 'EV-8'
  | 'EV-9'
  | 'EV-10'
  | 'EV-11';

/** Words that claim something observed: each needs an evidence id (spec 3.6). */
export const OBSERVED_WORDS: readonly StateWord[] = ['running', 'paused', 'idle'];

/** A parameter of a line: numbers and codes only (never wording). */
export type ParamValue = number | string | boolean | null | readonly string[];

/** Type-only brands: decisions and chips are made only by this module. */
declare const decisionBrand: unique symbol;
declare const recordBrand: unique symbol;
declare const chipBrand: unique symbol;

/**
 * The evidence each confirmed state stands for (spec 3.6.1). A chip that
 * claims one of these states must carry the claim core/evidence.ts issued
 * (ADR rule 2, A-01): `observed` takes the claim, and `decide` refuses these
 * codes at the type level.
 */
export const CLAIM_EVIDENCE = {
  collecting: 'EV-3',
  idle: 'EV-5',
  free: 'EV-6',
  input_stopped: 'EV-7',
  stopped: 'EV-8',
} as const satisfies Readonly<Record<ClaimState, EvidenceCode>>;

/** Evidence ids that need a confirmed claim. */
export type ClaimedEvidence = (typeof CLAIM_EVIDENCE)[ClaimState];
/** Evidence ids `decide` accepts without a claim. */
export type UnclaimedEvidence = Exclude<EvidenceCode, ClaimedEvidence>;

/**
 * One decision of a section 3.6 table: the first matching row, its word (or
 * null when the row shows plain text instead of a chip), the code of its line,
 * the evidence id that backs an observed word or plain text, and for the five
 * confirmed states the claim itself.
 */
export interface ChipDecision<Row extends string = string, Line extends string = string> {
  readonly row: Row;
  readonly word: StateWord | null;
  readonly line: Line;
  readonly evidence: EvidenceCode | null;
  /** Numbers and codes the line needs (ages, retry seconds, failed steps); never wording. */
  readonly params?: Readonly<Record<string, ParamValue>>;
  /** The core/evidence.ts claim behind a confirmed state (EV-3, EV-5 to EV-8), else null. */
  readonly claim: Confirmed<ClaimState> | null;
  readonly [decisionBrand]: true;
}

function freeze<Row extends string, Line extends string>(
  row: Row,
  word: StateWord | null,
  line: Line,
  evidence: EvidenceCode | null,
  claim: Confirmed<ClaimState> | null,
  params: Readonly<Record<string, ParamValue>> | undefined,
): ChipDecision<Row, Line> {
  return Object.freeze(
    params === undefined
      ? { row, word, line, evidence, claim }
      : { row, word, line, evidence, claim, params },
  ) as ChipDecision<Row, Line>;
}

/**
 * Build a decision; refuses an observed word or plain text without evidence
 * (spec 3.6). The evidence of a confirmed state (EV-3, EV-5 to EV-8) is not
 * accepted here: use `observed` with the claim.
 */
export function decide<Row extends string, Line extends string>(
  row: Row,
  word: StateWord | null,
  line: Line,
  evidence: UnclaimedEvidence | null,
  params?: Readonly<Record<string, ParamValue>>,
): ChipDecision<Row, Line> {
  if ((word === null || OBSERVED_WORDS.includes(word)) && evidence === null) {
    throw new Error(`Row ${row} claims an observation without evidence (spec 3.6)`);
  }
  return freeze(row, word, line, evidence, null, params);
}

/** A decision whose state core/evidence.ts confirmed: it carries the claim and its evidence id. */
export function observed<Row extends string, Line extends string, S extends ClaimState>(
  row: Row,
  word: StateWord | null,
  line: Line,
  claim: Confirmed<S>,
  params?: Readonly<Record<string, ParamValue>>,
): ChipDecision<Row, Line> {
  return freeze(row, word, line, CLAIM_EVIDENCE[claim.state], claim, params);
}

/**
 * Record-only plain text: it states what a record says, with no EV-1..EV-11
 * code; never label a record EV-9.
 */
export interface RecordDecision<Row extends string = string, Line extends string = string> {
  readonly row: Row;
  readonly word: null;
  readonly line: Line;
  readonly evidence: 'record';
  readonly claim: null;
  readonly record: unknown;
  readonly [recordBrand]: true;
}

export function recordDecision<Row extends string, Line extends string>(
  row: Row,
  line: Line,
  source: unknown,
): RecordDecision<Row, Line> {
  if (record(source) === undefined)
    throw new TypeError('Record evidence requires an own-property record');
  return Object.freeze({
    row,
    word: null,
    line,
    evidence: 'record',
    claim: null,
    record: source,
  }) as RecordDecision<Row, Line>;
}

/**
 * A decision as a view model shows it (spec 7.3): its row, word, line code,
 * evidence id, params (never wording) and claim, without a record's raw body.
 * Made only here, so a view model cannot hold a chip core did not decide.
 */
export interface Chip {
  readonly row: string;
  readonly word: StateWord | null;
  readonly line: string;
  /** The evidence id behind an observed word or plain text; `record` for a record-only line. */
  readonly evidence: EvidenceCode | 'record' | null;
  readonly params: Readonly<Record<string, ParamValue>>;
  readonly claim: Confirmed<ClaimState> | null;
  readonly [chipBrand]: true;
}

/** The chip of a decision (a record decision's raw record is not carried). */
export function chipOf(decision: ChipDecision | RecordDecision): Chip {
  return Object.freeze({
    row: decision.row,
    word: decision.word,
    line: decision.line,
    evidence: decision.evidence,
    params: decision.evidence === 'record' ? {} : (decision.params ?? {}),
    claim: decision.claim,
  }) as Chip;
}
