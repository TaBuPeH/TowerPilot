import { recordDecision, type RecordDecision } from './battle.ts';
import { entries, finite, list, own, record, revision, stringList, text } from './read.ts';
import { decide, type ChipDecision } from './vocabulary.ts';

/** Backend wire values PER KIND: _command/acknowledge/_finish in workflows/battles.py. */
export const COMMAND_WIRE_STATES: Readonly<Record<'controls' | 'action', readonly string[]>> =
  Object.freeze({
    controls: Object.freeze(['requested', 'pending', 'applied', 'rejected', 'expired']),
    action: Object.freeze(['requested', 'started', 'completed', 'failed', 'rejected', 'expired']),
  });

export const TERMINAL_COMMAND_STATES: readonly string[] = Object.freeze([
  'applied',
  'completed',
  'failed',
  'rejected',
  'expired',
]);

export interface CommandView {
  readonly decision: ChipDecision | RecordDecision<'K4', 'done'>;
  readonly id: unknown;
  readonly state: unknown;
  readonly kind: unknown;
  readonly revision: number | null;
  readonly payload: unknown;
  readonly requestedAt: number | null;
  readonly updatedAt: number | null;
  readonly reason: unknown;
  readonly evidence: unknown;
  readonly appliedPaths: readonly unknown[] | null;
}

export function payloadPaths(payload: unknown, prefix = ''): readonly string[] {
  return entries(payload).flatMap(([key, value]) => {
    const path = prefix === '' ? key : `${prefix}.${key}`;
    // workflows/control_settings.py flatten treats shopping_plan as one path.
    return path === 'shopping_plan' || record(value) === undefined
      ? [path]
      : payloadPaths(value, path);
  });
}

/** K1–K7. Display labels, prototype names and wrong-kind states never match. */
export function commandView(command: unknown): CommandView {
  const kind = own(command, 'kind');
  const state = own(command, 'state');
  const paths = stringList(own(command, 'applied_paths'));
  const requestedPaths = payloadPaths(own(command, 'payload'));
  const raw = {
    id: own(command, 'id'),
    state,
    kind,
    revision: revision(own(command, 'revision')) ?? null,
    payload: own(command, 'payload'),
    requestedAt: finite(own(command, 'requested_at')) ?? null,
    updatedAt: finite(own(command, 'updated_at')) ?? null,
    reason: own(command, 'reason'),
    evidence: own(command, 'evidence'),
    appliedPaths: list(own(command, 'applied_paths')) ?? null,
  };
  const allowed =
    kind === 'controls'
      ? COMMAND_WIRE_STATES.controls
      : kind === 'action'
        ? COMMAND_WIRE_STATES.action
        : [];
  if (typeof state !== 'string' || !allowed.includes(state))
    return { ...raw, decision: decide('K7', 'unknown', 'command_state_unrecognised', null) };
  let decision: CommandView['decision'];
  switch (state) {
    case 'requested':
      decision = decide('K1', 'waiting', 'sent_waiting_for_pickup', null);
      break;
    case 'pending':
      decision =
        paths === undefined ||
        (paths.length > 0 &&
          (paths.length >= requestedPaths.length ||
            paths.some((path) => !requestedPaths.includes(path)) ||
            new Set(paths).size !== paths.length))
          ? decide('K7', 'unknown', 'command_paths_unreadable', null)
          : paths.length === 0
            ? decide('K2', 'waiting', 'waiting_to_apply', null)
            : decide('K2a', 'waiting', 'partly_applied', null);
      break;
    case 'started':
      decision = decide('K3', 'waiting', 'started_waiting_for_result', null);
      break;
    case 'applied':
    case 'completed':
      decision = recordDecision('K4', 'done', command);
      break;
    case 'failed':
    case 'rejected':
      decision = decide('K5', 'failed', 'command_refused_or_failed', null);
      break;
    case 'expired':
      decision = decide(
        'K6',
        'failed',
        raw.reason === 'battle_ended' ? 'battle_record_closed' : 'command_expired',
        null,
      );
      break;
    default:
      decision = decide('K7', 'unknown', 'command_state_unrecognised', null);
  }
  return { ...raw, decision };
}

/** True only for a recognised terminal state of this command's own kind. */
export function commandTerminal(command: unknown): boolean {
  return commandViewTerminal(commandView(command));
}

/** `commandTerminal` for a command already read into its view (a receipt or a listed row). */
export function commandViewTerminal(view: CommandView): boolean {
  const state = text(view.state);
  return (
    view.decision.row !== 'K7' && state !== undefined && TERMINAL_COMMAND_STATES.includes(state)
  );
}
