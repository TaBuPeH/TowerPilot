import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { lastRead } from '../../../copy/dashboard/shell.ts';
import type { ServerText } from '../../../copy/server.ts';
import type { CopyEntry, CopyNode } from '../../../copy/text.ts';
import type { HandoffCommand } from '../../../store/commands.ts';
import {
  selectNeedsYou,
  type CheckedReadVM,
  type NeedItemVM,
} from '../../../store/selectors/needsYou.ts';
import { Button } from '../../../ui/Button.tsx';
import { Chip } from '../../../ui/Chip.tsx';
import { ConfirmDialog } from '../../../ui/ConfirmDialog.tsx';
import { Disclosure } from '../../../ui/Disclosure.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { RegionBody, RegionCount, RegionEnd } from '../../../ui/Region.tsx';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { useDashboard, useEpochKey, useView } from '../context.ts';
import { DashboardRegion } from '../shared/DashboardRegion.tsx';
import './needsYou.css';

const words = copy.dashboard.needsYou;
const setupLinks = {
  tools: '#emulators',
  profile: '#profiles',
  connection: '#emulators',
  screens: '#recognition',
} as const;

function lineFor(item: NeedItemVM): CopyEntry {
  switch (item.code) {
    case 'launch_guard':
      return words.setupBattle;
    case 'finish_setup':
      return item.setupStep === null ? words.finish.screens : words.finish[item.setupStep];
    case 'other_runs_images':
      return { short: words.images(item.imageCount ?? 0), why: words.imagesWhy.short };
    case 'status_need':
      return item.chip?.line === 'process_discovery_unconfirmed'
        ? words.lines.process_discovery_unconfirmed
        : words.lines.no_live_status;
    case 'setup_outcome': {
      switch (item.chip?.line) {
        case 'setup_status_unavailable':
          return words.lines.setup_status_unavailable;
        case 'equipment_not_restored':
          return words.lines.equipment_not_restored;
        case 'setup_failed':
          return { short: words.dated('setup_failed', item.updatedAt) };
        case 'setup_partial':
          return { short: words.dated('setup_partial', item.updatedAt) };
        default:
          return { short: words.dated('setup_tracking_closed', item.updatedAt) };
      }
    }
  }
}

function CheckedRead({ read }: { readonly read: CheckedReadVM }) {
  return <li>{words.checked(read.source, read.outcome, read.ageSec)}</li>;
}

/** A new instance for every target/revision: consent cannot survive changed evidence. */
function CollectionConfirmation({
  command,
  operationId,
  battleId,
}: {
  readonly command: HandoffCommand;
  readonly operationId: ServerText<'id'> | null;
  readonly battleId: ServerText<'id'> | null;
}) {
  const { commands } = useDashboard();
  const [open, setOpen] = useState(false);
  const confirming = useRef(false);
  const cancel = useCallback(() => {
    setOpen(false);
  }, []);
  return (
    <>
      <Button
        tier="secondary"
        label={words.confirm.open.short}
        focusKey="needsYou-confirm"
        onClick={() => {
          confirming.current = false;
          setOpen(true);
        }}
      />
      <ConfirmDialog
        open={open}
        title={words.confirm.title.short}
        body={
          <>
            <p>{words.confirm.body.short}</p>
            {operationId === null ? null : (
              <p>
                <ServerValue value={operationId} kind="id" label={words.confirm.operation.short} />
              </p>
            )}
            {battleId === null ? null : (
              <p>
                <ServerValue value={battleId} kind="id" label={words.confirm.battle.short} />
              </p>
            )}
          </>
        }
        confirmLabel={words.confirm.action.short}
        cancelLabel={copy.dashboard.shell.cancel.short}
        tier="primary"
        onCancel={cancel}
        onConfirm={() => {
          if (!open || confirming.current) return;
          confirming.current = true;
          setOpen(false);
          void commands.handoff(command);
        }}
      />
    </>
  );
}

function OperationEvents({ operationId }: { readonly operationId: ServerText<'id'> | null }) {
  return operationId === null ? null : (
    <a href={`#runtime?operation_id=${encodeURIComponent(operationId)}`}>{words.events.short}</a>
  );
}

type Impact = NonNullable<NeedItemVM['impact']>;

/** What the guards mean for Start and collection, as two facts. */
function ImpactFacts({ impact }: { readonly impact: Impact }) {
  return (
    <Facts className="tp-need__impact">
      <Fact label={words.impact.start.short} value={words.start[impact.start].short} />
      <Fact
        label={words.impact.collection.short}
        value={words.collection[impact.collection].short}
      />
    </Facts>
  );
}

function sameImpact(a: Impact | null, b: Impact | null): boolean {
  return a !== null && b !== null && a.start === b.start && a.collection === b.collection;
}

/**
 * One item's Details: why it needs you, what was read, the reports, and its
 * operation's events. Current guard impacts remain visible beside the issue.
 */
function ItemDetails({ item, line }: { readonly item: NeedItemVM; readonly line: CopyEntry }) {
  return (
    <Disclosure label={words.details.short} focusKey={`needsYou-${item.code}-why`}>
      <p>
        {line.why ??
          (item.code === 'launch_guard'
            ? words.guardWhy.short
            : item.code === 'finish_setup'
              ? words.finishWhy.short
              : words.setupWhy.short)}
      </p>
      <p>{item.lastReadAgeSec === null ? words.noRead.short : lastRead(item.lastReadAgeSec)}</p>
      <ul>
        {item.sources.map((read) => (
          <CheckedRead key={read.source} read={read} />
        ))}
      </ul>
      {item.operationId === null ? null : (
        <p>
          <ServerValue value={item.operationId} kind="id" label={words.operation.short} />
        </p>
      )}
      {item.error === null ? null : (
        <p>
          <ServerValue value={item.error} kind="message" label={words.error.short} />
        </p>
      )}
      {item.setupBattleStatus === null ? null : (
        <p>
          <ServerValue
            value={item.setupBattleStatus}
            kind="detail"
            label={words.setupStatus.short}
          />
        </p>
      )}
      {item.setupBattleReason === null ? null : (
        <p>
          <ServerValue
            value={item.setupBattleReason}
            kind="message"
            label={words.setupReason.short}
          />
        </p>
      )}
      {item.finalScreenName === null ? null : (
        <p>
          <ServerValue value={item.finalScreenName} kind="name" label={words.screen.short} />
        </p>
      )}
      {item.finalScreenVerified === null ? null : (
        <p>
          {item.finalScreenVerified ? words.screenVerified.short : words.screenUnverified.short}
        </p>
      )}
      {item.impact?.setupMessage == null ? null : (
        <p>
          <ServerValue
            value={item.impact.setupMessage}
            kind="message"
            label={words.setupGuard.short}
          />
        </p>
      )}
      {item.impact?.collectionMessage == null ? null : (
        <p>
          <ServerValue
            value={item.impact.collectionMessage}
            kind="message"
            label={words.collectionGuard.short}
          />
        </p>
      )}
      <OperationEvents operationId={item.operationId} />
    </Disclosure>
  );
}

/**
 * One issue on the group's grid: chip and headline, then its resolving actions
 * in the shared action column, then one quiet Details disclosure.
 */
function NeedItem({ item, shown }: { readonly item: NeedItemVM; readonly shown: Impact | null }) {
  const { commands } = useDashboard();
  const line = lineFor(item);
  const retry = item.code === 'status_need' || item.chip?.line === 'setup_status_unavailable';
  const step = item.setupStep;
  return (
    <li className="tp-need" data-need={item.code}>
      <div className="tp-need__line">
        <Chip state={item.chip?.word ?? (item.code === 'launch_guard' ? 'blocked' : 'unknown')} />
        <span>{line.short}</span>
      </div>
      <div className="tp-need__actions">
        {item.handoffCommand === null ? null : (
          <CollectionConfirmation
            key={JSON.stringify(item.handoffCommand)}
            command={item.handoffCommand}
            operationId={item.operationId}
            battleId={item.handoffBattleId}
          />
        )}
        {retry ? (
          <Button
            tier="secondary"
            label={item.code === 'status_need' ? words.retryStatus.short : words.retry.short}
            focusKey={`needsYou-${item.code}-retry`}
            onClick={() => {
              // F-081 retries the status read, F-082 the setup progress read; both are reads only.
              if (item.code === 'status_need') commands.checkStatus();
              else commands.checkSetupStatus();
            }}
          />
        ) : null}
        {step === null ? null : (
          <a
            className="tp-need__fix tp-button"
            data-tier="secondary"
            href={setupLinks[step]}
            data-focus-key={`needsYou-${item.code}-setup`}
          >
            {words.finishActions[step].short}
          </a>
        )}
        {item.code === 'other_runs_images' ? (
          <a className="tp-need__fix tp-button" data-tier="secondary" href="#recognition">
            {words.learn.short}
          </a>
        ) : null}
        {item.code === 'launch_guard' || item.code === 'setup_outcome' ? (
          <a className="tp-need__fix tp-button" data-tier="secondary" href="#recognition">
            {words.review.short}
          </a>
        ) : null}
      </div>
      <ItemDetails item={item} line={line} />
      {item.impact === null || sameImpact(item.impact, shown) ? null : (
        <ImpactFacts impact={item.impact} />
      )}
    </li>
  );
}

/** Keep focus in the region when a later read removes the focused issue (spec 8.11). */
function NeedList({
  visible,
  children,
}: {
  readonly visible: readonly NeedItemVM[];
  readonly children: CopyNode;
}) {
  const list = useRef<HTMLUListElement>(null);
  const focused = useRef<{
    readonly code: string;
    readonly order: readonly string[];
    readonly element: HTMLElement;
    readonly key: string | null;
  } | null>(null);
  useLayoutEffect(() => {
    const was = focused.current;
    if (was === null || was.element.isConnected) return;
    const rows = Array.from(list.current?.children ?? []);
    const sameRow = rows.find((element) => element.getAttribute('data-need') === was.code);
    const replacement = Array.from(
      sameRow?.querySelectorAll<HTMLElement>('[data-focus-key]') ?? [],
    ).find((element) => element.getAttribute('data-focus-key') === was.key);
    const sameRowAction = sameRow?.querySelector<HTMLElement>('button, a[href], summary');
    const index = was.order.indexOf(was.code);
    const candidates = [...was.order.slice(index + 1), ...was.order.slice(0, index).reverse()];
    const next = candidates.find((code) => visible.some((item) => item.code === code));
    const row = rows.find((element) => element.getAttribute('data-need') === next);
    const action = row?.querySelector<HTMLElement>('button, a[href], summary');
    focused.current = null;
    (replacement ?? sameRowAction ?? action ?? document.getElementById('needsYou-title'))?.focus();
  }, [visible]);
  return (
    <ul
      ref={list}
      className="tp-needs"
      hidden={visible.length === 0}
      onFocusCapture={(event) => {
        const code = event.target.closest('[data-need]')?.getAttribute('data-need');
        if (code != null)
          focused.current = {
            code,
            order: visible.map((item) => item.code),
            element: event.target,
            key: event.target.getAttribute('data-focus-key'),
          };
      }}
      onBlurCapture={(event) => {
        if (
          event.relatedTarget instanceof Node &&
          !event.currentTarget.contains(event.relatedTarget)
        ) {
          focused.current = null;
        }
      }}
    >
      {children}
    </ul>
  );
}

function NeedsYouContent() {
  const vm = useView(selectNeedsYou);
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? vm.items : vm.visible;
  // Share an impact only when every issue that reports one agrees.
  const firstImpact = vm.items.find((item) => item.impact !== null)?.impact ?? null;
  const shown = vm.items.every(
    (item) => item.impact === null || sameImpact(item.impact, firstImpact),
  )
    ? firstImpact
    : null;
  return (
    <>
      <span className="tp-need__count">
        <span className="tp-visually-hidden">{words.countOpen.short}</span>
        <RegionCount value={vm.count} />
        <span className="tp-visually-hidden">{words.countClose.short}</span>
      </span>
      <RegionEnd>
        <a href="#runtime">{words.runtime.short}</a>
      </RegionEnd>
      <RegionBody>
        {vm.count === 0 ? (
          <div className="tp-need__empty">
            <p>{words.empty.short}</p>
            <Disclosure label={words.checkDetails.short} focusKey="needsYou-checked">
              <ul>
                {vm.checked
                  .filter((read) => read.outcome === 'answered')
                  .map((read) => (
                    <CheckedRead key={read.source} read={read} />
                  ))}
              </ul>
            </Disclosure>
            <ul className="tp-need__unavailable">
              {vm.checked
                .filter((read) => read.outcome !== 'answered')
                .map((read) => (
                  <CheckedRead key={read.source} read={read} />
                ))}
            </ul>
          </div>
        ) : null}
        {vm.count === 0 || shown === null ? null : <ImpactFacts impact={shown} />}
        <NeedList visible={visible}>
          {visible.map((item) => (
            <NeedItem key={item.code} item={item} shown={shown} />
          ))}
        </NeedList>
        {!expanded && vm.showAll ? (
          <Button
            tier="quiet"
            label={words.showAll(vm.count)}
            focusKey="needsYou-show-all"
            onClick={() => {
              setExpanded(true);
            }}
          />
        ) : null}
      </RegionBody>
    </>
  );
}

/** Needs you: source-backed issues and person-confirmed collection permission. */
export function NeedsYou() {
  return (
    <DashboardRegion id="needsYou">
      <NeedsYouContent key={useEpochKey()} />
    </DashboardRegion>
  );
}
