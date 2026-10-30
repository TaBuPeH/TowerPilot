import { useId, useLayoutEffect, useRef } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { selectInputSlot } from '../../../store/selectors/inputSlot.ts';
import { selectOutcomes } from '../../../store/selectors/outcomes.ts';
import { Why } from '../../../ui/Why.tsx';
import { useView } from '../context.ts';
import { OutcomeReceipt } from '../outcomes/OutcomeReceipt.tsx';
import { outcomeEntry } from '../outcomes/presentation.ts';
import { InputFacts, InputOffButton, InputOffReason, InputWhy } from '../shared/InputControl.tsx';

/** Non-dashboard revocation with the same permission, observation and request evidence. */
export function HeaderInputControl() {
  const vm = useView(selectInputSlot);
  const outcomes = useView(selectOutcomes);
  const own = useId();
  const reasonId = `${own}-reason`;
  const group = useRef<HTMLDivElement>(null);
  const focused = useRef<HTMLElement | null>(null);
  // Receipts can disappear independently of the revocation button's offering.
  useLayoutEffect(() => {
    if (focused.current !== null && !focused.current.isConnected) {
      focused.current = null;
      group.current?.focus();
    }
  });
  // Settled state is represented above by InputFacts. Keep every pending or
  // uncertain input receipt visible, including older and unbound requests.
  const lines = outcomes.lines.filter(
    (line) =>
      line.slot === 'input' &&
      (line.word !== null || line.phase === 'sending' || line.phase === 'unconfirmed'),
  );
  return (
    <div
      ref={group}
      className="tp-header-input"
      role="group"
      aria-label={copy.dashboard.shell.slots.input.short}
      tabIndex={-1}
      onFocusCapture={(event) => {
        focused.current = event.target;
      }}
      onBlurCapture={(event) => {
        if (
          event.target.isConnected &&
          (!(event.relatedTarget instanceof Node) ||
            !event.currentTarget.contains(event.relatedTarget))
        ) {
          focused.current = null;
        }
      }}
    >
      <span className="tp-header-input__label">{copy.dashboard.shell.slots.input.short}</span>
      <div className="tp-header-input__status">
        <InputFacts vm={vm} />
      </div>
      <div className="tp-header-input__action">
        <InputOffButton vm={vm} focusKey="topbar:input:turn-off" reasonId={reasonId} />
        <InputOffReason vm={vm} reasonId={reasonId} />
        <InputWhy vm={vm} />
      </div>
      {lines.length === 0 ? null : (
        <ul className="tp-header-input__outcomes" aria-label={copy.dashboard.outcomes.title.short}>
          {lines.map((line) => (
            <li key={line.id} data-outcome-id={line.id}>
              <OutcomeReceipt
                line={line}
                targetId={`${own}-${line.id}-target`}
                actions={<Why why={outcomeEntry(line).why ?? copy.dashboard.outcomes.why.short} />}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
