import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPreviewPainter } from '../../../src/api/preview.ts';
import type { PreviewPart } from '../../../src/api/previewMultipart.ts';

const frames: StubImage[] = [];
class StubImage {
  src = '';
  naturalWidth = 12;
  naturalHeight = 24;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() {
    frames.push(this);
  }
}
afterEach(() => {
  frames.length = 0;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function setup() {
  vi.stubGlobal('Image', StubImage);
  const revoked = vi.fn();
  let id = 0;
  vi.stubGlobal('URL', { createObjectURL: () => `blob:test-${++id}`, revokeObjectURL: revoked });
  const canvas = document.createElement('canvas');
  const draw = vi.fn();
  vi.spyOn(canvas, 'getContext').mockReturnValue({
    drawImage: draw,
  } as unknown as CanvasRenderingContext2D);
  const painter = createPreviewPainter(() => 1000);
  painter.attach(canvas);
  const signal = new AbortController();
  const frame = vi.fn();
  const failed = vi.fn();
  const part: PreviewPart = {
    kind: 'frame',
    sequence: 1,
    backend: 'mumu',
    sourceAgeMs: null,
    jpeg: new Uint8Array([255, 216, 255, 217]),
  };
  return { painter, canvas, draw, revoked, signal, frame, failed, part };
}
describe('PV1 PV2 latest decoded picture and URL lifetime', () => {
  it('cancels obsolete decode, revokes both URLs, and paints only the newest part', () => {
    const h = setup();
    h.painter.offer(h.part, h.signal.signal, h.frame, h.failed);
    const late = frames[0]?.onload;
    h.painter.offer(
      { ...h.part, sequence: 2, backend: 'scrcpy', sourceAgeMs: 50 },
      h.signal.signal,
      h.frame,
      h.failed,
    );
    late?.();
    expect(h.draw).not.toHaveBeenCalled();
    frames[1]?.onload?.();
    expect(h.draw).toHaveBeenCalledTimes(1);
    expect(h.frame).toHaveBeenCalledWith({
      kind: 'frame',
      sequence: 2,
      backend: 'scrcpy',
      sourceAgeMs: 50,
      receivedAt: 1000,
    });
    expect(h.revoked).toHaveBeenCalledTimes(2);
    expect(h.revoked).toHaveBeenNthCalledWith(1, 'blob:test-1');
    expect(h.revoked).toHaveBeenNthCalledWith(2, 'blob:test-2');
  });
  it('abort prevents a late decode from repainting and clear removes all pixels', () => {
    const h = setup();
    h.painter.offer(h.part, h.signal.signal, h.frame, h.failed);
    const late = frames[0]?.onload;
    h.signal.abort();
    h.painter.clear();
    late?.();
    expect(h.draw).not.toHaveBeenCalled();
    expect(h.frame).not.toHaveBeenCalled();
    expect(h.revoked).toHaveBeenCalledTimes(1);
    expect(h.canvas.width).toBe(0);
    expect(h.canvas.height).toBe(0);
  });
  it('decode failure cannot count as successful delivery; error markers clear the old picture', () => {
    const h = setup();
    h.painter.offer(h.part, h.signal.signal, h.frame, h.failed);
    frames[0]?.onerror?.();
    expect(h.failed).toHaveBeenCalledTimes(1);
    expect(h.frame).not.toHaveBeenCalled();
    h.painter.offer({ ...h.part, kind: 'error', sequence: 0 }, h.signal.signal, h.frame, h.failed);
    expect(h.canvas.width).toBe(0);
    expect(h.frame).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error', sequence: 0 }));
    expect(h.revoked).toHaveBeenCalledTimes(1);
  });
});
