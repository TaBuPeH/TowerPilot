import { createElement, useEffect, useRef } from 'react';
import { recognitionRunFromHash } from '../core/recognition.ts';
import { RecognitionPage } from '../features/recognition/RecognitionPage.tsx';
import { useDashboard, useView } from '../features/dashboard/context.ts';
import { selectRecognitionPage } from '../store/selectors/recognition.ts';
import { useRouteHash } from './router.ts';
import { ConnectedAdvancedRepairs } from './recognitionAdvanced.ts';

/** The Recognition page with its advanced repairs (R9 to R11, their own store) mounted inside. */
export function ConnectedRecognitionPage() {
  const hash = useRouteHash();
  const vm = useView(selectRecognitionPage);
  const { commands } = useDashboard();
  const entry = useRef<{ hash: string; epoch: number; applied: boolean } | null>(null);
  useEffect(() => {
    if (entry.current?.hash !== hash || entry.current.epoch !== vm.epoch) {
      entry.current = { hash, epoch: vm.epoch, applied: false };
      commands.selectRecognitionRun(null);
    }
    if (entry.current.applied || !vm.contextConfirmed || vm.run.read !== 'ready') return;
    const requested = recognitionRunFromHash(hash);
    const known = vm.run.runs.find((run) => run.name === requested);
    commands.selectRecognitionRun(known?.name ?? null);
    entry.current.applied = true;
  }, [hash, vm.epoch, vm.contextConfirmed, vm.run.read, vm.run.runs, commands]);
  return createElement(RecognitionPage, { Advanced: ConnectedAdvancedRepairs });
}
