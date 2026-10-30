import { entry, made } from '../entry.ts';
import { ageAgo, lastRead } from './shell.ts';

/** The Automation slot (spec 3.6.4, F-008 to F-014, F-016): owned by its region author. */
export const automation = {
  title: entry('Automation'),
  purposes: {
    gems: entry('Gem collector'),
    run: entry('Run'),
    learning: entry('Screen learning'),
    process: entry('Automation'),
  },
  lines: {
    last_read: entry(
      'Last read unavailable',
      'The latest status read did not answer. These are the last known records; the Stops remain available.',
    ),
    cant_tell_whats_running: entry(
      "Can't tell what's running",
      'The harvest record or ownership list is incomplete, or the process scan is not fresh. Nothing here proves automation idle.',
    ),
    started: entry('Process listed'),
    start_time_not_recorded: entry('Start time not recorded'),
    last_listed: entry(
      'Last listed time unavailable',
      'The process scan is not fresh. The listed process may still be running; Stop remains available.',
    ),
    operation_battle: entry('Battle reported'),
    stop_not_confirmed: entry(
      'Stop not confirmed',
      'The operation is still recorded as stopping. Its Stop remains available until a complete fresh read proves it gone.',
    ),
    starting: entry(
      'Starting',
      'An accepted operation is recorded; it has not yet reported a fresh battle.',
    ),
    waiting_to_track_battle: entry('Waiting to track a battle'),
    recorded_process_not_seen: entry(
      "Recorded; its process isn't seen",
      'A retained operation, worker liveness or a stale publication does not prove automation is running or idle. Stop remains available.',
    ),
    run_battle: entry('Battle reported'),
    another_process_holds: entry(
      'Another process holds the emulator',
      'Stop it there, or turn input off. This page cannot stop a process reported only as an owner.',
    ),
    no_automation_running: entry(
      'No automation running',
      'A fresh complete scan lists no process, operation or owner, and no known holder of the emulator remains unresolved.',
    ),
    no_automation_now_harvesting_on: entry(
      'No automation running now; Gem harvesting is On',
      'The fresh complete ownership read is empty. Gem harvesting may start automation next; its slot shows what is holding it up.',
    ),
    last_known_idle: entry(
      'Last known: idle',
      'A previous fresh complete read proved idle. The current process scan does not prove what is running now.',
    ),
    holder_unresolved: entry(
      'Who holds the emulator is unresolved',
      'A known holder of the emulator has not been proved gone. Turn input off and the known Stops remain available.',
    ),
  },
  details: entry('Details'),
  detailsUnavailable: entry('Details unavailable'),
  instance: entry('Instance'),
  step: entry('Step'),
  boundary: entry('Boundary wait'),
  endsAutomation: entry('Ends automation, not the battle'),
  foreign: entry('From another install'),
  stopThere: entry('Stop it there, or turn input off'),
  retained: entry('Last known automation'),
  retry: entry('Retry status'),
  scheduled: entry('Will stop after this run'),
  unscheduled: entry('Will not stop after this run'),
  cancelScheduled: entry('Cancel scheduled stop'),
  cancelUnavailable: entry('Waiting for a valid operation revision'),
  setup: entry('Setup running'),
  setupStepUnknown: entry('Step not reported'),
  /** The labels of one automation target's facts (label beside value, never a fused line). */
  facts: {
    wave: entry('Wave'),
    lastWave: entry('Last wave'),
    battle: entry('Battle'),
    started: entry('Started'),
    pid: entry('PID'),
    owner: entry('Owner'),
    scheduledStop: entry('Scheduled stop'),
  },
  foreignOwner: entry('Another install'),
  notRecorded: entry('Not recorded'),
  battle: {
    wave_pending: entry('Wave not reported yet'),
    waiting: entry('Waiting to track a battle'),
    none: entry('No tracked battle'),
    unknown: entry('Unknown'),
  },
  started: (created: number, pid: number | null) => {
    const date = new Date(created * 1000);
    const stamp = Number.isNaN(date.getTime()) ? null : date.toLocaleString('en-US');
    return made(
      stamp === null
        ? `Start time not recorded${pid === null ? '' : `, PID ${String(pid)}`}`
        : `Started ${stamp}${pid === null ? '' : `, PID ${String(pid)}`}`,
    );
  },
  startUnknown: (pid: number) => made(`Start time not recorded, PID ${String(pid)}`),
  /** The Started fact's value: the local start time, or that none is recorded. */
  startedAt: (created: number) => {
    const date = new Date(created * 1000);
    return made(Number.isNaN(date.getTime()) ? 'Not recorded' : date.toLocaleString('en-US'));
  },
  /** Mid-line, after a run name ('Farm - wave 123'). */
  wave: (wave: number) => made(`wave ${wave.toLocaleString('en-US')}`),
  /** The Wave fact's value (Automation's row). */
  waveValue: (wave: number) => made(wave.toLocaleString('en-US')),
  /** The Last wave fact's value: the last reported wave and its age. */
  lastWaveValue: (wave: number, age: number | null) =>
    made(`${wave.toLocaleString('en-US')}, ${age === null ? 'age not recorded' : ageAgo(age)}`),
  lastIdle: (age: number) => made(`Last known: idle, ${ageAgo(age)}`),
  lastListed: (age: number) => made(`last listed ${ageAgo(age)}`),
  lastRead,
} as const;
