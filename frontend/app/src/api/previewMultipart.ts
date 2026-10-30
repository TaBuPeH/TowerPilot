import type { PreviewPartIdentity } from '../core/preview.ts';

export interface PreviewPart extends PreviewPartIdentity {
  readonly jpeg: Uint8Array<ArrayBuffer>;
}
export const MAX_PREVIEW_PART = 8 * 1024 * 1024;
export const MAX_PREVIEW_HEADERS = 8192;
const encoder = new TextEncoder();
const decoder = new TextDecoder('ascii', { fatal: true });
export class PreviewIdentityError extends Error {}

export function previewBoundary(contentType: string): string {
  const match =
    /^multipart\/x-mixed-replace\s*;\s*boundary=(?:"([A-Za-z0-9_-]{1,70})"|([A-Za-z0-9_-]{1,70}))\s*$/i.exec(
      contentType,
    );
  const boundary = match?.[1] ?? match?.[2];
  if (boundary === undefined) throw new Error('Invalid multipart content type');
  return boundary;
}
function integer(value: string | undefined): number {
  if (value === undefined || !/^\d+$/.test(value)) throw new Error('Invalid multipart integer');
  const n = Number(value);
  if (!Number.isSafeInteger(n)) throw new Error('Invalid multipart integer');
  return n;
}
/** The four headers identify these pixels, never a nearby status response. */
export function previewPartIdentity(headers: ReadonlyMap<string, string>): PreviewPartIdentity {
  const kind = headers.get('x-preview-frame-kind');
  const backend = headers.get('x-preview-backend');
  const sequence = integer(headers.get('x-preview-sequence'));
  const age = headers.get('x-preview-source-age-ms');
  if (kind !== 'frame' && kind !== 'error') throw new Error('Missing or invalid frame kind');
  if (backend !== 'mumu' && backend !== 'scrcpy' && backend !== 'adb' && backend !== 'unknown')
    throw new Error('Missing or invalid frame source');
  if (kind === 'frame' && sequence === 0) throw new Error('Invalid successful sequence');
  if (
    age === undefined ||
    (age !== 'unknown' && (!/^\d+(?:\.\d+)?$/.test(age) || !Number.isFinite(Number(age))))
  )
    throw new Error('Missing or invalid source age');
  return { kind, backend, sequence, sourceAgeMs: age === 'unknown' ? null : Number(age) };
}

/** Bounded incremental parser. Each feed returns only its newest complete part. */
export function createPreviewParser(boundary: string, maxPart = MAX_PREVIEW_PART) {
  if (!/^[A-Za-z0-9_-]{1,70}$/.test(boundary)) throw new Error('Invalid multipart boundary');
  const marker = encoder.encode(`--${boundary}\r\n`);
  const capacity = maxPart + MAX_PREVIEW_HEADERS + marker.length + 4;
  let buffer: Uint8Array<ArrayBuffer> = new Uint8Array(0);
  let state: 'boundary' | 'headers' | 'body' = 'boundary';
  let length = 0;
  let identity: PreviewPartIdentity | null = null;
  const consume = (n: number) => {
    buffer = buffer.slice(n);
  };
  const parse = (): PreviewPart | null => {
    let latest: PreviewPart | null = null;
    for (;;) {
      if (state === 'boundary') {
        if (buffer.length < marker.length) return latest;
        if (!marker.every((byte, i) => buffer[i] === byte))
          throw new Error('Malformed multipart boundary');
        consume(marker.length);
        state = 'headers';
      }
      if (state === 'headers') {
        let end = -1;
        for (let i = 0; i + 3 < buffer.length && i <= MAX_PREVIEW_HEADERS; i++) {
          if (
            buffer[i] === 13 &&
            buffer[i + 1] === 10 &&
            buffer[i + 2] === 13 &&
            buffer[i + 3] === 10
          ) {
            end = i;
            break;
          }
        }
        if (end < 0) {
          if (buffer.length > MAX_PREVIEW_HEADERS) throw new Error('Multipart headers too large');
          return latest;
        }
        const headers = new Map<string, string>();
        for (const line of decoder.decode(buffer.subarray(0, end)).split('\r\n')) {
          const split = line.indexOf(':');
          if (split < 1) throw new Error('Malformed multipart header');
          const key = line.slice(0, split).toLowerCase();
          if (headers.has(key)) throw new Error('Duplicate multipart header');
          headers.set(key, line.slice(split + 1).trim());
        }
        length = integer(headers.get('content-length'));
        if (length < 4 || length > maxPart) throw new Error('Multipart part too large or empty');
        if (headers.get('content-type')?.toLowerCase() !== 'image/jpeg')
          throw new Error('Expected JPEG part');
        try {
          identity = previewPartIdentity(headers);
        } catch {
          throw new PreviewIdentityError('Part identity unavailable');
        }
        consume(end + 4);
        state = 'body';
      }
      {
        if (buffer.length < length + 2) return latest;
        if (
          buffer[0] !== 255 ||
          buffer[1] !== 216 ||
          buffer[length - 2] !== 255 ||
          buffer[length - 1] !== 217 ||
          buffer[length] !== 13 ||
          buffer[length + 1] !== 10
        )
          throw new Error('Truncated or malformed JPEG part');
        if (identity === null) throw new Error('Missing part identity');
        latest = { ...identity, jpeg: buffer.slice(0, length) };
        consume(length + 2);
        identity = null;
        state = 'boundary';
      }
    }
  };
  return {
    feed(chunk: Uint8Array): PreviewPart | null {
      let latest: PreviewPart | null = null;
      // A hostile read can itself exceed the budget; never copy it wholesale.
      for (let offset = 0; offset < chunk.length;) {
        const room = capacity - buffer.length;
        if (room <= 0) throw new Error('Multipart buffer limit exceeded');
        const end = Math.min(chunk.length, offset + 65536, offset + room);
        const piece = chunk.subarray(offset, end);
        const next = new Uint8Array(buffer.length + piece.length);
        next.set(buffer);
        next.set(piece, buffer.length);
        buffer = next;
        latest = parse() ?? latest;
        offset = end;
      }
      return latest;
    },
    finish() {
      if (buffer.length !== 0 || state !== 'boundary')
        throw new Error('Truncated multipart response');
    },
    buffered: () => buffer.length,
  };
}
