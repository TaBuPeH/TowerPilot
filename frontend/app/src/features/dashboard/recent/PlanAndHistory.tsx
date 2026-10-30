import { copy } from '../../../copy/catalogue.ts';
import { ageAgo, lastRead } from '../../../copy/dashboard/shell.ts';
import type { CopyEntry, CopyNode } from '../../../copy/text.ts';
import { selectRecent, type ScheduleVM } from '../../../store/selectors/recent.ts';
import { Chip } from '../../../ui/Chip.tsx';
import { Disclosure } from '../../../ui/Disclosure.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { RegionBody } from '../../../ui/Region.tsx';
import { ServerValue } from '../../../ui/ServerValue.tsx';
import { Tile } from '../../../ui/Tile.tsx';
import { useView } from '../context.ts';
import { DashboardRegion } from '../shared/DashboardRegion.tsx';
import './recent.css';

const words = copy.dashboard.recent;
const scheduleLines: Readonly<Record<string, CopyEntry>> = words.lines;

/**
 * Read-only schedule wording; eligibility and state belong to core/schedule.
 * A plain state is one line; a planned or last seen block is labelled facts.
 */
function scheduleParts(vm: ScheduleVM): { readonly line: CopyNode; readonly facts: CopyNode } {
  if (vm.line === 'last_known' && vm.previous !== null) {
    const age = vm.params['ageSec'];
    const inner = scheduleParts(vm.previous);
    return {
      line: (
        <>
          <span>{words.lastKnown.short}</span>
          {inner.line}
          {typeof age === 'number' ? <span>{lastRead(age)}</span> : null}
        </>
      ),
      facts: inner.facts,
    };
  }
  if (vm.line === 'winner_next_check' || vm.line === 'winner_no_clock_check') {
    return {
      line: null,
      facts: (
        <Facts layout="inline">
          <Fact
            label={words.facts.nextBlock.short}
            value={
              vm.winner === null ? (
                words.winnerUnknown.short
              ) : (
                <ServerValue value={vm.winner} kind="name" />
              )
            }
          />
          <Fact
            label={words.facts.nextCheck.short}
            value={
              <span className="tp-recent-line">
                <span>
                  {vm.nextClockAt === null
                    ? words.noClock.short
                    : words.nextCheckAt(vm.nextClockAt, vm.clockOffsetSec)}
                </span>
                {vm.nextInSec === null ? null : <span>{words.inDuration(vm.nextInSec)}</span>}
              </span>
            }
          />
          <Fact label={words.facts.switch.short} value={words.switchWaits.short} />
        </Facts>
      ),
    };
  }
  if (vm.line === 'last_seen') {
    const age = vm.params['ageSec'];
    return {
      line: null,
      facts: (
        <Facts layout="inline">
          <Fact
            label={words.facts.lastSeen.short}
            value={
              vm.observedWinner === null ? (
                words.winnerUnknown.short
              ) : (
                <ServerValue value={vm.observedWinner} kind="name" />
              )
            }
          />
          <Fact
            label={words.facts.seen.short}
            value={typeof age === 'number' ? ageAgo(age) : words.noAge.short}
          />
        </Facts>
      ),
    };
  }
  return {
    line: <span>{scheduleLines[vm.line]?.short ?? words.lines.schedule_unknown.short}</span>,
    facts: null,
  };
}

/** Schedule: its state chip and line with Why, then the planned block as facts. */
function ScheduleTile({ vm }: { readonly vm: ScheduleVM }) {
  const parts = scheduleParts(vm);
  const status = vm.word !== null || parts.line !== null;
  return (
    <Tile
      density="compact"
      titleId="recent-schedule-title"
      title={words.schedule.short}
      end={<a href="#schedule">{words.scheduleLink.short}</a>}
    >
      {status ? (
        <div className="tp-recent-line">
          {vm.word === null ? null : <Chip state={vm.word} />}
          {parts.line}
          {vm.reason === null && vm.error === null && vm.previous?.error == null ? null : (
            <Disclosure label={copy.common.why.short} focusKey="schedule-why">
              {vm.reason === null ? null : (
                <ServerValue value={vm.reason} kind="detail" label={words.reason.short} />
              )}
              {vm.error === null ? null : <ServerValue value={vm.error} kind="message" />}
              {vm.previous?.error === null || vm.previous?.error === undefined ? null : (
                <ServerValue value={vm.previous.error} kind="message" />
              )}
            </Disclosure>
          )}
        </div>
      ) : null}
      {parts.facts}
    </Tile>
  );
}

/** Subscribed inside the region boundary, so selector faults retain its fallback. */
function RecentContent() {
  const vm = useView(selectRecent);
  const last = vm.lastBattle;
  const battle = last.battle.kind === 'known' ? last.battle.value : null;
  return (
    <RegionBody>
      <ul className="tp-tiles tp-recent">
        <li data-recent="schedule">
          <ScheduleTile vm={vm.schedule} />
        </li>
        <li data-recent="battle">
          <Tile
            density="compact"
            titleId="recent-battle-title"
            title={words.lastBattle.short}
            end={<a href="#history">{words.historyLink.short}</a>}
          >
            {battle === null ? (
              <p className="tp-recent-line">
                {last.line === 'recorded' || last.line === 'last_known'
                  ? words.battleLines.unavailable.short
                  : words.battleLines[last.line].short}
              </p>
            ) : (
              <>
                <Facts layout="inline">
                  <Fact
                    label={words.facts.run.short}
                    value={
                      <span className="tp-recent-line">
                        {last.line === 'last_known' ? <span>{words.lastKnown.short}</span> : null}
                        {battle.label === null ? (
                          words.battleUnknown.short
                        ) : (
                          <ServerValue value={battle.label} kind="name" />
                        )}
                      </span>
                    }
                  />
                  <Fact label={words.facts.end.short} value={words.ends[battle.line].short} />
                  <Fact
                    label={words.facts.when.short}
                    value={last.endedAgeSec === null ? words.noAge.short : ageAgo(last.endedAgeSec)}
                  />
                  <Fact
                    label={words.facts.wave.short}
                    value={
                      battle.wave === null ? words.notRecorded.short : words.waveValue(battle.wave)
                    }
                  />
                </Facts>
                <div className="tp-recent-foot">
                  <Disclosure label={words.details.short} focusKey="last-battle-why">
                    <p>
                      {battle.endedAt === null
                        ? words.noTime.short
                        : words.recordedAt(battle.endedAt)}
                    </p>
                    {last.readAgeSec === null ? null : <p>{lastRead(last.readAgeSec)}</p>}
                    {battle.line !== 'tracking_ended' || battle.status === null ? null : (
                      <ServerValue value={battle.status} kind="detail" label={words.status.short} />
                    )}
                    {battle.blueprint === null ? null : (
                      <ServerValue
                        value={battle.blueprint}
                        kind="id"
                        label={words.blueprint.short}
                      />
                    )}
                    {battle.endReason === null ? null : (
                      <ServerValue
                        value={battle.endReason}
                        kind="message"
                        label={words.endReason.short}
                      />
                    )}
                    {battle.result === null ? null : (
                      <ServerValue value={battle.result} kind="detail" label={words.result.short} />
                    )}
                  </Disclosure>
                </div>
              </>
            )}
          </Tile>
        </li>
      </ul>
    </RegionBody>
  );
}

/** Region 5: the Schedule tile and the Last battle tile, side by side (F-077 to F-080). */
export function PlanAndHistory() {
  return (
    <DashboardRegion id="recent">
      <RecentContent />
    </DashboardRegion>
  );
}
