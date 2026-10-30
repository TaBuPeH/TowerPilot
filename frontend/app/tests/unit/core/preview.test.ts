// @vitest-environment node
import { describe, expect, it } from 'vitest';
import {
  PREVIEW_PRESETS,
  PREVIEW_FPS,
  previewAvailability,
  previewEffectiveFps,
  previewPreferences,
  previewReport,
  previewStreamId,
  previewThreshold,
  previewUrl,
  projectPreview,
  type PreviewProjectionInput,
  type DisplayedPreview,
} from '../../../src/core/preview.ts';
import { previewBody } from '../../support/previewFixture.ts';

const displayed: DisplayedPreview = {
  kind: 'frame',
  sequence: 2,
  backend: 'mumu',
  sourceAgeMs: null,
  receivedAt: 1000,
};
const healthy: PreviewProjectionInput = {
  running: true,
  connected: true,
  failed: false,
  statusFailed: false,
  now: 1000,
  fps: 15,
  reportAt: 1000,
  frame: displayed,
  report: previewReport(previewBody('preview_status', 'streaming_mumu')),
};
describe('PV1–PV7 preview owner', () => {
  it('owns every verdict and never treats an image alone as receiving', () => {
    expect(projectPreview({ ...healthy, running: false })).toBe('off');
    expect(projectPreview({ ...healthy, running: false, connected: false })).toBe('connection');
    expect(projectPreview({ ...healthy, frame: null, report: null })).toBe('connecting');
    expect(projectPreview(healthy)).toBe('receiving');
    expect(
      projectPreview({
        ...healthy,
        report: {
          ...previewReport(previewBody('preview_status', 'streaming_mumu')),
          lastErrorKnown: false,
        },
      }),
    ).toBe('unknown');
    expect(projectPreview({ ...healthy, frame: null })).toBe('unknown');
    expect(projectPreview({ ...healthy, report: null })).toBe('unknown');
    expect(projectPreview({ ...healthy, statusFailed: true })).toBe('unknown');
    expect(projectPreview({ ...healthy, failed: true })).toBe('failed');
    expect(projectPreview({ ...healthy, now: 4001 })).toBe('stale');
    expect(
      projectPreview({
        ...healthy,
        report: previewReport(previewBody('preview_status', 'failed_mumu')),
      }),
    ).toBe('failed');
    expect(
      projectPreview({
        ...healthy,
        report: previewReport(previewBody('preview_status', 'closed_mumu')),
      }),
    ).toBe('failed');
    expect(projectPreview({ ...healthy, frame: { ...displayed, kind: 'error' } })).toBe('failed');
    expect(projectPreview({ ...healthy, frame: { ...displayed, backend: 'unknown' } })).toBe(
      'unknown',
    );
    expect(
      projectPreview({
        ...healthy,
        report: previewReport(previewBody('preview_status', 'stale_mumu')),
      }),
    ).toBe('stale');
    const fallback = previewReport(previewBody('preview_status', 'fallback_mumu_to_scrcpy'));
    expect(
      projectPreview({ ...healthy, report: fallback, frame: { ...displayed, backend: 'scrcpy' } }),
    ).toBe('fallback');
  });
  it('projects receipt and server age between reads with the fractional-FPS threshold', () => {
    expect(previewThreshold(15)).toBe(3);
    expect(previewThreshold(0.5)).toBe(4);
    expect(projectPreview({ ...healthy, now: 4000 })).toBe('receiving');
    expect(projectPreview({ ...healthy, fps: 0.5, now: 5000 })).toBe('receiving');
    expect(projectPreview({ ...healthy, fps: 0.5, now: 5001 })).toBe('stale');
    expect(projectPreview({ ...healthy, reportAt: -2100 })).toBe('unknown');
  });
  it('preserves measured nulls and real zero, requested vs actual, and the ADB cap', () => {
    const recovered = previewReport(
      previewBody('preview_status', 'error_marker_status_after_recovery'),
    );
    expect(recovered.fallback).toBe(false);
    expect(
      projectPreview({
        ...healthy,
        report: recovered,
        frame: { ...displayed, sequence: 1, backend: 'adb' },
      }),
    ).toBe('receiving');
    const idle = previewReport(previewBody('preview_status', 'idle_reserved'));
    expect(idle.actualFps).toBeNull();
    expect(idle.width).toBeNull();
    expect(idle.frames).toBe(0);
    expect(previewReport(previewBody('preview_status', 'stale_adb')).actualFps).toBe(0);
    const adb = previewReport(previewBody('preview_status', 'fallback_scrcpy_to_adb'));
    expect(adb.requested).toBe('scrcpy');
    expect(adb.backend).toBe('adb');
    expect(adb.effectiveFps).toBe(4);
    expect(previewEffectiveFps(15, 'adb')).toBe(4);
    expect(previewEffectiveFps(2, 'adb')).toBe(2);
  });
  it('owns exact presets, explicit Auto URL, safe IDs, and viewer preferences only', () => {
    expect(PREVIEW_PRESETS).toEqual({
      native: { scale: 1, q: 90 },
      balanced: { scale: 0.65, q: 85 },
      low: { scale: 0.4, q: 70 },
    });
    const prefs = previewPreferences({
      source: 'auto',
      quality: 'balanced',
      fps: 0.5,
      running: true,
    });
    expect(prefs).toEqual({ source: 'auto', quality: 'balanced', fps: 0.5 });
    expect(previewPreferences(null).quality).toBe('balanced');
    expect(previewUrl('main', 'pv1', 15, prefs)).toBe(
      '/api/stream.mjpg?instance=main&stream_id=pv1&fps=15&scale=0.65&q=85&backend=auto',
    );
    expect(previewStreamId('!'.repeat(70))).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
    expect(previewStreamId('a'.repeat(100))).toHaveLength(64);
    expect(PREVIEW_FPS).toEqual([0.5, 1, 2, 4, 10, 15, 30]);
    const saved = previewAvailability(
      previewBody('preview_backends', 'saved_scrcpy_fractional_fps'),
    );
    expect(saved.savedSource).toBe('scrcpy');
    expect(saved.fps).toBe(12.5);
    expect(previewPreferences({ fps: 12.5 }).fps).toBe(12.5);
  });
});
