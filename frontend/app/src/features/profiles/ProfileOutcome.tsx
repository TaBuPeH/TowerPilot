import { useRef } from 'react';
import { profiles as t } from '../../copy/profiles.ts';
import type { CopyEntry } from '../../copy/text.ts';
import type { ProfileOutcomeVM } from '../../store/selectors/profiles.ts';
import { Button } from '../../ui/Button.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { Notice } from '../../ui/Notice.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { useDashboard } from '../dashboard/context.ts';

function wording(outcome: ProfileOutcomeVM): CopyEntry {
  switch (outcome.code) {
    case 'sending':
      return t.outcomes.sending;
    case 'accepted':
      return t.outcomes.accepted;
    case 'done':
      return t.outcomes.done[outcome.kind];
    case 'refused':
      if (outcome.refusalCode === 'context_changed') return t.outcomes.contextChanged;
      return outcome.kind === 'account_create' ? t.outcomes.refusedCreate : t.outcomes.refused;
    case 'unknown':
      return t.outcomes.unknown;
    case 'applied':
      return outcome.unbind ? t.outcomes.unbound : t.outcomes.applied;
  }
}

/**
 * The newest Profiles write of one form: what the selector says it proved
 * (sent, done, refused with the server's reason, unknown, or what a later read
 * showed), the account or profile it named, a promotion's problems, and
 * Dismiss (it hides the line; nothing is ever sent again from here).
 */
export function ProfileOutcome({ outcome }: { readonly outcome: ProfileOutcomeVM }) {
  const { commands } = useDashboard();
  const box = useRef<HTMLDivElement>(null);
  const entry = wording(outcome);
  return (
    <div
      ref={box}
      className="tp-profile-outcome"
      data-outcome={outcome.code}
      data-kind={outcome.kind}
      data-intent={outcome.id}
    >
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
          <span className="tp-profile-outcome__line">
            <span>{entry.short}</span>
            {outcome.target === null ? null : <ServerValue kind="name" value={outcome.target} />}
            {outcome.maxTier === null ? null : <span>{t.outcomes.tierTo(outcome.maxTier)}</span>}
          </span>
        }
        {...(entry.why === undefined ? {} : { why: entry.why })}
        {...(outcome.error === null &&
        outcome.problems.length === 0 &&
        outcome.abilitiesVerified !== false
          ? {}
          : {
              server: (
                <>
                  {outcome.error === null ? null : (
                    <span data-refusal="true">
                      <ServerValue kind="message" value={outcome.error} />
                    </span>
                  )}
                  {outcome.problems.length === 0 ? null : (
                    <Disclosure
                      label={
                        outcome.code === 'refused'
                          ? t.files.problems(outcome.problems.length)
                          : t.outcomes.problems(outcome.problems.length)
                      }
                    >
                      <ul className="tp-profile-problems">
                        {outcome.problems.map((problem, index) => (
                          <li key={String(index)}>
                            <ServerValue kind="message" value={problem} />
                          </li>
                        ))}
                      </ul>
                    </Disclosure>
                  )}
                  {outcome.abilitiesVerified === false ? (
                    <span>{t.outcomes.abilitiesUnverified.short}</span>
                  ) : null}
                </>
              ),
            })}
        actions={
          outcome.dismissOffered ? (
            <Button
              tier="quiet"
              label={t.outcomes.dismiss.short}
              focusKey={`profile-outcome:${outcome.id}:dismiss`}
              onClick={() => {
                const region =
                  box.current?.closest<HTMLElement>('[data-region]')?.dataset['region'];
                commands.dismiss(outcome.id);
                if (region !== undefined) document.getElementById(`${region}-title`)?.focus();
              }}
            />
          ) : undefined
        }
      />
      {outcome.currentContext === null ? null : (
        <div data-current-context="true">
          <Facts>
            <Fact
              label={t.outcomes.contextLabels.instance.short}
              value={
                outcome.currentContext.instance === null ? (
                  t.outcomes.contextUnknown.short
                ) : (
                  <ServerValue kind="name" value={outcome.currentContext.instance} />
                )
              }
            />
            <Fact
              label={t.outcomes.contextLabels.account.short}
              value={
                outcome.currentContext.account === null ? (
                  t.outcomes.contextUnknown.short
                ) : (
                  <ServerValue kind="name" value={outcome.currentContext.account} />
                )
              }
            />
            <Fact
              label={t.outcomes.contextLabels.profile.short}
              value={
                outcome.currentContext.profile === null ? (
                  t.outcomes.contextUnknown.short
                ) : (
                  <ServerValue kind="name" value={outcome.currentContext.profile} />
                )
              }
            />
            <Fact
              label={t.outcomes.contextLabels.serial.short}
              value={
                outcome.currentContext.serial === null ? (
                  t.outcomes.contextUnknown.short
                ) : (
                  <ServerValue kind="name" value={outcome.currentContext.serial} />
                )
              }
            />
          </Facts>
        </div>
      )}
    </div>
  );
}
