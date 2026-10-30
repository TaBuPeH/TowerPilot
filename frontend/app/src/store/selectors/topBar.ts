import { knownFact, unknownFact, type Fact } from '../../core/facts.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import { connectionView, reconnectGate } from '../../core/connection.ts';
import { CONNECTION_WRITES } from '../../core/emulators.ts';
import { finite, id, list, own, ownPath } from '../../core/read.ts';
import type { Fenced } from '../commands.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { ContextView, DashboardState } from '../types.ts';
import { chipVM, commandVM, type ChipVM, type CommandVM } from '../viewModels.ts';

/**
 * The top bar's account fact (dashboard-spec F-002), using the sanctioned
 * path: raw read -> store selector -> ServerText -> view model. The view model
 * holds server text and codes only, never catalogue wording.
 */
export interface TopBarVM {
  readonly account: Fact<ServerText<'name'>>;
}

/** The untrusted accounts read, before this selector checks its fields. */
export interface AccountsRead {
  readonly body: unknown;
  readonly observedAt: number;
}

/** Own properties of a plain object, or null (S-095: inherited names never match). */
function ownEntries(value: unknown): ReadonlyMap<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null;
  }
  return new Map(Object.entries(value));
}

export function selectTopBar(read: AccountsRead | null): TopBarVM {
  if (read === null) {
    return { account: unknownFact('not_read') };
  }
  const body = ownEntries(read.body);
  const active = body?.get('active');
  const accounts = body?.get('accounts');
  if (active === null && Array.isArray(accounts)) {
    return { account: unknownFact('no_active_account') };
  }
  if (typeof active !== 'string' || !Array.isArray(accounts)) {
    return { account: unknownFact('accounts_unreadable') };
  }
  for (const entry of accounts) {
    const account = ownEntries(entry);
    if (account?.get('id') === active) {
      const label = fromServer('name', account.get('label'));
      return {
        account: label === null ? unknownFact('no_label') : knownFact(label, read.observedAt),
      };
    }
  }
  return { account: unknownFact('no_active_account') };
}

/**
 * Reconnect as the view model offered it (review F6M-02): the page context it was drawn in
 * and the address it showed, sent in the body so the server connects that address.
 */
export interface ReconnectCommand extends Fenced {
  readonly context: ContextView;
  readonly serial: string;
}

export interface TopBarStateVM extends TopBarVM {
  readonly emulator: Fact<ServerText<'name'>>;
  /** The configured key remains available when the display name is unknown (7.2). */
  readonly emulatorInstance: ServerText<'id'> | null;
  readonly connection: ChipVM;
  readonly reconnect: CommandVM<ReconnectCommand>;
  readonly reconnectAction: 'choose_emulator' | 'reconnect';
  readonly accountAgeSec: number | null;
  readonly accountLastKnown: boolean;
  readonly connectionAgeSec: number | null;
}

export function selectTopBarVM(state: DashboardState): TopBarStateVM {
  const accounts = state.reads.accounts.lastAnswered;
  const account = selectTopBar(
    accounts?.outcome.kind === 'answered'
      ? { body: accounts.outcome.body, observedAt: accounts.arrivedAtMs / 1000 }
      : null,
  ).account;
  const config = answeredBody(state, 'config');
  const instance = id(own(config, 'active_instance'));
  const serial =
    instance === undefined ? undefined : id(ownPath(config, 'instances', instance, 'serial'));
  const emulators = latestRead(state, 'wizardEmulators');
  const emulatorBody = emulators?.outcome.kind === 'answered' ? emulators.outcome.body : undefined;
  // The wizard exposes a display name by adb port, not by the config's instance key.
  const match = (list(own(emulatorBody, 'instances')) ?? []).find((row) => {
    const port = finite(own(row, 'adb_port'));
    return port !== undefined && serial === `127.0.0.1:${port}`;
  });
  const name = fromServer('name', own(match, 'name'));
  const status = latestRead(state, 'status');
  // F6M-03: any connection request awaiting its reply holds Reconnect too.
  const sending = state.intents.some(
    (r) => CONNECTION_WRITES.includes(r.kind) && r.phase === 'sending',
  );
  const gate = reconnectGate(serial, contextReadOf(status), sending);
  const reconnectCommand: ReconnectCommand | null =
    gate.enabled && gate.action === 'reconnect' && state.context !== null && serial !== undefined
      ? { epoch: state.epoch, context: state.context, serial }
      : null;
  return {
    account,
    emulator:
      name !== null && emulators !== null
        ? knownFact(name, emulators.arrivedAtMs / 1000)
        : unknownFact('emulator_name_unknown'),
    emulatorInstance: fromServer('id', instance),
    connection: chipVM(
      connectionView(contextReadOf(status), status === null ? 0 : readAgeSec(state, status)),
    ),
    reconnect:
      reconnectCommand === null
        ? commandVM<ReconnectCommand>(
            true,
            gate.action === 'choose_emulator',
            gate.reason === 'ready' ? 'process_list_unavailable' : gate.reason,
            sending,
            null,
          )
        : commandVM(true, true, null, false, reconnectCommand),
    reconnectAction: gate.action,
    accountAgeSec: accounts === null ? null : readAgeSec(state, accounts),
    accountLastKnown: state.reads.accounts.latest?.outcome.kind === 'failed',
    connectionAgeSec:
      state.reads.status.lastAnswered === null
        ? null
        : readAgeSec(state, state.reads.status.lastAnswered),
  };
}
