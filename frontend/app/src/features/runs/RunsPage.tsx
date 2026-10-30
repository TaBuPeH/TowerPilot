import { useEffect, useId } from 'react';
import type { PageTarget } from '../../ui/routes.ts';
import { copy } from '../../copy/catalogue.ts';
import type { CopyNode } from '../../copy/text.ts';
import { selectRunsPage } from '../../store/selectors/runs.ts';
import { editingTargetVM, selectEditingTarget } from '../../store/selectors/editingTarget.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow } from '../../ui/ActionRow.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { RegionBoundary } from '../../ui/RegionBoundary.tsx';
import { Region, RegionBody, RegionEnd, RegionInline } from '../../ui/Region.tsx';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';
import { Why } from '../../ui/Why.tsx';
import {
  FaultPoint,
  useDashboard,
  useEpochKey,
  usePartError,
  useView,
} from '../dashboard/context.ts';
import { RevocationFallback } from '../dashboard/shared/RevocationFallback.tsx';
import { TopBar } from '../dashboard/topBar/TopBar.tsx';
import { BattleDetails } from './BattleDetails.tsx';
import { RunEditor } from './RunEditor.tsx';
import { LiveShopping } from './LiveShopping.tsx';
import { RunEntry } from './RunEntry.tsx';
import { SavedShopping } from './SavedShopping.tsx';
import { AddRun } from './AddRun.tsx';
import { ShoppingLibrary } from './ShoppingLibrary.tsx';
import { RunOutcome } from './RunOutcome.tsx';
import { RunDefaults } from './RunDefaults.tsx';
import './runs.css';

const t = copy.runs;

/** The previous Runs page keeps the shopping queue and the entry settings (brief RUN-14, RUN-17). */
const RECOGNITION = '#recognition';

/** PE6: these forms keep their active values; the entry editor has its own target. */
function ActiveOnly({ children }: { readonly children: CopyNode }) {
  const target = useView(selectEditingTarget);
  const vm = editingTargetVM(target);
  const reasonId = useId();
  return (
    <fieldset
      className="tp-run-entry tp-run-entry__field"
      disabled={target.inactive}
      data-runs-active-only="true"
      aria-describedby={target.inactive ? reasonId : undefined}
    >
      {target.inactive ? (
        <>
          <legend className="tp-profiles__summary">
            <span>{copy.editingTarget.active.short}</span>
            {vm.active === null ? (
              copy.editingTarget.none.short
            ) : (
              <ServerValue kind="name" value={vm.active} />
            )}
          </legend>
          <div id={reasonId}>{copy.runEntry.activeOnly.short}</div>
        </>
      ) : null}
      <div>{children}</div>
    </fieldset>
  );
}

function LiveSection() {
  const vm = useView(selectRunsPage);
  const { commands } = useDashboard();
  const live = vm.live;
  const line = live.outcome;
  if (live.state !== 'tracked' || live.editor === null || !live.active) {
    const entry =
      live.state === 'tracked'
        ? t.live.prepared
        : live.state === 'none'
          ? t.live.none
          : t.live.unavailable;
    const check = (
      <Button
        tier="quiet"
        label={t.live.check.short}
        focusKey="runs:check-battle"
        onClick={commands.checkBattle}
      />
    );
    // F6R-04: the lines of a battle no longer shown stay, each naming its battle.
    return line === null && live.earlier.length === 0 && live.details.length === 0 ? (
      <RegionInline>
        <span>{entry.short}</span>
        {check}
      </RegionInline>
    ) : (
      <RegionBody>
        <ActionRow>
          <span>{entry.short}</span>
          {check}
        </ActionRow>
        {live.earlier.map((earlier) => (
          <RunOutcome key={earlier.line.id} outcome={earlier} />
        ))}
        {line === null ? null : <RunOutcome outcome={line} />}
        <BattleDetails details={live.details} lastKnown={live.detailsLastKnown} />
      </RegionBody>
    );
  }
  return (
    <>
      <RegionEnd>
        <span className="tp-runs__battle">
          {live.label === null ? null : <ServerValue kind="name" value={live.label} />}
          {live.wave === null ? null : <span>{copy.dashboard.thisBattle.wave(live.wave)}</span>}
        </span>
      </RegionEnd>
      <RegionBody>
        <Tile titleId="runs-live-routines" title={t.live.routines.short} intro={t.live.scope}>
          {live.boundsKnown ? null : (
            <div className="tp-runs__note">
              <span>{t.live.boundsUnknown.short}</span>
              <Why why={t.live.boundsUnknown.why} />
            </div>
          )}
          <RunEditor
            editor={live.editor}
            live
            saveLabel={t.live.save}
            onSave={(command) => {
              void commands.saveLive(command);
            }}
            extra={
              <Button
                tier="quiet"
                label={t.live.check.short}
                focusKey="runs:check-battle"
                onClick={commands.checkBattle}
              />
            }
          />
        </Tile>
        <LiveShopping />
        {line === null ? null : <RunOutcome outcome={line} />}
        {live.earlier.map((earlier) => (
          <RunOutcome key={earlier.line.id} outcome={earlier} />
        ))}
        <BattleDetails details={live.details} lastKnown={live.detailsLastKnown} />
      </RegionBody>
    </>
  );
}

function SavedSection() {
  const vm = useView(selectRunsPage);
  const { commands } = useDashboard();
  const saved = vm.saved;
  const line = saved.outcome;
  return (
    <>
      <RegionEnd>
        {saved.readAgeSec === null ? null : <span>{t.readAge(saved.readAgeSec)}</span>}
      </RegionEnd>
      <RegionBody>
        <Tile titleId="runs-saved-routines" title={t.saved.routines.short} intro={t.saved.scope}>
          <div className="tp-runs__picker tp-control-row">
            <label className="tp-runs__picker-label">
              <span>{t.saved.picker.short}</span>
              <select
                className="tp-field-control"
                value={saved.run ?? ''}
                data-focus-key="runs:run"
                onChange={(event) => {
                  commands.openRuns(true, event.currentTarget.value);
                }}
              >
                {saved.runs.length === 0 && saved.run === null ? (
                  <option value="">{t.saved.noRuns.short}</option>
                ) : null}
                {saved.run !== null && !saved.runListed && saved.runName !== null ? (
                  <ServerOption value={saved.run} label={saved.runName} />
                ) : null}
                {saved.runs.map((run) =>
                  run.label === null ? (
                    <option key={run.key} value={run.key}>
                      {t.saved.unnamedRun.short}
                    </option>
                  ) : (
                    <ServerOption key={run.key} value={run.key} label={run.label} />
                  ),
                )}
              </select>
            </label>
            {saved.readiness === null ? null : (
              <span className="tp-runs__readiness">
                {
                  copy.dashboard.startNext.readiness[
                    saved.readiness === 'images_missing' ? 'not_ready' : saved.readiness
                  ].short
                }
              </span>
            )}
            <a className="tp-button" data-tier="quiet" href={RECOGNITION}>
              {t.saved.prepare.short}
            </a>
          </div>
          <ActiveOnly>
            <AddRun />
          </ActiveOnly>
          {saved.run !== null && !saved.runListed ? (
            <p className="tp-runs__note" data-run-unlisted="true">
              {t.saved.notListed.short}
            </p>
          ) : null}
          {saved.comboListed ? <p className="tp-runs__note">{t.saved.combo.short}</p> : null}
          {saved.notice === null ? null : (
            <p className="tp-runs__note">
              <ServerValue kind="message" value={saved.notice} />
            </p>
          )}
          {saved.catalogueUnavailable ? (
            <div className="tp-runs__note" role="status">
              <p>{t.saved.catalogueUnavailable.short}</p>
              {saved.catalogueError === null ? null : (
                <ServerValue kind="message" value={saved.catalogueError} />
              )}
              <Button tier="quiet" label={t.saved.checkRuns.short} onClick={commands.checkRuns} />
            </div>
          ) : saved.read === 'not_read' ? (
            <p>{t.saved.reading.short}</p>
          ) : null}
          {saved.read === 'unavailable' ? (
            <p className="tp-runs__note">
              <span>{t.saved.unavailable.short}</span>
              {saved.readError === null ? null : (
                <ServerValue kind="message" value={saved.readError} />
              )}
              {/* SR-8: reading the run again stays possible while its read fails. */}
              <Button
                tier="quiet"
                label={t.saved.check.short}
                focusKey="runs:check"
                onClick={commands.checkRun}
              />
            </p>
          ) : null}
          <ActiveOnly>
            {saved.editor === null ? null : (
              <RunEditor
                editor={saved.editor}
                live={false}
                saveLabel={t.saved.save}
                onSave={(command) => {
                  void commands.saveRun(command);
                }}
                extra={
                  <Button
                    tier="quiet"
                    label={t.saved.check.short}
                    focusKey="runs:check"
                    onClick={commands.checkRun}
                  />
                }
              />
            )}
          </ActiveOnly>
        </Tile>
        <ActiveOnly>
          <SavedShopping />
        </ActiveOnly>
        <RunEntry />
        {line === null ? null : <RunOutcome outcome={line} />}
        <RunDefaults />
      </RegionBody>
    </>
  );
}

function RunsRegion({
  id,
  part,
  title,
  children,
}: {
  readonly id: string;
  readonly part: 'runsLive' | 'runsSaved';
  readonly title: typeof t.live.title;
  readonly children: CopyNode;
}) {
  return (
    <Region
      id={id}
      title={title.short}
      fallback={<RevocationFallback part={part} />}
      resetKey={useEpochKey()}
      onError={usePartError(part)}
    >
      <FaultPoint part={part} />
      {children}
    </Region>
  );
}

/**
 * The Runs page (brief RUN-01 to RUN-13): this battle's live settings, then
 * the saved settings of a run, each its own region with its own boundary
 * (a fallback keeps Pause harvesting and Turn input off). Opening it tells
 * the store which run to read; a route target focuses its section heading.
 */
export function RunsPage({
  target,
}: {
  /** The route's target (app/router.ts `#runs/live`, `#runs/saved/<run>`); others open nothing. */
  readonly target: PageTarget | null;
}) {
  const { commands } = useDashboard();
  const run = target?.kind === 'saved' ? target.run : null;
  const focus = target?.kind === 'live' || target?.kind === 'saved' ? target.kind : null;
  useEffect(() => {
    commands.openRuns(true, run);
  }, [commands, run]);
  useEffect(
    () => () => {
      commands.openRuns(false, null);
    },
    [commands],
  );
  useEffect(() => {
    if (focus === null) return;
    document.getElementById(focus === 'live' ? 'runsLive-title' : 'runsSaved-title')?.focus();
  }, [focus, run]);
  return (
    <>
      <header className="tp-topbar">
        <RegionBoundary
          fallback={<RevocationFallback part="topBar" />}
          resetKey={useEpochKey()}
          onError={usePartError('topBar')}
        >
          <FaultPoint part="topBar" />
          <TopBar pageId="runs" title={t.pageName.short} />
        </RegionBoundary>
      </header>
      <main id="main" className="tp-main" tabIndex={-1}>
        <FaultPoint part="page" />
        <div className="tp-runs">
          <RunsRegion id="runsLive" part="runsLive" title={t.live.title}>
            <ActiveOnly>
              <LiveSection />
            </ActiveOnly>
          </RunsRegion>
          <RunsRegion id="runsSaved" part="runsSaved" title={t.saved.title}>
            <SavedSection />
          </RunsRegion>
          <ActiveOnly>
            <ShoppingLibrary />
          </ActiveOnly>
        </div>
      </main>
    </>
  );
}
