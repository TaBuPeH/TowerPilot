import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { ageAgo, lastRead } from '../../../copy/dashboard/shell.ts';
import type { CopyEntry } from '../../../copy/text.ts';
import { actionName, isShoppingAction } from '../../../core/actions.ts';
import { ownOr } from '../../../core/read.ts';
import type { ServerText } from '../../../copy/server.ts';
import {
  selectThisBattle,
  type ActionCodeVM,
  type ActionLastVM,
  type ActionNextVM,
  type ActionRowVM,
  type ActionSettingVM,
  type BattleCommandVM,
  type QueueVM,
  type RoutineRowVM,
  type ThisBattleVM,
  type ShoppingTryOnceVM,
} from '../../../store/selectors/thisBattle.ts';
import type { ChipVM } from '../../../store/viewModels.ts';
import { Button } from '../../../ui/Button.tsx';
import { ActionRow as Actions, Reason } from '../../../ui/ActionRow.tsx';
import { Chip } from '../../../ui/Chip.tsx';
import { Disclosure } from '../../../ui/Disclosure.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { Notice } from '../../../ui/Notice.tsx';
import { useAnnounce } from '../../../ui/LiveRegion.tsx';
import { RegionBody, RegionEnd, RegionInline } from '../../../ui/Region.tsx';
import { pageHref } from '../../../ui/routes.ts';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { useDashboard, useView } from '../context.ts';
import { DashboardRegion } from '../shared/DashboardRegion.tsx';
import { WhyTip } from '../shared/WhyTip.tsx';
import './thisBattle.css';

const words = copy.dashboard.thisBattle;

/** The one element that states the region's shared Run now reason (see `SharedReason`). */
const SHARED_REASON_ID = 'battle-run-now-reason';

/** The words of a disabled Run now or Try once reason code. */
function reasonEntry(reason: string | null): CopyEntry {
  return ownOr(words.disabled, reason ?? 'report_unavailable', words.disabled.report_unavailable);
}

/** The id a disabled button's description points at: the shared reason, else its own. */
function describedBy(enabled: boolean, shared: string | null, own: string): string | undefined {
  if (enabled) return undefined;
  return shared === null ? own : SHARED_REASON_ID;
}

/**
 * F-055: "the blocker word": a routine's blocker is a code the runtime
 * publishes; known codes read as words, any other reads "blocked" with the
 * code as sent under Why.
 */
function Blocker({ code }: { readonly code: ServerText<'message'> }) {
  const known: Readonly<Record<string, CopyEntry>> = words.blockers;
  if (Object.hasOwn(known, code))
    return <span>{ownOr(known, code, words.blockers.other).short}</span>;
  return (
    <span>
      {words.blockers.other.short}
      <Disclosure label={copy.common.why.short}>
        <ServerValue value={code} kind="message" />
      </Disclosure>
    </span>
  );
}

function Decision({ value }: { readonly value: ChipVM }) {
  const line = ownOr(words.lines, value.line, words.unknown).short;
  return (
    <>
      {value.word === null ? null : <Chip state={value.word} />}
      {value.word !== null && copy.states[value.word].short === line ? null : <span>{line}</span>}
    </>
  );
}

function outcomeEntry(outcome: ThisBattleVM['outcomes'][number]): CopyEntry {
  if (outcome.command !== null) return ownOr(words.lines, outcome.command.line, words.unknown);
  if (outcome.settledByRead !== null) return words.recorded;
  if (outcome.phase === 'sending') return copy.dashboard.shell.sending;
  return outcome.outcome === 'not_recorded' ? words.refused : words.outcomeUnknown;
}

/** RUN-12: the live editor and the battle run's saved settings, on the new Runs page. */
function ScopedLinks() {
  const { runKey } = useView(selectThisBattle);
  return (
    <div className="tp-battle__links tp-control-row">
      <a href={pageHref('runs', { kind: 'live' })} data-focus-key="battle-edit">
        {words.actions.edit.short}
      </a>
      <a
        href={pageHref('runs', runKey === null ? null : { kind: 'saved', run: runKey })}
        data-focus-key="battle-saved"
      >
        {words.actions.saved.short}
      </a>
    </div>
  );
}

function Result({ row, queue }: { readonly row: RoutineRowVM; readonly queue: QueueVM | null }) {
  return (
    <div className="tp-battle__result">
      <Decision value={row.result.chip} />
      {row.resultAgeSec === null ? null : (
        <span className="tp-battle__age">
          <span className="tp-visually-hidden">{words.listSeparator.short}</span>
          {ageAgo(row.resultAgeSec)}
        </span>
      )}
      {row.result.confirmedPurchases === null ? null : (
        <span>{words.purchases(row.result.confirmedPurchases)}</span>
      )}
      {row.result.reason === null ? null : (
        <Disclosure label={copy.common.why.short}>
          <ServerValue value={row.result.reason} kind="message" />
        </Disclosure>
      )}
      {queue === null ? null : <ShoppingDecision value={queue} />}
    </div>
  );
}

function RoutineRow({
  row,
  queue,
  battleKey,
  sharedReason,
}: {
  readonly row: RoutineRowVM;
  readonly queue: QueueVM | null;
  readonly battleKey: string;
  readonly sharedReason: string | null;
}) {
  const { commands } = useDashboard();
  const reason = reasonEntry(row.runNow.reason);
  const nameId = `battle-${row.routine}-name`;
  const reasonId = `battle-${row.routine}-reason`;
  const unsupported = row.runNow.reason === 'action_unsupported';
  return (
    <tbody className="tp-battle__routine">
      <tr className="tp-battle__row">
        <th scope="row">
          <strong className="tp-battle__name" id={nameId}>
            {actionName(copy.actions.names, row.routine, copy.actions.otherName).short}
          </strong>
        </th>
        <td className="tp-battle__setting">
          <span className="tp-battle__column-label">{words.columns.battle.short}</span>
          {unsupported ? (
            words.disabled.action_unsupported.short
          ) : row.timing.row === 'F-050' ? (
            <Decision value={row.timing} />
          ) : row.savedEnabled === null ? (
            words.unknown.short
          ) : row.savedEnabled ? (
            words.on.short
          ) : (
            words.off.short
          )}
          {!unsupported && row.savedIntervalSec !== null ? (
            <span>{words.every(row.savedIntervalSec)}</span>
          ) : null}
        </td>
        <td className="tp-battle__next">
          <span className="tp-battle__column-label">{words.columns.next.short}</span>
          {unsupported || row.timing.row === 'F-050' ? (
            words.dash.short
          ) : row.timing.line === 'paused_until_window' && row.windowEndsAt !== null ? (
            words.paused(row.windowEndsAt)
          ) : row.timing.line === 'routine_blocker' && row.blocker !== null ? (
            <Blocker code={row.blocker} />
          ) : row.nextInSec !== null ? (
            words.next(row.nextInSec)
          ) : row.timing.line === 'routine_reported' && row.stateCode !== null ? (
            ownOr(words.states, row.stateCode, words.unknown).short
          ) : (
            <Decision value={row.timing} />
          )}
        </td>
        <td className="tp-battle__last">
          <span className="tp-battle__column-label">{words.columns.result.short}</span>
          <Result row={row} queue={queue} />
        </td>
        <td className="tp-battle__action">
          <Actions
            reason={
              row.runNow.enabled || sharedReason !== null ? undefined : (
                <Reason id={reasonId} entry={reason} />
              )
            }
          >
            {/* Name = visible label + target ("Run now Ad gems"): WCAG 2.5.3 label in name, axe label-content-name-mismatch. */}
            <button
              type="button"
              className="tp-button"
              data-tier="secondary"
              data-focus-key={`run-now-${row.routine}`}
              aria-labelledby={`${nameId}-run ${nameId}-now ${nameId}`}
              aria-disabled={row.runNow.enabled ? undefined : true}
              aria-describedby={describedBy(row.runNow.enabled, sharedReason, reasonId)}
              onClick={() => {
                if (row.runNow.enabled && row.runNow.command !== null)
                  void commands.runNow(row.runNow.command);
              }}
            >
              <span id={`${nameId}-run`}>{words.run.short}</span>
              <span id={`${nameId}-now`}>{words.now.short}</span>
            </button>
          </Actions>
        </td>
      </tr>
      {queue === null ? null : (
        <tr className="tp-battle__queue-row">
          <td colSpan={5}>
            <Queue key={battleKey} value={queue} sharedReason={sharedReason} />
          </td>
        </tr>
      )}
    </tbody>
  );
}

/** Rule 6: "Not recognised", with the code as sent under Why. */
function NotRecognised({ code }: { readonly code: ServerText<'id'> | null }) {
  return (
    <div className="tp-battle__inline">
      <span>{words.notRecognised.short}</span>
      {code === null ? null : (
        <Disclosure label={copy.common.why.short}>
          <ServerValue value={code} kind="id" />
        </Disclosure>
      )}
    </div>
  );
}

/** One result or effect code in its words: known, not sent, or not recognised with its code. */
function CodeWords<K extends string>({
  code,
  table,
}: {
  readonly code: ActionCodeVM<K>;
  readonly table: Readonly<Record<string, CopyEntry>>;
}) {
  switch (code.kind) {
    case 'known':
      return <span>{ownOr(table, code.code, words.notRecognised).short}</span>;
    case 'unreported':
      return <span>{words.unknown.short}</span>;
    case 'unrecognised':
      return <NotRecognised code={code.code} />;
  }
}

/** AC1/AC3: a result, its effect (rule 2: only `confirmed` reads as confirmed), age and reason. */
function LastWords({ last }: { readonly last: ActionLastVM }) {
  return (
    <>
      <CodeWords code={last.result} table={copy.actions.results} />
      <CodeWords code={last.effect} table={copy.actions.effects} />
      {last.ageSec === null ? null : <span>{ageAgo(last.ageSec)}</span>}
      {last.manual ? <span>{words.fromRunNow.short}</span> : null}
      {last.reason === null ? null : (
        <Disclosure label={copy.common.why.short}>
          <ServerValue value={last.reason} kind="message" />
        </Disclosure>
      )}
    </>
  );
}

/** AC1/AC5: the Next cell of an action row; an old reading has no Due word and no countdown. */
function NextWords({ next }: { readonly next: ActionNextVM }) {
  switch (next.kind) {
    case 'off':
      return <span>{words.off.short}</span>;
    case 'running':
      return (
        <span>
          {next.endsInSec === null
            ? words.itemNext.running.short
            : words.runningFor(next.endsInSec)}
        </span>
      );
    case 'blocker':
      return next.code === null ? (
        <span>{words.blockers.other.short}</span>
      ) : (
        <Blocker code={next.code} />
      );
    case 'due':
      return <span>{words.itemNext.due.short}</span>;
    case 'in':
      return <span>{words.next(next.sec)}</span>;
    case 'not_reported':
      return <span>{words.itemNext.notReported.short}</span>;
    case 'state':
      return <span>{words.itemNext[next.code].short}</span>;
    case 'unrecognised':
      return <NotRecognised code={next.code} />;
    case 'last_known':
      return <span>{words.lastKnown(next.ageSec)}</span>;
    case 'not_current':
      return <span>{words.itemNext.notCurrent.short}</span>;
    case 'prepared':
      return <span>{words.dash.short}</span>;
  }
}

/** AC1: one descriptor field: "label value unit", or not recognised when its type is not read. */
function SettingLine({ field }: { readonly field: ActionSettingVM }) {
  return (
    <Fact
      label={
        field.label === null ? (
          <span>{copy.actions.otherField.short}</span>
        ) : (
          <ServerValue value={field.label} kind="name" />
        )
      }
      value={
        field.unrecognisedType !== null ? (
          <NotRecognised code={field.unrecognisedType} />
        ) : (
          <>
            <span>
              {field.value === null
                ? words.unknown.short
                : typeof field.value === 'boolean'
                  ? field.value
                    ? words.on.short
                    : words.off.short
                  : words.fieldNumber(field.value)}
            </span>
            {field.unit === null ? null : <ServerValue value={field.unit} kind="name" />}
          </>
        )
      }
    />
  );
}

/** AC1/AC3: one `runtime.actions` item, rendered from the item alone (no branch per action). */
function ActionRow({
  row,
  index,
  queue,
  battleKey,
  sharedReason,
}: {
  readonly row: ActionRowVM;
  readonly index: number;
  readonly queue: QueueVM | null;
  readonly battleKey: string;
  readonly sharedReason: string | null;
}) {
  const { commands } = useDashboard();
  const reason = reasonEntry(row.runNow.reason);
  const nameId = `battle-action-${String(index)}-name`;
  const reasonId = `battle-action-${String(index)}-reason`;
  return (
    <tbody className="tp-battle__routine">
      <tr className="tp-battle__row">
        <th scope="row">
          <strong className="tp-battle__name" id={nameId}>
            {row.label !== null ? (
              <ServerValue value={row.label} kind="name" />
            ) : row.code !== null ? (
              <ServerValue value={row.code} kind="id" />
            ) : (
              copy.actions.otherName.short
            )}
          </strong>
        </th>
        <td className="tp-battle__setting">
          <span className="tp-battle__column-label">{words.columns.battle.short}</span>
          <span>
            {row.enabled === null
              ? words.unknown.short
              : row.enabled
                ? words.on.short
                : words.off.short}
          </span>
          {row.settings.length === 0 ? null : (
            <Disclosure label={words.settings.short}>
              <Facts className="tp-battle__settings">
                {row.settings.map((field) => (
                  <SettingLine key={field.path} field={field} />
                ))}
              </Facts>
            </Disclosure>
          )}
        </td>
        <td className="tp-battle__next">
          <span className="tp-battle__column-label">{words.columns.next.short}</span>
          <NextWords next={row.next} />
        </td>
        <td className="tp-battle__last">
          <span className="tp-battle__column-label">{words.columns.result.short}</span>
          <div className="tp-battle__result">
            {row.last === null ? (
              <span>{words.lines.no_result_yet.short}</span>
            ) : (
              <LastWords last={row.last} />
            )}
            {queue === null ? null : <ShoppingDecision value={queue} />}
          </div>
        </td>
        <td className="tp-battle__action">
          <Actions
            reason={
              row.runNow.enabled || sharedReason !== null ? undefined : (
                <div className="tp-battle__reason">
                  <span id={reasonId}>
                    {reason.short}
                    {row.runNowServerReason === null ? null : (
                      <>
                        {words.separator.short}
                        <ServerValue value={row.runNowServerReason} kind="message" />
                      </>
                    )}
                  </span>
                  <WhyTip subject={reason.short} why={reason.why} />
                </div>
              )
            }
          >
            {/* Name = visible label + target ("Run now Ad gems"): WCAG 2.5.3 label in name. */}
            <button
              type="button"
              className="tp-button"
              data-tier="secondary"
              data-focus-key={`run-now-${row.key}`}
              aria-labelledby={`${nameId}-run ${nameId}-now ${nameId}`}
              aria-disabled={row.runNow.enabled ? undefined : true}
              aria-describedby={describedBy(row.runNow.enabled, sharedReason, reasonId)}
              onClick={() => {
                if (row.runNow.enabled && row.runNow.command !== null)
                  void commands.runNow(row.runNow.command);
              }}
            >
              <span id={`${nameId}-run`}>{words.run.short}</span>
              <span id={`${nameId}-now`}>{words.now.short}</span>
            </button>
          </Actions>
        </td>
      </tr>
      {queue === null ? null : (
        <tr className="tp-battle__queue-row">
          <td colSpan={5}>
            <Queue key={battleKey} value={queue} sharedReason={sharedReason} />
          </td>
        </tr>
      )}
    </tbody>
  );
}

/**
 * The one reason every Run now and Try once button shares, stated once for the region; each
 * disabled button's description points here (root's visual note: never seven copies).
 */
function SharedReason({ reason }: { readonly reason: string }) {
  return (
    <div className="tp-battle__shared-reason">
      <Reason id={SHARED_REASON_ID} entry={reasonEntry(reason)} />
    </div>
  );
}

/** AC5: why the action readings are not current, once above the rows, with a fresh read. */
function ReadingsNotice({
  value,
  sharedReason,
}: {
  readonly value: NonNullable<ThisBattleVM['actionsNotice']>;
  readonly sharedReason: string | null;
}) {
  const { commands } = useDashboard();
  const isShared = value.reason === sharedReason;
  const entry = isShared
    ? reasonEntry(value.reason)
    : ownOr(words.readings, value.reason, words.readings.action_report_time_invalid);
  return (
    <Notice
      state="unknown"
      line={<span id={isShared ? SHARED_REASON_ID : undefined}>{entry.short}</span>}
      {...(value.ageSec === null ? {} : { age: ageAgo(value.ageSec) })}
      {...(entry.why === undefined ? {} : { why: entry.why })}
      actions={
        <Button tier="quiet" label={copy.shoppingLive.check.short} onClick={commands.checkBattle} />
      }
    />
  );
}

function ShoppingTryOnceAction({
  item,
  sharedReason,
}: {
  readonly item: ShoppingTryOnceVM;
  readonly sharedReason: string | null;
}) {
  const { commands } = useDashboard();
  const id = `shopping-try-${item.action}`;
  return (
    <div className="tp-shopping-try__action">
      <Actions
        reason={
          item.button.enabled || sharedReason !== null ? undefined : (
            <Reason id={`${id}-reason`} entry={reasonEntry(item.button.reason)} />
          )
        }
      >
        <button
          type="button"
          className="tp-button"
          data-tier="secondary"
          aria-labelledby={`${id}-label ${id}-target`}
          aria-disabled={item.button.enabled ? undefined : true}
          aria-describedby={describedBy(item.button.enabled, sharedReason, `${id}-reason`)}
          onClick={() => {
            if (item.button.enabled && item.button.command !== null)
              void commands.runNow(item.button.command);
          }}
        >
          <span id={`${id}-label`}>{words.tryOnce.short}</span>
        </button>
      </Actions>
    </div>
  );
}

function ShoppingTryOnce({
  value,
  sharedReason,
}: {
  readonly value: readonly ShoppingTryOnceVM[];
  readonly sharedReason: string | null;
}) {
  return (
    <div className="tp-shopping-try">
      <p>
        {words.tryNotice.short}
        <WhyTip subject={words.tryOnce.short} why={words.tryNotice.why} />
      </p>
      {value.map((item) => (
        <div key={item.action} className="tp-control-row">
          <span id={`shopping-try-${item.action}-target`}>
            {item.known === null ? (
              item.label === null ? (
                words.queue.unknownUpgrade.short
              ) : (
                <ServerValue kind="name" value={item.label} />
              )
            ) : (
              words.tryTargets[item.known].short
            )}
          </span>
          <ShoppingTryOnceAction item={item} sharedReason={sharedReason} />
        </div>
      ))}
    </div>
  );
}

function Queue({
  value,
  sharedReason,
}: {
  readonly value: QueueVM;
  /** The region's shared Run now reason, passed down from its row. */
  readonly sharedReason: string | null;
}) {
  const { tryOnce } = useView(selectThisBattle);
  return (
    <div className="tp-battle__queue">
      <ShoppingTryOnce
        value={tryOnce.filter((item) => item.known !== null)}
        sharedReason={sharedReason}
      />
      {value.warnings.map((warning) => {
        const entry = ownOr(
          {
            reserve_not_protected: words.queue.reserve_not_protected,
            purchase_quantity_unverified: words.queue.purchase_quantity_unverified,
          },
          warning.line,
          words.unknown,
        );
        return (
          <Notice
            key={warning.line}
            state={warning.word ?? 'unknown'}
            line={entry.short}
            {...(entry.why === undefined ? {} : { why: entry.why })}
            actions={warning.line === 'reserve_not_protected' ? <ScopedLinks /> : null}
          />
        );
      })}
      {value.needsReview ? (
        <p>{words.queue.review.short}</p>
      ) : (
        <Facts layout="inline">
          <Fact
            label={words.queue.rule.short}
            value={ownOr(words.queue.rules, value.mode ?? '', words.queue.unknownRule).short}
          />
          <Fact
            label={words.queue.attempts.short}
            value={
              ownOr(words.queue.cadence, value.cadence ?? '', words.queue.cadenceUnknown).short
            }
          />
          <Fact
            label={words.queue.reserve.short}
            value={
              value.reserve === null
                ? words.queue.noReserve.short
                : words.fieldNumber(value.reserve)
            }
          />
        </Facts>
      )}
      <Disclosure
        label={words.queue.title.short}
        count={value.items.length}
        focusKey="battle-queue"
        preview={
          <>
            {value.items.slice(0, 3).map((item, index) => (
              <span key={item.key}>
                {index === 0 ? null : words.listSeparator.short}
                {item.label === null ? (
                  words.queue.unknownUpgrade.short
                ) : (
                  <ServerValue value={item.label} kind="name" />
                )}
              </span>
            ))}
          </>
        }
      >
        {value.items.length === 0 ? (
          <p>{words.queue.empty.short}</p>
        ) : (
          <ol>
            {value.items.map((item) => (
              <li key={item.key}>
                <div className="tp-battle__queue-entry tp-control-row">
                  <span
                    id={
                      item.tryOnce === null
                        ? undefined
                        : `shopping-try-${item.tryOnce.action}-target`
                    }
                  >
                    {item.label === null ? (
                      words.queue.unknownUpgrade.short
                    ) : (
                      <ServerValue value={item.label} kind="name" />
                    )}
                  </span>
                  {item.enabled ? null : <span>{words.queue.disabled.short}</span>}
                  <span>
                    {item.tapLimit === null ? words.queue.noCap.short : words.tapCap(item.tapLimit)}
                  </span>
                  {value.mode === 'weighted_split' && item.weight !== null ? (
                    <span>{words.weight(item.weight)}</span>
                  ) : null}
                  {item.tryOnce === null ? null : (
                    <ShoppingTryOnceAction item={item.tryOnce} sharedReason={sharedReason} />
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
        <ScopedLinks />
      </Disclosure>
    </div>
  );
}

function ShoppingDecision({ value }: { readonly value: QueueVM }) {
  return (
    <Disclosure label={copy.common.why.short} focusKey="shopping-decision">
      {value.reportedAgeSec === null ? null : <p>{words.reported(value.reportedAgeSec)}</p>}
      <p>{words.queue.decision.short}</p>
      {value.decision === null ? null : <ServerValue value={value.decision} kind="detail" />}
      {value.decisionReason === null ? null : (
        <ServerValue value={value.decisionReason} kind="message" />
      )}
      {value.decisionUpgrade === null ? null : (
        <ServerValue value={value.decisionUpgrade} kind="name" />
      )}
      {value.pendingUpgrade === null ? null : (
        <ServerValue label={words.queue.pending.short} value={value.pendingUpgrade} kind="name" />
      )}
      <p>{words.queue.confirmation.short}</p>
      {value.confirmationUpgrade === null ? null : (
        <ServerValue value={value.confirmationUpgrade} kind="name" />
      )}
      {value.confirmationOutcome === null ? null : (
        <ServerValue value={value.confirmationOutcome} kind="detail" />
      )}
      {value.confirmationReason === null ? null : (
        <ServerValue value={value.confirmationReason} kind="message" />
      )}
    </Disclosure>
  );
}

function CommandRow({ value }: { readonly value: BattleCommandVM }) {
  return (
    <li className="tp-battle__command">
      <Decision value={value} />
      {value.shoppingState === null ? null : (
        <span>{words.shoppingStates[value.shoppingState].short}</span>
      )}
      {value.actionText === null ? null : <ServerValue kind="id" value={value.actionText} />}
      {value.serverEvidence === null ? null : (
        <Disclosure label={words.commandEvidence.short}>
          <ServerValue kind="detail" value={value.serverEvidence} />
        </Disclosure>
      )}
      {value.row === 'K4' && value.resultChip !== null ? (
        <Decision value={value.resultChip} />
      ) : null}
      {value.outcome === null ? null : (
        <div className="tp-battle__outcome">
          <span>{words.outcome.short}</span>
          <LastWords last={value.outcome} />
        </div>
      )}
      {value.resultAgeSec === null ? null : <span>{ageAgo(value.resultAgeSec)}</span>}
      {value.row === 'K4' && value.result.confirmedPurchases !== null ? (
        <span>{words.purchases(value.result.confirmedPurchases)}</span>
      ) : null}
      {value.reason === null && value.result.reason === null ? null : (
        <Disclosure label={copy.common.why.short}>
          {value.reason === null ? null : <ServerValue value={value.reason} kind="message" />}
          {value.result.reason === null ? null : (
            <ServerValue value={value.result.reason} kind="message" />
          )}
        </Disclosure>
      )}
    </li>
  );
}

function BattleContent() {
  const vm = useView(selectThisBattle);
  const { commands } = useDashboard();
  const announce = useAnnounce();
  const previousWord = useRef(vm.header.word);
  const announcedOutcomes = useRef(new Map<string, string>());
  const [dismissed, setDismissed] = useState(0);
  const outcomeArea = useRef<HTMLDivElement>(null);
  const focused = useRef<HTMLElement | null>(null);
  const neighbours = useRef<HTMLElement[]>([]);
  useEffect(() => {
    if (previousWord.current !== vm.header.word && vm.header.word !== null)
      announce(copy.states[vm.header.word].short);
    previousWord.current = vm.header.word;
  }, [announce, vm.header.word]);
  useEffect(() => {
    for (const outcome of vm.outcomes) {
      if (outcome.phase === 'sending') continue;
      const signature = `${outcome.phase}:${outcome.outcome}:${outcome.settledByRead ?? ''}:${outcome.command?.line ?? ''}`;
      if (announcedOutcomes.current.get(outcome.id) === signature) continue;
      announcedOutcomes.current.set(outcome.id, signature);
      announce(outcomeEntry(outcome).short);
    }
  }, [announce, vm.outcomes]);
  useEffect(() => {
    if (dismissed === 0) return undefined;
    const timeout = window.setTimeout(() => {
      setDismissed(0);
    }, 8000);
    return () => {
      window.clearTimeout(timeout);
    };
  }, [dismissed]);
  useLayoutEffect(() => {
    if (focused.current !== null && !focused.current.isConnected) {
      (
        neighbours.current.find((target) => target.isConnected) ??
        document.getElementById('thisBattle-title')
      )?.focus();
      focused.current = null;
    }
  }, [vm.outcomes, vm.noTrackedBattle]);
  if (vm.noTrackedBattle)
    return (
      // F4-02: an empty region is one line beside its title.
      <RegionInline>
        <span>{words.noTracked.short}</span>
        <span>{words.gameUntracked.short}</span>
      </RegionInline>
    );
  return (
    <>
      <RegionEnd>
        <div className="tp-battle__header">
          <Facts layout="inline">
            <Fact
              label={words.run.short}
              value={
                vm.label === null ? (
                  words.unknown.short
                ) : (
                  <ServerValue value={vm.label} kind="name" />
                )
              }
            />
            <Fact
              label={words.waveLabel.short}
              value={vm.wave === null ? words.unknown.short : words.fieldNumber(vm.wave)}
            />
          </Facts>
          <div className="tp-battle__status">
            {vm.header.word === null ? null : <Chip state={vm.header.word} />}
            {vm.header.row === 'B7' && vm.reportAgeSec !== null
              ? words.oldReport(vm.reportAgeSec)
              : ownOr(words.lines, vm.header.line, words.unknown).short}
            {vm.header.row === 'B1' && vm.readAgeSec !== null ? (
              <span>{lastRead(vm.readAgeSec)}</span>
            ) : null}
            {(vm.header.row === 'B5' || vm.header.row === 'B6') && vm.reportAgeSec !== null ? (
              <span>
                {words.listSeparator.short}
                {words.reported(vm.reportAgeSec)}
              </span>
            ) : null}
          </div>
        </div>
      </RegionEnd>
      <RegionBody>
        {vm.changed ? (
          <div>
            {words.changed.short}
            <ScopedLinks />
          </div>
        ) : null}
        {vm.actionsNotice === null ? null : (
          <ReadingsNotice value={vm.actionsNotice} sharedReason={vm.sharedReason} />
        )}
        {vm.sharedReason === null || vm.sharedReason === vm.actionsNotice?.reason ? null : (
          <SharedReason reason={vm.sharedReason} />
        )}
        {vm.routines.length === 0 && (vm.actions?.length ?? 0) === 0 ? null : (
          <table className="tp-battle__routines" aria-labelledby="thisBattle-title">
            <thead className="tp-battle__columns">
              <tr>
                <th scope="col">{words.columns.routine.short}</th>
                <th scope="col">{words.columns.battle.short}</th>
                <th scope="col">{words.columns.next.short}</th>
                <th scope="col">{words.columns.result.short}</th>
                <th scope="col">{words.columns.action.short}</th>
              </tr>
            </thead>
            {vm.routines.map((row) => (
              <RoutineRow
                key={row.routine}
                row={row}
                queue={isShoppingAction(row.routine) ? vm.queue : null}
                battleKey={vm.battleKey}
                sharedReason={vm.sharedReason}
              />
            ))}
            {(vm.actions ?? []).map((row, index) => (
              <ActionRow
                key={row.key}
                row={row}
                index={index}
                queue={row.shopping ? vm.queue : null}
                battleKey={vm.battleKey}
                sharedReason={vm.sharedReason}
              />
            ))}
          </table>
        )}
        <ul className="tp-rows">
          {vm.commands.map((command) => (
            <CommandRow key={command.key} value={command} />
          ))}
        </ul>
        <ul className="tp-rows">
          {vm.outcomes
            .filter(
              (outcome) =>
                outcome.command !== null &&
                !vm.commands.some((command) => command.key === outcome.command?.key),
            )
            .map((outcome) =>
              outcome.command === null ? null : (
                <CommandRow key={outcome.id} value={outcome.command} />
              ),
            )}
        </ul>
        <div
          ref={outcomeArea}
          onFocusCapture={(event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            focused.current = target;
            const rows = Array.from(
              outcomeArea.current?.querySelectorAll<HTMLElement>('[data-battle-outcome]') ?? [],
            );
            const index = rows.findIndex((row) => row.contains(target));
            neighbours.current = [
              ...rows.slice(index + 1),
              ...rows.slice(0, index).reverse(),
            ].flatMap((row) => {
              const first = row.querySelector<HTMLElement>('button, summary, a[href]');
              return first === null ? [] : [first];
            });
          }}
          onBlurCapture={(event) => {
            if (
              event.relatedTarget instanceof Node &&
              !event.currentTarget.contains(event.relatedTarget)
            )
              focused.current = null;
          }}
        >
          {vm.outcomes
            .filter((outcome) => outcome.settledByRead === null && outcome.command === null)
            .map((outcome) => {
              const entry = outcomeEntry(outcome);
              return (
                <div key={outcome.id} data-battle-outcome={outcome.id}>
                  <Notice
                    state={
                      outcome.phase === 'sending'
                        ? 'waiting'
                        : outcome.outcome === 'not_recorded'
                          ? 'failed'
                          : 'unknown'
                    }
                    line={entry.short}
                    {...(entry.why === undefined ? {} : { why: entry.why })}
                    server={
                      outcome.error === null ? null : (
                        <ServerValue value={outcome.error} kind="message" />
                      )
                    }
                    actions={
                      <>
                        <Button
                          tier="quiet"
                          label={copy.shoppingLive.check.short}
                          onClick={commands.checkBattle}
                        />
                        {outcome.resendOffered ? (
                          <>
                            <Button
                              tier="secondary"
                              label={words.actions.resend.short}
                              {...(vm.resendAvailable
                                ? {}
                                : { disabled: true, reasonId: `resend-${outcome.id}-reason` })}
                              onClick={() => {
                                void commands.resend(outcome.id);
                              }}
                            />
                            {vm.resendAvailable ? null : (
                              <span id={`resend-${outcome.id}-reason`}>
                                {words.resendUnavailable.short}
                              </span>
                            )}
                          </>
                        ) : null}
                        <Button
                          tier="quiet"
                          label={words.actions.dismiss.short}
                          describedBy={`dismiss-${outcome.id}`}
                          onClick={() => {
                            commands.dismiss(outcome.id);
                            setDismissed((count) => count + 1);
                          }}
                        />
                        <span className="tp-battle__sr" id={`dismiss-${outcome.id}`}>
                          {words.dismissDescription.short}
                        </span>
                      </>
                    }
                  />
                </div>
              );
            })}
        </div>
        {dismissed > 0 ? <p>{words.dismissed.short}</p> : null}
      </RegionBody>
    </>
  );
}

/** Region 'thisBattle' (spec 2.1): owned by its region author. */
export function ThisBattle() {
  return (
    <DashboardRegion id="thisBattle">
      <BattleContent />
    </DashboardRegion>
  );
}
