import { finite, id, own, record, revision, text } from './read.ts';
import { harvestView, type ReadableHarvest } from './readability.ts';

export interface OverlayRoutine {
  readonly routine: 'ad_gems' | 'floating_gems';
  readonly target: boolean | null;
  readonly skipped: 'live_choice' | 'unsupported' | 'unchanged' | 'unknown' | null;
}

export interface OverlayView {
  readonly kind: 'none' | 'reported' | 'unknown';
  readonly state: string | null;
  readonly pending: boolean;
  readonly applied: boolean;
  readonly cadence: 'current_battle' | 'unknown';
  readonly routines: readonly OverlayRoutine[];
  readonly battleId: string | null;
  readonly commandId: string | null;
  readonly choiceRevision: number | null;
  readonly waitedSec: number | null;
}

/** S-036: reports toggles per routine; it never carries source intervals. */
export function overlayView(harvest: ReadableHarvest): OverlayView {
  const view = harvestView(harvest);
  const overlay = view.overlay;
  const state = text(own(overlay, 'state')) ?? null;
  const routines: OverlayRoutine[] = [];
  const routineNames: readonly OverlayRoutine['routine'][] = ['ad_gems', 'floating_gems'];
  for (const routine of routineNames) {
    const target = own(own(overlay, 'targets'), routine);
    const skipped = own(own(overlay, 'skipped'), routine);
    routines.push({
      routine,
      target: typeof target === 'boolean' ? target : null,
      skipped:
        skipped === undefined
          ? null
          : skipped === 'live_choice' || skipped === 'unsupported' || skipped === 'unchanged'
            ? skipped
            : 'unknown',
    });
  }
  const pending =
    [
      'requested',
      'pending',
      'waiting',
      'waiting_for_battle',
      'not_requested',
      'off_pending',
    ].includes(state ?? '') || view.state === 'off_pending';
  return {
    kind: overlay === null ? 'none' : record(overlay) === undefined ? 'unknown' : 'reported',
    state,
    pending,
    applied: state === 'applied' && !pending,
    cadence: own(overlay, 'cadence_source') === 'current_battle' ? 'current_battle' : 'unknown',
    routines,
    battleId: id(own(overlay, 'battle_id')) ?? null,
    commandId: id(own(overlay, 'command_id')) ?? null,
    choiceRevision: revision(own(overlay, 'choice_revision')) ?? null,
    waitedSec: finite(own(overlay, 'waited_sec')) ?? null,
  };
}
