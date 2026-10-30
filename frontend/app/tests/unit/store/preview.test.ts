import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPreviewStore } from '../../../src/store/preview.ts';
import type { PreviewApi } from '../../../src/api/preview.ts';
import type { PreviewTarget } from '../../../src/store/preview.ts';
import type { DisplayedPreview } from '../../../src/core/preview.ts';
import { previewBody } from '../../support/previewFixture.ts';
import { record } from '../../../scripts/generate-contract.mjs';

function harness(connected = true) {
  let frame: (value: DisplayedPreview) => void = () => undefined;
  let id = 0;
  const api = {
    availability: vi.fn(() => Promise.resolve(previewBody('preview_backends', 'all_available'))),
    status: vi.fn((streamId: string, signal: AbortSignal): Promise<unknown> => {
      if (signal.aborted) return Promise.reject(new Error('Aborted status request'));
      return Promise.resolve({
        ...record(previewBody('preview_status', 'streaming_mumu')),
        stream_id: streamId,
      });
    }),
    stream: vi.fn((_url: string, signal: AbortSignal, onFrame: typeof frame) => {
      frame = onFrame;
      return new Promise<void>((resolve) => {
        signal.addEventListener(
          'abort',
          () => {
            resolve();
          },
          { once: true },
        );
      });
    }),
    clear: vi.fn(),
    attach: vi.fn(),
    load: () => null,
    save: vi.fn(() => true),
    now: () => Date.now(),
    newId: () => `test${++id}`,
  } satisfies PreviewApi;
  const store = createPreviewStore(api);
  store.setTarget({ epoch: 1, key: 'main/a', instance: 'main', connected, hidden: false });
  const emit = () => {
    frame({
      kind: 'frame',
      sequence: 1,
      backend: 'mumu',
      sourceAgeMs: null,
      receivedAt: Date.now(),
    });
  };
  return { api, store, emit };
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());
describe('PV1 PV3 PV4 PV5 PV6 preview session lifecycle', () => {
  it('an aborted unresolved availability cannot time out its replacement session', async () => {
    const { store, api } = harness();
    let resolveOld: (value: unknown) => void = () => undefined;
    api.availability.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    store.expand(true);
    const old = store.start();
    await Promise.resolve();
    await store.start();
    const replacement = api.stream.mock.calls[0]?.[1];
    expect(replacement?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(8100);
    expect(replacement?.aborted).toBe(false);
    resolveOld(previewBody('preview_backends', 'all_available'));
    await old;
    expect(api.stream).toHaveBeenCalledTimes(1);
    store.stop();
  });
  it('does no request on mount, disclosure, or preference edits while off', () => {
    const { store, api } = harness();
    store.expand(true);
    store.edit({ fps: 0.5, source: 'adb', quality: 'low' });
    expect(api.stream).not.toHaveBeenCalled();
    expect(api.availability).not.toHaveBeenCalled();
    expect(api.status).not.toHaveBeenCalled();
    expect(api.save).toHaveBeenCalledWith({ fps: 0.5, source: 'adb', quality: 'low' });
  });
  it('checks once, polls single-flight, aborts and rejects a late decoded part after Stop', async () => {
    const { store, api, emit } = harness();
    store.expand(true);
    await store.start();
    expect(api.availability).toHaveBeenCalledTimes(1);
    expect(api.stream).toHaveBeenCalledTimes(1);
    emit();
    expect(store.getSnapshot().frame?.sequence).toBe(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.status).toHaveBeenCalledTimes(1);
    const signal = api.stream.mock.calls[0]?.[1];
    store.stop();
    expect(signal?.aborted).toBe(true);
    emit();
    await vi.advanceTimersByTimeAsync(6000);
    expect(store.getSnapshot().frame).toBeNull();
    expect(api.status).toHaveBeenCalledTimes(1);
    expect(api.clear).toHaveBeenCalled();
  });
  it('reconnect closes the old session before opening a new ID', async () => {
    const { store, api } = harness();
    store.expand(true);
    await store.start();
    const first = api.stream.mock.calls[0];
    await store.start();
    expect(first?.[1].aborted).toBe(true);
    expect(api.stream).toHaveBeenCalledTimes(2);
    expect(api.stream.mock.calls[1]?.[0]).not.toBe(first?.[0]);
    store.stop();
  });
  it.each(['hidden', 'context', 'collapse', 'offline'])('closes on %s', async (event) => {
    const { store, api, emit } = harness();
    store.expand(true);
    await store.start();
    if (event === 'collapse') store.expand(false);
    else
      store.setTarget({
        epoch: event === 'context' ? 2 : 1,
        key: event === 'context' ? 'other/a' : 'main/a',
        instance: 'main',
        connected: event !== 'offline',
        hidden: event === 'hidden',
      });
    emit();
    expect(store.getSnapshot().frame).toBeNull();
    expect(store.getSnapshot().running).toBe(false);
    expect(api.stream.mock.calls[0]?.[1].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.stream).toHaveBeenCalledTimes(1);
  });
  it('rejects delayed old-context availability and status and unknown transport never captures', async () => {
    const { store, api } = harness();
    let answer: (value: unknown) => void = () => undefined;
    api.availability.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    store.expand(true);
    const pending = store.start();
    await Promise.resolve();
    store.setTarget({
      epoch: 2,
      key: 'other/a',
      instance: 'other',
      connected: false,
      hidden: false,
    });
    answer(previewBody('preview_backends', 'all_available'));
    await pending;
    expect(api.stream).not.toHaveBeenCalled();
    expect(store.getSnapshot().availability).toBeNull();
    await store.start();
    expect(api.availability).toHaveBeenCalledTimes(1);
  });
  it('PV1 PV4 C-01 keeps a replacement context bound after obsolete status and frame callbacks complete', async () => {
    const { store, api, emit } = harness();
    let answer: (value: unknown) => void = () => undefined;
    api.status.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    store.expand(true);
    await store.start();
    const old = api.stream.mock.calls[0];
    const oldId = store.getSnapshot().streamId;
    expect(old).toBeDefined();
    await vi.advanceTimersByTimeAsync(7000);
    expect(api.status).toHaveBeenCalledTimes(1);
    const oldStatusSignal = api.status.mock.calls[0]?.[1];
    store.setTarget({
      epoch: 2,
      key: 'main/beta',
      instance: 'main',
      connected: true,
      hidden: false,
    });
    store.edit({ fps: 15, source: 'auto', quality: 'low' });
    await store.start();
    emit();
    await vi.advanceTimersByTimeAsync(2000);
    const replacement = store.getSnapshot();
    expect(replacement.streamId).not.toBe(oldId);
    expect(replacement.report?.streamId).toBe(replacement.streamId);
    expect(replacement.frame?.sequence).toBe(1);
    expect(old?.[1].aborted).toBe(true);
    expect(oldStatusSignal?.aborted).toBe(true);
    // Exact recorded old status, with correlation-only stream_id rebinding.
    answer({ ...record(previewBody('preview_status', 'streaming_scrcpy')), stream_id: oldId });
    // Deliberately deliver an obsolete decoded frame callback despite abort.
    old?.[2]({
      kind: 'frame',
      sequence: 2,
      backend: 'scrcpy',
      sourceAgeMs: 50,
      receivedAt: Date.now(),
    });
    await vi.advanceTimersByTimeAsync(0); // Drain old poll's continuation/finally before asserting.
    expect(store.getSnapshot()).toEqual(replacement);
    await vi.advanceTimersByTimeAsync(6000);
    expect(api.status.mock.calls.filter(([id]) => id === oldId)).toHaveLength(1);
    expect(api.status.mock.calls.filter(([id]) => id === replacement.streamId)).toHaveLength(4);
    expect(api.stream).toHaveBeenCalledTimes(2);
    expect(api.stream.mock.calls[1]?.[1].aborted).toBe(false);
    expect(store.getSnapshot().frame).toEqual(replacement.frame);
    store.stop();
  });
});

const target = (over: Partial<PreviewTarget> = {}): PreviewTarget => ({
  epoch: 1,
  key: 'main/a',
  instance: 'main',
  connected: true,
  hidden: false,
  ...over,
});
const settle = () => vi.advanceTimersByTimeAsync(0);

describe('L5-06 / FOCUS #5 preview attaches by itself while the emulator is online', () => {
  it('attaches once the device reports online and not before', async () => {
    const { store, api } = harness(false);
    store.present(true);
    await settle();
    for (let i = 0; i < 5; i++) {
      store.setTarget(target({ connected: false }));
      await vi.advanceTimersByTimeAsync(10000);
    }
    expect(api.availability).not.toHaveBeenCalled();
    expect(api.stream).not.toHaveBeenCalled();
    expect(store.getSnapshot().running).toBe(false);
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().running).toBe(true);
    store.present(false);
  });
  it('attaches when the viewer mounts over an online device, with no press', async () => {
    const { store, api } = harness();
    expect(api.stream).not.toHaveBeenCalled();
    store.present(true);
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(1);
    store.present(false);
  });
  it('detaches when the device goes offline and reattaches with a new stream when it returns', async () => {
    const { store, api } = harness();
    store.present(true);
    await settle();
    const first = api.stream.mock.calls[0];
    store.setTarget(target({ connected: false }));
    expect(first?.[1].aborted).toBe(true);
    expect(store.getSnapshot().running).toBe(false);
    expect(store.getSnapshot().frame).toBeNull();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.stream).toHaveBeenCalledTimes(1);
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(2);
    expect(api.stream.mock.calls[1]?.[0]).not.toBe(first?.[0]);
    expect(store.getSnapshot().running).toBe(true);
    store.present(false);
  });
  it('keeps one session across repeated reads while online', async () => {
    const { store, api } = harness();
    store.present(true);
    await settle();
    for (let i = 0; i < 5; i++) {
      store.setTarget(target());
      await vi.advanceTimersByTimeAsync(1000);
    }
    expect(api.stream).toHaveBeenCalledTimes(1);
    expect(api.availability).toHaveBeenCalledTimes(1);
    store.present(false);
  });
  it('pauses while the tab is hidden and reattaches on return without a press', async () => {
    const { store, api } = harness();
    store.present(true);
    await settle();
    store.setTarget(target({ hidden: true }));
    expect(api.stream.mock.calls[0]?.[1].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.stream).toHaveBeenCalledTimes(1);
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(2);
    store.present(false);
  });
  it('a viewer that unmounts and returns reattaches; collapse and expand does too', async () => {
    const { store, api } = harness();
    store.present(true);
    await settle();
    store.present(false);
    expect(api.stream.mock.calls[0]?.[1].aborted).toBe(true);
    store.present(true);
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(2);
    store.expand(false);
    expect(api.stream.mock.calls[1]?.[1].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.stream).toHaveBeenCalledTimes(2);
    store.expand(true);
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(3);
    store.present(false);
  });
  it('does not fight the person: Stop holds across reads, hide, offline and return', async () => {
    const { store, api } = harness();
    store.present(true);
    await settle();
    expect(store.getSnapshot().stopped).toBe(false);
    store.stop();
    expect(store.getSnapshot().stopped).toBe(true); // the offline line must not promise an automatic start
    expect(api.stream.mock.calls[0]?.[1].aborted).toBe(true);
    for (const next of [
      target(),
      target({ hidden: true }),
      target(),
      target({ connected: false }),
      target(),
    ]) {
      store.setTarget(next);
      await vi.advanceTimersByTimeAsync(5000);
    }
    store.present(false);
    store.present(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.stream).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().running).toBe(false);
    await store.start();
    expect(api.stream).toHaveBeenCalledTimes(2);
    // After the person starts again, automatic behaviour resumes.
    store.setTarget(target({ connected: false }));
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(3);
    store.present(false);
  });
  it('backs off after stream errors: one retry per window, doubling, only while online', async () => {
    const { store, api } = harness();
    api.stream.mockImplementation(() => Promise.resolve());
    const read = async (ms: number) => {
      await vi.advanceTimersByTimeAsync(ms);
      store.setTarget(target());
      await settle();
    };
    store.present(true);
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().failed).toBe(true);
    // Many reads inside the first 3 s window cause no retry.
    for (let i = 0; i < 5; i++) await read(500);
    expect(api.stream).toHaveBeenCalledTimes(1);
    await read(1000);
    expect(api.stream).toHaveBeenCalledTimes(2);
    // Second window is 6 s.
    for (let i = 0; i < 5; i++) await read(1000);
    expect(api.stream).toHaveBeenCalledTimes(2);
    await read(1500);
    expect(api.stream).toHaveBeenCalledTimes(3);
    // Offline: no retries at all, however long.
    store.setTarget(target({ connected: false }));
    await vi.advanceTimersByTimeAsync(120000);
    store.setTarget(target({ connected: false }));
    expect(api.stream).toHaveBeenCalledTimes(3);
    // Back online resets the backoff: one prompt attempt.
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(4);
    store.present(false);
  });
  it('a received frame clears the backoff', async () => {
    const { store, api } = harness();
    // Each session delivers one frame, then ends; without the reset the window would double.
    api.stream.mockImplementation((_url, _signal, onFrame) => {
      onFrame({
        kind: 'frame',
        sequence: 1,
        backend: 'mumu',
        sourceAgeMs: null,
        receivedAt: Date.now(),
      });
      return Promise.resolve();
    });
    store.present(true);
    await settle();
    for (const expected of [2, 3, 4]) {
      await vi.advanceTimersByTimeAsync(3100);
      store.setTarget(target());
      await settle();
      expect(api.stream).toHaveBeenCalledTimes(expected);
    }
    store.present(false);
  });
  it('lifecycle changes during a backoff window do not bypass the deadline', async () => {
    const { store, api } = harness();
    api.stream.mockImplementation(() => Promise.resolve());
    store.present(true);
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(1);
    // Hide/show, collapse/expand and unmount/remount inside the 3 s window start nothing.
    store.setTarget(target({ hidden: true }));
    store.setTarget(target());
    store.expand(false);
    store.expand(true);
    store.present(false);
    store.present(true);
    await vi.advanceTimersByTimeAsync(1000);
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2100);
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(2);
    // Going offline and back inside the next window resets it: one prompt attempt.
    store.setTarget(target({ connected: false }));
    await vi.advanceTimersByTimeAsync(500);
    store.setTarget(target());
    await settle();
    expect(api.stream).toHaveBeenCalledTimes(3);
    store.present(false);
  });
});
