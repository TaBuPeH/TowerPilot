import { knownFact, unknownFact, type Fact } from './facts.ts';
import { finite, own, record } from './read.ts';
import {
  harvestView,
  readHarvest,
  type ReadableHarvest,
  type ReadableHarvestView,
} from './readability.ts';
import { readOwnership, type Ownership } from './ownership.ts';
import { scanFresh, type FreshScan } from './scan.ts';

/**
 * D-4 Evidence gate (safety rules S-020, S-021): the ONLY basis for saying
 * stopped, free, idle, input stopped or no collector, on every surface (Pause
 * and input-off feedback, the owner notice, the automation row, the Start
 * handover). A read passes when it was started after the answer or event it is
 * judged against, had no transport or parse error, and D-1, D-2 and D-3 all
 * hold. Absent means unknown, never empty.
 *
 * Both results carry type-only brands made only by this module, so a
 * `ReadAssessment` always holds the D-1, D-2 and D-3 verdicts of a real read
 * and a `GateRead` always passed D-4. The claims of core/evidence.ts accept
 * only these, never a hand-built record of the same fields (R7-01).
 */
declare const assessmentBrand: unique symbol;
declare const gateBrand: unique symbol;

/** One `/api/status` read as the store keeps it. */
export interface StatusRead {
  /** The request sequence point at which the read was STARTED (monotonic within the page). */
  readonly startedSeq: number;
  readonly outcome:
    | { readonly kind: 'answered'; readonly body: unknown }
    | { readonly kind: 'failed'; readonly reason: string };
}

/** D-1, D-2 and D-3 of one read, each with its own reason. */
export interface ReadAssessment {
  /** The request sequence point at which the read was started. */
  readonly startedSeq: number;
  /** False for a transport failure or an answer whose body is not a plain object. */
  readonly answered: boolean;
  /** The raw body as received (undefined for a failed read). */
  readonly body: unknown;
  /** D-1. */
  readonly harvest: Fact<ReadableHarvest>;
  /** D-2. */
  readonly scan: Fact<FreshScan>;
  /** D-3. */
  readonly ownership: Fact<Ownership>;
  readonly [assessmentBrand]: true;
}

/** A read that passed D-4. */
export interface GateRead {
  readonly startedSeq: number;
  readonly harvest: ReadableHarvest;
  readonly view: ReadableHarvestView;
  readonly scan: FreshScan;
  readonly ownership: Ownership;
  /** `harvest.server_time` of the read (H). */
  readonly serverTime: number;
  readonly [gateBrand]: true;
}

function assessed(
  startedSeq: number,
  answered: boolean,
  body: unknown,
  verdicts: {
    readonly harvest: Fact<ReadableHarvest>;
    readonly scan: Fact<FreshScan>;
    readonly ownership: Fact<Ownership>;
  },
): ReadAssessment {
  return Object.freeze({ startedSeq, answered, body, ...verdicts }) as ReadAssessment;
}

/** D-1, D-2 and D-3 of a status read, judged member by member. */
export function assessStatus(read: StatusRead): ReadAssessment {
  if (read.outcome.kind === 'failed') {
    const failed = unknownFact(read.outcome.reason);
    return assessed(read.startedSeq, false, undefined, {
      harvest: failed,
      scan: failed,
      ownership: failed,
    });
  }
  const body = read.outcome.body;
  if (record(body) === undefined) {
    const failed = unknownFact('body_not_object');
    return assessed(read.startedSeq, false, body, {
      harvest: failed,
      scan: failed,
      ownership: failed,
    });
  }
  const harvest = readHarvest(own(body, 'harvest'));
  const serverTime =
    harvest.kind === 'known'
      ? harvestView(harvest.value).serverTime
      : finite(own(own(body, 'harvest'), 'server_time'));
  const scan = scanFresh(own(body, 'process_refresh'), serverTime);
  const ownership =
    harvest.kind === 'known'
      ? readOwnership(own(body, 'processes'), harvest.value)
      : unknownFact(`harvest_unreadable:${harvest.reason}`);
  return assessed(read.startedSeq, true, body, { harvest, scan, ownership });
}

/** True when the read was started after `after` (null: nothing to be later than). */
export function startedAfter(startedSeq: number, after: number | null): boolean {
  return after === null || startedSeq > after;
}

/** D-4 from an assessment. */
export function gateOf(assessment: ReadAssessment, after: number | null): Fact<GateRead> {
  if (!startedAfter(assessment.startedSeq, after)) {
    return unknownFact('read_not_later');
  }
  if (!assessment.answered) {
    return unknownFact('read_failed');
  }
  if (assessment.harvest.kind === 'unknown') {
    return unknownFact(`harvest_unreadable:${assessment.harvest.reason}`);
  }
  if (assessment.scan.kind === 'unknown') {
    return unknownFact(`scan_not_fresh:${assessment.scan.reason}`);
  }
  if (assessment.ownership.kind === 'unknown') {
    return unknownFact(`ownership_incomplete:${assessment.ownership.reason}`);
  }
  const view = harvestView(assessment.harvest.value);
  const gate = Object.freeze({
    startedSeq: assessment.startedSeq,
    harvest: assessment.harvest.value,
    view,
    scan: assessment.scan.value,
    ownership: assessment.ownership.value,
    serverTime: view.serverTime,
  }) as GateRead;
  return knownFact(gate, gate.serverTime);
}

/** D-4: `read` passes the evidence gate when started after `after` (the answer's sequence point). */
export function evidenceGate(read: StatusRead, after: number | null): Fact<GateRead> {
  return gateOf(assessStatus(read), after);
}

/** True when no process row, no `harvest.operation` and no `harvest.owner` exists in a D-4 read. */
export function nothingRecorded(gate: GateRead): boolean {
  return (
    gate.ownership.processes.length === 0 &&
    gate.ownership.operation === null &&
    gate.ownership.owner === null
  );
}
