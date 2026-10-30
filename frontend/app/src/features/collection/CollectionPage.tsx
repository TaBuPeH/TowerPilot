import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { collection as t } from '../../copy/collection.ts';
import type { CopyEntry, CopyNode } from '../../copy/text.ts';
import {
  selectCollectionPage,
  selectCollectionTargetName,
  type CollectionPageVM,
  type PolicyEditorVM,
  type ProfilePatchCommand,
} from '../../store/selectors/collection.ts';
import type { SaveOutcomeVM, SettingsModuleVM } from '../../store/selectors/settingsModule.ts';
import type { CommandVM } from '../../store/viewModels.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { CopyLine } from '../../ui/CopyLine.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { RegionBoundary } from '../../ui/RegionBoundary.tsx';
import { Region, RegionBody, RegionEnd } from '../../ui/Region.tsx';
import { pageHref, type PageTarget } from '../../ui/routes.ts';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';
import { ShownList } from '../../ui/ShownList.tsx';
import { Tile } from '../../ui/Tile.tsx';
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
import { SaveOutcome } from '../settings/SaveOutcome.tsx';
import { SettingsModuleForm } from '../settings/SettingsModuleForm.tsx';
import { EditingTargetLine } from '../profiles/EditingTargetLine.tsx';
import './collection.css';

/** Orbit fields drawn under "Tap geometry": native screen pixels, advanced. */
const GEOMETRY = [
  'gem_orbit.center',
  'gem_orbit.radius',
  'gem_orbit.angle_deg',
  'gem_orbit.jitter_frac',
  'gem_orbit.spread_deg',
];

function reasonOf(reason: string | null): CopyEntry {
  const reasons: Readonly<Record<string, CopyEntry>> = t.reasons;
  return (reason === null ? undefined : reasons[reason]) ?? t.reasons.context_unknown;
}

function TargetLabel({ id }: { readonly id?: string }) {
  const name = useView(selectCollectionTargetName);
  return name === null ? null : (
    <span id={id} data-collection-target="true">
      <span>{t.profile.short}</span>
      <ServerValue kind="name" value={name} />
    </span>
  );
}

/** Run names remain separate list items; only the selected policy offers run links. */
function PolicyUses({
  runs,
  links,
}: {
  readonly runs: PolicyEditorVM['usedBy'];
  readonly links?: PolicyEditorVM['usedByKeys'];
}) {
  if (runs.length === 0) return <span>{t.policies.unused.short}</span>;
  return (
    <ul className="tp-collection__uses">
      {runs.map((run, index) => (
        <li key={run}>
          {links === undefined ? (
            <ServerValue kind="id" value={run} />
          ) : (
            <a href={pageHref('runs', { kind: 'saved', run: links[index] ?? '' })}>
              <ServerValue kind="id" value={run} />
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

function PatchOutcome({ outcome }: { readonly outcome: SaveOutcomeVM }) {
  const { commands } = useDashboard();
  return (
    <div data-collection-outcome="true">
      <TargetLabel />
      <SaveOutcome outcome={outcome} onDismiss={commands.dismiss} />
    </div>
  );
}

/** One profile patch button: enabled with its command, or disabled with its visible reason. */
function PatchButton({
  vm,
  label,
  focusKey,
  id,
  onSend,
  before,
  after,
}: {
  readonly vm: CommandVM<ProfilePatchCommand>;
  readonly label: CopyEntry;
  readonly focusKey: string;
  readonly id?: string;
  readonly onSend: (command: ProfilePatchCommand) => void;
  readonly before?: CopyNode;
  readonly after?: CopyNode;
}) {
  const own = useId();
  const inactive = useCollection(null).inactive;
  const { command } = vm;
  const enabled = vm.enabled && command !== null;
  // F6C-03: one button element whatever its state, so a focused button keeps focus.
  return (
    <ActionRow
      reason={enabled ? null : <Reason id={`${own}-reason`} entry={reasonOf(vm.reason)} />}
    >
      {before}
      {inactive ? <TargetLabel id={`${own}-target`} /> : null}
      <Button
        {...(id === undefined ? {} : { id })}
        {...(inactive ? { targetId: `${own}-target` } : {})}
        tier="secondary"
        label={label.short}
        focusKey={focusKey}
        busy={vm.sending}
        {...(enabled ? {} : { disabled: true as const, reasonId: `${own}-reason` })}
        onClick={() => {
          if (command !== null && vm.enabled) onSend(command);
        }}
      />
      {after}
    </ActionRow>
  );
}

function TextField({
  label,
  value,
  scope,
  path,
  helpId,
  invalid,
  pin = {},
}: {
  readonly label: CopyEntry;
  readonly value: string;
  readonly scope: string | null;
  readonly path: string;
  readonly helpId?: string;
  readonly invalid?: CopyEntry | null;
  /** Draft values to record with this edit when the draft has none (the copy's source). */
  readonly pin?: Readonly<Record<string, string>>;
}) {
  const { commands } = useDashboard();
  const own = useId();
  const described = [helpId ?? null, invalid ? `${own}-invalid` : null]
    .filter((entry) => entry !== null)
    .join(' ');
  return (
    <label className="tp-collection__field">
      <span id={`${own}-label`}>{label.short}</span>
      <input
        type="text"
        className="tp-collection__input tp-field-control"
        autoComplete="off"
        spellCheck={false}
        value={value}
        disabled={scope === null}
        aria-labelledby={`${own}-label`}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={described === '' ? undefined : described}
        data-focus-key={`${scope ?? 'none'}:${path}`}
        onChange={(event) => {
          if (scope === null) return;
          for (const [key, value] of Object.entries(pin)) commands.editDraft(scope, key, value);
          commands.editDraft(scope, path, event.currentTarget.value);
        }}
      />
      {invalid ? (
        <span id={`${own}-invalid`} className="tp-collection__invalid">
          {invalid.short}
        </span>
      ) : null}
    </label>
  );
}

function CollectionRegion({
  id,
  title,
  children,
}: {
  readonly id: string;
  readonly title: CopyEntry;
  readonly children: CopyNode;
}) {
  return (
    <Region
      id={id}
      title={title.short}
      fallback={<RevocationFallback part="collection" />}
      resetKey={useEpochKey()}
      onError={usePartError(id)}
    >
      <FaultPoint part={id} />
      {children}
    </Region>
  );
}

/** Settings module form wired to the page's commands. */
function Module({
  vm,
  legend,
  intro,
  advanced,
  notes,
}: {
  readonly vm: SettingsModuleVM | null;
  readonly legend: CopyEntry;
  readonly intro?: CopyEntry;
  readonly advanced?: { readonly label: CopyEntry; readonly paths: readonly string[] };
  readonly notes?: Readonly<Record<string, CopyEntry>>;
}) {
  const { commands } = useDashboard();
  const page = useCollection(null);
  if (vm === null) return null;
  return (
    <fieldset className="tp-collection__module" disabled={page.inactive} data-active-profile="true">
      <SettingsModuleForm
        vm={vm}
        legend={legend}
        {...(intro === undefined ? {} : { intro })}
        {...(advanced === undefined ? {} : { advanced })}
        {...(notes === undefined ? {} : { notes })}
        onEdit={commands.editDraft}
        onDiscard={commands.discardDraft}
        onSave={(command) => {
          if (!page.inactive) void commands.saveSettings(command);
        }}
        onDismiss={commands.dismiss}
      />
    </fieldset>
  );
}

function useCollection(selected: string | null): CollectionPageVM {
  const select = useCallback(
    (state: Parameters<typeof selectCollectionPage>[0]) => selectCollectionPage(state, selected),
    [selected],
  );
  return useView(select);
}

function ReadLine({ vm }: { readonly vm: CollectionPageVM }) {
  if (vm.settingsRead === 'ready' && vm.sourceRead === 'ready') return null;
  if (vm.settingsRead === 'other_context') return <CopyLine entry={t.otherContext} />;
  const unavailable = vm.settingsRead === 'unavailable' || vm.sourceRead === 'unavailable';
  return <p>{(unavailable ? t.unavailable : t.reading).short}</p>;
}

/** C1: profile-wide rewards. */
function RewardsSection({ selected }: { readonly selected: string | null }) {
  const vm = useCollection(selected);
  const { commands } = useDashboard();
  return (
    <>
      <RegionEnd>
        {vm.settingsAgeSec === null ? null : <span>{t.readAge(vm.settingsAgeSec)}</span>}
      </RegionEnd>
      <RegionBody>
        <Tile titleId="collection-saved-title" title={t.savedSettings.short} intro={t.scope}>
          <NotSent />
          <Facts layout="cells">
            <Fact
              label={t.activeProfile.short}
              value={
                vm.profile === null ? (
                  t.noActiveProfile.short
                ) : (
                  <ServerValue kind="name" value={vm.profile} />
                )
              }
            />
            <Fact
              label={t.rewards.quotaLabel.short}
              value={
                <>
                  <span>{t.rewards.quota.short}</span>
                  {t.rewards.quota.why === undefined ? null : (
                    <HelpTip label={t.rewards.quotaLabel.short}>{t.rewards.quota.why}</HelpTip>
                  )}
                </>
              }
            />
          </Facts>
          {vm.inactive ? <div className="tp-reason">{t.activeOnly.short}</div> : null}
          <ReadLine vm={vm} />
          <ActionRow>
            <Button
              tier="quiet"
              label={t.check.short}
              focusKey="collection:check"
              onClick={() => {
                commands.checkCollection();
              }}
            />
            <a href={pageHref('dashboard')}>{t.dashboard.short}</a>
          </ActionRow>
        </Tile>
        <Module vm={vm.rewards} legend={t.rewards.title} intro={t.rewards.note} />
      </RegionBody>
    </>
  );
}

function Editor({
  editor,
  focusOnOpen,
}: {
  readonly editor: PolicyEditorVM;
  /** Opened from a link to this policy (`#collection/policy/<id>`): its heading takes focus. */
  readonly focusOnOpen: boolean;
}) {
  const { commands } = useDashboard();
  const [confirming, setConfirming] = useState(false);
  const triggerId = useId();
  const confirmId = useId();
  const restoreDeleteFocus = useRef(false);
  useEffect(() => {
    if (confirming) document.getElementById(confirmId)?.focus();
    else if (restoreDeleteFocus.current) {
      document.getElementById(triggerId)?.focus();
      restoreDeleteFocus.current = false;
    }
  }, [confirming, confirmId, triggerId]);
  useEffect(() => {
    if (focusOnOpen) document.getElementById('collection-editor-title')?.focus();
  }, [focusOnOpen]);
  const send = (command: ProfilePatchCommand) => {
    void commands.patchProfile(command);
  };
  return (
    <Tile
      className="tp-collection__editor"
      titleId="collection-editor-title"
      title={
        <>
          {editor.label === null ? null : <ServerValue kind="name" value={editor.label} />}
          {editor.idText === null ? null : (
            <span className="tp-collection__id">
              <ServerValue kind="id" value={editor.idText} />
            </span>
          )}
        </>
      }
    >
      <div data-used-by="true">
        <Facts>
          <Fact
            label={t.policies.usedBy.short}
            value={<PolicyUses runs={editor.usedBy} links={editor.usedByKeys} />}
          />
        </Facts>
      </div>
      <div className="tp-collection__row tp-control-row" data-form="label">
        <PatchButton
          before={
            <TextField
              label={t.editor.label}
              value={editor.labelDraft}
              scope={editor.labelScope}
              path="label"
              pin={editor.pin}
            />
          }
          vm={editor.saveLabel}
          label={t.editor.saveLabel}
          focusKey={`collection:label:${editor.id}`}
          onSend={send}
          after={
            editor.labelDirty && editor.labelScope !== null ? (
              <Button
                tier="quiet"
                label={t.editor.cancel.short}
                focusKey={`collection:label:${editor.id}:cancel`}
                onClick={() => {
                  if (editor.labelScope !== null) commands.discardDraft(editor.labelScope);
                }}
              />
            ) : null
          }
        />
      </div>
      {editor.labelOutcome === null ? null : <PatchOutcome outcome={editor.labelOutcome} />}
      <Module
        vm={editor.gather}
        legend={t.editor.gather}
        notes={{ guild_store: t.editor.guildStore }}
      />
      <p className="tp-collection__help">
        {t.editor.timing.short}
        {t.editor.timing.why === undefined ? null : (
          <HelpTip label={t.editor.timing.short}>{t.editor.timing.why}</HelpTip>
        )}
      </p>
      <Module vm={editor.ad} legend={t.editor.ad} />
      <Module
        vm={editor.orbit}
        legend={t.editor.orbit}
        advanced={{ label: t.editor.geometry, paths: GEOMETRY }}
      />
      <div className="tp-collection__row tp-control-row" data-form="delete">
        {confirming && editor.remove.command !== null ? (
          <div className="tp-collection__confirm" role="group" aria-label={t.editor.remove.short}>
            <CopyLine entry={t.editor.confirmRemove} />
            <ActionRow>
              <Button
                tier="secondary"
                label={t.editor.confirm.short}
                id={confirmId}
                focusKey="collection:delete:confirm"
                onClick={() => {
                  const command = editor.remove.command;
                  setConfirming(false);
                  if (command !== null) send(command);
                }}
              />
              <Button
                tier="quiet"
                label={t.editor.cancel.short}
                focusKey="collection:delete:cancel"
                onClick={() => {
                  restoreDeleteFocus.current = true;
                  setConfirming(false);
                }}
              />
            </ActionRow>
          </div>
        ) : (
          <PatchButton
            vm={editor.remove}
            id={triggerId}
            label={t.editor.remove}
            focusKey={`collection:delete:${editor.id}`}
            onSend={() => {
              setConfirming(true);
            }}
          />
        )}
      </div>
      {editor.removeOutcome === null ? null : <PatchOutcome outcome={editor.removeOutcome} />}
    </Tile>
  );
}

/** C2 to C5: the policies, the selected one's editor, and a copy. */
function PoliciesSection({ selected }: { readonly selected: string | null }) {
  const vm = useCollection(selected);
  const { commands } = useDashboard();
  const send = (command: ProfilePatchCommand) => {
    void commands.patchProfile(command);
  };
  return (
    <RegionBody>
      <Tile
        titleId="collection-policy-list-title"
        title={t.policies.list.short}
        intro={t.policies.intro}
      >
        <EditingTargetLine />
        {vm.sourceRead === 'ready' && vm.policies.length === 0 ? (
          <p>{t.policies.none.short}</p>
        ) : null}
        {vm.policies.length === 0 ? null : (
          <ShownList
            className="tp-collection__policies"
            items={vm.policies}
            itemKey={(row) => row.id}
            more={t.policies.more.short}
            render={(row) => (
              <div data-policy={row.id} data-selected={row.selected}>
                <a
                  href={pageHref('collection', { kind: 'policy', policy: row.id })}
                  aria-current={row.selected ? 'true' : undefined}
                >
                  {row.name === null ? null : <ServerValue kind="name" value={row.name} />}
                </a>
                {row.selected ? null : (
                  <Facts>
                    <Fact
                      label={t.policies.usedBy.short}
                      value={<PolicyUses runs={row.usedBy} />}
                    />
                  </Facts>
                )}
              </div>
            )}
          />
        )}
        {vm.missing.length === 0 ? null : (
          <div className="tp-collection__problems" data-missing="true">
            <p>{t.policies.missing.short}</p>
            <ul>
              {vm.missing.map((entry, index) => (
                <li key={String(index)}>
                  {entry.run === null ? null : <ServerValue kind="id" value={entry.run} />}
                  {entry.policy === null ? null : <ServerValue kind="id" value={entry.policy} />}
                </li>
              ))}
            </ul>
          </div>
        )}
        {vm.problems.length === 0 ? null : (
          <div>
            <Facts>
              <Fact label={t.policies.problems.short} value={vm.problems.length} />
            </Facts>
            <ul className="tp-collection__problems">
              {vm.problems.map((problem, index) => (
                <li key={String(index)}>
                  <ServerValue kind="message" value={problem} />
                </li>
              ))}
            </ul>
          </div>
        )}
        {vm.validatorError === null ? null : (
          <p>
            <span>{t.policies.validatorError.short}</span>
            <ServerValue kind="message" value={vm.validatorError} />
          </p>
        )}
      </Tile>
      {vm.editor === null ? null : (
        <Editor key={vm.editor.id} editor={vm.editor} focusOnOpen={selected === vm.editor.id} />
      )}
      {vm.deletedOutcomes.map((entry) => (
        <Tile
          key={entry.outcome.id}
          titleId={`collection-deletion-${entry.outcome.id}`}
          title={t.editor.deletion.short}
          end={entry.id === null ? null : <ServerValue kind="id" value={entry.id} />}
        >
          <PatchOutcome outcome={entry.outcome} />
        </Tile>
      ))}
      <Tile titleId="collection-new-policies-title" title={t.policies.new.short}>
        <p id="collection-policy-id-help" className="tp-collection__help">
          {t.copy.idHelp.short}
        </p>
        <fieldset className="tp-collection__form tp-tile" data-form="create">
          <legend className="tp-tile__title">{t.create.title.short}</legend>
          <CopyLine entry={t.create.intro} variant="intro" />
          {vm.create.choices.length === 0 ? (
            <div data-first-policy="unavailable">
              <CopyLine entry={t.policies.noTemplate} />
            </div>
          ) : (
            <div className="tp-collection__row tp-control-row">
              <label className="tp-collection__field">
                <span>{t.create.template.short}</span>
                <select
                  className="tp-field-control"
                  value={vm.create.template}
                  disabled={vm.create.scope === null}
                  data-focus-key="collection:create:template"
                  onChange={(event) => {
                    if (vm.create.scope !== null) {
                      for (const [key, value] of Object.entries(vm.create.pin))
                        commands.editDraft(vm.create.scope, key, value);
                      commands.editDraft(vm.create.scope, 'template', event.currentTarget.value);
                    }
                  }}
                >
                  {vm.create.choices.map((choice) => (
                    <ServerOption key={choice.key} value={choice.key} label={choice.label} />
                  ))}
                </select>
              </label>
              <TextField
                label={t.create.id}
                value={vm.create.id}
                scope={vm.create.scope}
                path="id"
                pin={vm.create.pin}
                helpId="collection-policy-id-help"
                invalid={
                  vm.create.id !== '' && vm.create.idReason !== null
                    ? reasonOf(vm.create.idReason)
                    : null
                }
              />
            </div>
          )}
          <div className="tp-collection__row tp-control-row">
            <PatchButton
              vm={vm.create.save}
              label={t.create.save}
              focusKey="collection:create:save"
              onSend={send}
              after={
                vm.create.dirty && vm.create.scope !== null ? (
                  <Button
                    tier="quiet"
                    label={t.editor.cancel.short}
                    focusKey="collection:create:cancel"
                    onClick={() => {
                      if (vm.create.scope !== null) commands.discardDraft(vm.create.scope);
                    }}
                  />
                ) : null
              }
            />
          </div>
          {vm.create.outcome === null ? null : <PatchOutcome outcome={vm.create.outcome} />}
        </fieldset>
        <fieldset className="tp-collection__form tp-tile" data-form="copy">
          <legend className="tp-tile__title">{t.copy.title.short}</legend>
          <CopyLine entry={t.copy.intro} variant="intro" />
          <div className="tp-collection__row tp-control-row">
            <label className="tp-collection__field">
              <span>{t.copy.from.short}</span>
              <select
                className="tp-field-control"
                value={vm.copy.from}
                disabled={vm.copy.scope === null}
                data-focus-key="collection:copy:from"
                onChange={(event) => {
                  if (vm.copy.scope !== null) {
                    for (const [key, value] of Object.entries(vm.copy.pin))
                      commands.editDraft(vm.copy.scope, key, value);
                    commands.editDraft(vm.copy.scope, 'from', event.currentTarget.value);
                  }
                }}
              >
                {vm.policies.map((row) =>
                  row.name === null ? null : (
                    <ServerOption key={row.id} value={row.id} label={row.name} />
                  ),
                )}
              </select>
            </label>
            <TextField
              label={t.copy.id}
              value={vm.copy.id}
              scope={vm.copy.scope}
              path="id"
              pin={vm.copy.pin}
              helpId="collection-policy-id-help"
              invalid={
                vm.copy.id !== '' && vm.copy.idReason !== null ? reasonOf(vm.copy.idReason) : null
              }
            />
            <TextField
              label={t.copy.label}
              value={vm.copy.label}
              scope={vm.copy.scope}
              path="label"
              pin={vm.copy.pin}
            />
          </div>
          <div className="tp-collection__row tp-control-row">
            <PatchButton
              vm={vm.copy.save}
              label={t.copy.save}
              focusKey="collection:copy:save"
              onSend={send}
              after={
                vm.copy.dirty && vm.copy.scope !== null ? (
                  <Button
                    tier="quiet"
                    label={t.editor.cancel.short}
                    focusKey="collection:copy:cancel"
                    onClick={() => {
                      if (vm.copy.scope !== null) commands.discardDraft(vm.copy.scope);
                    }}
                  />
                ) : null
              }
            />
          </div>
          {vm.copy.outcome === null ? null : <PatchOutcome outcome={vm.copy.outcome} />}
        </fieldset>
      </Tile>
    </RegionBody>
  );
}

function choreEntry(name: string): CopyEntry | null {
  const names: Readonly<Record<string, CopyEntry>> = t.chores.names;
  return names[name] ?? null;
}

/** C6: the between-run chores (an opt-out list), saved as one whole list. */
function ChoresSection() {
  const vm = useCollection(null);
  const { commands } = useDashboard();
  const chores = vm.chores;
  return (
    <RegionBody>
      <Tile titleId="collection-chores-title" title={t.chores.saved.short} intro={t.chores.intro}>
        <EditingTargetLine />
        <ul className="tp-collection__chores">
          {chores.rows.map((row) => {
            const known = choreEntry(row.name);
            return (
              <li key={row.name} data-chore={row.name} data-changed={row.changed}>
                <label className="tp-collection__check">
                  <input
                    type="checkbox"
                    checked={row.enabled}
                    disabled={chores.scope === null || row.unknown}
                    data-focus-key={`collection:chore:${row.name}`}
                    onChange={(event) => {
                      if (chores.scope !== null) {
                        for (const [key, value] of Object.entries(chores.pin))
                          commands.editDraft(chores.scope, key, value);
                        commands.editDraft(chores.scope, row.name, event.currentTarget.checked);
                      }
                    }}
                  />
                  <span>
                    {known === null ? (
                      row.nameText === null ? null : (
                        <ServerValue kind="id" value={row.nameText} />
                      )
                    ) : (
                      known.short
                    )}
                  </span>
                </label>
                {known?.why === undefined ? null : (
                  <HelpTip label={known.short}>{known.why}</HelpTip>
                )}
                {row.name === 'shatter' ? (
                  <span className="tp-collection__warn">{t.chores.destroys.short}</span>
                ) : null}
                {row.unknown ? (
                  <span className="tp-collection__help">{t.chores.unknown.short}</span>
                ) : null}
              </li>
            );
          })}
        </ul>
        <div className="tp-collection__row tp-control-row">
          <PatchButton
            vm={chores.save}
            label={t.chores.save}
            focusKey="collection:chores:save"
            onSend={(command) => {
              void commands.patchProfile(command);
            }}
            after={
              chores.scope !== null && chores.rows.some((row) => row.changed) ? (
                <Button
                  tier="quiet"
                  label={t.editor.cancel.short}
                  focusKey="collection:chores:discard"
                  onClick={() => {
                    if (chores.scope !== null) commands.discardDraft(chores.scope);
                  }}
                />
              ) : null
            }
          />
        </div>
        {chores.outcome === null ? null : <PatchOutcome outcome={chores.outcome} />}
      </Tile>
    </RegionBody>
  );
}

function NotSent() {
  const vm = useCollection(null);
  if (vm.notSent === null) return null;
  const notSent: Readonly<Record<string, CopyEntry>> = t.notSent;
  const entry = notSent[vm.notSent];
  if (entry === undefined) return null;
  return (
    <div className="tp-collection__not-sent" data-not-sent={vm.notSent} role="status">
      <span>{entry.short}</span>
      {entry.why === undefined ? null : <Why why={entry.why} />}
    </div>
  );
}

/**
 * The Collection page (collection brief C1 to C6): global rewards, the gathering policies
 * and their three settings modules, a policy copy, label and delete, and the between-run
 * chores. Opening it reads the settings, the active profile's source and the vocabulary;
 * every save is one explicit request, never resent.
 */
export function CollectionPage({ target }: { readonly target: PageTarget | null }) {
  const { commands } = useDashboard();
  const selected = target?.kind === 'policy' ? target.policy : null;
  useEffect(() => {
    commands.openCollection(true);
    return () => {
      commands.openCollection(false);
    };
  }, [commands]);
  return (
    <>
      <header className="tp-topbar">
        <RegionBoundary
          fallback={<RevocationFallback part="topBar" />}
          resetKey={useEpochKey()}
          onError={usePartError('topBar')}
        >
          <FaultPoint part="topBar" />
          <TopBar pageId="collection" title={t.pageName.short} />
        </RegionBoundary>
      </header>
      <main id="main" className="tp-main" tabIndex={-1}>
        <FaultPoint part="page" />
        <div className="tp-collection">
          <CollectionRegion id="collectionRewards" title={t.rewards.title}>
            <RewardsSection selected={selected} />
          </CollectionRegion>
          <CollectionRegion id="collectionPolicies" title={t.policies.title}>
            <PoliciesSection selected={selected} />
          </CollectionRegion>
          <CollectionRegion id="collectionChores" title={t.chores.title}>
            <ChoresSection />
          </CollectionRegion>
        </div>
      </main>
    </>
  );
}
