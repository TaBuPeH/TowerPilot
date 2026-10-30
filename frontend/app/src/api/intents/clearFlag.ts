import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * Cancel a scheduled stop (POST /api/control clear_flag; spec 7.4, F-014):
 * no key, never resent. A lost or 5xx answer stays unconfirmed for good; a
 * later `stop_flag` read is shown as the current state, never as this
 * request's result (the flag file may have been removed before the answer
 * failed, 10.3).
 */
export interface ClearFlagInput {
  readonly operationId: string;
  readonly expectedRevision: number;
}

export function sendClearFlag(
  machine: IntentMachine,
  input: ClearFlagInput,
  context: PageContext | null,
): SendResult {
  const target = `stop_flag:${input.operationId}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'clear_flag',
      target,
      endpoint: 'control',
      path: '/api/control',
      body: {
        action: 'clear_flag',
        operation_id: input.operationId,
        expected_revision: input.expectedRevision,
      },
      key: null,
      meta: { operationId: input.operationId, expectedRevision: input.expectedRevision },
      context,
      supersedes: [],
    }),
  );
}
