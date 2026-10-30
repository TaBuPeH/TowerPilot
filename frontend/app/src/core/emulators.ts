import { finite, id, list, own, ownPath, record, stringList, text } from './read.ts';

/**
 * The Emulators page's rules (emulators brief M1 to M8). Discovery, configuration, a
 * transport's live state and the result of a request are four different facts: a
 * configured address, an open port, a running VM or a saved adoption is never shown as a
 * connected device, and a launch or an adoption is never shown as a recognized game screen.
 */

export interface ConfiguredInstance {
  readonly instance: string;
  /** The configured address, or null while it is not set yet. */
  readonly serial: string | null;
  /** The scan's port probe: open, closed, or not probed (an address that has no port). */
  readonly portOpen: boolean | null;
}

export interface DetectedInstance {
  /** A stable key: emulator family and index (or the row number when the scan has none). */
  readonly key: string;
  readonly emulator: string | null;
  /** The emulator's own instance index or key (MuMu a number, BlueStacks a name). */
  readonly index: string | null;
  readonly name: string | null;
  readonly running: boolean | null;
  /** `127.0.0.1:<adb port>` when the scan names the port, else null. */
  readonly serial: string | null;
  readonly adbEnabled: boolean | null;
  readonly display: string | null;
  readonly displayOk: boolean | null;
  /** A per-instance scan error (the emulator manager answered something unreadable). */
  readonly error: string | null;
}

export interface ScannedTool {
  readonly emulator: string | null;
  readonly adb: string | null;
  readonly devices: readonly string[];
  /** Why it was not probed (a foreign adb would restart the shared daemon), or null. */
  readonly note: string | null;
}

export interface Suggestion {
  readonly serial: string;
  readonly emulator: string | null;
  readonly name: string | null;
  readonly adb: string | null;
  readonly alsoSeen: readonly string[];
}

export interface InventoryView {
  readonly activeInstance: string | null;
  /** The active instance has no address yet: an adopt may only fill it (expect_placeholder). */
  readonly placeholder: boolean;
  readonly configured: readonly ConfiguredInstance[];
  readonly instances: readonly DetectedInstance[];
  /** HD-Player can be running even when no instance row identifies its VM. */
  readonly blueStacksRunning: boolean;
  readonly scanned: readonly ScannedTool[];
  readonly suggestion: Suggestion | null;
  /** The scan's own time (server seconds), or null. */
  readonly scannedAt: number | null;
  /** A whole-scan error, or null. */
  readonly error: string | null;
}

function indexOf(value: unknown): string | null {
  const number = finite(value);
  if (number !== undefined) return String(number);
  return id(value) ?? null;
}

/** A readable discovery answer (`GET /api/wizard/emulators`), else null. */
export function inventoryView(body: unknown): InventoryView | null {
  const configured = list(own(body, 'configured'));
  if (configured === undefined) return null;
  const portOpen = (value: unknown) => (typeof value === 'boolean' ? value : null);
  const flag = (value: unknown) => (typeof value === 'boolean' ? value : null);
  const suggestion = own(body, 'suggestion');
  const suggested = id(own(suggestion, 'serial'));
  return {
    activeInstance: id(own(body, 'active_instance')) ?? null,
    placeholder: own(body, 'config_serial_placeholder') === true,
    blueStacksRunning: (list(own(body, 'running')) ?? []).some((row) =>
      isBlueStacks(text(own(row, 'emulator')) ?? null),
    ),
    configured: configured.flatMap((row): ConfiguredInstance[] => {
      const instance = id(own(row, 'instance'));
      if (instance === undefined) return [];
      const serial = text(own(row, 'serial'));
      return [
        {
          instance,
          serial: serial === undefined || serial === '' ? null : serial,
          portOpen: portOpen(own(row, 'port_open')),
        },
      ];
    }),
    instances: (list(own(body, 'instances')) ?? []).map((row, at): DetectedInstance => {
      const emulator = text(own(row, 'emulator')) ?? null;
      const index = indexOf(own(row, 'index'));
      const port = finite(own(row, 'adb_port'));
      return {
        key: `${emulator ?? '?'}:${index ?? `#${String(at)}`}`,
        emulator,
        index,
        name: text(own(row, 'name')) ?? null,
        running: flag(own(row, 'running')),
        serial: port === undefined ? null : `127.0.0.1:${String(port)}`,
        adbEnabled: flag(own(row, 'adb_enabled')),
        display: text(own(row, 'display')) ?? null,
        displayOk: flag(own(row, 'display_ok')),
        error: text(own(row, 'error')) ?? null,
      };
    }),
    scanned: (list(own(body, 'scanned')) ?? []).map((row) => ({
      emulator: text(own(row, 'emulator')) ?? null,
      adb: text(own(row, 'adb')) ?? null,
      devices: stringList(own(row, 'devices')) ?? [],
      note: text(own(row, 'note')) ?? null,
    })),
    suggestion:
      suggested === undefined
        ? null
        : {
            serial: suggested,
            emulator: text(own(suggestion, 'emulator')) ?? null,
            name: text(own(suggestion, 'name')) ?? null,
            adb: text(own(suggestion, 'adb')) ?? null,
            alsoSeen: stringList(own(suggestion, 'also_seen')) ?? [],
          },
    scannedAt: finite(own(body, 't')) ?? null,
    error: text(own(body, 'error')) ?? null,
  };
}

/** The configured row of the active instance, or null. */
export function activeConfigured(view: InventoryView): ConfiguredInstance | null {
  return view.configured.find((row) => row.instance === view.activeInstance) ?? null;
}

/** True when some configured instance already uses `serial` ("In use": no Use button). */
export function inUse(view: InventoryView, serial: string): boolean {
  return view.configured.some((row) => row.serial === serial);
}

/**
 * The adb binary of an instance's emulator family, from the scan: the family's own adb
 * (MuMu's adb.exe, BlueStacks' HD-Adb.exe), never a plain `adb` on PATH. Adopt writes it into
 * the configuration so the device is driven with its own adb (mixing them kills the shared
 * daemon). Null when the scan lists none.
 */
export function familyAdb(view: InventoryView, emulator: string | null): string | null {
  const family = (emulator ?? '').split(' ')[0] ?? '';
  if (family === '') return null;
  const found = view.scanned.find(
    (tool) =>
      tool.adb !== null && !/^adb$/iu.test(tool.adb) && (tool.emulator ?? '').startsWith(family),
  );
  return found?.adb ?? null;
}

/** The emulator families the launch route drives. */
const LAUNCHABLE = ['MuMu', 'BlueStacks'];

function launchable(row: DetectedInstance): boolean {
  return row.index !== null && LAUNCHABLE.some((family) => (row.emulator ?? '').startsWith(family));
}

function isBlueStacks(emulator: string | null): boolean {
  return (emulator ?? '').startsWith('BlueStacks');
}

/** BlueStacks publishes one shared ADB switch, repeated on its instance rows. */
export function blueStacksAdb(view: InventoryView): { readonly enabled: boolean | null } | null {
  const rows = view.instances.filter((row) => isBlueStacks(row.emulator));
  if (rows.length === 0) return null;
  const flags = rows.map((row) => row.adbEnabled);
  return { enabled: flags.every((flag) => flag === flags[0]) ? (flags[0] ?? null) : null };
}

/** What a detected instance offers (legacy Emulators panel rules). */
export interface InstanceActions {
  /** Running with an address no configured instance uses: Use this one. */
  readonly use: boolean;
  /** Its address is configured: In use (a status, not a button). */
  readonly inUse: boolean;
  /** Stopped and launchable: Start it. */
  readonly start: boolean;
  /** Running, launchable and the active instance's configured address: Open The Tower. */
  readonly openGame: boolean;
  /** A stopped BlueStacks instance whose ADB switch is off or display not ready: Prepare. */
  readonly prepare: boolean;
  /** Prepare remains visible but cannot write while any HD-Player is running. */
  readonly prepareBlocked: 'bluestacks_running' | null;
}

export function instanceActions(view: InventoryView, row: DetectedInstance): InstanceActions {
  const active = activeConfigured(view)?.serial ?? null;
  const used = row.serial !== null && inUse(view, row.serial);
  const stopped = row.running === false;
  return {
    use: row.running === true && row.serial !== null && !used,
    inUse: used,
    start: stopped && launchable(row),
    openGame:
      row.running === true && launchable(row) && row.serial !== null && row.serial === active,
    prepare:
      stopped &&
      row.index !== null &&
      isBlueStacks(row.emulator) &&
      (row.adbEnabled === false || row.displayOk === false),
    prepareBlocked:
      view.blueStacksRunning ||
      view.instances.some(
        (instance) => isBlueStacks(instance.emulator) && instance.running === true,
      )
        ? 'bluestacks_running'
        : null,
  };
}

// ------------------------------------------------------------------ requests

/** An adopt as the page sends it: the address, its family adb, and the scan's guards. */
export interface AdoptRequest {
  readonly serial: string;
  readonly adb: string | null;
  /** The active instance the scan named: the server refuses when the configuration changed. */
  readonly expectInstance: string;
  /** The scan saw an empty address: the server only fills an empty one. */
  readonly expectPlaceholder: boolean;
}

/** The adopt body: `{serial, adb?, expect_instance, expect_placeholder?: true}`. */
export function adoptBody(request: AdoptRequest): Readonly<Record<string, unknown>> {
  return {
    serial: request.serial,
    ...(request.adb === null ? {} : { adb: request.adb }),
    expect_instance: request.expectInstance,
    ...(request.expectPlaceholder ? { expect_placeholder: true } : {}),
  };
}

/** A launch body: `{emulator: "MuMu" | "BlueStacks", index}` (a MuMu index is a number). */
export function launchBody(
  emulator: string,
  index: string,
): Readonly<Record<string, unknown>> | null {
  const family = LAUNCHABLE.find((name) => emulator.startsWith(name));
  if (family === undefined) return null;
  if (family === 'MuMu') {
    const number = Number(index);
    return Number.isInteger(number) && /^\d+$/u.test(index)
      ? { emulator: family, index: number }
      : null;
  }
  return { emulator: family, index };
}

// ------------------------------------------------------------------ answers

type Answer = { readonly status: number; readonly body: unknown };

/** A JSON object refusal (4xx): the server answered that it did not do this request. */
function refusal(answer: Answer) {
  if (answer.status < 400 || answer.status > 499 || record(answer.body) === undefined) return null;
  if (own(answer.body, 'ok') === true) return null;
  return {
    kind: 'refused' as const,
    status: answer.status,
    code: id(own(answer.body, 'code')) ?? null,
    error: text(own(answer.body, 'error')) ?? text(own(answer.body, 'message')) ?? null,
  };
}

type Refused = NonNullable<ReturnType<typeof refusal>>;

/**
 * The server's own words on an answer that proves nothing (review F6M-09): a 5xx JSON body
 * such as Prepare's `cannot write bluestacks.conf: ...`. Shown beside the Unknown outcome,
 * never read as a result: the request may have been done in part.
 */
export function serverWords(answer: Answer): string | null {
  if (record(answer.body) === undefined) return null;
  return text(own(answer.body, 'error')) ?? text(own(answer.body, 'message')) ?? null;
}

/**
 * The requests that change which emulator Tower Pilot drives or its connection: one at a
 * time (review F6M-03), each may change what the next one acts on.
 */
export const CONNECTION_WRITES: readonly string[] = [
  'adopt',
  'launch',
  'prepare',
  'tools_install',
  'reconnect',
];

/**
 * A first install (review F6M-01): the active instance names no address yet
 * (backend/config.example.yaml ships `serial: ""`), so there is no page context, which
 * needs one. Connection setup acts on the instance itself; null when the config read does
 * not name an active instance whose address is exactly empty.
 */
export function unconfiguredInstance(configBody: unknown): {
  readonly instance: string;
  readonly account: string | null;
  readonly profile: string | null;
} | null {
  const instance = id(own(configBody, 'active_instance'));
  if (instance === undefined) return null;
  if (ownPath(configBody, 'instances', instance, 'serial') !== '') return null;
  return {
    instance,
    account: id(ownPath(configBody, 'instances', instance, 'account')) ?? null,
    profile: id(own(configBody, 'active_profile')) ?? null,
  };
}

export type AdoptAnswer =
  /** Saved in the configuration; `connected` is the server's own connect attempt, separately. */
  | {
      readonly kind: 'saved';
      readonly connected: boolean | null;
      readonly instance: string | null;
      readonly message: string | null;
      readonly backup: string | null;
    }
  | Refused
  | { readonly kind: 'unknown' };

export function adoptAnswer(
  answer: Answer,
  sent: { readonly serial?: string | undefined },
): AdoptAnswer {
  if (
    answer.status === 200 &&
    own(answer.body, 'ok') === true &&
    sent.serial !== undefined &&
    own(answer.body, 'serial') === sent.serial
  ) {
    const connected = own(answer.body, 'connected');
    return {
      kind: 'saved',
      connected: typeof connected === 'boolean' ? connected : null,
      instance: id(own(answer.body, 'instance')) ?? null,
      message: text(own(answer.body, 'message')) ?? null,
      backup: text(own(answer.body, 'backup')) ?? null,
    };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}

/**
 * What a later config read says about an adopt whose answer was lost: `applied` when the
 * instance's address is the sent one; null otherwise. A configuration still at the revision
 * the adopt was made from never proves it failed: the request may still be running and
 * commit later (the rule of Collection review F6C-R1-02), so it stays unknown.
 */
export function settleAdopt(
  configBody: unknown,
  sent: {
    readonly instance?: string | undefined;
    readonly serial?: string | undefined;
  },
): 'applied' | null {
  if (sent.instance === undefined || sent.serial === undefined) return null;
  const instances = own(configBody, 'instances');
  if (record(instances) === undefined) return null;
  return ownPath(instances, sent.instance, 'serial') === sent.serial ? 'applied' : null;
}

export type LaunchAnswer =
  /** The route started the emulator manager (not a recognized game screen). */
  | { readonly kind: 'accepted'; readonly message: string | null }
  /** HTTP 200 with `ok: false`: the manager reported a failure. */
  | { readonly kind: 'failed'; readonly message: string | null }
  | Refused
  | { readonly kind: 'unknown' };

export function launchAnswer(answer: Answer): LaunchAnswer {
  if (answer.status === 200 && record(answer.body) !== undefined) {
    const ok = own(answer.body, 'ok');
    const message = text(own(answer.body, 'message')) ?? text(own(answer.body, 'error')) ?? null;
    if (ok === true) return { kind: 'accepted', message };
    if (ok === false) return { kind: 'failed', message };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}

export interface PrepareChange {
  readonly key: string;
  readonly from: string | null;
  readonly to: string | null;
}

export type PrepareAnswer =
  | {
      readonly kind: 'done';
      readonly changed: readonly PrepareChange[];
      readonly added: readonly string[];
      /** The backup of bluestacks.conf the server made, or null when nothing was written. */
      readonly backup: string | null;
      readonly message: string | null;
    }
  | Refused
  | { readonly kind: 'unknown' };

export function prepareAnswer(answer: Answer): PrepareAnswer {
  if (answer.status === 200 && own(answer.body, 'ok') === true) {
    const changed = own(answer.body, 'changed');
    return {
      kind: 'done',
      changed: Object.keys(record(changed) ?? {}).map((key) => ({
        key,
        from: text(ownPath(changed, key, 'from')) ?? null,
        to: text(ownPath(changed, key, 'to')) ?? null,
      })),
      added: stringList(own(answer.body, 'added')) ?? [],
      backup: text(own(answer.body, 'backup')) ?? null,
      message: text(own(answer.body, 'message')) ?? null,
    };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}

export type InstallAnswer =
  /** 202: the download started; 200: one was already running (answered, not restarted). */
  | { readonly kind: 'accepted'; readonly started: boolean; readonly stage: string | null }
  | Refused
  | { readonly kind: 'unknown' };

export function installAnswer(answer: Answer): InstallAnswer {
  if ((answer.status === 202 || answer.status === 200) && own(answer.body, 'ok') === true)
    return {
      kind: 'accepted',
      started: answer.status === 202,
      stage: id(own(answer.body, 'stage')) ?? null,
    };
  return refusal(answer) ?? { kind: 'unknown' };
}

export interface ToolsView {
  readonly stage: string | null;
  readonly message: string | null;
  readonly percent: number | null;
  readonly installed: boolean;
  readonly path: string | null;
  /** A download or install is running (completion only comes from a later read). */
  readonly inProgress: boolean;
}

const TOOLS_SETTLED = ['idle', 'error', 'ready'];

export function toolsView(body: unknown): ToolsView | null {
  if (record(body) === undefined || own(body, 'installed') === undefined) return null;
  const stage = id(own(body, 'stage')) ?? null;
  const installed = own(body, 'installed') === true;
  return {
    stage,
    message: text(own(body, 'message')) ?? null,
    percent: finite(own(body, 'percent')) ?? null,
    installed,
    path: text(own(body, 'path')) ?? null,
    inProgress: !installed && stage !== null && !TOOLS_SETTLED.includes(stage),
  };
}

/**
 * A later tools read about an install whose answer was lost: running or done means it was
 * taken. Idle proves nothing (the request may still start the download), so it stays unknown.
 */
export function settleInstall(toolsBody: unknown): 'applied' | null {
  const view = toolsView(toolsBody);
  if (view === null) return null;
  return view.installed || view.inProgress ? 'applied' : null;
}

export type ResolutionView =
  | {
      readonly kind: 'measured';
      readonly serial: string | null;
      readonly width: number;
      readonly height: number;
      /** Portrait, as the game needs it. */
      readonly portrait: boolean;
      /** Exactly the 1080 x 2560 reference layout. */
      readonly reference: boolean;
      readonly note: string | null;
      readonly at: number | null;
    }
  | {
      readonly kind: 'failed';
      readonly serial: string | null;
      readonly error: string | null;
      readonly at: number | null;
    };

/** A resolution check (`GET /api/wizard/resolution`, or the saved wizard section), else null. */
export function resolutionView(body: unknown): ResolutionView | null {
  const serial = text(own(body, 'serial')) ?? null;
  const at = finite(own(body, 't')) ?? null;
  if (own(body, 'ok') === false)
    return { kind: 'failed', serial, error: text(own(body, 'error')) ?? null, at };
  const width = finite(own(body, 'width'));
  const height = finite(own(body, 'height'));
  if (own(body, 'ok') !== true || width === undefined || height === undefined) return null;
  return {
    kind: 'measured',
    serial,
    width,
    height,
    portrait: own(body, 'expected') === true,
    reference: own(body, 'reference') === true,
    note: text(own(body, 'note')) ?? null,
    at,
  };
}

export type ReconnectAnswer =
  /** Answered: the device state the adb server reported for the address after connecting. */
  | {
      readonly kind: 'answered';
      readonly online: boolean;
      readonly deviceState: string | null;
      readonly message: string | null;
    }
  | Refused
  | { readonly kind: 'unknown' };

/** `POST /api/wizard/reconnect`: `ok` is the socket-observed `device` state, not the connect message. */
export function reconnectAnswer(answer: Answer): ReconnectAnswer {
  if (answer.status === 200 && record(answer.body) !== undefined) {
    const ok = own(answer.body, 'ok');
    if (typeof ok === 'boolean')
      return {
        kind: 'answered',
        online: ok && own(answer.body, 'device_state') === 'device',
        deviceState: id(own(answer.body, 'device_state')) ?? null,
        message: text(own(answer.body, 'message')) ?? null,
      };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}
