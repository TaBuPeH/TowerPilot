import { finite, list, own, record, stringList, text } from '../read.ts';
import { readOwnerRecord, type OwnerRecord } from '../ownership.ts';

/**
 * Turn input off and Allow game input answers (`POST /api/automation-access`,
 * safety rules S-040 to S-045). The answer is a past result: it decides the
 * current Game input chip only through the I3, I4 and I4a rows of spec 3.6.3
 * and only while no newer input fact supersedes it (S-047); the current chip
 * "input stopped" needs EV-7 (core/evidence.ts `confirmInputStopped`).
 */

/** One `still_owned` entry: a D-3-valid owner with its input fence state. */
export interface StillOwned {
  readonly owner: OwnerRecord;
  /** `refused` (fenced), `unfenced`, or another value as sent. */
  readonly input: unknown;
  /** The owner's own recovery as sent (`stop_operation`, `pause_harvesting` or null). */
  readonly recovery: unknown;
}

/** One failed step of a revocation (`errors[]`), by its step code. */
export interface FailedStep {
  readonly step: string | null;
  readonly message: unknown;
}

export type InputAnswerClass =
  /** S-040: a proving answer; the setting saved. */
  | {
      readonly kind: 'input_stopped';
      readonly stopped: readonly string[];
      readonly settingSaved: true;
    }
  /** S-045: proving, but `config_persisted` false: a warning that the setting may turn back on. */
  | { readonly kind: 'input_stopped_not_saved'; readonly stopped: readonly string[] }
  /** S-040: nothing was running. */
  | { readonly kind: 'nothing_running'; readonly settingSaved: boolean }
  /** S-041: `not_enforced` or an `unfenced` owner: a warning naming each owner and its way out. */
  | {
      readonly kind: 'not_enforced';
      readonly owners: readonly StillOwned[];
      readonly unfenced: readonly StillOwned[];
    }
  /**
   * S-042: still stopping, unconfirmed; names the collectors. S-044: with
   * failed steps (`stopping_with_errors`), each named, with a retry. Both keep
   * every fact of the answer: the owners, and S-041's unfenced owners with
   * their way out, independently of the stopping state.
   */
  | ({ readonly kind: 'stopping' | 'stopping_with_errors' } & StoppingDetail)
  /** S-043: HTTP 500 `input_revocation_failed`: input NOT revoked; what was done is listed separately. */
  | {
      readonly kind: 'revocation_failed';
      readonly error: unknown;
      readonly errors: readonly FailedStep[];
      readonly stopped: readonly string[];
      readonly harvestPaused: HarvestPausedClaim | null;
      readonly settingSaved: boolean | null;
    }
  /** An enable that the server applied. */
  | { readonly kind: 'enabled' }
  /** S-049: a refused enable (409 `device_owned`): the shared owner notice handles it. */
  | { readonly kind: 'enable_refused_owner'; readonly body: unknown }
  /** A definite refusal of an enable (for example 409 `action_lock`). */
  | { readonly kind: 'enable_refused'; readonly code: string | null; readonly error: unknown }
  /** Anything else: the outcome is unknown (never green). */
  | { readonly kind: 'unknown'; readonly reason: string };

/** Everything a `stopping` answer says (S-041, S-042, S-044, S-046). */
export interface StoppingDetail {
  readonly stopping: readonly string[];
  readonly owners: readonly StillOwned[];
  /** S-041: the owners still sending input unfenced, each to be named with its way out. */
  readonly unfenced: readonly StillOwned[];
  readonly errors: readonly FailedStep[];
  readonly harvestPaused: HarvestPausedClaim | null;
}

/** The answer's "harvesting paused" claim, which belongs to its revision (S-046). */
export interface HarvestPausedClaim {
  readonly paused: true;
  readonly revision: number | null;
}

function stillOwned(value: unknown): readonly StillOwned[] | null {
  const entries = list(value);
  if (entries === undefined) {
    return null;
  }
  const out: StillOwned[] = [];
  for (const entry of entries) {
    const owner = readOwnerRecord(entry);
    if (owner === undefined || owner === null) {
      return null;
    }
    out.push({ owner, input: own(entry, 'input'), recovery: own(entry, 'recovery') });
  }
  return out;
}

function failedSteps(value: unknown): readonly FailedStep[] | null {
  const entries = list(value);
  if (entries === undefined) {
    return null;
  }
  return entries.map((entry) => ({
    step: text(own(entry, 'step')) ?? null,
    message: own(entry, 'message'),
  }));
}

function harvestPaused(revocation: unknown): HarvestPausedClaim | null {
  const harvest = own(revocation, 'harvest');
  if (own(harvest, 'paused') !== true) {
    return null;
  }
  const rev = own(harvest, 'revision');
  return {
    paused: true,
    revision: typeof rev === 'number' && Number.isSafeInteger(rev) ? rev : null,
  };
}

/** The four lists of a revocation in contract shape, or null. */
interface RevocationLists {
  readonly errors: readonly unknown[];
  readonly stopping: readonly string[];
  readonly stopped: readonly string[];
  readonly stillOwnedRaw: readonly unknown[];
}

function lists(revocation: unknown): RevocationLists | null {
  const errors = list(own(revocation, 'errors'));
  const stopping = stringList(own(revocation, 'stopping'));
  const stopped = stringList(own(revocation, 'stopped'));
  const stillOwnedRaw = list(own(revocation, 'still_owned'));
  if (
    errors === undefined ||
    stopping === undefined ||
    stopped === undefined ||
    stillOwnedRaw === undefined
  ) {
    return null;
  }
  return { errors, stopping, stopped, stillOwnedRaw };
}

/**
 * S-040: a proving turn-off answer. HTTP 200 with `revocation` in contract
 * shape, and either `state: 'input_stopped'` with `fenced` and `latched`
 * true, `errors: []`, `stopping: []` and every `still_owned` entry a
 * D-3-valid owner with `input: 'refused'`; or `state: 'nothing_running'` with
 * every list empty.
 */
export function provesInputStopped(answer: unknown): boolean {
  if (own(answer, 'status') !== 200) {
    return false;
  }
  const body = own(answer, 'body');
  if (own(body, 'ok') !== true) {
    return false;
  }
  const revocation = own(body, 'revocation');
  if (record(revocation) === undefined) {
    return false;
  }
  const found = lists(revocation);
  if (found === null) {
    return false;
  }
  const state = own(revocation, 'state');
  if (state === 'nothing_running') {
    return (
      found.errors.length === 0 &&
      found.stopping.length === 0 &&
      found.stopped.length === 0 &&
      found.stillOwnedRaw.length === 0
    );
  }
  if (state !== 'input_stopped') {
    return false;
  }
  if (own(revocation, 'fenced') !== true || own(revocation, 'latched') !== true) {
    return false;
  }
  if (found.errors.length > 0 || found.stopping.length > 0) {
    return false;
  }
  const owners = stillOwned(found.stillOwnedRaw);
  return owners !== null && owners.every((entry) => entry.input === 'refused');
}

/** Classify a turn-off or enable answer (`{status, body}`, or null for a lost answer). */
export function classifyInputAnswer(
  answer: unknown,
  request: 'disable' | 'enable',
): InputAnswerClass {
  const status = finite(own(answer, 'status'));
  const body = own(answer, 'body');
  if (status === undefined || record(body) === undefined) {
    return { kind: 'unknown', reason: 'no_answer' };
  }
  if (request === 'enable') {
    if (status === 200 && own(body, 'ok') === true && own(body, 'enabled') === true) {
      return { kind: 'enabled' };
    }
    if (status === 409 && own(body, 'code') === 'device_owned') {
      return { kind: 'enable_refused_owner', body };
    }
    if (status === 409 && typeof own(body, 'code') === 'string') {
      return {
        kind: 'enable_refused',
        code: text(own(body, 'code')) ?? null,
        error: own(body, 'error'),
      };
    }
    return { kind: 'unknown', reason: 'enable_answer_not_in_contract' };
  }
  const revocation = own(body, 'revocation');
  const found = lists(revocation);
  if (status === 500 && own(body, 'code') === 'input_revocation_failed') {
    const persisted = own(revocation, 'config_persisted');
    return {
      kind: 'revocation_failed',
      error: own(body, 'error'),
      errors: failedSteps(own(revocation, 'errors')) ?? [],
      stopped: found?.stopped ?? [],
      harvestPaused: harvestPaused(revocation),
      settingSaved: typeof persisted === 'boolean' ? persisted : null,
    };
  }
  if (status !== 200 || own(body, 'ok') !== true || found === null) {
    return { kind: 'unknown', reason: 'answer_not_in_contract' };
  }
  const owners = stillOwned(found.stillOwnedRaw);
  if (owners === null) {
    return { kind: 'unknown', reason: 'still_owned_malformed' };
  }
  const state = own(revocation, 'state');
  const persisted = own(revocation, 'config_persisted');
  if (provesInputStopped(answer)) {
    if (state === 'nothing_running') {
      return { kind: 'nothing_running', settingSaved: persisted === true };
    }
    return persisted === true
      ? { kind: 'input_stopped', stopped: found.stopped, settingSaved: true }
      : { kind: 'input_stopped_not_saved', stopped: found.stopped };
  }
  const unfenced = owners.filter((entry) => entry.input === 'unfenced');
  if (state === 'stopping') {
    const detail: StoppingDetail = {
      stopping: found.stopping,
      owners,
      unfenced,
      errors: failedSteps(found.errors) ?? [],
      harvestPaused: harvestPaused(revocation),
    };
    return { kind: detail.errors.length > 0 ? 'stopping_with_errors' : 'stopping', ...detail };
  }
  if (state === 'not_enforced' || unfenced.length > 0) {
    return { kind: 'not_enforced', owners, unfenced };
  }
  return { kind: 'unknown', reason: 'revocation_state_unknown' };
}
