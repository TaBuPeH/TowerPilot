import { readEndpoint, type ReadOutcome } from './client.ts';
import type { Sequence } from './sequence.ts';
import { browserTimers, type TimerHandle, type Timers } from './timers.ts';
import type { ApiPath, Transport } from './transport.ts';
import { ENDPOINTS, type Endpoint, type EndpointId, type ReadEndpointId } from './wire.ts';

/**
 * The reads of spec 7.2, one hand-written poller (ADR decision: no fetching
 * library, because automatic retries conflict with the no-resend rule; only
 * GETs are ever repeated here, never a write).
 *
 *  - Single flight per resource: a read never starts while another read of
 *    the same resource is in flight. A forced read (Retry status, Check
 *    status, a visible tab, an answer that needs a later read) bypasses the
 *    due time and the backoff, but not single flight: during a flight it is
 *    queued once and starts as soon as the flight ends, so it is a read
 *    STARTED after the event that asked for it.
 *  - Cadence per resource and mode (active, idle, off) from the store's plan,
 *    measured from the end of the previous read with the browser clock.
 *  - Hidden tab: status and calibration slow to 10 s, the other periodic
 *    reads stop, the one-second clock pauses; becoming visible forces one read
 *    of every resource marked `onVisible`.
 *  - Failure backoff `min(30000, 500 * 2^min(6, n))` (first retry 1000 ms) or
 *    `min(60000, 1000 * 2^min(6, n))` (first 2000 ms); reads marked `none` are
 *    not retried (one queued refresh at a time).
 *  - Every GET is aborted after 5000 ms (`AbortController`): a timed-out read
 *    is a failed read.
 *  - Context fence (S-014): each read records the epoch it started in; an
 *    answer from an older epoch is dropped and never delivered. A context
 *    change aborts every flight and reads every resource again.
 */

export type PolledResource =
  | 'status'
  | 'calibrate'
  | 'calibrateOperation'
  | 'battlesActive'
  | 'schedule'
  | 'battles'
  | 'config'
  | 'accounts'
  | 'runs'
  | 'wizardTools'
  | 'wizardState'
  | 'wizardEmulators'
  | 'runControls'
  | 'profileFiles'
  | 'profileSource'
  | 'targetSource'
  | 'settings'
  | 'vocab'
  | 'runTemplates';

export const POLLED_RESOURCES: readonly PolledResource[] = [
  'status',
  'calibrate',
  'calibrateOperation',
  'battlesActive',
  'schedule',
  'battles',
  'config',
  'accounts',
  'runs',
  'wizardTools',
  'wizardState',
  'wizardEmulators',
  'runControls',
  'profileFiles',
  'profileSource',
  'targetSource',
  'settings',
  'vocab',
  'runTemplates',
];

export type Backoff = 'status' | 'schedule' | 'none';

export interface Policy {
  readonly endpoint: ReadEndpointId;
  /** Interval while the plan says active; null: no periodic read in that mode. */
  readonly activeMs: number | null;
  readonly idleMs: number | null;
  /** Interval while the tab is hidden; null: no periodic read while hidden. */
  readonly hiddenMs: number | null;
  readonly backoff: Backoff;
  /** A tab becoming visible forces one read. */
  readonly onVisible: boolean;
}

/** Spec 7.2, row by row. */
export const POLICIES: Readonly<Record<PolledResource, Policy>> = Object.freeze({
  status: {
    endpoint: 'status',
    activeMs: 500,
    idleMs: 2500,
    hiddenMs: 10000,
    backoff: 'status',
    onVisible: true,
  },
  calibrate: {
    endpoint: 'calibrate',
    activeMs: 500,
    idleMs: 2500,
    hiddenMs: 10000,
    backoff: 'status',
    onVisible: true,
  },
  battlesActive: {
    endpoint: 'battlesActive',
    activeMs: 500,
    idleMs: 2500,
    hiddenMs: null,
    backoff: 'status',
    onVisible: true,
  },
  schedule: {
    endpoint: 'schedule',
    activeMs: 15000,
    idleMs: null,
    hiddenMs: null,
    backoff: 'schedule',
    onVisible: false,
  },
  battles: {
    endpoint: 'battles',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'schedule',
    onVisible: false,
  },
  config: {
    endpoint: 'config',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  accounts: {
    endpoint: 'accounts',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  runs: {
    endpoint: 'runs',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  // Recognition page (R1, R2, R8): the operation this page follows, by its id, while it works.
  calibrateOperation: {
    endpoint: 'calibrate',
    activeMs: 1000,
    idleMs: null,
    hiddenMs: 10000,
    backoff: 'none',
    onVisible: true,
  },
  wizardTools: {
    endpoint: 'wizardTools',
    // M5: followed while the Emulators page is open and an install runs (readPlan `active`).
    activeMs: 1500,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  wizardState: {
    endpoint: 'wizardState',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  wizardEmulators: {
    endpoint: 'wizardEmulators',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: false,
  },
  runControls: {
    endpoint: 'runControls',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: false,
  },
  // The Profiles page (profiles brief P1, P5): read while it is open, on its events.
  profileFiles: {
    endpoint: 'profileFiles',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  profileSource: {
    endpoint: 'profileSource',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  targetSource: {
    endpoint: 'profileSource',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  // The Collection page (collection brief C1 to C6): read while it is open, on its events.
  settings: {
    endpoint: 'settings',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: true,
  },
  vocab: {
    endpoint: 'vocab',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: false,
  },
  // C5: the run templates (the shipped default gathering policies), read when Collection opens.
  runTemplates: {
    endpoint: 'runTemplates',
    activeMs: null,
    idleMs: null,
    hiddenMs: null,
    backoff: 'none',
    onVisible: false,
  },
});

/** The failure backoff after `failures` consecutive failed reads. */
export function backoffMs(backoff: Backoff, failures: number): number | null {
  const n = Math.min(6, Math.max(1, failures));
  switch (backoff) {
    case 'status':
      return Math.min(30000, 500 * 2 ** n);
    case 'schedule':
      return Math.min(60000, 1000 * 2 ** n);
    default:
      return null;
  }
}

export const GET_TIMEOUT_MS = 5000;
export const CLOCK_MS = 1000;

/** The editing selection carried only by targetSource reads. */
export interface TargetSourceSelection {
  readonly name: string;
  readonly generation: number;
}

/** How a resource should be read now: its path (null: nothing to read) and its mode. */
export interface ReadPlan {
  readonly path: ApiPath | null;
  readonly mode: 'active' | 'idle' | 'off';
  /** Only targetSource: the editing selection asking for this read. */
  readonly targetSelection?: TargetSourceSelection;
  /**
   * A failure backoff the plan asks for now, for a resource whose policy has none (
one):
   * a failed read is read again on it until a read answers (only GETs, never a write).
   */
  readonly retry?: Exclude<Backoff, 'none'>;
}

export interface Visibility {
  readonly hidden: () => boolean;
  readonly subscribe: (listener: () => void) => () => void;
}

/** The document's visibility (the Page Visibility API). */
export function documentVisibility(): Visibility {
  return {
    hidden: () => document.visibilityState === 'hidden',
    subscribe: (listener) => {
      document.addEventListener('visibilitychange', listener);
      return () => {
        document.removeEventListener('visibilitychange', listener);
      };
    },
  };
}

/** One finished read of the current epoch, as the store keeps it. */
export interface ReadArrival {
  readonly resource: PolledResource;
  readonly path: ApiPath;
  /** Only targetSource: captured at start, never inferred from the selection at arrival. */
  readonly targetSelection?: TargetSourceSelection;
  /** The epoch the read was started (and answered) in. */
  readonly epoch: number;
  readonly startedSeq: number;
  readonly arrivedSeq: number;
  /** Browser clock, ms; only intervals between two of them are ever used. */
  readonly startedAtMs: number;
  readonly arrivedAtMs: number;
  readonly outcome:
    | {
        readonly kind: 'answered';
        readonly status: number;
        readonly body: unknown;
        readonly contract: 'exact' | 'drift';
        readonly issues: readonly string[];
      }
    | {
        readonly kind: 'failed';
        /** `timeout`, `aborted`, `network`, `http_<status>`, `body_not_json`, `body_not_object`. */
        readonly reason: string;
        readonly status: number | null;
        /** Retained failure answer when one arrived; absent after network loss. */
        readonly body?: unknown;
      };
}

export interface PollerOptions {
  readonly transport: Transport;
  readonly sequence: Sequence;
  /** The current context epoch (api/context.ts). */
  readonly epoch: () => number;
  /** The store's read plan per resource (path and mode). */
  readonly plan: (resource: PolledResource) => ReadPlan;
  /** Receives every read of the current epoch, answered or failed. */
  readonly deliver: (arrival: ReadArrival) => void;
  readonly endpoints?: Readonly<Record<EndpointId, Endpoint>>;
  readonly timers?: Timers;
  readonly visibility?: Visibility;
  readonly timeoutMs?: number;
  /** The one-second store clock (F-112): paused while the tab is hidden. */
  readonly onClock?: () => void;
  /** The visibility changed (the store keeps it for the read plan). */
  readonly onVisibility?: (hidden: boolean) => void;
}

export interface Poller {
  /** Entry: read every resource whose plan names a path, then keep the cadences. */
  readonly start: () => void;
  /** Stop every timer and abort every flight; nothing is delivered after this. */
  readonly stop: () => void;
  /** A forced read: bypasses the due time and the backoff, queued once behind a flight. */
  readonly read: (resource: PolledResource) => void;
  /** The plan may have changed: recompute every resource's next read. */
  readonly replan: () => void;
  /** A new context epoch began: abort every flight, reset backoff, read everything again. */
  readonly contextChanged: () => void;
  readonly inFlight: (resource: PolledResource) => boolean;
  /**
   * A forced read whose result the caller awaits (a click's own read, INV-1): it joins the
   * resource's flight or starts one (single flight), and resolves with the delivered read,
   * or null when nothing was delivered (no path, stopped, dropped by the context fence).
   */
  readonly readNow: (resource: PolledResource) => Promise<ReadArrival | null>;
}

interface Flight {
  readonly controller: AbortController;
  readonly epoch: number;
  readonly startedSeq: number;
  readonly startedAtMs: number;
  readonly path: ApiPath;
  readonly targetSelection?: TargetSourceSelection;
  timedOut: boolean;
  timeout: TimerHandle | null;
}

interface Slot {
  flight: Flight | null;
  timer: TimerHandle | null;
  failures: number;
  again: boolean;
  lastEndMs: number | null;
}

export function createPoller(options: PollerOptions): Poller {
  const endpoints = options.endpoints ?? ENDPOINTS;
  const timers = options.timers ?? browserTimers;
  const visibility = options.visibility ?? null;
  const timeoutMs = options.timeoutMs ?? GET_TIMEOUT_MS;
  const slots = new Map<PolledResource, Slot>(
    POLLED_RESOURCES.map((resource) => [
      resource,
      { flight: null, timer: null, failures: 0, again: false, lastEndMs: null },
    ]),
  );
  let running = false;
  let clock: TimerHandle | null = null;
  let unsubscribe: (() => void) | null = null;
  const waiters = new Map<PolledResource, ((arrival: ReadArrival | null) => void)[]>();

  const release = (resource: PolledResource, arrival: ReadArrival | null) => {
    const waiting = waiters.get(resource) ?? [];
    waiters.delete(resource);
    for (const resolve of waiting) resolve(arrival);
  };

  const slotOf = (resource: PolledResource): Slot => {
    const slot = slots.get(resource);
    if (slot === undefined) throw new Error(`api/poller: unknown resource ${resource}`);
    return slot;
  };
  const hidden = (): boolean => visibility?.hidden() === true;

  const clearTimer = (slot: Slot) => {
    if (slot.timer !== null) {
      timers.clearTimeout(slot.timer);
      slot.timer = null;
    }
  };

  const schedule = (resource: PolledResource) => {
    const slot = slotOf(resource);
    clearTimer(slot);
    if (!running || slot.flight !== null) return;
    const policy = POLICIES[resource];
    const plan = options.plan(resource);
    if (plan.path === null) return;
    if (slot.lastEndMs === null) {
      // Never read in this epoch (its path just became known, e.g. the schedule once the
      // context names the instance): the entry read happens now, whatever the mode.
      begin(resource);
      return;
    }
    let delay: number | null =
      plan.mode === 'active' ? policy.activeMs : plan.mode === 'idle' ? policy.idleMs : null;
    const backoff = policy.backoff === 'none' ? (plan.retry ?? 'none') : policy.backoff;
    if (slot.failures > 0 && backoff !== 'none') {
      delay = backoffMs(backoff, slot.failures);
    }
    if (delay === null) return;
    if (hidden()) {
      if (policy.hiddenMs === null) return;
      delay = Math.max(delay, policy.hiddenMs);
    }
    const now = timers.now();
    const due = slot.lastEndMs + delay;
    slot.timer = timers.setTimeout(
      () => {
        slot.timer = null;
        begin(resource);
      },
      Math.max(0, due - now),
    );
  };

  const finish = (resource: PolledResource, flight: Flight, outcome: ReadOutcome) => {
    if (flight.timeout !== null) timers.clearTimeout(flight.timeout);
    const slot = slotOf(resource);
    if (slot.flight !== flight) return; // aborted by stop() or a context change
    slot.flight = null;
    slot.lastEndMs = timers.now();
    let delivered: ReadArrival | null = null;
    try {
      if (running && flight.epoch === options.epoch()) {
        slot.failures = outcome.kind === 'failed' ? slot.failures + 1 : 0;
        const arrivedSeq = options.sequence.next();
        delivered = {
          resource,
          path: flight.path,
          ...(flight.targetSelection === undefined
            ? {}
            : { targetSelection: flight.targetSelection }),
          epoch: flight.epoch,
          startedSeq: flight.startedSeq,
          arrivedSeq,
          startedAtMs: flight.startedAtMs,
          arrivedAtMs: slot.lastEndMs,
          outcome:
            outcome.kind === 'answered'
              ? {
                  kind: 'answered',
                  status: outcome.answer.status,
                  body: outcome.answer.body,
                  contract: outcome.answer.contract,
                  issues: outcome.answer.issues,
                }
              : {
                  kind: 'failed',
                  reason: flight.timedOut ? 'timeout' : outcome.reason,
                  status: outcome.answer?.status ?? null,
                  ...(outcome.answer?.body === undefined ? {} : { body: outcome.answer.body }),
                },
        };
        options.deliver(delivered);
      }
    } finally {
      release(resource, delivered);
      if (slot.again) {
        slot.again = false;
        begin(resource);
      } else {
        schedule(resource);
      }
    }
  };

  const begin = (resource: PolledResource) => {
    if (!running) return;
    const slot = slotOf(resource);
    if (slot.flight !== null) {
      slot.again = true;
      return;
    }
    clearTimer(slot);
    const plan = options.plan(resource);
    if (plan.path === null) return;
    const controller = new AbortController();
    const flight: Flight = {
      controller,
      epoch: options.epoch(),
      startedSeq: options.sequence.next(),
      startedAtMs: timers.now(),
      path: plan.path,
      ...(resource === 'targetSource' && plan.targetSelection !== undefined
        ? { targetSelection: { ...plan.targetSelection } }
        : {}),
      timedOut: false,
      timeout: null,
    };
    flight.timeout = timers.setTimeout(() => {
      flight.timedOut = true;
      controller.abort();
    }, timeoutMs);
    slot.flight = flight;
    void readEndpoint(
      options.transport,
      endpoints[POLICIES[resource].endpoint],
      plan.path,
      controller.signal,
    ).then((outcome) => {
      finish(resource, flight, outcome);
    });
  };

  const abortFlight = (resource: PolledResource, slot: Slot) => {
    const flight = slot.flight;
    slot.flight = null;
    slot.again = false;
    if (flight === null) return;
    if (flight.timeout !== null) timers.clearTimeout(flight.timeout);
    flight.controller.abort();
    // Only the aborted flight's waiters learn that nothing was delivered. A context change
    // made inside `deliver` (finish already cleared the flight) leaves them to finish.
    release(resource, null);
  };

  const tick = () => {
    clock = null;
    if (!running || hidden()) return;
    options.onClock?.();
    clock = timers.setTimeout(tick, CLOCK_MS);
  };

  const startClock = () => {
    if (clock === null && options.onClock !== undefined && !hidden()) {
      clock = timers.setTimeout(tick, CLOCK_MS);
    }
  };

  const onVisibility = () => {
    if (!running) return;
    const isHidden = hidden();
    options.onVisibility?.(isHidden);
    if (isHidden) {
      if (clock !== null) {
        timers.clearTimeout(clock);
        clock = null;
      }
      for (const resource of POLLED_RESOURCES) schedule(resource);
      return;
    }
    startClock();
    for (const resource of POLLED_RESOURCES) {
      if (POLICIES[resource].onVisible) {
        begin(resource);
      } else {
        schedule(resource);
      }
    }
  };

  return {
    start: () => {
      if (running) return;
      running = true;
      unsubscribe = visibility?.subscribe(onVisibility) ?? null;
      startClock();
      for (const resource of POLLED_RESOURCES) begin(resource);
    },
    stop: () => {
      running = false;
      unsubscribe?.();
      unsubscribe = null;
      if (clock !== null) {
        timers.clearTimeout(clock);
        clock = null;
      }
      for (const [resource, slot] of slots) {
        clearTimer(slot);
        abortFlight(resource, slot);
        release(resource, null);
      }
    },
    read: (resource) => {
      begin(resource);
    },
    readNow: (resource) =>
      new Promise<ReadArrival | null>((resolve) => {
        if (!running || options.plan(resource).path === null) {
          resolve(null);
          return;
        }
        waiters.set(resource, [...(waiters.get(resource) ?? []), resolve]);
        if (slotOf(resource).flight === null) begin(resource);
      }),
    replan: () => {
      for (const resource of POLLED_RESOURCES) schedule(resource);
    },
    contextChanged: () => {
      for (const [resource, slot] of slots) {
        clearTimer(slot);
        abortFlight(resource, slot);
        slot.failures = 0;
        slot.lastEndMs = null;
      }
      for (const resource of POLLED_RESOURCES) begin(resource);
    },
    inFlight: (resource) => slotOf(resource).flight !== null,
  };
}
