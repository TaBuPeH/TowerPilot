import { createContext, useCallback, useContext, type ErrorInfo } from 'react';
import type { DashboardCommands } from '../../store/commands.ts';
import type { PreviewStore } from '../../store/preview.ts';
import type { DashboardStore } from '../../store/dashboardStore.ts';
import { useStoreSelector, viewEqual } from '../../store/store.ts';
import type { DashboardState } from '../../store/types.ts';

/**
 * What the Dashboard page needs from app/ (features may not import app/ or
 * api/): the store, the commands a click calls, the one-diagnostic-per-fault
 * reporter (S-090, C-20) and the parts a test asks to fail.
 */
export interface DashboardContextValue {
  readonly preview?: PreviewStore;
  readonly store: DashboardStore;
  readonly commands: DashboardCommands;
  /** One diagnostic per distinct fault (the reporter dedupes, never retries). */
  readonly reportFault: (part: string, error: unknown) => void;
  /**
   * Parts a test asks to fail while drawing (X4 to X6, S-090 to S-094). None
   * on the real page unless a test sets the session key app/ reads.
   */
  readonly faults: FaultSet;
}

/** Which parts are asked to fail; read at every draw, so a test can add one later (a warm fault). */
export interface FaultSet {
  readonly has: (part: string) => boolean;
}

export const DashboardContext = createContext<DashboardContextValue | null>(null);

export function useDashboard(): DashboardContextValue {
  const value = useContext(DashboardContext);
  if (value === null) {
    throw new Error('The Dashboard needs a DashboardContext provider (app/App.tsx)');
  }
  return value;
}

/**
 * A view model of the current state. `select` must be stable (module-level);
 * a new snapshot that yields an equal view model does not re-render (a clock
 * tick that changes nothing visible).
 */
export function useView<V>(select: (state: DashboardState) => V): V {
  return useStoreSelector(useDashboard().store, select, viewEqual);
}

/** The context epoch, the reset key of every boundary (a new emulator or account draws again). */
const selectEpoch = (state: DashboardState): number => state.epoch;
export function useEpochKey(): string {
  return String(useView(selectEpoch));
}

/** The onError of a part's boundary: one diagnostic per distinct fault. */
export function usePartError(part: string): (error: unknown, info: ErrorInfo) => void {
  const { reportFault } = useDashboard();
  return useCallback(
    (error: unknown) => {
      reportFault(part, error);
    },
    [part, reportFault],
  );
}

const sameState = (state: DashboardState): DashboardState => state;

/**
 * Throws while drawing when a test asked this part to fail (the X4 to X6
 * scenarios). It draws on every store update, so a fault a test adds to the
 * set later appears at the next read (a warm page), as a view-model throw would.
 */
export function FaultPoint({ part }: { readonly part: string }) {
  const { faults, store } = useDashboard();
  useStoreSelector(store, sameState);
  if (faults.has(part)) {
    throw new Error(`Injected fault in ${part}`);
  }
  return null;
}
