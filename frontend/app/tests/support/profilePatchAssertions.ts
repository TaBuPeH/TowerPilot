import { expect } from 'vitest';
import { own } from '../../src/core/read.ts';
import type { IntentMachine } from '../../src/api/intents/machine.ts';
export function assertProfilePatchContexts(machine: IntentMachine) {
  for (const intent of machine.snapshot()) {
    if (intent.request.path !== '/api/profile-patch') continue;
    expect(own(JSON.parse(intent.request.body), 'expected_context')).toEqual(intent.context);
  }
}
