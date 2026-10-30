import { clearTimeout as clearRealTimeout, setTimeout as setRealTimeout } from 'node:timers';
import { act, render, within, type RenderResult } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { afterEach, onTestFinished, vi, type Mock } from 'vitest';
import { App } from '../../../../src/app/App.tsx';
import type { DashboardCommands } from '../../../../src/store/commands.ts';
import { createDashboardStore, type DashboardStore } from '../../../../src/store/dashboardStore.ts';
import type { DashboardState } from '../../../../src/store/types.ts';
import {
  DashboardContext,
  type DashboardContextValue,
} from '../../../../src/features/dashboard/context.ts';
import { LiveRegionProvider } from '../../../../src/ui/LiveRegion.tsx';
import { bootDashboard, type BootOptions } from '../../../support/dataLayer.ts';

/** A labelled fact's value, scoped to its description-list term. */
export function factValue(container: HTMLElement, label: string): HTMLElement {
  const term = within(container).getByText(label, { selector: 'dt' });
  const value = term.nextElementSibling;
  if (!(value instanceof HTMLElement) || value.tagName !== 'DD')
    throw new Error(`Missing value for fact ${label}`);
  return value;
}

/**
 * Two ways to draw the Dashboard in tests (jsdom, no socket):
 *
 * - `renderPage`: the whole App on the real data layer (app/wiring.ts) over the
 *   fake transport, with fake timers: real reads, real commands, real POSTs
 *   recorded by the transport. For flows (a click sends exactly one POST,
 *   polls keep focus, fallbacks keep revocations).
 * - `renderRegion`: one component on a fixed DashboardState (built with
 *   tests/unit/store/regionHelpers.ts) and recording commands. For a region's
 *   decision rows.
 */

/** jsdom has no modal dialog; the native one is polyfilled where missing (focus, Escape, close event). */
export function installDialogPolyfill(): void {
  const proto = HTMLDialogElement.prototype;
  const probe = document.createElement('dialog');
  document.body.append(probe);
  try {
    probe.showModal();
    const works = probe.open;
    probe.close();
    probe.remove();
    if (works) return;
  } catch {
    probe.remove();
  }
  proto.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };
  proto.show = function show(this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };
  proto.close = function close(this: HTMLDialogElement) {
    if (!this.hasAttribute('open')) return;
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
}

/**
 * Testing Library drains effects with a 0 ms timer that it advances only for
 * Jest's fake timers (it looks for a `jest` global); Vitest's fake timers need
 * the same nudge, or every user-event call waits forever.
 */
function shimJestTimers(): void {
  const scope = globalThis as { jest?: unknown };
  scope.jest ??= {
    advanceTimersByTime: (ms: number) => {
      vi.advanceTimersByTime(ms);
    },
  };
}

export interface PageOptions extends BootOptions {
  /** Parts made to fail while drawing (X4 to X6); a Set may be changed later (a warm fault). */
  readonly faults?: readonly string[] | Set<string>;
}

export interface PageHarness extends ReturnType<typeof bootDashboard> {
  readonly view: RenderResult;
  readonly user: UserEvent;
  /** Advance fake time by `ms` (0: let answers and effects run) inside act. */
  readonly advance: (ms?: number) => Promise<void>;
}

/**
 * A page belongs to the test that drew it. When that test ends, passed, failed or
 * timed out, the page is drained before RTL cleanup: advances and user
 * events still in flight (with an act() scope open) are awaited. The page refuses
 * further advances and user events, then stops its data layer.
 * Otherwise the timed-out test keeps running beside the next one, their act()
 * scopes overlap, and React stops flushing updates for every later test in the file.
 * Teardown gets its own real-time bound, leaving time to stop data before the
 * hook's bound (TEARDOWN_MS, not a test timeout). An unsettled act cannot be cancelled.
 * A second renderPage that the orphaned body reaches is not torn down; its first
 * advance then fails on real timers.
 */
const TEARDOWN_MS = 60_000;
const DRAIN_MS = TEARDOWN_MS - 1_000;
const PAGE_ENDED =
  'renderPage: this page belongs to a test that has ended (timed out?); it no longer advances or takes input.';
const pageTeardowns = new Set<() => Promise<void>>();

// Vitest's stack ordering runs this before the earlier setup.ts RTL cleanup.
afterEach(async () => {
  // Finish every page even if one fails. Rethrow each cached failure from its
  // onTestFinished backstop so a drain failure cannot skip the other afterEach hooks.
  await Promise.allSettled([...pageTeardowns].map((finish) => finish()));
}, TEARDOWN_MS);

/** `user` that throws once `ended()` is true, before any event is dispatched. */
function guardUser(
  user: UserEvent,
  ended: () => boolean,
  track: <T>(step: Promise<T>) => Promise<T>,
): UserEvent {
  return new Proxy(user, {
    get(target, key, receiver): unknown {
      const value: unknown = Reflect.get(target, key, receiver);
      if (typeof value !== 'function') return value;
      return (...args: unknown[]): unknown => {
        if (ended()) throw new Error(PAGE_ENDED);
        const result: unknown = Reflect.apply(value, target, args);
        return result instanceof Promise ? track(result) : result;
      };
    },
  });
}

/** The whole App on the fake transport; call `vi.useFakeTimers()` first. */
export async function renderPage(options: PageOptions = {}): Promise<PageHarness> {
  installDialogPolyfill();
  shimJestTimers();
  const booted = bootDashboard(options);
  let ended = false;
  const inFlight = new Set<Promise<unknown>>();
  const track = async <T,>(step: Promise<T>): Promise<T> => {
    // React's act result is a thenable: subscribe once, or draining it can pop
    // the same act scope a second time while the original caller is awaiting it.
    const pending = Promise.resolve(step);
    inFlight.add(pending);
    try {
      return await pending;
    } finally {
      inFlight.delete(pending);
    }
  };
  let finishing: Promise<void> | undefined;
  const finish = (): Promise<void> => {
    ended = true;
    finishing ??= (async () => {
      let deadline: ReturnType<typeof setRealTimeout> | undefined;
      try {
        if (inFlight.size > 0) {
          await Promise.race([
            Promise.allSettled([...inFlight]),
            new Promise<never>((_, reject) => {
              deadline = setRealTimeout(() => {
                reject(
                  new Error(`renderPage: in-flight work did not settle within ${DRAIN_MS} ms`),
                );
              }, DRAIN_MS);
            }),
          ]);
        }
      } finally {
        clearRealTimeout(deadline);
        pageTeardowns.delete(finish);
        booted.data.stop();
      }
    })();
    return finishing;
  };
  pageTeardowns.add(finish);
  onTestFinished(finish, TEARDOWN_MS);
  const faults = options.faults instanceof Set ? options.faults : new Set(options.faults ?? []);
  const view = render(<App data={booted.data} faults={faults} />);
  const advance = async (ms = 0) => {
    if (ended) throw new Error(PAGE_ENDED);
    await track(
      act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      }),
    );
  };
  await advance(0);
  const user = userEvent.setup({
    delay: null,
    advanceTimers: (ms) => {
      vi.advanceTimersByTime(ms);
    },
  });
  return { ...booted, view, user: guardUser(user, () => ended, track), advance };
}

/**
 * Enter `text` into `element` as one input event: the click `user.type` starts with, then a
 * paste at the caret it leaves. For steps whose subject is the entered value rather than each
 * keystroke: every keystroke redraws the page, which on a loaded machine made a long value
 * alone exceed the test timeout.
 */
export async function enterText(user: UserEvent, element: Element, text: string): Promise<void> {
  await user.click(element);
  // user.type ignores a disabled control; a paste would land in whatever has focus instead.
  if (element.ownerDocument.activeElement !== element)
    throw new Error('enterText: element did not take focus');
  await user.paste(text);
}

export type RecordingCommands = {
  readonly [K in keyof DashboardCommands]: Mock<DashboardCommands[K]>;
};

/** Commands that record their calls and send nothing. */
export function recordingCommands(): RecordingCommands {
  const done = () => Promise.resolve();
  return {
    pause: vi.fn(done),
    turnOn: vi.fn(done),
    turnInputOff: vi.fn(done),
    allowInput: vi.fn(done),
    stop: vi.fn(done),
    clearFlag: vi.fn(done),
    handoff: vi.fn(done),
    start: vi.fn(done),
    collect: vi.fn(done),
    reconnect: vi.fn(done),
    runNow: vi.fn(done),
    resend: vi.fn(done),
    dismiss: vi.fn(() => undefined),
    acceptCurrent: vi.fn(() => undefined),
    checkStatus: vi.fn(() => undefined),
    checkSetupStatus: vi.fn(() => undefined),
    selectRun: vi.fn(() => undefined),
    selectGemSource: vi.fn(() => undefined),
    openRuns: vi.fn(() => undefined),
    editDraft: vi.fn(() => undefined),
    discardDraft: vi.fn(() => undefined),
    saveRun: vi.fn(done),
    addRun: vi.fn(done),
    checkRunAdd: vi.fn(),
    saveLive: vi.fn(done),
    resetLive: vi.fn(done),
    checkRun: vi.fn(() => undefined),
    checkRuns: vi.fn(() => undefined),
    checkBattle: vi.fn(() => undefined),
    checkEarlierBattle: vi.fn(done),
    pickEditingTarget: vi.fn(() => undefined),
    checkEditingProfiles: vi.fn(() => undefined),
    returnEditingToActive: vi.fn(() => undefined),
    openProfiles: vi.fn(() => undefined),
    checkProfile: vi.fn(done),
    profileWrite: vi.fn(done),
    openCollection: vi.fn(() => undefined),
    saveSettings: vi.fn(done),
    patchProfile: vi.fn(done),
    patchRunEntry: vi.fn(done),
    checkRunEntry: vi.fn(() => undefined),
    checkCollection: vi.fn(() => undefined),
    openEmulators: vi.fn(() => undefined),
    openRuntime: vi.fn(() => undefined),
    openEquipment: vi.fn(() => undefined),
    openRecognition: vi.fn(() => undefined),
    selectRecognitionRun: vi.fn(() => undefined),
    checkRecognitionReadiness: vi.fn(done),
    checkRecognition: vi.fn(() => undefined),
    recognitionLaunch: vi.fn(done),
    recognitionApply: vi.fn(done),
    recognitionStop: vi.fn(done),
    emulatorWrite: vi.fn(done),
    rescanEmulators: vi.fn(() => undefined),
    checkTools: vi.fn(() => undefined),
    checkSettings: vi.fn(() => undefined),
    checkResolution: vi.fn(done),
  };
}

export interface RegionHarness {
  readonly view: RenderResult;
  readonly store: DashboardStore;
  readonly commands: RecordingCommands;
  readonly reportFault: Mock<DashboardContextValue['reportFault']>;
  /** Replace the state (a later read) and let React draw it. */
  readonly setState: (state: DashboardState) => void;
}

/** `element` inside the Dashboard context, on a fixed state with recording commands. */
export function renderRegion(
  state: DashboardState,
  element: ReactElement,
  options: { readonly faults?: readonly string[] } = {},
): RegionHarness {
  installDialogPolyfill();
  const store = createDashboardStore(state);
  const commands = recordingCommands();
  const reportFault = vi.fn<DashboardContextValue['reportFault']>();
  const value: DashboardContextValue = {
    store,
    commands,
    reportFault,
    faults: new Set(options.faults ?? []),
  };
  const view = render(
    <LiveRegionProvider>
      <DashboardContext value={value}>{element}</DashboardContext>
    </LiveRegionProvider>,
  );
  return {
    view,
    store,
    commands,
    reportFault,
    setState: (next) => {
      act(() => {
        store.update(() => next);
      });
    },
  };
}
