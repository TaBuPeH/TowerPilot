import { entry, made } from '../entry.ts';
import { ageAgo } from './shell.ts';

const sources = {
  status: entry('Live status'),
  calibrate: entry('Setup progress'),
  calibrateOperation: entry('Screen learning'),
  runs: entry('Runs and readiness'),
  wizardTools: entry('Connection tools'),
  wizardState: entry('Setup steps'),
  config: entry('Configuration'),
  accounts: entry('Profiles'),
  battlesActive: entry('Tracked battle'),
  schedule: entry('Schedule'),
  battles: entry('Battle history'),
  wizardEmulators: entry('Emulators'),
  runControls: entry('Run settings'),
  profileFiles: entry('Automation profiles'),
  profileSource: entry('Active automation profile'),
  targetSource: entry('Profile being edited'),
  settings: entry('Settings'),
  vocab: entry('Profile vocabulary'),
  runTemplates: entry('Run templates'),
} as const;
const readOutcomes = {
  answered: entry('answered'),
  failed: entry('unavailable'),
  not_read: entry('not checked yet'),
} as const;
const setupLines = {
  setup_failed: entry('Setup failed'),
  setup_partial: entry('Setup partly finished'),
  setup_tracking_closed: entry('Setup tracking stopped'),
} as const;

type SourceCode = keyof typeof sources;
type ReadOutcomeCode = keyof typeof readOutcomes;
type SetupOutcomeCode = keyof typeof setupLines;

/** Needs-you wording and presentation formats (spec 6.4 and 7.3). */
export const needsYou = {
  title: entry('Needs you'),
  countOpen: entry('('),
  countClose: entry(')'),
  runtime: entry('All runtime events'),
  events: entry('Events for this operation'),
  review: entry('Review setup result'),
  retryStatus: entry('Retry status'),
  retry: entry('Retry'),
  /** One item's disclosure: why it needs you, the reads behind it and its operation's events. */
  details: entry('Details'),
  setupBattle: entry('Setup battle needs review'),
  lines: {
    no_live_status: entry('No live status', 'Retry status to read the current automation state.'),
    process_discovery_unconfirmed: entry(
      "Can't tell what's running",
      'The process scan is unavailable or stale. Retry status to check again.',
    ),
    setup_status_unavailable: entry(
      'Setup status unavailable',
      'Retry to check live status; review setup result for setup progress.',
    ),
    equipment_not_restored: entry(
      'Equipment not restored yet — other automation is on hold',
      'Review setup result before starting other automation.',
    ),
    ...setupLines,
  },
  /** What the setup guards mean for Start and collection: facts shown once for the group. */
  impact: {
    start: entry('Start'),
    collection: entry('Gem collection'),
  },
  start: {
    off: entry('Off'),
    permitted: entry('Allowed by the setup guard'),
    unconfirmed: entry('Permission not confirmed'),
  },
  collection: {
    // F4-05: scoped to the action the guard blocks, beside any collector still running.
    on_hold: entry('Starting collection is blocked'),
    confirmed_manual_battle: entry('Allowed: you confirmed a new manual battle'),
    permitted: entry('Allowed by the collection guard'),
    unconfirmed: entry('Permission not confirmed'),
  },
  guardWhy: entry(
    'Review setup result to resolve the setup battle. Confirming a new manual battle permits gem collection only; Start follows the setup guard.',
  ),
  setupWhy: entry('Review setup result and the operation events for the recorded outcome.'),
  imagesWhy: entry('Learn the missing screens on the Recognition page before starting those runs.'),
  learn: entry('Learn screens'),
  finish: {
    tools: entry('Finish setup: install connection tools'),
    profile: entry('Finish setup: create or choose a profile'),
    connection: entry('Finish setup: connect an emulator'),
    screens: entry('Finish setup: learn screens for a run'),
  },
  finishActions: {
    tools: entry('Install connection tools'),
    profile: entry('Choose a profile'),
    connection: entry('Connect an emulator'),
    screens: entry('Learn screens'),
  },
  finishWhy: entry('Complete this step, then check the next setup requirement.'),
  confirm: {
    open: entry('Confirm a new manual battle...'),
    title: entry('Confirm a new manual battle'),
    body: entry(
      'I confirm the battle on screen is a new battle I started manually, not the setup test battle. This permits gem collection only; Start stays blocked until setup is resolved.',
    ),
    action: entry('Confirm'),
    operation: entry('Setup operation:'),
    battle: entry('Setup battle:'),
  },
  source: sources,
  read: readOutcomes,
  noRead: entry('No successful read yet'),
  operation: entry('Operation:'),
  error: entry('Setup report:'),
  setupStatus: entry('Setup battle report:'),
  setupReason: entry('Setup battle reason:'),
  screen: entry('Final screen:'),
  screenVerified: entry('Final screen verified'),
  screenUnverified: entry('Final screen not verified'),
  setupGuard: entry('Start guard report:'),
  collectionGuard: entry('Collection guard report:'),
  showAll: (count: number) => made(`Show all (${String(count)})`),
  images: (count: number) => made(`Other runs need ${String(count)} screen images`),
  dated: (code: SetupOutcomeCode, at: number | null) =>
    made(
      `${setupLines[code].short} — ${at === null ? 'time not recorded' : new Date(at * 1000).toLocaleString()}`,
    ),
  checked: (source: SourceCode, outcome: ReadOutcomeCode, age: number | null) =>
    made(
      `${sources[source].short}: ${readOutcomes[outcome].short}${age === null ? '' : ` - checked ${ageAgo(age)}`}`,
    ),
  empty: entry('No unresolved issues reported'),
  checkDetails: entry('Checks and freshness'),
} as const;
