import { useId, useState } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import type { CopyEntry, CopyNode } from '../../../copy/text.ts';
import type { StopTargetVM } from '../../../store/selectors/automationSlot.ts';
import { Button } from '../../../ui/Button.tsx';
import { ConfirmDialog } from '../../../ui/ConfirmDialog.tsx';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { useDashboard } from '../context.ts';
import { WhyTip } from './WhyTip.tsx';

const text = copy.dashboard.shell;

export interface StopButtonProps {
  readonly stop: StopTargetVM;
  /** The target's visible name for the dialog title ("Stop {name}?", F-011): catalogue text or a ServerValue. */
  readonly name?: CopyNode;
  /** The id of the visible element naming the target; a fallback names it by its id or PID. */
  readonly targetId?: string;
}

/**
 * One Stop (INV-3): a cancel of the exact operation or a kill of the exact
 * process, after a confirmation every time (F-011, F-012, spec 8.7). A Stop
 * whose target is incomplete is shown disabled with its visible reason; a
 * click while one is in flight sends nothing new (the command layer).
 */
export function StopButton({ stop, targetId, name }: StopButtonProps) {
  const { commands } = useDashboard();
  const [asking, setAsking] = useState(false);
  const own = useId();
  const nameId = targetId ?? `${own}-target`;
  const reasonId = `${own}-reason`;
  if (!stop.shown) return null;
  const command = stop.command;
  const label = stop.kind === 'kill' ? text.stopProcess.short : text.revocations.stop.short;
  const reasons: Readonly<Record<string, CopyEntry>> = text.stopDisabled;
  const reason = stop.reason === null || stop.enabled ? null : (reasons[stop.reason] ?? null);
  return (
    <span className="tp-stop" data-focus-key={`stop:${stop.key}`}>
      {targetId === undefined ? (
        <span id={nameId} className="tp-stop__target">
          {stop.operation !== null ? (
            <ServerValue kind="id" value={stop.operation} label={text.targetOperation.short} />
          ) : stop.pid === null ? null : (
            <>
              <span>{text.targetPid.short}</span>
              <span>{stop.pid}</span>
            </>
          )}
        </span>
      ) : null}
      {stop.enabled && command !== null ? (
        <Button
          tier="halt"
          label={label}
          targetId={nameId}
          focusKey={`stop-button:${stop.key}`}
          busy={stop.sending}
          onClick={() => {
            setAsking(true);
          }}
        />
      ) : (
        <Button
          tier="halt"
          label={label}
          targetId={nameId}
          focusKey={`stop-button:${stop.key}`}
          disabled
          reasonId={reasonId}
          onClick={() => undefined}
        />
      )}
      {stop.enabled ? null : (
        <>
          <span id={reasonId} className="tp-reason">
            {reason === null ? null : reason.short}
          </span>
          {reason === null ? null : <WhyTip subject={reason.short} why={reason.why} />}
        </>
      )}
      {command === null ? null : (
        <ConfirmDialog
          open={asking}
          tier="halt"
          title={
            stop.kind === 'kill' ? (
              text.confirmKill.title.short
            ) : name === undefined || !asking ? (
              text.confirmStop.title.short
            ) : (
              <>
                <span>{text.confirmStop.lead.short}</span>
                <span className="tp-dialog__name">{name}</span>
                <span>{text.confirmStop.end.short}</span>
              </>
            )
          }
          body={
            <p>
              {stop.kind === 'kill' ? text.confirmKill.body.short : text.confirmStop.body.short}
            </p>
          }
          confirmLabel={
            stop.kind === 'kill' ? text.confirmKill.confirm.short : text.confirmStop.confirm.short
          }
          cancelLabel={text.cancel.short}
          onConfirm={() => {
            setAsking(false);
            void commands.stop(command);
          }}
          onCancel={() => {
            setAsking(false);
          }}
        />
      )}
    </span>
  );
}
