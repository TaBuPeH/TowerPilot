import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import type { SendResult } from './send.ts';

/**
 * Turn input off and Allow game input (POST /api/automation-access; spec 7.4,
 * INV-2, S-005, S-006, S-040 to S-049). Both share the target `input`, so a
 * Turn input off replaces a pending enable's line and its late answer can
 * flip nothing (S-005); it also replaces a pending Turn on (7.4: Turn on is
 * "superseded by any Pause or input Off"). Turn input off needs no context:
 * the server revokes input and pauses harvesting for its own active context
 * (`_revoke_taps`). It is never queued and never blocked by another write
 * (S-004): every click sends a new request ("Turn off again" is a new one).
 */
export const INPUT_TARGET = 'input';

export function sendInputOff(machine: IntentMachine, context: PageContext | null): SendResult {
  return {
    kind: 'sent',
    record: machine.send({
      kind: 'input_off',
      target: INPUT_TARGET,
      endpoint: 'automationAccess',
      path: '/api/automation-access',
      body: { enabled: false },
      key: null,
      meta: {},
      context,
      supersedes: ['turn_on'],
    }),
  };
}

/** Allow game input: a new request per click, never resent (a lost enable stays unresolved, S-048). */
export function sendInputOn(machine: IntentMachine, context: PageContext | null): SendResult {
  return {
    kind: 'sent',
    record: machine.send({
      kind: 'input_on',
      target: INPUT_TARGET,
      endpoint: 'automationAccess',
      path: '/api/automation-access',
      body: { enabled: true },
      key: null,
      meta: {},
      context,
      supersedes: [],
    }),
  };
}
