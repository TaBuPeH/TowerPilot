import { copy } from '../../../copy/catalogue.ts';
import type { CopyEntry } from '../../../copy/text.ts';
import { selectFallback } from '../../../store/selectors/fallback.ts';
import { Button } from '../../../ui/Button.tsx';
import { Notice } from '../../../ui/Notice.tsx';
import { useDashboard, useView } from '../context.ts';
import { StopButton } from './StopButton.tsx';

const text = copy.dashboard.shell;

export type FallbackPart = keyof typeof text.fallback;

export interface RevocationFallbackProps {
  /** Which part failed: its "Can't show ..." line. */
  readonly part: FallbackPart;
  /** The Automation slot and the Control card keep the known Stops too (X4, X6, S-092). */
  readonly withStops?: boolean;
}

/**
 * What a part that failed to draw shows instead (spec 3.5, S-090 to S-094,
 * X4 to X6): one Unknown line naming the part, then Pause harvesting and Turn
 * input off, always rendered and enabled (a fallback does not evaluate D-8),
 * and, where asked, the Stop per known operation. Never Start or Turn on. It
 * reads only the minimal fallback records (store/selectors/fallback.ts), and
 * updates in place, so focus on its buttons survives every poll (S-094).
 */
export function RevocationFallback({ part, withStops = false }: RevocationFallbackProps) {
  const { commands } = useDashboard();
  const view = useView(selectFallback);
  const line: CopyEntry = text.fallback[part];
  return (
    <div className="tp-fallback" data-fallback={part}>
      <Notice
        state="unknown"
        line={line.short}
        {...(line.why === undefined ? {} : { why: line.why })}
        actions={
          <>
            <Button
              tier="halt"
              label={text.revocations.pause.short}
              focusKey={`fallback:${part}:pause`}
              busy={view.pause.sending}
              onClick={() => {
                void commands.pause();
              }}
            />
            <Button
              tier="halt"
              label={text.revocations.turnInputOff.short}
              focusKey={`fallback:${part}:input-off`}
              busy={view.turnInputOff.sending}
              onClick={() => {
                void commands.turnInputOff();
              }}
            />
            {withStops && view.stops.some((stop) => stop.shown) ? (
              <div className="tp-fallback__automation">
                <p>{copy.dashboard.automation.endsAutomation.short}</p>
                {view.stops.map((stop) => (
                  <StopButton key={stop.key} stop={stop} />
                ))}
              </div>
            ) : null}
          </>
        }
      />
    </div>
  );
}
