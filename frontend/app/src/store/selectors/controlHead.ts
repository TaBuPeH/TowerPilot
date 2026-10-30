import type { StateWord } from '../../core/vocabulary.ts';
import { selectAutomationSlot } from './automationSlot.ts';
import { selectGemSlot } from './gemSlot.ts';
import { selectInputSlot } from './inputSlot.ts';
import type { DashboardState } from '../types.ts';

/**
 * The Control card's header end (spec 2.2 sketch, F-112): whole seconds since
 * the last answered status read arrived, from the store's one-second clock;
 * null before the first answer. The age causes no request and no announcement.
 */
export function selectLastReadAge(state: DashboardState): number | null {
  const read = state.reads.status.lastAnswered;
  return read === null ? null : Math.max(0, Math.floor((state.nowMs - read.arrivedAtMs) / 1000));
}

/**
 * F-007, spec 8.2: the three Control words the live region announces when one
 * changes. `settled` is false until a status read has arrived (answered or
 * failed), so the first draw announces nothing.
 */
export interface ControlWordsVM {
  readonly settled: boolean;
  readonly automation: StateWord | null;
  readonly input: StateWord | null;
  readonly gem: StateWord | null;
}

export function selectControlWords(state: DashboardState): ControlWordsVM {
  return {
    settled: state.reads.status.latest !== null,
    automation: selectAutomationSlot(state).word.word,
    input: selectInputSlot(state).seen.word,
    gem: selectGemSlot(state).seen.word,
  };
}
