import { automationSlot, type StopControl } from '../core/automation.ts';
import { confirmIdle, confirmStopped } from '../core/evidence.ts';
import { gateOf, type GateRead, type ReadAssessment } from '../core/gate.ts';
import { answerClaims, boundIntents, contextIntents, knownLeaseHolders } from './intents.ts';
import type { DashboardState } from './types.ts';

/**
 * S-092, S-059 and INV-3: the last known Stop per operation (and kill per
 * untracked process) in this context, and the last known INCOMPLETE target
 * (a disabled Stop with its reason: no valid revision, no start time, a
 * malformed linked row). Each answered status read refreshes them from the
 * Automation slot's own Stop targets, built from the validated id and newest
 * revision (core/automation.ts, INV-3), so a fallback that cannot draw the
 * slot still offers every Stop the slot showed: working ones enabled,
 * incomplete ones disabled with their visible reason (F4-01). An incomplete
 * target is never turned into a command: it carries only what the read named.
 * A target leaves only when a D-4 read proves its owner gone (D-5, or its PID
 * no longer listed); an unreadable, stale or failed read keeps it. A context
 * change clears them (the reducer).
 */
export type DisabledReason = Extract<StopControl, { readonly kind: 'disabled' }>['reason'];

export type LastKnownStop =
  | {
      readonly key: string;
      readonly kind: 'cancel';
      readonly operationId: string;
      readonly expectedRevision: number;
    }
  | { readonly key: string; readonly kind: 'kill'; readonly pid: number; readonly created: number }
  | {
      readonly key: string;
      readonly kind: 'disabled';
      readonly reason: DisabledReason;
      readonly operationId: string | null;
      readonly pid: number | null;
    };

/**
 * The one key of a Stop target (focus, dedupe): `operation:<id>` for a cancel,
 * `process:<pid>:<created>` for a kill, `incomplete:...` for a disabled target
 * (by its operation id, else its PID, else the row it came from). Null for a
 * row the page cannot stop at all (a process owner: "stop it there").
 */
export function stopKeyOf(stop: StopControl, rowKey: string): string | null {
  switch (stop.kind) {
    case 'cancel':
      return `operation:${stop.operationId}`;
    case 'kill':
      return `process:${stop.pid.toString()}:${stop.created.toString()}`;
    case 'disabled':
      return stop.operationId !== null
        ? `incomplete:operation:${stop.operationId}`
        : stop.pid !== null
          ? `incomplete:process:${stop.pid.toString()}`
          : `incomplete:${rowKey}`;
    case 'none':
      return null;
  }
}

/** A Stop target as a retained record (null for a row the page cannot stop). */
export function lastKnownOf(stop: StopControl, rowKey: string): LastKnownStop | null {
  const key = stopKeyOf(stop, rowKey);
  if (key === null || stop.kind === 'none') return null;
  return { key, ...stop };
}

/** A retained record as the Stop control it stands for. */
export function controlOf(stop: LastKnownStop): StopControl {
  switch (stop.kind) {
    case 'cancel':
      return {
        kind: 'cancel',
        operationId: stop.operationId,
        expectedRevision: stop.expectedRevision,
      };
    case 'kill':
      return { kind: 'kill', pid: stop.pid, created: stop.created };
    case 'disabled':
      return {
        kind: 'disabled',
        reason: stop.reason,
        operationId: stop.operationId,
        pid: stop.pid,
      };
  }
}

/**
 * INV-3, one control per target: an incomplete target is dropped when a
 * working Stop of the same operation id, or a kill of the same PID, is known
 * (a working Stop is never shadowed by a disabled one).
 */
export function withoutShadowed<T extends LastKnownStop>(stops: readonly T[]): readonly T[] {
  const operations = new Set(
    stops.flatMap((stop) => (stop.kind === 'cancel' ? [stop.operationId] : [])),
  );
  const pids = new Set(stops.flatMap((stop) => (stop.kind === 'kill' ? [stop.pid] : [])));
  return stops.filter(
    (stop) =>
      stop.kind !== 'disabled' ||
      !(
        (stop.operationId !== null && operations.has(stop.operationId)) ||
        (stop.pid !== null && pids.has(stop.pid))
      ),
  );
}

/** D-5 or an unlisted PID in a D-4 read: the target's owner is proved gone. */
function provedGone(stop: LastKnownStop, gate: GateRead): boolean {
  const listed = (pid: number) => gate.ownership.processes.some((row) => row.pid === pid);
  switch (stop.kind) {
    case 'cancel':
      return confirmStopped({ operationId: stop.operationId, pid: null }, gate).kind === 'known';
    case 'kill':
      return !listed(stop.pid);
    case 'disabled':
      if (stop.pid !== null) return !listed(stop.pid);
      if (stop.operationId !== null) {
        return confirmStopped({ operationId: stop.operationId, pid: null }, gate).kind === 'known';
      }
      // No identity at all: a complete read that no longer shows its row has it gone.
      return true;
  }
}

export function nextLastKnownStops(
  state: DashboardState,
  assessment: ReadAssessment,
): readonly LastKnownStop[] {
  if (!assessment.answered) return state.lastKnownStops;
  const view = automationSlot({
    status: {
      newest: assessment,
      lastGood: null,
      leaseHolders: knownLeaseHolders(contextIntents(state)),
    },
    // Only answers made in this named context are its revisions (R8-02).
    extraClaims: answerClaims(boundIntents(state)),
    lastIdleAgeSec: null,
  });
  const fresh = new Map<string, LastKnownStop>();
  for (const row of view.rows) {
    const stop = lastKnownOf(row.stop, row.key);
    if (stop !== null) fresh.set(stop.key, stop);
  }
  const gate = gateOf(assessment, null);
  const kept: LastKnownStop[] = [];
  for (const previous of state.lastKnownStops) {
    const current = fresh.get(previous.key);
    if (current !== undefined) {
      // The newest revision wins, never a lower one (S-050).
      kept.push(
        current.kind === 'cancel' &&
          previous.kind === 'cancel' &&
          previous.expectedRevision > current.expectedRevision
          ? { ...current, expectedRevision: previous.expectedRevision }
          : current,
      );
      fresh.delete(previous.key);
      continue;
    }
    if (gate.kind === 'unknown' || !provedGone(previous, gate.value)) kept.push(previous);
  }
  return Object.freeze(withoutShadowed([...kept, ...fresh.values()]));
}

/** AS2 (EV-5): this read proves idle - a D-4 read, nothing recorded, no unresolved lease holder. */
export function idleProvenBy(state: DashboardState, assessment: ReadAssessment): boolean {
  const gate = gateOf(assessment, null);
  return (
    gate.kind === 'known' &&
    confirmIdle(gate.value, knownLeaseHolders(contextIntents(state))).kind === 'known'
  );
}
