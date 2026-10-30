import { id, list, own, revision } from './read.ts';

/**
 * S-050: Stop and Stop again send the highest valid revision known for that
 * exact operation id across all sources - process rows (`operation_revision`,
 * `operation.revision`), `harvest.operation`, `harvest.owner` (its
 * `recovery.expected_revision`), an owner notice's `recovery`, earlier Stop
 * answers and coded conflicts (`operation.revision`), and the button's own.
 * Highest revision wins, never source priority, never another id. With no
 * valid revision nothing is sent (INV-3: "Waiting for fresh status").
 */

/** One place a revision for an operation id was learned. Both fields are raw until judged. */
export interface RevisionClaim {
  readonly operationId: unknown;
  readonly revision: unknown;
}

/** The highest valid revision (safe integer >= 0) claimed for exactly `operationId`, or null. */
export function stopRevision(operationId: string, claims: readonly RevisionClaim[]): number | null {
  let best: number | null = null;
  for (const claim of claims) {
    if (id(claim.operationId) !== operationId) {
      continue;
    }
    const value = revision(claim.revision);
    if (value !== undefined && (best === null || value > best)) {
      best = value;
    }
  }
  return best;
}

/** Revision claims of one `/api/status` body: process rows, `harvest.operation`, `harvest.owner` recovery. */
export function statusRevisionClaims(body: unknown): readonly RevisionClaim[] {
  const claims: RevisionClaim[] = [];
  for (const row of list(own(body, 'processes')) ?? []) {
    const rowId = own(row, 'operation_id');
    claims.push({ operationId: rowId, revision: own(row, 'operation_revision') });
    const operation = own(row, 'operation');
    claims.push({ operationId: own(operation, 'id'), revision: own(operation, 'revision') });
  }
  const harvest = own(body, 'harvest');
  claims.push(...harvestRevisionClaims(harvest));
  return claims;
}

/** Revision claims of a harvest record: `operation` and the owner's `recovery`. */
export function harvestRevisionClaims(harvest: unknown): readonly RevisionClaim[] {
  const operation = own(harvest, 'operation');
  const owner = own(harvest, 'owner');
  return [
    { operationId: own(operation, 'id'), revision: own(operation, 'revision') },
    ...recoveryClaims(owner),
  ];
}

/** A `recovery` of an owner record or a refusal body: `{action, operation_id, expected_revision}`. */
export function recoveryClaims(holder: unknown): readonly RevisionClaim[] {
  const recovery = own(holder, 'recovery');
  return [
    { operationId: own(recovery, 'operation_id'), revision: own(recovery, 'expected_revision') },
  ];
}

/** Revision claims of an answer body (a Stop answer, a coded conflict, a refusal). */
export function answerRevisionClaims(body: unknown): readonly RevisionClaim[] {
  const operation = own(body, 'operation');
  return [
    { operationId: own(operation, 'id'), revision: own(operation, 'revision') },
    ...recoveryClaims(body),
    ...recoveryClaims(own(body, 'owner')),
  ];
}
