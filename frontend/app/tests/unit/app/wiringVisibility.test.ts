import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bootDashboard, flush } from '../../support/dataLayer.ts';

/**
 * The production default of the visibility source (app/wiring.ts, spec 7.2, F-108): the wiring is
 * built WITHOUT a `visibility` option, so it must read the real `document.visibilityState` and
 * listen to `visibilitychange`. tests/unit/api/poller.test.ts proves the poller's backoff against an
 * injected fake source; this proves the page itself is connected to the real one (a default of
 * `null` passes every other Vitest test and is caught only by Playwright).
 */
const START = 1_800_000_000_000;

let state: DocumentVisibilityState = 'visible';
let stopWiring: (() => void) | null = null;

function setVisibility(next: DocumentVisibilityState): void {
  state = next;
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  state = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  vi.useFakeTimers({ now: START });
});
afterEach(() => {
  stopWiring?.();
  stopWiring = null;
  vi.useRealTimers();
  Reflect.deleteProperty(document, 'visibilityState');
});

describe('the default visibility source of the wired data layer (F-108)', () => {
  it('backs the reads off while document.visibilityState is hidden and reads again when visible', async () => {
    const { fake, data } = bootDashboard({ documentVisibility: true });
    stopWiring = data.stop;
    const statusReads = () =>
      fake.requests.filter((entry) => entry.method === 'GET' && entry.path === '/api/status');
    const battleReads = () => fake.requests.filter((entry) => entry.path === '/api/battles/active');

    await vi.advanceTimersByTimeAsync(4000);
    expect(data.store.getSnapshot().hidden).toBe(false);
    // Visible: status is read again and again, and so is the battle.
    expect(statusReads().length).toBeGreaterThan(2);
    expect(battleReads().length).toBeGreaterThan(2);

    // Hidden: the store learns it, status slows to one read per 10 s, battle reads stop.
    setVisibility('hidden');
    expect(data.store.getSnapshot().hidden).toBe(true);
    const statusBefore = statusReads().length;
    const battleBefore = battleReads().length;
    await vi.advanceTimersByTimeAsync(30_000);
    const hiddenStatus = statusReads().slice(statusBefore);
    expect(hiddenStatus).toHaveLength(3);
    // The first hidden read waits out the slow interval; each next one follows 10 s later.
    const [first, second, third] = hiddenStatus.map((entry) => entry.at);
    expect([second, third]).toEqual([(first ?? 0) + 10_000, (first ?? 0) + 20_000]);
    expect(battleReads().length).toBe(battleBefore);

    // Visible again: the store learns it and every resource is read at once.
    const statusHidden = statusReads().length;
    setVisibility('visible');
    await flush();
    expect(data.store.getSnapshot().hidden).toBe(false);
    expect(statusReads().length).toBe(statusHidden + 1);
    expect(statusReads().at(-1)?.at).toBe(Date.now());
    expect(battleReads().length).toBe(battleBefore + 1);
  });
});
