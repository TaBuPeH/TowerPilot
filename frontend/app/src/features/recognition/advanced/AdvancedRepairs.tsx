import { useEffect, useState, useSyncExternalStore } from 'react';
import { recognitionAdvanced as t } from '../../../copy/recognitionAdvanced.ts';
import type { RecognitionAdvancedStore } from '../../../store/recognitionAdvanced.ts';
import { RegionBoundary } from '../../../ui/RegionBoundary.tsx';
import { Tile } from '../../../ui/Tile.tsx';
import { Analysis } from './Analysis.tsx';
import { Coverage } from './Coverage.tsx';
import { CropEditor } from './CropEditor.tsx';
import './advanced.css';
export interface AdvancedRepairsProps {
  readonly store: RecognitionAdvancedStore;
  readonly run: string | null;
}
/** R9-R11. Observation and human image repair have no game-input controls. */
export function AdvancedRepairs({ store, run }: AdvancedRepairsProps) {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    store.setRun(run);
  }, [store, run]);
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    const timer = setInterval(() => {
      const s = store.getSnapshot();
      const target = s.key === null ? undefined : s.targets[s.key];
      if (
        target?.analysisBusy === false &&
        (target.selectedOperation !== '' || target.launch?.phase === 'unconfirmed')
      )
        void store.readAnalysis();
    }, 2000);
    return () => {
      clearInterval(timer);
    };
  }, [store]);
  return (
    <div className="tp-advanced">
      <div data-region="recognition-analysis">
        <Tile titleId="recognition-analysis-title" title={t.analysis.short}>
          <RegionBoundary fallback={t.unavailable.short}>
            <Analysis store={store} state={state} now={now} />
          </RegionBoundary>
        </Tile>
      </div>
      <div data-region="recognition-coverage">
        <Tile titleId="recognition-coverage-title" title={t.coverage.short}>
          <RegionBoundary fallback={t.unavailable.short}>
            <Coverage store={store} state={state} now={now} />
          </RegionBoundary>
        </Tile>
      </div>
      <div data-region="recognition-crop">
        <Tile titleId="recognition-crop-title" title={t.crop.short}>
          <RegionBoundary fallback={t.unavailable.short}>
            <CropEditor store={store} state={state} now={now} />
          </RegionBoundary>
        </Tile>
      </div>
    </div>
  );
}
