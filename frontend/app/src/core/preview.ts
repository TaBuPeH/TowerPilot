import { finite, list, own, ownPath, text } from './read.ts';

export type PreviewSource = 'auto' | 'mumu' | 'scrcpy' | 'adb';
export type ActualSource = Exclude<PreviewSource, 'auto'> | 'unknown';
export type PreviewQuality = 'native' | 'balanced' | 'low';
export interface PreviewPreferences {
  readonly fps: number | null;
  readonly source: PreviewSource;
  readonly quality: PreviewQuality;
}
export const PREVIEW_PRESETS = {
  native: { scale: 1, q: 90 },
  balanced: { scale: 0.65, q: 85 },
  low: { scale: 0.4, q: 70 },
};
export const PREVIEW_FPS = [0.5, 1, 2, 4, 10, 15, 30];
export function previewSource(value: unknown): PreviewSource | null {
  return value === 'auto' || value === 'mumu' || value === 'scrcpy' || value === 'adb'
    ? value
    : null;
}
export function previewQuality(value: unknown): PreviewQuality | null {
  return value === 'native' || value === 'balanced' || value === 'low' ? value : null;
}
export function previewFps(value: unknown): number | null {
  const n = finite(value);
  return n !== undefined && n >= 0.5 && n <= 30 ? n : null;
}
export function previewPreferences(raw: unknown): PreviewPreferences {
  return {
    fps: previewFps(own(raw, 'fps')),
    source: previewSource(own(raw, 'source')) ?? 'auto',
    quality: previewQuality(own(raw, 'quality')) ?? 'balanced',
  };
}
export const previewThreshold = (fps: number): number => Math.max(3, 2 / fps);
export const previewEffectiveFps = (fps: number, source: ActualSource): number =>
  source === 'adb' ? Math.min(4, fps) : fps;
export function previewStreamId(random: string): string {
  const safe = random.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 61);
  return `pv_${safe || 'session'}`;
}
export function previewUrl(
  instance: string,
  streamId: string,
  fps: number,
  prefs: PreviewPreferences,
): `/api/${string}` {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(streamId) || previewFps(fps) === null)
    throw new Error('Invalid preview session');
  const preset = PREVIEW_PRESETS[prefs.quality];
  return `/api/stream.mjpg?instance=${encodeURIComponent(instance)}&stream_id=${streamId}&fps=${fps}&scale=${preset.scale}&q=${preset.q}&backend=${prefs.source}`;
}
export interface PreviewPartIdentity {
  readonly kind: 'frame' | 'error';
  readonly sequence: number;
  readonly backend: ActualSource;
  readonly sourceAgeMs: number | null;
}
export interface DisplayedPreview extends PreviewPartIdentity {
  readonly receivedAt: number;
}
export interface PreviewReport {
  readonly streamId: string | null;
  readonly instance: string | null;
  readonly targetFps: number | null;
  readonly actualFps: number | null;
  readonly effectiveFps: number | null;
  readonly frames: number | null;
  readonly errors: number | null;
  readonly width: number | null;
  readonly height: number | null;
  readonly jpegBytes: number | null;
  readonly captureMs: number | null;
  readonly encodeMs: number | null;
  readonly serverAge: number | null;
  readonly active: boolean | null;
  readonly lastError: string | null;
  readonly lastErrorKnown: boolean;
  readonly backend: ActualSource;
  readonly requested: PreviewSource | null;
  readonly failures: readonly string[];
  readonly fallback: boolean;
}
function nonnegative(value: unknown): number | null {
  const n = finite(value);
  return n !== undefined && n >= 0 ? n : null;
}
export function previewReport(raw: unknown): PreviewReport {
  const backend = previewSource(own(raw, 'backend'));
  const requested = previewSource(own(raw, 'requested_backend'));
  const failures = list(own(raw, 'backend_failures')) ?? [];
  const active = own(raw, 'active');
  return {
    streamId: text(own(raw, 'stream_id')) ?? null,
    instance: text(own(raw, 'instance')) ?? null,
    targetFps: previewFps(own(raw, 'target_fps')),
    actualFps: nonnegative(own(raw, 'actual_fps')),
    effectiveFps: nonnegative(own(raw, 'effective_fps')),
    frames: nonnegative(own(raw, 'frames')),
    errors: nonnegative(own(raw, 'errors')),
    width: nonnegative(own(raw, 'width')),
    height: nonnegative(own(raw, 'height')),
    jpegBytes: nonnegative(own(raw, 'jpeg_bytes')),
    captureMs: nonnegative(own(raw, 'capture_ms')),
    encodeMs: nonnegative(own(raw, 'encode_ms')),
    serverAge: nonnegative(own(raw, 'last_frame_age_s')),
    active: typeof active === 'boolean' ? active : null,
    lastError: text(own(raw, 'last_error')) ?? null,
    lastErrorKnown: own(raw, 'last_error') === null || typeof own(raw, 'last_error') === 'string',
    backend: backend === null || backend === 'auto' ? 'unknown' : backend,
    requested,
    fallback:
      requested === 'auto'
        ? failures.some(
            (f) => own(f, 'stage') === 'capture' && previewSource(own(f, 'backend')) !== backend,
          )
        : requested !== null && requested !== backend,
    failures: failures.flatMap((f) => {
      const reason = text(own(f, 'reason'));
      return reason === undefined ? [] : [reason];
    }),
  };
}
export interface PreviewAvailability {
  readonly instance: string | null;
  readonly fps: number | null;
  readonly savedSource: PreviewSource | null;
  readonly ok: boolean;
  readonly error: string | null;
  readonly sources: readonly {
    readonly source: ActualSource;
    readonly available: boolean | null;
    readonly reason: string | null;
  }[];
}
export function previewAvailability(raw: unknown): PreviewAvailability {
  return {
    instance: text(own(raw, 'instance')) ?? null,
    fps: previewFps(ownPath(raw, 'settings', 'fps')),
    savedSource: previewSource(ownPath(raw, 'settings', 'backend')),
    ok: own(raw, 'ok') === true,
    error: text(own(raw, 'error')) ?? null,
    sources: ['mumu', 'scrcpy', 'adb'].map((source) => {
      const value = ownPath(raw, source, 'available');
      return {
        source: source === 'mumu' ? 'mumu' : source === 'scrcpy' ? 'scrcpy' : 'adb',
        available: typeof value === 'boolean' ? value : null,
        reason: text(ownPath(raw, source, 'reason')) ?? null,
      };
    }),
  };
}
export type PreviewVerdict =
  'off' | 'connection' | 'connecting' | 'receiving' | 'fallback' | 'stale' | 'failed' | 'unknown';
export interface PreviewProjectionInput {
  readonly running: boolean;
  readonly connected: boolean;
  readonly failed: boolean;
  readonly frame: DisplayedPreview | null;
  readonly report: PreviewReport | null;
  readonly reportAt: number | null;
  readonly statusFailed: boolean;
  readonly now: number;
  readonly fps: number | null;
}
export function projectPreview(s: PreviewProjectionInput): PreviewVerdict {
  if (!s.running) return s.connected ? 'off' : 'connection';
  if (s.failed || s.frame?.kind === 'error' || s.report?.lastError || s.report?.active === false)
    return 'failed';
  const threshold = previewThreshold(s.fps ?? 0.5);
  if (s.frame !== null && (s.now - s.frame.receivedAt) / 1000 > threshold) return 'stale';
  if (s.statusFailed || (s.reportAt !== null && (s.now - s.reportAt) / 1000 > threshold))
    return 'unknown';
  if (
    s.report?.serverAge !== null &&
    s.report?.serverAge !== undefined &&
    s.report.serverAge + (s.now - (s.reportAt ?? s.now)) / 1000 > threshold
  )
    return 'stale';
  if (s.frame === null)
    return s.report === null || s.report.frames === 0 ? 'connecting' : 'unknown';
  if (
    s.frame.backend === 'unknown' ||
    s.report === null ||
    s.report.frames === null ||
    s.report.frames < s.frame.sequence ||
    s.report.frames <= 0 ||
    s.report.active !== true ||
    s.report.serverAge === null ||
    !s.report.lastErrorKnown ||
    s.report.requested === null ||
    s.report.backend !== s.frame.backend
  )
    return 'unknown';
  return s.report.fallback ? 'fallback' : 'receiving';
}
