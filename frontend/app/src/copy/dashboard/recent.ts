import { entry, made } from '../entry.ts';
import { isoText } from '../format.ts';
import { automation } from './automation.ts';

/**
 * The UTC instant of an epoch-seconds time through the one epoch-to-ISO rule (format.ts isoText).
 * A time no date can hold throws the RangeError `toISOString` threw here before, so the region's
 * fallback still takes over exactly as it did.
 */
function iso(seconds: number): string {
  const text = isoText(seconds * 1000);
  if (text === null) throw new RangeError('Invalid time value');
  return text;
}

/** Catalogue entries of the Dashboard's recent part (spec section 6.2). Owned by its region. */
export const recent = {
  schedule: entry('Schedule'),
  scheduleLink: entry('Schedule page'),
  lastBattle: entry('Last battle'),
  historyLink: entry('Run history'),
  separator: entry(' · '),
  lastKnown: entry('Last known:'),
  /** The tiles' fact labels: one labelled value each, never a run joined by dots. */
  facts: {
    nextBlock: entry('Next block'),
    nextCheck: entry('Next check'),
    switch: entry('Switch'),
    lastSeen: entry('Last seen'),
    seen: entry('Seen'),
    run: entry('Run'),
    end: entry('End'),
    when: entry('When'),
    wave: entry('Wave'),
  },
  switchWaits: entry('waits for the run to end'),
  notRecorded: entry('Not recorded'),
  /** The last battle's recorded detail (end time, status, run, result). */
  details: entry('Details'),
  winnerUnknown: entry('Block name not recorded'),
  battleUnknown: entry('Battle name not recorded'),
  noClock: entry('no clock check planned'),
  noAge: entry('age not available'),
  noTime: entry('End time not recorded'),
  reason: entry('Recorded reason:'),
  status: entry('Recorded status:'),
  result: entry('Recorded result:'),
  endReason: entry('Recorded end reason:'),
  blueprint: entry('Recorded run:'),
  lines: {
    not_checked: entry('not checked yet'),
    schedule_unavailable: entry('schedule unavailable'),
    no_eligible_block: entry('no block open'),
    no_blocks_today: entry('no blocks today'),
    plan_unavailable: entry("the day plan can't be read"),
    not_seen_running: entry('Not seen running'),
    scheduler_stopping: entry('scheduler stopping'),
    scheduler_starting: entry('scheduler starting'),
    scheduler_run_unconfirmed: entry('scheduler run not confirmed running'),
    scheduler_process_unconfirmed: entry('scheduler process not confirmed alive'),
    schedule_unknown: entry('schedule not confirmed'),
  },
  battleLines: {
    not_checked: entry('Not checked yet'),
    unavailable: entry('Unavailable'),
    no_battle_recorded: entry('No battle recorded yet'),
    no_previous_battle: entry('No previous battle recorded'),
  },
  ends: {
    ended: entry('Ended'),
    tracking_stopped_end_not_seen: entry('tracking stopped, end not seen'),
    tracking_ended: entry('tracking ended'),
  },
  wave: automation.wave,
  waveValue: automation.waveValue,
  /** The Next check fact: the local clock time of the next check (UTC when no offset is known). */
  nextCheckAt: (at: number, offsetSec: number | null) =>
    made(`${iso(at + (offsetSec ?? 0)).slice(11, 16)}${offsetSec === null ? ' UTC' : ''}`),
  inDuration: (seconds: number) => {
    const whole = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    return made(
      hours > 0
        ? `(in ${hours} h ${minutes} m)`
        : minutes > 0
          ? `(in ${minutes} m)`
          : `(in ${whole} s)`,
    );
  },
  recordedAt: (at: number) => made(`Recorded end time: ${iso(at)}`),
} as const;
