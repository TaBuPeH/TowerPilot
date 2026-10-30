import { selectEditingTarget } from './editingTarget.ts';
import { profileWriteKind } from '../../core/profiles.ts';
import { selectRun } from '../../core/readiness.ts';
import { id, list, own, ownPath } from '../../core/read.ts';
import { assessedRead, answeredBody } from '../reads.ts';
import { contextIntents, inCurrentContext, unresolvedIntent } from '../intents.ts';
import { toolsView } from '../../core/emulators.ts';
import { gemPreviewRun, runControlsPath } from './gemSlot.ts';
import {
  latestOperationWorking,
  RECOGNITION_LAUNCH_KINDS,
  trackedOperation,
  trackedOperationPath,
  trackedOperationWorking,
} from './recognitionOperation.ts';
import type { DashboardState, IntentView, ResourceId } from '../types.ts';
import type { ReadPlan } from '../../api/poller.ts';

/**
 * The read plan of spec 7.2, decided from the store and handed to the poller
 * (app/wiring.ts): each resource's path and mode, and the reads an event asks
 * for ("Also read" column). Pure: the poller does the timing.
 */
export type ReadPlanVM = ReadPlan;

function anyProcessListed(state: DashboardState): boolean {
  return (list(own(answeredBody(state, 'status'), 'processes'))?.length ?? 0) > 0;
}

function calibrationRunning(state: DashboardState): boolean {
  return own(answeredBody(state, 'calibrate'), 'running') === true;
}

function sendingKind(state: DashboardState, kinds: readonly string[]): boolean {
  return contextIntents(state).some(
    (record) => record.phase === 'sending' && kinds.includes(record.kind),
  );
}

function anyUnresolved(state: DashboardState): boolean {
  return contextIntents(state).some(
    (record) => unresolvedIntent(record) && !record.dismissed && record.supersededBySeq === null,
  );
}

/** The selected run (F-037) and its run-controls blueprint (dashboard.py strips `bp_`). */
export function selectedRunOf(state: DashboardState): string | null {
  return selectRun(answeredBody(state, 'runs'), state.selectedRun);
}

function statusMode(state: DashboardState): 'active' | 'idle' {
  const latest = state.reads.status.latest;
  if (latest === null || latest.outcome.kind === 'failed') return 'active';
  if (assessedRead(latest).scan.kind === 'unknown') return 'active';
  if (
    anyProcessListed(state) ||
    calibrationRunning(state) ||
    sendingKind(state, ['start', 'start_gems', 'reconnect']) ||
    anyUnresolved(state)
  ) {
    return 'active';
  }
  return 'idle';
}

export function selectReadPlan(state: DashboardState, resource: ResourceId): ReadPlanVM {
  const instance = state.context?.instance;
  switch (resource) {
    case 'status':
      return { path: '/api/status', mode: statusMode(state) };
    case 'calibrate':
      return {
        path: '/api/calibrate/status',
        mode:
          calibrationRunning(state) ||
          sendingKind(state, ['start', 'start_gems', ...RECOGNITION_LAUNCH_KINDS]) ||
          (state.recognitionOpen && latestOperationWorking(state))
            ? 'active'
            : 'idle',
      };
    case 'calibrateOperation': {
      // R1, R2: the operation the Recognition page follows, by its id, while the page is open.
      const path = state.recognitionOpen ? trackedOperationPath(trackedOperation(state)) : null;
      return path === null
        ? { path: null, mode: 'off' }
        : { path, mode: trackedOperationWorking(state) ? 'active' : 'off' };
    }
    case 'battlesActive': {
      const answered = state.reads.battlesActive.lastAnswered;
      const active = ownPath(answeredBody(state, 'battlesActive'), 'battle', 'status') === 'active';
      return { path: '/api/battles/active', mode: answered === null || active ? 'active' : 'idle' };
    }
    case 'schedule': {
      if (instance === undefined) return { path: null, mode: 'off' };
      const body = answeredBody(state, 'schedule');
      const relevant =
        anyProcessListed(state) ||
        own(body, 'availability') === 'available' ||
        own(body, 'reason') === 'scheduler_observation_stale';
      return {
        path: `/api/schedule/status?instance=${encodeURIComponent(instance)}`,
        mode: relevant ? 'active' : 'off',
      };
    }
    case 'battles':
      return instance === undefined
        ? { path: null, mode: 'off' }
        : { path: `/api/battles?limit=3&instance=${encodeURIComponent(instance)}`, mode: 'off' };
    case 'config':
      // The config read names the page context. Until one answers in this epoch, a failed
      // or timed-out read is read again on the schedule backoff (2 s doubling to 60 s), so a
      // page whose first read failed is never left without a context; once confirmed, config
      // is read on events only.
      return state.contextConfirmed
        ? { path: '/api/config', mode: 'off' }
        : { path: '/api/config', mode: 'off', retry: 'schedule' };
    case 'accounts':
      return { path: '/api/accounts', mode: 'off' };
    case 'runs':
      return { path: '/api/runs', mode: 'off' };
    case 'wizardTools':
      // M5: while the Emulators page is open and an install runs, its progress is followed;
      // leaving the page stops this (the install itself goes on; it is never cancelled here).
      return {
        path: '/api/wizard/tools',
        mode: state.emulatorsOpen && toolsInProgress(state) ? 'active' : 'off',
      };
    case 'wizardState':
      return { path: '/api/wizard/state', mode: 'off' };
    case 'wizardEmulators':
      return { path: '/api/wizard/emulators', mode: 'off' };
    case 'runControls': {
      // F-111: the Start picker's run first, then the gem picker's; a run not read yet in
      // this context is read before one already cached.
      const wanted = previewPaths(state);
      const path = wanted.find((entry) => state.runControls[entry] === undefined) ?? wanted[0];
      return path === undefined ? { path: null, mode: 'off' } : { path, mode: 'off' };
    }
    case 'profileFiles':
      return state.profilesOpen || state.collectionOpen || state.runsOpen
        ? { path: '/api/profile-files', mode: 'off' }
        : { path: null, mode: 'off' };
    case 'settings':
      return state.collectionOpen ||
        state.emulatorsOpen ||
        state.runtimeOpen ||
        state.runsOpen ||
        state.equipmentOpen
        ? { path: '/api/settings', mode: 'off' }
        : { path: null, mode: 'off' };
    case 'runTemplates':
      return state.collectionOpen || state.runsOpen
        ? { path: '/api/run-templates', mode: 'off' }
        : { path: null, mode: 'off' };
    case 'vocab':
      return state.collectionOpen || state.runsOpen
        ? { path: '/api/vocab', mode: 'off' }
        : { path: null, mode: 'off' };
    case 'targetSource': {
      const target = selectEditingTarget(state);
      return (state.collectionOpen || state.runsOpen) &&
        target.inactive &&
        target.name !== null &&
        target.blocked === null
        ? {
            path: `/api/profile-src/${encodeURIComponent(target.name)}`,
            mode: 'off',
            targetSelection: { name: target.name, generation: target.generation },
          }
        : { path: null, mode: 'off' };
    }
    case 'profileSource': {
      // The active profile as the page context names it (its config read): a new profile
      // is a new context, read again from the start (S-014).
      const profile = state.context?.profile ?? null;
      return (state.profilesOpen || state.collectionOpen || state.runsOpen) && profile !== null
        ? { path: `/api/profile-src/${encodeURIComponent(profile)}`, mode: 'off' }
        : { path: null, mode: 'off' };
    }
  }
}

/** The reads the Profiles page relies on (profiles brief P1 to P5). */
const PROFILE_READS: readonly ResourceId[] = [
  'config',
  'accounts',
  'profileFiles',
  'profileSource',
  'runs',
];

/** The reads that follow an Emulators page request (emulators brief M2 to M5). */
const EMULATOR_READS: Partial<Record<string, readonly ResourceId[]>> = {
  adopt: ['config', 'status', 'wizardState', 'wizardEmulators'],
  launch: ['status'],
  prepare: ['wizardEmulators'],
  tools_install: ['wizardTools'],
};

/** An adopt changes the configured address (a new context): its record is of the old one. */
function adoptsOfThisInstance(state: DashboardState): readonly IntentView[] {
  return state.intents.filter(
    (record) =>
      record.kind === 'adopt' &&
      record.context !== null &&
      record.context.instance === state.context?.instance &&
      !inCurrentContext(state, record),
  );
}

/** M5: the newest answered tools read says a download or install is running. */
function toolsInProgress(state: DashboardState): boolean {
  return toolsView(answeredBody(state, 'wizardTools'))?.inProgress === true;
}

/** The Recognition page's writes (screens brief R1 to R8). */
const RECOGNITION_WRITES: readonly string[] = [
  'calibrate_start',
  'calibrate_observe',
  'calibrate_apply',
  'calibrate_stop',
];

/** The reads that show the Collection page's saved settings (collection brief C1 to C6). */
const COLLECTION_READS: readonly ResourceId[] = [
  'config',
  'settings',
  'profileSource',
  'targetSource',
  'profileFiles',
];

/** The run the Runs page shows: the one it was opened on, else the Start picker's default (RUN-01). */
export function runsPageRun(state: DashboardState): string | null {
  return state.runsRun ?? selectedRunOf(state);
}

/** The tracked battle's run, whose saved bounds the live editor checks against (RUN-08). */
export function liveBattleRun(state: DashboardState): string | null {
  return id(ownPath(answeredBody(state, 'battlesActive'), 'battle', 'blueprint')) ?? null;
}

/**
 * The run-controls paths the page needs: on the Runs page its run and the tracked
 * battle's run first (brief RUN-02, RUN-08), then the Start picker's run and the gem
 * picker's (F-111).
 */
function previewPaths(state: DashboardState): readonly `/api/${string}`[] {
  const paths: `/api/${string}`[] = [];
  if (state.runsOpen) {
    for (const run of [runsPageRun(state), liveBattleRun(state)]) {
      if (run !== null && run !== 'combo' && !paths.includes(runControlsPath(run)))
        paths.push(runControlsPath(run));
    }
  }
  const start = selectedRunOf(state);
  if (start !== null && start !== 'combo' && !paths.includes(runControlsPath(start)))
    paths.push(runControlsPath(start));
  const gem = gemPreviewRun(state);
  if (gem !== null && !paths.includes(runControlsPath(gem))) paths.push(runControlsPath(gem));
  return paths;
}

function identityKey(state: DashboardState): string | null {
  const identity = ownPath(answeredBody(state, 'status'), 'harvest', 'identity');
  const fields = ['instance', 'serial', 'account', 'profile'].map(
    (name) => id(own(identity, name)) ?? null,
  );
  return fields.every((field) => field === null) ? null : JSON.stringify(fields);
}

function battleId(state: DashboardState): string | null {
  return id(ownPath(answeredBody(state, 'battlesActive'), 'battle', 'id')) ?? null;
}

/** The reads an event between two states asks for (spec 7.2 "Also read"; F-108 to F-111). */
export function readTriggers(
  previous: DashboardState,
  next: DashboardState,
): readonly ResourceId[] {
  const wanted = new Set<ResourceId>();
  const before = new Map(previous.intents.map((record) => [record.id, record]));
  for (const record of contextIntents(next)) {
    const old = before.get(record.id);
    const answered = record.answerSeq !== null && record.answerSeq !== old?.answerSeq;
    const expired = record.lostReason === 'window_expired' && old?.lostReason !== 'window_expired';
    if (answered || expired) wanted.add('status');
    if (answered && (record.kind === 'input_off' || record.kind === 'input_on'))
      wanted.add('config');
    if (answered && (record.kind === 'run_now' || record.kind === 'live_controls'))
      wanted.add('battlesActive');
    // RUN-05: a saved-run save's answer is followed by a read of that run's saved settings.
    if (answered && record.kind === 'run_save') wanted.add('runControls');
  }
  // Profiles (P1, P2, P5): an account or profile write may change the page context. Its
  // answer, or the loss of its answer, is followed by the config read that names the
  // context (a new one starts a new epoch) and by the reads that settle it. A record of
  // another account on this emulator counts: the write itself may have switched accounts.
  for (const record of next.intents) {
    if (!profileWriteKind(record.kind)) continue;
    const old = before.get(record.id);
    const answered = record.answerSeq !== null && record.answerSeq !== old?.answerSeq;
    // F6P-06: any newly lost answer (the window ended, the connection failed or was cut)
    // asks for the settling reads once; the write itself is never sent again.
    const lost = record.lostReason !== null && record.lostReason !== old?.lostReason;
    if (answered || lost) for (const resource of PROFILE_READS) wanted.add(resource);
  }
  // Collection (C1 to C6): a settings save or a profile patch, answered or its answer lost,
  // is followed by the reads that show what is saved now (never by a resend).
  for (const record of contextIntents(next)) {
    if (
      record.kind !== 'settings_save' &&
      record.kind !== 'profile_patch' &&
      record.kind !== 'run_add'
    )
      continue;
    const old = before.get(record.id);
    const answered = record.answerSeq !== null && record.answerSeq !== old?.answerSeq;
    const lost = record.lostReason !== null && record.lostReason !== old?.lostReason;
    if (answered || lost) {
      for (const resource of COLLECTION_READS) wanted.add(resource);
      // RL9/RL12: active entry patches change the compiled catalogue too (labels,
      // disabled tabs). Never retain an earlier run label after its explicit clear.
      if (
        record.kind === 'profile_patch' &&
        record.meta.name === record.context?.profile &&
        record.meta.patchPath?.[0] === 'blueprints'
      )
        wanted.add('runs');
      if (record.kind === 'run_add') {
        wanted.add('runs');
        wanted.add('runControls');
      }
    }
  }
  // Emulators (M2 to M5): each request's answer, or the loss of its answer, is followed by
  // the reads that show what changed (never by a resend). An adopt rewrites the configured
  // address, so discovery is read again for its "In use" marks.
  for (const record of contextIntents(next).concat(adoptsOfThisInstance(next))) {
    const reads = EMULATOR_READS[record.kind];
    if (reads === undefined) continue;
    const old = before.get(record.id);
    const answered = record.answerSeq !== null && record.answerSeq !== old?.answerSeq;
    const lost = record.lostReason !== null && record.lostReason !== old?.lostReason;
    if (answered || lost) for (const resource of reads) wanted.add(resource);
  }
  // Recognition (R1 to R8): a launch, apply or stop answered, or its answer lost, is followed by
  // the status reads that show the operation (never by a resend).
  for (const record of contextIntents(next)) {
    if (!RECOGNITION_WRITES.includes(record.kind)) continue;
    const old = before.get(record.id);
    const answered = record.answerSeq !== null && record.answerSeq !== old?.answerSeq;
    const lost = record.lostReason !== null && record.lostReason !== old?.lostReason;
    if (answered || lost) {
      for (const resource of ['calibrate', 'calibrateOperation', 'status'] as const)
        wanted.add(resource);
      if (record.kind === 'calibrate_apply') wanted.add('runs');
    }
  }
  if (next.recognitionOpen && !previous.recognitionOpen) {
    for (const resource of [
      'config',
      'calibrate',
      'calibrateOperation',
      'status',
      'battlesActive',
      'runs',
    ] as const)
      wanted.add(resource);
  }
  if (next.runtimeOpen && !previous.runtimeOpen) {
    for (const resource of ['config', 'settings'] as const) wanted.add(resource);
  }
  if (next.equipmentOpen && !previous.equipmentOpen) {
    for (const resource of ['config', 'settings'] as const) wanted.add(resource);
  }
  if (next.emulatorsOpen && !previous.emulatorsOpen) {
    for (const resource of ['config', 'settings', 'wizardTools', 'wizardState'] as const)
      wanted.add(resource);
  }
  if (next.runsOpen && !previous.runsOpen) {
    wanted.add('runs');
    wanted.add('settings');
    wanted.add('profileSource');
    wanted.add('profileFiles');
    wanted.add('runTemplates');
    wanted.add('vocab');
  }
  if (next.collectionOpen && !previous.collectionOpen) {
    for (const resource of [...COLLECTION_READS, 'vocab', 'runs', 'runTemplates'] as const)
      wanted.add(resource);
  }
  if (next.profilesOpen && !previous.profilesOpen) {
    for (const resource of [...PROFILE_READS, 'wizardTools', 'wizardState'] as const)
      wanted.add(resource);
  }
  for (const record of contextIntents(next)) {
    const old = before.get(record.id);
    const answered = record.answerSeq !== null && record.answerSeq !== old?.answerSeq;
    if (
      answered &&
      (record.kind === 'start' || record.kind === 'start_gems' || record.kind === 'handoff')
    ) {
      wanted.add('runs');
    }
  }
  const identityBefore = identityKey(previous);
  const identityNow = identityKey(next);
  if (identityBefore !== null && identityNow !== null && identityBefore !== identityNow) {
    for (const resource of ['config', 'accounts', 'runs', 'wizardTools', 'wizardState'] as const) {
      wanted.add(resource);
    }
  }
  if (calibrationRunning(previous) && !calibrationRunning(next)) {
    for (const resource of ['config', 'accounts', 'runs', 'wizardTools', 'wizardState'] as const) {
      wanted.add(resource);
    }
  }
  const battleBefore = battleId(previous);
  const battleNow = battleId(next);
  if (battleBefore !== battleNow && previous.reads.battlesActive.lastAnswered !== null) {
    wanted.add('battles');
    if (battleNow !== null) wanted.add('battlesActive');
  }
  if (selectedRunOf(previous) !== selectedRunOf(next)) wanted.add('runControls');
  if (
    next.runsOpen &&
    (!previous.runsOpen ||
      runsPageRun(previous) !== runsPageRun(next) ||
      liveBattleRun(previous) !== liveBattleRun(next))
  )
    wanted.add('runControls');
  // F-026: the gem picker's run is read once when it changes; after any preview answer,
  // a needed preview not read yet is read next (each path is read once, failures included).
  if (gemPreviewRun(previous) !== gemPreviewRun(next)) wanted.add('runControls');
  if (
    next.runControls !== previous.runControls &&
    previewPaths(next).some((path) => next.runControls[path] === undefined)
  ) {
    wanted.add('runControls');
  }
  const targetPlan = selectReadPlan(next, 'targetSource');
  const previousTargetPlan = selectReadPlan(previous, 'targetSource');
  if (
    targetPlan.path !== null &&
    (targetPlan.path !== previousTargetPlan.path ||
      targetPlan.targetSelection?.generation !== previousTargetPlan.targetSelection?.generation)
  )
    wanted.add('targetSource');
  return [...wanted];
}
