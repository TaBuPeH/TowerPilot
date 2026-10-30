import { EditingTargetPicker } from './EditingTargetPicker.tsx';
import { editingTarget as targetCopy } from '../../copy/editingTarget.ts';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { profiles as t } from '../../copy/profiles.ts';
import type { CopyEntry, CopyNode, CopyText } from '../../copy/text.ts';
import {
  selectProfilesPage,
  type ProfileRowVM,
  type ProfilesSectionVM,
  type ValidationVM,
} from '../../store/selectors/profiles.ts';
import type { ProfileWriteCommand } from '../../store/commands.ts';
import type { CommandVM } from '../../store/viewModels.ts';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { CopyLine } from '../../ui/CopyLine.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { Tile, Tiles } from '../../ui/Tile.tsx';
import { Button } from '../../ui/Button.tsx';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { RegionBoundary } from '../../ui/RegionBoundary.tsx';
import { Region, RegionBody, RegionEnd } from '../../ui/Region.tsx';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import {
  FaultPoint,
  useDashboard,
  useEpochKey,
  usePartError,
  useView,
} from '../dashboard/context.ts';
import { RevocationFallback } from '../dashboard/shared/RevocationFallback.tsx';
import { TopBar } from '../dashboard/topBar/TopBar.tsx';
import { ProfileOutcome } from './ProfileOutcome.tsx';
import './profiles.css';

const RECOGNITION = '#recognition';
const EMULATORS = '#emulators';

function reasonOf(reason: string | null): CopyEntry {
  const reasons: Readonly<Record<string, CopyEntry>> = t.reasons;
  return (reason === null ? undefined : reasons[reason]) ?? t.reasons.context_unknown;
}

/** One write button: enabled with its command, or disabled with its visible reason. */
function WriteButton({
  vm,
  label,
  focusKey,
  onSend,
  tier = 'secondary',
}: {
  readonly vm: CommandVM<ProfileWriteCommand>;
  readonly label: CopyEntry;
  readonly focusKey: string;
  readonly onSend: (command: ProfileWriteCommand) => void;
  readonly tier?: 'secondary' | 'quiet';
}) {
  const own = useId();
  const { command } = vm;
  const enabled = vm.enabled && command !== null;
  return (
    <ActionRow
      reason={enabled ? undefined : <Reason id={`${own}-reason`} entry={reasonOf(vm.reason)} />}
    >
      <Button
        tier={tier}
        label={label.short}
        focusKey={focusKey}
        busy={vm.sending}
        {...(enabled ? { disabled: false } : { disabled: true, reasonId: `${own}-reason` })}
        onClick={() => {
          if (enabled) onSend(command);
        }}
      />
    </ActionRow>
  );
}

function TextField({
  label,
  value,
  scope,
  path,
  help,
  invalid,
}: {
  readonly label: CopyEntry;
  readonly value: string;
  readonly scope: string | null;
  readonly path: string;
  readonly help?: CopyEntry;
  readonly invalid?: CopyEntry | null;
}) {
  const { commands } = useDashboard();
  const own = useId();
  const described = [help === undefined ? null : `${own}-help`, invalid ? `${own}-invalid` : null]
    .filter((entry) => entry !== null)
    .join(' ');
  return (
    <label className="tp-profiles__field">
      <span>{label.short}</span>
      <input
        type="text"
        className="tp-profiles__input tp-field-control"
        autoComplete="off"
        spellCheck={false}
        value={value}
        disabled={scope === null}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={described === '' ? undefined : described}
        data-focus-key={`${scope ?? 'none'}:${path}`}
        onChange={(event) => {
          if (scope !== null) commands.editDraft(scope, path, event.currentTarget.value);
        }}
      />
      {help === undefined ? null : (
        <span id={`${own}-help`} className="tp-profiles__help">
          {help.short}
        </span>
      )}
      {invalid ? (
        <span id={`${own}-invalid`} className="tp-profiles__invalid">
          {invalid.short}
        </span>
      ) : null}
    </label>
  );
}

/** A heading's background, as a help icon beside it; nothing without a "why". */
function helpFor(subject: CopyText, why: CopyText | undefined) {
  return why === undefined ? undefined : <HelpTip label={subject}>{why}</HelpTip>;
}

function CheckField({
  label,
  checked,
  scope,
  path,
  consent,
  condition,
}: {
  readonly label: CopyEntry;
  readonly checked: boolean;
  readonly scope: string | null;
  readonly path: string;
  /** Record this key when ticked (consent for one reviewed write), '' when cleared. */
  readonly consent?: string;
  readonly condition?: CopyEntry;
}) {
  const { commands } = useDashboard();
  const own = useId();
  return (
    <span className="tp-profiles__check">
      <label>
        <input
          type="checkbox"
          aria-describedby={condition === undefined ? undefined : `${own}-condition`}
          checked={checked}
          disabled={scope === null}
          data-focus-key={`${scope ?? 'none'}:${path}`}
          onChange={(event) => {
            if (scope === null) return;
            const ticked = event.currentTarget.checked;
            commands.editDraft(scope, path, consent === undefined ? ticked : ticked ? consent : '');
          }}
        />
        <span>{label.short}</span>
      </label>
      {condition === undefined ? null : (
        <span className="tp-profiles__condition">
          <span id={`${own}-condition`}>{condition.short}</span>
          {condition.why === undefined ? null : (
            <HelpTip label={condition.short}>{condition.why}</HelpTip>
          )}
        </span>
      )}
      {label.why === undefined ? null : <HelpTip label={label.short}>{label.why}</HelpTip>}
    </span>
  );
}

function ProfileSelect({
  label,
  value,
  rows,
  scope,
  path,
  empty,
  onPick,
}: {
  readonly label: CopyEntry;
  readonly value: string;
  readonly rows: readonly ProfileRowVM[];
  readonly scope: string | null;
  readonly path: string;
  readonly empty: CopyEntry;
  readonly onPick?: (name: string) => void;
}) {
  const { commands } = useDashboard();
  return (
    <label className="tp-profiles__field">
      <span>{label.short}</span>
      <select
        className="tp-field-control"
        value={value}
        disabled={scope === null}
        data-focus-key={`${scope ?? 'none'}:${path}`}
        onChange={(event) => {
          const next = event.currentTarget.value;
          if (scope !== null) commands.editDraft(scope, path, next);
          onPick?.(next);
        }}
      >
        {rows.some((row) => row.key === value) ? null : <option value="">{empty.short}</option>}
        {rows.map((row) =>
          row.name === null ? null : (
            <ServerOption key={row.key} value={row.key} label={row.name} />
          ),
        )}
      </select>
    </label>
  );
}

function Validation({ validation }: { readonly validation: ValidationVM }) {
  return (
    <div className="tp-profiles__validation" data-validation="true">
      {validation.validatorError !== null ? (
        <span data-validator-error="true">
          <span>{t.files.validatorUnavailable.short}</span>
          <ServerValue kind="message" value={validation.validatorError} />
        </span>
      ) : validation.problems.length === 0 ? (
        <span>{t.files.valid.short}</span>
      ) : (
        <Disclosure label={t.files.problems(validation.problems.length)}>
          <ul className="tp-profile-problems">
            {validation.problems.map((problem, index) => (
              <li key={String(index)}>
                <ServerValue kind="message" value={problem} />
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
      {validation.warnings.length === 0 ? null : (
        <Disclosure label={t.files.warnings(validation.warnings.length)}>
          <ul className="tp-profile-problems">
            {validation.warnings.map((warning, index) => (
              <li key={String(index)}>
                <ServerValue kind="message" value={warning} />
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
    </div>
  );
}

function ProfilesRegion({
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
      fallback={<RevocationFallback part="profiles" />}
      resetKey={useEpochKey()}
      onError={usePartError(id)}
    >
      <FaultPoint part={id} />
      {children}
    </Region>
  );
}

/**
 * F6-14 PE1 (classic Active selector "none - legacy constants"): unbind the active profile,
 * behind a confirmation. An explicit action beside Activate, never an empty picker option.
 */
function UnbindProfile({
  unbind,
  onSend,
}: {
  readonly unbind: NonNullable<ProfilesSectionVM['unbind']>;
  readonly onSend: (command: ProfileWriteCommand) => void;
}) {
  const own = useId();
  const [asking, setAsking] = useState<ProfileWriteCommand | null>(null);
  const { command } = unbind.command;
  // The confirmation applies only to the context it was opened in (a context change closes it).
  const current =
    asking !== null && command !== null && asking.epoch === command.epoch ? command : null;
  if (asking !== null && current === null) setAsking(null);
  return (
    <div data-form="unbind">
      <Tile
        as="fieldset"
        className="tp-profiles__form"
        titleId="profiles-unbind-title"
        title={t.unbind.title.short}
      >
        <Facts>
          <Fact
            label={t.unbind.active.short}
            value={
              unbind.active === null ? (
                t.unbind.none.short
              ) : (
                <ServerValue kind="name" value={unbind.active} />
              )
            }
          />
        </Facts>
        <CopyLine entry={t.unbind.consequence} variant="note" />
        <ActionRow
          reason={
            unbind.command.enabled ? undefined : (
              <Reason id={`${own}-reason`} entry={reasonOf(unbind.command.reason)} />
            )
          }
        >
          <Button
            tier="quiet"
            label={t.unbind.submit.short}
            focusKey="profiles:unbind:submit"
            busy={unbind.command.sending}
            {...(unbind.command.enabled
              ? { disabled: false }
              : { disabled: true, reasonId: `${own}-reason` })}
            onClick={() => {
              if (unbind.command.enabled && command !== null) setAsking(command);
            }}
          />
        </ActionRow>
        {unbind.outcome === null ? null : <ProfileOutcome outcome={unbind.outcome} />}
        <ConfirmDialog
          open={current !== null}
          tier="primary"
          title={t.unbind.confirm.short}
          body={
            <div>
              <p>{t.unbind.effect.short}</p>
              <Facts>
                <Fact
                  label={t.unbind.account.short}
                  value={
                    unbind.account === null ? (
                      t.unbind.installation.short
                    ) : (
                      <ServerValue kind="name" value={unbind.account} />
                    )
                  }
                />
                <Fact
                  label={t.unbind.emulator.short}
                  value={
                    unbind.instance === null ? (
                      t.outcomes.contextUnknown.short
                    ) : (
                      <ServerValue kind="name" value={unbind.instance} />
                    )
                  }
                />
                <Fact
                  label={t.unbind.profile.short}
                  value={
                    unbind.active === null ? (
                      t.outcomes.contextUnknown.short
                    ) : (
                      <ServerValue kind="name" value={unbind.active} />
                    )
                  }
                />
              </Facts>
            </div>
          }
          confirmLabel={t.unbind.send.short}
          cancelLabel={t.unbind.cancel.short}
          onCancel={() => {
            setAsking(null);
          }}
          onConfirm={() => {
            setAsking(null);
            if (current !== null) onSend(current);
          }}
        />
      </Tile>
    </div>
  );
}

/** P1, P2: the emulator's local account, the switch, and a new account. */
function AccountSection() {
  const vm = useView(selectProfilesPage);
  const { commands } = useDashboard();
  const account = vm.account;
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const question = useId();
  /** Close the switch question and give focus back to the button that opened it. */
  const closeConfirm = useCallback(() => {
    setConfirming(false);
    rowRef.current?.querySelector<HTMLElement>('[data-focus-key="profiles:account:use"]')?.focus();
  }, []);
  useEffect(() => {
    const box = confirmRef.current;
    if (!confirming || box === null) return undefined;
    box.querySelector('button')?.focus();
    // Escape closes the question like Cancel.
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeConfirm();
    };
    box.addEventListener('keydown', onKey);
    return () => {
      box.removeEventListener('keydown', onKey);
    };
  }, [confirming, closeConfirm]);
  const keeps = account.keeps.edits + account.keeps.unresolved;
  const send = (command: ProfileWriteCommand) => {
    void commands.profileWrite(command);
  };
  return (
    <>
      <RegionEnd>
        {account.readAgeSec === null ? null : <span>{t.account.readAge(account.readAgeSec)}</span>}
      </RegionEnd>
      <RegionBody>
        <NotSent />
        <Tiles min="wide">
          <Tile
            titleId="profiles-binding-title"
            title={t.account.binding.short}
            intro={{ short: t.account.scope.short }}
            end={helpFor(t.account.binding.short, t.account.scope.why)}
          >
            <Facts>
              <Fact
                label={t.account.emulator.short}
                value={
                  account.instance === null ? (
                    t.outcomes.contextUnknown.short
                  ) : (
                    <ServerValue kind="id" value={account.instance} />
                  )
                }
              />
              {account.read === 'ready' ? (
                <Fact
                  label={t.account.inUse.short}
                  value={
                    <span className="tp-profiles__summary" data-active-account="true">
                      {account.active === null ? (
                        t.account.noneActive.short
                      ) : (
                        <>
                          {account.active.label === null ? null : (
                            <ServerValue kind="name" value={account.active.label} />
                          )}
                          {account.active.id === null ? null : (
                            <ServerValue kind="id" value={account.active.id} />
                          )}
                        </>
                      )}
                    </span>
                  }
                />
              ) : null}
            </Facts>
            {account.read === 'not_read' ? <p>{t.account.reading.short}</p> : null}
            {account.read === 'settling' ? <p>{t.account.settling.short}</p> : null}
            {account.read === 'unavailable' ? <p>{t.account.unavailable.short}</p> : null}
            {account.read === 'ready' && account.accounts.length === 0 ? (
              <p>{t.account.noAccounts.short}</p>
            ) : null}
            {account.accounts.length === 0 ? null : (
              <div className="tp-profiles__row tp-control-row" ref={rowRef}>
                <label className="tp-profiles__field">
                  <span>{t.account.picker.short}</span>
                  <select
                    className="tp-field-control"
                    value={account.pick ?? ''}
                    disabled={account.pickScope === null}
                    data-focus-key="profiles:account:pick"
                    onChange={(event) => {
                      setConfirming(false);
                      if (account.pickScope !== null)
                        commands.editDraft(account.pickScope, 'id', event.currentTarget.value);
                    }}
                  >
                    {account.pick === null ? (
                      <option value="">{t.reasons.choose_account.short}</option>
                    ) : null}
                    {account.accounts.map((choice) =>
                      choice.option === null ? null : (
                        <ServerOption key={choice.key} value={choice.key} label={choice.option} />
                      ),
                    )}
                  </select>
                </label>
                <WriteButton
                  vm={account.select}
                  label={t.account.use}
                  focusKey="profiles:account:use"
                  onSend={(command) => {
                    if (keeps > 0) setConfirming(true);
                    else send(command);
                  }}
                />
              </div>
            )}
            {confirming && account.select.command !== null ? (
              <div
                ref={confirmRef}
                className="tp-profiles__confirm"
                role="group"
                aria-labelledby={question}
                data-switch-confirm="true"
              >
                <p id={question}>
                  {t.account.keeps(account.keeps.edits, account.keeps.unresolved)}
                </p>
                <ActionRow>
                  <Button
                    tier="secondary"
                    label={t.account.confirmSwitch.short}
                    focusKey="profiles:account:confirm"
                    onClick={() => {
                      const command = account.select.command;
                      closeConfirm();
                      if (command !== null) send(command);
                    }}
                  />
                  <Button
                    tier="quiet"
                    label={t.account.cancel.short}
                    focusKey="profiles:account:cancel"
                    onClick={closeConfirm}
                  />
                </ActionRow>
              </div>
            ) : null}
            {account.outcome === null ? null : <ProfileOutcome outcome={account.outcome} />}
          </Tile>
          <div data-form="create">
            <Tile
              as="fieldset"
              className="tp-profiles__form"
              titleId="profiles-create-title"
              title={t.create.title.short}
            >
              <TextField
                label={t.create.id}
                value={account.create.id}
                scope={account.create.scope}
                path="id"
                help={t.create.idHelp}
              />
              <TextField
                label={t.create.label}
                value={account.create.label}
                scope={account.create.scope}
                path="label"
              />
              {account.create.importOffered ? (
                <CheckField
                  label={t.create.importCurrent}
                  condition={t.create.importCondition}
                  checked={account.create.importCurrent}
                  scope={account.create.scope}
                  path="import_current"
                />
              ) : null}
              <div className="tp-profiles__actions">
                <WriteButton
                  vm={account.create.command}
                  label={t.create.submit}
                  focusKey="profiles:create:submit"
                  onSend={send}
                />
              </div>
            </Tile>
          </div>
        </Tiles>
      </RegionBody>
    </>
  );
}

/** P3: the operator's highest unlocked tier. */
function TierSection() {
  const vm = useView(selectProfilesPage);
  const { commands } = useDashboard();
  const tier = vm.tier;
  return (
    <RegionBody>
      <div data-tier-saved="true">
        <Facts>
          <Fact
            label={tier.current !== null ? t.tier.current.short : t.tier.default.short}
            value={
              tier.current !== null
                ? t.tier.value(tier.current)
                : tier.defaultValue !== null
                  ? t.tier.value(tier.defaultValue)
                  : t.tier.notSaved.short
            }
          />
          {tier.current === null && tier.defaultValue !== null ? (
            <Fact label={t.tier.recordedState.short} value={t.tier.defaultNote.short} />
          ) : null}
          {tier.current === null ? null : (
            <Fact
              label={t.tier.source.short}
              value={
                tier.setBy === 'operator' ? (
                  t.tier.byOperator.short
                ) : tier.setBy === 'other' && tier.setByText !== null ? (
                  <>
                    {t.tier.byOther.short} <ServerValue kind="id" value={tier.setByText} />
                  </>
                ) : (
                  t.tier.sourceUnknown.short
                )
              }
            />
          )}
        </Facts>
      </div>
      <div className="tp-profiles__row tp-control-row">
        <TextField
          label={t.tier.input}
          value={tier.shown}
          scope={tier.scope}
          path="max_tier"
          help={t.tier.range}
          invalid={tier.invalid === null ? null : t.tier.invalid[tier.invalid]}
        />
        <WriteButton
          vm={tier.save}
          label={t.tier.save}
          focusKey="profiles:tier:save"
          onSend={(command) => {
            void commands.profileWrite(command);
          }}
        />
      </div>
      {tier.outcome === null ? null : <ProfileOutcome outcome={tier.outcome} />}
    </RegionBody>
  );
}

/** P4: the setup reads, each distinct, with links to the pages that own them. */
function SetupSection() {
  const vm = useView(selectProfilesPage);
  const setup = vm.setup;
  return (
    <RegionBody>
      <Tiles>
        <Tile titleId="profiles-connection-title" title={t.setup.connection.short}>
          <Facts>
            <Fact
              label={t.setup.toolsLabel.short}
              value={
                <span className="tp-profiles__status" data-setup="tools" data-state={setup.tools}>
                  <span>{t.setup.tools[setup.tools].short}</span>
                  <a href={EMULATORS}>{t.setup.emulators.short}</a>
                </span>
              }
            />
            <Fact
              label={t.setup.progress.short}
              value={
                <span data-setup="calibration" data-state={setup.calibration}>
                  {t.setup.calibration[setup.calibration].short}
                </span>
              }
            />
          </Facts>
        </Tile>
        <Tile titleId="profiles-readiness-title" title={t.setup.readiness.short}>
          <div data-setup="runs">
            {setup.runs === null ? (
              <p>{t.setup.runsUnknown.short}</p>
            ) : (
              <Facts layout="cells">
                <Fact label={t.setup.ready.short} value={setup.runs.ready} />
                <Fact label={t.setup.notReady.short} value={setup.runs.notReady} />
                {setup.runs.unknown > 0 ? (
                  <Fact label={t.setup.unknown.short} value={setup.runs.unknown} />
                ) : null}
              </Facts>
            )}
          </div>
          <a href={RECOGNITION}>{t.setup.recognition.short}</a>
          <CopyLine entry={t.setup.note} variant="note" />
        </Tile>
      </Tiles>
    </RegionBody>
  );
}

/** P5: the profile files, activation, copies and scan-draft promotions. */
function FilesSection() {
  const vm = useView(selectProfilesPage);
  const { commands } = useDashboard();
  const files = vm.profiles;
  const send = (command: ProfileWriteCommand) => {
    void commands.profileWrite(command);
  };
  return (
    <RegionBody>
      <Tile
        titleId="profiles-inventory-title"
        title={t.files.inventory.short}
        intro={{ short: t.files.scope.short }}
        end={helpFor(t.files.inventory.short, t.files.scope.why)}
      >
        {files.read === 'not_read' ? <p>{t.files.reading.short}</p> : null}
        {files.read === 'settling' ? <p>{t.files.settling.short}</p> : null}
        {files.read === 'unavailable' ? <p>{t.files.unavailable.short}</p> : null}
        {files.read === 'ready' && files.rows.length === 0 ? <p>{t.files.none.short}</p> : null}
        {files.rows.length === 0 ? null : (
          <table className="tp-profiles__table">
            <thead>
              <tr>
                <th scope="col">{t.files.columns.name.short}</th>
                <th scope="col">{t.files.columns.kind.short}</th>
                <th scope="col">{t.files.columns.modified.short}</th>
              </tr>
            </thead>
            <tbody>
              {files.rows.map((row) => (
                <tr key={row.key} data-profile={row.key} data-active={row.active}>
                  <td className="tp-profiles__file-name">
                    {row.name === null ? null : <ServerValue kind="name" value={row.name} />}
                    {row.active ? (
                      <span className="tp-profiles__mark">{t.files.activeMark.short}</span>
                    ) : null}
                  </td>
                  <td className="tp-profiles__file-kind">
                    <span className="tp-profiles__mobile-label" aria-hidden="true">
                      {t.files.columns.kind.short}
                    </span>
                    <span>
                      <span>{t.files.kinds[row.kind].short}</span>
                      {row.note === null ? null : (
                        <span className="tp-profiles__help">
                          <ServerValue kind="message" value={row.note} />
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="tp-profiles__file-modified">
                    <span className="tp-profiles__mobile-label" aria-hidden="true">
                      {t.files.columns.modified.short}
                    </span>
                    {row.mtime === null
                      ? t.files.modifiedUnknown.short
                      : t.files.modified(row.mtime)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Tile>
      <Tiles min="wide">
        <div data-form="automation" data-active-profile="true">
          <Tile
            as="fieldset"
            className="tp-profiles__form"
            titleId="profiles-automation-title"
            title={targetCopy.automation.short}
          >
            {files.unbind === null ? (
              <Facts>
                <Fact
                  label={t.unbind.active.short}
                  value={
                    files.active === null ? (
                      targetCopy.none.short
                    ) : (
                      <ServerValue kind="name" value={files.active} />
                    )
                  }
                />
              </Facts>
            ) : null}
            <div>
              {files.activeValidation === null ? (
                files.activeSourceError === null ? null : (
                  <span>
                    <span>{t.files.sourceUnavailable.short}</span>
                    <ServerValue kind="message" value={files.activeSourceError} />
                  </span>
                )
              ) : (
                <Validation validation={files.activeValidation} />
              )}
            </div>
            <div data-form="activate">
              <Tile
                as="fieldset"
                className="tp-profiles__form"
                titleId="profiles-activate-title"
                title={t.activate.title.short}
                level={4}
              >
                <CopyLine entry={t.activate.effect} variant="note" />
                <div className="tp-profiles__row tp-control-row">
                  <ProfileSelect
                    label={t.activate.picker}
                    value={files.activate.pick ?? ''}
                    rows={files.rows}
                    scope={files.activate.scope}
                    path="name"
                    empty={t.activate.choose}
                    onPick={(name) => {
                      if (name !== '') void commands.checkProfile(name);
                    }}
                  />
                  <WriteButton
                    vm={files.activate.command}
                    label={t.activate.submit}
                    focusKey="profiles:activate:submit"
                    onSend={send}
                  />
                </div>
                {files.activate.check === 'checking' ? <p>{t.activate.checking.short}</p> : null}
                {files.activate.validation === null ? null : (
                  <Validation validation={files.activate.validation} />
                )}
                {files.activate.checkError === null ? null : (
                  <p>
                    <span>{t.reasons.unreadable.short}</span>
                    <ServerValue kind="message" value={files.activate.checkError} />
                  </p>
                )}
                {files.activate.outcome === null ? null : (
                  <ProfileOutcome outcome={files.activate.outcome} />
                )}
              </Tile>
            </div>
            {files.unbind === null ? null : <UnbindProfile unbind={files.unbind} onSend={send} />}
          </Tile>
        </div>
        <EditingTargetPicker key={vm.epoch} />
        <div data-form="copy">
          <Tile
            as="fieldset"
            className="tp-profiles__form"
            titleId="profiles-copy-title"
            title={t.copy.title.short}
          >
            <div className="tp-profiles__row tp-control-row">
              <ProfileSelect
                label={t.copy.from}
                value={files.copy.from}
                rows={files.copy.sources}
                scope={files.copy.scope}
                path="from"
                empty={t.reasons.choose_source}
              />
              <TextField
                label={t.copy.name}
                value={files.copy.name}
                scope={files.copy.scope}
                path="name"
              />
            </div>
            {files.copy.exists ? (
              <CheckField
                label={t.copy.overwrite}
                checked={files.copy.overwrite}
                scope={files.copy.scope}
                path="overwrite"
                consent={files.copy.overwriteKey}
              />
            ) : null}
            <div className="tp-profiles__actions">
              <WriteButton
                vm={files.copy.command}
                label={t.copy.submit}
                focusKey="profiles:copy:submit"
                onSend={send}
              />
            </div>
          </Tile>
        </div>
        <div data-form="promote">
          <Tile
            as="fieldset"
            className="tp-profiles__form"
            titleId="profiles-promote-title"
            title={t.promote.title.short}
          >
            <div className="tp-profiles__row tp-control-row">
              <ProfileSelect
                label={t.promote.draft}
                value={files.promote.draft}
                rows={files.promote.drafts}
                scope={files.promote.scope}
                path="draft"
                empty={t.promote.noDrafts}
              />
              <TextField
                label={t.promote.name}
                value={files.promote.name}
                scope={files.promote.scope}
                path="name"
              />
              <ProfileSelect
                label={t.promote.base}
                value={files.promote.base}
                rows={files.promote.bases}
                scope={files.promote.scope}
                path="base"
                empty={t.reasons.choose_source}
              />
            </div>
            {files.promote.exists ? (
              <CheckField
                label={t.promote.overwrite}
                checked={files.promote.overwrite}
                scope={files.promote.scope}
                path="overwrite"
                consent={files.promote.overwriteKey}
              />
            ) : null}
            <div className="tp-profiles__actions">
              <WriteButton
                vm={files.promote.command}
                label={t.promote.submit}
                focusKey="profiles:promote:submit"
                onSend={send}
              />
            </div>
          </Tile>
        </div>
      </Tiles>
      {files.filesOutcome === null ? null : <ProfileOutcome outcome={files.filesOutcome} />}
    </RegionBody>
  );
}

function NotSent() {
  const vm = useView(selectProfilesPage);
  if (vm.notSent === null) return null;
  const notSent: Readonly<Record<string, CopyEntry>> = t.notSent;
  const entry = notSent[vm.notSent];
  if (entry === undefined) return null;
  return (
    <div className="tp-profiles__not-sent" data-not-sent={vm.notSent} role="status">
      <span>{entry.short}</span>
      {entry.why === undefined ? null : <HelpTip label={entry.short}>{entry.why}</HelpTip>}
    </div>
  );
}

/**
 * The Profiles page (profiles brief P1 to P5): the local game account bound
 * to this emulator, the operator's tier, the setup state, and the automation
 * profiles. Opening it tells the store to read the profile files and the
 * active profile; every write goes through app/'s click-time context fence.
 */
export function ProfilesPage() {
  const { commands } = useDashboard();
  useEffect(() => {
    commands.openProfiles(true);
    return () => {
      commands.openProfiles(false);
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
          <TopBar pageId="profiles" title={t.pageName.short} />
        </RegionBoundary>
      </header>
      <main id="main" className="tp-main" tabIndex={-1}>
        <FaultPoint part="page" />
        <div className="tp-profiles">
          <ProfilesRegion id="profilesAccount" title={t.account.title}>
            <AccountSection />
          </ProfilesRegion>
          <ProfilesRegion id="profilesTier" title={t.tier.title}>
            <TierSection />
          </ProfilesRegion>
          <ProfilesRegion id="profilesFiles" title={t.files.title}>
            <FilesSection />
          </ProfilesRegion>
          <ProfilesRegion id="profilesSetup" title={t.setup.title}>
            <SetupSection />
          </ProfilesRegion>
        </div>
      </main>
    </>
  );
}
