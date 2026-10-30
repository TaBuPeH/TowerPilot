import { entry, made } from '../entry.ts';

/**
 * The Dashboard shell (spec sections 2.1, 3.1, 3.3, 3.5, 8): region names,
 * the eight status words, the setting-pill prefixes, the shared fallback lines
 * and the revocation labels every fallback keeps (S-091).
 */
export const shell = {
  skipToContent: entry('Skip to content'),
  pageName: entry('Dashboard'),
  slots: {
    automation: entry('Automation'),
    input: entry('Game input'),
    gem: entry('Gem harvesting'),
  },
  regions: {
    control: entry('Control'),
    needsYou: entry('Needs you'),
    thisBattle: entry('This battle'),
    startNext: entry('Start next'),
    recent: entry('Plan and history'),
  },
  /** Spec 3.1: the eight status words; no other state word exists. */
  states: {
    running: entry('Running'),
    waiting: entry('Waiting'),
    paused: entry('Paused'),
    idle: entry('Idle'),
    stopping: entry('Stopping'),
    unknown: entry('Unknown'),
    blocked: entry('Blocked'),
    failed: entry('Failed'),
  },
  /** Spec 3.2: the setting-pill prefixes. */
  pills: {
    yourChoice: entry('Setting:'),
    setting: entry('Setting:'),
  },
  /** Spec 3.7 and S-091: the revocations every fallback keeps. */
  revocations: {
    pause: entry('Pause harvesting'),
    turnInputOff: entry('Turn input off'),
    stop: entry('Stop'),
  },
  /** Spec 3.5, S-090: one line per part that could not be drawn. */
  fallback: {
    runtime: entry(
      "Can't show this part of the Runtime page",
      'Saved drafts remain in their original context. Reload to try again.',
    ),
    automation: entry(
      "Can't show automation state",
      'This part of the page failed to draw. Pause harvesting, Turn input off and the known Stops still work; reloading the page draws it again.',
    ),
    gem: entry(
      'Harvesting may be on',
      'This part of the page failed to draw, so harvesting may be on. Pause harvesting and Turn input off still work; reloading the page draws it again.',
    ),
    input: entry(
      "Can't show game input",
      'This part of the page failed to draw. Turn input off and Pause harvesting still work; reloading the page draws it again.',
    ),
    outcomes: entry(
      "Can't show request results",
      'This part of the page failed to draw. Unresolved requests are kept and are never sent again on their own.',
    ),
    needsYou: entry(
      "Can't show what needs you",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; reloading the page draws it again.',
    ),
    thisBattle: entry(
      "Can't show this battle",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; reloading the page draws it again.',
    ),
    startNext: entry(
      "Can't show Start next",
      'This part of the page failed to draw. Nothing can be started from here until it draws again; Pause harvesting and Turn input off still work.',
    ),
    recent: entry(
      "Can't show plan and history",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; reloading the page draws it again.',
    ),
    topBar: entry(
      "Can't show the account and emulator",
      'This part of the page failed to draw. The controls below still work.',
    ),
    runsLive: entry(
      "Can't show this battle's settings",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; unsaved changes are kept and reloading the page draws it again.',
    ),
    runsSaved: entry(
      "Can't show the saved settings",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; unsaved changes are kept and reloading the page draws it again.',
    ),
    recognition: entry(
      "Can't show this part of the Recognition page",
      'This part of the page failed to draw. Pause harvesting, Turn input off and the known Stops still work; nothing was sent, and reloading the page draws it again.',
    ),
    emulators: entry(
      "Can't show this part of the Emulators page",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; nothing was sent, and reloading the page draws it again.',
    ),
    collection: entry(
      "Can't show this part of the Collection page",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; unsaved changes are kept and reloading the page draws it again.',
    ),
    profiles: entry(
      "Can't show this part of the Profiles page",
      'This part of the page failed to draw. Pause harvesting and Turn input off still work; unsaved changes are kept and reloading the page draws it again.',
    ),
    page: entry(
      "Couldn't load the Dashboard",
      'The page failed to draw. Pause harvesting, Turn input off and the known Stops still work; Try again draws the page again.',
    ),
  },
  tryAgain: entry('Try again'),
  /** F-107: applied low-stakes results; failures never toast. */
  toasts: {
    reconnect: entry('Reconnect started'),
    clear_flag: entry('Scheduled stop cancelled'),
    dismiss: entry('Dismiss'),
  },
  sending: entry('Sending'),
  /** F-011, F-012, spec 8.7: a Stop or kill asks first; Cancel is focused. */
  confirmStop: {
    title: entry('Stop this automation?'),
    /** "Stop {name}?" around a named target. */
    lead: entry('Stop'),
    end: entry('?'),
    body: entry('Gem harvesting pauses too. The battle keeps playing.'),
    confirm: entry('Stop'),
  },
  confirmKill: {
    title: entry('Stop this process?'),
    body: entry(
      'It ends exactly the process shown, by its PID and start time. If that process already ended, nothing else is stopped.',
    ),
    confirm: entry('Stop process'),
  },
  cancel: entry('Cancel'),
  stopProcess: entry('Stop process'),
  /** S-050, S-060, S-061: why a Stop is disabled. */
  stopDisabled: {
    waiting_for_fresh_status: entry(
      'Waiting for fresh status',
      'No valid revision is known for this operation yet; the next status read supplies it.',
    ),
    created_missing: entry(
      "Can't prove it is the same process",
      'The process list does not record when this process started, so a kill could hit another process with the same PID.',
    ),
    operation_unidentified: entry(
      "Can't identify the operation",
      'This row names an operation without a valid id or revision.',
    ),
    process_unidentified: entry("Can't identify the process", 'This row has no valid PID.'),
  },
  targetPid: entry('PID'),
  targetOperation: entry('Operation:'),
} as const;

/** "{n} s ago", "{n} m ago", "{n} h ago": an age, from whole seconds. */
export function ageAgo(seconds: number): ReturnType<typeof made> {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return made(`${String(whole)} s ago`);
  if (whole < 3600) return made(`${String(Math.floor(whole / 60))} m ago`);
  return made(`${String(Math.floor(whole / 3600))} h ago`);
}

/** F-007: "Gem harvesting: Unknown. Game input: Idle": the Control words that changed. */
export function wordChanges(
  changes: readonly (readonly [slot: string, word: string])[],
): ReturnType<typeof made> {
  return made(changes.map(([slot, word]) => `${slot}: ${word}`).join('. '));
}

/** "last read {age}": the age of the last good read, for a stale or failed slot. */
export function lastRead(seconds: number): ReturnType<typeof made> {
  return made(`last read ${ageAgo(seconds)}`);
}
