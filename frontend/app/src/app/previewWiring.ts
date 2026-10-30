import { createPreviewApi } from '../api/preview.ts';
import { createPreviewStore } from '../store/preview.ts';
import { selectPreviewTarget } from '../store/selectors/preview.ts';
import type { DashboardStore } from '../store/dashboardStore.ts';

/** A single viewer, independent of Dashboard read failures and write commands. */
export function createPreviewData(dashboard: DashboardStore) {
  const preview = createPreviewStore(createPreviewApi());
  let unsubscribe: (() => void) | null = null;
  const sync = () => {
    preview.setTarget(selectPreviewTarget(dashboard.getSnapshot()));
  };
  return {
    store: preview,
    start() {
      if (unsubscribe !== null) return;
      sync();
      unsubscribe = dashboard.subscribe(sync);
    },
    stop() {
      unsubscribe?.();
      unsubscribe = null;
      preview.present(false);
    },
  };
}
