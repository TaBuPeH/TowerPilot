import {
  editingTarget,
  editTargetKey,
  pickableProfiles,
  type EditingTarget,
} from '../../core/editingTarget.ts';
import { profileFilesView } from '../../core/profiles.ts';
import { own } from '../../core/read.ts';
import { fromServer } from '../../copy/server.ts';
import type { DashboardState, ReadRecord } from '../types.ts';

/** Only the latest successful read grants listing evidence, never a retained older answer. */
export function editingFiles(state: DashboardState) {
  const read = state.reads.profileFiles.latest;
  return read?.outcome.kind === 'answered' && read.outcome.status === 200
    ? profileFilesView(read.outcome.body)
    : null;
}

/**
 * The profiles a pick may name now. A pick is kept under the page context, so none is
 * offered before a config read of this page confirms that context: the read that does
 * starts a new epoch, which would drop a pick made earlier. The reducer accepts exactly these.
 */
export function pickableNow(state: DashboardState) {
  return state.context === null || !state.contextConfirmed
    ? []
    : pickableProfiles(editingFiles(state));
}

export function selectEditingTarget(state: DashboardState): EditingTarget {
  const pick = state.context === null ? undefined : state.editTargets[editTargetKey(state.context)];
  return {
    ...editingTarget(state.context, pick, editingFiles(state)),
    generation: state.editTargetGeneration,
  };
}

export function editingTargetVM(target: EditingTarget) {
  return {
    ...target,
    name: fromServer('name', target.name),
    active: fromServer('name', target.active),
  };
}

export function selectEditingChoices(state: DashboardState) {
  return pickableNow(state).map((file) => ({
    key: file.name,
    name: fromServer('name', file.name),
  }));
}

export function editingFilesFailed(state: DashboardState) {
  const read = state.reads.profileFiles.latest;
  return read !== null && (read.outcome.kind === 'failed' || read.outcome.status !== 200);
}

/** The selected profile owns one source slot for both reads and presentation. */
export function editingSourceResource(target: EditingTarget) {
  return target.inactive ? 'targetSource' : 'profileSource';
}

/** A failed selection read must not expose an older successful source as current evidence. */
export function editingSourceError(state: DashboardState) {
  const target = selectEditingTarget(state);
  const read = state.reads[editingSourceResource(target)].latest;
  if (
    target.inactive &&
    (read?.targetSelection?.name !== target.name ||
      read.targetSelection.generation !== target.generation)
  )
    return null;
  const body = read?.outcome.body;
  return fromServer('message', own(body, 'error'));
}

/** Editors use evidence for their selection; journal settlement independently uses the raw read. */
export function editorSource(
  state: DashboardState,
  basis: 'latest' | 'lastAnswered' = 'latest',
): ReadRecord | null {
  const target = selectEditingTarget(state);
  if (target.blocked !== null || target.name === null) return null;
  const read = state.reads[editingSourceResource(target)][basis];
  if (
    target.inactive &&
    (read?.targetSelection?.name !== target.name ||
      read.targetSelection.generation !== target.generation)
  )
    return null;
  return read?.outcome.kind === 'answered' &&
    read.outcome.status === 200 &&
    own(read.outcome.body, 'name') === target.name
    ? read
    : null;
}
