import { copy } from '../../../../copy/catalogue.ts';
import type { AutomationLine as AutomationLineCode } from '../../../../core/automation.ts';
import type { CopyEntry } from '../../../../copy/text.ts';
import type { ChipVM } from '../../../../store/viewModels.ts';
import { Chip } from '../../../../ui/Chip.tsx';
import { WhyTip } from '../../shared/WhyTip.tsx';

const text = copy.dashboard.automation;
const lines: Readonly<Record<AutomationLineCode, CopyEntry>> = text.lines;
const lookup: Readonly<Record<string, CopyEntry>> = lines;

/** Words for the core-owned A1..A13 and AS1..AS5 decisions. */
export function AutomationLine({ seen }: { readonly seen: ChipVM }) {
  const line = lookup[seen.line] ?? text.lines.cant_tell_whats_running;
  const age = seen.params['ageSec'];
  const shown =
    typeof age !== 'number'
      ? line.short
      : seen.line === 'last_read'
        ? text.lastRead(age)
        : seen.line === 'last_known_idle'
          ? text.lastIdle(age)
          : seen.line === 'last_listed'
            ? text.lastListed(age)
            : line.short;
  return (
    <div className="tp-automation-state">
      {seen.word === null ? null : <Chip state={seen.word} />}
      {seen.line === 'started' ||
      seen.line === 'start_time_not_recorded' ||
      seen.line === 'operation_battle' ||
      seen.line === 'run_battle' ? null : (
        <p>{shown}</p>
      )}
    </div>
  );
}

/** The background of the line, a help icon after it; Stop stays ahead of it in tab order. */
export function AutomationWhy({ seen }: { readonly seen: ChipVM }) {
  const line = lookup[seen.line] ?? text.lines.cant_tell_whats_running;
  return <WhyTip subject={line.short} why={line.why} />;
}
