import { own } from '../../core/read.ts';
import { contextIntents } from '../intents.ts';
import type { DashboardState } from '../types.ts';

/**
 * F-107: toasts echo only APPLIED low-stakes results: a Reconnect the server
 * accepted, and a scheduled stop it cancelled. A failure, a refusal or an
 * unknown outcome never toasts (those are outcome lines, spec 3.4). Codes only.
 */
export type ToastKind = 'reconnect' | 'clear_flag';

export interface ToastVM {
  readonly id: string;
  readonly kind: ToastKind;
  /** The answer's sequence point: a toast is shown once per answer. */
  readonly answerSeq: number;
}

const LOW_STAKES: ReadonlySet<string> = new Set<ToastKind>(['reconnect', 'clear_flag']);

function isToastKind(kind: string): kind is ToastKind {
  return LOW_STAKES.has(kind);
}

export function selectToasts(state: DashboardState): readonly ToastVM[] {
  return contextIntents(state).flatMap((record) => {
    const answer = record.answer;
    if (
      !isToastKind(record.kind) ||
      record.phase !== 'confirmed' ||
      record.answerSeq === null ||
      answer === null ||
      answer.status < 200 ||
      answer.status > 299 ||
      own(answer.body, 'ok') !== true
    ) {
      return [];
    }
    return [{ id: record.id, kind: record.kind, answerSeq: record.answerSeq }];
  });
}
