import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { selectOutcomes, type OutcomeLineVM } from '../../../store/selectors/outcomes.ts';
import { Button } from '../../../ui/Button.tsx';
import { ConfirmDialog } from '../../../ui/ConfirmDialog.tsx';
import { Disclosure } from '../../../ui/Disclosure.tsx';
import { RegionBoundary } from '../../../ui/RegionBoundary.tsx';
import { useDashboard, useEpochKey, usePartError, useView } from '../context.ts';
import { OutcomeReceipt } from './OutcomeReceipt.tsx';
import { OwnerNotice, OwnerNoticeFallback } from './OwnerNotice.tsx';
import { outcomeEntry } from './presentation.ts';
import { Dismiss } from './Dismiss.tsx';
import './outcomes.css';

const t = copy.dashboard.outcomes;

function OutcomeLine({
  line,
  onDismiss,
}: {
  readonly line: OutcomeLineVM;
  readonly onDismiss: () => void;
}) {
  const { commands } = useDashboard();
  const [asking, setAsking] = useState(false);
  const [stopping, setStopping] = useState(false);
  const targetId = useId();
  const entry = outcomeEntry(line);
  return (
    <OutcomeReceipt
      line={line}
      targetId={targetId}
      actions={
        <>
          <Disclosure label={copy.common.why.short}>
            <p>{entry.why ?? t.why.short}</p>
          </Disclosure>
          {line.phase === 'sending' ? null : (
            <Button
              tier="secondary"
              label={t.check.short}
              onClick={() => {
                commands.checkStatus();
              }}
            />
          )}
          {line.resendOffered && line.bound ? (
            <Button
              tier="secondary"
              label={t.resend.short}
              onClick={() => {
                if (line.kind === 'handoff') setAsking(true);
                else void commands.resend(line.id);
              }}
            />
          ) : null}
          {line.stopAgain === null || line.settledByRead !== null ? null : (
            <Button
              tier="halt"
              label={t.stopAgain.short}
              targetId={targetId}
              onClick={() => {
                setStopping(true);
              }}
            />
          )}
          {line.detail.kind === 'stop' && line.detail.turnInputOff ? (
            <Button
              tier="halt"
              label={copy.dashboard.shell.revocations.turnInputOff.short}
              onClick={() => {
                void commands.turnInputOff();
              }}
            />
          ) : null}
          {line.kind === 'input_off' &&
          [
            'revocation_failed',
            'stopping_with_errors',
            'unknown',
            'no_answer',
            'not_enforced',
            'input_stopped_not_saved',
          ].includes(line.outcome) ? (
            <Button
              tier="halt"
              label={t.turnOffAgain.short}
              onClick={() => {
                void commands.turnInputOff();
              }}
            />
          ) : null}
          <Dismiss onClick={onDismiss} />
          <ConfirmDialog
            open={asking}
            tier="primary"
            title={t.handoffTitle.short}
            body={<p>{t.handoffBody.short}</p>}
            confirmLabel={t.resend.short}
            cancelLabel={copy.dashboard.shell.cancel.short}
            onConfirm={() => {
              setAsking(false);
              void commands.resend(line.id, true);
            }}
            onCancel={() => {
              setAsking(false);
            }}
          />
          <ConfirmDialog
            open={stopping}
            tier="halt"
            title={t.stopAgain.short}
            body={<p>{t.stopAgainBody.short}</p>}
            confirmLabel={t.stopAgain.short}
            cancelLabel={copy.dashboard.shell.cancel.short}
            onConfirm={() => {
              setStopping(false);
              if (line.stopAgain !== null) void commands.stop(line.stopAgain);
            }}
            onCancel={() => {
              setStopping(false);
            }}
          />
        </>
      }
    />
  );
}

/** The strip sits beneath the three slots; dismissal never changes their revocations. */
export function Outcomes() {
  const vm = useView(selectOutcomes);
  const { commands } = useDashboard();
  const epoch = useEpochKey();
  const onError = usePartError('owner-notice');
  const list = useRef<HTMLUListElement>(null);
  const focused = useRef<string | null>(null);
  const previous = useRef<readonly string[]>([]);
  const [dismissed, setDismissed] = useState(0);
  const notice = vm.ownerNotice?.action === 'run_now' ? null : vm.ownerNotice;
  const lines = useMemo(
    // Run now lines belong to This battle; the Runs page shows its own saves (slot `runs`).
    () =>
      vm.lines.filter(
        (line) =>
          line.kind !== 'run_now' &&
          line.slot !== 'runs' &&
          line.slot !== 'profiles' &&
          line.slot !== 'settings' &&
          line.slot !== 'emulators' &&
          line.slot !== 'recognition' &&
          line.id !== notice?.intentId,
      ),
    [vm.lines, notice?.intentId],
  );
  const ids = useMemo(
    () => [...lines.map((line) => line.id), ...(notice === null ? [] : [notice.intentId])],
    [lines, notice],
  );
  useLayoutEffect(() => {
    const was = focused.current;
    if (was !== null && !ids.includes(was)) {
      const at = previous.current.indexOf(was);
      const next =
        previous.current.slice(at + 1).find((id) => ids.includes(id)) ??
        [...previous.current.slice(0, at)].reverse().find((id) => ids.includes(id));
      const row = Array.from(list.current?.children ?? []).find(
        (el) => el.getAttribute('data-outcome-id') === next,
      );
      const action =
        row?.querySelector<HTMLElement>('button:not([aria-disabled="true"]):not(:disabled)') ??
        row?.querySelector<HTMLElement>('a[href], summary');
      focused.current = null;
      (action ?? document.getElementById('control-title'))?.focus();
    }
    previous.current = ids;
  }, [ids]);
  useEffect(() => {
    if (!dismissed) return undefined;
    const timer = window.setTimeout(() => {
      setDismissed(0);
    }, 8000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [dismissed]);
  const dismiss = (id: string) => {
    commands.dismiss(id);
    setDismissed((count) => count + 1);
  };
  return (
    <div className="tp-outcomes">
      <ul
        ref={list}
        className="tp-outcomes__list"
        aria-label={t.title.short}
        onFocusCapture={(event) => {
          focused.current =
            event.target.closest('[data-outcome-id]')?.getAttribute('data-outcome-id') ?? null;
        }}
        onBlurCapture={(event) => {
          if (
            event.relatedTarget instanceof Node &&
            !event.currentTarget.contains(event.relatedTarget)
          )
            focused.current = null;
        }}
      >
        {lines.map((line) => (
          <li key={line.id} data-outcome-id={line.id}>
            <OutcomeLine
              line={line}
              onDismiss={() => {
                dismiss(line.id);
              }}
            />
          </li>
        ))}
        {notice === null ? null : (
          <li key={notice.intentId} data-outcome-id={notice.intentId} data-owner-notice="true">
            <RegionBoundary
              resetKey={`${epoch}:${notice.intentId}`}
              onError={onError}
              fallback={
                <OwnerNoticeFallback
                  action={notice.action}
                  onDismiss={() => {
                    dismiss(notice.intentId);
                  }}
                />
              }
            >
              <OwnerNotice
                notice={notice}
                onDismiss={() => {
                  dismiss(notice.intentId);
                }}
              />
            </RegionBoundary>
          </li>
        )}
      </ul>
      {dismissed ? <p>{t.dismissed.short}</p> : null}
      {vm.otherContextUnresolved ? <p>{t.unbound.short}</p> : null}
      {vm.notices.map((notice, i) => (
        <p key={i}>{t.notSent[notice.kind].short}</p>
      ))}
    </div>
  );
}
