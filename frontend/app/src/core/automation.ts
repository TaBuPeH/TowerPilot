import { operationPublication, ownerPublication } from './collection.ts';
import { confirmFree, confirmIdle } from './evidence.ts';
import { gateOf, type ReadAssessment } from './gate.ts';
import { gemSeen } from './harvestView.ts';
import { stopTarget } from './ownership.ts';
import { finite, id, list, own, pid, record } from './read.ts';
import { unresolvedLeaseHolders } from './lease.ts';
import { harvestView } from './readability.ts';
import { drawnRead, staleAge, type StatusContext } from './statusContext.ts';
import { stopRevision, statusRevisionClaims, type RevisionClaim } from './stopRevision.ts';
import { decide, observed, type ChipDecision } from './vocabulary.ts';

/**
 * The Automation slot (spec 3.6.4, INV-3, safety rules S-027, S-028, S-059,
 * S-061, S-092): one row per operation id - a process row, `harvest.operation`
 * or an operation `harvest.owner`, a record already shown as a process row is
 * not repeated - and one per process owner, each with its own chip and its own
 * Stop; a summary when there is no row. Running needs EV-1 (a listed row on a
 * fresh scan) or EV-2 (a fresh publication); Idle needs EV-5.
 */

export type AutomationRow =
  | 'A1'
  | 'A2'
  | 'A3'
  | 'A4'
  | 'A5'
  | 'A6'
  | 'A7'
  | 'A8'
  | 'A9'
  | 'A10'
  | 'A11'
  | 'A12'
  | 'A13'
  | 'AS1'
  | 'AS2'
  | 'AS3'
  | 'AS4'
  | 'AS5';

export type AutomationLine =
  | 'last_read'
  | 'cant_tell_whats_running'
  | 'started'
  | 'start_time_not_recorded'
  | 'last_listed'
  | 'operation_battle'
  | 'stop_not_confirmed'
  | 'starting'
  | 'waiting_to_track_battle'
  | 'recorded_process_not_seen'
  | 'run_battle'
  | 'another_process_holds'
  | 'no_automation_running'
  | 'no_automation_now_harvesting_on'
  | 'last_known_idle'
  | 'holder_unresolved';

export type AutomationDecision = ChipDecision<AutomationRow, AutomationLine>;

/** What one click of a row's Stop sends, or why it is disabled (INV-3, S-050, S-060, S-061). */
export type StopControl =
  | { readonly kind: 'cancel'; readonly operationId: string; readonly expectedRevision: number }
  | { readonly kind: 'kill'; readonly pid: number; readonly created: number }
  | {
      readonly kind: 'disabled';
      readonly reason:
        | 'waiting_for_fresh_status'
        | 'created_missing'
        | 'operation_unidentified'
        | 'process_unidentified';
      readonly operationId: string | null;
      readonly pid: number | null;
    }
  /** A process owner the page cannot stop: "Stop it there, or turn input off" (7.5). */
  | { readonly kind: 'none' };

export interface OperationRowView {
  /** Stable key for focus and list identity: `process:<pid>`, `operation:<id>`, `owner:<pid>`. */
  readonly key: string;
  readonly source: 'process_row' | 'operation' | 'owner_operation' | 'owner_process';
  readonly operationId: string | null;
  readonly pid: number | null;
  readonly seen: AutomationDecision;
  readonly stop: StopControl;
  readonly raw: unknown;
}

export interface AutomationSlotView {
  readonly rows: readonly OperationRowView[];
  /** The summary, present only when there is no row (AS1 to AS5). */
  readonly summary: AutomationDecision | null;
}

export interface AutomationFacts {
  readonly status: StatusContext;
  /** Revisions learned outside the read: owner notices, Stop answers, conflicts, buttons (S-050). */
  readonly extraClaims: readonly RevisionClaim[];
  /** The age of the last fresh read that gave AS2 (Idle), for AS4's "last known: idle", or null. */
  readonly lastIdleAgeSec: number | null;
}

function cancelOrWait(operationId: string, claims: readonly RevisionClaim[]): StopControl {
  const expected = stopRevision(operationId, claims);
  return expected === null
    ? { kind: 'disabled', reason: 'waiting_for_fresh_status', operationId, pid: null }
    : { kind: 'cancel', operationId, expectedRevision: expected };
}

function word(
  read: ReadAssessment,
  stale: number | null,
): { readonly kind: 'stale'; readonly age: number } | { readonly kind: 'incomplete' } | null {
  if (stale !== null) {
    return { kind: 'stale', age: stale };
  }
  if (read.harvest.kind === 'unknown' || read.ownership.kind === 'unknown') {
    return { kind: 'incomplete' };
  }
  return null;
}

/** The Automation slot (spec 3.6.4) with every row's Stop (INV-3). */
export function automationSlot(facts: AutomationFacts): AutomationSlotView {
  const read = drawnRead(facts.status);
  if (read === null) {
    return {
      rows: [],
      summary: decide('AS1', 'unknown', 'cant_tell_whats_running', null, { reason: 'not_read' }),
    };
  }
  const stale = staleAge(facts.status);
  const blanket = word(read, stale);
  const claims = [...statusRevisionClaims(read.body), ...facts.extraClaims];
  const rows: OperationRowView[] = [];
  const shown = new Set<string>();
  const scanFresh = read.scan.kind === 'known';
  const serverTime = finite(own(own(read.body, 'harvest'), 'server_time'));
  const refreshAge = finite(own(own(read.body, 'process_refresh'), 'age_sec'));

  const blanketDecision = (): AutomationDecision | null => {
    if (blanket === null) {
      return null;
    }
    return blanket.kind === 'stale'
      ? decide('A1', 'unknown', 'last_read', null, { ageSec: blanket.age })
      : decide('A2', 'unknown', 'cant_tell_whats_running', null);
  };

  for (const row of list(own(read.body, 'processes')) ?? []) {
    const target = stopTarget(row);
    const rowPid = pid(own(row, 'pid')) ?? null;
    const created = finite(own(row, 'created'));
    let stop: StopControl;
    let operationId: string | null = null;
    if (target.kind === 'operation') {
      operationId = target.operationId;
      shown.add(target.operationId);
      stop = cancelOrWait(target.operationId, claims);
    } else if (target.kind === 'kill') {
      stop = target;
    } else {
      stop = { kind: 'disabled', reason: target.reason, operationId: null, pid: target.pid };
    }
    const seen =
      blanketDecision() ??
      (scanFresh
        ? created === undefined
          ? decide('A3', 'running', 'start_time_not_recorded', 'EV-1')
          : decide('A3', 'running', 'started', 'EV-1', { created })
        : decide('A4', 'unknown', 'last_listed', null, { ageSec: refreshAge ?? null }));
    rows.push({
      key: `process:${rowPid === null ? rows.length.toString() : rowPid.toString()}`,
      source: 'process_row',
      operationId,
      pid: rowPid,
      seen,
      stop,
      raw: row,
    });
  }

  const harvest = own(read.body, 'harvest');
  const readable = read.harvest.kind === 'known' ? read.harvest.value : null;
  const state = readable === null ? null : harvestView(readable).state;
  const operation = own(harvest, 'operation');
  const operationId = id(own(operation, 'id'));
  if (record(operation) !== undefined && operationId !== undefined && !shown.has(operationId)) {
    shown.add(operationId);
    const status = own(operation, 'status');
    const seen =
      blanketDecision() ??
      (readable !== null && operationPublication(readable) !== null
        ? decide('A5', 'running', 'operation_battle', 'EV-2')
        : status === 'cancelling'
          ? decide('A6', 'stopping', 'stop_not_confirmed', null)
          : status === 'accepted'
            ? decide('A7', 'waiting', 'starting', null)
            : status === 'running' &&
                own(operation, 'worker_alive') === true &&
                state === 'waiting_for_battle'
              ? decide('A8', 'waiting', 'waiting_to_track_battle', null)
              : decide('A9', 'unknown', 'recorded_process_not_seen', null));
    rows.push({
      key: `operation:${operationId}`,
      source: 'operation',
      operationId,
      pid: null,
      seen,
      stop: cancelOrWait(operationId, claims),
      raw: operation,
    });
  }

  const owner = own(harvest, 'owner');
  if (own(owner, 'kind') === 'operation') {
    const ownerId = id(own(owner, 'operation_id'));
    if (ownerId !== undefined && !shown.has(ownerId)) {
      shown.add(ownerId);
      const status = own(owner, 'status');
      const seen =
        blanketDecision() ??
        (readable !== null && ownerPublication(readable) !== null
          ? decide('A10', 'running', 'run_battle', 'EV-2')
          : status === 'cancelling'
            ? decide('A11', 'stopping', 'stop_not_confirmed', null)
            : status === 'accepted'
              ? decide('A11', 'waiting', 'starting', null)
              : decide('A12', 'unknown', 'recorded_process_not_seen', null));
      rows.push({
        key: `operation:${ownerId}`,
        source: 'owner_operation',
        operationId: ownerId,
        pid: pid(own(owner, 'pid')) ?? null,
        seen,
        stop: cancelOrWait(ownerId, claims),
        raw: owner,
      });
    }
  } else if (own(owner, 'kind') === 'process') {
    const ownerPid = pid(own(owner, 'pid')) ?? null;
    rows.push({
      key: `owner:${ownerPid === null ? 'unknown' : ownerPid.toString()}`,
      source: 'owner_process',
      operationId: null,
      pid: ownerPid,
      seen:
        blanketDecision() ??
        decide('A13', 'unknown', 'another_process_holds', null, {
          foreign: own(owner, 'foreign') === true,
          pid: ownerPid,
        }),
      stop: { kind: 'none' },
      raw: owner,
    });
  }

  if (rows.length > 0) {
    return { rows, summary: null };
  }
  return { rows, summary: summary(facts, read, stale, serverTime) };
}

function summary(
  facts: AutomationFacts,
  read: ReadAssessment,
  stale: number | null,
  serverTime: number | undefined,
): AutomationDecision {
  if (stale !== null) {
    return decide('AS1', 'unknown', 'last_read', null, { ageSec: stale });
  }
  if (read.harvest.kind === 'unknown' || read.ownership.kind === 'unknown') {
    return decide('AS1', 'unknown', 'cant_tell_whats_running', null);
  }
  const gate = gateOf(read, null);
  if (gate.kind === 'known') {
    const idle = confirmIdle(gate.value, facts.status.leaseHolders);
    if (idle.kind === 'known') {
      return observed('AS2', 'idle', 'no_automation_running', idle.value);
    }
    const free = confirmFree(gate.value, facts.status.leaseHolders);
    if (gate.value.view.choice === 'on' && free.kind === 'known') {
      const gem = gemSeen(facts.status);
      return observed('AS3', gem.word, 'no_automation_now_harvesting_on', free.value, {
        gemRow: gem.row,
      });
    }
  }
  if (read.scan.kind === 'unknown') {
    return facts.lastIdleAgeSec !== null
      ? decide('AS4', 'unknown', 'last_known_idle', null, { ageSec: facts.lastIdleAgeSec })
      : decide('AS4', 'unknown', 'cant_tell_whats_running', null);
  }
  return unresolvedLeaseHolders(
    facts.status.leaseHolders,
    gate.kind === 'known' ? gate.value : null,
  ).length > 0
    ? decide('AS5', 'unknown', 'holder_unresolved', null)
    : decide('AS5', 'unknown', 'cant_tell_whats_running', null, {
        serverTime: serverTime ?? null,
      });
}

/** The slot's one word for the 9.1 matrix: the word of its one row, or its summary. */
export function automationWord(view: AutomationSlotView): AutomationDecision {
  const first = view.rows[0];
  if (first !== undefined) {
    return first.seen;
  }
  if (view.summary === null) {
    throw new Error('An automation slot has a row or a summary');
  }
  return view.summary;
}

/**
 * S-059: a retained holder - an operation `cancelling`, `accepted` or of
 * unknown state with no process row - always keeps a working Stop (with the
 * newest revision) for as long as it is retained.
 */
export function holders(view: AutomationSlotView): readonly OperationRowView[] {
  return view.rows.filter(
    (row) =>
      (row.source === 'operation' || row.source === 'owner_operation') &&
      row.seen.word !== 'running',
  );
}
