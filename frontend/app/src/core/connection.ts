import { finite, id, list, own } from './read.ts';
import { decide, type ChipDecision } from './vocabulary.ts';

/** A read is fenced by the context in which the page requested it. */
export type ContextRead =
  | { readonly kind: 'answered'; readonly body: unknown; readonly sameContext: boolean }
  | { readonly kind: 'failed' }
  | null;

export type ConnectionLine =
  | 'connection_unconfirmed'
  | 'connected'
  | 'adb_offline'
  | 'adb_unauthorized'
  | 'adb_missing'
  | 'adb_unreachable'
  | 'emulator_unconfigured';

/** C1–C4 / EV-11. Arrival age is supplied, never taken from a browser clock. */
export function connectionView(
  read: ContextRead,
  readAgeSec: number,
): ChipDecision<'C1' | 'C2' | 'C3' | 'C4', ConnectionLine> {
  const age = finite(readAgeSec);
  if (read?.kind !== 'answered' || !read.sameContext || age === undefined || age < 0 || age > 10) {
    return decide('C1', 'unknown', 'connection_unconfirmed', null, { ageSec: age ?? null });
  }
  // backend/dashboard.py api_status/_device_state; adbclient.py _ask_state.
  switch (own(read.body, 'device_state')) {
    case 'device':
      return decide('C2', null, 'connected', 'EV-11');
    case 'offline':
      return decide('C3', 'blocked', 'adb_offline', null);
    case 'unauthorized':
      return decide('C3', 'blocked', 'adb_unauthorized', null);
    case 'missing':
      return decide('C3', 'blocked', 'adb_missing', null);
    case 'no_server':
      return decide('C3', 'blocked', 'adb_unreachable', null);
    case 'unconfigured':
      return decide('C3', 'blocked', 'emulator_unconfigured', null);
    default:
      return decide('C4', 'unknown', 'connection_unconfirmed', null);
  }
}

/**
 * The backend refuses a connection change (reconnect, adopt, launch, tools install) while a
 * runner is alive (dashboard.py `_require_idle`). The page offers one only when the newest
 * same-context status read lists no process; the server's own check stays authoritative.
 */
export function idleForConnectionChange(
  status: ContextRead,
): 'ready' | 'process_list_unavailable' | 'process_listed' {
  const processes =
    status?.kind === 'answered' && status.sameContext
      ? list(own(status.body, 'processes'))
      : undefined;
  if (processes === undefined) return 'process_list_unavailable';
  return processes.length === 0 ? 'ready' : 'process_listed';
}

/** F-004: Choose emulator is navigation; a listed process prevents reconnect. */
export function reconnectGate(
  serial: unknown,
  status: ContextRead,
  sending: boolean,
): {
  readonly action: 'choose_emulator' | 'reconnect';
  readonly enabled: boolean;
  readonly reason:
    'ready' | 'choose_emulator' | 'sending' | 'process_list_unavailable' | 'process_listed';
} {
  if (id(serial) === undefined)
    return { action: 'choose_emulator', enabled: true, reason: 'choose_emulator' };
  if (sending) return { action: 'reconnect', enabled: false, reason: 'sending' };
  const idle = idleForConnectionChange(status);
  return { action: 'reconnect', enabled: idle === 'ready', reason: idle };
}
