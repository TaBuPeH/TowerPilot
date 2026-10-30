import { createElement, useEffect, useState } from 'react';
import { createRecognitionAdvancedApi } from '../api/recognitionAdvanced.ts';
import {
  createRecognitionAdvancedStore,
  type RecognitionAdvancedStore,
} from '../store/recognitionAdvanced.ts';
import type { DashboardStore } from '../store/dashboardStore.ts';
import { own } from '../core/read.ts';
import { AdvancedRepairs } from '../features/recognition/advanced/AdvancedRepairs.tsx';
import { useDashboard } from '../features/dashboard/context.ts';
const stores = new WeakMap<DashboardStore, RecognitionAdvancedStore>();
export function ConnectedAdvancedRepairs({ run }: { readonly run: string | null }) {
  const dashboard = useDashboard().store;
  const [store] = useState(() => {
    let s = stores.get(dashboard);
    if (s === undefined) {
      s = createRecognitionAdvancedStore(createRecognitionAdvancedApi());
      stores.set(dashboard, s);
    }
    return s;
  });
  useEffect(() => {
    const sync = () => {
      const s = dashboard.getSnapshot();
      const latest = s.reads.status.latest;
      store.setTarget(
        s.contextConfirmed ? s.context : null,
        s.epoch,
        latest?.outcome.kind === 'answered' && own(latest.outcome.body, 'connected') === true,
      );
    };
    const unsub = dashboard.subscribe(sync);
    sync();
    return unsub;
  }, [dashboard, store]);
  return createElement(AdvancedRepairs, { store, run });
}
