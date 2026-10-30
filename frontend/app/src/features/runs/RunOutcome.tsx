import { useRef } from 'react';
import { copy } from '../../copy/catalogue.ts';
import type { CopyEntry } from '../../copy/text.ts';
import type { RunOutcomeCode, RunOutcomeVM } from '../../store/selectors/runs.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow } from '../../ui/ActionRow.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { Notice } from '../../ui/Notice.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { useDashboard } from '../dashboard/context.ts';

const t = copy.runs.outcomes;

/** The words of each outcome code the selector gives (store/selectors/runs.ts). */
const WORDS: Readonly<Record<RunOutcomeCode, CopyEntry>> = {
  sending: t.sending,
  saved: t.saved,
  saved_partly: t.savedPartly,
  not_saved: t.notSaved,
  not_saved_changed: t.notSavedChanged,
  unknown_save: t.unknownSave,
  unconfirmed_save: t.unconfirmedSave,
  applied: t.applied,
  not_applied: t.notApplied,
  live_recorded: t.liveRecorded,
  earlier_recorded: t.earlierRecorded,
  live_not_recorded: t.liveNotRecorded,
  live_unknown: t.liveUnknown,
  live_unconfirmed: t.liveUnconfirmed,
  live_settled: t.liveSettled,
  live_applied: t.liveApplied,
  live_not_applied: t.liveNotApplied,
};

/**
 * The newest save of one editor (SR-4, SR-8): its words, the server's
 * message under Why, and only the actions the selector offers ("Send same
 * request again" for an unknown live change, Dismiss). Dismissing hides the
 * line; the record and its fence stay (store/selectors/runs.ts). A line of a
 * battle the page no longer shows names that battle and offers a read of it
 * (F6R-04); a refused resend is shown beside the unknown outcome, never in
 * its place (F6R-01).
 */
export function RunOutcome({ outcome }: { readonly outcome: RunOutcomeVM }) {
  const { commands } = useDashboard();
  const { line } = outcome;
  const entry = WORDS[outcome.code];
  const box = useRef<HTMLDivElement>(null);
  return (
    <div ref={box} className="tp-run-outcome" data-outcome={outcome.code} data-intent={line.id}>
      <Notice
        state={outcome.word}
        lead={
          <>
            {outcome.earlier === null ? (
              <span>{t.lead.short}</span>
            ) : (
              <span data-earlier-battle="true">
                <span>{t.earlier.short}</span>
                {outcome.earlier.label === null ? null : (
                  <ServerValue kind="name" value={outcome.earlier.label} />
                )}
              </span>
            )}
            {line.answeredAgeSec === null ? null : (
              <span>{t.answeredAgo(line.answeredAgeSec)}</span>
            )}
          </>
        }
        line={
          outcome.accepted ? (
            <span>
              <span>{entry.short}</span>
              <span data-accepted="true">{t.accepted.short}</span>
            </span>
          ) : (
            entry.short
          )
        }
        {...(entry.why === undefined ? {} : { why: entry.why })}
        {...(line.error === null && line.attemptRefused === null
          ? {}
          : {
              server: (
                <>
                  {line.error === null ? null : (
                    <Disclosure label={copy.common.why.short}>
                      <ServerValue kind="message" value={line.error} />
                    </Disclosure>
                  )}
                  {line.attemptRefused === null ? null : (
                    <span className="tp-run-outcome__attempt" data-attempt-refused="true">
                      <span>{t.attemptRefused.short}</span>
                      <Disclosure label={copy.common.why.short}>
                        <span>{t.attemptRefused.why}</span>
                        {line.attemptRefused.error === null ? null : (
                          <ServerValue kind="message" value={line.attemptRefused.error} />
                        )}
                      </Disclosure>
                    </span>
                  )}
                </>
              ),
            })}
        actions={
          <ActionRow>
            {outcome.resendOffered ? (
              <Button
                tier="secondary"
                label={t.resend.short}
                focusKey={`run-outcome:${line.id}:resend`}
                onClick={() => {
                  void commands.resend(line.id);
                }}
              />
            ) : null}
            {outcome.acceptOffered ? (
              <Button
                tier="quiet"
                label={t.acceptCurrent.short}
                focusKey={`run-outcome:${line.id}:accept`}
                onClick={() => {
                  commands.acceptCurrent(line.id);
                }}
              />
            ) : null}
            {outcome.earlier?.checkOffered === true ? (
              <Button
                tier="quiet"
                label={t.checkEarlier.short}
                focusKey={`run-outcome:${line.id}:check`}
                onClick={() => {
                  void commands.checkEarlierBattle(line.id);
                }}
              />
            ) : null}
            {outcome.dismissOffered ? (
              <Button
                tier="quiet"
                label={t.dismiss.short}
                focusKey={`run-outcome:${line.id}:dismiss`}
                onClick={() => {
                  // The line goes with its button: focus moves to the section heading.
                  const region =
                    box.current?.closest<HTMLElement>('[data-region]')?.dataset['region'];
                  commands.dismiss(line.id);
                  if (region !== undefined) document.getElementById(`${region}-title`)?.focus();
                }}
              />
            ) : null}
          </ActionRow>
        }
      />
    </div>
  );
}
