import { createElement, useEffect, useState } from 'react';
import { createScheduleApi } from '../api/schedule.ts';
import { SchedulePage } from '../features/schedule/SchedulePage.tsx';
import { useDashboard } from '../features/dashboard/context.ts';
import { createScheduleStore, currentSchedule, type ScheduleStore } from '../store/schedule.ts';
import { sameContext } from '../api/context.ts';
import type { DashboardStore } from '../store/dashboardStore.ts';
import { selectEditingTarget } from '../store/selectors/editingTarget.ts';

const editors = new WeakMap<DashboardStore, ScheduleStore>();
/** The app wires transport to the store; the feature only renders the store. */
export function ConnectedSchedulePage() {
  const { store: dashboard, commands } = useDashboard();
  const [schedule] = useState(() => {
    let editor = editors.get(dashboard);
    if (editor === undefined) {
      editor = createScheduleStore(createScheduleApi());
      editors.set(dashboard, editor);
      const scheduleStore = editor;
      let seen = '';
      // RL8: the cached editor outlives the route, including a reply received after
      // leaving Schedule. Observe its public journal state for this dashboard lifetime.
      scheduleStore.subscribe(() => {
        const state = scheduleStore.getSnapshot();
        const intent = currentSchedule(state)?.intent;
        if (
          state.target.inactive ||
          intent === null ||
          intent === undefined ||
          intent.phase === 'sending' ||
          !sameContext(state.context, dashboard.getSnapshot().context)
        )
          return;
        const marker = JSON.stringify([
          intent.id,
          intent.phase,
          currentSchedule(state)?.resolvedIntent,
        ]);
        if (seen === marker) return;
        seen = marker;
        commands.checkRuns();
      });
    }
    return editor;
  });
  useEffect(() => {
    const sync = () => {
      const state = dashboard.getSnapshot();
      schedule.setTarget(
        state.contextConfirmed ? state.context : null,
        state.epoch,
        selectEditingTarget(state),
      );
    };
    const unsubscribe = dashboard.subscribe(sync);
    sync();
    return unsubscribe;
  }, [dashboard, schedule]);
  return createElement(SchedulePage, { schedule });
}
