import { useCallback, useEffect, useId, useState } from 'react';
import { copy } from '../../copy/catalogue.ts';
import { emulators as t } from '../../copy/emulators.ts';
import type { CopyEntry, CopyNode } from '../../copy/text.ts';
import {
  selectEmulatorsPage,
  type EmulatorOutcomeVM,
  type EmulatorRowVM,
  type EmulatorWriteCommand,
  type EmulatorsPageVM,
  type ResolutionVM,
} from '../../store/selectors/emulators.ts';
import { selectOutcomes } from '../../store/selectors/outcomes.ts';
import type { CommandVM } from '../../store/viewModels.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Chip } from '../../ui/Chip.tsx';
import { CopyLine } from '../../ui/CopyLine.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { Notice } from '../../ui/Notice.tsx';
import { RegionBoundary } from '../../ui/RegionBoundary.tsx';
import { Region, RegionBody, RegionEnd } from '../../ui/Region.tsx';
import { pageHref } from '../../ui/routes.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { Tile, Tiles } from '../../ui/Tile.tsx';
import {
  FaultPoint,
  useDashboard,
  useEpochKey,
  usePartError,
  useView,
} from '../dashboard/context.ts';
import { OwnerNotice } from '../dashboard/outcomes/OwnerNotice.tsx';
import { RevocationFallback } from '../dashboard/shared/RevocationFallback.tsx';
import { TopBar } from '../dashboard/topBar/TopBar.tsx';
import { SettingsModuleForm } from '../settings/SettingsModuleForm.tsx';
import './emulators.css';
import { EmulatorSettings } from './EmulatorSettings.tsx';

const connectionLines: Readonly<Record<string, CopyEntry>> = copy.dashboard.topBar.connection;
/** The requests this page owns; their owner notices are shown here (the shared notice). */
const PAGE_KINDS: readonly string[] = ['adopt', 'launch', 'prepare', 'tools_install', 'reconnect'];

/** The column name inside a cell: hidden while the table is a table, shown on the 390 cards. */
function CellLabel({ entry }: { readonly entry: CopyEntry }) {
  return <span className="tp-emulators__cell-label">{entry.short}</span>;
}

function reasonOf(reason: string | null): CopyEntry {
  const reasons: Readonly<Record<string, CopyEntry>> = t.reasons;
  return (reason === null ? undefined : reasons[reason]) ?? t.reasons.context_unknown;
}

/** One action button, the same element whatever its state; disabled with its visible reason. */
function Action({
  vm,
  label,
  focusKey,
  onClick,
  beside,
  tier = 'secondary',
}: {
  readonly vm: CommandVM<unknown>;
  readonly label: CopyEntry;
  readonly focusKey: string;
  readonly onClick: () => void;
  readonly beside?: CopyNode;
  readonly tier?: 'secondary' | 'quiet';
}) {
  const own = useId();
  return (
    <div className="tp-emulators__action">
      <ActionRow
        reason={vm.enabled ? null : <Reason id={`${own}-reason`} entry={reasonOf(vm.reason)} />}
      >
        <Button
          tier={tier}
          label={label.short}
          focusKey={focusKey}
          busy={vm.sending}
          {...(vm.enabled ? {} : { disabled: true as const, reasonId: `${own}-reason` })}
          onClick={() => {
            if (vm.enabled) onClick();
          }}
        />
        {beside}
      </ActionRow>
    </div>
  );
}

function outcomeLine(outcome: EmulatorOutcomeVM): CopyEntry {
  const o = t.outcomes;
  switch (outcome.code) {
    case 'sending':
      return o.sending;
    case 'refused':
      return o.refused;
    case 'unknown':
      return o.unknown;
    case 'applied':
      return o.applied;
    case 'not_applied':
      return o.not_applied;
    case 'saved':
      return o.adopt.saved;
    case 'accepted':
      return outcome.kind === 'tools_install' ? o.install.accepted : o.launch.accepted;
    case 'already_running':
      return o.install.already_running;
    case 'failed':
      return outcome.kind === 'reconnect' ? o.reconnect.failed : o.launch.failed;
    case 'done':
      if (outcome.kind === 'reconnect') return o.reconnect.done;
      return outcome.changed.length === 0 && outcome.added.length === 0
        ? o.prepare.nothing
        : o.prepare.done;
  }
}

/** The newest request of one action: what it proved, the server's words, and Dismiss. */
function Outcome({ outcome }: { readonly outcome: EmulatorOutcomeVM }) {
  const { commands } = useDashboard();
  const entry = outcomeLine(outcome);
  const connect =
    outcome.kind === 'adopt' && outcome.code === 'saved'
      ? outcome.connected === true
        ? t.outcomes.adopt.connected
        : outcome.connected === false
          ? t.outcomes.adopt.notConnected
          : t.outcomes.adopt.connectUnknown
      : null;
  const details =
    outcome.message !== null ||
    outcome.error !== null ||
    outcome.backup !== null ||
    outcome.changed.length > 0 ||
    outcome.added.length > 0 ||
    connect !== null;
  return (
    <div
      className="tp-emulators__outcome"
      data-outcome={outcome.code}
      data-kind={outcome.kind}
      data-intent={outcome.id}
    >
      <Notice
        state={outcome.word}
        lead={
          <>
            <span>{t.outcomes.lead.short}</span>
            {outcome.answeredAgeSec === null ? null : (
              <span>{t.outcomes.answeredAgo(outcome.answeredAgeSec)}</span>
            )}
            {outcome.restored ? <span>{t.outcomes.restored.short}</span> : null}
          </>
        }
        line={
          <span className="tp-emulators__outcome-line">
            <span>{entry.short}</span>
            {outcome.target === null ? null : <ServerValue kind="id" value={outcome.target} />}
            {outcome.deviceState === null ? null : (
              <ServerValue kind="id" value={outcome.deviceState} />
            )}
          </span>
        }
        {...(entry.why === undefined ? {} : { why: entry.why })}
        {...(details
          ? {
              server: (
                <>
                  {connect === null ? null : (
                    <span data-connect={String(outcome.connected)}>{connect.short}</span>
                  )}
                  {outcome.message === null ? null : (
                    <ServerValue kind="message" value={outcome.message} />
                  )}
                  {outcome.error === null ? null : outcome.code === 'unknown' ? (
                    <span data-server-error="true">
                      <span>{t.outcomes.serverError.short}</span>
                      <ServerValue kind="message" value={outcome.error} />
                    </span>
                  ) : (
                    <span data-refusal="true">
                      <ServerValue kind="message" value={outcome.error} />
                    </span>
                  )}
                  {outcome.changed.length === 0 ? null : (
                    <ul className="tp-emulators__changes">
                      {outcome.changed.map((change, index) => (
                        <li key={String(index)}>
                          {change.key === null ? null : (
                            <ServerValue kind="id" value={change.key} />
                          )}
                          {change.from === null ? null : (
                            <ServerValue kind="id" value={change.from} />
                          )}
                          {change.to === null ? null : <ServerValue kind="id" value={change.to} />}
                        </li>
                      ))}
                    </ul>
                  )}
                  {outcome.added.length === 0 ? null : (
                    <span>
                      <span>{t.outcomes.prepare.added.short}</span>
                      {outcome.added.map((key) => (
                        <ServerValue key={key} kind="id" value={key} />
                      ))}
                    </span>
                  )}
                  {outcome.backup === null ? null : (
                    <span>
                      <span>{t.outcomes.prepare.backup.short}</span>
                      <ServerValue kind="id" value={outcome.backup} />
                    </span>
                  )}
                </>
              ),
            }
          : {})}
        actions={
          outcome.dismissOffered ? (
            <Button
              tier="quiet"
              label={t.outcomes.dismiss.short}
              focusKey={`emulator-outcome:${outcome.id}:dismiss`}
              onClick={() => {
                commands.dismiss(outcome.id);
              }}
            />
          ) : null
        }
      />
    </div>
  );
}

function EmulatorsRegion({
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
      fallback={<RevocationFallback part="emulators" />}
      resetKey={useEpochKey()}
      onError={usePartError(id)}
    >
      <FaultPoint part={id} />
      {children}
    </Region>
  );
}

/** M8: the owner notice of a refused request of this page (the shared, D-4-judged notice). */
function PageOwnerNotice() {
  const vm = useView(selectOutcomes);
  const { commands } = useDashboard();
  const notice = vm.ownerNotice;
  if (notice === null || !PAGE_KINDS.includes(notice.action)) return null;
  return (
    <div className="tp-emulators__owner" data-owner-notice="true">
      <OwnerNotice
        notice={notice}
        onDismiss={() => {
          commands.dismiss(notice.intentId);
        }}
      />
    </div>
  );
}

function Resolution({ vm }: { readonly vm: ResolutionVM }) {
  const r = t.resolution;
  return (
    <div className="tp-emulators__resolution" data-resolution={vm.kind}>
      <Facts className="tp-emulators__facts">
        {vm.kind === 'no_result' ? (
          <>
            <Fact label={r.result.short} value={(vm.answered ? r.unreadable : r.noAnswer).short} />
            {vm.status === null ? null : (
              <Fact label={r.response.short} value={r.status(vm.status)} />
            )}
          </>
        ) : vm.kind === 'measured' ? (
          <>
            <Fact
              label={r.dimensions.short}
              value={<span data-size="true">{r.size(vm.width, vm.height)}</span>}
            />
            <Fact
              label={r.orientation.short}
              value={(vm.portrait ? r.portrait : r.landscape).short}
            />
            {vm.portrait ? (
              <Fact
                label={r.layout.short}
                value={(vm.reference ? r.reference : r.notReference).short}
              />
            ) : null}
            {vm.note === null ? null : (
              <Fact
                label={r.noteLabel.short}
                value={<ServerValue kind="message" value={vm.note} />}
              />
            )}
          </>
        ) : (
          <>
            <Fact label={r.result.short} value={r.failed.short} />
            {vm.error === null ? null : (
              <Fact label={r.error.short} value={<ServerValue kind="message" value={vm.error} />} />
            )}
          </>
        )}
        {vm.checkedAgeSec === null ? null : (
          <Fact label={r.checked.short} value={r.checkedAgo(vm.checkedAgeSec)} />
        )}
        {vm.kind === 'no_result' || vm.savedAt === null ? null : (
          <Fact label={r.saved.short} value={r.savedAt(vm.savedAt)} />
        )}
      </Facts>
    </div>
  );
}

/** M6, M8: the active instance, its address, the live connection, reconnect and screen size. */
function TargetSection() {
  const vm = useView(selectEmulatorsPage);
  const { commands } = useDashboard();
  const [checking, setChecking] = useState(false);
  const line = connectionLines[vm.connection.line];
  const serial = vm.resolution.serial;
  return (
    <>
      <RegionEnd>
        {vm.connectionAgeSec === null ? null : <span>{t.target.liveAge(vm.connectionAgeSec)}</span>}
      </RegionEnd>
      <RegionBody>
        <NotSent />
        <PageOwnerNotice />
        <Tiles min="wide">
          <Tile titleId="emulators-connection-title" title={t.target.connection.short}>
            <Facts className="tp-emulators__facts-list">
              <div className="tp-facts__item">
                <dt>{t.target.instance.short}</dt>
                <dd>
                  {vm.instance === null ? null : <ServerValue kind="id" value={vm.instance} />}
                </dd>
              </div>
              <div className="tp-facts__item">
                <dt>{t.target.account.short}</dt>
                <dd>
                  {vm.account === null ? (
                    t.target.noAccount.short
                  ) : (
                    <ServerValue kind="id" value={vm.account} />
                  )}
                </dd>
              </div>
              <div className="tp-facts__item">
                <dt>{t.target.address.short}</dt>
                <dd data-configured-serial="true">
                  {vm.configuredSerial === null ? (
                    t.target.notSet.short
                  ) : (
                    <>
                      <ServerValue kind="id" value={vm.configuredSerial} />
                      {vm.configuredName === null ? null : (
                        <ServerValue kind="name" value={vm.configuredName} />
                      )}
                      <span className="tp-emulators__muted">
                        {
                          (vm.portOpen === true
                            ? t.target.port.open
                            : vm.portOpen === false
                              ? t.target.port.closed
                              : t.target.port.unknown
                          ).short
                        }
                      </span>
                    </>
                  )}
                </dd>
              </div>
              <div className="tp-facts__item">
                <dt>{t.target.live.short}</dt>
                <dd data-live={vm.connection.line}>
                  {vm.connection.word === null ? null : <Chip state={vm.connection.word} />}
                  <span>{line?.short}</span>
                  <HelpTip label={t.target.live.short}>
                    <p>{t.target.liveNote.short}</p>
                    {t.target.liveNote.why === undefined ? null : <p>{t.target.liveNote.why}</p>}
                  </HelpTip>
                </dd>
              </div>
            </Facts>
            {vm.unconfigured ? (
              <div className="tp-emulators__line" data-unconfigured="true">
                <p>{t.target.unconfigured.short}</p>
                {t.target.unconfigured.why === undefined ? null : (
                  <HelpTip label={t.target.unconfigured.short}>
                    <p>{t.target.unconfigured.why}</p>
                  </HelpTip>
                )}
              </div>
            ) : null}
            {vm.boot === null ? null : (
              <div className="tp-emulators__line" data-boot="true">
                <span>{t.target.boot.short}</span>
                {vm.boot.ageSec === null ? null : <span>{t.target.liveAge(vm.boot.ageSec)}</span>}
                {t.target.boot.why === undefined ? null : (
                  <HelpTip label={t.target.boot.short}>
                    <p>{t.target.boot.why}</p>
                  </HelpTip>
                )}
              </div>
            )}
            <div className="tp-emulators__row">
              <Action
                vm={vm.reconnect}
                label={t.target.reconnect}
                focusKey="emulators:reconnect"
                onClick={() => {
                  if (vm.reconnect.command !== null) void commands.reconnect(vm.reconnect.command);
                }}
                beside={
                  <>
                    <a className="tp-button" data-tier="quiet" href={pageHref('dashboard')}>
                      {t.target.dashboard.short}
                    </a>
                    <HelpTip label={t.target.dashboard.short}>
                      <p>{t.target.dashboardActions.short}</p>
                    </HelpTip>
                  </>
                }
              />
            </div>
            {vm.reconnectOutcome === null ? null : <Outcome outcome={vm.reconnectOutcome} />}
            {vm.adoptOutcome === null ? null : <Outcome outcome={vm.adoptOutcome} />}
          </Tile>
          <Tile
            className="tp-emulators__sub"
            titleId="emulators-resolution-title"
            title={t.resolution.title.short}
            end={
              <HelpTip label={t.resolution.title.short}>
                <p>{t.resolution.note.short}</p>
                {t.resolution.note.why === undefined ? null : <p>{t.resolution.note.why}</p>}
              </HelpTip>
            }
          >
            <ActionRow>
              <Action
                vm={{
                  shown: true,
                  enabled: serial !== null && !checking,
                  reason: serial === null ? 'no_address' : checking ? 'sending' : null,
                  sending: checking,
                  command: null,
                }}
                label={t.resolution.check}
                focusKey="emulators:resolution"
                onClick={() => {
                  if (serial === null) return;
                  setChecking(true);
                  void commands.checkResolution(serial).finally(() => {
                    setChecking(false);
                  });
                }}
              />
            </ActionRow>
            {checking ? <p>{t.resolution.checking.short}</p> : null}
            {vm.resolution.check === null ? null : <Resolution vm={vm.resolution.check} />}
            {(vm.resolution.check === null || vm.resolution.check.kind === 'no_result') &&
            vm.resolution.saved !== null ? (
              <Resolution vm={vm.resolution.saved} />
            ) : null}
          </Tile>
        </Tiles>
      </RegionBody>
    </>
  );
}

type SuggestionVM = NonNullable<EmulatorsPageVM['suggestion']>;

/** The "Suggested" label with the reason it is suggested, as help beside it. */
function SuggestedLabel() {
  const d = t.detected;
  return (
    <span className="tp-emulators__suggested">
      <span>{d.suggested.short}</span>
      {d.suggested.why === undefined ? null : (
        <HelpTip label={d.suggested.short}>
          <p>{d.suggested.why}</p>
        </HelpTip>
      )}
    </span>
  );
}

function Row({
  row,
  suggestion,
  onUse,
  onLaunch,
  onPrepare,
}: {
  readonly row: EmulatorRowVM;
  /** The suggestion that names this row's address: the row carries it, no separate block repeats it. */
  readonly suggestion: SuggestionVM | null;
  readonly onUse: (command: EmulatorWriteCommand, row: EmulatorRowVM) => void;
  readonly onLaunch: (command: EmulatorWriteCommand) => void;
  readonly onPrepare: (command: EmulatorWriteCommand) => void;
}) {
  const d = t.detected;
  return (
    <tr data-row={row.key} {...(suggestion === null ? {} : { 'data-suggestion': 'true' })}>
      <td>
        <CellLabel entry={d.columns.emulator} />
        {row.emulator === null ? null : <ServerValue kind="name" value={row.emulator} />}
        {suggestion === null ? null : <SuggestedLabel />}
      </td>
      <td>
        <CellLabel entry={d.columns.instance} />
        {row.name === null ? null : <ServerValue kind="name" value={row.name} />}
      </td>
      <td>
        <CellLabel entry={d.columns.state} />
        <div className="tp-emulators__cell">
          <span>
            {
              (row.running === true
                ? d.running
                : row.running === false
                  ? d.off
                  : row.listed
                    ? d.stateUnknown
                    : d.stateNotReported
              ).short
            }
          </span>
          {row.displayNotReady ? (
            <span className="tp-emulators__warn">
              <span>{d.display.short}</span>
              {row.display === null ? null : <ServerValue kind="id" value={row.display} />}
            </span>
          ) : null}
          {row.error === null ? null : <ServerValue kind="message" value={row.error} />}
        </div>
      </td>
      <td>
        <CellLabel entry={d.columns.address} />
        {row.serial === null ? null : <ServerValue kind="id" value={row.serial} />}
        {suggestion === null || suggestion.alsoSeen.length === 0 ? null : (
          <span className="tp-emulators__muted">
            {d.alsoSeen.short}
            {suggestion.alsoSeen.map((serial) => (
              <ServerValue key={serial} kind="id" value={serial} />
            ))}
          </span>
        )}
      </td>
      <td>
        <CellLabel entry={d.columns.actions} />
        <div className="tp-emulators__cell tp-emulators__commands">
          {row.inUse ? <span data-in-use="true">{d.inUse.short}</span> : null}
          {suggestion !== null ? (
            <Action
              vm={suggestion.use}
              label={d.use}
              focusKey="emulators:use:suggestion"
              onClick={() => {
                if (suggestion.use.command !== null) onUse(suggestion.use.command, row);
              }}
            />
          ) : row.use === null ? null : (
            <Action
              vm={row.use}
              label={d.use}
              focusKey={`emulators:use:${row.key}`}
              onClick={() => {
                if (row.use?.command != null) onUse(row.use.command, row);
              }}
            />
          )}
          {row.openGame === null ? null : (
            <Action
              vm={row.openGame}
              label={d.openGame}
              focusKey={`emulators:open:${row.key}`}
              onClick={() => {
                if (row.openGame?.command != null) onLaunch(row.openGame.command);
              }}
            />
          )}
          {row.start === null ? null : (
            <Action
              vm={row.start}
              label={d.start}
              focusKey={`emulators:start:${row.key}`}
              onClick={() => {
                if (row.start?.command != null) onLaunch(row.start.command);
              }}
            />
          )}
          {row.prepare === null ? null : (
            <Action
              vm={row.prepare}
              tier="quiet"
              label={d.prepare}
              focusKey={`emulators:prepare:${row.key}`}
              onClick={() => {
                if (row.prepare?.command != null) onPrepare(row.prepare.command);
              }}
            />
          )}
          {row.launchOutcome === null ? null : <Outcome outcome={row.launchOutcome} />}
          {row.prepareOutcome === null ? null : <Outcome outcome={row.prepareOutcome} />}
        </div>
      </td>
    </tr>
  );
}

/** M1 to M4: the last scan, the suggestion, each detected emulator and its explicit actions. */
function DetectedSection() {
  const vm = useView(selectEmulatorsPage);
  const { commands } = useDashboard();
  const d = t.detected;
  const [asking, setAsking] = useState<
    | { readonly kind: 'use'; readonly command: EmulatorWriteCommand; readonly to: string }
    | { readonly kind: 'prepare'; readonly command: EmulatorWriteCommand }
    | null
  >(null);
  const send = useCallback(
    (command: EmulatorWriteCommand) => {
      void commands.emulatorWrite(command);
    },
    [commands],
  );
  const askUse = (command: EmulatorWriteCommand) => {
    if (command.action.kind !== 'adopt') return;
    setAsking({ kind: 'use', command, to: command.action.serial });
  };
  // F6M-C-02: a confirmation opened while the request was allowed closes when it no longer is
  // (a runner listed meanwhile, another request in flight): nothing is sent from it.
  // Adjusting state while rendering (not in an effect) so the stale dialog never paints once.
  if (vm.writeGate !== null && asking !== null) setAsking(null);
  // The suggestion names a detected address: that row carries it. Only one no row names stands alone.
  const suggested = vm.suggestion;
  const rowSuggestion = (row: EmulatorRowVM) => (row.key === suggested?.rowKey ? suggested : null);
  const suggestionOnly = suggested?.rowKey == null ? suggested : null;
  return (
    <>
      <RegionEnd>
        {vm.scan.ageSec === null ? null : <span>{d.scanAge(vm.scan.ageSec)}</span>}
      </RegionEnd>
      <RegionBody>
        <ActionRow>
          <Button
            tier="secondary"
            label={d.rescan.short}
            focusKey="emulators:rescan"
            onClick={() => {
              commands.rescanEmulators();
            }}
          />
        </ActionRow>
        {vm.scan.read === 'not_read' ? <p>{d.reading.short}</p> : null}
        {vm.scan.read === 'unavailable' ? <p>{d.unavailable.short}</p> : null}
        {vm.scan.error === null ? null : (
          <p>
            <span>{d.error.short}</span>
            <ServerValue kind="message" value={vm.scan.error} />
          </p>
        )}
        {vm.scan.otherInstance ? <p>{d.otherInstance.short}</p> : null}
        {vm.blueStacksAdb === null ? null : (
          <p data-bluestacks-adb="true">
            {
              (vm.blueStacksAdb.enabled === true
                ? d.blueStacksAdb.on
                : vm.blueStacksAdb.enabled === false
                  ? d.blueStacksAdb.off
                  : d.blueStacksAdb.unknown
              ).short
            }
          </p>
        )}
        {suggestionOnly === null ? null : (
          <div className="tp-emulators__suggestion" data-suggestion="true">
            <SuggestedLabel />
            <Facts layout="inline">
              {suggestionOnly.emulator === null ? null : (
                <Fact
                  label={d.columns.emulator.short}
                  value={<ServerValue kind="name" value={suggestionOnly.emulator} />}
                />
              )}
              {suggestionOnly.name === null ? null : (
                <Fact
                  label={d.columns.instance.short}
                  value={<ServerValue kind="name" value={suggestionOnly.name} />}
                />
              )}
              {suggestionOnly.serial === null ? null : (
                <Fact
                  label={d.columns.address.short}
                  value={<ServerValue kind="id" value={suggestionOnly.serial} />}
                />
              )}
              {suggestionOnly.alsoSeen.length === 0 ? null : (
                <Fact
                  label={d.alsoSeen.short}
                  value={
                    <>
                      {suggestionOnly.alsoSeen.map((serial) => (
                        <ServerValue key={serial} kind="id" value={serial} />
                      ))}
                    </>
                  }
                />
              )}
            </Facts>
            <Action
              vm={suggestionOnly.use}
              label={d.use}
              focusKey="emulators:use:suggestion"
              onClick={() => {
                const command = suggestionOnly.use.command;
                if (command !== null) askUse(command);
              }}
            />
          </div>
        )}
        {vm.scan.read === 'ready' && vm.rows.length === 0 ? <p>{d.none.short}</p> : null}
        {vm.rows.length === 0 ? null : (
          <table className="tp-emulators__table">
            <thead>
              <tr>
                <th scope="col">{d.columns.emulator.short}</th>
                <th scope="col">{d.columns.instance.short}</th>
                <th scope="col">{d.columns.state.short}</th>
                <th scope="col">{d.columns.address.short}</th>
                <th scope="col">{d.columns.actions.short}</th>
              </tr>
            </thead>
            <tbody>
              {vm.rows.map((row) => (
                <Row
                  key={row.key}
                  row={row}
                  suggestion={rowSuggestion(row)}
                  onUse={(command) => {
                    askUse(command);
                  }}
                  onLaunch={send}
                  onPrepare={(command) => {
                    setAsking({ kind: 'prepare', command });
                  }}
                />
              ))}
            </tbody>
          </table>
        )}
        {vm.notProbed.length === 0 ? null : (
          <Tile titleId="emulators-not-probed-title" title={d.notProbed.short}>
            <Facts>
              {vm.notProbed.map((entry, index) => (
                <Fact
                  key={String(index)}
                  label={
                    entry.emulator === null ? (
                      d.columns.emulator.short
                    ) : (
                      <ServerValue kind="name" value={entry.emulator} />
                    )
                  }
                  value={
                    entry.note === null ? null : <ServerValue kind="message" value={entry.note} />
                  }
                />
              ))}
            </Facts>
          </Tile>
        )}
        {vm.configured.length === 0 ? null : (
          <Tile
            className="tp-emulators__sub"
            titleId="emulators-configured-title"
            title={d.configured.short}
          >
            <ul className="tp-emulators__notes">
              {vm.configured.map((row, index) => (
                <li key={String(index)} data-configured={row.instance ?? ''}>
                  {row.instance === null ? null : <ServerValue kind="id" value={row.instance} />}
                  {row.serial === null ? (
                    <span>{t.target.notSet.short}</span>
                  ) : (
                    <ServerValue kind="id" value={row.serial} />
                  )}
                  <span className="tp-emulators__muted">
                    {
                      (row.portOpen === true
                        ? d.portOpen
                        : row.portOpen === false
                          ? d.portClosed
                          : d.portUnknown
                      ).short
                    }
                  </span>
                </li>
              ))}
            </ul>
          </Tile>
        )}
        <ConfirmDialog
          open={asking?.kind === 'use'}
          tier="primary"
          title={d.confirmUse.title.short}
          body={
            <p>
              {asking?.kind === 'use' && asking.command.action.kind === 'adopt'
                ? d.confirmUse.body(
                    // F6M-C-01: the instance the request names, the account of that target.
                    asking.command.action.expectInstance,
                    vm.target?.account ?? t.target.noAccount.short,
                    vm.configuredSerial ?? t.target.notSet.short,
                    asking.to,
                  )
                : null}
            </p>
          }
          confirmLabel={d.confirmUse.confirm.short}
          cancelLabel={d.confirmUse.cancel.short}
          onConfirm={() => {
            const command = asking?.command;
            setAsking(null);
            if (command !== undefined && vm.writeGate === null) send(command);
          }}
          onCancel={() => {
            setAsking(null);
          }}
        />
        <ConfirmDialog
          open={asking?.kind === 'prepare'}
          tier="primary"
          title={d.confirmPrepare.title.short}
          body={<p>{d.confirmPrepare.body.short}</p>}
          confirmLabel={d.confirmPrepare.confirm.short}
          cancelLabel={d.confirmPrepare.cancel.short}
          onConfirm={() => {
            const command = asking?.command;
            setAsking(null);
            if (command !== undefined && vm.writeGate === null) send(command);
          }}
          onCancel={() => {
            setAsking(null);
          }}
        />
      </RegionBody>
    </>
  );
}

/** M5: the connection tools, the license consent, the install and its progress reads. */
function ToolsSection() {
  const vm = useView(selectEmulatorsPage);
  const { commands } = useDashboard();
  const [license, setLicense] = useState(false);
  const tools = vm.tools;
  const install: EmulatorsPageVM['tools']['install'] =
    tools.install.enabled && !license
      ? { ...tools.install, enabled: false, reason: 'accept_license', command: null }
      : tools.install;
  return (
    <>
      <RegionEnd>
        {tools.ageSec === null ? null : <span>{t.tools.readAge(tools.ageSec)}</span>}
        <HelpTip label={t.tools.title.short}>
          <p>{t.tools.identity.short}</p>
          {t.tools.identity.why === undefined ? null : <p>{t.tools.identity.why}</p>}
        </HelpTip>
      </RegionEnd>
      <RegionBody>
        <p
          className="tp-emulators__line"
          data-tools={
            tools.read !== 'ready'
              ? tools.read
              : tools.installed
                ? 'installed'
                : (tools.stage ?? 'unknown')
          }
        >
          <span>
            {
              (tools.read === 'not_read'
                ? t.tools.reading
                : tools.read === 'unavailable'
                  ? t.tools.unavailable
                  : tools.installed
                    ? t.tools.installed
                    : t.tools.notInstalled
              ).short
            }
          </span>
        </p>
        <Facts>
          {tools.stage === null ? null : (
            <Fact
              label={t.tools.stage.short}
              value={<ServerValue kind="id" value={tools.stage} />}
            />
          )}
          {tools.path === null ? null : (
            <Fact label={t.tools.path.short} value={<ServerValue kind="id" value={tools.path} />} />
          )}
        </Facts>
        {tools.message === null ? null : (
          <p role="status">
            <ServerValue kind="message" value={tools.message} />
          </p>
        )}
        {tools.inProgress && tools.percent !== null ? (
          <p className="tp-emulators__line">
            <progress max={100} value={tools.percent} aria-label={t.tools.progress.short} />
            <span>{t.tools.percent(tools.percent)}</span>
          </p>
        ) : null}
        {tools.installed ? null : (
          <>
            <label className="tp-emulators__check">
              <input
                type="checkbox"
                checked={license}
                data-focus-key="emulators:license"
                onChange={(event) => {
                  setLicense(event.currentTarget.checked);
                }}
              />
              <span>{t.tools.license.short}</span>
            </label>
            <div className="tp-emulators__row">
              <Action
                vm={install}
                label={t.tools.install}
                focusKey="emulators:install"
                onClick={() => {
                  if (install.command !== null) void commands.emulatorWrite(install.command);
                }}
                beside={
                  <Button
                    tier="quiet"
                    label={t.tools.check.short}
                    focusKey="emulators:tools:check"
                    onClick={() => {
                      commands.checkTools();
                    }}
                  />
                }
              />
            </div>
            {tools.inProgress ? <p>{t.tools.leaving.short}</p> : null}
          </>
        )}
        {tools.outcome === null ? null : <Outcome outcome={tools.outcome} />}
      </RegionBody>
    </>
  );
}

/** M7: the saved preview settings of this emulator (the shared settings editor). */
function PreviewSection() {
  const vm = useView(selectEmulatorsPage);
  const { commands } = useDashboard();
  return (
    <>
      <RegionEnd>
        {vm.previewAgeSec === null ? null : <span>{t.preview.readAge(vm.previewAgeSec)}</span>}
      </RegionEnd>
      <RegionBody>
        <ActionRow>
          <a href={pageHref('dashboard')}>{t.preview.dashboard.short}</a>
        </ActionRow>
        {vm.previewState === 'ready' ? null : (
          <div className="tp-emulators__row tp-control-row" data-preview-state={vm.previewState}>
            <CopyLine entry={t.preview.states[vm.previewState]} />
            <Button
              tier="quiet"
              label={t.preview.check.short}
              focusKey="emulators:preview:check"
              onClick={() => {
                commands.checkSettings();
              }}
            />
          </div>
        )}
        {vm.preview === null ? null : (
          <SettingsModuleForm
            vm={vm.preview}
            labelledBy="emulatorsPreview-title"
            intro={t.preview.intro}
            onEdit={commands.editDraft}
            onDiscard={commands.discardDraft}
            onSave={(command) => {
              void commands.saveSettings(command);
            }}
            onDismiss={commands.dismiss}
          />
        )}
      </RegionBody>
    </>
  );
}

function NotSent() {
  const vm = useView(selectOutcomes);
  const notice = vm.notices.find((entry) => entry.kind === 'command_not_sent');
  const notSent: Readonly<Record<string, CopyEntry>> = t.notSent;
  const entry = notice === undefined ? undefined : notSent[notice.reason];
  if (notice === undefined || entry === undefined) return null;
  return (
    <div className="tp-emulators__line" data-not-sent={notice.reason} role="status">
      <span>{entry.short}</span>
    </div>
  );
}

/**
 * The Emulators page (emulators brief M1 to M8): this emulator and its live connection,
 * what the last scan detected with its explicit actions, the connection tools, and the
 * saved preview settings. Opening it sends nothing that changes a device or a connection.
 */
export function EmulatorsPage() {
  const { commands } = useDashboard();
  useEffect(() => {
    commands.openEmulators(true);
    return () => {
      commands.openEmulators(false);
    };
  }, [commands]);
  return (
    <>
      <header className="tp-topbar tp-emulators-topbar">
        <RegionBoundary
          fallback={<RevocationFallback part="topBar" />}
          resetKey={useEpochKey()}
          onError={usePartError('topBar')}
        >
          <FaultPoint part="topBar" />
          <TopBar pageId="emulators" title={t.pageName.short} />
        </RegionBoundary>
      </header>
      <main id="main" className="tp-main" tabIndex={-1}>
        <FaultPoint part="page" />
        <div className="tp-emulators">
          <EmulatorSettings />
          <EmulatorsRegion id="emulatorsTarget" title={t.target.title}>
            <TargetSection />
          </EmulatorsRegion>
          <EmulatorsRegion id="emulatorsDetected" title={t.detected.title}>
            <DetectedSection />
          </EmulatorsRegion>
          <EmulatorsRegion id="emulatorsTools" title={t.tools.title}>
            <ToolsSection />
          </EmulatorsRegion>
          <EmulatorsRegion id="emulatorsPreview" title={t.preview.title}>
            <PreviewSection />
          </EmulatorsRegion>
        </div>
      </main>
    </>
  );
}
