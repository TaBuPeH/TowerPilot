import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * Run now (POST /api/operations/{id}/actions; spec 7.4, F-056, F-059): the
 * battle identity, revision and action the person saw, a key frozen at the
 * click. A 500 `command_outcome_unknown` or a lost answer leaves it
 * unconfirmed ("may have been recorded"); a person's resend with the same key
 * is answered from the recorded command.
 */
export interface RunNowInput {
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
  readonly action: string;
}

export function sendRunNow(
  machine: IntentMachine,
  input: RunNowInput,
  context: PageContext | null,
  newKey: () => string,
): SendResult {
  const target = `routine:${input.battleId}:${input.action}`;
  return sendOnce(machine, target, context, () => {
    const key = newKey();
    return machine.send({
      kind: 'run_now',
      target,
      endpoint: 'actions',
      path: `/api/operations/${encodeURIComponent(input.operationId)}/actions`,
      body: {
        battle_id: input.battleId,
        generation: input.generation,
        expected_revision: input.expectedRevision,
        idempotency_key: key,
        action: input.action,
      },
      key,
      meta: {
        operationId: input.operationId,
        battleId: input.battleId,
        generation: input.generation,
        expectedRevision: input.expectedRevision,
        action: input.action,
      },
      context,
      supersedes: [],
    });
  });
}
