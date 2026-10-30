import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * Stop an untracked runner (POST /api/control kill; spec 7.4, S-060): the
 * exact `pid` and `created` of the row the person saw; never resent, one
 * request in flight per process. A row with `created: null` has no kill at
 * all (core/ownership.ts gives it a disabled Stop), so a caller cannot pass
 * one here.
 */
export interface KillInput {
  readonly pid: number;
  readonly created: number;
}

export function sendKill(
  machine: IntentMachine,
  input: KillInput,
  context: PageContext | null,
): SendResult {
  if (!Number.isSafeInteger(input.pid) || input.pid <= 0 || !Number.isFinite(input.created)) {
    return { kind: 'not_sent', reason: 'target_incomplete' };
  }
  const target = `process:${input.pid.toString()}:${input.created.toString()}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'kill',
      target,
      endpoint: 'control',
      path: '/api/control',
      body: { action: 'kill', pid: input.pid, created: input.created },
      key: null,
      meta: { pid: input.pid, created: input.created },
      context,
      supersedes: [],
    }),
  );
}
