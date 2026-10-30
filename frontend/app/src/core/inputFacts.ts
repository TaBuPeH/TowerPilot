import { tapsSetting, type ConfigRead } from './config.ts';
import type { ReadAssessment } from './gate.ts';
import { harvestView } from './readability.ts';

/**
 * The game-input facts of one context (safety rules S-040, S-047, S-048): the
 * newest config and status reads, the Allow game input requests this page sent
 * and the newest Turn input off answer. The store keeps these records; core
 * decides supersession and unresolved enables from them, here, so EV-7 never
 * rests on a caller's own verdict (R7-01).
 */

/** One Allow game input request this page sent, and whether its outcome is known. */
export interface EnableRequest {
  /** The request sequence point at which it was sent. */
  readonly sentSeq: number;
  /** True once its answer arrived or a later read settled it (S-048). */
  readonly settled: boolean;
}

/** The newest Turn input off answer in this context. */
export interface TurnOffAnswer {
  /** `{status, body}` as received. */
  readonly answer: unknown;
  /** The request sequence point at which it arrived. */
  readonly answerSeq: number;
}

export interface InputFacts {
  /** The newest config read. */
  readonly config: ConfigRead | null;
  /** The newest status read, assessed (a failed read included). */
  readonly status: ReadAssessment | null;
  readonly enables: readonly EnableRequest[];
  readonly turnOff: TurnOffAnswer | null;
}

/**
 * S-047: the turn-off outcome becomes history as soon as a newer input fact
 * exists: a newer enable request sent (even pending), a config read started
 * after the answer with `allow_taps: true`, or a later readable status (D-1,
 * started after the answer) without `input_revoked: true`.
 */
export function turnOffSuperseded(facts: InputFacts): boolean {
  const turnOff = facts.turnOff;
  if (turnOff === null) {
    return false;
  }
  if (facts.enables.some((enable) => enable.sentSeq > turnOff.answerSeq)) {
    return true;
  }
  const taps = tapsSetting(facts.config);
  if (taps.kind === 'known' && taps.startedSeq > turnOff.answerSeq && taps.allowTaps) {
    return true;
  }
  const status = facts.status;
  return (
    status !== null &&
    status.startedSeq > turnOff.answerSeq &&
    status.harvest.kind === 'known' &&
    harvestView(status.harvest.value).inputRevoked !== true
  );
}

/** S-048: an enable sent before `seq` whose outcome is not known yet. */
export function enableUnresolvedBefore(facts: InputFacts, seq: number): boolean {
  return facts.enables.some((enable) => !enable.settled && enable.sentSeq < seq);
}

/** I2 of spec 3.6.3: any enable sent and not yet settled. */
export function anyEnableUnresolved(facts: InputFacts): boolean {
  return facts.enables.some((enable) => !enable.settled);
}
