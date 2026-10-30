import { createElement, useEffect, useState } from 'react';
import { createEquipmentApi } from '../api/equipment.ts';
import { createEquipmentStore, type EquipmentStore } from '../store/equipment.ts';
import type { DashboardStore } from '../store/dashboardStore.ts';
import { EquipmentPage } from '../features/equipment/EquipmentPage.tsx';
import { useDashboard } from '../features/dashboard/context.ts';

const editors = new WeakMap<DashboardStore, EquipmentStore>();
export function ConnectedEquipmentPage() {
  const dashboard = useDashboard().store;
  const [equipment] = useState(() => {
    let editor = editors.get(dashboard);
    if (editor === undefined) {
      editor = createEquipmentStore(createEquipmentApi());
      editors.set(dashboard, editor);
    }
    return editor;
  });
  useEffect(() => {
    const sync = () => {
      const s = dashboard.getSnapshot();
      equipment.setTarget(s.contextConfirmed ? s.context : null, s.epoch);
    };
    const unsubscribe = dashboard.subscribe(sync);
    sync();
    return unsubscribe;
  }, [dashboard, equipment]);
  return createElement(EquipmentPage, { equipment });
}
