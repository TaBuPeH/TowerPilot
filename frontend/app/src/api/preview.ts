import type { DisplayedPreview } from '../core/preview.ts';
import {
  createPreviewParser,
  previewBoundary,
  PreviewIdentityError,
  type PreviewPart,
} from './previewMultipart.ts';
import { readEndpoint } from './client.ts';
import { fetchTransport, fetchStreamTransport } from './transport.ts';
import { ENDPOINTS } from './wire.ts';
import { readJsonItem, writeJsonItem } from './storage.ts';

export interface PreviewApi {
  readonly availability: (signal: AbortSignal) => Promise<unknown>;
  readonly status: (id: string, signal: AbortSignal) => Promise<unknown>;
  readonly stream: (
    url: `/api/${string}`,
    signal: AbortSignal,
    frame: (value: DisplayedPreview) => void,
    failed: (reason?: 'unknown') => void,
  ) => Promise<void>;
  readonly attach: (canvas: HTMLCanvasElement | null) => void;
  readonly clear: () => void;
  readonly load: () => unknown;
  readonly save: (value: unknown) => boolean;
  readonly now: () => number;
  readonly newId: () => string;
}

/** One cancellable decode. A newer complete part replaces it; there is no frame queue. */
export function createPreviewPainter(now: () => number = () => performance.now()) {
  let canvas: HTMLCanvasElement | null = null;
  let cancel: (() => void) | null = null;
  let sequence = 0;
  const clear = () => {
    cancel?.();
    cancel = null;
    sequence = 0;
    if (canvas !== null) {
      canvas.width = 0;
      canvas.height = 0;
    }
  };
  return {
    attach(value: HTMLCanvasElement | null) {
      clear();
      canvas = value;
    },
    clear,
    offer(
      part: PreviewPart,
      signal: AbortSignal,
      frame: (value: DisplayedPreview) => void,
      failed: () => void,
    ) {
      if (signal.aborted) return;
      if (part.kind === 'frame' && part.sequence <= sequence) return;
      cancel?.();
      const receivedAt = now();
      if (part.kind === 'error') {
        const previousSequence = sequence;
        clear();
        sequence = previousSequence;
        frame({
          kind: part.kind,
          sequence: part.sequence,
          backend: part.backend,
          sourceAgeMs: part.sourceAgeMs,
          receivedAt,
        });
        return;
      }
      const image = new Image();
      const url = URL.createObjectURL(new Blob([part.jpeg], { type: 'image/jpeg' }));
      let active = true;
      const cleanup = () => {
        if (!active) return;
        active = false;
        image.onload = null;
        image.onerror = null;
        image.src = '';
        URL.revokeObjectURL(url);
        signal.removeEventListener('abort', cleanup);
        if (cancel === cleanup) cancel = null;
      };
      cancel = cleanup;
      signal.addEventListener('abort', cleanup, { once: true });
      image.onload = () => {
        if (active && !signal.aborted && canvas !== null) {
          const context = canvas.getContext('2d');
          if (context !== null && image.naturalWidth > 0 && image.naturalHeight > 0) {
            canvas.width = image.naturalWidth;
            canvas.height = image.naturalHeight;
            context.drawImage(image, 0, 0);
            sequence = part.sequence;
            frame({
              kind: part.kind,
              sequence: part.sequence,
              backend: part.backend,
              sourceAgeMs: part.sourceAgeMs,
              receivedAt,
            });
          } else failed();
        }
        cleanup();
      };
      image.onerror = () => {
        if (active && !signal.aborted) failed();
        cleanup();
      };
      image.src = url;
    },
  };
}

/** Streaming is binary, so it uses a bounded reader instead of the JSON transport. */
export async function readPreviewStream(
  url: `/api/${string}`,
  signal: AbortSignal,
  part: (value: PreviewPart) => void,
) {
  const { headers, reader } = await fetchStreamTransport(url, signal);
  const cancel = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener('abort', cancel, { once: true });
  try {
    const parser = createPreviewParser(previewBoundary(headers.get('Content-Type') ?? ''));
    for (;;) {
      if (signal.aborted) return;
      const read = await reader.read();
      if (read.done) {
        parser.finish();
        return;
      }
      const latest = parser.feed(read.value);
      if (latest !== null) part(latest);
    }
  } finally {
    signal.removeEventListener('abort', cancel);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
const PREFS_KEY = 'tp.preview.preferences.v1';
function viewerStorage() {
  try {
    return localStorage;
  } catch {
    return null;
  }
}
export function createPreviewApi(): PreviewApi {
  const now = () => performance.now();
  const painter = createPreviewPainter(now);
  return {
    async availability(signal) {
      const result = await readEndpoint(
        fetchTransport,
        ENDPOINTS.previewBackends,
        '/api/stream/backends',
        signal,
      );
      if (result.kind === 'failed' && result.answer === null)
        throw new Error('Availability read failed');
      return result.kind === 'answered' ? result.answer.body : result.answer?.body;
    },
    async status(id, signal) {
      const result = await readEndpoint(
        fetchTransport,
        ENDPOINTS.previewStatus,
        `/api/stream/status?stream_id=${id}`,
        signal,
      );
      if (result.kind !== 'answered') throw new Error('Status read failed');
      return result.answer.body;
    },
    async stream(url, signal, frame, failed) {
      try {
        await readPreviewStream(url, signal, (part) => {
          painter.offer(part, signal, frame, failed);
        });
      } catch (error) {
        if (error instanceof PreviewIdentityError) failed('unknown');
        else throw error;
      }
    },
    attach: (canvas) => {
      painter.attach(canvas);
    },
    clear: painter.clear,
    load: () => readJsonItem(viewerStorage(), PREFS_KEY),
    save: (value) => writeJsonItem(viewerStorage(), PREFS_KEY, value),
    now,
    newId: () => crypto.randomUUID(),
  };
}
