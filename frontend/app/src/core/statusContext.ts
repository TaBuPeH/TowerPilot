import type { ReadAssessment } from './gate.ts';
import type { KnownLeaseHolder } from './lease.ts';

/**
 * What the Control slots are drawn from: the newest status read and, on a
 * warm page whose newest read failed, the last read that answered with its
 * age (G2, A1: "last read {age}"; S-092 keeps that read's Stops), and the
 * lease owners this page knows of (EV-5, EV-6, S-073): the store keeps their
 * records, core/lease.ts decides whether a D-4 read resolves them.
 */
export interface StatusContext {
  /** The newest status read, assessed; null before any read finished. */
  readonly newest: ReadAssessment | null;
  /** The newest read that answered, when a later one failed (a warm page), and its age in seconds. */
  readonly lastGood: { readonly read: ReadAssessment; readonly ageSec: number } | null;
  readonly leaseHolders: readonly KnownLeaseHolder[];
}

/** The answered read the slots draw rows and records from: the newest, or the last good one. */
export function drawnRead(context: StatusContext): ReadAssessment | null {
  if (context.newest !== null && context.newest.answered) {
    return context.newest;
  }
  return context.lastGood?.read ?? null;
}

/** The age of the last good read when the newest read failed after it (warm page), else null. */
export function staleAge(context: StatusContext): number | null {
  if (context.newest !== null && context.newest.answered) {
    return null;
  }
  return context.lastGood?.ageSec ?? null;
}
