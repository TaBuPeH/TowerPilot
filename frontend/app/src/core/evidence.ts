import { collectionEvidence } from './collection.ts';
import { tapsSetting } from './config.ts';
import { knownFact, unknownFact, type Fact } from './facts.ts';
import { gateOf, nothingRecorded, type GateRead } from './gate.ts';
import { enableUnresolvedBefore, turnOffSuperseded, type InputFacts } from './inputFacts.ts';
import { unresolvedLeaseHolders, type KnownLeaseHolder } from './lease.ts';
import { provesInputStopped } from './outcomes/input.ts';
import { ownerGone, type OwnerIdentity } from './ownership.ts';
import { harvestView, type ReadableHarvest } from './readability.ts';

/** The brand of a confirmed claim: type-only, so no other module can write one. */
declare const confirmedBrand: unique symbol;

/**
 * The five states a claim may confirm (spec 3.1, 5.1): collecting, idle,
 * free, stopped and input stopped. No other state is ever confirmed.
 */
export type ClaimState = 'collecting' | 'idle' | 'free' | 'stopped' | 'input_stopped';

/**
 * A state claim backed by the evidence its definition requires (ADR rule 2).
 * A type-only brand: an object literal, a spread copy or any value not made
 * here lacks the brand, and type assertions are refused by lint outside this
 * file. Only the five checked constructors below make claims (spec 5.1,
 * R7-01): there is no generic issuer, so no caller can confirm a state from a
 * timestamp or from a record that did not pass its definition's gate. Core's
 * chip decisions carry the claim behind every observed word they confirm
 * (core/vocabulary.ts `observed`), so it reaches the view models.
 */
export interface Confirmed<S extends ClaimState> {
  readonly state: S;
  /** Seconds since the Unix epoch, server clock. */
  readonly observedAt: number;
  readonly [confirmedBrand]: S;
}

/** What a confirmed claim says. */
export interface ConfirmedReading<S extends ClaimState> {
  readonly state: S;
  /** Seconds since the Unix epoch, server clock. */
  readonly observedAt: number;
}

/** What `claim` says, as a plain record. */
export function readConfirmed<S extends ClaimState>(claim: Confirmed<S>): ConfirmedReading<S> {
  return Object.freeze({ state: claim.state, observedAt: claim.observedAt });
}

function issue<S extends ClaimState>(state: S, observedAt: number): Fact<Confirmed<S>> {
  const claim = Object.freeze({ state, observedAt }) as Confirmed<S>;
  return knownFact(claim, observedAt);
}

/**
 * The five evidence claims (spec 5.1, ADR rule 2). Each calls the checks of
 * its definition and then the private constructor above; nothing else can
 * produce a `Confirmed` value. `observedAt` is the server time (H) of the
 * read that proved it.
 */

/** EV-3, D-6 (S-030): green Collecting only for the current `harvest.operation`. */
export function confirmCollecting(harvest: ReadableHarvest): Fact<Confirmed<'collecting'>> {
  const evidence = collectionEvidence(harvest);
  if (evidence.kind === 'unknown') {
    return evidence;
  }
  return issue('collecting', harvestView(harvest).serverTime);
}

/**
 * EV-5 (S-027, S-073): Idle needs a D-4 read with choice Off or unset, no
 * `harvest.operation`, no `harvest.owner`, no process row, and no lease
 * holder this page knows of in this context that this read does not resolve.
 */
export function confirmIdle(
  gate: GateRead,
  leaseHolders: readonly KnownLeaseHolder[],
): Fact<Confirmed<'idle'>> {
  if (gate.view.choice === 'on') {
    return unknownFact('choice_on');
  }
  if (!nothingRecorded(gate)) {
    return unknownFact('automation_recorded');
  }
  if (unresolvedLeaseHolders(leaseHolders, gate).length > 0) {
    return unknownFact('lease_holder_unresolved');
  }
  return issue('idle', gate.serverTime);
}

/**
 * EV-6 (S-026, S-073): free means no owner of any kind in a D-4 read: no
 * process row, no `harvest.operation`, no `harvest.owner`, and no lease holder
 * this page knows of that this read does not resolve.
 */
export function confirmFree(
  gate: GateRead,
  leaseHolders: readonly KnownLeaseHolder[],
): Fact<Confirmed<'free'>> {
  if (!nothingRecorded(gate)) {
    return unknownFact('automation_recorded');
  }
  if (unresolvedLeaseHolders(leaseHolders, gate).length > 0) {
    return unknownFact('lease_holder_unresolved');
  }
  return issue('free', gate.serverTime);
}

/** EV-8 (S-020, D-5): a named owner is stopped only when D-5 holds in a D-4 read. */
export function confirmStopped(owner: OwnerIdentity, gate: GateRead): Fact<Confirmed<'stopped'>> {
  if (!ownerGone(owner, gate.ownership)) {
    return unknownFact(
      owner.operationId === null && owner.pid === null ? 'owner_unidentified' : 'owner_present',
    );
  }
  return issue('stopped', gate.serverTime);
}

/**
 * EV-7 (S-040, S-047, S-048, D-4): input stopped needs the newest turn-off
 * answer to prove it (S-040), no newer input fact superseding it (S-047), no
 * enable sent before it still unresolved (S-048), a later D-4 status read
 * (started after the answer) with `input_revoked === true`, and a config read
 * started after the answer showing `allow_taps: false`. Supersession and
 * unresolved enables are decided here from the facts, never taken from the
 * caller. The answer alone is a past result, never the current state.
 */
export function confirmInputStopped(facts: InputFacts): Fact<Confirmed<'input_stopped'>> {
  const turnOff = facts.turnOff;
  if (turnOff === null) {
    return unknownFact('no_turn_off_answer');
  }
  if (!provesInputStopped(turnOff.answer)) {
    return unknownFact('answer_not_proving');
  }
  if (turnOffSuperseded(facts)) {
    return unknownFact('superseded');
  }
  if (enableUnresolvedBefore(facts, turnOff.answerSeq)) {
    return unknownFact('earlier_enable_unresolved');
  }
  if (facts.status === null) {
    return unknownFact('no_later_status');
  }
  const gate = gateOf(facts.status, turnOff.answerSeq);
  if (gate.kind === 'unknown') {
    return gate;
  }
  if (gate.value.view.inputRevoked !== true) {
    return unknownFact('input_revoked_not_true');
  }
  const taps = tapsSetting(facts.config);
  if (taps.kind === 'unknown') {
    return unknownFact(taps.reason);
  }
  if (taps.startedSeq <= turnOff.answerSeq) {
    return unknownFact('no_later_config');
  }
  if (taps.allowTaps) {
    return unknownFact('allow_taps_true');
  }
  return issue('input_stopped', gate.value.serverTime);
}
