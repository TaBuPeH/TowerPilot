import { useEffect, useId, useRef, useState } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import type { OwnerNoticeVM } from '../../../store/selectors/outcomes.ts';
import { Button } from '../../../ui/Button.tsx';
import { ConfirmDialog } from '../../../ui/ConfirmDialog.tsx';
import { Disclosure } from '../../../ui/Disclosure.tsx';
import { Notice } from '../../../ui/Notice.tsx';
import { useAnnounce } from '../../../ui/LiveRegion.tsx';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { useDashboard } from '../context.ts';
import { Recovery } from './Recovery.tsx';
import { Dismiss } from './Dismiss.tsx';

const t = copy.dashboard.outcomes;
const shell = copy.dashboard.shell;

/** A new request from an explicit retry; never the identical-key resend path. */
function useRetry(notice: OwnerNoticeVM) {
  const { commands } = useDashboard();
  return () => {
    const retry = notice.retry;
    switch (retry.kind) {
      case 'pause':
        void commands.pause();
        break;
      case 'input_off':
        void commands.turnInputOff();
        break;
      case 'input_on':
        void commands.allowInput();
        break;
      case 'reconnect':
        void commands.reconnect(retry.command);
        break;
      case 'start':
        void commands.start(retry.command);
        break;
      case 'start_gems':
        void commands.collect(retry.command);
        break;
      case 'turn_on':
        void commands.turnOn(retry.command);
        break;
      case 'stop':
      case 'kill':
        void commands.stop(retry.command);
        break;
      case 'clear_flag':
        void commands.clearFlag(retry.command);
        break;
      case 'handoff':
        void commands.handoff(retry.command);
        break;
      case 'run_now':
        void commands.runNow(retry.command);
        break;
      case 'none':
        break;
    }
  };
}

export function OwnerNoticeFallback({
  action,
  onDismiss,
}: {
  readonly action: OwnerNoticeVM['action'];
  readonly onDismiss: () => void;
}) {
  const { commands } = useDashboard();
  return (
    <Notice
      state="unknown"
      line={t.owner.failed(t.actions[action].short)}
      actions={
        <>
          <Button
            tier="halt"
            label={shell.revocations.pause.short}
            onClick={() => {
              void commands.pause();
            }}
          />
          <Button
            tier="halt"
            label={shell.revocations.turnInputOff.short}
            onClick={() => {
              void commands.turnInputOff();
            }}
          />
          <Dismiss onClick={onDismiss} />
        </>
      }
    />
  );
}

/** Only this small child reads the owner's fields, inside its own error boundary. */
export function OwnerNotice({
  notice,
  onDismiss,
}: {
  readonly notice: OwnerNoticeVM;
  readonly onDismiss: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const retry = useRetry(notice);
  const retryReason = useId();
  const announce = useAnnounce();
  const announced = useRef(false);
  useEffect(() => {
    if (announced.current) return;
    announced.current = true;
    announce(t.owner.refused(t.actions[notice.action].short));
  }, [announce, notice.action]);
  const unreadable = notice.noticeClass === 'owner_unreadable';
  const confirmRetry =
    notice.action === 'handoff' || notice.action === 'stop' || notice.action === 'kill';
  const owner = (
    <>
      {notice.purposeCode === 'gems' ? (
        <span>{t.owner.collector.short}</span>
      ) : notice.purposeCode === 'run' ? (
        <span>{t.owner.run.short}</span>
      ) : null}
      {notice.purpose === null ? null : <ServerValue kind="name" value={notice.purpose} />}
      {notice.operation === null ? null : <ServerValue kind="id" value={notice.operation} />}
      {notice.pid === null ? null : (
        <>
          <span>{t.pid.short}</span>
          <span>{notice.pid}</span>
        </>
      )}
      {notice.script === null ? null : <ServerValue kind="name" value={notice.script} />}
    </>
  );
  return (
    <Notice
      state={unreadable ? 'unknown' : notice.canRetry ? 'idle' : 'blocked'}
      lead={t.owner.refused(t.actions[notice.action].short)}
      line={
        unreadable ? (
          t.owner.unreadable.short
        ) : notice.canRetry ? (
          t.owner.free.short
        ) : (
          <>
            {owner}
            {notice.stopped?.kind === 'known' && notice.currentOwner !== null ? (
              <>
                <span>{t.owner.stopped.short}</span>
                {notice.currentOperation === null ? null : (
                  <ServerValue kind="id" value={notice.currentOperation} />
                )}
                {notice.currentPid === null ? null : (
                  <>
                    <span>{t.pid.short}</span>
                    <span>{notice.currentPid}</span>
                  </>
                )}
              </>
            ) : notice.starting ? (
              <span>{t.owner.starting.short}</span>
            ) : notice.noticeClass === 'lease_holder' ? (
              <>
                {notice.holders.length === 0 ? (
                  <span>{t.owner.unidentified.short}</span>
                ) : (
                  notice.holders.map((holder, i) => (
                    <span key={i}>
                      <span>{t.holder.short}</span>
                      <span>{holder.pid}</span>
                      {holder.role === null ? null : <ServerValue kind="id" value={holder.role} />}
                    </span>
                  ))
                )}
              </>
            ) : (
              <span>{t.owner.using.short}</span>
            )}
            {notice.status === null ? null : <ServerValue kind="id" value={notice.status} />}
            {notice.foreign ? <span>{t.owner.foreign.short}</span> : null}
          </>
        )
      }
      actions={
        <>
          {unreadable && notice.retry.kind === 'none' ? (
            <span>
              <Button
                tier="secondary"
                label={shell.tryAgain.short}
                disabled
                reasonId={retryReason}
                onClick={() => undefined}
              />
              <span id={retryReason}>{t.retryUnavailable.short}</span>
            </span>
          ) : unreadable ? (
            <Button
              tier="secondary"
              label={shell.tryAgain.short}
              onClick={() => {
                if (confirmRetry) setAsking(true);
                else retry();
              }}
            />
          ) : (
            <Recovery recovery={notice.recovery} />
          )}
          <Dismiss onClick={onDismiss} />
          <Disclosure label={copy.common.why.short}>
            {notice.error === null ? null : <ServerValue kind="message" value={notice.error} />}
          </Disclosure>
          <ConfirmDialog
            open={asking}
            tier={notice.action === 'handoff' ? 'primary' : 'halt'}
            title={
              notice.action === 'handoff' ? t.handoffTitle.short : shell.confirmStop.title.short
            }
            body={
              <p>
                {notice.action === 'handoff' ? t.handoffBody.short : shell.confirmStop.body.short}
              </p>
            }
            confirmLabel={shell.tryAgain.short}
            cancelLabel={shell.cancel.short}
            onConfirm={() => {
              setAsking(false);
              retry();
            }}
            onCancel={() => {
              setAsking(false);
            }}
          />
        </>
      }
    />
  );
}
