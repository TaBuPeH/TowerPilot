import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * The collection handoff (POST /api/setup-battle/collection-handoff; spec
 * 7.4, S-101). Sent only from a person's explicit confirmation in the
 * handoff dialog, bound to the operation, setup battle, evidence revision and
 * context on screen; never automatically, never for another battle. A resend
 * needs a new dialog confirmation (`machine.resend(id, {confirmedAgain: true})`).
 */
export interface HandoffConfirmation {
  readonly operationId: string;
  readonly setupBattleId: string;
  readonly evidenceRevision: string;
  /** The person's attestation in the dialog; nothing else may produce it. */
  readonly attestation: 'new_manual_battle';
}

export function sendHandoff(
  machine: IntentMachine,
  confirmation: HandoffConfirmation,
  context: PageContext,
): SendResult {
  const target = `handoff:${confirmation.operationId}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'handoff',
      target,
      endpoint: 'handoff',
      path: '/api/setup-battle/collection-handoff',
      body: {
        operation_id: confirmation.operationId,
        setup_battle_id: confirmation.setupBattleId,
        evidence_revision: confirmation.evidenceRevision,
        expected_context: {
          instance: context.instance,
          account: context.account,
          serial: context.serial,
        },
        attestation: confirmation.attestation,
      },
      key: null,
      meta: {
        operationId: confirmation.operationId,
        setupBattleId: confirmation.setupBattleId,
        evidenceRevision: confirmation.evidenceRevision,
      },
      context,
      supersedes: [],
    }),
  );
}
