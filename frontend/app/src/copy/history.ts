import { actions as sharedActions } from './actions.ts';
import { entry, made } from './entry.ts';
import { durationText, isoText, serverDateText } from './format.ts';
import type { CopyText } from './text.ts';

export const history = {
  pageName: entry('Run history'),
  records: entry('Records'),
  recordTable: entry('Recorded battles'),
  detail: entry('Selected record'),
  separator: entry(': '),
  paging: entry('Record pages'),
  recordsLoaded: entry('Records loaded'),
  filter: entry('Recorded status'),
  filtersAndPaging: entry('Filters and paging'),
  filters: {
    all: entry('All statuses'),
    active: entry('Active'),
    prepared: entry('Prepared'),
    ended: entry('Ended'),
    interrupted: entry('Observation stopped'),
  },
  refresh: entry('Refresh records'),
  retry: entry('Retry records'),
  detailRefresh: entry('Refresh detail'),
  detailRetry: entry('Retry detail'),
  previous: entry('Previous page'),
  next: entry('Next page'),
  back: entry('Back to records'),
  noPrevious: entry('No earlier page.'),
  noNext: entry('No next page was reported.'),
  recordUnavailable: entry('This record has no ID to read.'),
  reading: entry('Loading records…'),
  readingDetail: entry('Loading selected record…'),
  targetUnknown: entry('Select an emulator and account before reading history.'),
  choose: entry('Choose a record to inspect its evidence.'),
  scrollHint: entry('More records on this page are below; scroll the list to see them.'),
  empty: entry('No records on this page.'),
  unnamed: entry('Unnamed battle'),
  unknown: entry('Unknown / not recorded'),
  unavailable: entry(
    'This read is unavailable. Previously loaded evidence, if shown, has not been refreshed.',
  ),
  errors: {
    network: entry('The server could not be reached.'),
    unavailable: entry('The server did not return a readable history result.'),
    not_found: entry(
      'The selected record is missing or does not belong to this emulator and account.',
    ),
    wrong_context: entry(
      'The reply named a different emulator, account or record and was discarded.',
    ),
  },
  result: entry('Recorded result'),
  reason: entry('Recorded end reason'),
  status: entry('Recorded status'),
  tier: entry('Tier'),
  observedWave: entry('Last observed wave'),
  finalWave: entry('Final wave'),
  start: entry('Recorded start'),
  blueprint: entry('Run'),
  id: entry('Record ID'),
  origin: entry('Origin'),
  certainty: entry('Start evidence'),
  observedStart: entry('Observation began'),
  partial: entry(
    'Partial history: observation began after the battle started. Earlier time and actions are unknown.',
  ),
  prepared: entry('Prepared does not mean started. No battle start is established by preparation.'),
  interrupted: entry(
    'Observation stopped before its end was seen. This does not establish defeat or a battle end.',
  ),
  ended: entry('Recorded end'),
  updated: entry('Record updated'),
  duration: entry('Observed duration'),
  provenance: entry('Wave provenance'),
  observation: entry('Observation status'),
  previousId: entry('Previous record'),
  setup: entry('Setup evidence'),
  setupState: entry('Recorded setup state'),
  setupReason: entry('Setup reason'),
  setupDetail: entry('Setup source'),
  setupPartial: entry('Setup continued with partial evidence.'),
  loadout: entry('Recorded loadout'),
  modules: entry('Module evidence'),
  stats: entry('Stats capture'),
  statsRecordedState: entry('Recorded capture state'),
  statsReason: entry('Capture reason'),
  statsError: entry('Capture error'),
  statsStates: {
    verified: entry('Verified for this record'),
    unverified: entry('Unverified'),
    failed: entry('Capture failed'),
    pending: entry('Capture pending'),
    missing: entry('Missing / not recorded'),
    unknown: entry('Capture state unknown'),
  },
  collection: entry('Sampled collection evidence'),
  collectionStates: {
    sampled: entry('Sampled observations'),
    unverified: entry('Unverified provenance; counts withheld'),
    missing: entry('Missing / not recorded'),
  },
  sampled: entry(
    'These are sampled observations, not the battle’s total gems. An ad claim confirmation does not establish a balance increase.',
  ),
  claims: entry('Recorded ad claims seen'),
  balances: entry('Recorded orbit balance confirmations'),
  increase: entry('Recorded orbit balance increase in this sample'),
  from: entry('Sample began'),
  through: entry('Sample observed through'),
  gaps: entry('Observation gaps'),
  openGap: entry('Open gap began'),
  gapNote: entry(
    'Only recorded gaps are shown. An empty record does not establish continuous coverage.',
  ),
  snapshot: entry('Configuration snapshot'),
  noSnapshot: entry('Configuration was not recorded.'),
  effective: entry('Recorded effective controls'),
  activity: entry('Timestamped activity'),
  noActivity: entry('No activity was recorded.'),
  rawEvidence: entry('Recorded evidence'),
  moduleRecord: entry('Module evidence record'),
  statsRecord: entry('Stats capture record'),
  collectionRecord: entry('Collection evidence record'),
  gapRecord: entry('Recorded gap intervals'),
  eventRecord: (index: number) => made(`Activity record ${index}`),
  /**
   * A2-7 AC4: recorded executions of timed actions. A history is a read, not a status: an open row
   * says what it was when read, and a missing list never says that nothing ran.
   */
  actions: {
    heading: entry('Actions in this battle'),
    list: entry('Recorded action executions'),
    readNote: entry('Newest first; recorded evidence, not live status.'),
    states: {
      not_reported: entry('This server did not report action history for this record.'),
      before_history: entry(
        'Recorded before action history existed. This does not mean that no action ran.',
      ),
      newer_schema: entry(
        'Written by a newer version of Tower Pilot, so it is not shown. This does not mean that no action ran.',
      ),
      unreadable: entry('Action history could not be read for this record.'),
      empty: entry('No action executions are recorded for this battle.'),
    },
    reasonCode: entry('Recorded reason code'),
    truncated: entry('Older executions exist. Only the newest ones are shown.'),
    truncatedNoCursor: entry('Older executions exist, but the server named no way to read them.'),
    complete: entry('All recorded executions for this battle are shown.'),
    older: entry('Older executions'),
    olderBusy: entry('Loading older executions…'),
    olderPending: entry('Wait for the current older-executions read.'),
    olderRetry: entry('Retry older executions'),
    olderLoaded: (count: number) =>
      made(count === 1 ? '1 older execution loaded' : `${count} older executions loaded`),
    skipped: (count: number) =>
      made(
        count === 1
          ? '1 damaged record was skipped by the server.'
          : `${count} damaged records were skipped by the server.`,
      ),
    code: entry('Action code'),
    instance: entry('Instance'),
    finished: entry('Finished'),
    trigger: entry('Trigger'),
    state: entry('State'),
    result: entry('Result'),
    effect: entry('Effect'),
    duration: entry('Duration'),
    coverage: entry('Coverage'),
    checks: entry('Occurrences in this entry'),
    firstCheck: entry('First check'),
    lastCheck: entry('Last check'),
    notFinished: entry('Not finished when read'),
    noFinish: entry('Not recorded: stopped before it finished'),
    noDuration: entry('Not recorded'),
    notRecognised: entry('Not recognised'),
    triggers: {
      automatic: entry('Automatic'),
      manual: entry('Manual (Run now)'),
    },
    // A state is a result word, except an open row, which History reads rather than watches.
    stateWords: { running: entry('Open when read'), ...sharedActions.results },
    coverages: {
      full: entry('Full record'),
      since_adoption: entry('Since this automation adopted the battle'),
      unknown: entry('Coverage unknown'),
    },
    readAt: (at: number | null) => {
      const instant = at === null ? null : isoText(at);
      return instant === null ? history.unknown.short : made(`Read ${instant}`);
    },
    durationValue: (ms: number) => made(durationText(ms / 1000)),
  },
  boundary: entry('End evidence'),
  page: (page: number) => made(`Page ${page}`),
  number: (value: number | null): CopyText =>
    value === null ? history.unknown.short : made(String(value)),
  seconds: (value: number | null): CopyText =>
    value === null ? history.unknown.short : made(`${value} seconds observed`),
  time: (value: number | null): CopyText => {
    const instant = value === null ? null : isoText(value * 1000);
    return instant === null ? history.unknown.short : made(instant);
  },
  recordTime: (value: number | null): CopyText =>
    value === null || isoText(value * 1000) === null
      ? history.unknown.short
      : made(serverDateText(value)),
  loaded: (at: number): CopyText => {
    const instant = isoText(at);
    return instant === null ? history.unknown.short : made(`Last loaded ${instant}`);
  },
} as const;
