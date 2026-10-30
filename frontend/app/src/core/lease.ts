import type { GateRead } from './gate.ts';
import { list, own, ownPath, pid, record } from './read.ts';

/**
 * Lease holders this page knows of (safety rule S-073, EV-5 and EV-6 of spec
 * 3.6.1): a holder named by an owner notice (`device_owned_lease`, its
 * `owner.lease.holders`) or by a Stop answer (`stop.holder.lease.holders`,
 * S-053). Idle and free are never claimed while such a holder is unresolved.
 * The store keeps the records; core decides, here, whether a D-4 read resolves
 * them, so a claim never rests on a caller's own "resolved" verdict (R7-01).
 */

/** One holder of a device lease, as an owner record lists it. */
export interface LeaseHolder {
  readonly pid: number;
  readonly role: unknown;
}

/** A lease owner this page learned of: the raw owner record and when it arrived. */
export interface KnownLeaseHolder {
  /** The raw owner record (`body.owner` of the notice, `stop.holder` of a Stop answer). */
  readonly owner: unknown;
  /** The request sequence point at which the answer naming it arrived. */
  readonly learnedSeq: number;
}

/** S-073 and S-053: the listed holders; a missing or invalid list names nobody. */
export function leaseHolders(owner: unknown): readonly LeaseHolder[] {
  const raw = list(ownPath(owner, 'lease', 'holders'));
  if (raw === undefined) return [];
  const holders: LeaseHolder[] = [];
  for (const row of raw) {
    const holderPid = pid(own(row, 'pid'));
    if (holderPid === undefined) return [];
    holders.push({ pid: holderPid, role: own(row, 'role') });
  }
  return holders;
}

/**
 * S-073: a known lease owner is resolved only by a D-4 read started after it
 * was learned, in which it listed holders (an unidentified holder is never
 * proven gone), no record carries a lease, and no listed holder's PID appears
 * in a process row, the owner, the operation or the operation's worker.
 */
export function leaseResolvedBy(holder: KnownLeaseHolder, gate: GateRead): boolean {
  if (gate.startedSeq <= holder.learnedSeq) return false;
  const holders = leaseHolders(holder.owner);
  if (holders.length === 0) return false;
  const view = gate.view;
  if (
    record(own(view.owner, 'lease')) !== undefined ||
    record(own(view.operation, 'lease')) !== undefined
  ) {
    return false;
  }
  const { ownership } = gate;
  return holders.every(
    (listed) =>
      ownership.processes.every((row) => row.pid !== listed.pid) &&
      (ownership.owner === null || ownership.owner.pid !== listed.pid) &&
      pid(own(ownership.operation?.raw, 'pid')) !== listed.pid &&
      pid(ownPath(ownership.operation?.raw, 'worker', 'pid')) !== listed.pid,
  );
}

/** The known lease owners that `gate` does not resolve (all of them when there is no D-4 read). */
export function unresolvedLeaseHolders(
  known: readonly KnownLeaseHolder[],
  gate: GateRead | null,
): readonly KnownLeaseHolder[] {
  return gate === null ? known : known.filter((holder) => !leaseResolvedBy(holder, gate));
}
