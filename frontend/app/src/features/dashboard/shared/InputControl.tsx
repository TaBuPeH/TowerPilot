import { copy } from '../../../copy/catalogue.ts';
import type { CopyEntry, CopyText } from '../../../copy/text.ts';
import type { InputSlotVM } from '../../../store/selectors/inputSlot.ts';
import { Button } from '../../../ui/Button.tsx';
import { Pill } from '../../../ui/Pill.tsx';
import { useDashboard } from '../context.ts';
import { listParam, SeenLine } from '../control/seen.tsx';
import { WhyTip } from './WhyTip.tsx';

const text = copy.dashboard.control.input;
const shell = copy.dashboard.shell;
const lines: Readonly<Record<string, CopyEntry>> = text.lines;

/** The Permission value: the input setting as configured, never evidence. */
export function inputSettingEntry(vm: InputSlotVM): CopyEntry {
  if (!vm.setting.known) return text.setting.unknown;
  return vm.setting.allowed ? text.setting.allowed : text.setting.off;
}

/** The Observed line's words, and the failed turn-off steps after the first. */
export function inputSeenWords(vm: InputSlotVM): {
  readonly line: CopyText;
  readonly moreSteps: readonly string[];
} {
  const steps = listParam(vm.seen, 'steps');
  const collectors = listParam(vm.seen, 'collectors');
  const line =
    vm.seen.line === 'turn_off_not_confirmed_for'
      ? text.notConfirmedFor(collectors.length)
      : vm.seen.line === 'turn_off_step_failed'
        ? text.stepFailed(steps[0] ?? 'unknown_step')
        : (lines[vm.seen.line] ?? text.lines.cant_tell_whats_running).short;
  return { line, moreSteps: steps.slice(1) };
}

/** Permission and observed evidence stay distinct in every input-control placement. */
export function InputFacts({ vm }: { readonly vm: InputSlotVM }) {
  const seen = inputSeenWords(vm);
  return (
    <>
      <Pill prefix={text.permission.short} value={inputSettingEntry(vm).short} />
      <SeenLine prefix={text.observed.short} chip={vm.seen} line={seen.line} />
      {seen.moreSteps.map((step) => (
        <span key={step} className="tp-seen__extra">
          {text.stepFailed(step)}
        </span>
      ))}
    </>
  );
}

/** The selector owns offering and busy state; each click remains a fresh revocation. */
export function InputOffButton({
  vm,
  focusKey,
  reasonId,
}: {
  readonly vm: InputSlotVM;
  readonly focusKey: string;
  readonly reasonId: string;
}) {
  const { commands } = useDashboard();
  return vm.turnOff.shown ? (
    <Button
      tier="halt"
      label={shell.revocations.turnInputOff.short}
      focusKey={focusKey}
      busy={vm.turnOff.sending}
      {...(vm.offReason === null ? {} : { describedBy: reasonId })}
      onClick={() => {
        void commands.turnInputOff();
      }}
    />
  ) : null;
}

export function InputOffReason({
  vm,
  reasonId,
}: {
  readonly vm: InputSlotVM;
  readonly reasonId: string;
}) {
  return vm.turnOff.shown && vm.offReason !== null ? (
    <span id={reasonId} className="tp-reason">
      {text.offReasons[vm.offReason].short}
    </span>
  ) : null;
}

export function InputWhy({ vm }: { readonly vm: InputSlotVM }) {
  return <WhyTip subject={inputSeenWords(vm).line} why={lines[vm.seen.line]?.why} />;
}
