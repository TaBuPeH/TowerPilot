import { tapsSetting, type TapsSetting } from './config.ts';
import { confirmFree, confirmInputStopped, confirmStopped } from './evidence.ts';
import { gateOf, type ReadAssessment } from './gate.ts';
import { anyEnableUnresolved, turnOffSuperseded, type InputFacts } from './inputFacts.ts';
import type { KnownLeaseHolder } from './lease.ts';
import { classifyInputAnswer, provesInputStopped, type StoppingDetail } from './outcomes/input.ts';
import { ownerIdentity } from './ownership.ts';
import { own } from './read.ts';
import { harvestView } from './readability.ts';
import { decide, observed, type ChipDecision } from './vocabulary.ts';

/**
 * The Game input slot (spec 3.6.3, safety rules S-006, S-047, S-048, INV-2).
 * No row says Running: no backend field reports input actually being
 * delivered. A turn-off answer is a past result: it decides a row only through
 * I3, I4 and I4a, and only while no newer input fact supersedes it (S-047).
 */

/** The Game input slot's facts: the input facts of core/inputFacts.ts plus what ownership adds. */
export interface GameInputFacts extends InputFacts {
  /** Lease owners this page knows of (S-073); I5 never says free while one is unresolved. */
  readonly leaseHolders: readonly KnownLeaseHolder[];
  /** S-092 / S-059: a retained or unverified holder this page still keeps a Stop for. */
  readonly retainedHolder: boolean;
}

export type InputRow = 'I1' | 'I2' | 'I3' | 'I4' | 'I4a' | 'I5' | 'I6';

export type InputLine =
  | 'cant_read_input_setting'
  | 'earlier_turn_on_no_answer'
  | 'turn_off_not_confirmed_for'
  | 'turn_off_step_failed'
  | 'turn_off_not_confirmed'
  | 'automation_input_stopped'
  | 'turned_off_not_yet_confirmed'
  | 'no_automation_using_input'
  | 'cant_tell_whats_running'
  | 'taps_may_be_allowed'
  | 'input_revocation_not_confirmed';

export type InputDecision = ChipDecision<InputRow, InputLine>;

/** The first matching row of the Game input table (spec 3.6.3). */
export function gameInputSeen(facts: GameInputFacts): InputDecision {
  const taps: TapsSetting = tapsSetting(facts.config);
  if (taps.kind === 'unknown') {
    return decide('I1', 'unknown', 'cant_read_input_setting', null, { reason: taps.reason });
  }
  if (anyEnableUnresolved(facts)) {
    return decide('I2', 'unknown', 'earlier_turn_on_no_answer', null);
  }
  const superseded = turnOffSuperseded(facts);
  const turnOff = facts.turnOff;
  if (turnOff !== null && !superseded) {
    const answer = classifyInputAnswer(turnOff.answer, 'disable');
    if (
      (answer.kind === 'stopping' || answer.kind === 'stopping_with_errors') &&
      !stoppingConfirmed(answer, facts.status, turnOff.answerSeq)
    ) {
      if (answer.stopping.length > 0) {
        return decide('I3', 'stopping', 'turn_off_not_confirmed_for', null, {
          collectors: answer.stopping,
        });
      }
      if (answer.kind === 'stopping_with_errors') {
        return decide('I3', 'stopping', 'turn_off_step_failed', null, {
          steps: answer.errors.map((error) => error.step ?? 'unknown_step'),
        });
      }
      return decide('I3', 'stopping', 'turn_off_not_confirmed', null);
    }
    const confirmed = confirmInputStopped(facts);
    if (confirmed.kind === 'known') {
      return observed('I4', 'idle', 'automation_input_stopped', confirmed.value);
    }
    if (provesInputStopped(turnOff.answer)) {
      return decide('I4a', 'unknown', 'turned_off_not_yet_confirmed', null, {
        reason: confirmed.reason,
      });
    }
  }
  const gate = facts.status === null ? null : gateOf(facts.status, null);
  if (gate !== null && gate.kind === 'known') {
    const free = confirmFree(gate.value, facts.leaseHolders);
    if (free.kind === 'known') {
      return observed('I5', 'idle', 'no_automation_using_input', free.value);
    }
  }
  if (gate === null || gate.kind === 'unknown') {
    return decide('I6', 'unknown', 'cant_tell_whats_running', null);
  }
  return taps.allowTaps
    ? decide('I6', 'unknown', 'taps_may_be_allowed', null)
    : decide('I6', 'unknown', 'input_revocation_not_confirmed', null);
}

/**
 * S-042: a `stopping` answer becomes confirmed only through S-020 for those
 * collectors - D-5 (core/evidence.ts `confirmStopped`) in a D-4 read started
 * after the answer, with each collector's PID when the answer names its owner
 * - and with choice Off or unset in the same read (S-025).
 */
function stoppingConfirmed(
  answer: StoppingDetail,
  status: ReadAssessment | null,
  answerSeq: number,
): boolean {
  if (status === null || answer.stopping.length === 0) {
    return false;
  }
  const gate = gateOf(status, answerSeq);
  if (gate.kind === 'unknown' || gate.value.view.choice === 'on') {
    return false;
  }
  const named = answer.owners.map((entry) => ownerIdentity(entry.owner));
  return answer.stopping.every((operationId) => {
    const identity = named.find((owner) => owner.operationId === operationId) ?? {
      operationId,
      pid: null,
    };
    return confirmStopped(identity, gate.value).kind === 'known';
  });
}

/**
 * S-042, F-021: the newest turn-off answer (not superseded) was `stopping`,
 * and a later D-4 read confirmed every collector it named stopped, with choice
 * Off or unset in that read. A past result about those collectors; it says
 * nothing about the input setting, so an unreadable config does not matter.
 */
export function turnOffStoppingSettled(facts: GameInputFacts): boolean {
  const turnOff = facts.turnOff;
  if (turnOff === null || turnOffSuperseded(facts)) return false;
  const answer = classifyInputAnswer(turnOff.answer, 'disable');
  return (
    (answer.kind === 'stopping' || answer.kind === 'stopping_with_errors') &&
    stoppingConfirmed(answer, facts.status, turnOff.answerSeq)
  );
}

/** Why Turn input off is shown (INV-2, S-006), first that applies; null when it is not shown. */
export type InputOffReason =
  | 'taps_allowed'
  | 'setting_unknown'
  | 'enable_unresolved'
  | 'harvest_not_reported'
  | 'status_unknown'
  | 'automation_may_run'
  | 'automation_listed'
  | 'input_not_idle';

/**
 * F-017: Allow game input is offered only while the Setting reads Off and a
 * D-4 read proves the emulator free (EV-6, the same facts that gate Idle): no
 * listed or recorded owner, no lease holder this page knows of that the read
 * does not resolve (F4-03, X10), and no holder this page keeps a Stop for.
 */
export function allowInputOffered(facts: GameInputFacts): boolean {
  const taps = tapsSetting(facts.config);
  if (taps.kind !== 'known' || taps.allowTaps || facts.retainedHolder || facts.status === null) {
    return false;
  }
  const gate = gateOf(facts.status, null);
  return gate.kind === 'known' && confirmFree(gate.value, facts.leaseHolders).kind === 'known';
}

/** True when any automation is listed or recorded in the newest answered read, or retained. */
function automationRecorded(facts: GameInputFacts): boolean {
  if (facts.retainedHolder) {
    return true;
  }
  const status = facts.status;
  if (status === null || !status.answered) {
    return false;
  }
  const processes = own(status.body, 'processes');
  const harvest = own(status.body, 'harvest');
  const listed = Array.isArray(processes) && processes.length > 0;
  const operation = own(harvest, 'operation');
  const owner = own(harvest, 'owner');
  return (
    listed ||
    (operation !== undefined && operation !== null) ||
    (owner !== undefined && owner !== null)
  );
}

/**
 * INV-2 with S-006: Turn input off is rendered (and enabled) whenever input
 * may be on or its state is unknown - the Setting not confirmed Off, an enable
 * unresolved, harvest unreadable or absent, the scan failing D-2, automation
 * listed or recorded without `input_revoked === true` - whenever the Game
 * input chip is not Idle, and whenever any automation is listed or recorded.
 */
export function inputOffOffered(facts: GameInputFacts, seen: InputDecision): InputOffReason | null {
  const taps = tapsSetting(facts.config);
  if (taps.kind === 'unknown') {
    return 'setting_unknown';
  }
  if (taps.allowTaps) {
    return 'taps_allowed';
  }
  if (anyEnableUnresolved(facts)) {
    return 'enable_unresolved';
  }
  const status = facts.status;
  if (status === null || !status.answered) {
    return 'status_unknown';
  }
  if (own(status.body, 'harvest') === undefined || own(status.body, 'harvest') === null) {
    return 'harvest_not_reported';
  }
  if (status.harvest.kind === 'unknown' || status.scan.kind === 'unknown') {
    return 'status_unknown';
  }
  const recorded = automationRecorded(facts);
  if (recorded && harvestView(status.harvest.value).inputRevoked !== true) {
    return 'automation_may_run';
  }
  if (status.ownership.kind === 'unknown') {
    return 'status_unknown';
  }
  if (recorded) {
    return 'automation_listed';
  }
  return seen.word === 'idle' ? null : 'input_not_idle';
}
