import { knownFact, unknownFact, type Fact } from './facts.ts';
import { finite, hasOwn, id, own, pid, record, revision } from './read.ts';
import { harvestView, type ReadableHarvest } from './readability.ts';

/**
 * D-3 Complete ownership, D-5 Owner gone and the Stop target of a process row
 * (safety rules S-022, S-024, S-026, S-061). Ownership is judged only from a
 * readable harvest (S-031) plus the `processes` list of the same body.
 */

/** One `processes` row that passed D-3. */
export interface ProcessRow {
  readonly pid: number;
  /** The linked operation id, or null for an untracked runner (no `operation_id` key). */
  readonly operationId: string | null;
  /** The highest valid revision the row carries (`operation_revision`, `operation.revision`), or null. */
  readonly operationRevision: number | null;
  /** `created` when finite (process start time), else null (S-060: a kill needs it). */
  readonly created: number | null;
  readonly raw: unknown;
}

/** `harvest.operation` when D-3 accepts it. */
export interface OperationRecord {
  readonly id: string;
  readonly revision: number | null;
  readonly status: unknown;
  readonly workerAlive: unknown;
  readonly raw: unknown;
}

/** `harvest.owner` when D-3 accepts it. */
export type OwnerRecord =
  | {
      readonly kind: 'operation';
      readonly operationId: string;
      readonly pid: number | null;
      readonly raw: unknown;
    }
  | { readonly kind: 'process'; readonly pid: number; readonly raw: unknown };

/** A complete ownership read: every record D-3 needs, each valid. */
export interface Ownership {
  readonly processes: readonly ProcessRow[];
  readonly operation: OperationRecord | null;
  readonly owner: OwnerRecord | null;
}

/** D-3 for one process row; null when the row is malformed. */
export function readProcessRow(row: unknown): ProcessRow | null {
  if (record(row) === undefined) {
    return null;
  }
  const rowPid = pid(own(row, 'pid'));
  if (rowPid === undefined) {
    return null;
  }
  const created = finite(own(row, 'created')) ?? null;
  if (!hasOwn(row, 'operation_id')) {
    return Object.freeze({
      pid: rowPid,
      operationId: null,
      operationRevision: null,
      created,
      raw: row,
    });
  }
  const operationId = id(own(row, 'operation_id'));
  if (operationId === undefined) {
    return null;
  }
  const operation = own(row, 'operation');
  if (operation !== undefined && operation !== null && own(operation, 'id') !== operationId) {
    return null;
  }
  // D-3: `operation_revision` is absent or a safe integer; null or anything else is malformed.
  const rowRevision = own(row, 'operation_revision');
  if (hasOwn(row, 'operation_revision') && revision(rowRevision) === undefined) {
    return null;
  }
  const revisions = [revision(rowRevision), revision(own(operation, 'revision'))].filter(
    (value) => value !== undefined,
  );
  return Object.freeze({
    pid: rowPid,
    operationId,
    operationRevision: revisions.length === 0 ? null : Math.max(...revisions),
    created,
    raw: row,
  });
}

function readOperation(value: unknown): OperationRecord | null | undefined {
  if (value === null) {
    return null;
  }
  const operationId = id(own(value, 'id'));
  if (record(value) === undefined || operationId === undefined) {
    return undefined;
  }
  return Object.freeze({
    id: operationId,
    revision: revision(own(value, 'revision')) ?? null,
    status: own(value, 'status'),
    workerAlive: own(value, 'worker_alive'),
    raw: value,
  });
}

/** D-3 for one owner record: null for a JSON null, undefined when malformed. */
export function readOwnerRecord(value: unknown): OwnerRecord | null | undefined {
  if (value === null) {
    return null;
  }
  if (record(value) === undefined) {
    return undefined;
  }
  const kind = own(value, 'kind');
  if (kind === 'operation') {
    const operationId = id(own(value, 'operation_id'));
    const ownerPid = own(value, 'pid');
    if (operationId === undefined || (ownerPid !== null && pid(ownerPid) === undefined)) {
      return undefined;
    }
    return Object.freeze({
      kind: 'operation',
      operationId,
      pid: pid(ownerPid) ?? null,
      raw: value,
    });
  }
  if (kind === 'process') {
    const ownerPid = pid(own(value, 'pid'));
    const operationId = own(value, 'operation_id');
    if (ownerPid === undefined || (operationId !== undefined && operationId !== null)) {
      return undefined;
    }
    return Object.freeze({ kind: 'process', pid: ownerPid, raw: value });
  }
  return undefined;
}

/**
 * D-3: `processes` is an array of valid rows; the `harvest.operation` key is
 * present and null or a record with a non-empty string `id`; the
 * `harvest.owner` key is present and null, an operation owner or a process
 * owner. One malformed record makes ownership unknown for the whole read
 * (S-022); an absent list or key is unknown, never empty (S-021).
 */
export function readOwnership(processes: unknown, harvest: ReadableHarvest): Fact<Ownership> {
  const view = harvestView(harvest);
  if (!Array.isArray(processes)) {
    return unknownFact(processes === undefined ? 'processes_missing' : 'processes_not_list');
  }
  const rows: ProcessRow[] = [];
  for (const row of processes) {
    const valid = readProcessRow(row);
    if (valid === null) {
      return unknownFact('process_row_malformed');
    }
    rows.push(valid);
  }
  if (!view.hasOperationKey) {
    return unknownFact('operation_missing');
  }
  const operation = readOperation(view.operation);
  if (operation === undefined) {
    return unknownFact('operation_malformed');
  }
  if (!view.hasOwnerKey) {
    return unknownFact('owner_missing');
  }
  const owner = readOwnerRecord(view.owner);
  if (owner === undefined) {
    return unknownFact('owner_malformed');
  }
  return knownFact(Object.freeze({ processes: rows, operation, owner }), view.serverTime);
}

/** Who an owner is, for D-5: its operation id, its PID, or both; null where unknown. */
export interface OwnerIdentity {
  readonly operationId: string | null;
  readonly pid: number | null;
}

/** The identity of an ownership record, for D-5 and for naming a replacing owner (S-026). */
export function ownerIdentity(owner: OwnerRecord): OwnerIdentity {
  return owner.kind === 'operation'
    ? { operationId: owner.operationId, pid: owner.pid }
    : { operationId: null, pid: owner.pid };
}

/**
 * D-5 Owner gone, judged in a complete ownership read (the caller passes the
 * ownership of a D-4 read): no record names the owner's operation id; no
 * process row holds the owner's PID unless that row carries a different
 * operation id; an unlinked process (or a process owner) rules the owner out
 * only when the owner's PID is known and differs. An owner with neither an id
 * nor a PID is never proven gone.
 */
export function ownerGone(owner: OwnerIdentity, ownership: Ownership): boolean {
  if (owner.operationId === null && owner.pid === null) {
    return false;
  }
  const namesOwner = (operationId: string | null): boolean =>
    owner.operationId !== null && operationId === owner.operationId;
  for (const row of ownership.processes) {
    if (namesOwner(row.operationId)) {
      return false;
    }
    if (row.operationId === null) {
      // An unlinked process: the owner is ruled out only by a known, different PID.
      if (owner.pid === null || row.pid === owner.pid) {
        return false;
      }
    } else if (owner.pid !== null && row.pid === owner.pid && owner.operationId === null) {
      // The owner's PID now carries an operation id the owner never had: not proven different.
      return false;
    }
  }
  if (ownership.operation !== null && namesOwner(ownership.operation.id)) {
    return false;
  }
  const current = ownership.owner;
  if (current !== null) {
    if (current.kind === 'operation' && namesOwner(current.operationId)) {
      return false;
    }
    if (current.kind === 'process' && (owner.pid === null || current.pid === owner.pid)) {
      return false;
    }
    if (
      current.kind === 'operation' &&
      owner.operationId === null &&
      owner.pid !== null &&
      current.pid === owner.pid
    ) {
      // A process owner's PID now named by an operation owner: not proven different.
      return false;
    }
  }
  return true;
}

/** What a Stop control on one process row sends, or why it is disabled (INV-3, S-060, S-061). */
export type ProcessStopTarget =
  | {
      readonly kind: 'operation';
      readonly operationId: string;
      readonly rowRevision: number | null;
    }
  | { readonly kind: 'kill'; readonly pid: number; readonly created: number }
  | {
      readonly kind: 'disabled';
      readonly reason: 'created_missing' | 'operation_unidentified' | 'process_unidentified';
      readonly pid: number | null;
    };

/**
 * The Stop of one raw process row, judged on the row alone so a malformed row
 * elsewhere in the list cannot remove it: a linked row stops through its
 * operation (S-061); a linked row with a malformed operation identity is
 * disabled with "can't identify the operation"; an untracked row is killed by
 * its exact `pid` and `created` (S-060), and disabled when `created` is not a
 * finite number.
 */
export function stopTarget(row: unknown): ProcessStopTarget {
  const rowPid = pid(own(row, 'pid')) ?? null;
  if (hasOwn(row, 'operation_id')) {
    // The operation's identity decides the target; a malformed revision only means the
    // revision comes from another source, or "Waiting for fresh status" (S-050).
    const operationId = id(own(row, 'operation_id'));
    const operation = own(row, 'operation');
    if (
      operationId === undefined ||
      (operation !== undefined && operation !== null && own(operation, 'id') !== operationId)
    ) {
      return { kind: 'disabled', reason: 'operation_unidentified', pid: rowPid };
    }
    const revisions = [
      revision(own(row, 'operation_revision')),
      revision(own(operation, 'revision')),
    ].filter((value) => value !== undefined);
    return {
      kind: 'operation',
      operationId,
      rowRevision: revisions.length === 0 ? null : Math.max(...revisions),
    };
  }
  if (rowPid === null) {
    return { kind: 'disabled', reason: 'process_unidentified', pid: null };
  }
  const created = finite(own(row, 'created'));
  if (created === undefined) {
    return { kind: 'disabled', reason: 'created_missing', pid: rowPid };
  }
  return { kind: 'kill', pid: rowPid, created };
}
