import type { ConfigRead } from '../../core/config.ts';
import { own } from '../../core/read.ts';
import { assessedRead, configReadOf } from '../reads.ts';
import type { ContextView, DashboardState } from '../types.ts';

/**
 * What a Pause click needs to choose its context (INV-1, OD-13): the latest
 * config read that answered, the harvest record of the newest status read that
 * passed D-1 (so its `harvest.identity` names the emulator) and the context
 * this browser session recorded. api/intents/harvest.ts decides, in core's
 * order, and asks the server in the click when none of them names it.
 */
export interface PauseInputs {
  readonly config: ConfigRead | null;
  /** The raw harvest record of the newest status read that passed D-1, or undefined. */
  readonly readableHarvest: unknown;
  readonly session: ContextView | null;
}

export function selectPauseInputs(state: DashboardState): PauseInputs {
  const slot = state.reads.status;
  let readableHarvest: unknown = undefined;
  for (const read of [slot.latest, slot.lastAnswered]) {
    if (read?.outcome.kind === 'answered' && assessedRead(read).harvest.kind === 'known') {
      readableHarvest = own(read.outcome.body, 'harvest');
      break;
    }
  }
  return {
    config: configReadOf(state.reads.config.lastAnswered ?? state.reads.config.latest),
    readableHarvest,
    session: state.sessionContext,
  };
}
