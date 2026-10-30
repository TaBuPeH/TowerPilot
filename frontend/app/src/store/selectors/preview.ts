import { fromServer } from '../../copy/server.ts';
import { projectPreview, PREVIEW_FPS, previewEffectiveFps } from '../../core/preview.ts';
import { connectionView } from '../../core/connection.ts';
import { contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import type { PreviewState, PreviewTarget } from '../preview.ts';

export function selectPreviewTarget(s: DashboardState): PreviewTarget {
  const read = latestRead(s, 'status');
  return {
    epoch: s.epoch,
    key: s.contextConfirmed && s.context !== null ? JSON.stringify(s.context) : null,
    instance: s.contextConfirmed ? (s.context?.instance ?? null) : null,
    connected:
      s.contextConfirmed &&
      s.context !== null &&
      read?.epoch === s.epoch &&
      connectionView(contextReadOf(read), readAgeSec(s, read)).line === 'connected',
    hidden: s.hidden,
  };
}
export function selectPreview(s: PreviewState) {
  const report = s.report;
  const savedFps = s.availability?.fps ?? null;
  const options = [
    ...new Set([
      ...PREVIEW_FPS,
      ...(savedFps === null ? [] : [savedFps]),
      ...(s.prefs.fps === null ? [] : [s.prefs.fps]),
    ]),
  ].sort((a, b) => a - b);
  return {
    ...s,
    verdict: projectPreview({ ...s, connected: s.target.connected }),
    offline: !s.target.connected,
    canStart: s.target.connected && !s.target.hidden && s.target.instance !== null,
    savedSource: s.availability?.savedSource ?? null,
    savedFps,
    fpsOptions: options,
    actualSource: s.frame?.backend ?? 'unknown',
    receiptAge: s.frame === null ? null : Math.max(0, (s.now - s.frame.receivedAt) / 1000),
    sourceAge: s.frame?.sourceAgeMs ?? null,
    reportAge: s.reportAt === null ? null : Math.max(0, (s.now - s.reportAt) / 1000),
    effective:
      report?.effectiveFps ??
      (s.fps === null || s.frame === null || s.frame.backend === 'unknown'
        ? null
        : previewEffectiveFps(s.fps, s.frame.backend)),
    lastError: fromServer('message', report?.lastError),
    failures:
      report?.failures.flatMap((value) => {
        const message = fromServer('message', value);
        return message === null ? [] : [message];
      }) ?? [],
    availabilityError: fromServer('message', s.availability?.error),
    sources:
      s.availability?.sources.map((source) => ({
        ...source,
        reason: fromServer('message', source.reason),
      })) ?? [],
  };
}
