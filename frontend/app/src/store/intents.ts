import { own } from '../core/read.ts';
import { classifyHarvestAnswer } from '../core/outcomes/harvest.ts';
import { classifyRefusal } from '../core/outcomes/refusal.ts';
import { knownOn, type KnownOn, type OnLearned, type UnsettledOn } from '../core/harvestChoice.ts';
import type { EnableRequest, TurnOffAnswer } from '../core/inputFacts.ts';
import type { KnownLeaseHolder } from '../core/lease.ts';
import { answerRevisionClaims, type RevisionClaim } from '../core/stopRevision.ts';
import type { ContextView, DashboardState, IntentView } from './types.ts';

/**
 * The write records as core's inputs (spec 7.3; safety rules S-003, S-005,
 * S-047, S-048, S-050, S-073). A record from another context is hidden and
 * changes nothing here (S-014). A record made before the page knew any
 * context (null) is bound to no context: its line stays visible (marked so),
 * and it may only make the page MORE careful here - an unresolved enable,
 * a lease holder, an unsettled Turn on - never support a claim: its answer is
 * never a turn-off receipt, a revision or a Known On of the current context.
 */
/** Context-scoped drafts must wait until config confirms their persistence boundary. */
export function hasConfirmedContext(
  state: Pick<DashboardState, 'context' | 'contextConfirmed'>,
): boolean {
  return state.contextConfirmed && state.context !== null;
}

/**
 * The draft-scope key of a context (emulator, serial, account, profile): the editors' drafts
 * (Runs saved settings, the Profiles forms) are kept under it, so a draft made for one
 * account is never shown or sent in another. `none` before any context is known.
 */
export function contextScopeKey(context: ContextView | null): string {
  return context === null
    ? 'none'
    : JSON.stringify([context.instance, context.serial, context.account, context.profile]);
}

export function sameContextView(a: ContextView | null, b: ContextView | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    a.instance === b.instance &&
    a.serial === b.serial &&
    a.account === b.account &&
    a.profile === b.profile
  );
}

/** True when `record` belongs to the page's current context. */
export function inCurrentContext(state: DashboardState, record: IntentView): boolean {
  return record.context === null || sameContextView(record.context, state.context);
}

/** Every record whose line the current context shows (its own, and context-less ones), oldest first. */
export function contextIntents(state: DashboardState): readonly IntentView[] {
  return state.intents.filter((record) => inCurrentContext(state, record));
}

/** Only the records made in the current, named context: the ones whose answers count as its evidence. */
export function boundIntents(state: DashboardState): readonly IntentView[] {
  return state.intents.filter(
    (record) => record.context !== null && sameContextView(record.context, state.context),
  );
}

/** True while the record's outcome is unknown (in flight or unconfirmed). */
export function unresolvedIntent(record: IntentView): boolean {
  return record.phase === 'sending' || record.phase === 'unconfirmed';
}

/**
 * D-8: every Turn on whose outcome is not known, with its expected revision
 * (a read with a higher revision settles it: enable is a compare-and-set).
 */
export function unsettledOns(records: readonly IntentView[]): readonly UnsettledOn[] {
  return records
    .filter((record) => record.kind === 'turn_on' && unresolvedIntent(record))
    .map((record) => ({ expectedRevision: record.meta.expectedRevision ?? null }));
}

/**
 * D-7: the highest acknowledged On, from readable reads (kept by the reducer)
 * and from Turn on answers that turned it on. A replaced Turn on's late
 * answer still counts here: a CAS that applied before a later Pause carries a
 * lower revision than that Pause, so it can never hide Pause (S-001, S-003).
 */
export function knownOnOf(state: DashboardState, records: readonly IntentView[]): KnownOn | null {
  const learned: OnLearned[] = state.knownOnFromReads === null ? [] : [state.knownOnFromReads];
  for (const record of records) {
    if (record.kind !== 'turn_on' || record.answer === null || record.answerSeq === null) continue;
    const answer = classifyHarvestAnswer(record.answer, 'turn_on');
    if (answer.kind === 'turned_on') {
      learned.push({ revision: answer.revision, learnedSeq: record.answerSeq });
    }
  }
  return knownOn(learned);
}

/** S-048: every Allow game input sent in this context, and whether its outcome is known. */
export function enableRequests(records: readonly IntentView[]): readonly EnableRequest[] {
  return records
    .filter((record) => record.kind === 'input_on')
    .map((record) => ({ sentSeq: record.firstSentSeq, settled: !unresolvedIntent(record) }));
}

/** The newest Turn input off answer in this context (a past result; core decides what it proves). */
export function turnOffAnswer(records: readonly IntentView[]): TurnOffAnswer | null {
  let newest: TurnOffAnswer | null = null;
  for (const record of records) {
    if (record.kind !== 'input_off' || record.answer === null || record.answerSeq === null)
      continue;
    if (newest === null || record.answerSeq > newest.answerSeq) {
      newest = {
        answer: { status: record.answer.status, body: record.answer.body },
        answerSeq: record.answerSeq,
      };
    }
  }
  return newest;
}

/**
 * S-050: revisions learned outside the reads - every answer's operation,
 * recovery and owner recovery (Stop answers, coded conflicts, owner notices)
 * and each Stop button's own revision.
 */
export function answerClaims(records: readonly IntentView[]): readonly RevisionClaim[] {
  const claims: RevisionClaim[] = [];
  for (const record of records) {
    if (record.kind === 'stop' && record.meta.operationId !== undefined) {
      claims.push({ operationId: record.meta.operationId, revision: record.meta.expectedRevision });
    }
    if (record.answer !== null) claims.push(...answerRevisionClaims(record.answer.body));
  }
  return claims;
}

/**
 * S-073, S-053: the lease owners this page learned of - a `device_owned_lease`
 * notice's owner, and a Stop answer's `stop.holder` - each with the point it
 * arrived. Core decides whether a later D-4 read resolves them.
 */
export function knownLeaseHolders(records: readonly IntentView[]): readonly KnownLeaseHolder[] {
  const holders: KnownLeaseHolder[] = [];
  for (const record of records) {
    if (record.answer === null || record.answerSeq === null) continue;
    const body = record.answer.body;
    const notice = classifyRefusal(body);
    if (notice.kind === 'lease_holder') {
      holders.push({ owner: notice.owner, learnedSeq: record.answerSeq });
    }
    const holder = own(own(body, 'stop'), 'holder');
    if (own(holder, 'lease') !== undefined) {
      holders.push({ owner: holder, learnedSeq: record.answerSeq });
    }
  }
  return holders;
}
