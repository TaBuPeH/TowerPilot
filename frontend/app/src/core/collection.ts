import { knownFact, unknownFact, type Fact } from './facts.ts';
import { entries, finite, hasOwn, id, own, record, revision, text } from './read.ts';
import { harvestView, type ReadableHarvest } from './readability.ts';

/**
 * Collection evidence (D-6, safety rules S-028, S-030, S-035) and the fresh
 * publication of a battle (EV-2 of spec 3.6.1). Running, active, a heartbeat
 * or `worker_alive` alone never prove collection or input (S-028): only a
 * fresh, valid publication of the battle by the current operation, with a gem
 * routine exactly `true`, does. Every time is compared with H, the
 * `harvest.server_time` of the same body, never with the browser clock.
 */

/** `backend/control/harvest.py` FRESH_SEC: the oldest publication that counts. */
export const FRESH_SEC = 15;
/** How far an evidence time may lie after H (D-6: "no evidence time is later than H+1"). */
export const EVIDENCE_FUTURE_SEC = 1;

/** The battle identity a fresh publication vouches for. */
export interface PublishedBattle {
  readonly id: string;
  readonly generation: string;
  readonly publishedAt: number;
  readonly ageSec: number;
}

/**
 * EV-2, the battle conditions: `fresh === true`, `observation_status`
 * `observing`, `status` `active` or absent, `publication_valid === true`,
 * string `id` and `generation`, `published_at` finite within [H-15, H],
 * `observation_age_sec` finite within 0..15 and within 1 s of H - published_at.
 */
export function freshPublication(battle: unknown, serverTime: number): PublishedBattle | null {
  if (record(battle) === undefined || !Number.isFinite(serverTime)) {
    return null;
  }
  if (own(battle, 'fresh') !== true || own(battle, 'observation_status') !== 'observing') {
    return null;
  }
  if (hasOwn(battle, 'status') && own(battle, 'status') !== 'active') {
    return null;
  }
  if (own(battle, 'publication_valid') !== true) {
    return null;
  }
  const battleId = text(own(battle, 'id'));
  const generation = text(own(battle, 'generation'));
  if (battleId === undefined || generation === undefined) {
    return null;
  }
  const publishedAt = finite(own(battle, 'published_at'));
  const ageSec = finite(own(battle, 'observation_age_sec'));
  if (publishedAt === undefined || ageSec === undefined) {
    return null;
  }
  if (publishedAt < serverTime - FRESH_SEC || publishedAt > serverTime) {
    return null;
  }
  if (ageSec < 0 || ageSec > FRESH_SEC || Math.abs(ageSec - (serverTime - publishedAt)) > 1) {
    return null;
  }
  return Object.freeze({ id: battleId, generation, publishedAt, ageSec });
}

/** Evidence times of `operation.evidence`: none may lie after H+1 (D-6). */
function evidenceTimesValid(evidence: unknown, serverTime: number): boolean {
  if (record(evidence) === undefined) {
    return true;
  }
  const limit = serverTime + EVIDENCE_FUTURE_SEC;
  for (const key of ['observed_through_at', 'started_at']) {
    if (hasOwn(evidence, key)) {
      const time = finite(own(evidence, key));
      if (time === undefined || time > limit) {
        return false;
      }
    }
  }
  // Decision 6: a malformed latest_confirmations container is not a blocker,
  // but a confirmation time it does state may not lie in the future.
  for (const [, confirmation] of entries(own(evidence, 'latest_confirmations'))) {
    const time = finite(own(confirmation, 'observed_at'));
    if (time !== undefined && time > limit) {
      return false;
    }
  }
  return true;
}

/**
 * EV-2 for `harvest.operation`: a string `id`, `status` `running`,
 * `worker_alive === true`, a fresh publication of its battle,
 * `evidence.generation` equal to the battle's generation when present, and no
 * evidence time later than H+1.
 */
export function operationPublication(harvest: ReadableHarvest): PublishedBattle | null {
  const view = harvestView(harvest);
  const operation = view.operation;
  if (id(own(operation, 'id')) === undefined) {
    return null;
  }
  if (own(operation, 'status') !== 'running' || own(operation, 'worker_alive') !== true) {
    return null;
  }
  const battle = freshPublication(own(operation, 'battle'), view.serverTime);
  if (battle === null) {
    return null;
  }
  const evidence = own(operation, 'evidence');
  if (hasOwn(evidence, 'generation') && own(evidence, 'generation') !== battle.generation) {
    return null;
  }
  return evidenceTimesValid(evidence, view.serverTime) ? battle : null;
}

/** EV-2 for an operation `harvest.owner`: kind `operation`, `status` `running`, a fresh publication of `owner.battle`. */
export function ownerPublication(harvest: ReadableHarvest): PublishedBattle | null {
  const view = harvestView(harvest);
  const owner = view.owner;
  if (own(owner, 'kind') !== 'operation' || id(own(owner, 'operation_id')) === undefined) {
    return null;
  }
  if (own(owner, 'status') !== 'running') {
    return null;
  }
  return freshPublication(own(owner, 'battle'), view.serverTime);
}

/** The gem routine flags of a `routines` record: exactly true, exactly false, or anything else. */
export interface GemFlags {
  readonly adGems: unknown;
  readonly floatingGems: unknown;
}

/** `routines.ad_gems.enabled` and `routines.floating_gems.enabled`, or null when `routines` is not a plain object. */
export function gemFlags(routines: unknown): GemFlags | null {
  if (record(routines) === undefined) {
    return null;
  }
  return {
    adGems: own(own(routines, 'ad_gems'), 'enabled'),
    floatingGems: own(own(routines, 'floating_gems'), 'enabled'),
  };
}

/** What D-6 proves: which operation collects, in which battle, with which routines on. */
export interface CollectionEvidence {
  readonly operationId: string;
  readonly battle: PublishedBattle;
  readonly adGems: boolean;
  readonly floatingGems: boolean;
  /** `operation.harvest_managed === false`: started by Collect gems only. */
  readonly startedByCollectOnly: boolean;
}

/**
 * D-6 Collection evidence for the current `harvest.operation` (EV-3): D-1
 * (the argument is a ReadableHarvest, S-031), `input_revoked === false`, EV-2
 * for the operation, `routines` a plain object and at least one gem routine
 * `enabled` exactly `true`.
 */
export function collectionEvidence(harvest: ReadableHarvest): Fact<CollectionEvidence> {
  const view = harvestView(harvest);
  if (view.inputRevoked !== false) {
    return unknownFact('input_revoked_not_false');
  }
  const operation = view.operation;
  const operationId = id(own(operation, 'id'));
  if (operationId === undefined) {
    return unknownFact('no_operation');
  }
  const battle = operationPublication(harvest);
  if (battle === null) {
    return unknownFact('publication_not_fresh');
  }
  const flags = gemFlags(own(operation, 'routines'));
  if (flags === null) {
    return unknownFact('routines_invalid');
  }
  const adGems = flags.adGems === true;
  const floatingGems = flags.floatingGems === true;
  if (!adGems && !floatingGems) {
    return unknownFact('no_gem_routine_on');
  }
  return knownFact(
    Object.freeze({
      operationId,
      battle,
      adGems,
      floatingGems,
      startedByCollectOnly: own(operation, 'harvest_managed') === false,
    }),
    view.serverTime,
  );
}

/** What EV-4 proves: a run's own gem report. */
export interface RunGemReport {
  readonly operationId: string;
  readonly battle: PublishedBattle;
  readonly adGems: boolean;
  readonly floatingGems: boolean;
}

/**
 * EV-4 (S-035): a full driver collects gems only with `owner.gathers === true`,
 * `owner.gems.basis` `published_routines`, a gem flag exactly `true`, and EV-2
 * for `owner.battle`. Basis `stale` or `not_reported` is unknown; kind `run`
 * never implies gathering.
 */
export function runGemReport(harvest: ReadableHarvest): Fact<RunGemReport> {
  const view = harvestView(harvest);
  const owner = view.owner;
  const operationId = id(own(owner, 'operation_id'));
  if (own(owner, 'kind') !== 'operation' || operationId === undefined) {
    return unknownFact('no_operation_owner');
  }
  const gems = own(owner, 'gems');
  if (own(gems, 'basis') !== 'published_routines') {
    return unknownFact('gems_not_published');
  }
  const battle = ownerPublication(harvest);
  if (battle === null) {
    return unknownFact('publication_not_fresh');
  }
  if (own(owner, 'gathers') !== true) {
    return unknownFact('not_gathering');
  }
  const adGems = own(gems, 'ad_gems') === true;
  const floatingGems = own(gems, 'floating_gems') === true;
  if (!adGems && !floatingGems) {
    return unknownFact('no_gem_flag');
  }
  return knownFact(Object.freeze({ operationId, battle, adGems, floatingGems }), view.serverTime);
}

/** The latest ad window that did not succeed, or the collector restart, as the backend classified it. */
export interface GemMissCause {
  /** The backend's cause code (`backend/control/harvest.py` CAUSE_CODES); the copy words each one. */
  readonly code: string;
  /** Seconds before `harvest.server_time`, or null when the record gives no usable time. */
  readonly agoSec: number | null;
}

/**
 * What this battle's gem collection did, as the backend published it: the
 * confirmation counters of `evidence` (durable lower bounds, never totals),
 * the ad windows that ran out, the consecutive failed claims and the cause of
 * the latest miss. Every field is null when the record does not give a valid
 * value; absence is never a zero.
 */
export interface GemOutcomes {
  readonly adConfirmations: number | null;
  readonly orbitConfirmations: number | null;
  readonly windowsMissed: number | null;
  readonly failureStreak: number | null;
  readonly cause: GemMissCause | null;
  /**
   * The backend's claims.finder_suspected, read as published: true when
   * frames are seen but the recognizer matches nothing. Never computed here;
   * anything but a boolean reads null.
   */
  readonly finderSuspected: boolean | null;
}

/**
 * `operation` (the standalone collector) or `owner` (a full driver) outcomes,
 * read only from a fresh publication of its battle (EV-2): an older one
 * describes something that may have changed. Counters from another battle
 * generation than the publication's are not this battle's. Null when nothing
 * usable is published.
 */
export function gemOutcomes(
  harvest: ReadableHarvest,
  holder: 'operation' | 'owner',
): GemOutcomes | null {
  const view = harvestView(harvest);
  const battle = holder === 'operation' ? operationPublication(harvest) : ownerPublication(harvest);
  if (battle === null) {
    return null;
  }
  const source = holder === 'operation' ? view.operation : view.owner;
  const evidence = own(source, 'evidence');
  const claims = own(source, 'claims');
  const counted =
    record(evidence) !== undefined &&
    own(evidence, 'generation') === battle.generation &&
    evidenceTimesValid(evidence, view.serverTime);
  const causeCode = id(own(own(claims, 'cause'), 'code'));
  const at = finite(own(own(claims, 'cause'), 'at'));
  const finder = own(claims, 'finder_suspected');
  const outcomes: GemOutcomes = {
    adConfirmations: counted ? (revision(own(evidence, 'ad_claim_confirmations')) ?? null) : null,
    orbitConfirmations: counted
      ? (revision(own(evidence, 'orbit_balance_confirmations')) ?? null)
      : null,
    windowsMissed: revision(own(claims, 'windows_missed')) ?? null,
    failureStreak: revision(own(claims, 'failure_streak')) ?? null,
    cause:
      causeCode === undefined
        ? null
        : { code: causeCode, agoSec: at === undefined ? null : Math.max(0, view.serverTime - at) },
    finderSuspected: typeof finder === 'boolean' ? finder : null,
  };
  return Object.values(outcomes).every((value) => value === null) ? null : outcomes;
}
