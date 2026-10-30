import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * Stop a tracked operation (POST /api/operations/{id}/cancel; spec 7.4,
 * INV-3, S-050 to S-058). One request in flight per operation id (S-058);
 * "Stop again" is a new request with the newest revision the caller computed
 * (core/stopRevision.ts, S-050), never a resend. A Stop never blocks a status
 * read (S-012) and never waits behind another write (S-004).
 */
export function stopTarget(operationId: string): string {
  return `operation:${operationId}`;
}

export interface StopInput {
  readonly operationId: string;
  readonly expectedRevision: number;
}

export function sendStop(
  machine: IntentMachine,
  input: StopInput,
  context: PageContext | null,
): SendResult {
  if (input.operationId === '' || !Number.isSafeInteger(input.expectedRevision)) {
    return { kind: 'not_sent', reason: 'target_incomplete' };
  }
  const target = stopTarget(input.operationId);
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'stop',
      target,
      endpoint: 'cancel',
      path: `/api/operations/${encodeURIComponent(input.operationId)}/cancel`,
      body: { expected_revision: input.expectedRevision },
      key: null,
      meta: { operationId: input.operationId, expectedRevision: input.expectedRevision },
      context,
      supersedes: [],
    }),
  );
}
