import { createElement, useEffect, useState } from 'react';
import { createHistoryReader } from '../api/history.ts';
import { HistoryPage } from '../features/history/HistoryPage.tsx';
import { useDashboard } from '../features/dashboard/context.ts';
import { createHistoryStore } from '../store/history.ts';

/** The app owns API wiring. Features receive a store and never import a transport. */
export function ConnectedHistoryPage() {
  const dashboard = useDashboard().store;
  const [history] = useState(() => createHistoryStore(createHistoryReader()));
  useEffect(() => {
    const sync = () => {
      const state = dashboard.getSnapshot();
      const context = state.context;
      history.setTarget(
        !state.contextConfirmed || context === null
          ? null
          : {
              instance: context.instance,
              serial: context.serial,
              account: context.account,
              epoch: state.epoch,
            },
      );
    };
    const unsubscribe = dashboard.subscribe(sync);
    sync();
    return () => {
      unsubscribe();
      history.setTarget(null);
      history.dispose();
    };
  }, [dashboard, history]);
  return createElement(HistoryPage, { history });
}
