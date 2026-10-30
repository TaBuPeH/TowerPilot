import { copy } from '../../../../copy/catalogue.ts';
import type { OperationRowVM } from '../../../../store/selectors/automationSlot.ts';
import { Chip } from '../../../../ui/Chip.tsx';
import { Disclosure } from '../../../../ui/Disclosure.tsx';
import { Fact, Facts } from '../../../../ui/Facts.tsx';
import { ServerValue } from '../../../../ui/ServerValue.tsx';
import { AutomationLine, AutomationWhy } from './AutomationLine.tsx';

const text = copy.dashboard.automation;

/** The battle fact: the wave as reported, or what is known about the battle. */
function BattleFact({ battle }: { readonly battle: OperationRowVM['battle'] }) {
  if (battle.line === 'wave' && battle.wave !== null)
    return <Fact label={text.facts.wave.short} value={text.waveValue(battle.wave)} />;
  if (battle.line === 'last_wave' && battle.wave !== null)
    return (
      <Fact
        label={text.facts.lastWave.short}
        value={text.lastWaveValue(battle.wave, battle.ageSec)}
      />
    );
  if (battle.line === 'wave' || battle.line === 'last_wave') return null;
  return <Fact label={text.facts.battle.short} value={text.battle[battle.line].short} />;
}

/**
 * The visible identity and core-owned status of one automation target, as
 * labelled facts: its name beside its state, then wave and start time; the PID
 * is a diagnostic, in Details. The Stop's accessible name keeps the fused
 * identity ("Started ..., PID n") from a hidden element, so two rows of one
 * purpose stay distinct (F-010).
 */
export function AutomationFacts({ row }: { readonly row: OperationRowVM }) {
  const identity =
    row.created !== null
      ? text.started(row.created, row.pid)
      : row.pid === null
        ? null
        : text.startUnknown(row.pid);
  return (
    <div data-operation-key={row.key}>
      <Facts layout="inline" className="tp-automation-facts">
        <Fact
          label={
            <span id={`automation-${row.key}`}>
              {row.label === null ? (
                text.purposes[row.purpose].short
              ) : (
                <ServerValue kind="name" value={row.label} />
              )}
            </span>
          }
          value={
            <span className="tp-automation-state-line">
              <AutomationLine seen={row.seen} />
              <AutomationWhy seen={row.seen} />
            </span>
          }
        />
        <BattleFact battle={row.battle} />
        {row.created === null && row.pid === null ? null : (
          <Fact
            label={text.facts.started.short}
            value={row.created === null ? text.notRecorded.short : text.startedAt(row.created)}
          />
        )}
        {row.seen.params['foreign'] === true ? (
          <Fact label={text.facts.owner.short} value={text.foreignOwner.short} />
        ) : null}
        {row.scheduledStop === null ? null : (
          <Fact
            label={text.facts.scheduledStop.short}
            value={
              <span className="tp-automation-state">
                {row.scheduledStop ? <Chip state="waiting" /> : null}
                <span>{row.scheduledStop ? text.scheduled.short : text.unscheduled.short}</span>
              </span>
            }
          />
        )}
      </Facts>
      {identity === null ? null : (
        <span id={`automation-identity-${row.key}`} hidden>
          {identity}
        </span>
      )}
    </div>
  );
}

/** Diagnostics stay behind a stable native disclosure through every poll: the slot's footer. */
export function AutomationMeta({ row }: { readonly row: OperationRowVM }) {
  return (
    <Disclosure label={text.details.short} focusKey={`automation-details:${row.key}`}>
      {row.details.cmdline === null ? (
        <p>{text.detailsUnavailable.short}</p>
      ) : (
        <ServerValue kind="detail" value={row.details.cmdline} />
      )}
      <Facts>
        {row.pid === null ? null : <Fact label={text.facts.pid.short} value={row.pid} />}
        {row.details.instance === null ? null : (
          <Fact
            label={text.instance.short}
            value={<ServerValue kind="name" value={row.details.instance} />}
          />
        )}
        {row.details.step === null ? null : (
          <Fact
            label={text.step.short}
            value={<ServerValue kind="detail" value={row.details.step} />}
          />
        )}
        {row.details.recovery === null ? null : (
          <Fact
            label={text.boundary.short}
            value={<ServerValue kind="detail" value={row.details.recovery} />}
          />
        )}
      </Facts>
    </Disclosure>
  );
}
