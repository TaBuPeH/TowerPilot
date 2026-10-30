// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { record } from '../../../scripts/generate-contract.mjs';
import {
  createPreviewParser,
  previewBoundary,
  previewPartIdentity,
} from '../../../src/api/previewMultipart.ts';
import { previewCases, previewSample } from '../../support/previewFixture.ts';

const encode = new TextEncoder();
function part(headers = '', jpeg = new Uint8Array([255, 216, 255, 217]), sequence = 1) {
  const head = encode.encode(
    `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\nX-Preview-Frame-Kind: frame\r\nX-Preview-Sequence: ${sequence}\r\nX-Preview-Backend: mumu\r\nX-Preview-Source-Age-Ms: unknown\r\n${headers}\r\n`,
  );
  const all = new Uint8Array(head.length + jpeg.length + 2);
  all.set(head);
  all.set(jpeg, head.length);
  all.set([13, 10], head.length + jpeg.length);
  return all;
}
describe('PV2 PV7 bounded multipart reader', () => {
  it('accepts an 8 MiB part completed in a read coalesced with the next part, within its buffer bound', () => {
    // Synthetic size/transport stress: no image decoder is involved in this parser-bound regression.
    const pixels = new Uint8Array(8 * 1024 * 1024);
    pixels.set([255, 216]);
    pixels.set([255, 217], pixels.length - 2);
    const large = part('', pixels);
    const next = new Uint8Array(100_000);
    next.set([255, 216]);
    next.set([255, 217], next.length - 2);
    const second = part('', next, 2);
    const p = createPreviewParser('frame');
    expect(p.feed(large.subarray(0, -3))).toBeNull();
    const coalesced = new Uint8Array(3 + second.length);
    coalesced.set(large.subarray(-3));
    coalesced.set(second, 3);
    expect(p.feed(coalesced)?.sequence).toBe(2);
    expect(p.buffered()).toBe(0);
    p.finish();
  });
  it('validates per-part provenance for every recorded multipart case, including error sequence zero', () => {
    let count = 0;
    for (const name of Object.keys(record(previewCases['preview_stream']))) {
      const answer = previewSample('preview_stream', name);
      const multipart = record(answer['body'])['multipart'];
      if (multipart === undefined) continue;
      const parts = record(multipart)['parts'];
      if (!Array.isArray(parts)) continue;
      for (const value of parts) {
        const headers = new Map(
          Object.entries(record(record(value)['headers'])).map(([key, val]) => [
            key.toLowerCase(),
            String(val),
          ]),
        );
        const parsed = previewPartIdentity(headers);
        expect(parsed.kind).toBe(headers.get('x-preview-frame-kind'));
        expect(parsed.backend).toBe(headers.get('x-preview-backend'));
        expect(parsed.sequence).toBe(Number(headers.get('x-preview-sequence')));
        count++;
      }
    }
    expect(count).toBe(26);
  });
  it('handles one-byte short reads and boundaries split at every position', () => {
    const bytes = part();
    for (let split = 1; split < bytes.length; split++) {
      const p = createPreviewParser('frame');
      const first = p.feed(bytes.subarray(0, split));
      const second = p.feed(bytes.subarray(split));
      expect(second ?? first).toMatchObject({ sequence: 1, backend: 'mumu', sourceAgeMs: null });
      expect(p.buffered()).toBe(0);
      p.finish();
    }
    const p = createPreviewParser('frame');
    let latest;
    for (const byte of bytes) latest = p.feed(new Uint8Array([byte])) ?? latest;
    expect(latest?.jpeg).toEqual(new Uint8Array([255, 216, 255, 217]));
  });
  it('keeps only the latest complete part in a coalesced read', () => {
    const one = part();
    // Synthetic transport coalescing; preserve JPEG bytes while varying only sequence.
    const second = part().slice();
    const offset = new TextDecoder().decode(one).indexOf('Sequence: 1') + 'Sequence: '.length;
    second[offset] = 50;
    const bytes = new Uint8Array(one.length + second.length);
    bytes.set(one);
    bytes.set(second, one.length);
    expect(createPreviewParser('frame').feed(bytes)?.sequence).toBe(2);
  });
  it.each(['wat', '-1', '3.5', '999999999', '9007199254740992'])(
    'rejects corrupt Content-Length %s without buffering pixels',
    (length) => {
      // Synthetic transport corruption, not a new backend response case.
      const bytes = part();
      const header = new TextDecoder('latin1').decode(bytes).split('\r\n\r\n')[0] ?? '';
      const p = createPreviewParser('frame');
      expect(() =>
        p.feed(
          encode.encode(
            header.replace('Content-Length: 4', `Content-Length: ${length}`) + '\r\n\r\n',
          ),
        ),
      ).toThrow();
      expect(p.buffered()).toBeLessThan(8192);
    },
  );
  it('rejects duplicate/missing headers, truncated JPEGs and excessive header buffers', () => {
    expect(() => createPreviewParser('frame').feed(part('Content-Length: 4\r\n'))).toThrow(
      'Duplicate',
    );
    expect(() => previewPartIdentity(new Map())).toThrow();
    expect(() =>
      createPreviewParser('frame').feed(part('', new Uint8Array([255, 216, 0, 0]))),
    ).toThrow('JPEG');
    const p = createPreviewParser('frame');
    p.feed(part().subarray(0, -3));
    expect(() => {
      p.finish();
    }).toThrow('Truncated');
    expect(() =>
      createPreviewParser('frame').feed(encode.encode('--frame\r\n' + 'x'.repeat(9000))),
    ).toThrow('headers too large');
  });
  it('validates the response boundary and enforces a finite part limit', () => {
    expect(previewBoundary('multipart/x-mixed-replace; boundary="frame"')).toBe('frame');
    expect(() => previewBoundary('image/jpeg')).toThrow();
    expect(() => createPreviewParser('frame', 3).feed(part())).toThrow();
  });
});
