import {
  launchAnswer,
  launchRecordedIn,
  operationActive,
  operationView,
  type OperationView,
} from '../../core/recognition.ts';
import { id, own } from '../../core/read.ts';
import { contextIntents, contextScopeKey, unresolvedIntent } from '../intents.ts';
import type { DashboardState, ReadRecord } from '../types.ts';

/**
 * The calibrator operation the Recognition page follows (screens brief R1, R2): the one a
 * launch of this page made in this context (its answer, or a later status read naming the
 * launch's key), else the one kept for this context across a reload, else none (the plain
 * status read then shows the latest of the context). Shared by the read plan and the page.
 */
export const RECOGNITION_LAUNCH_KINDS: readonly string[] = ['calibrate_start', 'calibrate_observe'];

/** The draft scope that keeps the followed operation of a context across a reload. */
export function keptOperationScope(state: DashboardState): string | null {
  return state.context === null ? null : `recognition:operation:${contextScopeKey(state.context)}`;
}

function answered200(read: ReadRecord | null): unknown {
  return read !== null && read.outcome.kind === 'answered' && read.outcome.status === 200
    ? read.outcome.body
    : undefined;
}

/** The operation a launch record names: its receipt, else a later read carrying its key. */
export function launchOperation(state: DashboardState, recordId: string): OperationView | null {
  const record = state.intents.find((entry) => entry.id === recordId);
  if (record === undefined) return null;
  if (record.answer !== null) {
    const answer = launchAnswer(record.answer);
    if (answer.kind === 'accepted') return answer.operation;
  }
  if (record.key === null) return null;
  for (const slot of [state.reads.calibrateOperation, state.reads.calibrate]) {
    const found = launchRecordedIn(answered200(slot.lastAnswered), record.key);
    if (found !== null) return found;
  }
  return null;
}

export interface TrackedOperation {
  readonly id: string;
  /** `launch`: a launch of this page in this context; `kept`: kept across a reload. */
  readonly source: 'launch' | 'kept';
}

export function trackedOperation(state: DashboardState): TrackedOperation | null {
  const launches = contextIntents(state).filter((record) =>
    RECOGNITION_LAUNCH_KINDS.includes(record.kind),
  );
  for (let index = launches.length - 1; index >= 0; index -= 1) {
    const record = launches[index];
    if (record === undefined) continue;
    const found = launchOperation(state, record.id);
    if (found !== null) return { id: found.id, source: 'launch' };
    // A newer launch whose outcome is unknown hides an older one: it may be the operation now.
    if (unresolvedIntent(record)) return null;
  }
  const scope = keptOperationScope(state);
  const kept = scope === null ? undefined : state.runDrafts[scope]?.['id'];
  return typeof kept === 'string' && kept !== '' ? { id: kept, source: 'kept' } : null;
}

/** The newest answered read of the followed operation by its id, when it names it. */
export function trackedOperationRead(
  state: DashboardState,
  tracked: TrackedOperation | null,
): OperationView | null {
  if (tracked === null) return null;
  const body = answered200(state.reads.calibrateOperation.lastAnswered);
  const operation = operationView(own(body, 'operation'));
  return operation !== null && operation.id === tracked.id ? operation : null;
}

/** The status-read path of the followed operation, or null. */
export function trackedOperationPath(tracked: TrackedOperation | null): `/api/${string}` | null {
  return tracked === null
    ? null
    : `/api/calibrate/status?operation_id=${encodeURIComponent(tracked.id)}`;
}

/** The followed operation still works (or its state is not read yet). */
export function trackedOperationWorking(state: DashboardState): boolean {
  const tracked = trackedOperation(state);
  if (tracked === null) return false;
  const operation = trackedOperationRead(state, tracked);
  return operation === null || operationActive(operation);
}

/** The plain status read's operation is working. */
export function latestOperationWorking(state: DashboardState): boolean {
  const operation = operationView(
    own(answered200(state.reads.calibrate.lastAnswered), 'operation'),
  );
  return operation !== null && operationActive(operation);
}

export function operationIdOf(value: unknown): string | null {
  return id(own(value, 'operation_id')) ?? null;
}
