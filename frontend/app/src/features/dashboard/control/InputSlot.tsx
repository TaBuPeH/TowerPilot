import { useId } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { selectInputSlot } from '../../../store/selectors/inputSlot.ts';
import { Button } from '../../../ui/Button.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { SlotAction, SlotFacts } from '../../../ui/Slot.tsx';
import { useDashboard, useView } from '../context.ts';
import {
  InputOffButton,
  InputOffReason,
  InputWhy,
  inputSeenWords,
  inputSettingEntry,
} from '../shared/InputControl.tsx';
import { SeenLine } from './seen.tsx';

const text = copy.dashboard.control.input;

/**
 * Control / Game input (spec 3.6.3, INV-2; F-017, F-018, F-020, F-022): Turn
 * input off (halt tier, rendered and enabled whenever input may be on or
 * unknown; every click is a new request) on the label's line, then two facts:
 * Permission (the setting, a choice, never evidence) and Observed (the Seen
 * now chip, with its Why). Allow game input shows only while the Setting reads
 * Off and a fresh read shows no owner. DOM order is the visual order, so the
 * revocation leads.
 */
export function InputSlot() {
  const vm = useView(selectInputSlot);
  const { commands } = useDashboard();
  const own = useId();
  const permissionId = `${own}-permission`;
  const observedId = `${own}-observed`;
  const reasonId = `${own}-reason`;
  const usesFacts = vm.offReason === 'taps_allowed';
  const seen = inputSeenWords(vm);
  return (
    <>
      <SlotAction>
        <InputOffButton
          vm={vm}
          focusKey="input:turn-off"
          reasonId={usesFacts ? `${permissionId} ${observedId}` : reasonId}
        />
        {vm.allow.shown ? (
          vm.allow.enabled ? (
            <Button
              tier="secondary"
              label={text.allow.short}
              focusKey="input:allow"
              onClick={() => {
                void commands.allowInput();
              }}
            />
          ) : (
            <>
              <Button
                tier="secondary"
                label={text.allow.short}
                focusKey="input:allow"
                busy={vm.allow.sending}
                disabled
                reasonId={`${own}-allow`}
                onClick={() => undefined}
              />
              <span id={`${own}-allow`} className="tp-reason">
                {text.allowReasons.request_in_flight.short}
              </span>
            </>
          )
        ) : null}
      </SlotAction>
      <SlotFacts>
        <Facts layout="inline">
          <Fact
            label={text.facts.permission.short}
            value={
              <span id={permissionId} data-fact="permission">
                {inputSettingEntry(vm).short}
              </span>
            }
          />
          <Fact
            label={text.facts.observed.short}
            value={
              <div className="tp-control__value">
                <div id={observedId}>
                  <SeenLine chip={vm.seen} line={seen.line} />
                  {seen.moreSteps.map((step) => (
                    <span key={step} className="tp-seen__extra">
                      {text.stepFailed(step)}
                    </span>
                  ))}
                </div>
                <InputWhy vm={vm} />
              </div>
            }
          />
        </Facts>
        {usesFacts ? null : <InputOffReason vm={vm} reasonId={reasonId} />}
      </SlotFacts>
    </>
  );
}
