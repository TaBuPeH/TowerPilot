import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { list, own, ownPath, text } from '../../core/read.ts';
import { abilities as t, abilityLabel } from '../../copy/abilities.ts';
import {
  abilityKey,
  abilityConsumers,
  abilityReadOnly,
  canSaveAbility,
  canReconcileAbility,
  currentAbilities,
  currentAbility,
  sourceRevision,
  type AbilitiesStore,
  type AbilityFamily,
} from '../../store/abilities.ts';
import { abilityNode, abilityValue, nodeAt, vocabValues } from '../../store/abilitiesDraft.ts';
import {
  abilityGlobalField,
  abilityMessage,
  abilityName,
  latestAbility,
  abilityProblems,
  abilityFieldId,
  abilityErrorAttributes,
} from '../../store/selectors/abilities.ts';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { Region, RegionBody, RegionEnd } from '../../ui/Region.tsx';
import { Tile, Tiles } from '../../ui/Tile.tsx';
import { Facts, Fact } from '../../ui/Facts.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { CopyLine } from '../../ui/CopyLine.tsx';
import { AbilityHelp } from './AbilityHelp.tsx';
import { ShownList } from '../../ui/ShownList.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { TopBar } from '../dashboard/topBar/TopBar.tsx';
import { AbilityField, AbilityBackendErrors, Json } from './AbilityField.tsx';
import { RuleEditor } from './RuleEditor.tsx';
import { SettingsField } from '../settings/SettingsModuleForm.tsx';
import { EditingTargetLine } from '../profiles/EditingTargetLine.tsx';
import { TargetSourceError } from '../profiles/TargetSourceError.tsx';
import { editingTarget as targetCopy } from '../../copy/editingTarget.ts';
import { editingTargetVM } from '../../store/selectors/editingTarget.ts';
import './abilities.css';
import type { PageTarget } from '../../ui/routes.ts';

export function AbilitiesPage({
  abilities: store,
  target: routeTarget = null,
}: {
  readonly abilities: AbilitiesStore;
  readonly target?: PageTarget | null;
}) {
  const s = useSyncExternalStore(store.subscribe, store.getSnapshot),
    target = currentAbilities(s),
    e = currentAbility(s);
  const routeKey =
    routeTarget?.kind === 'ability' ? abilityKey(routeTarget.family, routeTarget.policy) : null;
  const openedRoute = useRef<string | null>(null);
  // RL11: wait for this target's source before selecting its specific policy.
  useEffect(() => {
    const marker = JSON.stringify([s.key, routeKey]);
    if (
      routeKey !== null &&
      target?.editors[routeKey] !== undefined &&
      openedRoute.current !== marker
    ) {
      openedRoute.current = marker;
      store.select(routeKey);
      document.getElementById('abilities-editor')?.focus();
    }
  }, [store, routeKey, s.key, target?.editors]);
  const problems = abilityProblems(e),
    errorSummary = useRef<HTMLElement>(null);
  const [family, setFamily] = useState<AbilityFamily>(
    routeTarget?.kind === 'ability' ? routeTarget.family : 'uw_policies',
  );
  const newId = target?.newIds[family] ?? '';
  const [now, setNow] = useState(Date.now),
    [confirm, setConfirm] = useState<{
      action: 'delete' | 'cancel';
      key: string | null;
      name: string | null;
      version: number;
    } | null>(null);
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    if (e?.deleting === true) document.getElementById('abilities-editor')?.focus();
  }, [e?.deleting]);
  useEffect(() => {
    if (e?.outcome === 'refused') errorSummary.current?.focus();
  }, [e?.outcome, e?.detail]);
  const raw = e === null ? undefined : abilityValue(e.node),
    cl = own(raw, 'chain_lightning');
  const clMode = text(own(cl, 'mode')) ?? 'absent';
  const armValue = own(raw, 'arm');
  const armMode =
    armValue === 'always'
      ? 'always'
      : own(armValue, 'on') === 'second_wind'
        ? 'second_wind'
        : 'unspecified';
  const secondWind = armMode === 'second_wind';
  const stale = target !== null && (target.readAt === null || now - target.readAt > 30_000);
  const readOnly = abilityReadOnly(s);
  const readUnavailable = target?.busy === true || s.context === null;
  const consumers = e === null || target === null ? [] : abilityConsumers(target, e);
  const name = abilityName(e?.id),
    planLabel = abilityName(own(raw, 'label')),
    globalKey = abilityKey('global', 'demon_mode_always');
  const globalField = abilityGlobalField(target, e);
  const recordedVerification = ownPath(target?.source, 'profile', 'player', 'abilities_verified');
  const editing = editingTargetVM(s.editing);
  const shownTarget = e?.family === 'global' ? editing.active : editing.name;
  return (
    <>
      <header className="tp-topbar">
        <TopBar pageId="abilities" title={t.pageName.short} readOnly />
      </header>
      <main id="main" tabIndex={-1} className="tp-main tp-abilities">
        <Region id="abilities-target" title={t.target.short} fallback={t.readError.short}>
          <RegionBody>
            <CopyLine entry={t.intro} variant="intro" />
            <Tiles>
              <Tile titleId="abilities-current-target" title={t.currentTarget.short}>
                <Facts className="tp-abilities__target" layout="cells">
                  {(
                    [
                      ['instance', s.context?.instance],
                      ['account', s.context?.account],
                      ['profile', s.context?.profile],
                    ] as const
                  ).map(([key, v]) => {
                    const shown = abilityName(v);
                    return (
                      <Fact
                        key={key}
                        label={abilityLabel(key)}
                        value={
                          shown === null ? (
                            t.unknown.short
                          ) : (
                            <ServerValue kind="name" value={shown} />
                          )
                        }
                      />
                    );
                  })}
                </Facts>
                {/* F6-14-C-05: unavailable while a read runs, but never natively disabled: a person who
            pressed it keeps keyboard focus on it (a disabled button drops focus to the page). */}
                <ActionRow
                  reason={
                    target?.busy === true ? (
                      <span role="status">
                        <Reason id="abilities-read-reason" entry={t.reading} />
                      </span>
                    ) : s.context === null ? (
                      <Reason id="abilities-no-target" entry={t.noTarget} />
                    ) : undefined
                  }
                >
                  <button
                    className="tp-button"
                    type="button"
                    onClick={() => {
                      if (!readUnavailable) void store.refresh();
                    }}
                    aria-disabled={readUnavailable || undefined}
                    aria-describedby={
                      target?.busy === true
                        ? 'abilities-read-reason'
                        : s.context === null
                          ? 'abilities-no-target'
                          : undefined
                    }
                  >
                    {t.read.short}
                  </button>
                </ActionRow>
                {target?.error === true ? <p role="alert">{t.readError.short}</p> : null}
                <TargetSourceError error={abilityMessage(target?.sourceError)} />
                {stale ? <p role="status">{t.stale.short}</p> : null}
                {s.editing.name === null && s.context !== null ? (
                  <p id="abilities-no-target">{t.noTarget.short}</p>
                ) : null}
                {readOnly ? <p role="status">{t.readOnly.short}</p> : null}
                <CopyLine entry={t.scope} variant="note" />
              </Tile>
              <Tile
                className="tp-abilities__card"
                titleId="abilities-global"
                title={t.global.short}
              >
                <div data-testid="abilities-active-only">
                  <Facts>
                    <Fact
                      label={targetCopy.active.short}
                      value={
                        editing.active === null ? (
                          targetCopy.none.short
                        ) : (
                          <ServerValue kind="name" value={editing.active} />
                        )
                      }
                    />
                  </Facts>
                  <CopyLine entry={t.overrideHelp} variant="note" />
                  <ActionRow
                    reason={
                      s.editing.inactive || target?.editors[globalKey] === undefined ? (
                        <Reason
                          id="abilities-global-reason"
                          entry={s.editing.inactive ? t.activeOnly : t.globalUnavailable}
                        />
                      ) : undefined
                    }
                  >
                    <button
                      type="button"
                      className="tp-button"
                      aria-pressed={target?.selected === globalKey}
                      onClick={() => {
                        store.select(globalKey);
                      }}
                      disabled={s.editing.inactive || target?.editors[globalKey] === undefined}
                      aria-describedby={
                        s.editing.inactive || target?.editors[globalKey] === undefined
                          ? 'abilities-global-reason'
                          : undefined
                      }
                    >
                      {t.global.short}
                    </button>
                  </ActionRow>
                </div>
              </Tile>
            </Tiles>
            <EditingTargetLine />
          </RegionBody>
        </Region>
        <div className="tp-abilities__layout">
          <Region
            id="abilities-plans-region"
            titleId="abilities-plans"
            title={t.plans.short}
            fallback={t.readError.short}
          >
            <RegionBody>
              <div className="tp-abilities__library-layout">
                <div>
                  <label>
                    {t.plans.short}
                    <select
                      className="tp-field-control"
                      value={family}
                      onChange={(event) => {
                        setFamily(
                          event.target.value === 'uw_policies' ? 'uw_policies' : 'rescue_policies',
                        );
                      }}
                    >
                      <option value="uw_policies">{t.uw.short}</option>
                      <option value="rescue_policies">{t.rescue.short}</option>
                    </select>
                  </label>
                  <ShownList
                    className="tp-abilities__list"
                    items={Object.entries(target?.editors ?? {}).filter(
                      ([, entry]) => entry.family === family && !entry.deleting,
                    )}
                    itemKey={([key]) => key}
                    more={t.morePlans.short}
                    render={([key, entry]) => {
                      const label = abilityName(
                        text(own(abilityValue(entry.node), 'label')) ?? entry.id,
                      );
                      return (
                        <ActionRow>
                          <button
                            type="button"
                            className="tp-button"
                            aria-pressed={target?.selected === key}
                            onClick={() => {
                              store.select(key);
                            }}
                          >
                            {label === null ? (
                              t.unknown.short
                            ) : (
                              <ServerValue kind="name" value={label} />
                            )}
                          </button>
                          {entry.dirty ? <span>{t.dirty.short}</span> : null}
                        </ActionRow>
                      );
                    }}
                  />
                  {Object.values(target?.editors ?? {}).some(
                    (entry) => entry.family === family,
                  ) ? null : (
                    <p>{t.empty.short}</p>
                  )}
                </div>
                <div>
                  <label htmlFor="abilities-new-id">{t.id.short}</label>
                  <input
                    className="tp-field-control"
                    id="abilities-new-id"
                    value={newId}
                    disabled={s.key === null}
                    aria-describedby="abilities-id-help"
                    onChange={(event) => {
                      store.newId(family, event.target.value);
                    }}
                  />
                  <p id="abilities-id-help">{t.idHelp.short}</p>
                  <ActionRow>
                    <button
                      className="tp-button"
                      type="button"
                      disabled={newId === ''}
                      onClick={() => {
                        store.newId(family, '');
                      }}
                    >
                      {t.discardId.short}
                    </button>
                    {([false, true] as const).map((copy) => (
                      <button
                        key={String(copy)}
                        type="button"
                        className="tp-button"
                        disabled={
                          readOnly ||
                          target?.readAt == null ||
                          !/^[-\w]+$/.test(newId) ||
                          Object.hasOwn(target.editors, abilityKey(family, newId)) ||
                          (copy && e?.family !== family)
                        }
                        onClick={() => {
                          store.create(family, newId, copy);
                        }}
                      >
                        {copy ? t.copy.short : t.create.short}
                      </button>
                    ))}
                  </ActionRow>
                </div>
              </div>
            </RegionBody>
          </Region>
          <Region
            id="abilities-editor-region"
            titleId="abilities-editor"
            title={
              name === null ? (
                t.plans.short
              ) : (
                <>
                  <span className="tp-abilities__editor-name">
                    {planLabel !== null ? (
                      <ServerValue kind="name" value={planLabel} />
                    ) : e?.family === 'global' ? (
                      t.global.short
                    ) : (
                      <ServerValue kind="name" value={name} />
                    )}
                  </span>
                  {planLabel !== null || e?.family === 'global' ? (
                    <>
                      {t.space}
                      <span className="tp-abilities__editor-id">
                        {t.planId.short}
                        {t.space}
                        <ServerValue kind="name" value={name} />
                      </span>
                    </>
                  ) : null}
                </>
              )
            }
            fallback={t.readError.short}
          >
            <RegionBody>
              {e === null ? null : (
                <>
                  <Facts layout="cells">
                    {e.outcome === null ? (
                      <Fact
                        label={t.saveTarget.short}
                        value={
                          shownTarget === null ? (
                            targetCopy.none.short
                          ) : (
                            <ServerValue kind="name" value={shownTarget} />
                          )
                        }
                      />
                    ) : null}
                    <Fact label={t.state.short} value={e.dirty ? t.dirty.short : t.clean.short} />
                    <Fact
                      label={t.consumers.short}
                      value={
                        consumers.length === 0 ? (
                          t.unused.short
                        ) : (
                          <ul>
                            {consumers.map((id) => {
                              const shown = abilityName(id);
                              return shown === null ? null : (
                                <li key={id}>
                                  <ServerValue kind="name" value={shown} />
                                </li>
                              );
                            })}
                          </ul>
                        )
                      }
                    />
                  </Facts>
                  {problems.length === 0 ? null : (
                    <section
                      ref={errorSummary}
                      tabIndex={-1}
                      aria-labelledby="abilities-problem-heading"
                      className="tp-abilities__errors"
                    >
                      <h3 id="abilities-problem-heading">{t.problems.short}</h3>
                      <ul>
                        {problems.map((problem, index) => (
                          <li key={index}>
                            <a
                              href={`#${problem.fieldId}`}
                              onClick={(event) => {
                                event.preventDefault();
                                (
                                  document.getElementById(problem.fieldId) ??
                                  document.getElementById(abilityFieldId([]))
                                )?.focus();
                              }}
                            >
                              <ServerValue kind="message" value={problem.message} />
                            </a>
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                  <fieldset
                    className="tp-abilities__fields"
                    id={abilityFieldId([])}
                    tabIndex={-1}
                    disabled={readOnly}
                  >
                    <legend className="tp-visually-hidden">
                      {e.family === 'global'
                        ? t.global.short
                        : e.family === 'uw_policies'
                          ? t.uw.short
                          : t.rescue.short}
                    </legend>
                    {e.family === 'global' ? (
                      globalField === null ? (
                        <p>{t.readError.short}</p>
                      ) : (
                        <SettingsField
                          field={globalField}
                          scope={s.key}
                          note={null}
                          onEdit={(_scope, _key, value) => {
                            store.edit([], abilityNode(value));
                          }}
                        />
                      )
                    ) : (
                      <>
                        <label>
                          {t.label.short}
                          <input
                            className="tp-field-control"
                            {...abilityErrorAttributes(problems, ['label'])}
                            value={text(own(raw, 'label')) ?? ''}
                            onChange={(event) => {
                              store.edit(['label'], abilityNode(event.target.value));
                            }}
                          />
                        </label>
                        <AbilityBackendErrors problems={problems} path={['label']} />
                        {e.family === 'uw_policies' ? (
                          <Tiles>
                            <Tile
                              titleId="abilities-baseline"
                              title={t.baseline.short}
                              intro={t.owned}
                            >
                              {vocabValues(target?.vocab, 'uw_names')
                                .filter((w) => w !== 'chain_lightning')
                                .map((weapon) => {
                                  const owned =
                                    ownPath(target?.source, 'profile', 'player', 'uws', weapon) ===
                                    true;
                                  return (
                                    <div key={weapon} className="tp-abilities__weapon">
                                      <AbilityField
                                        path={['baseline', weapon]}
                                        problems={problems}
                                        label={abilityLabel(weapon)}
                                        spec={{ type: 'bool' }}
                                        node={nodeAt(e.node, ['baseline', weapon])}
                                        onChange={(next) => {
                                          store.edit(['baseline', weapon], next);
                                        }}
                                      />
                                      {owned ? null : <p>{t.skipped.short}</p>}
                                    </div>
                                  );
                                })}
                            </Tile>
                            <Tile
                              titleId="abilities-chain-lightning"
                              title={t.cl.short}
                              end={<AbilityHelp subject={t.cl.short} entries={[t.ranges]} />}
                            >
                              <label>
                                {t.clMode.short}
                                <select
                                  className="tp-field-control"
                                  {...abilityErrorAttributes(problems, ['chain_lightning', 'mode'])}
                                  value={clMode}
                                  onChange={(event) => {
                                    store.switchMode(['chain_lightning'], event.target.value);
                                  }}
                                >
                                  <option value="absent">{t.absent.short}</option>
                                  {vocabValues(target?.vocab, 'cl_modes').map((mode) => (
                                    <option key={mode} value={mode}>
                                      {abilityLabel(mode)}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <AbilityBackendErrors
                                problems={problems}
                                path={['chain_lightning', 'mode']}
                              />
                              {(clMode === 'fleet_marks'
                                ? ['pre_mark_waves', 'off_after_waves', 'always_on_above']
                                : clMode === 'off_until_wave'
                                  ? ['on_above']
                                  : []
                              ).map((key) => (
                                <AbilityField
                                  path={['chain_lightning', key]}
                                  problems={problems}
                                  key={key}
                                  label={abilityLabel(key)}
                                  spec={{ type: 'list' }}
                                  node={nodeAt(e.node, ['chain_lightning', key])}
                                  onOptional={
                                    key === 'always_on_above'
                                      ? (enabled) => {
                                          store.optionalRange(['chain_lightning', key], enabled);
                                        }
                                      : undefined
                                  }
                                  onChange={(next) => {
                                    store.edit(['chain_lightning', key], next);
                                  }}
                                />
                              ))}
                            </Tile>
                          </Tiles>
                        ) : (
                          <>
                            <Tile
                              titleId="abilities-arming"
                              title={t.arm.short}
                              end={<AbilityHelp subject={t.arm.short} entries={[t.duration]} />}
                            >
                              <label>
                                {t.armMode.short}
                                <select
                                  className="tp-field-control"
                                  {...abilityErrorAttributes(problems, ['arm', 'on'])}
                                  value={armMode}
                                  onChange={(event) => {
                                    store.switchMode(['arm'], event.target.value);
                                  }}
                                >
                                  <option value="unspecified">{t.unspecified.short}</option>
                                  <option value="always">{t.always.short}</option>
                                  <option value="second_wind">{t.secondWind.short}</option>
                                </select>
                              </label>
                              <AbilityBackendErrors problems={problems} path={['arm', 'on']} />
                              {secondWind
                                ? ['watch_sec', 'immunity_sec'].map((key) => (
                                    <AbilityField
                                      path={['arm', key]}
                                      problems={problems}
                                      key={key}
                                      label={abilityLabel(key)}
                                      spec={{ type: 'float' }}
                                      node={nodeAt(e.node, ['arm', key])}
                                      onChange={(next) => {
                                        store.edit(['arm', key], next);
                                      }}
                                    />
                                  ))
                                : null}
                              <label>
                                <input
                                  {...abilityErrorAttributes(problems, ['end_sprint_after_sw'])}
                                  type="checkbox"
                                  checked={own(raw, 'end_sprint_after_sw') === true}
                                  onChange={(event) => {
                                    store.edit(
                                      ['end_sprint_after_sw'],
                                      abilityNode(event.target.checked),
                                    );
                                  }}
                                />
                                {t.endSprint.short}
                              </label>
                              <AbilityBackendErrors
                                problems={problems}
                                path={['end_sprint_after_sw']}
                              />
                              <CopyLine entry={t.sprintHelp} variant="note" />
                            </Tile>
                            <RuleEditor
                              store={store}
                              node={e.node}
                              vocab={target?.vocab}
                              problems={problems}
                            />
                          </>
                        )}
                      </>
                    )}
                  </fieldset>
                  <details open={e.review || e.outcome === 'conflict'}>
                    <summary>{t.compare.short}</summary>
                    <div className="tp-abilities__compare">
                      <section>
                        <h3>{t.original.short}</h3>
                        <Json value={e.original} />
                      </section>
                      <section>
                        <h3>{t.latest.short}</h3>
                        <Json value={target === null ? undefined : latestAbility(target, e)} />
                      </section>
                      <section>
                        <h3>{t.proposed.short}</h3>
                        <Json value={raw} />
                      </section>
                    </div>
                  </details>
                  {e.review ? (
                    <label>
                      <input
                        type="checkbox"
                        checked={e.reviewed}
                        onChange={(event) => {
                          store.review(event.target.checked);
                        }}
                      />
                      {t.review.short}
                    </label>
                  ) : null}
                  {e.review && !e.reviewed ? <p>{t.reviewNeeded.short}</p> : null}
                  <ActionRow
                    reason={
                      e.dirty && !canSaveAbility(s, now) ? (
                        <Reason id="abilities-save-reason" entry={t.blocked} />
                      ) : undefined
                    }
                  >
                    <button
                      type="button"
                      className="tp-button"
                      data-tier="primary"
                      disabled={!canSaveAbility(s, now)}
                      aria-describedby={
                        e.dirty && !canSaveAbility(s, now) ? 'abilities-save-reason' : undefined
                      }
                      onClick={() => {
                        store.save();
                      }}
                    >
                      {e.family === 'global' ? t.saveGlobal.short : t.save.short}
                    </button>
                    <button
                      type="button"
                      className="tp-button"
                      disabled={
                        (!e.dirty && Object.keys(e.stash).length === 0) ||
                        e.outcome === 'checking' ||
                        e.outcome === 'sending' ||
                        e.outcome === 'unknown'
                      }
                      onClick={() => {
                        setConfirm({
                          action: 'cancel',
                          key: s.key,
                          name: target?.selected ?? null,
                          version: e.version,
                        });
                      }}
                    >
                      {t.cancel.short}
                    </button>
                    {e.family === 'global' ? null : (
                      <button
                        type="button"
                        className="tp-button"
                        disabled={!canSaveAbility(s, now, true)}
                        onClick={() => {
                          setConfirm({
                            action: 'delete',
                            key: s.key,
                            name: target?.selected ?? null,
                            version: e.version,
                          });
                        }}
                      >
                        {t.delete.short}
                      </button>
                    )}
                  </ActionRow>
                  {e.blockedReason === undefined ? null : (
                    <p role="status">{t.targetCheck[e.blockedReason].short}</p>
                  )}
                  {e.outcome === null ? null : (
                    <p role="status">
                      {/* The target and the outcome are separate lines (each its own text). */}
                      <span className="tp-abilities__line">
                        {t.saveTarget.short}
                        {t.space}
                        {shownTarget === null ? (
                          targetCopy.none.short
                        ) : (
                          <ServerValue kind="name" value={shownTarget} />
                        )}
                      </span>
                      <span className="tp-abilities__line">
                        {e.isNew &&
                        e.submission?.deleted === true &&
                        (e.outcome === 'saved' || e.outcome === 'readApplied')
                          ? t.deletedDraft.short
                          : e.outcome === 'unknown'
                            ? t.unknownResult.short
                            : t[e.outcome].short}
                      </span>
                    </p>
                  )}
                  {e.detail === undefined ? null : <Json value={e.detail} />}
                  {e.revision !==
                    sourceRevision(e.family === 'global' ? target?.settings : target?.source) ||
                  e.outcome === 'conflict' ||
                  e.outcome === 'refused' ||
                  e.outcome === 'unknown' ? (
                    <ActionRow>
                      <button
                        type="button"
                        className="tp-button"
                        disabled={!canReconcileAbility(s, now)}
                        onClick={() => {
                          store.reconcile();
                        }}
                      >
                        {t.reconcile.short}
                      </button>
                    </ActionRow>
                  ) : null}
                </>
              )}
            </RegionBody>
          </Region>
        </div>
        <Region id="abilities-evidence" title={t.evidence.short} fallback={t.readError.short}>
          <RegionEnd>
            <AbilityHelp subject={t.evidence.short} entries={[t.evidenceHelp]} />
          </RegionEnd>
          <RegionBody>
            <Facts layout="cells">
              <Fact
                label={t.recordedVerification.short}
                value={
                  recordedVerification === true
                    ? t.yes.short
                    : recordedVerification === false
                      ? t.no.short
                      : t.unknown.short
                }
              />
              <Fact label={t.readiness.short} value={t.readinessUnchecked.short} />
            </Facts>
            <ActionRow>
              <a className="tp-button" href="#recognition">
                {t.recognition.short}
              </a>
            </ActionRow>
            <details>
              <summary>{t.savedDetails.short}</summary>
              <Json value={ownPath(target?.source, 'profile', 'player')} />
            </details>
            {(['problems', 'warnings', 'validator_error'] as const).map((key) => {
              const values =
                key === 'validator_error'
                  ? [own(target?.source, key)]
                  : (list(own(target?.source, key)) ?? []);
              if (!values.some((value) => abilityMessage(value) !== null)) return null;
              return (
                <Tile
                  key={key}
                  titleId={`abilities-${key}`}
                  title={
                    key === 'problems'
                      ? t.problems.short
                      : key === 'warnings'
                        ? t.warnings.short
                        : t.validator.short
                  }
                >
                  {values.map((value, index) => {
                    const shown = abilityMessage(value);
                    return shown === null ? null : (
                      <p key={index}>
                        <ServerValue kind="message" value={shown} />
                      </p>
                    );
                  })}
                </Tile>
              );
            })}
          </RegionBody>
        </Region>
        <ConfirmDialog
          open={confirm !== null}
          title={confirm?.action === 'delete' ? t.confirmDelete.short : t.cancelTitle.short}
          body={confirm?.action === 'delete' ? t.deleteHelp.short : t.cancelHelp.short}
          confirmLabel={confirm?.action === 'delete' ? t.delete.short : t.cancel.short}
          cancelLabel={t.keep.short}
          tier="primary"
          onCancel={() => {
            setConfirm(null);
          }}
          onConfirm={() => {
            const current = store.getSnapshot(),
              entry = currentAbility(current);
            if (
              current.key === confirm?.key &&
              currentAbilities(current)?.selected === confirm.name &&
              entry?.version === confirm.version
            ) {
              if (confirm.action === 'delete') store.save(true);
              else store.cancel();
            }
            setConfirm(null);
          }}
        />
      </main>
    </>
  );
}
