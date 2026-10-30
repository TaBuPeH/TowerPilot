import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * Reconnect the emulator (POST /api/wizard/reconnect `{serial}`; spec 7.4, F-004, M8):
 * never resent; the next `device_state` shows the state without attributing it to this
 * request. The body names the address the page showed (review F6M-02): without it the
 * server connects whatever address its configuration names at that moment.
 */
export const RECONNECT_TARGET = 'reconnect';

export function sendReconnect(
  machine: IntentMachine,
  context: PageContext | null,
  serial: string,
): SendResult {
  return sendOnce(machine, RECONNECT_TARGET, context, () =>
    machine.send({
      kind: 'reconnect',
      target: RECONNECT_TARGET,
      endpoint: 'reconnect',
      path: '/api/wizard/reconnect',
      body: { serial },
      key: null,
      meta: { serial },
      context,
      supersedes: [],
    }),
  );
}
