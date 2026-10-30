import { knownFact, unknownFact, type Fact } from './facts.ts';
import { finite, hasOwn, own, record, revision, text } from './read.ts';

/**
 * D-1 Readable harvest (safety rules, Definitions): the only gate through which
 * a `/api/status` `harvest` record reaches the collection and ownership
 * helpers (S-031). A record that fails D-1 means the choice is unknown and NO
 * field of it counts as evidence for anything.
 *
 * `ReadableHarvest` carries a type-only brand made only here, so no object
 * literal or spread copy is one, and type assertions are refused by lint
 * outside this file.
 */
declare const readableBrand: unique symbol;

/** The person's harvesting choice as D-1 accepts it. */
export type HarvestChoice = 'on' | 'off' | 'unset';

/** What a readable harvest record says, field by field; sub-records stay raw for their own checks. */
export interface ReadableHarvestView {
  /** `harvest.server_time` (H): the only clock every timing rule compares against. */
  readonly serverTime: number;
  readonly choice: HarvestChoice;
  /** `desired.revision`, a safe integer >= 0. */
  readonly revision: number;
  /** `code` when it is a string, else null. */
  readonly code: string | null;
  /** `state` when it is a string, else null. */
  readonly state: string | null;
  /** `input_revoked` exactly as sent; only boolean `true` counts as revoked (S-007). */
  readonly inputRevoked: unknown;
  /** True when the `operation` key is present (D-3 needs the key). */
  readonly hasOperationKey: boolean;
  readonly operation: unknown;
  /** True when the `owner` key is present (D-3 needs the key). */
  readonly hasOwnerKey: boolean;
  readonly owner: unknown;
  readonly host: unknown;
  readonly source: unknown;
  readonly overlay: unknown;
  readonly identity: unknown;
  readonly desired: unknown;
  readonly nextAttemptAt: unknown;
  readonly circuitOpen: unknown;
  readonly collectionGuard: unknown;
  readonly message: unknown;
}

/** A harvest record that passed D-1. */
export interface ReadableHarvest {
  readonly view: ReadableHarvestView;
  readonly [readableBrand]: true;
}

const CHOICES: readonly (readonly [HarvestChoice, boolean | null])[] = [
  ['on', true],
  ['off', false],
  ['unset', null],
];

/**
 * D-1: `harvest` is a plain object with `version === 1`, a finite
 * `server_time`, and a plain-object `desired` whose `revision` is a safe
 * integer >= 0 and whose `choice`/`enabled` agree (`on`/true, `off`/false,
 * `unset`/null); `code` is not `status_failed` or `harvest_state_unreadable`.
 * Anything else is unknown, with the reason.
 */
export function readHarvest(raw: unknown): Fact<ReadableHarvest> {
  if (record(raw) === undefined) {
    return unknownFact(raw === undefined ? 'harvest_missing' : 'harvest_not_object');
  }
  if (own(raw, 'version') !== 1) {
    return unknownFact('harvest_version');
  }
  const serverTime = finite(own(raw, 'server_time'));
  if (serverTime === undefined) {
    return unknownFact('server_time_invalid');
  }
  const code = text(own(raw, 'code')) ?? null;
  if (code === 'status_failed' || code === 'harvest_state_unreadable') {
    return unknownFact(code);
  }
  const desired = own(raw, 'desired');
  if (record(desired) === undefined) {
    return unknownFact('desired_invalid');
  }
  const rev = revision(own(desired, 'revision'));
  if (rev === undefined) {
    return unknownFact('desired_revision_invalid');
  }
  const choiceRaw = own(desired, 'choice');
  const enabled = own(desired, 'enabled');
  const choice = CHOICES.find(([name, flag]) => name === choiceRaw && flag === enabled)?.[0];
  if (choice === undefined) {
    return unknownFact('desired_contradictory');
  }
  const view: ReadableHarvestView = Object.freeze({
    serverTime,
    choice,
    revision: rev,
    code,
    state: text(own(raw, 'state')) ?? null,
    inputRevoked: own(raw, 'input_revoked'),
    hasOperationKey: hasOwn(raw, 'operation'),
    operation: own(raw, 'operation'),
    hasOwnerKey: hasOwn(raw, 'owner'),
    owner: own(raw, 'owner'),
    host: own(raw, 'host'),
    source: own(raw, 'source'),
    overlay: own(raw, 'overlay'),
    identity: own(raw, 'identity'),
    desired,
    nextAttemptAt: own(raw, 'next_attempt_at'),
    circuitOpen: own(raw, 'circuit_open'),
    collectionGuard: own(raw, 'collection_guard'),
    message: own(raw, 'message'),
  });
  return knownFact(Object.freeze({ view }) as ReadableHarvest, serverTime);
}

/** What a readable harvest says. */
export function harvestView(harvest: ReadableHarvest): ReadableHarvestView {
  return harvest.view;
}

/** S-007: input counts as revoked only when the record passed D-1 and says boolean `true`. */
export function inputRevoked(harvest: ReadableHarvest): boolean {
  return harvestView(harvest).inputRevoked === true;
}
