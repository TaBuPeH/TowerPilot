import { automationSlot } from '../../core/automation.ts';
import { controlOf, lastKnownOf, withoutShadowed, type LastKnownStop } from '../lastKnownStops.ts';
import type { DashboardState } from '../types.ts';
import { commandVM, type CommandVM } from '../viewModels.ts';
import {
  noticedStopVMs,
  operationIds,
  stopTargetVM,
  type StopOrigin,
  type StopTargetVM,
} from './automationSlot.ts';
import { controlFacts } from './controlFacts.ts';

/**
 * The minimal records every fallback renders from (spec 9.1 X4 to X6; S-091,
 * S-092, INV-1 to INV-3): Pause and Turn input off, always rendered and
 * enabled (a fallback does not evaluate D-8 or the input chip), and every
 * Stop target the page knows: the last known targets of this context, plus
 * those built from the newest read's validated identity alone - so a cold
 * page whose first read cannot be drawn still has its Stop (X6). A working
 * target is enabled with its exact command; an incomplete one (no valid
 * revision, no start time, a malformed linked row) is drawn disabled with its
 * reason and never given a command (F4-01). No Start, Collect or Turn on is
 * ever part of a fallback.
 */
export interface FallbackVM {
  readonly pause: CommandVM<null>;
  readonly turnInputOff: CommandVM<null>;
  readonly stops: readonly StopTargetVM[];
}

export function selectFallback(state: DashboardState): FallbackVM {
  const facts = controlFacts(state);
  const targets = new Map<string, { readonly stop: LastKnownStop; readonly origin: StopOrigin }>();
  // Each Stop sends the highest revision this context knows for its id (S-050), whichever
  // source drew it: a newer read never lowers a retained revision.
  for (const stop of state.lastKnownStops) targets.set(stop.key, { stop, origin: 'last_known' });
  try {
    const view = automationSlot({
      status: facts.status,
      extraClaims: facts.claims,
      lastIdleAgeSec: null,
    });
    for (const row of view.rows) {
      // Keyed by the Stop's own target, as the last known Stops are (one control per target).
      const stop = lastKnownOf(row.stop, row.key);
      if (stop !== null) targets.set(stop.key, { stop, origin: 'read' });
    }
  } catch {
    // The last known Stops stand on their own when the newest read cannot be projected.
  }
  const kept = new Set(withoutShadowed([...targets.values()].map((entry) => entry.stop)));
  const incompleteIds = [...kept].flatMap((stop) =>
    stop.kind === 'disabled' && stop.operationId !== null ? [stop.operationId] : [],
  );
  const known = [...targets.values()]
    .filter((entry) => kept.has(entry.stop))
    .map((entry) =>
      stopTargetVM(state, facts, entry.stop.key, controlOf(entry.stop), entry.origin),
    );
  // INV-3: an operation an owner notice named keeps its Stop here too (dismissed or not).
  return {
    pause: commandVM(true, true, null, false, null),
    turnInputOff: commandVM(true, true, null, false, null),
    stops: [
      ...known,
      ...noticedStopVMs(state, facts, new Set([...operationIds(known), ...incompleteIds])),
    ],
  };
}
