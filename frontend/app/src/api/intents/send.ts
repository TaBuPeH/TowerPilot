import { sameContext, type PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import type { IntentRecord } from './types.ts';

/** What a write command did on a person's click. */
export type SendResult =
  /** A new request was recorded and sent. */
  | { readonly kind: 'sent'; readonly record: IntentRecord }
  /** The same request (body and key) was sent again (INV-1: a Pause whose outcome is unknown). */
  | { readonly kind: 'resent'; readonly record: IntentRecord }
  /** Nothing was sent: the same target already has a request in flight (one in flight per target). */
  | { readonly kind: 'in_flight'; readonly record: IntentRecord }
  /** Nothing was sent, and why (no POST left the page). */
  | { readonly kind: 'not_sent'; readonly reason: string };

/** The newest record on `target` in `context` that no newer request replaced, or null. */
export function latestOn(
  machine: IntentMachine,
  target: string,
  context: PageContext | null,
): IntentRecord | null {
  const records = machine.snapshot();
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const item = records[index];
    if (
      item !== undefined &&
      item.target === target &&
      item.supersededBySeq === null &&
      sameContext(item.context, context)
    ) {
      return item;
    }
  }
  return null;
}

/** Send `draft` unless the same target already has a request in flight (S-058 for Stop). */
export function sendOnce(
  machine: IntentMachine,
  target: string,
  context: PageContext | null,
  send: () => IntentRecord,
): SendResult {
  const current = latestOn(machine, target, context);
  if (current !== null && current.phase === 'sending') {
    return { kind: 'in_flight', record: current };
  }
  return { kind: 'sent', record: send() };
}
