import { configRevision } from '../../core/config.ts';
import type { EmulatorWrite } from '../../api/intents/emulators.ts';
import {
  CONNECTION_WRITES,
  activeConfigured,
  adoptAnswer,
  blueStacksAdb,
  familyAdb,
  installAnswer,
  instanceActions,
  inventoryView,
  launchAnswer,
  prepareAnswer,
  reconnectAnswer,
  resolutionView,
  serverWords,
  toolsView,
  unconfiguredInstance,
  type InventoryView,
  type ResolutionView,
} from '../../core/emulators.ts';
import { id, list, own, ownPath } from '../../core/read.ts';
import { settingsView } from '../../core/settingsFields.ts';
import type { StateWord } from '../../core/vocabulary.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { Fenced } from '../commands.ts';
import { settledByRead } from '../fences.ts';
import { idleForConnectionChange } from '../../core/connection.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { ContextView, DashboardState, IntentView, ReadRecord } from '../types.ts';
import { commandVM, type ChipVM, type CommandVM } from '../viewModels.ts';
import {
  settingsMatchContext,
  settingsModuleVM,
  settingsSavePending,
  type SettingsModuleVM,
} from './settingsModule.ts';
import { selectTopBarVM, type ReconnectCommand } from './topBar.ts';

/**
 * The Emulators page (emulators brief M1 to M8). Four facts stay apart: what is configured
 * (the config), what the last scan observed (discovery, with its age), the transport's live
 * state (the status read's `device_state`, with its age) and what a request did (its answer,
 * or a later read). None of them is shown as a recognized game screen.
 */

/** An Emulators page write as its view model offered it. */
export interface EmulatorWriteCommand extends Fenced {
  readonly context: ContextView | null;
  readonly action: EmulatorWrite;
}

export type ReadState = 'not_read' | 'unavailable' | 'ready';

export type EmulatorOutcomeCode =
  | 'sending'
  | 'saved'
  | 'accepted'
  | 'already_running'
  | 'failed'
  | 'done'
  | 'refused'
  | 'unknown'
  | 'applied'
  | 'not_applied';

export interface EmulatorOutcomeVM {
  readonly id: string;
  readonly kind: 'adopt' | 'launch' | 'prepare' | 'tools_install' | 'reconnect';
  readonly code: EmulatorOutcomeCode;
  readonly word: StateWord | null;
  /** The address an adopt saved, or the VM a launch or prepare named. */
  readonly target: ServerText<'id'> | null;
  /** The server's message (connect result, manager message), as sent. */
  readonly message: ServerText<'message'> | null;
  /** A refusal's reason, as sent. */
  readonly error: ServerText<'message'> | null;
  /** An adopt's own connect attempt: true, false, or not reported. */
  readonly connected: boolean | null;
  /** A reconnect's answered device state (`device`, `offline`, `unauthorized`, ...). */
  readonly deviceState: ServerText<'id'> | null;
  readonly backup: ServerText<'id'> | null;
  readonly changed: readonly {
    readonly key: ServerText<'id'> | null;
    readonly from: ServerText<'id'> | null;
    readonly to: ServerText<'id'> | null;
  }[];
  readonly added: readonly ServerText<'id'>[];
  readonly answeredAgeSec: number | null;
  readonly restored: boolean;
  readonly dismissOffered: boolean;
}

export interface EmulatorRowVM {
  readonly key: string;
  readonly emulator: ServerText<'name'> | null;
  readonly name: ServerText<'name'> | null;
  /** Discovery only: the VM process, not the transport. */
  readonly running: boolean | null;
  /** A device an adb lists that no detected instance names (its only evidence, F6M-06). */
  readonly listed: boolean;
  readonly serial: ServerText<'id'> | null;
  readonly display: ServerText<'id'> | null;
  readonly displayNotReady: boolean;
  readonly error: ServerText<'message'> | null;
  readonly inUse: boolean;
  readonly use: CommandVM<EmulatorWriteCommand> | null;
  readonly start: CommandVM<EmulatorWriteCommand> | null;
  readonly openGame: CommandVM<EmulatorWriteCommand> | null;
  readonly prepare: CommandVM<EmulatorWriteCommand> | null;
  readonly launchOutcome: EmulatorOutcomeVM | null;
  readonly prepareOutcome: EmulatorOutcomeVM | null;
}

export interface EmulatorsPageVM {
  readonly epoch: number;
  readonly context: ContextView | null;
  readonly instance: ServerText<'id'> | null;
  readonly account: ServerText<'id'> | null;
  /** The configured address of the active instance, or null (not set yet). */
  readonly configuredSerial: ServerText<'id'> | null;
  readonly configuredName: ServerText<'name'> | null;
  /** The last scan's port probe of the configured address. */
  readonly portOpen: boolean | null;
  readonly connection: ChipVM;
  readonly connectionAgeSec: number | null;
  readonly reconnect: CommandVM<ReconnectCommand>;
  readonly reconnectOutcome: EmulatorOutcomeVM | null;
  /**
   * The newest status read lists a boot runner, with that read's age (not tied to any click;
   * null when the newest status read did not answer, review F6M-06).
   */
  readonly boot: { readonly ageSec: number | null } | null;
  /** A first install: the active instance names no address yet (review F6M-01). */
  readonly unconfigured: boolean;
  /**
   * The emulator and account a Use acts on (the page context, or the first-install setup
   * target): the confirmation names exactly these (F6M-C-01).
   */
  readonly target: { readonly instance: string; readonly account: string | null } | null;
  /**
   * F6M-C-02: why no connection request may be sent now (the gates shared by every action:
   * context, scan, one request at a time, a listed runner), or null. An open confirmation
   * closes when this is not null.
   */
  readonly writeGate: string | null;
  readonly scan: {
    readonly read: ReadState;
    readonly ageSec: number | null;
    readonly error: ServerText<'message'> | null;
    /** The scan named another active instance than the page shows: scan again first. */
    readonly otherInstance: boolean;
    readonly placeholder: boolean;
  };
  readonly suggestion: {
    readonly serial: ServerText<'id'> | null;
    /** Detected row containing this suggestion; null when it is not in the scan list. */
    readonly rowKey: string | null;
    readonly name: ServerText<'name'> | null;
    readonly emulator: ServerText<'name'> | null;
    readonly alsoSeen: readonly ServerText<'id'>[];
    readonly use: CommandVM<EmulatorWriteCommand>;
  } | null;
  readonly rows: readonly EmulatorRowVM[];
  /** A single setting for all BlueStacks instances, not an instance-local switch. */
  readonly blueStacksAdb: { readonly enabled: boolean | null } | null;
  readonly configured: readonly {
    readonly instance: ServerText<'id'> | null;
    readonly serial: ServerText<'id'> | null;
    readonly portOpen: boolean | null;
  }[];
  readonly notProbed: readonly {
    readonly emulator: ServerText<'name'> | null;
    readonly note: ServerText<'message'> | null;
  }[];
  readonly adoptOutcome: EmulatorOutcomeVM | null;
  readonly tools: {
    readonly read: ReadState;
    readonly ageSec: number | null;
    readonly installed: boolean;
    readonly inProgress: boolean;
    readonly stage: ServerText<'id'> | null;
    readonly message: ServerText<'message'> | null;
    readonly percent: number | null;
    readonly path: ServerText<'id'> | null;
    readonly failed: boolean;
    /** Install, offered once the person accepted the license (the button's own reason). */
    readonly install: CommandVM<EmulatorWriteCommand>;
    readonly outcome: EmulatorOutcomeVM | null;
  };
  readonly resolution: {
    /** The address a check reads: the active instance's configured address. */
    readonly serial: string | null;
    readonly check: ResolutionVM | null;
    readonly saved: ResolutionVM | null;
  };
  readonly preview: SettingsModuleVM | null;
  readonly previewAgeSec: number | null;
  /**
   * F6M-08: why no preview form is drawn: the settings are not read yet, could not be read,
   * were read for another emulator, account or profile, or have no device-preview module.
   */
  readonly previewState: 'ready' | 'not_read' | 'unavailable' | 'other_context' | 'no_module';
}

export type ResolutionVM =
  | {
      readonly kind: 'measured';
      readonly serial: ServerText<'id'> | null;
      readonly width: number;
      readonly height: number;
      readonly portrait: boolean;
      readonly reference: boolean;
      readonly note: ServerText<'message'> | null;
      readonly checkedAgeSec: number | null;
      readonly savedAt: number | null;
    }
  | {
      readonly kind: 'failed';
      readonly serial: ServerText<'id'> | null;
      readonly error: ServerText<'message'> | null;
      readonly checkedAgeSec: number | null;
      readonly savedAt: number | null;
    }
  /** F6M-07: the check got no answer, or an answer that names no size. */
  | {
      readonly kind: 'no_result';
      readonly answered: boolean;
      readonly status: number | null;
      readonly checkedAgeSec: number | null;
    };

function readState(slot: {
  readonly latest: ReadRecord | null;
  readonly lastAnswered: ReadRecord | null;
}): ReadState {
  if (slot.lastAnswered?.outcome.kind === 'answered') {
    return slot.lastAnswered.outcome.status === 200 ? 'ready' : 'unavailable';
  }
  if (slot.latest === null) return 'not_read';
  return slot.latest.outcome.kind === 'failed' ? 'unavailable' : 'not_read';
}

function ageOf(state: DashboardState, slot: { readonly lastAnswered: ReadRecord | null }) {
  return slot.lastAnswered === null ? null : readAgeSec(state, slot.lastAnswered);
}

/** Records of this emulator instance (an adopt changes the address: its context changes). */
function instanceRecords(state: DashboardState): readonly IntentView[] {
  const instance = state.context?.instance;
  return state.intents.filter(
    (record) => record.context === null || record.context.instance === instance,
  );
}

function ids(values: readonly string[]): readonly ServerText<'id'>[] {
  return values.flatMap((value) => {
    const shown = fromServer('id', value);
    return shown === null ? [] : [shown];
  });
}

/** The point a read must start after to show what a record did: its answer, or its loss. */
function judgedAt(record: IntentView): number {
  return Math.max(record.sentSeq, record.answerSeq ?? record.lostSeq ?? 0);
}

function outcomeVM(
  state: DashboardState,
  record: IntentView | undefined,
): EmulatorOutcomeVM | null {
  if (record === undefined || record.dismissed) return null;
  const kind = record.kind;
  if (
    kind !== 'adopt' &&
    kind !== 'launch' &&
    kind !== 'prepare' &&
    kind !== 'tools_install' &&
    kind !== 'reconnect'
  )
    return null;
  const raw = record.answer;
  const settled = settledByRead(state, record);
  let code: EmulatorOutcomeCode = record.phase === 'sending' ? 'sending' : 'unknown';
  let message: unknown = null;
  let error: unknown = null;
  let connected: boolean | null = null;
  let deviceState: unknown = null;
  let backup: unknown = null;
  let changed: readonly { key: string; from: string | null; to: string | null }[] = [];
  let added: readonly string[] = [];
  if (raw !== null && record.phase !== 'sending') {
    switch (kind) {
      case 'adopt': {
        const answer = adoptAnswer(raw, record.meta);
        if (answer.kind === 'saved') {
          code = 'saved';
          message = answer.message;
          connected = answer.connected;
          backup = answer.backup;
        } else if (answer.kind === 'refused') {
          code = 'refused';
          error = answer.error;
        }
        break;
      }
      case 'launch': {
        const answer = launchAnswer(raw);
        if (answer.kind === 'accepted' || answer.kind === 'failed') {
          code = answer.kind;
          message = answer.message;
        } else if (answer.kind === 'refused') {
          code = 'refused';
          error = answer.error;
        }
        break;
      }
      case 'prepare': {
        const answer = prepareAnswer(raw);
        if (answer.kind === 'done') {
          code = 'done';
          message = answer.message;
          backup = answer.backup;
          changed = answer.changed;
          added = answer.added;
        } else if (answer.kind === 'refused') {
          code = 'refused';
          error = answer.error;
        }
        break;
      }
      case 'tools_install': {
        const answer = installAnswer(raw);
        if (answer.kind === 'accepted') code = answer.started ? 'accepted' : 'already_running';
        else if (answer.kind === 'refused') {
          code = 'refused';
          error = answer.error;
        }
        break;
      }
      case 'reconnect': {
        const answer = reconnectAnswer(raw);
        if (answer.kind === 'answered') {
          code = answer.online ? 'done' : 'failed';
          message = answer.message;
          deviceState = answer.deviceState;
        } else if (answer.kind === 'refused') {
          code = 'refused';
          error = answer.error;
        }
        break;
      }
    }
  }
  // F6M-09: an answer that proves nothing keeps the server's own words beside Unknown.
  if (code === 'unknown' && raw !== null && error === null) error = serverWords(raw);
  if (code === 'unknown' && (settled === 'applied' || settled === 'not_applied')) code = settled;
  const target =
    kind === 'adopt'
      ? record.meta.serial
      : kind === 'launch' || kind === 'prepare'
        ? (record.meta.name ?? record.meta.index)
        : undefined;
  return {
    id: record.id,
    kind,
    code,
    word:
      code === 'sending'
        ? 'waiting'
        : code === 'unknown'
          ? 'unknown'
          : code === 'refused' || code === 'failed' || code === 'not_applied'
            ? 'blocked'
            : null,
    target: fromServer('id', target),
    message: fromServer('message', message),
    error: fromServer('message', error),
    connected,
    deviceState: fromServer('id', deviceState),
    backup: fromServer('id', backup),
    changed: changed.map((entry) => ({
      key: fromServer('id', entry.key),
      from: fromServer('id', entry.from),
      to: fromServer('id', entry.to),
    })),
    added: ids(added),
    answeredAgeSec:
      record.answeredAtMs === null
        ? null
        : Math.max(0, Math.floor((state.nowMs - record.answeredAtMs) / 1000)),
    restored: record.restored,
    dismissOffered: code !== 'sending' && code !== 'unknown',
  };
}

function resolutionVM(
  view: ResolutionView | null,
  checkedAgeSec: number | null,
  saved: boolean,
): ResolutionVM | null {
  if (view === null) return null;
  const savedAt = saved ? view.at : null;
  return view.kind === 'measured'
    ? {
        kind: 'measured',
        serial: fromServer('id', view.serial),
        width: view.width,
        height: view.height,
        portrait: view.portrait,
        reference: view.reference,
        note: fromServer('message', view.note),
        checkedAgeSec,
        savedAt,
      }
    : {
        kind: 'failed',
        serial: fromServer('id', view.serial),
        error: fromServer('message', view.error),
        checkedAgeSec,
        savedAt,
      };
}

export function selectEmulatorsPage(state: DashboardState): EmulatorsPageVM {
  const context = state.context;
  const top = selectTopBarVM(state);
  const records = instanceRecords(state);
  const newest = (target: string) => records.filter((record) => record.target === target).at(-1);
  // F6M-03: one connection request at a time, Reconnect included, whatever its context.
  const sendingAny = state.intents.some(
    (record) => CONNECTION_WRITES.includes(record.kind) && record.phase === 'sending',
  );
  const scanSlot = state.reads.wizardEmulators;
  const inventory: InventoryView | null = inventoryView(answeredBody(state, 'wizardEmulators'));
  const scanRead = inventory === null ? readState(scanSlot) : 'ready';
  const config = answeredBody(state, 'config');
  // F6M-01: a first install has no page context (no address yet); connection setup acts on
  // the active instance the config read names, until an adopt gives it an address.
  const setup = context === null ? unconfiguredInstance(config) : null;
  const target = context !== null && state.contextConfirmed ? context : setup;
  const otherInstance =
    inventory !== null &&
    target !== null &&
    inventory.activeInstance !== null &&
    inventory.activeInstance !== target.instance;
  const baseRevision = configRevision(config);
  const configuredSerial =
    context === null ? undefined : id(ownPath(config, 'instances', context.instance, 'serial'));
  const lastScanSeq = scanSlot.lastAnswered?.startedSeq ?? 0;

  const command = (action: EmulatorWrite): EmulatorWriteCommand => ({
    epoch: state.epoch,
    context,
    action,
  });
  const writeReason = (): string | null =>
    target === null
      ? 'context_unknown'
      : inventory === null
        ? 'scan_not_read'
        : otherInstance
          ? 'scan_other_instance'
          : sendingAny
            ? 'write_in_flight'
            : null;
  // Adopt, launch and the tools install are refused by the server while a runner is alive.
  const idle = idleForConnectionChange(contextReadOf(latestRead(state, 'status')));
  const idleReason = idle === 'ready' ? null : idle;
  const offered = (reason: string | null, next: EmulatorWriteCommand | null) =>
    reason === null && next !== null
      ? commandVM(true, true, null, false, next)
      : commandVM<EmulatorWriteCommand>(true, false, reason ?? 'context_unknown', sendingAny, null);
  // M2: an adopt whose outcome is not known yet blocks another until a config read made
  // after its answer (or its loss) arrives. That read settles it only by naming the sent
  // address; otherwise it stays Unknown, and a new adopt is the person's explicit choice.
  const adoptRecord = records.filter((record) => record.kind === 'adopt').at(-1);
  const adoptPoint = adoptRecord?.answerSeq ?? adoptRecord?.lostSeq ?? 0;
  const adoptPending =
    adoptRecord !== undefined &&
    adoptRecord.phase === 'unconfirmed' &&
    settledByRead(state, adoptRecord) === null &&
    (state.reads.config.lastAnswered?.startedSeq ?? 0) <= adoptPoint;
  const adopt = (serial: string, adb: string | null): CommandVM<EmulatorWriteCommand> => {
    const reason = writeReason() ?? idleReason ?? (adoptPending ? 'adopt_unresolved' : null);
    return offered(
      reason,
      reason === null && inventory !== null && inventory.activeInstance !== null
        ? command({
            kind: 'adopt',
            serial,
            adb,
            expectInstance: inventory.activeInstance,
            expectPlaceholder: inventory.placeholder,
            baseRevision,
          })
        : null,
    );
  };
  /** M3: no duplicate launch: after a launch answered or lost, scan again before another. */
  const launch = (emulator: string, index: string, name: string | null) => {
    const target = `emulators:launch:${emulator}:${index}`;
    const last = newest(target);
    const waiting = last !== undefined && last.phase !== 'refused' && lastScanSeq <= judgedAt(last);
    const reason = writeReason() ?? idleReason ?? (waiting ? 'launch_wait_scan' : null);
    return offered(
      reason,
      reason === null ? command({ kind: 'launch', emulator, index, name }) : null,
    );
  };

  /** M4: a prepare; after one answered or lost, scan again before another. */
  const prepare = (
    index: string,
    name: string | null,
    waiting: boolean,
    blocked: string | null,
  ) => {
    const reason = writeReason() ?? idleReason ?? blocked ?? (waiting ? 'prepare_wait_scan' : null);
    return offered(reason, reason === null ? command({ kind: 'prepare', index, name }) : null);
  };
  const rows: EmulatorRowVM[] = (inventory?.instances ?? []).map((row) => {
    const actions = inventory === null ? null : instanceActions(inventory, row);
    const launchTarget =
      row.emulator === null || row.index === null
        ? null
        : `emulators:launch:${row.emulator}:${row.index}`;
    const prepareTarget = row.index === null ? null : `emulators:prepare:${row.index}`;
    const prepareLast = prepareTarget === null ? undefined : newest(prepareTarget);
    const prepareWaiting =
      prepareLast !== undefined &&
      prepareLast.phase !== 'refused' &&
      lastScanSeq <= judgedAt(prepareLast);
    return {
      key: row.key,
      emulator: fromServer('name', row.emulator),
      name: fromServer('name', row.name),
      running: row.running,
      listed: false,
      serial: fromServer('id', row.serial),
      display: fromServer('id', row.display),
      displayNotReady: row.displayOk === false,
      error: fromServer('message', row.error),
      inUse: actions?.inUse === true,
      use:
        actions?.use === true && row.serial !== null && inventory !== null
          ? adopt(row.serial, familyAdb(inventory, row.emulator))
          : null,
      start:
        actions?.start === true && row.emulator !== null && row.index !== null
          ? launch(row.emulator, row.index, row.name)
          : null,
      openGame:
        actions?.openGame === true && row.emulator !== null && row.index !== null
          ? launch(row.emulator, row.index, row.name)
          : null,
      prepare:
        actions?.prepare === true && row.index !== null
          ? prepare(row.index, row.name, prepareWaiting, actions.prepareBlocked)
          : null,
      launchOutcome: launchTarget === null ? null : outcomeVM(state, newest(launchTarget)),
      prepareOutcome: prepareTarget === null ? null : outcomeVM(state, prepareLast),
    };
  });
  // Devices an adb lists that no detected instance names (a daemon device): Use, or In use.
  const instanceSerials = new Set((inventory?.instances ?? []).map((row) => row.serial));
  for (const tool of inventory?.scanned ?? []) {
    for (const device of tool.devices) {
      if (instanceSerials.has(device)) continue;
      instanceSerials.add(device);
      const used = inventory?.configured.some((row) => row.serial === device) === true;
      rows.push({
        key: `device:${device}`,
        emulator: fromServer('name', tool.emulator),
        name: null,
        running: null,
        listed: true,
        serial: fromServer('id', device),
        display: null,
        displayNotReady: false,
        error: null,
        inUse: used,
        use: used ? null : adopt(device, tool.adb),
        start: null,
        openGame: null,
        prepare: null,
        launchOutcome: null,
        prepareOutcome: null,
      });
    }
  }

  const suggestion = inventory?.suggestion ?? null;
  const suggestedSerial = fromServer('id', suggestion?.serial);
  const suggestedRowKey =
    suggestedSerial === null
      ? null
      : (rows.find((row) => row.serial === suggestedSerial)?.key ?? null);
  const active = inventory === null ? null : activeConfigured(inventory);
  const toolsSlot = state.reads.wizardTools;
  const tools = toolsView(answeredBody(state, 'wizardTools'));
  const installTarget = 'emulators:tools';
  const installLast = newest(installTarget);
  const installWaiting =
    installLast !== undefined &&
    installLast.phase !== 'refused' &&
    (toolsSlot.lastAnswered?.startedSeq ?? 0) <= judgedAt(installLast);
  // F6M-05: the common context, scan and one-request gates first, then the tools' own.
  const installReason =
    writeReason() ??
    (tools === null
      ? 'tools_not_read'
      : tools.installed
        ? 'tools_installed'
        : tools.inProgress
          ? 'install_running'
          : (idleReason ?? (installWaiting ? 'install_wait_read' : null)));

  const check = state.resolutionCheck;
  const checkSerial = configuredSerial ?? null;
  const checkedAgeSec =
    check === null ? null : Math.max(0, Math.floor((state.nowMs - check.atMs) / 1000));
  const checkedView = check === null ? null : resolutionView(check.body);
  // F6M-07: a check that got no answer, or one naming no size, says so (never nothing).
  const checked: ResolutionVM | null =
    check === null || check.serial !== checkSerial
      ? null
      : checkedView === null
        ? {
            kind: 'no_result',
            // A status is an answer, even when its body is not one this page can read.
            answered: check.result.kind === 'answered' || check.result.status !== null,
            status: check.result.status,
            checkedAgeSec,
          }
        : resolutionVM(checkedView, checkedAgeSec, false);
  const savedSection = own(answeredBody(state, 'wizardState'), 'resolution');
  const savedView = resolutionView(savedSection);
  const saved =
    savedView !== null && savedView.serial === checkSerial
      ? resolutionVM(savedView, null, true)
      : null;

  const settings = settingsView(answeredBody(state, 'settings'));
  // F6M-06: the boot runner is what the newest status read lists, dated; a failed newer
  // read leaves nothing to say.
  const status = latestRead(state, 'status');
  const processes =
    status?.outcome.kind === 'answered' ? (list(own(status.outcome.body, 'processes')) ?? []) : [];
  const preview = settingsModuleVM(state, settings, 'device-preview', {
    // F6M-04: this page reads no profile source; its saves wait for the settings read only.
    pending: settingsSavePending(state, ['settings']),
  });
  const shown = target ?? context;
  return {
    epoch: state.epoch,
    context,
    instance: fromServer('id', shown?.instance),
    account: fromServer('id', shown?.account),
    unconfigured: setup !== null,
    target: shown === null ? null : { instance: shown.instance, account: shown.account },
    writeGate: writeReason() ?? idleReason,
    blueStacksAdb: inventory === null ? null : blueStacksAdb(inventory),
    configuredSerial: fromServer('id', configuredSerial),
    configuredName: top.emulator.kind === 'known' ? top.emulator.value : null,
    portOpen: active?.serial === configuredSerial ? (active?.portOpen ?? null) : null,
    connection: top.connection,
    connectionAgeSec: top.connectionAgeSec,
    reconnect: top.reconnect,
    reconnectOutcome: outcomeVM(state, newest('reconnect')),
    boot:
      status !== null && processes.some((process) => own(process, 'runner') === 'boot')
        ? { ageSec: readAgeSec(state, status) }
        : null,
    scan: {
      read: scanRead,
      ageSec: ageOf(state, scanSlot),
      error: fromServer('message', inventory?.error),
      otherInstance,
      placeholder: inventory?.placeholder === true,
    },
    suggestion:
      suggestion === null || inventory === null || otherInstance
        ? null
        : {
            serial: suggestedSerial,
            rowKey: suggestedRowKey,
            name: fromServer('name', suggestion.name),
            emulator: fromServer('name', suggestion.emulator),
            alsoSeen: ids(suggestion.alsoSeen),
            use: inventory.configured.some((row) => row.serial === suggestion.serial)
              ? commandVM<EmulatorWriteCommand>(true, false, 'in_use', false, null)
              : adopt(suggestion.serial, suggestion.adb),
          },
    rows,
    configured: (inventory?.configured ?? []).map((row) => ({
      instance: fromServer('id', row.instance),
      serial: fromServer('id', row.serial),
      portOpen: row.portOpen,
    })),
    notProbed: (inventory?.scanned ?? [])
      .filter((tool) => tool.note !== null)
      .map((tool) => ({
        emulator: fromServer('name', tool.emulator),
        note: fromServer('message', tool.note),
      })),
    adoptOutcome: outcomeVM(state, adoptRecord),
    tools: {
      read: tools === null ? readState(toolsSlot) : 'ready',
      ageSec: ageOf(state, toolsSlot),
      installed: tools?.installed === true,
      inProgress: tools?.inProgress === true,
      stage: fromServer('id', tools?.stage),
      message: fromServer('message', tools?.message),
      percent: tools?.percent ?? null,
      path: fromServer('id', tools?.path),
      failed: tools?.stage === 'error',
      install: offered(
        installReason,
        installReason === null ? command({ kind: 'tools_install' }) : null,
      ),
      outcome: outcomeVM(state, installLast),
    },
    resolution: { serial: checkSerial, check: checked, saved },
    preview,
    previewAgeSec: ageOf(state, state.reads.settings),
    previewState:
      settings === null
        ? readState(state.reads.settings) === 'not_read'
          ? 'not_read'
          : 'unavailable'
        : !settingsMatchContext(settings, context)
          ? 'other_context'
          : preview === null
            ? 'no_module'
            : 'ready',
  };
}
