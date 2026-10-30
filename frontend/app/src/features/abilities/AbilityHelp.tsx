import { abilities as t } from '../../copy/abilities.ts';
import type { CopyEntry, CopyText } from '../../copy/text.ts';
import { HelpTip } from '../../ui/HelpTip.tsx';

/**
 * Background for a field group or heading: each entry's line, then its "why".
 * Explanation only; a current value, a consequence, an error or a readiness
 * state stays visible in the page.
 */
export function AbilityHelp({
  subject,
  entries,
}: {
  readonly subject: CopyText;
  readonly entries: readonly CopyEntry[];
}) {
  return (
    <HelpTip label={subject}>
      {entries.map((entry) => (
        <p key={entry.short}>
          {entry.short}
          {entry.why === undefined ? null : (
            <>
              {t.space}
              {entry.why}
            </>
          )}
        </p>
      ))}
    </HelpTip>
  );
}
