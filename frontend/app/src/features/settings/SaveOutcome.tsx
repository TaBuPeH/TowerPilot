import { useRef } from 'react';
import { settings as t } from '../../copy/settings.ts';
import type { SaveOutcomeVM } from '../../store/selectors/settingsModule.ts';
import { Button } from '../../ui/Button.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { Notice } from '../../ui/Notice.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { useDashboard } from '../dashboard/context.ts';

function wording(code: SaveOutcomeVM['code']) {
  switch (code) {
    case 'sending':
      return t.outcomes.sending;
    case 'saved':
      return t.outcomes.saved;
    case 'refused':
      return t.outcomes.refused;
    case 'context_changed':
      return t.outcomes.context_changed;
    case 'unknown':
      return t.outcomes.unknown;
    case 'applied':
      return t.outcomes.applied;
    case 'not_applied':
      return t.outcomes.not_applied;
    case 'conflict_applied':
      return t.outcomes.conflict_applied;
  }
}

/**
 * The newest save of one settings module or profile change: what it proved (saved,
 * refused with the server's reason and problems, unknown, or what a later read showed),
 * and Dismiss once it is settled. Nothing is ever sent again from here.
 */
export function SaveOutcome({
  outcome,
  onDismiss,
}: {
  readonly outcome: SaveOutcomeVM;
  readonly onDismiss: (id: string) => void;
}) {
  const { commands } = useDashboard();
  const box = useRef<HTMLDivElement>(null);
  const entry = wording(outcome.code);
  return (
    <div ref={box} className="tp-save-outcome" data-outcome={outcome.code} data-intent={outcome.id}>
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
          <span>
            {entry.short}
            {outcome.accepted ? (
              <span data-accepted="true">{t.outcomes.accepted.short}</span>
            ) : null}
          </span>
        }
        {...(entry.why === undefined ? {} : { why: entry.why })}
        {...(outcome.error === null &&
        outcome.problems.length === 0 &&
        outcome.currentContext === null
          ? {}
          : {
              server: (
                <>
                  {outcome.error === null ? null : (
                    <span data-refusal="true">
                      <ServerValue kind="message" value={outcome.error} />
                    </span>
                  )}
                  {outcome.currentContext === null ? null : (
                    <pre>
                      <ServerValue kind="detail" value={outcome.currentContext} />
                    </pre>
                  )}
                  {outcome.problems.length === 0 ? null : (
                    <Disclosure label={t.outcomes.problems(outcome.problems.length)}>
                      <ul className="tp-save-outcome__problems">
                        {outcome.problems.map((problem, index) => (
                          <li key={String(index)}>
                            <ServerValue kind="message" value={problem} />
                          </li>
                        ))}
                      </ul>
                    </Disclosure>
                  )}
                </>
              ),
            })}
        actions={
          <>
            {outcome.acceptOffered ? (
              <Button
                tier="quiet"
                label={t.outcomes.acceptCurrent.short}
                focusKey={`save-outcome:${outcome.id}:accept`}
                onClick={() => {
                  commands.acceptCurrent(outcome.id);
                  const legend = box.current?.closest('fieldset')?.querySelector('legend');
                  if (legend instanceof HTMLElement) legend.focus();
                }}
              />
            ) : null}
            {outcome.dismissOffered ? (
              <Button
                tier="quiet"
                label={t.outcomes.dismiss.short}
                focusKey={`save-outcome:${outcome.id}:dismiss`}
                onClick={() => {
                  const legend = box.current?.closest('fieldset')?.querySelector('legend');
                  onDismiss(outcome.id);
                  if (legend instanceof HTMLElement) legend.focus();
                }}
              />
            ) : null}
          </>
        }
      />
    </div>
  );
}
