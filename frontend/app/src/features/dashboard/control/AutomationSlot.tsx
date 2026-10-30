import { copy } from '../../../copy/catalogue.ts';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { Chip } from '../../../ui/Chip.tsx';
import { selectAutomationSlot } from '../../../store/selectors/automationSlot.ts';
import { SlotAction, SlotFacts, SlotMeta } from '../../../ui/Slot.tsx';
import { Button } from '../../../ui/Button.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { useDashboard, useView } from '../context.ts';
import { StopButton } from '../shared/StopButton.tsx';
import { AutomationFacts, AutomationMeta } from './automation/AutomationRow.tsx';
import { AutomationLine, AutomationWhy } from './automation/AutomationLine.tsx';
import './automation/automation.css';

const text = copy.dashboard.automation;

/**
 * The Automation slot body (spec 3.6.4, INV-3): owned by the Control region.
 * DOM order is the visual order: the Stops (on the label's line), then each
 * target's facts with its Why, then the Details footer, so Stop stays ahead
 * of every disclosure in tab order.
 */
export function AutomationSlot() {
  const view = useView(selectAutomationSlot);
  const { commands } = useDashboard();
  const retained = view.stops.filter((stop) => !view.rows.some((row) => row.stop.key === stop.key));
  return (
    <>
      <SlotAction>
        <ul className="tp-automation-list">
          {view.rows.map((row) => (
            <li key={row.key}>
              <StopButton
                stop={row.stop}
                name={
                  row.label === null ? (
                    text.purposes[row.purpose].short
                  ) : (
                    <ServerValue kind="name" value={row.label} />
                  )
                }
                targetId={`automation-${row.key}${row.created === null && row.pid === null ? '' : ` automation-identity-${row.key}`}`}
              />
              {row.stop.reason === 'waiting_for_fresh_status' ? (
                <Button
                  tier="quiet"
                  label={text.retry.short}
                  onClick={() => {
                    commands.checkStatus();
                  }}
                />
              ) : null}
              {row.scheduledStop !== true ? null : row.clearFlag === null ? (
                <p>{text.cancelUnavailable.short}</p>
              ) : (
                <Button
                  tier="secondary"
                  label={text.cancelScheduled.short}
                  onClick={() => {
                    if (row.clearFlag !== null) void commands.clearFlag(row.clearFlag);
                  }}
                />
              )}
              {row.stop.shown ? (
                <p className="tp-automation-note">{text.endsAutomation.short}</p>
              ) : (
                <p>{text.stopThere.short}</p>
              )}
            </li>
          ))}
          {retained.map((stop) => (
            <li key={stop.key}>
              <Chip state="unknown" />
              <p>{text.retained.short}</p>
              <StopButton stop={stop} />
              {stop.reason === 'waiting_for_fresh_status' ? (
                <Button
                  tier="quiet"
                  label={text.retry.short}
                  onClick={() => {
                    commands.checkStatus();
                  }}
                />
              ) : null}
              <p>{text.endsAutomation.short}</p>
            </li>
          ))}
        </ul>
      </SlotAction>
      <SlotFacts>
        {view.summary === null ? null : (
          <div className="tp-automation-state-line">
            <AutomationLine seen={view.summary} />
            <AutomationWhy seen={view.summary} />
          </div>
        )}
        {view.rows.length === 0 ? null : (
          <ul className="tp-automation-list">
            {view.rows.map((row) => (
              <li key={row.key}>
                <AutomationFacts row={row} />
              </li>
            ))}
          </ul>
        )}
        {view.setup === null ? null : (
          <Facts>
            <Fact
              label={text.setup.short}
              value={
                view.setup.step === null ? (
                  text.setupStepUnknown.short
                ) : (
                  <ServerValue kind="detail" value={view.setup.step} />
                )
              }
            />
          </Facts>
        )}
      </SlotFacts>
      {view.rows.length === 0 ? null : (
        <SlotMeta>
          <ul className="tp-automation-list">
            {view.rows.map((row) => (
              <li key={row.key}>
                <AutomationMeta row={row} />
              </li>
            ))}
          </ul>
        </SlotMeta>
      )}
    </>
  );
}
