import { copy } from '../../copy/catalogue.ts';
import { settings } from '../../copy/settings.ts';
import { Chip } from '../../ui/Chip.tsx';

/** Summarize existing field problems without hiding their individual messages. */
export function ValidationSummary({ problems }: { readonly problems: number }) {
  return problems === 0 ? null : (
    <Chip
      state="blocked"
      label={copy.runs.needsAttention.short}
      age={settings.outcomes.problems(problems)}
    />
  );
}
