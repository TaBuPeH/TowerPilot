import { useEffect, useRef } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { ageAgo } from '../../../copy/dashboard/shell.ts';
import type { CopyNode } from '../../../copy/text.ts';
import type { OutcomeLineVM } from '../../../store/selectors/outcomes.ts';
import { Notice } from '../../../ui/Notice.tsx';
import { useAnnounce } from '../../../ui/LiveRegion.tsx';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { OutcomeDetail } from './OutcomeDetail.tsx';
import { outcomeEntry } from './presentation.ts';

const t = copy.dashboard.outcomes;

/** One receipt presentation preserves dates, uncertainty and context at every placement. */
export function OutcomeReceipt({
  line,
  targetId,
  actions,
}: {
  readonly line: OutcomeLineVM;
  readonly targetId: string;
  readonly actions?: CopyNode;
}) {
  const entry = outcomeEntry(line);
  const announce = useAnnounce();
  const announced = useRef(new Set<string>());
  const arrival = `${line.phase}:${line.outcome}:${line.settledByRead ?? ''}`;
  useEffect(() => {
    if (line.phase === 'sending' || announced.current.has(arrival)) return;
    announced.current.add(arrival);
    announce(entry.short);
  }, [announce, arrival, entry.short, line.phase]);
  const age = line.answeredAgeSec === null ? null : ageAgo(line.answeredAgeSec);
  const datedInput =
    line.detail.kind === 'input' && line.detail.class === 'input_stopped' && age !== null;
  const operation = line.detail.kind === 'stop' ? line.detail.operation : null;
  return (
    <Notice
      state={line.word}
      lead={
        <>
          <span>{t.leadPrefix(t.slots[line.slot].short, t.actions[line.kind].short)}</span>
          {line.detail.kind === 'stop' ? (
            operation?.purpose === 'gems' ? (
              <span>{t.owner.collector.short}</span>
            ) : operation?.label == null ? (
              <span>{t.automationTarget.short}</span>
            ) : (
              <ServerValue kind="name" value={operation.label} />
            )
          ) : null}
          <span>{t.leadEnd(age)}</span>
        </>
      }
      line={datedInput ? t.stoppedAt(age) : entry.short}
      server={
        <>
          {line.target === null ? null : (
            <ServerValue id={targetId} kind="id" value={line.target} />
          )}
          {line.error === null ? null : <ServerValue kind="message" value={line.error} />}
          {line.operationError == null ? null : (
            <ServerValue kind="message" value={line.operationError} />
          )}
          {line.earlierEnableUnresolved ? <span>{t.earlierEnable.short}</span> : null}
          <OutcomeDetail line={line} />
          {line.current ? null : <span>{t.history.short}</span>}
          {line.bound ? null : <span>{t.unbound.short}</span>}
        </>
      }
      actions={actions}
    />
  );
}
