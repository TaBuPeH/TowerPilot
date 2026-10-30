import { copy } from '../../../copy/catalogue.ts';
import { ageAgo } from '../../../copy/dashboard/shell.ts';
import type { ServerText } from '../../../copy/server.ts';
import type { OutcomeLineVM } from '../../../store/selectors/outcomes.ts';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { Recovery } from './Recovery.tsx';

const t = copy.dashboard.outcomes;

function Ids({
  values,
  stopping = false,
}: {
  readonly values: readonly ServerText<'id'>[];
  readonly stopping?: boolean;
}) {
  return values.map((value, i) => (
    <p key={i}>
      <span>{stopping ? t.stopping.short : t.stopped.short}</span>
      <ServerValue kind="id" value={value} />
    </p>
  ));
}

/** Renders every part of the classified receipt without deriving current state. */
export function OutcomeDetail({ line }: { readonly line: OutcomeLineVM }) {
  const d = line.detail;
  switch (d.kind) {
    case 'stop':
      return (
        <>
          {d.effects === null ? null : (
            <>
              {d.effects.paused ? <p>{t.effects.paused.short}</p> : null}
              {d.effects.requested ? <p>{t.effects.requested.short}</p> : null}
              {d.effects.signalled.map((pid) => (
                <p key={pid}>
                  <span>{t.effects.signalled.short}</span>
                  <span>{pid}</span>
                </p>
              ))}
              {d.effects.released ? <p>{t.effects.released.short}</p> : null}
              {d.effects.family === 'stopped' ? <p>{t.effects.stoppedFamily.short}</p> : null}
              {d.effects.family === 'stopping' ? <p>{t.effects.stoppingFamily.short}</p> : null}
              {d.effects.family === 'unverified' ? <p>{t.effects.unverifiedFamily.short}</p> : null}
              {d.effects.family === 'gone' ? <p>{t.effects.goneFamily.short}</p> : null}
            </>
          )}
          {d.holders.map((h, i) => (
            <p key={i}>
              <span>{t.holder.short}</span>
              <span>{h.pid}</span>
              {h.role === null ? null : <ServerValue kind="id" value={h.role} />}
            </p>
          ))}
          {d.class === 'partial' && d.operation !== null ? (
            // F4-04: what the server said the operation was doing when it answered, dated.
            <p data-reply-operation-state={d.operation.status}>
              {t.operationAtReply(
                line.answeredAgeSec === null ? null : ageAgo(line.answeredAgeSec),
                d.operation.status,
              )}
            </p>
          ) : null}
          {d.class === 'partial' && d.conflict !== null ? (
            <ServerValue kind="message" value={d.conflict} />
          ) : null}
          {d.operation?.errorMessage == null ? null : (
            <ServerValue kind="message" value={d.operation.errorMessage} />
          )}
        </>
      );
    case 'input':
      return (
        <>
          <Ids values={d.stopped} />
          <Ids values={d.stopping} stopping />
          {d.class === 'not_enforced' && d.owners.length === 0 ? (
            <p>{t.input.unidentified.short}</p>
          ) : null}
          {d.unfenced.map((owner, i) => (
            <div key={i}>
              {owner.operation === null ? null : <ServerValue kind="id" value={owner.operation} />}
              {owner.pid === null ? null : (
                <p>
                  <span>{t.pid.short}</span>
                  <span>{owner.pid}</span>
                </p>
              )}
              <Recovery recovery={owner.recovery} />
            </div>
          ))}
          {d.failedSteps.map((step, i) => (
            <p key={i}>
              <span>{t.failedStep.short}</span>
              {step.step === null ? null : (
                <span>{copy.dashboard.outcomes.failedStepName(step.step)}</span>
              )}
              {step.message === null ? null : <ServerValue kind="message" value={step.message} />}
            </p>
          ))}
          {d.settingSaved === false ? <p>{t.settingNotSaved.short}</p> : null}
          {d.harvestPaused === null ? null : (
            <p>
              {line.harvestPausedCurrent === false || !line.current
                ? t.harvestHistory.short
                : t.harvestPaused.short}
            </p>
          )}
        </>
      );
    case 'harvest':
      return (
        <>
          <Ids values={d.stopped} />
          <Ids values={d.stopping} stopping />
          {d.stopErrors.map((error, i) => (
            <ServerValue key={i} kind="message" value={error} />
          ))}
        </>
      );
    case 'start':
      return (
        <>
          {d.operation === null ? null : <ServerValue kind="id" value={d.operation} />}
          <Ids values={d.stoppedAtHandover} />
          <Ids values={d.wasStoppingAtHandover} stopping />
        </>
      );
    case 'handoff':
      return d.handoff === null ? null : <ServerValue kind="id" value={d.handoff} />;
    default:
      return null;
  }
}
