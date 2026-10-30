import type { PreviewApi } from '../api/preview.ts';
import {
  previewAvailability,
  previewPreferences,
  previewReport,
  previewStreamId,
  previewUrl,
  type PreviewAvailability,
  type PreviewPreferences,
  type PreviewReport,
  type DisplayedPreview,
} from '../core/preview.ts';
import { createStore, type Store } from './store.ts';

export interface PreviewTarget {
  readonly epoch: number;
  readonly key: string | null;
  readonly instance: string | null;
  readonly connected: boolean;
  readonly hidden: boolean;
}
export interface PreviewState {
  readonly target: PreviewTarget;
  readonly prefs: PreviewPreferences;
  readonly expanded: boolean;
  readonly running: boolean;
  readonly checking: boolean;
  readonly availability: PreviewAvailability | null;
  readonly availabilityFailed: boolean;
  readonly preferenceFailed: boolean;
  readonly streamId: string | null;
  readonly fps: number | null;
  readonly frame: DisplayedPreview | null;
  readonly report: PreviewReport | null;
  readonly reportAt: number | null;
  readonly statusFailed: boolean;
  readonly failed: boolean;
  /** The person pressed Stop this page session: nothing starts by itself until Start or Reconnect. */
  readonly stopped: boolean;
  readonly now: number;
}
export interface PreviewStore extends Store<PreviewState> {
  readonly setTarget: (value: PreviewTarget) => void;
  readonly expand: (value: boolean) => void;
  readonly start: () => Promise<void>;
  readonly check: () => Promise<void>;
  /** The person's Stop: the session ends and stays off until the person starts it again. */
  readonly stop: () => void;
  /** Viewer mounted or unmounted. Unmounting ends the session without recording a choice. */
  readonly present: (value: boolean) => void;
  readonly edit: (value: PreviewPreferences) => void;
  readonly attach: (canvas: HTMLCanvasElement | null) => void;
}
/** Wait before reconnecting after a failed session: 3 s, doubling per failure, capped. */
const RETRY_BASE_MS = 3000;
const RETRY_MAX_MS = 60000;

export function createPreviewStore(api: PreviewApi): PreviewStore {
  const store = createStore<PreviewState>({
    target: { epoch: 0, key: null, instance: null, connected: false, hidden: true },
    prefs: previewPreferences(api.load()),
    expanded: true,
    running: false,
    checking: false,
    availability: null,
    availabilityFailed: false,
    preferenceFailed: false,
    streamId: null,
    fps: null,
    frame: null,
    report: null,
    reportAt: null,
    statusFailed: false,
    failed: false,
    stopped: false,
    now: api.now(),
  });
  let generation = 0;
  let controller: AbortController | null = null;
  let closed: Promise<void> = Promise.resolve();
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let tickTimer: ReturnType<typeof setTimeout> | null = null;
  // Auto-attach bookkeeping, in memory only: the person's Stop lasts for this page session.
  let viewing = false;
  let personStopped = false;
  let dead = false;
  let failures = 0;
  let retryAt = 0;
  const noteFailure = () => {
    failures++;
    dead = true;
    retryAt = api.now() + Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** (failures - 1));
  };
  const halt = () => {
    generation++;
    dead = false;
    controller?.abort();
    controller = null;
    if (pollTimer !== null) clearTimeout(pollTimer);
    if (tickTimer !== null) clearTimeout(tickTimer);
    pollTimer = null;
    tickTimer = null;
    api.clear();
    store.update((s) => ({
      ...s,
      running: false,
      checking: false,
      streamId: null,
      frame: null,
      report: null,
      reportAt: null,
      failed: false,
      statusFailed: false,
    }));
  };
  const active = (token: number, id?: string) =>
    generation === token &&
    !controller?.signal.aborted &&
    (id === undefined || store.getSnapshot().streamId === id);
  const availability = async (token: number, request: AbortController) => {
    const signal = request.signal;
    const timeout = setTimeout(() => {
      request.abort();
    }, 8000);
    const cancelTimeout = () => {
      clearTimeout(timeout);
    };
    signal.addEventListener('abort', cancelTimeout, { once: true });
    try {
      const raw = await api.availability(signal);
      if (!active(token) || signal.aborted) return null;
      const value = previewAvailability(raw);
      if (value.instance !== store.getSnapshot().target.instance && value.ok) {
        store.update((s) => ({ ...s, availability: null, availabilityFailed: true }));
        return null;
      }
      store.update((s) => ({ ...s, availability: value, availabilityFailed: !value.ok }));
      return value.ok && value.fps !== null ? value : null;
    } catch {
      if (generation === token) store.update((s) => ({ ...s, availabilityFailed: true }));
      return null;
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener('abort', cancelTimeout);
    }
  };
  const run = async () => {
    halt();
    const initial = store.getSnapshot();
    if (
      !initial.expanded ||
      initial.target.hidden ||
      !initial.target.connected ||
      initial.target.instance === null
    )
      return;
    const token = generation;
    const abort = new AbortController();
    const cancelled = () => abort.signal.aborted;
    controller = abort;
    store.update((s) => ({ ...s, running: true, checking: true }));
    await closed;
    if (!active(token) || cancelled()) return;
    const available = await availability(token, abort);
    if (!active(token) || cancelled()) {
      if (generation === token) {
        noteFailure();
        store.update((s) => ({ ...s, checking: false, failed: true }));
      }
      return;
    }
    if (available === null || !store.getSnapshot().target.connected) {
      noteFailure();
      store.update((s) => ({ ...s, checking: false, failed: true }));
      return;
    }
    const prefs = store.getSnapshot().prefs;
    const fps = prefs.fps ?? available.fps;
    if (fps === null) return;
    const id = previewStreamId(api.newId());
    store.update((s) => ({ ...s, streamId: id, fps, checking: false }));
    let sequence = 0;
    const fail = (reason?: 'unknown') => {
      if (!active(token, id)) return;
      noteFailure();
      abort.abort();
      api.clear();
      if (pollTimer !== null) clearTimeout(pollTimer);
      store.update((s) => ({
        ...s,
        failed: reason !== 'unknown',
        statusFailed: reason === 'unknown',
        frame: null,
      }));
    };
    // Store the close promise before another session can start. Abort cancels pending decode too.
    closed = api
      .stream(
        previewUrl(initial.target.instance, id, fps, prefs),
        abort.signal,
        (frame) => {
          if (!active(token, id) || cancelled()) return;
          if (frame.kind === 'frame' && frame.sequence <= sequence) return;
          if (frame.kind === 'frame') {
            sequence = frame.sequence;
            failures = 0;
            retryAt = 0;
          }
          store.update((s) => ({ ...s, frame, now: api.now() }));
        },
        fail,
      )
      .then(
        () => {
          if (!cancelled()) fail();
        },
        () => {
          if (!cancelled()) fail();
        },
      );
    const poll = async () => {
      if (!active(token, id) || cancelled()) return;
      const request = new AbortController();
      const cancel = () => {
        request.abort();
      };
      abort.signal.addEventListener('abort', cancel, { once: true });
      const timeout = setTimeout(cancel, 8000);
      try {
        const report = previewReport(await api.status(id, request.signal));
        if (!active(token, id) || cancelled()) return;
        if (report.streamId !== id || report.instance !== initial.target.instance) {
          store.update((s) => ({ ...s, statusFailed: true }));
        } else
          store.update((s) => ({
            ...s,
            report,
            reportAt: api.now(),
            statusFailed: false,
            now: api.now(),
          }));
      } catch {
        if (active(token, id) && !cancelled())
          store.update((s) => ({ ...s, statusFailed: true, now: api.now() }));
      } finally {
        clearTimeout(timeout);
        abort.signal.removeEventListener('abort', cancel);
        if (active(token, id) && !cancelled())
          pollTimer = setTimeout(() => {
            void poll();
          }, 2000);
      }
    };
    const tick = () => {
      if (!active(token, id) || cancelled()) return;
      store.update((s) => ({ ...s, now: api.now() }));
      tickTimer = setTimeout(tick, 250);
    };
    pollTimer = setTimeout(() => {
      void poll();
    }, 2000);
    tick();
  };
  const wanted = () => {
    const s = store.getSnapshot();
    return (
      viewing &&
      s.expanded &&
      !personStopped &&
      s.target.connected &&
      !s.target.hidden &&
      s.target.instance !== null
    );
  };
  /**
   * Attach when the emulator is already online and nothing the person chose says otherwise.
   * Runs on dashboard reads (the existing poll cadence), never on its own timer: a failed
   * session is retried at most once per backoff window, and only while the device reads online.
   */
  const reconcile = () => {
    if (!wanted()) return;
    const s = store.getSnapshot();
    if (s.checking && !s.running) return;
    // The backoff deadline holds across hide/show, unmount/remount and collapse/expand too.
    if (api.now() < retryAt) return;
    if (!s.running || dead) void run();
  };
  return {
    ...store,
    start() {
      personStopped = false;
      store.update((s) => ({ ...s, stopped: false }));
      failures = 0;
      retryAt = 0;
      return run();
    },
    stop() {
      personStopped = true;
      halt();
      store.update((s) => ({ ...s, stopped: true }));
    },
    present(value) {
      viewing = value;
      if (value) reconcile();
      else halt();
    },
    attach: api.attach,
    setTarget(target) {
      const previous = store.getSnapshot().target;
      if (
        target.key !== previous.key ||
        target.epoch !== previous.epoch ||
        (target.hidden && !previous.hidden) ||
        (!target.connected && previous.connected)
      )
        halt();
      if (target.connected && !previous.connected) {
        failures = 0;
        retryAt = 0;
      }
      store.update((s) => ({
        ...s,
        target,
        ...(target.key !== previous.key || target.epoch !== previous.epoch
          ? { availability: null, availabilityFailed: false }
          : {}),
      }));
      reconcile();
    },
    expand(expanded) {
      if (!expanded) halt();
      store.update((s) => ({ ...s, expanded }));
      reconcile();
    },
    async check() {
      if (store.getSnapshot().checking) return;
      // Checking prerequisites while stopped never opens a capture session.
      if (store.getSnapshot().running) return;
      halt();
      const token = generation;
      const request = new AbortController();
      controller = request;
      store.update((s) => ({ ...s, checking: true }));
      await availability(token, request);
      if (generation === token) store.update((s) => ({ ...s, checking: false }));
    },
    edit(prefs) {
      const running = store.getSnapshot().running;
      const preferenceFailed = !api.save(prefs);
      store.update((s) => ({ ...s, prefs, preferenceFailed }));
      if (running) void run();
    },
  };
}
