import { useRef, useState } from 'react';
import { recognitionAdvanced as t } from '../../../copy/recognitionAdvanced.ts';
import { dragRect, type CropDraft } from '../../../core/recognitionImages.ts';
import type {
  RecognitionAdvancedState,
  RecognitionAdvancedStore,
} from '../../../store/recognitionAdvanced.ts';
import { selectAdvanced } from '../../../store/selectors/recognitionAdvanced.ts';
import { ActionRow, Reason } from '../../../ui/ActionRow.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { CopyLine } from '../../../ui/CopyLine.tsx';
import { Button } from '../../../ui/Button.tsx';
import { ConfirmDialog } from '../../../ui/ConfirmDialog.tsx';
import { Data, Dimensions, Values } from './Data.tsx';
const fields = ['x', 'y', 'w', 'h'] as const;
export function CropEditor({
  store,
  state,
  now,
}: {
  readonly store: RecognitionAdvancedStore;
  readonly state: RecognitionAdvancedState;
  readonly now: number;
}) {
  const vm = selectAdvanced(state, now);
  const target = vm.target;
  const frame = target?.frame ?? null;
  const draft = target?.draft;
  const [fullSize, setFullSize] = useState(false);
  const start = useRef<{ x: number; y: number; token: string } | null>(null);
  const [replacement, setReplacement] = useState<{
    key: string;
    epoch: number;
    name: string;
    token: string;
    draft: CropDraft;
  } | null>(null);
  const answer = vm.crop;
  const exists = vm.exists;
  const unavailable =
    state.context === null || !state.online || target?.checking === true || vm.cropPending;
  const reasonEntry =
    state.context === null
      ? t.noTarget
      : !state.online
        ? t.offline
        : target?.checking === true
          ? t.checking
          : vm.cropPending
            ? t.pending
            : vm.cropCheck.kind === 'invalid'
              ? t.validation[vm.cropCheck.reason]
              : null;
  const reason = reasonEntry?.short ?? null;
  const takeBlocked = unavailable || target?.frameBusy === true;
  const validation = vm.cropCheck.kind === 'invalid' ? vm.cropCheck.reason : null;
  const validationIsReason = validation !== null && reasonEntry === t.validation[validation];
  const overlay = vm.overlay;
  return (
    <>
      <p>{t.still.short}</p>
      <CopyLine entry={t.drag} variant="note" />
      <ActionRow
        reason={takeBlocked ? <Reason id="frame-blocked" entry={reasonEntry ?? t.reading} /> : null}
      >
        <Button
          tier="secondary"
          label={t.takeFrame.short}
          onClick={() => {
            void store.takeFrame();
          }}
          {...(takeBlocked ? { disabled: true, reasonId: 'frame-blocked' } : {})}
        />
      </ActionRow>
      {target?.frameError === true ? <p role="status">{t.frameError.short}</p> : null}
      {target?.contextError === true ? <p role="status">{t.contextError.short}</p> : null}
      {frame !== null ? (
        <>
          <p>{t.frameAge(Math.max(0, Math.floor((now - frame.takenAt) / 1000)))}</p>
          <Facts layout="cells">
            <Fact label={t.native.short} value={<Dimensions value={frame.size} />} />
          </Facts>
          <Button
            tier="quiet"
            label={fullSize ? t.fitFrame.short : t.fullSize.short}
            onClick={() => {
              setFullSize((v) => !v);
            }}
          />
          <div
            className="tp-advanced__frame-scroll"
            role="region"
            aria-label={t.frameAlt.short}
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- The scroll viewport needs keyboard focus (WCAG 2.1.1), verified in Playwright.
            tabIndex={0}
          >
            <div
              className="tp-advanced__crop-frame"
              data-full-size={fullSize}
              onPointerDown={(e) => {
                if (frame.size === null) return;
                const b = e.currentTarget.getBoundingClientRect();
                start.current = { x: e.clientX - b.left, y: e.clientY - b.top, token: frame.token };
                e.currentTarget.setPointerCapture(e.pointerId);
              }}
              onPointerMove={(e) => {
                const a = start.current;
                if (a === null || a.token !== frame.token || frame.size === null) return;
                const b = e.currentTarget.getBoundingClientRect();
                store.draw(
                  dragRect(
                    a,
                    { x: e.clientX - b.left, y: e.clientY - b.top },
                    { width: b.width, height: b.height },
                    frame.size,
                  ),
                );
              }}
              onPointerUp={(e) => {
                const a = start.current;
                if (a !== null && a.token === frame.token && frame.size !== null) {
                  const b = e.currentTarget.getBoundingClientRect();
                  store.draw(
                    dragRect(
                      a,
                      { x: e.clientX - b.left, y: e.clientY - b.top },
                      { width: b.width, height: b.height },
                      frame.size,
                    ),
                  );
                }
                start.current = null;
              }}
              onPointerCancel={() => {
                start.current = null;
              }}
            >
              <img
                key={frame.token}
                src={frame.src}
                alt={t.frameAlt.short}
                width={frame.size?.width}
                height={frame.size?.height}
                draggable={false}
                onLoad={(e) => {
                  store.frameLoaded(frame.token, {
                    width: e.currentTarget.naturalWidth,
                    height: e.currentTarget.naturalHeight,
                  });
                }}
                onError={() => {
                  store.frameFailed(frame.token);
                }}
              />
              {frame.size !== null ? (
                <svg
                  aria-hidden="true"
                  viewBox={`0 0 ${frame.size.width} ${frame.size.height}`}
                  preserveAspectRatio="none"
                >
                  {overlay === null ? null : (
                    <rect x={overlay.x} y={overlay.y} width={overlay.w} height={overlay.h} />
                  )}
                </svg>
              ) : null}
            </div>
          </div>
        </>
      ) : null}
      <label className="tp-advanced__field">
        {t.target.short}
        <input
          className="tp-field-control"
          id="recognition-crop-name"
          value={draft?.name ?? ''}
          aria-invalid={validation === 'name'}
          aria-describedby={
            validation === 'name' ? 'crop-target-hint crop-validation' : 'crop-target-hint'
          }
          onChange={(e) => {
            store.edit('name', e.target.value);
          }}
        />
      </label>
      <p id="crop-target-hint" className="tp-advanced__hint">
        {t.targetHint.short}
      </p>
      {validation === 'name' ? <p id="crop-validation">{t.validation.name.short}</p> : null}
      <div className="tp-advanced__coordinates">
        {fields.map((field) => (
          <label className="tp-advanced__field" key={field}>
            {t[field].short}
            <input
              className="tp-field-control"
              inputMode="numeric"
              value={draft?.[field] ?? ''}
              aria-invalid={validation === 'numbers' || validation === 'bounds'}
              aria-describedby={
                validation === 'numbers' || validation === 'bounds' ? 'crop-validation' : undefined
              }
              onChange={(e) => {
                store.edit(field, e.target.value);
              }}
              onKeyDown={(e) => {
                const value = draft?.[field];
                if (
                  (e.key === 'ArrowUp' || e.key === 'ArrowDown') &&
                  value !== undefined &&
                  /^\d+$/u.test(value)
                ) {
                  e.preventDefault();
                  store.edit(field, String(Number(value) + (e.key === 'ArrowUp' ? 1 : -1)));
                }
              }}
            />
          </label>
        ))}
      </div>
      {validation !== null && validation !== 'name' ? (
        <p id="crop-validation">{t.validation[validation].short}</p>
      ) : null}
      <ActionRow
        reason={
          reason !== null && !validationIsReason ? (
            <Reason id="crop-save-blocked" entry={reasonEntry ?? t.reading} />
          ) : null
        }
      >
        <Button
          tier="secondary"
          label={exists ? t.replace.short : t.save.short}
          onClick={() => {
            if (exists && draft !== undefined && frame !== null && state.key !== null)
              setReplacement({
                key: state.key,
                epoch: state.epoch,
                name: draft.name,
                token: frame.token,
                draft: { ...draft },
              });
            else void store.save();
          }}
          {...(reason !== null
            ? {
                disabled: true,
                reasonId: validationIsReason ? 'crop-validation' : 'crop-save-blocked',
              }
            : {})}
        />
      </ActionRow>
      <ConfirmDialog
        open={
          replacement !== null && replacement.key === state.key && replacement.epoch === state.epoch
        }
        title={t.replace.short}
        body={
          <>
            <p>{t.replaceBody.short}</p>
            <p>{replacement === null ? null : t.replacementName(replacement.name)}</p>
          </>
        }
        confirmLabel={t.replaceConfirm.short}
        cancelLabel={t.cancel.short}
        tier="primary"
        onCancel={() => {
          setReplacement(null);
        }}
        onConfirm={() => {
          const approved = replacement;
          setReplacement(null);
          if (approved !== null && approved.key === state.key && approved.epoch === state.epoch)
            void store.save(approved);
        }}
      />
      {answer.kind !== 'none' ? (
        <div role="status">
          <p>
            {answer.kind === 'written'
              ? t.written.short
              : answer.kind === 'refused'
                ? t.refused.short
                : answer.kind === 'sending'
                  ? t.sending.short
                  : t.unknown.short}
          </p>
          {answer.message !== null ? <Data value={answer.message} /> : null}
          {answer.kind === 'refused' ? (
            <>
              <Data value={answer.code} />
              <Data value={answer.error} />
            </>
          ) : null}
          {answer.kind === 'written' ? (
            <>
              <p>
                <Data value={answer.rel} />
              </p>
              <Facts layout="cells">
                <Fact
                  label={t.size.short}
                  value={<Values values={[answer.width, answer.height]} />}
                />
                <Fact label={t.secondBest.short} value={<Data value={answer.secondBest} />} />
              </Facts>
            </>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
