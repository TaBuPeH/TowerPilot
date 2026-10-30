import { knownFact, unknownFact, type Fact } from './facts.ts';
import { own, record, text } from './read.ts';

/**
 * Launch guards (safety rule S-100): Start is judged by `setup_battle_guard`
 * (scope `run`) only, Collect and Turn on by `collection_guard` (scope
 * `collection`) only. Only an explicit `blocks_launch === false` permits; a
 * missing guard, a missing field or a record of the other scope is
 * unconfirmed. The two permits carry distinct type-only brands made only
 * here, so one can never be passed where the other is required
 * (tests/unit/core/guards.test-d.ts).
 */
declare const setupBrand: unique symbol;
declare const collectionBrand: unique symbol;

/** Permission to Start, from `setup_battle_guard` alone. */
export interface SetupBattleGuardPermit {
  /** When the guard was observed (server clock). */
  readonly observedAt: number;
  readonly [setupBrand]: true;
}
/** Permission to Collect or Turn on, from `collection_guard` alone. */
export interface CollectionGuardPermit {
  /** When the guard was observed (server clock). */
  readonly observedAt: number;
  readonly [collectionBrand]: true;
}

/** Why a guard does not permit: it blocks (with its own code), or it can't be confirmed. */
export type GuardRefusal =
  | { readonly kind: 'blocks'; readonly code: string | null; readonly message: unknown }
  | { readonly kind: 'unconfirmed'; readonly reason: GuardUnconfirmedReason };

export type GuardUnconfirmedReason =
  'guard_missing' | 'guard_not_object' | 'guard_scope_mismatch' | 'blocks_launch_missing';

export type GuardDecision<P> =
  | { readonly kind: 'permit'; readonly permit: P }
  | { readonly kind: 'refused'; readonly refusal: GuardRefusal };

function judge(
  guard: unknown,
  scope: 'run' | 'collection',
): { readonly kind: 'permit' } | { readonly kind: 'refused'; readonly refusal: GuardRefusal } {
  if (guard === undefined || guard === null) {
    return { kind: 'refused', refusal: { kind: 'unconfirmed', reason: 'guard_missing' } };
  }
  if (record(guard) === undefined) {
    return { kind: 'refused', refusal: { kind: 'unconfirmed', reason: 'guard_not_object' } };
  }
  if (own(guard, 'scope') !== scope) {
    return { kind: 'refused', refusal: { kind: 'unconfirmed', reason: 'guard_scope_mismatch' } };
  }
  const blocks = own(guard, 'blocks_launch');
  if (blocks === false) {
    return { kind: 'permit' };
  }
  if (blocks === true) {
    return {
      kind: 'refused',
      refusal: {
        kind: 'blocks',
        code: text(own(guard, 'code')) ?? null,
        message: own(guard, 'message'),
      },
    };
  }
  return { kind: 'refused', refusal: { kind: 'unconfirmed', reason: 'blocks_launch_missing' } };
}

/** S-100: Start may be offered only with this permit, judged from `setup_battle_guard` alone. */
export function setupGuard(
  setupBattleGuard: unknown,
  observedAt: number,
): GuardDecision<SetupBattleGuardPermit> {
  const decision = judge(setupBattleGuard, 'run');
  return decision.kind === 'permit'
    ? { kind: 'permit', permit: Object.freeze({ observedAt }) as SetupBattleGuardPermit }
    : decision;
}

/** S-100: Collect and Turn on may be offered only with this permit, judged from `collection_guard` alone. */
export function collectionGuard(
  collectionGuardRecord: unknown,
  observedAt: number,
): GuardDecision<CollectionGuardPermit> {
  const decision = judge(collectionGuardRecord, 'collection');
  return decision.kind === 'permit'
    ? { kind: 'permit', permit: Object.freeze({ observedAt }) as CollectionGuardPermit }
    : decision;
}

/** `setupGuard` as a Fact: the permit, or the reason it is withheld. */
export function setupPermit(
  setupBattleGuard: unknown,
  observedAt: number,
): Fact<SetupBattleGuardPermit> {
  const decision = setupGuard(setupBattleGuard, observedAt);
  return decision.kind === 'permit'
    ? knownFact(decision.permit, observedAt)
    : unknownFact(refusalReason(decision.refusal));
}

/** `collectionGuard` as a Fact: the permit, or the reason it is withheld. */
export function collectionPermit(
  collectionGuardRecord: unknown,
  observedAt: number,
): Fact<CollectionGuardPermit> {
  const decision = collectionGuard(collectionGuardRecord, observedAt);
  return decision.kind === 'permit'
    ? knownFact(decision.permit, observedAt)
    : unknownFact(refusalReason(decision.refusal));
}

function refusalReason(refusal: GuardRefusal): string {
  return refusal.kind === 'blocks' ? 'guard_blocks' : refusal.reason;
}

/** When the setup permit was observed (server clock). */
export function setupPermitObservedAt(permit: SetupBattleGuardPermit): number {
  return permit.observedAt;
}

/** When the collection permit was observed (server clock). */
export function collectionPermitObservedAt(permit: CollectionGuardPermit): number {
  return permit.observedAt;
}
