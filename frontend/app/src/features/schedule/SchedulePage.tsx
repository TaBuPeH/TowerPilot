import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { schedule as t } from '../../copy/schedule.ts';
import { currentSchedule, type ScheduleStore } from '../../store/schedule.ts';
import type { ServerText } from '../../copy/server.ts';
import {
  FIRST_DAY,
  WEEK_SLOTS,
  type BlockField,
  type ScheduleDraft,
} from '../../store/scheduleDraft.ts';
import { selectSchedule } from '../../store/selectors/schedule.ts';
import type { ScheduleView } from '../../store/selectors/schedule.ts';
import { isoText } from '../../copy/format.ts';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { Button } from '../../ui/Button.tsx';
import { useAnnounce } from '../../ui/LiveRegion.tsx';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';
import { pageHref } from '../../ui/routes.ts';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { Region, RegionBody } from '../../ui/Region.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { Facts, Fact } from '../../ui/Facts.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { CopyLine } from '../../ui/CopyLine.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { TopBar } from '../dashboard/topBar/TopBar.tsx';
import { EditingTargetLine } from '../profiles/EditingTargetLine.tsx';
import { TargetSourceError } from '../profiles/TargetSourceError.tsx';
import './schedule.css';

function fieldId(key: string, field: string): string {
  return `schedule:${encodeURIComponent(key)}:${field}`;
}

function PlanTarget({ name }: { readonly name: ServerText<'name'> | null }) {
  return (
    <Facts>
      <Fact
        label={t.editingProfile.short}
        value={name === null ? t.notReported.short : <ServerValue kind="name" value={name} />}
      />
    </Facts>
  );
}

/** Summarize entered assignments; inheritance and validation still belong to the scheduler. */
function WeekAssignments({
  week,
  days,
}: {
  readonly week: ScheduleDraft['week'];
  readonly days: ScheduleView['dayNames'];
}) {
  const overrides = WEEK_SLOTS.filter((slot) => slot !== 'default' && (week[slot] ?? '') !== '');
  const value = (slot: string) => {
    const named = days.find((day) => day.key === week[slot])?.label;
    return named === undefined || named === null ? (
      t.otherAssignment.short
    ) : (
      <ServerValue kind="name" value={named} />
    );
  };
  return (
    <Facts layout="cells">
      <Fact
        label={t.weekLabels.default.short}
        value={(week['default'] ?? '') === '' ? t.noDefault.short : value('default')}
      />
      {overrides.map((slot) => (
        <Fact key={slot} label={t.weekLabels[slot].short} value={value(slot)} />
      ))}
      {overrides.length === WEEK_SLOTS.length - 1 ? null : (
        <Fact
          label={overrides.length === 0 ? t.allWeekdays.short : t.otherWeekdays.short}
          value={t.inherited.short}
        />
      )}
    </Facts>
  );
}

/** F6-11 RL8: draft a first plan for a profile without one; Save schedule sends it. */
function StartPlan({
  runs,
  onStart,
  inactive,
}: {
  readonly runs: readonly { readonly key: string; readonly label: ServerText<'name'> | null }[];
  readonly onStart: (run: string) => void;
  readonly inactive: boolean;
}) {
  const copy = inactive ? t.startPlanInactive : t.startPlan;
  const impact = inactive ? t.startImpactInactive : t.startImpact;
  const [picked, setPicked] = useState('');
  const named = runs.filter((run) => run.label !== null);
  const run = named.some((item) => item.key === picked) ? picked : (named[0]?.key ?? '');
  if (named.length === 0) return <p>{t.startNoRun.short}</p>;
  return (
    <div className="tp-schedule__start tp-control-row">
      <label className="tp-schedule__run">
        <span>{t.startRun.short}</span>
        <select
          className="tp-field-control"
          id="schedule:start-run"
          value={run}
          onChange={(event) => {
            setPicked(event.currentTarget.value);
          }}
        >
          {named.map((item) =>
            item.label === null ? null : (
              <ServerOption key={item.key} value={item.key} label={item.label} />
            ),
          )}
        </select>
      </label>
      <ActionRow>
        <Button
          tier="primary"
          id="schedule:start"
          label={copy.short}
          onClick={() => {
            onStart(run);
          }}
        />
        {copy.why === undefined ? null : <HelpTip label={copy.short}>{copy.why}</HelpTip>}
      </ActionRow>
      <p className="tp-schedule__start-impact">{impact.short}</p>
    </div>
  );
}
export function SchedulePage({ schedule }: { readonly schedule: ScheduleStore }) {
  const state = useSyncExternalStore(schedule.subscribe, schedule.getSnapshot);
  const [now, setNow] = useState(Date.now);
  const [discardTarget, setDiscardTarget] = useState<{
    readonly key: string | null;
    readonly epoch: number;
  } | null>(null);
  const [removeTarget, setRemoveTarget] = useState<{
    readonly key: string | null;
    readonly epoch: number;
    readonly revision: string;
  } | null>(null);
  const [focusRequest, setFocusRequest] = useState<{
    readonly id: string;
    readonly serial: number;
  } | null>(null);
  const focus = (id: string) => {
    setFocusRequest((previous) => ({ id, serial: (previous?.serial ?? 0) + 1 }));
  };
  const vm = selectSchedule(state, now);
  const entry = vm.entry;
  const nextCheckMs = vm.nextCheck === null ? null : Date.parse(vm.nextCheck);
  const observedIso = vm.observedAt === null ? null : isoText(vm.observedAt * 1000);
  const outcomeCopy =
    vm.outcome === null
      ? null
      : vm.outcome === 'removed' && vm.target.inactive
        ? t.removedInactive
        : t[vm.outcome];
  const announce = useAnnounce();
  // F6-11-P2-04: "removed" is announced only once an answer or a read proves it, once per
  // request; sending a removal announces only that it was requested.
  const announcedRemoval = useRef<string | null>(null);
  const removalId = vm.removal ? (entry?.intent?.id ?? null) : null;
  const removalProved = vm.outcome === 'removed' || vm.outcome === 'readRemoved';
  useEffect(() => {
    if (removalId === null || !removalProved || announcedRemoval.current === removalId) return;
    announcedRemoval.current = removalId;
    announce(
      vm.outcome === 'readRemoved'
        ? t.readRemoved.short
        : vm.target.inactive
          ? t.removedInactive.short
          : t.removed.short,
    );
  }, [announce, removalId, removalProved, vm.outcome, vm.target.inactive]);
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, []);
  useLayoutEffect(() => {
    const remembered = currentSchedule(schedule.getSnapshot())?.focus;
    if (remembered) document.getElementById(remembered)?.focus();
  }, [schedule, state.key, entry?.day, vm.hasDraftPlan]);
  useLayoutEffect(() => {
    if (focusRequest !== null) document.getElementById(focusRequest.id)?.focus();
  }, [focusRequest]);
  return (
    <>
      <header className="tp-topbar">
        <TopBar pageId="schedule" title={t.pageName.short} readOnly />
      </header>
      <main
        id="main"
        className="tp-main tp-schedule"
        tabIndex={-1}
        onFocusCapture={(event) => {
          if (event.target instanceof HTMLElement && event.target.id.startsWith('schedule:'))
            schedule.rememberFocus(event.target.id);
        }}
      >
        <Region id="schedule-target" title={t.target.short} fallback={t.readError.short}>
          <RegionBody>
            <CopyLine entry={t.intro} variant="intro" />
            <EditingTargetLine />
            <Facts layout="cells" className="tp-schedule__target">
              <Fact
                label={t.emulator.short}
                value={
                  vm.instance === null ? (
                    t.notReported.short
                  ) : (
                    <ServerValue kind="name" value={vm.instance} />
                  )
                }
              />
              <Fact
                label={t.account.short}
                value={
                  vm.account === null ? (
                    t.notReported.short
                  ) : (
                    <ServerValue kind="name" value={vm.account} />
                  )
                }
              />
            </Facts>
          </RegionBody>
        </Region>
        <div className="tp-schedule__observed">
          <Region
            id="schedule-observation"
            titleId="schedule-observed"
            title={t.observed.short}
            fallback={t.unavailable.short}
          >
            <RegionBody>
              {vm.observationState === null ? null : <p>{t[vm.observationState].short}</p>}
              <Facts layout="cells" className="tp-schedule__facts">
                <Fact
                  label={t.observedProfile.short}
                  value={
                    vm.activeProfile === null ? (
                      t.notReported.short
                    ) : (
                      <ServerValue kind="name" value={vm.activeProfile} />
                    )
                  }
                />
                {vm.winner === null ? null : (
                  <Fact
                    label={t.winner.short}
                    value={<ServerValue kind="name" value={vm.winner} />}
                  />
                )}
                {vm.previousWinner === null ? null : (
                  <Fact
                    label={t.previousWinner.short}
                    value={<ServerValue kind="name" value={vm.previousWinner} />}
                  />
                )}
                <Fact
                  label={t.timezone.short}
                  value={
                    <>
                      {vm.timezone === null ? (
                        t.notReported.short
                      ) : (
                        <ServerValue kind="detail" value={vm.timezone} />
                      )}
                      {t.space}
                      {vm.offset === null ? null : <ServerValue kind="detail" value={vm.offset} />}
                    </>
                  }
                />
                <Fact
                  label={t.observedAt.short}
                  value={
                    vm.observedAt === null ? (
                      t.notReported.short
                    ) : observedIso === null ? (
                      vm.observedAt
                    ) : (
                      <time dateTime={observedIso}>{t.clock(vm.observedAt)}</time>
                    )
                  }
                />
                <Fact
                  label={t.nextCheck.short}
                  value={
                    vm.nextCheck === null ? (
                      t.noCheck.short
                    ) : nextCheckMs !== null && Number.isFinite(nextCheckMs) ? (
                      <time dateTime={isoText(nextCheckMs) ?? undefined}>
                        {t.clock(nextCheckMs / 1000)}
                      </time>
                    ) : (
                      <ServerValue kind="detail" value={vm.nextCheck} />
                    )
                  }
                />
                {vm.reason === null ? null : (
                  <Fact
                    label={t.observedReason.short}
                    value={<ServerValue kind="message" value={vm.reason} />}
                  />
                )}
              </Facts>
              {vm.observedAt === null && vm.nextCheck === null ? null : (
                <>
                  <p className="tp-schedule__clock-zone">{t.displayTimezone()}</p>
                  <Disclosure label={t.observationEvidence.short}>
                    <Facts>
                      <Fact
                        label={t.observedEpoch.short}
                        value={vm.observedAt ?? t.notReported.short}
                      />
                      <Fact
                        label={t.nextCheck.short}
                        value={
                          vm.nextCheck === null ? (
                            t.noCheck.short
                          ) : (
                            <ServerValue kind="detail" value={vm.nextCheck} />
                          )
                        }
                      />
                    </Facts>
                  </Disclosure>
                </>
              )}
              <p>{t.handoff.short}</p>
            </RegionBody>
          </Region>
        </div>
        <Region id="schedule-plan" title={t.days.short} fallback={t.readError.short}>
          <RegionBody>
            <ActionRow
              reason={
                state.context === null ? (
                  <Reason id="schedule:unknown-target" entry={t.unknownTarget} />
                ) : entry?.busy === true ? (
                  <span role="status">
                    <Reason id="schedule:read-reason" entry={t.reading} />
                  </span>
                ) : null
              }
            >
              <button
                type="button"
                className="tp-button"
                id="schedule:read"
                // Nothing can be read before the target is known: the button says why instead of
                // doing nothing (an enabled button here also told tests the plan had been read).
                disabled={state.context === null || entry?.busy === true}
                aria-describedby={
                  state.context === null
                    ? 'schedule:unknown-target'
                    : entry?.busy === true
                      ? 'schedule:read-reason'
                      : undefined
                }
                onClick={() => {
                  void schedule.refresh();
                }}
              >
                {t.refresh.short}
              </button>
            </ActionRow>
            {state.context !== null && vm.noProfile ? <p>{t.noProfile.short}</p> : null}
            {entry?.readError === true ? <p role="alert">{t.readError.short}</p> : null}
            <TargetSourceError error={vm.sourceError} />
            {vm.notSent === null ? null : <p role="alert">{t.notSent[vm.notSent].short}</p>}
            {vm.currentContext === null ? null : (
              <pre>
                <ServerValue kind="detail" value={vm.currentContext} />
              </pre>
            )}
            {entry?.source !== undefined && !vm.hasDraftPlan ? (
              <p>{vm.target.inactive ? t.missingInactive.short : t.missing.short}</p>
            ) : null}
            {/* F6-11 RL8: once the plan is removed its editor is gone; its outcome stays here. */}
            {!vm.hasDraftPlan && vm.outcome !== null ? (
              <section
                aria-label={t.validation.short}
                className="tp-schedule__errors"
                role={
                  vm.outcome === 'refused' || vm.outcome === 'removeRefused' ? 'alert' : 'status'
                }
              >
                <p>{outcomeCopy?.short}</p>
                <PlanTarget name={vm.profile} />
                {vm.error === null ? null : (
                  <p>
                    <ServerValue kind="message" value={vm.error} />
                  </p>
                )}
              </section>
            ) : null}
            {vm.planMissing ? (
              <StartPlan
                inactive={vm.target.inactive}
                runs={vm.runs}
                onStart={(run) => {
                  if (!schedule.start(run)) return;
                  announce(t.started.short);
                  focus(fieldId(`${FIRST_DAY}:0`, 'block'));
                }}
              />
            ) : null}
            {vm.empty ? <p role="alert">{t.empty.short}</p> : null}
            <p>
              <a id="schedule:profiles" href="#profiles">
                {t.profiles.short}
              </a>
            </p>
            {vm.hasDraftPlan && entry !== null ? (
              <>
                <Tile titleId="schedule-revisions" title={t.revisions.short}>
                  <Facts>
                    <Fact
                      label={t.revisionState.short}
                      value={
                        vm.revision === null || vm.baseRevision === null
                          ? t.notReported.short
                          : vm.revision === vm.baseRevision
                            ? t.revisionsMatch.short
                            : t.revisionsDiffer.short
                      }
                    />
                  </Facts>
                  <details className="tp-schedule__compare tp-disclosure">
                    <summary>{t.compare.short}</summary>
                    <Facts className="tp-schedule__revisions">
                      <Fact
                        label={t.savedSource.short}
                        value={
                          vm.revision === null ? (
                            t.notReported.short
                          ) : (
                            <ServerValue kind="id" value={vm.revision} />
                          )
                        }
                      />
                      <Fact
                        label={t.baseRevision.short}
                        value={
                          vm.baseRevision === null ? (
                            t.notReported.short
                          ) : (
                            <ServerValue kind="id" value={vm.baseRevision} />
                          )
                        }
                      />
                    </Facts>
                    {vm.savedPlan === null ? null : (
                      <pre>
                        <ServerValue kind="detail" value={vm.savedPlan} />
                      </pre>
                    )}
                  </details>
                </Tile>
                <CopyLine entry={t.priority} variant="note" />
                <nav aria-label={t.days.short} className="tp-schedule__days tp-control-row">
                  {vm.dayNames.map((day) => (
                    <button
                      key={day.key}
                      type="button"
                      className="tp-button"
                      aria-pressed={entry.day === day.key}
                      onClick={() => {
                        schedule.selectDay(day.key);
                      }}
                    >
                      {day.label === null ? (
                        t.dayPlan.short
                      ) : (
                        <ServerValue kind="name" value={day.label} />
                      )}
                    </button>
                  ))}
                </nav>
                <CopyLine entry={t.blankTimes} variant="note" />
                <CopyLine entry={t.crossMidnight} variant="note" />
                <div className="tp-schedule__blocks">
                  {vm.blocks.map((block) => (
                    <div key={block.key} data-block-key={block.key}>
                      <Tile
                        as="fieldset"
                        titleId={fieldId(block.key, 'title')}
                        title={t.block(block.index + 1)}
                        className="tp-schedule__block"
                      >
                        <div className="tp-schedule__block-fields tp-control-row">
                          <label className="tp-schedule__run">
                            <span>{t.run.short}</span>
                            <select
                              className="tp-field-control"
                              id={fieldId(block.key, 'blueprint')}
                              value={block.blueprint}
                              aria-invalid={block.invalid.blueprint || undefined}
                              aria-describedby={
                                block.invalid.blueprint ? 'schedule-errors' : undefined
                              }
                              onChange={(event) => {
                                schedule.edit(block.key, 'blueprint', event.currentTarget.value);
                              }}
                            >
                              {!vm.runs.some((run) => run.key === block.blueprint) ? (
                                <option value={block.blueprint}>{t.notReported.short}</option>
                              ) : null}
                              {vm.runs.map((run) =>
                                run.label === null ? null : (
                                  <ServerOption key={run.key} value={run.key} label={run.label} />
                                ),
                              )}
                            </select>
                          </label>
                          {(
                            ['block', 'after', 'until', 'count'] satisfies readonly BlockField[]
                          ).map((field) => (
                            <label key={field}>
                              <span>
                                {field === 'block'
                                  ? t.kind.short
                                  : field === 'after'
                                    ? t.after.short
                                    : field === 'until'
                                      ? t.until.short
                                      : t.count.short}
                              </span>
                              <input
                                className="tp-field-control"
                                id={fieldId(block.key, field)}
                                type="text"
                                inputMode={field === 'count' ? 'decimal' : 'text'}
                                value={block[field]}
                                aria-invalid={block.invalid[field] || undefined}
                                aria-describedby={
                                  vm.outcome === 'refused' ? 'schedule-errors' : undefined
                                }
                                onChange={(event) => {
                                  schedule.edit(block.key, field, event.currentTarget.value);
                                }}
                              />
                            </label>
                          ))}
                        </div>
                        <div className="tp-schedule__actions">
                          <ActionRow
                            reason={
                              <>
                                {block.index === 0 ? (
                                  <Reason
                                    id={fieldId(block.key, 'up-reason')}
                                    entry={t.firstBlock}
                                  />
                                ) : null}
                                {block.index === vm.blocks.length - 1 ? (
                                  <Reason
                                    id={fieldId(block.key, 'down-reason')}
                                    entry={t.lastBlock}
                                  />
                                ) : null}
                              </>
                            }
                          >
                            <button
                              type="button"
                              className="tp-button"
                              disabled={block.index === 0}
                              aria-describedby={
                                block.index === 0 ? fieldId(block.key, 'up-reason') : undefined
                              }
                              onClick={() => {
                                const position = schedule.move(block.key, -1);
                                if (position !== null) announce(t.moved(position));
                                focus(fieldId(block.key, 'blueprint'));
                              }}
                            >
                              {t.up.short}
                            </button>
                            <button
                              type="button"
                              className="tp-button"
                              disabled={block.index === vm.blocks.length - 1}
                              aria-describedby={
                                block.index === vm.blocks.length - 1
                                  ? fieldId(block.key, 'down-reason')
                                  : undefined
                              }
                              onClick={() => {
                                const position = schedule.move(block.key, 1);
                                if (position !== null) announce(t.moved(position));
                                focus(fieldId(block.key, 'blueprint'));
                              }}
                            >
                              {t.down.short}
                            </button>
                            <button
                              type="button"
                              className="tp-button"
                              onClick={() => {
                                const key = schedule.remove(block.key);
                                announce(t.deleted.short);
                                focus(key === null ? 'schedule:add' : fieldId(key, 'blueprint'));
                              }}
                            >
                              {t.remove.short}
                            </button>
                            <a
                              className="tp-button"
                              data-tier="quiet"
                              id={fieldId(block.key, 'runs')}
                              href={pageHref('runs', { kind: 'saved', run: block.blueprint })}
                            >
                              {t.editRun.short}
                            </a>
                          </ActionRow>
                        </div>
                        <Facts layout="cells" className="tp-schedule__quota">
                          <Fact label={t.quota.short} value={t.notReported.short} />
                          <Fact label={t.nextStart.short} value={t.notReported.short} />
                        </Facts>
                      </Tile>
                    </div>
                  ))}
                </div>
                {vm.blocks.length === 0 ? <p role="alert">{t.noBlocks.short}</p> : null}
                <ActionRow
                  reason={
                    <Reason
                      id="schedule:add-reason"
                      entry={vm.canAdd ? t.addHint : t.addUnavailable}
                    />
                  }
                >
                  <button
                    type="button"
                    className="tp-button"
                    id="schedule:add"
                    disabled={!vm.canAdd}
                    aria-describedby="schedule:add-reason"
                    onClick={() => {
                      const key = schedule.add();
                      if (key !== null) {
                        announce(t.added.short);
                        focus(fieldId(key, 'blueprint'));
                      }
                    }}
                  >
                    {t.add.short}
                  </button>
                </ActionRow>
                <Tile titleId="schedule-week-title" title={t.week.short}>
                  <WeekAssignments week={entry.draft?.week ?? {}} days={vm.dayNames} />
                  <details className="tp-schedule__week tp-disclosure">
                    <summary>{t.editWeek.short}</summary>
                    <p>{t.weekHint.short}</p>
                    <div className="tp-schedule__week-grid tp-control-row">
                      {WEEK_SLOTS.map((slot) => (
                        <label key={slot}>
                          <span>{t.weekLabels[slot].short}</span>
                          <input
                            className="tp-field-control"
                            id={`schedule:week:${slot}`}
                            list="schedule-day-options"
                            type="text"
                            value={entry.draft?.week[slot] ?? ''}
                            aria-invalid={vm.invalidWeek[slot] || undefined}
                            aria-describedby={vm.invalidWeek[slot] ? 'schedule-errors' : undefined}
                            placeholder={
                              slot === 'default' ? t.defaultRequired.short : t.inherited.short
                            }
                            onChange={(event) => {
                              schedule.mapWeek(slot, event.currentTarget.value);
                            }}
                          />
                        </label>
                      ))}
                    </div>
                    <datalist id="schedule-day-options">
                      {vm.dayNames.map((day) => (
                        <option key={day.key} value={day.key} />
                      ))}
                    </datalist>
                  </details>
                </Tile>
                <section
                  aria-label={t.validation.short}
                  className="tp-schedule__errors"
                  role={
                    vm.outcome === 'refused' || vm.outcome === 'removeRefused' ? 'alert' : 'status'
                  }
                >
                  <div id="schedule-errors">
                    {outcomeCopy === null ? null : <p>{outcomeCopy.short}</p>}
                    {vm.outcome === null ? null : <PlanTarget name={vm.profile} />}
                    {vm.error === null ? null : (
                      <p>
                        <ServerValue kind="message" value={vm.error} />
                      </p>
                    )}
                    {vm.problems.map((problem, index) => (
                      <p key={index}>
                        <ServerValue kind="message" value={problem} />
                      </p>
                    ))}
                  </div>
                </section>
                {vm.warnings.length === 0 ? null : (
                  <section aria-label={t.warnings.short} className="tp-schedule__errors">
                    <h3>{t.warnings.short}</h3>
                    {vm.warnings.map((warning, index) => (
                      <p key={index}>
                        <ServerValue kind="message" value={warning} />
                      </p>
                    ))}
                  </section>
                )}
                {entry.reconcile || vm.partial ? (
                  <div className="tp-schedule__errors">
                    <ActionRow
                      reason={<Reason id="schedule:reconcile-reason" entry={t.reconcileHint} />}
                    >
                      <button
                        type="button"
                        className="tp-button"
                        disabled={!entry.checked || entry.busy || entry.intent?.phase === 'sending'}
                        aria-describedby="schedule:reconcile-reason"
                        onClick={schedule.reconcile}
                      >
                        {t.reconcile.short}
                      </button>
                    </ActionRow>
                  </div>
                ) : null}
                <Tile
                  titleId="schedule-save-title"
                  title={t.save.short}
                  className="tp-schedule__save"
                >
                  <strong>{entry.dirty ? t.dirty.short : t.clean.short}</strong>
                  <span>{t.scope.short}</span>
                  <ActionRow
                    reason={
                      !vm.canSave ? (
                        <Reason
                          id="schedule:save-reason"
                          entry={t.saveUnavailable[vm.saveUnavailable]}
                        />
                      ) : null
                    }
                  >
                    <Button
                      id="schedule:save"
                      tier="secondary"
                      label={t.save.short}
                      busy={entry.intent?.phase === 'sending' || vm.checking}
                      {...(!vm.canSave ? { disabled: true, reasonId: 'schedule:save-reason' } : {})}
                      onClick={() => {
                        void schedule.save();
                      }}
                    />
                    <Button
                      id="schedule:discard"
                      tier="quiet"
                      label={t.discard.short}
                      {...(!entry.dirty || vm.pending
                        ? { disabled: true, reasonId: 'schedule:save-reason' }
                        : {})}
                      onClick={() => {
                        const target = schedule.getSnapshot();
                        setDiscardTarget({ key: target.key, epoch: target.epoch });
                      }}
                    />
                  </ActionRow>
                </Tile>
              </>
            ) : null}
            {vm.planSaved && entry !== null ? (
              <div className="tp-schedule__remove">
                <ActionRow
                  reason={
                    vm.removeBlocked === null ? null : (
                      <Reason
                        id="schedule:remove-reason"
                        entry={t.removeUnavailable[vm.removeBlocked]}
                      />
                    )
                  }
                >
                  <Button
                    id="schedule:remove"
                    tier="quiet"
                    label={t.removePlan.short}
                    busy={vm.checking}
                    {...(vm.removeBlocked === null
                      ? {}
                      : { disabled: true, reasonId: 'schedule:remove-reason' })}
                    onClick={() => {
                      const target = schedule.getSnapshot();
                      const revision = currentSchedule(target)?.baseRevision ?? null;
                      if (revision !== null)
                        setRemoveTarget({ key: target.key, epoch: target.epoch, revision });
                    }}
                  />
                </ActionRow>
                <CopyLine
                  entry={vm.target.inactive ? t.removeImpactInactive : t.removeImpact}
                  variant="note"
                />
              </div>
            ) : null}
          </RegionBody>
        </Region>
        <ConfirmDialog
          // F6-11 RL8: open only for the target and revision it was asked for.
          open={
            removeTarget !== null &&
            removeTarget.key === state.key &&
            removeTarget.epoch === state.epoch &&
            removeTarget.revision === entry?.baseRevision &&
            vm.removeBlocked === null
          }
          title={t.removeTitle.short}
          body={vm.target.inactive ? t.removeInactiveBody.short : t.removeBody.short}
          confirmLabel={t.removeConfirm.short}
          cancelLabel={t.cancel.short}
          tier="halt"
          onCancel={() => {
            setRemoveTarget(null);
          }}
          onConfirm={() => {
            const target = schedule.getSnapshot();
            const asked = removeTarget;
            setRemoveTarget(null);
            if (asked === null || asked.key !== target.key || asked.epoch !== target.epoch) return;
            // F6-11-P2-01/04: the click starts a context check; only a sent request is
            // announced, as requested (the outcome announces a proved removal).
            announce(t.removeChecking.short);
            focus('schedule:read');
            void schedule.removePlan(asked.revision).then((result) => {
              if (result === 'sent') announce(t.removeRequested.short);
              else if (result !== 'blocked') announce(t.notSent[result].short);
            });
          }}
        />
        <ConfirmDialog
          open={
            discardTarget !== null &&
            discardTarget.key === state.key &&
            discardTarget.epoch === state.epoch
          }
          title={t.discardTitle.short}
          body={t.discardBody.short}
          confirmLabel={t.discard.short}
          cancelLabel={t.cancel.short}
          tier="primary"
          onCancel={() => {
            setDiscardTarget(null);
          }}
          onConfirm={() => {
            const target = schedule.getSnapshot();
            if (discardTarget?.key === target.key && discardTarget.epoch === target.epoch)
              schedule.discard();
            setDiscardTarget(null);
          }}
        />
      </main>
    </>
  );
}
