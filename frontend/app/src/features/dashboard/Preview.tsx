import { useCallback, useEffect, useRef } from 'react';
import { useAnnounce } from '../../ui/LiveRegion.tsx';
import {
  previewCopy as c,
  previewNumber,
  previewFpsOption,
  previewSavedFps,
  previewReceiptAge,
  previewSourceAge,
  previewReportAge,
  previewDimensions,
  previewMillis,
  previewSeconds,
} from '../../copy/preview.ts';
import {
  previewSource,
  previewQuality,
  previewFps,
  type PreviewVerdict,
} from '../../core/preview.ts';
import { selectPreview } from '../../store/selectors/preview.ts';
import type { PreviewStore } from '../../store/preview.ts';
import { useStoreSelector, viewEqual } from '../../store/store.ts';
import { Chip, type ChipState } from '../../ui/Chip.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { RegionBoundary } from '../../ui/RegionBoundary.tsx';
import { useDashboard, useEpochKey } from './context.ts';
import { WhyTip } from './shared/WhyTip.tsx';
import './preview.css';

/** Each delivery verdict's status tone: the chip shape and colour that go with its words. */
const VERDICT_TONES: Readonly<Record<PreviewVerdict, ChipState>> = {
  off: 'idle',
  connection: 'unknown',
  connecting: 'waiting',
  receiving: 'running',
  fallback: 'running',
  stale: 'waiting',
  failed: 'failed',
  unknown: 'unknown',
};

function PreviewViewer({ store }: { readonly store: PreviewStore }) {
  const v = useStoreSelector(store, selectPreview, viewEqual);
  const announce = useAnnounce();
  const attach = useCallback(
    (canvas: HTMLCanvasElement | null) => {
      store.attach(canvas);
    },
    [store],
  );
  // The preview now attaches by itself, so its start-up is silent. After the person's first
  // press or edit on this page, verdict changes are announced for the rest of the visit.
  const touched = useRef(false);
  const touch = () => {
    touched.current = true;
  };
  useEffect(() => {
    if (v.expanded && touched.current) announce(c.verdicts[v.verdict].short);
  }, [announce, v.expanded, v.verdict]);
  useEffect(() => {
    store.present(true);
    return () => {
      store.present(false);
    };
  }, [store]);
  const report = v.report;
  return (
    <aside
      className="tp-preview tp-region"
      data-region="preview"
      aria-labelledby="preview-title"
      data-verdict={v.verdict}
    >
      <h2 id="preview-title" className="tp-region__title">
        {c.title.short}
      </h2>
      <HelpTip label={c.title.short}>
        <p>{c.viewOnly.short}</p>
        <p>{c.viewOnly.why}</p>
      </HelpTip>
      {v.expanded ? (
        <span className="tp-preview__verdict">
          <span data-preview-verdict>
            <Chip state={VERDICT_TONES[v.verdict]} label={c.verdicts[v.verdict].short} />
          </span>
          {v.verdict === 'unknown' ? (
            <WhyTip subject={c.verdicts.unknown.short} why={c.unknownDelivery.short} />
          ) : null}
        </span>
      ) : null}
      <button
        type="button"
        className="tp-button"
        data-tier="secondary"
        aria-expanded={v.expanded}
        aria-controls="preview-content"
        onClick={() => {
          touch();
          store.expand(!v.expanded);
        }}
      >
        {v.expanded ? c.hide.short : c.show.short}
      </button>
      {v.expanded ? (
        <div id="preview-content" className="tp-preview__content">
          <div className="tp-preview__status">
            <div className="tp-preview__actions">
              {v.running ? (
                <button
                  type="button"
                  className="tp-button"
                  data-tier="secondary"
                  onClick={() => {
                    touch();
                    store.stop();
                  }}
                >
                  {c.stop.short}
                </button>
              ) : (
                <button
                  type="button"
                  className="tp-button"
                  data-tier="secondary"
                  disabled={!v.canStart}
                  aria-describedby={v.offline ? 'preview-connection' : undefined}
                  onClick={() => {
                    touch();
                    void store.start();
                  }}
                >
                  {c.start.short}
                </button>
              )}
              <button
                type="button"
                className="tp-button"
                data-tier="secondary"
                disabled={!v.canStart || v.checking}
                onClick={() => {
                  touch();
                  void store.start();
                }}
              >
                {c.reconnect.short}
              </button>
            </div>
            {v.offline ? (
              <p id="preview-connection">
                {v.stopped ? c.offlineStopped.short : c.offlineWaiting.short}
              </p>
            ) : null}
            {v.checking ? <p>{c.checking.short}</p> : null}
            {v.verdict === 'failed' ? <p>{c.transportFailed.short}</p> : null}
            {v.verdict === 'stale' ? <p>{c.stale.short}</p> : null}
            {v.lastError !== null ? <ServerValue kind="message" value={v.lastError} /> : null}
            {v.failures.map((reason, index) => (
              <div key={index}>
                <ServerValue kind="message" value={reason} />
              </div>
            ))}
          </div>
          <div
            className="tp-preview__picture"
            role={v.frame?.kind === 'frame' ? 'img' : undefined}
            aria-label={v.frame?.kind === 'frame' ? c.canvas.short : undefined}
          >
            <canvas ref={attach} aria-hidden="true" hidden={v.frame?.kind !== 'frame'} />
            {v.frame?.kind !== 'frame' ? <p>{c.empty.short}</p> : null}
          </div>
          <div className="tp-preview__meta">
            <div className="tp-preview__facts">
              <p>
                {c.labels.actual.short}
                {c.sources[v.actualSource].short}
              </p>
              <p>{previewReceiptAge(v.receiptAge)}</p>
              <p>{previewSourceAge(v.sourceAge)}</p>
            </div>
            <details className="tp-preview__settings">
              <summary>{c.settings.short}</summary>
              <label htmlFor="preview-fps">
                <span id="preview-fps-label">{c.fps.short}</span>
                <select
                  className="tp-field-control"
                  id="preview-fps"
                  aria-labelledby="preview-fps-label"
                  value={v.prefs.fps ?? ''}
                  onChange={(event) => {
                    touch();
                    store.edit({
                      ...v.prefs,
                      fps: previewFps(
                        event.target.value === '' ? null : Number(event.target.value),
                      ),
                    });
                  }}
                >
                  <option value="">{c.useSaved.short}</option>
                  {v.fpsOptions.map((fps) => (
                    <option key={fps} value={fps}>
                      {previewFpsOption(fps)}
                    </option>
                  ))}
                </select>
              </label>
              <p>{previewSavedFps(v.savedFps)}</p>
              <label htmlFor="preview-source">
                <span id="preview-source-label">{c.source.short}</span>
                <select
                  className="tp-field-control"
                  id="preview-source"
                  aria-labelledby="preview-source-label"
                  value={v.prefs.source}
                  onChange={(event) => {
                    const source = previewSource(event.target.value);
                    touch();
                    if (source !== null) store.edit({ ...v.prefs, source });
                  }}
                >
                  <option value="auto">{c.sources.auto.short}</option>
                  <option value="mumu">{c.sources.mumu.short}</option>
                  <option value="scrcpy">{c.sources.scrcpy.short}</option>
                  <option value="adb">{c.sources.adb.short}</option>
                </select>
              </label>
              <p>
                {c.labels.saved.short}
                {v.savedSource === null ? c.saved.short : c.sources[v.savedSource].short}
              </p>
              <label htmlFor="preview-quality">
                <span id="preview-quality-label">{c.quality.short}</span>
                <select
                  className="tp-field-control"
                  id="preview-quality"
                  aria-labelledby="preview-quality-label"
                  value={v.prefs.quality}
                  onChange={(event) => {
                    const quality = previewQuality(event.target.value);
                    touch();
                    if (quality !== null) store.edit({ ...v.prefs, quality });
                  }}
                >
                  <option value="native">{c.qualities.native.short}</option>
                  <option value="balanced">{c.qualities.balanced.short}</option>
                  <option value="low">{c.qualities.low.short}</option>
                </select>
              </label>
              <button
                type="button"
                className="tp-button"
                data-tier="secondary"
                disabled={v.checking || v.running}
                aria-describedby={v.running ? 'preview-check-reason' : undefined}
                onClick={() => {
                  void store.check();
                }}
              >
                {c.check.short}
              </button>
              {v.running ? <p id="preview-check-reason">{c.checkBusy.short}</p> : null}
              {v.availabilityFailed ? <p>{c.availabilityFailed.short}</p> : null}
              {v.availabilityError !== null ? (
                <ServerValue kind="message" value={v.availabilityError} />
              ) : null}
              {v.sources.map((source) => (
                <div key={source.source}>
                  <span>{c.sources[source.source].short}</span>
                  <span>
                    {source.available === null
                      ? c.unknown.short
                      : source.available
                        ? c.available.short
                        : c.unavailable.short}
                  </span>
                  {source.reason !== null ? (
                    <ServerValue kind="message" value={source.reason} />
                  ) : null}
                </div>
              ))}
              {v.preferenceFailed ? <p>{c.storageFailed.short}</p> : null}
              <dl>
                <dt>{c.labels.requested.short}</dt>
                <dd>{c.sources[v.prefs.source].short}</dd>
                <dt>{c.labels.target.short}</dt>
                <dd>{previewNumber(v.fps ?? v.prefs.fps ?? v.savedFps)}</dd>
                <dt>{c.labels.effective.short}</dt>
                <dd>{previewNumber(v.effective)}</dd>
                <dt>{c.labels.rate.short}</dt>
                <dd>{previewNumber(report?.actualFps ?? null)}</dd>
                <dt>{c.labels.bytes.short}</dt>
                <dd>{previewNumber(report?.jpegBytes ?? null)}</dd>
                <dt>{c.labels.dimensions.short}</dt>
                <dd>{previewDimensions(report?.width ?? null, report?.height ?? null)}</dd>
                <dt>{c.labels.capture.short}</dt>
                <dd>{previewMillis(report?.captureMs ?? null)}</dd>
                <dt>{c.labels.encode.short}</dt>
                <dd>{previewMillis(report?.encodeMs ?? null)}</dd>
                <dt>{c.labels.errors.short}</dt>
                <dd>{previewNumber(report?.errors ?? null)}</dd>
                <dt>{c.labels.frames.short}</dt>
                <dd>{previewNumber(report?.frames ?? null)}</dd>
                <dt>{c.labels.report.short}</dt>
                <dd>{previewReportAge(v.reportAge)}</dd>
                <dt>{c.labels.serverAge.short}</dt>
                <dd>{previewSeconds(report?.serverAge ?? null)}</dd>
              </dl>
              <a href="#emulators">{c.emulators.short}</a>
            </details>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
export function Preview() {
  const { preview } = useDashboard();
  const epoch = useEpochKey();
  return preview === undefined ? null : (
    <RegionBoundary resetKey={epoch} fallback={<p>{c.transportFailed.short}</p>}>
      <PreviewViewer store={preview} />
    </RegionBoundary>
  );
}
