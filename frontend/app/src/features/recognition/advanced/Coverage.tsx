import { useId, useState } from 'react';
import type { CopyNode } from '../../../copy/text.ts';
import { recognitionAdvanced as t } from '../../../copy/recognitionAdvanced.ts';
import type {
  RecognitionAdvancedState,
  RecognitionAdvancedStore,
} from '../../../store/recognitionAdvanced.ts';
import { selectAdvanced, type AdvancedView } from '../../../store/selectors/recognitionAdvanced.ts';
import { ActionRow, Reason } from '../../../ui/ActionRow.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { ShownList } from '../../../ui/ShownList.tsx';
import { Button } from '../../../ui/Button.tsx';
import { Coordinates, Data, Dimensions, Status, Values } from './Data.tsx';
/** Closed diagnostic groups do not mount the full catalogue or its image elements. */
function Disclosure({
  title,
  count,
  hint,
  children,
}: {
  readonly title: CopyNode;
  readonly count: number;
  readonly hint?: CopyNode;
  readonly children: CopyNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <details
      className="tp-disclosure tp-advanced__disclosure"
      onToggle={(e) => {
        setOpen(e.currentTarget.open);
      }}
    >
      <summary id={id}>
        <span>{title}</span>
        <span className="tp-disclosure__count">{count}</span>
        {hint === undefined ? null : <span className="tp-disclosure__preview">{hint}</span>}
      </summary>
      {open ? (
        <div
          className="tp-advanced__records-viewport"
          role="region"
          aria-labelledby={id}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- The bounded records viewport must support keyboard scrolling.
          tabIndex={0}
        >
          {children}
        </div>
      ) : null}
    </details>
  );
}
function Thumbnail({ src }: { readonly src: string }) {
  const [failed, setFailed] = useState(false);
  return failed ? (
    <p role="status">{t.libraryError.short}</p>
  ) : (
    <img
      src={src}
      alt={t.library.short}
      loading="lazy"
      onError={() => {
        setFailed(true);
      }}
    />
  );
}
function Library({ folder, images }: Pick<AdvancedView['library'][number], 'folder' | 'images'>) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="tp-disclosure"
      onToggle={(e) => {
        setOpen(e.currentTarget.open);
      }}
    >
      <summary>
        <Data value={folder} />
        <span className="tp-disclosure__count">{images.length}</span>
      </summary>
      {open ? (
        <ShownList
          items={images}
          itemKey={(image) => image.key}
          more={t.more.short}
          className="tp-advanced__library"
          render={(image) => (
            <>
              <Data value={image.name} />
              {image.src === null ? null : <Thumbnail key={image.src} src={image.src} />}
            </>
          )}
        />
      ) : null}
    </details>
  );
}
export function Coverage({
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
  return (
    <>
      <ActionRow
        reason={
          state.context === null || target?.busy === true ? (
            <Reason id="coverage-blocked" entry={state.context === null ? t.noTarget : t.reading} />
          ) : null
        }
      >
        <Button
          tier="quiet"
          label={t.readCoverage.short}
          onClick={() => {
            void store.refresh();
          }}
          {...(state.context === null || target?.busy === true
            ? { disabled: true, reasonId: 'coverage-blocked' }
            : {})}
        />
      </ActionRow>
      {target?.readError === true ? <p role="status">{t.readError.short}</p> : null}
      {vm.stale ? <p>{t.stale.short}</p> : null}
      <p>{t.missingWarning.short}</p>
      <p>
        <Data value={vm.plan.note} />
      </p>
      <Disclosure title={t.mapping.short} count={vm.plan.mapping.length}>
        <table className="tp-advanced__mapping" aria-label={t.mapping.short}>
          <thead>
            <tr>
              <th scope="col">{t.source.short}</th>
              <th scope="col">{t.destination.short}</th>
              <th scope="col">{t.action.short}</th>
              <th scope="col">{t.status.short}</th>
            </tr>
          </thead>
          <tbody>
            {vm.plan.mapping.map((s) => (
              <tr key={s.id}>
                <td>
                  <span className="tp-advanced__mapping-label" aria-hidden="true">
                    {t.source.short}
                  </span>
                  <Data value={s.source} />
                </td>
                <td>
                  <span className="tp-advanced__mapping-label" aria-hidden="true">
                    {t.destination.short}
                  </span>
                  <Data value={s.destination} />
                </td>
                <td>
                  <span className="tp-advanced__mapping-label" aria-hidden="true">
                    {t.action.short}
                  </span>
                  <Data value={s.action} />
                </td>
                <td>
                  <span className="tp-advanced__mapping-label" aria-hidden="true">
                    {t.status.short}
                  </span>
                  <Status value={s.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Disclosure>
      <Disclosure title={t.interface.short} count={vm.plan.interface.length}>
        <ShownList
          items={vm.plan.interface}
          itemKey={(s) => s.id}
          more={t.more.short}
          className="tp-advanced__records"
          render={(s) => (
            <>
              <details className="tp-disclosure">
                <summary>
                  <Data value={s.label} />
                  {t.space}
                  <Status value={s.status} />
                </summary>
                <Facts layout="cells">
                  <Fact label={t.verified.short} value={<Data value={s.verified} />} />
                  <Fact label={t.total.short} value={<Data value={s.total} />} />
                </Facts>
                <ShownList
                  items={s.controls}
                  itemKey={(control) => control.id}
                  more={t.more.short}
                  className="tp-advanced__records"
                  render={(control) => (
                    <>
                      <p>
                        <Data value={control.label} />
                      </p>
                      <Facts layout="cells">
                        <Fact label={t.status.short} value={<Status value={control.status} />} />
                        <Fact label={t.target.short} value={<Data value={control.template} />} />
                      </Facts>
                      {control.optional === true ? <p>{t.optional.short}</p> : null}
                    </>
                  )}
                />
              </details>
            </>
          )}
        />
      </Disclosure>
      <Disclosure title={t.plan.short} count={vm.plan.steps.length} hint={t.captureTargets.short}>
        <p>{t.optional.short}</p>
        <Facts layout="cells">
          <Fact label={t.native.short} value={<Dimensions value={vm.plan.native} />} />
        </Facts>
        {vm.plan.steps.map((s) => (
          <details key={s.id} className="tp-disclosure">
            <summary>
              <Data value={s.title} />
            </summary>
            <p>
              <Data value={s.instructions} />
            </p>
            <p>
              <Data value={s.mode} />
            </p>
            <ShownList
              items={s.targets}
              itemKey={(target) => target.key}
              more={t.more.short}
              className="tp-advanced__records"
              render={(target) => (
                <>
                  <p>
                    <Data value={target.label} />
                    <Data value={target.rel} />
                  </p>
                  <p>
                    <Status value={target.status} />
                  </p>
                  <p>
                    {target.required === null
                      ? t.unavailable.short
                      : target.required
                        ? t.required.short
                        : t.notRequired.short}
                    {t.space}
                    {target.automatic === null
                      ? t.unavailable.short
                      : target.automatic
                        ? t.automatic.short
                        : t.manual.short}
                  </p>
                  <Facts layout="cells">
                    <Fact
                      label={t.reference.short}
                      value={<Dimensions value={target.reference} />}
                    />
                  </Facts>
                  {target.learned !== null ? (
                    <p>
                      {t.learned.short}
                      <Coordinates value={target.learned} />
                      <Dimensions value={target.learnedSize} />
                    </p>
                  ) : null}
                  <ActionRow>
                    <Button
                      tier="quiet"
                      label={t.repair.short}
                      onClick={() => {
                        store.repair(target.repair);
                        document.getElementById('recognition-crop-title')?.focus();
                      }}
                    />
                  </ActionRow>
                </>
              )}
            />
          </details>
        ))}
      </Disclosure>
      <Disclosure title={t.requiredImages.short} count={vm.required.length}>
        <ShownList
          items={vm.required}
          itemKey={(r) => r.key}
          more={t.more.short}
          className="tp-advanced__records"
          render={(r) => (
            <>
              <p>
                <Data value={r.rel} />
              </p>
              <Facts layout="cells">
                <Fact label={t.feature.short} value={<Data value={r.feature} />} />
              </Facts>
              <p>
                {r.have === null ? t.unavailable.short : r.have ? t.have.short : t.missing.short}
              </p>
              <Facts layout="cells">
                <Fact label={t.note.short} value={<Data value={r.note} />} />
                <Fact label={t.usedBy.short} value={<Values values={r.usedBy} />} />
              </Facts>
              <ActionRow>
                <Button
                  tier="quiet"
                  label={t.repair.short}
                  onClick={() => {
                    store.repair(r.repair);
                    document.getElementById('recognition-crop-title')?.focus();
                  }}
                />
              </ActionRow>
            </>
          )}
        />
      </Disclosure>
      <Disclosure title={t.missingSeen.short} count={vm.missingSeen.length}>
        <Values values={vm.missingSeen} />
      </Disclosure>
      <Disclosure title={t.catalogue.short} count={vm.catalogue.length}>
        <ShownList
          items={vm.catalogue}
          itemKey={(m) => m.key}
          more={t.more.short}
          className="tp-advanced__records"
          render={(m) => (
            <>
              <Facts layout="cells">
                <Fact label={t.name.short} value={<Data value={m.name} />} />
                <Fact label={t.slug.short} value={<Data value={m.slug} />} />
                <Fact label={t.abbrevs.short} value={<Values values={m.abbrevs} />} />
              </Facts>
              {m.learned === null
                ? t.unavailable.short
                : m.learned
                  ? t.learnedFlag.short
                  : t.notLearned.short}
            </>
          )}
        />
      </Disclosure>
      <Disclosure title={t.library.short} count={vm.library.length}>
        {vm.library.map((g) => (
          <Library key={g.key} folder={g.folder} images={g.images} />
        ))}
      </Disclosure>
    </>
  );
}
