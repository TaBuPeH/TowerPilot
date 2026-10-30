import type { Page } from '@playwright/test';

interface RaceProbe {
  held: boolean;
  aborted: string[];
  requests: { path: string; at: number }[];
  draws: { image: number; width: number; height: number }[];
  released: boolean;
  releasedAt: number | null;
  decodeCancelled: boolean;
  decodeHeld: boolean;
  release: () => Promise<void>;
}

/** Hold real response text / native JPEG completion; never change recorded answers or pixels. */
export async function installPreviewRace(page: Page, hold: 'status-decode' | 'availability') {
  await page.addInitScript((hold) => {
    let releaseText: () => void = () => undefined;
    const textGate = new Promise<void>((resolve) => {
      releaseText = resolve;
    });
    let releaseDecode = () => undefined;
    const probe: RaceProbe = {
      held: false,
      aborted: [],
      requests: [],
      draws: [],
      released: false,
      releasedAt: null,
      decodeCancelled: false,
      decodeHeld: false,
      async release() {
        releaseDecode();
        releaseText();
        // Completion barrier: the released text promise, transport/parser continuations,
        // store update and React paint all run before the test's negative assertions.
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => {
            resolve();
          }),
        );
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => {
            resolve();
          }),
        );
        probe.releasedAt = performance.now();
        probe.released = true;
      },
    };
    Reflect.set(window, 'previewRace', probe);
    const nativeFetch = window.fetch.bind(window);
    let heldText = false;
    window.fetch = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input.toString(), location.href);
      const preview = url.pathname.startsWith('/api/stream');
      if (preview) {
        probe.requests.push({ path: url.pathname + url.search, at: performance.now() });
        init?.signal?.addEventListener('abort', () => {
          probe.aborted.push(url.pathname + url.search);
        });
      }
      const response = await nativeFetch(input, init);
      const path = hold === 'availability' ? '/api/stream/backends' : '/api/stream/status';
      if (url.pathname === path && !heldText) {
        heldText = true;
        const text = await response.text();
        // Transport fault: delay an already received, exact recorded answer past abort.
        // Status stream_id rebinding is performed only by the existing fake backend.
        response.text = async () => {
          probe.held = true;
          await textGate;
          return text;
        };
      }
      return response;
    };
    if (hold !== 'status-decode') return;
    const NativeImage = window.Image;
    const src = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
    let images = 0;
    // Transport/decode fault: retain the second real decoded old JPEG and its queued
    // onload callback even when cancellation clears handlers/src. Release delivers the
    // obsolete completion to the actual painter closure, exercising its session fence.
    window.Image = class extends NativeImage {
      constructor() {
        super();

        const number = ++images;
        Reflect.set(this, 'previewImageNumber', number);
        if (number === 2) {
          let deferredClear = false;
          Object.defineProperty(this, 'src', {
            get: () => src?.get?.call(this) as string,
            set: (value: string) => {
              if (value === '') {
                deferredClear = true;
                probe.decodeCancelled = true;
              } else src?.set?.call(this, value);
            },
          });
          Object.defineProperty(this, 'onload', {
            set: (callback: ((event: Event) => void) | null) => {
              if (callback === null) return;
              this.addEventListener(
                'load',
                (event) => {
                  releaseDecode = () => {
                    callback.call(this, event);
                    if (deferredClear) src?.set?.call(this, '');
                  };
                  probe.decodeHeld = true;
                },
                { once: true },
              );
            },
          });
        }
      }
    };
    const draw = Reflect.get(CanvasRenderingContext2D.prototype, 'drawImage');
    CanvasRenderingContext2D.prototype.drawImage = function (image, dx, dy, ...rest: number[]) {
      probe.draws.push({
        image: Number(Reflect.get(image, 'previewImageNumber')),
        width: this.canvas.width,
        height: this.canvas.height,
      });
      Reflect.apply(draw, this, [image, dx, dy, ...rest]);
    };
  }, hold);
}

export async function raceProbe(page: Page) {
  return page.evaluate(() => Reflect.get(window, 'previewRace') as RaceProbe);
}

export async function releasePreviewRace(page: Page) {
  await page.evaluate(async () => {
    const probe = Reflect.get(window, 'previewRace') as RaceProbe;
    await probe.release();
  });
}
