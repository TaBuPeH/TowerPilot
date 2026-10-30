import type { KnownOn, UnsettledOn } from '../../core/harvestChoice.ts';
import type { GameInputFacts } from '../../core/input.ts';
import type { KnownLeaseHolder } from '../../core/lease.ts';
import type { StatusContext } from '../../core/statusContext.ts';
import { statusRevisionClaims, stopRevision, type RevisionClaim } from '../../core/stopRevision.ts';
import {
  answerClaims,
  boundIntents,
  contextIntents,
  enableRequests,
  knownLeaseHolders,
  knownOnOf,
  turnOffAnswer,
  unsettledOns,
} from '../intents.ts';
import { answeredBody, configReadOf, currentStatusAssessment, statusContextOf } from '../reads.ts';
import type { DashboardState, IntentView } from '../types.ts';

/**
 * Everything the Control slots feed core with, derived once per state: the
 * write records whose lines this context shows, the facts they carry (Known On,
 * unsettled Ons, enables, the newest turn-off answer, lease holders) and the
 * status context. Each input is core-issued (assessed reads) or a plain record
 * core judges itself; no claim is passed in ready-made. Records bound to no
 * context only make the page more careful (src/store/intents.ts).
 */
export interface ControlFacts {
  /** Records whose lines this context shows (its own and context-less ones). */
  readonly records: readonly IntentView[];
  /** Records made in this named context: the only ones whose answers are its evidence. */
  readonly bound: readonly IntentView[];
  readonly status: StatusContext;
  readonly leaseHolders: readonly KnownLeaseHolder[];
  /**
   * S-050: every revision this context knows per operation id - this context's answers,
   * conflicts, recoveries and Stop buttons, the newest answered status body, and the last
   * known Stops - so every Stop surface sends the same, highest one.
   */
  readonly claims: readonly RevisionClaim[];
  readonly knownOn: KnownOn | null;
  readonly unsettled: readonly UnsettledOn[];
  readonly input: GameInputFacts;
}

const memo = new WeakMap<DashboardState, ControlFacts>();

export function controlFacts(state: DashboardState): ControlFacts {
  const known = memo.get(state);
  if (known !== undefined) return known;
  const records = contextIntents(state);
  const bound = boundIntents(state);
  const leaseHolders = knownLeaseHolders(records);
  const status = statusContextOf(state, leaseHolders);
  const facts: ControlFacts = {
    records,
    bound,
    status,
    leaseHolders,
    claims: [
      ...answerClaims(bound),
      ...statusRevisionClaims(answeredBody(state, 'status')),
      ...state.lastKnownStops.flatMap((stop) =>
        stop.kind === 'cancel'
          ? [{ operationId: stop.operationId, revision: stop.expectedRevision }]
          : [],
      ),
    ],
    knownOn: knownOnOf(state, bound),
    unsettled: unsettledOns(records),
    input: {
      config: configReadOf(state.reads.config.latest),
      status: currentStatusAssessment(state),
      enables: enableRequests(records),
      turnOff: turnOffAnswer(bound),
      leaseHolders,
      // S-059, S-092: a holder this page still keeps a Stop for may still send input.
      retainedHolder: state.lastKnownStops.length > 0,
    },
  };
  memo.set(state, facts);
  return facts;
}

/** S-050: the highest revision this context knows for exactly `operationId` (null: none). */
export function highestStopRevision(facts: ControlFacts, operationId: string): number | null {
  return stopRevision(operationId, facts.claims);
}

/** The newest current-context record on `target` that no newer request replaced. */
export function latestOnTarget(facts: ControlFacts, target: string): IntentView | null {
  for (let index = facts.records.length - 1; index >= 0; index -= 1) {
    const record = facts.records[index];
    if (record !== undefined && record.target === target && record.supersededBySeq === null) {
      return record;
    }
  }
  return null;
}
