import { restoreEditTargets, saveEditTargets } from '../api/editTargets.ts';
import { createPreviewData } from './previewWiring.ts';
import type { PreviewStore } from '../store/preview.ts';
import { isShoppingAction } from '../core/actions.ts';
import { setupPermit } from '../core/guards.ts';
import { sameJson } from '../core/json.ts';
import { handoffTarget } from '../core/needs.ts';
import { finite, own, ownPath, text } from '../core/read.ts';
import { startGate } from '../core/readiness.ts';
import { savedSaveAnswer } from '../core/runControls.ts';
import { runAddSource, runIdCheck } from '../core/runAdd.ts';
import { selectRunEntry } from '../store/selectors/runEntry.ts';
import { selectRunsAdd } from '../store/selectors/runsAdd.ts';
import { sendRunAdd } from '../api/intents/runAdd.ts';
import { sendRunEntryPatch } from '../api/intents/runEntry.ts';
import { editingSourceResource, selectEditingTarget } from '../store/selectors/editingTarget.ts';
import { collectionSavePending } from '../store/selectors/collection.ts';
import {
  contextKey,
  createContextTracker,
  sameContext,
  type ContextTracker,
  type PageContext,
} from '../api/context.ts';
import { createDiagnostics, type Diagnostics } from '../api/diagnostics.ts';
import {
  configRendering,
  createClickTimeContextRead,
  createClickTimeLaunchContextRead,
  createClickTimeTargetCheck,
  type LaunchContext,
} from '../api/clickContext.ts';
import { restoreDrafts, saveDrafts } from '../api/drafts.ts';
import { sendClearFlag } from '../api/intents/clearFlag.ts';
import { sendHandoff } from '../api/intents/handoff.ts';
import { clickGet, pauseClick, resolvePauseContext, sendTurnOn } from '../api/intents/harvest.ts';
import { sendInputOff, sendInputOn } from '../api/intents/input.ts';
import { sendKill } from '../api/intents/kill.ts';
import { createIntentMachine, type IntentMachine } from '../api/intents/machine.ts';
import { draftSettlement } from '../api/intents/draftSettlement.ts';
import { sendReconnect } from '../api/intents/reconnect.ts';
import { sendRunNow } from '../api/intents/routineAction.ts';
import { sendLiveReset, sendLiveSave, sendSavedSave } from '../api/intents/runs.ts';
import type { SendResult } from '../api/intents/send.ts';
import { sendCollect, startClick } from '../api/intents/start.ts';
import { sendStop } from '../api/intents/stop.ts';
import {
  createPoller,
  documentVisibility,
  GET_TIMEOUT_MS,
  type Poller,
  type ReadArrival,
  type Visibility,
} from '../api/poller.ts';
import { createSequence, type Sequence } from '../api/sequence.ts';
import { browserSessionStorage, type KeyValueStorage } from '../api/storage.ts';
import { browserTimers, randomId, type Timers } from '../api/timers.ts';
import { fetchTransport, type Transport } from '../api/transport.ts';
import { ENDPOINTS, type Endpoint, type EndpointId } from '../api/wire.ts';
import { readEndpoint } from '../api/client.ts';
import { selectShoppingLive } from '../store/selectors/shoppingLive.ts';
import { selectRunsPage } from '../store/selectors/runs.ts';
import { isSavedShoppingScope } from '../store/selectors/shoppingSaved.ts';
import { selectThisBattle } from '../store/selectors/thisBattle.ts';
import { settleCommand } from '../core/routines.ts';
import {
  activationCheck,
  profileFilesView,
  profileSourceView,
  profileWriteKind,
  unbindUnresolved,
  type ProfileSourceView,
} from '../core/profiles.ts';
import { sendProfileWrite } from '../api/intents/profiles.ts';
import { sendProfilePatch, sendSettingsSave } from '../api/intents/collection.ts';
import { sendEmulatorWrite } from '../api/intents/emulators.ts';
import { createRecognitionApplyReadback } from '../api/recognitionApplyReadback.ts';
import {
  sendCalibrateApply,
  sendCalibrateObserve,
  sendCalibrateStart,
  sendCalibrateStop,
} from '../api/intents/recognition.ts';
import { CONNECTION_WRITES } from '../core/emulators.ts';
import { opaqueToken } from '../core/tokens.ts';
import type {
  DashboardCommands,
  Fenced,
  ProfileWriteCommand,
  RunNowCommand,
} from '../store/commands.ts';
import {
  createDashboardStore,
  initialDashboardState,
  type DashboardStore,
} from '../store/dashboardStore.ts';
import { answeredBody, contextReadOf, latestRead } from '../store/reads.ts';
import { idleForConnectionChange } from '../core/connection.ts';
import { controlFacts, highestStopRevision } from '../store/selectors/controlFacts.ts';
import { selectOutcomes } from '../store/selectors/outcomes.ts';
import { emulatorSwitchPending } from '../store/selectors/emulatorSettings.ts';
import {
  currentSettingsView,
  settingsModuleVM,
  settingsSavePending,
} from '../store/selectors/settingsModule.ts';
import { selectPauseInputs } from '../store/selectors/pauseInputs.ts';
import { recognitionLaunchReason, selectRecognitionPage } from '../store/selectors/recognition.ts';
import { keptOperationScope, trackedOperation } from '../store/selectors/recognitionOperation.ts';
import { readTriggers, selectReadPlan } from '../store/selectors/readPlan.ts';
import type { DashboardState, LocalNotice } from '../store/types.ts';

/**
 * The Dashboard's data layer, wired (spec 7.1 app row): the store, the
 * poller, the context fence and the write-intent machine. Reads flow from the
 * poller into the store; the store's read plan and triggers flow back to the
 * poller; the machine owns the write records and the store mirrors them.
 * `commands` implements DashboardCommands with api/ intents: the only way a
 * write is ever sent is a call of one of them (a person's click). Nothing
 * here retries a write, and the only automatic POST of the page is the C-20
 * diagnostics (`diagnostics.report`).
 *
 * Mounting it in the page is plan task R10; tests drive it with a fake
 * transport, fake timers and in-memory session storage.
 */
export interface DashboardDataOptions {
  readonly transport?: Transport;
  readonly storage?: KeyValueStorage | null;
  readonly timers?: Timers;
  readonly visibility?: Visibility | null;
  readonly newId?: () => string;
  readonly newKey?: () => string;
  readonly endpoints?: Readonly<Record<EndpointId, Endpoint>>;
}

export interface DashboardData {
  readonly preview: PreviewStore;
  readonly store: DashboardStore;
  readonly poller: Poller;
  readonly machine: IntentMachine;
  readonly tracker: ContextTracker;
  readonly sequence: Sequence;
  readonly diagnostics: Diagnostics;
  readonly commands: DashboardCommands;
  readonly start: () => void;
  readonly stop: () => void;
}

/** A read stamped with the epoch it belongs to (the config answer that names a new context). */
const inEpoch = (arrival: ReadArrival, epoch: number): ReadArrival => ({ ...arrival, epoch });

/** A captured live command still addresses the exact target offered by the newest selector. */
function sameLiveTarget(
  current:
    | Pick<RunNowCommand, 'operationId' | 'battleId' | 'generation' | 'expectedRevision'>
    | null
    | undefined,
  captured: Pick<RunNowCommand, 'operationId' | 'battleId' | 'generation' | 'expectedRevision'>,
): boolean {
  return (
    current !== null &&
    current !== undefined &&
    current.operationId === captured.operationId &&
    current.battleId === captured.battleId &&
    current.generation === captured.generation &&
    current.expectedRevision === captured.expectedRevision
  );
}

export function createDashboardData(options: DashboardDataOptions = {}): DashboardData {
  const transport = options.transport ?? fetchTransport;
  const storage = options.storage === undefined ? browserSessionStorage() : options.storage;
  const timers = options.timers ?? browserTimers;
  const visibility = options.visibility === undefined ? documentVisibility() : options.visibility;
  const newKey = options.newKey ?? randomId;
  const sequence = createSequence();
  const tracker = createContextTracker(storage);
  const machine = createIntentMachine({
    transport,
    sequence,
    storage,
    timers,
    context: tracker.current(),
    epoch: tracker.epoch(),
    ...(options.newId === undefined ? {} : { newId: options.newId }),
    ...(options.endpoints === undefined ? {} : { endpoints: options.endpoints }),
  });
  const restoredEditTargets = restoreEditTargets(storage);
  const store = createDashboardStore({
    ...initialDashboardState({
      nowMs: timers.now(),
      context: tracker.current(),
      hidden: visibility?.hidden() === true,
    }),
    intents: machine.snapshot(),
    // F6R-02: the drafts this tab kept, before any read is judged against them.
    runDrafts: restoreDrafts(storage),
    ...restoredEditTargets,
  });
  const diagnostics = createDiagnostics(transport, options.endpoints);
  const savedApplyReadback = createRecognitionApplyReadback(machine, transport, tracker, timers);

  const notice = (kind: LocalNotice['kind'], reason: string) => {
    store.dispatch({
      type: 'notice',
      notice: { kind, reason, atSeq: sequence.next(), atMs: timers.now() },
    });
  };

  const syncContext = () => {
    store.dispatch({
      type: 'context',
      context: tracker.current(),
      epoch: tracker.epoch(),
      confirmed: tracker.confirmed(),
      sessionContext: tracker.sessionRecorded(),
    });
  };

  let poller: Poller | null = null;

  const deliver = (arrival: ReadArrival) => {
    if (arrival.resource === 'config' && arrival.outcome.kind === 'answered') {
      const change = tracker.observeConfig(arrival.outcome.body);
      if (change.kind !== 'unchanged' || !store.getSnapshot().contextConfirmed) {
        machine.setContext(tracker.current(), tracker.epoch());
        syncContext();
      }
      if (change.kind === 'changed' || change.kind === 'established') {
        // Every read and fact from before this context is dropped and read again (S-014).
        poller?.contextChanged();
        // The answer that named the new context belongs to it.
        store.dispatch({ type: 'read', read: inEpoch(arrival, tracker.epoch()) });
        return;
      }
    }
    store.dispatch({ type: 'read', read: arrival });
  };

  const activePoller = createPoller({
    transport,
    sequence,
    epoch: tracker.epoch,
    plan: (resource) => selectReadPlan(store.getSnapshot(), resource),
    deliver,
    timers,
    ...(visibility === null ? {} : { visibility }),
    ...(options.endpoints === undefined ? {} : { endpoints: options.endpoints }),
    onClock: () => {
      store.dispatch({ type: 'clock', nowMs: timers.now() });
    },
    onVisibility: (hidden) => {
      store.dispatch({ type: 'visibility', hidden });
      // B-05: the clock paused while hidden; on return every read's age is judged at once,
      // so an expired observation is Unknown until the forced reads answer.
      store.dispatch({ type: 'clock', nowMs: timers.now() });
    },
  });
  poller = activePoller;

  let previous: DashboardState = store.getSnapshot();
  const onStore = () => {
    const next = store.getSnapshot();
    const before = previous;
    previous = next;
    for (const resource of readTriggers(before, next)) activePoller.read(resource);
    activePoller.replan();
    savedApplyReadback.check(next.reads.calibrate.latest?.startedSeq ?? 0);
  };
  const onMachine = () => {
    store.dispatch({ type: 'intents', records: machine.snapshot() });
  };

  /**
   * R8-06: a settlement a later read proved (store/fences.ts) is handed to the machine, which
   * keeps it with the record (and in session storage), so neither a later failed read nor a
   * reload offers the resend again. This never sends anything.
   */
  const settleRecords = () => {
    const state = store.getSnapshot();
    for (const record of machine.snapshot()) {
      const kept = state.readSettlements[record.id]?.settlement ?? record.readSettlement;
      if (
        kept !== null &&
        record.phase === 'unconfirmed' &&
        (record.readSettlement === null || draftSettlement(record, kept) === 'applied')
      ) {
        machine.settleByRead(record.id, kept);
      }
    }
  };
  /**
   * RUN-03, SR-2, F6R-02: a Runs save carries the draft scope and the raw values it was
   * made from in its meta (kept with the record in session storage, so a reload keeps
   * them). The draft is cleared only when that save is confirmed (its answer, or a later
   * read proving the saved settings hold its values), once per record, and then only where
   * it still holds exactly what was sent: anything typed since stays. A refused or unknown
   * save keeps the draft.
   */
  const draftsCleared = new Set<string>();
  const clearSavedDrafts = () => {
    const state = store.getSnapshot();
    for (const record of machine.snapshot()) {
      const { draftScope, draft } = record.meta;
      if (draftScope === undefined || draft === undefined || draftsCleared.has(record.id)) continue;
      const settled = state.readSettlements[record.id]?.settlement ?? record.readSettlement;
      if (draftSettlement(record, settled) !== 'applied') continue;
      // F6-12 SQ7 (LIVE-INT-01): a live queue change clears its submitted values at its
      // receipt, as a routine change does: the battle read itself says it is requested and
      // waiting (requested_controls against applied_controls), which a reload keeps, while a
      // confirmed record is not kept across a reload. Newer typing stays.
      draftsCleared.add(record.id);
      // SQ10: positional directive keys need their original base and order. If a newer
      // library edit remains, retain the complete draft for explicit revision reconciliation.
      if (
        draftScope.startsWith('runs:shopping-library:') &&
        Object.entries(state.runDrafts[draftScope] ?? {}).some(
          ([key, value]) => draft[key] !== value,
        )
      )
        continue;
      // SR-2: a save the server kept only in part clears only the values it holds.
      const answer =
        record.kind === 'run_save' && record.answer !== null && record.phase === 'confirmed'
          ? savedSaveAnswer(record.answer, record.meta)
          : null;
      const kept =
        answer?.kind === 'saved_partly'
          ? Object.fromEntries(Object.entries(draft).filter(([path]) => answer.kept.includes(path)))
          : draft;
      // F6-12-C-01: a saved queue draft whose newer edits remain keeps the structure they are
      // read against (its staged base and order) until a later save or Discard: a run without a
      // saved plan has no read base to fall back on while its follow-up read is missing.
      const newer = Object.entries(state.runDrafts[draftScope] ?? {}).some(
        ([key, value]) => draft[key] !== value,
      );
      const only =
        newer && isSavedShoppingScope(draftScope)
          ? Object.fromEntries(
              Object.entries(kept).filter(([path]) => path !== 'base' && path !== 'order'),
            )
          : kept;
      store.dispatch({ type: 'draft_discard', scope: draftScope, only });
      if (
        record.kind === 'run_add' &&
        sameContext(state.context, record.context) &&
        record.meta.blueprint !== undefined
      ) {
        store.dispatch({ type: 'runs_open', open: state.runsOpen, run: record.meta.blueprint });
      }
    }
  };
  /**
   * R1: the operation a launch of the Recognition page started is kept for its context (a
   * draft scope, so a reload follows the same operation and never starts another).
   */
  const keepRecognitionOperation = () => {
    const state = store.getSnapshot();
    const tracked = trackedOperation(state);
    const scope = keptOperationScope(state);
    if (tracked?.source !== 'launch' || scope === null) return;
    if (state.runDrafts[scope]?.['id'] === tracked.id) return;
    store.dispatch({ type: 'draft_edit', scope, path: 'id', value: tracked.id });
  };
  /** F6R-02: the drafts are written to session storage whenever they change. */
  let storedDrafts = store.getSnapshot().runDrafts;
  let storedEditTargetGeneration = store.getSnapshot().editTargetGeneration;
  const persistDrafts = () => {
    const targets = store.getSnapshot().editTargets;
    const generation = store.getSnapshot().editTargetGeneration;
    if (generation !== storedEditTargetGeneration) {
      storedEditTargetGeneration = generation;
      saveEditTargets(storage, targets, generation);
    }
    const drafts = store.getSnapshot().runDrafts;
    if (drafts === storedDrafts) return;
    storedDrafts = drafts;
    saveDrafts(storage, drafts);
  };

  /**
   * P5: read one profile's source (a GET with the 5 s read limit) and keep the answer for
   * this context; returns what it shows (null: not readable).
   */
  const checkProfileSource = async (name: string): Promise<ProfileSourceView | null> => {
    const epoch = tracker.epoch();
    const startedSeq = sequence.next();
    const controller = new AbortController();
    const timeout = timers.setTimeout(() => {
      controller.abort();
    }, GET_TIMEOUT_MS);
    const outcome = await readEndpoint(
      transport,
      (options.endpoints ?? ENDPOINTS).profileSource,
      `/api/profile-src/${encodeURIComponent(name)}`,
      controller.signal,
    );
    timers.clearTimeout(timeout);
    const answer = outcome.kind === 'answered' ? outcome.answer : outcome.answer;
    const body = outcome.kind === 'answered' && answer?.status === 200 ? answer.body : null;
    store.dispatch({
      type: 'profile_checked',
      epoch,
      check: {
        name,
        startedSeq,
        body,
        error: answer === null ? null : (text(own(answer.body, 'error')) ?? null),
      },
    });
    return tracker.epoch() === epoch ? profileSourceView(body) : null;
  };

  /** The live subscriptions: made by start, undone by stop, so a page can start again (React StrictMode). */
  let unsubscribe: (() => void)[] = [];

  /** S-014, S-101: a command drawn in another context epoch sends nothing. */
  const fenced = (command: Fenced): boolean => {
    if (command.epoch === store.getSnapshot().epoch && command.epoch === tracker.epoch()) {
      return true;
    }
    notice('command_not_sent', 'context_changed');
    return false;
  };

  /** S-101: the handoff target on screen must still be the one the newest status read names. */
  const handoffStillTargeted = (target: {
    readonly operationId?: string;
    readonly setupBattleId?: string;
    readonly evidenceRevision?: string;
  }): boolean => {
    const read = latestRead(store.getSnapshot(), 'status');
    const body = read?.outcome.kind === 'answered' ? read.outcome.body : undefined;
    const current = handoffTarget(body, read === null ? Number.NaN : read.arrivedAtMs / 1000);
    return (
      current !== null &&
      current.operationId === target.operationId &&
      current.setupBattleId === target.setupBattleId &&
      current.evidenceRevision === target.evidenceRevision
    );
  };

  const report = (result: SendResult) => {
    if (result.kind === 'not_sent') notice('command_not_sent', result.reason);
  };

  /**
   * Collection (C1 to C6): a settings save or profile patch of this context still in
   * flight. Both revisions move with either save, so one at a time (a second waits; it is
   * never queued or sent on its own later).
   */
  /** Contexts with a profile patch click still at its click-time config read (F6C-02). */
  const collectionChecks = new Set<string>();
  const collectionWriteInFlight = (context: PageContext): boolean =>
    machine
      .snapshot()
      .some(
        (record) =>
          (record.kind === 'settings_save' ||
            record.kind === 'profile_patch' ||
            record.kind === 'run_add') &&
          record.phase === 'sending' &&
          sameContext(record.context, context),
      );

  /** The page context Start and Collect compare against (core/readiness.ts): the config's five fields. */
  const launchContext = () => {
    const current = tracker.current();
    if (current === null) return null;
    const rendering = configRendering(
      answeredBody(store.getSnapshot(), 'config'),
      current.instance,
    );
    return { ...current, rendering };
  };

  /**
   * A GET /api/config started now (never joined to a read already in flight), with the
   * 5 s read limit: the complete context its answer names, else null (no answer, not 200,
   * or a body that does not name the emulator, its serial and the account and profile).
   */
  const readClickTimeContext = createClickTimeContextRead(
    transport,
    (options.endpoints ?? ENDPOINTS).config,
    timers,
  );
  /** The same click-time read, with the rendering descriptor a calibrator launch is fenced by. */
  const readClickTimeLaunchContext = createClickTimeLaunchContextRead(
    transport,
    (options.endpoints ?? ENDPOINTS).config,
    timers,
  );
  /**
   * F6M-03: one connection request at a time (adopt, launch, prepare, tools install,
   * reconnect, core CONNECTION_WRITES): each may change what the next one acts on. A
   * Reconnect whose click-time config read is still running holds the others too.
   */
  let reconnectChecking = false;
  /** A Recognition launch whose click-time reads are still running. */
  let recognitionChecking = false;
  /**
   * F6M-C-02: the shared runner gate (core idleForConnectionChange) checked again at the
   * final send boundary, after any asynchronous preflight and at a confirmation: a status
   * read listing a runner (or none readable) sends nothing, whatever the button showed.
   */
  const runnerGate = (): 'ready' | 'process_list_unavailable' | 'process_listed' =>
    idleForConnectionChange(contextReadOf(latestRead(store.getSnapshot(), 'status')));
  const connectionWriteInFlight = (): boolean =>
    reconnectChecking ||
    machine
      .snapshot()
      .some((record) => CONNECTION_WRITES.includes(record.kind) && record.phase === 'sending');
  /** Emulators with a Profiles click whose click-time checks are still running. */
  const profileChecks = new Set<string>();
  const unbindBlocked = (command: ProfileWriteCommand): boolean => {
    if (command.action.kind !== 'profile_activate' || command.action.name !== null) return false;
    const state = store.getSnapshot();
    return machine
      .snapshot()
      .some(
        (record) =>
          record.context?.instance === command.context.instance &&
          record.context.serial === command.context.serial &&
          unbindUnresolved(
            record,
            state.readSettlements[record.id]?.settlement ?? record.readSettlement,
          ),
      );
  };
  /**
   * The click-time checks of one Profiles write, then its one send. Defined below the
   * source check it uses; called only from `commands.profileWrite`.
   */
  const checkThenSendProfileWrite = async (command: ProfileWriteCommand): Promise<void> => {
    // A config read started after this click must name the reviewed context. The POST
    // also carries that context so the backend can refuse a later binding change.
    const named = await readClickTimeContext();
    if (named === null) {
      notice('command_not_sent', 'context_unconfirmed');
      return;
    }
    if (!fenced(command)) return;
    if (!sameContext(named, command.context) || !sameContext(tracker.current(), command.context)) {
      // The page first catches up with the server's binding through its own config read
      // (a new context clears older notices), then says why nothing was sent.
      await activePoller.readNow('config');
      notice('command_not_sent', 'context_changed');
      return;
    }
    // F6P-04: a copy or promotion never replaces the profile the fresh read names active.
    if (
      (command.action.kind === 'profile_copy' || command.action.kind === 'profile_promote') &&
      command.action.name === named.profile
    ) {
      notice('command_not_sent', 'destination_active');
      return;
    }
    // P5: activation only after a fresh read of that profile shows no problem. An unbind
    // (F6-14 PE1, name null) names no profile to check; the server fences it by context.
    if (command.action.kind === 'profile_activate' && command.action.name !== null) {
      const { name } = command.action;
      const check = await checkProfileSource(name);
      if (!fenced(command)) return;
      const file = profileFilesView(answeredBody(store.getSnapshot(), 'profileFiles'))?.files.find(
        (entry) => entry.name === name,
      );
      if (activationCheck(file ?? null, check).kind !== 'ready') {
        notice('command_not_sent', 'profile_not_valid');
        return;
      }
    }
    if (unbindBlocked(command)) {
      notice('command_not_sent', 'unbind_unresolved');
      return;
    }
    const result = sendProfileWrite(machine, command.action, command.context, command.draft);
    if (result.kind === 'in_flight') notice('command_not_sent', 'profile_write_in_flight');
    report(result);
  };
  const commands: DashboardCommands = {
    pause: async () => {
      const inputs = selectPauseInputs(store.getSnapshot());
      const known = resolvePauseContext(inputs.config, inputs.readableHarvest, inputs.session);
      const result = await pauseClick(
        {
          machine,
          newKey,
          readConfig: async () => {
            const arrival = await activePoller.readNow('config');
            return arrival?.outcome.kind === 'answered' ? arrival.outcome.body : undefined;
          },
          readHarvest: clickGet(transport, 'harvest', options.endpoints),
          currentContext: tracker.current,
        },
        known,
      );
      if (result.kind === 'not_sent') {
        notice('pause_not_sent', result.reason);
      } else {
        store.dispatch({ type: 'clear_notice', kind: 'pause_not_sent' });
      }
    },
    turnOn: (command) => {
      const context = tracker.current();
      if (!fenced(command)) return Promise.resolve();
      if (context === null || !tracker.confirmed()) {
        notice('command_not_sent', 'context_unknown');
      } else {
        report(sendTurnOn(machine, { ...command, context }, newKey));
      }
      return Promise.resolve();
    },
    turnInputOff: () => {
      report(sendInputOff(machine, tracker.current()));
      return Promise.resolve();
    },
    allowInput: () => {
      report(sendInputOn(machine, tracker.current()));
      return Promise.resolve();
    },
    stop: (command) => {
      if (!fenced(command)) return Promise.resolve();
      if (command.kind === 'cancel') {
        // S-050: the highest revision this context knows for the id at the click.
        const known = highestStopRevision(controlFacts(store.getSnapshot()), command.operationId);
        report(
          sendStop(
            machine,
            {
              operationId: command.operationId,
              expectedRevision: Math.max(command.expectedRevision, known ?? 0),
            },
            tracker.current(),
          ),
        );
      } else {
        report(sendKill(machine, command, tracker.current()));
      }
      return Promise.resolve();
    },
    clearFlag: (command) => {
      if (!fenced(command)) return Promise.resolve();
      report(sendClearFlag(machine, command, tracker.current()));
      return Promise.resolve();
    },
    handoff: (command) => {
      const context = tracker.current();
      if (!fenced(command)) return Promise.resolve();
      if (context === null || !tracker.confirmed()) {
        notice('command_not_sent', 'context_unknown');
      } else if (!handoffStillTargeted(command)) {
        notice('command_not_sent', 'handoff_target_changed');
      } else {
        report(sendHandoff(machine, command, context));
      }
      return Promise.resolve();
    },
    start: async (command) => {
      if (!fenced(command)) return;
      const context = launchContext();
      if (context === null || !tracker.confirmed()) {
        notice('start_not_sent', 'context_unknown');
        return;
      }
      const status = answeredBody(store.getSnapshot(), 'status');
      const serverTime = finite(ownPath(status, 'harvest', 'server_time')) ?? Number.NaN;
      const permit = setupPermit(own(status, 'setup_battle_guard'), serverTime);
      const result = await startClick(
        { machine, transport, newKey, epoch: tracker.epoch },
        {
          preset: command.preset,
          context,
          gate: (read) =>
            startGate(permit.kind === 'known' ? permit.value : null, read, context, command.preset),
        },
      );
      if (result.kind === 'not_sent') {
        notice('start_not_sent', result.reason);
      } else {
        store.dispatch({ type: 'clear_notice', kind: 'start_not_sent' });
      }
    },
    collect: (command) => {
      if (!fenced(command)) return Promise.resolve();
      const context = launchContext();
      if (context === null || !tracker.confirmed()) {
        notice('command_not_sent', 'context_unknown');
      } else {
        report(
          sendCollect(machine, { preset: command.preset, expected: context, context }, newKey),
        );
      }
      return Promise.resolve();
    },
    reconnect: async (command) => {
      if (!fenced(command)) return;
      if (connectionWriteInFlight()) {
        notice('command_not_sent', 'emulator_write_in_flight');
        return;
      }
      if (runnerGate() !== 'ready') {
        notice('command_not_sent', runnerGate());
        return;
      }
      // F6M-02: the address shown is the one connected, and only while a config read made
      // for this click still names the context the page showed.
      reconnectChecking = true;
      let named: PageContext | null;
      try {
        named = await readClickTimeContext();
      } finally {
        reconnectChecking = false;
      }
      if (!fenced(command)) return;
      if (
        named === null ||
        named.serial !== command.serial ||
        !sameContext(named, command.context) ||
        !sameContext(tracker.current(), command.context)
      ) {
        notice('command_not_sent', 'context_changed');
        return;
      }
      // F6M-C-02: a runner listed while the config read was in flight stops the send.
      if (runnerGate() !== 'ready') {
        notice('command_not_sent', runnerGate());
        return;
      }
      report(sendReconnect(machine, command.context, command.serial));
    },
    runNow: (command) => {
      if (!fenced(command)) return Promise.resolve();
      const battle = selectThisBattle(store.getSnapshot());
      const current = isShoppingAction(command.action)
        ? battle.tryOnce.find((item) => item.action === command.action)?.button
        : battle.actions === null
          ? battle.routines.find((item) => item.routine === command.action)?.runNow
          : battle.actions.find((item) => item.token === command.action)?.runNow;
      if (current?.enabled !== true || !sameLiveTarget(current.command, command)) {
        notice('command_not_sent', current?.reason ?? 'battle_changed');
        return Promise.resolve();
      }
      report(sendRunNow(machine, command, tracker.current(), newKey));
      return Promise.resolve();
    },
    resend: (intentId, confirmedAgain) => {
      // Only a line that offers it may be resent: unknown, not replaced, not settled by a later read.
      const line = selectOutcomes(store.getSnapshot()).lines.find((entry) => entry.id === intentId);
      if (line?.resendOffered !== true) {
        notice('command_not_sent', 'resend_not_offered');
        return Promise.resolve();
      }
      const record = machine.snapshot().find((entry) => entry.id === intentId);
      if (record?.kind === 'handoff' && !handoffStillTargeted(record.meta)) {
        // S-101: a new confirmation binds only the target the newest read still names.
        notice('command_not_sent', 'handoff_target_changed');
        return Promise.resolve();
      }
      const refused = machine.resend(
        intentId,
        confirmedAgain === undefined ? {} : { confirmedAgain },
      );
      if (refused !== null) notice('command_not_sent', refused);
      return Promise.resolve();
    },
    dismiss: (intentId) => {
      machine.dismiss(intentId);
    },
    acceptCurrent: (intentId) => {
      machine.accept(intentId);
    },
    checkStatus: () => {
      activePoller.read('status');
    },
    checkSetupStatus: () => {
      activePoller.read('calibrate');
    },
    selectRun: (run) => {
      store.dispatch({ type: 'select_run', run });
    },
    selectGemSource: (run) => {
      store.dispatch({ type: 'select_gem_source', run });
    },
    openRuns: (open, run) => {
      store.dispatch({ type: 'runs_open', open, run });
    },
    editDraft: (scope, path, value) => {
      store.dispatch({ type: 'draft_edit', scope, path, value });
    },
    discardDraft: (scope) => {
      store.dispatch({ type: 'draft_discard', scope });
    },
    saveRun: (command) => {
      if (!fenced(command)) return Promise.resolve();
      const result = sendSavedSave(
        machine,
        {
          blueprint: command.blueprint,
          context: command.context,
          expectedRevision: command.expectedRevision,
          changes: command.changes,
          draftScope: command.scope,
          draft: command.draft,
        },
        tracker.current(),
      );
      report(result);
      return Promise.resolve();
    },
    saveLive: (command) => {
      if (!fenced(command)) return Promise.resolve();
      if (own(command.changes, 'shopping_plan') !== undefined) {
        const current = selectShoppingLive(store.getSnapshot());
        if (!sameLiveTarget(current.command, command)) {
          notice('command_not_sent', current.reason ?? 'battle_changed');
          return Promise.resolve();
        }
      } else {
        const state = store.getSnapshot();
        // Judge the reserved submission against the latest reading, without replacing it
        // with (or rejecting it because of) text entered after the command was captured.
        const current = selectRunsPage({
          ...state,
          runDrafts: { ...state.runDrafts, [command.scope]: command.draft },
        }).live.editor?.save;
        if (
          current?.enabled !== true ||
          !sameLiveTarget(current.command, command) ||
          !sameJson(current.command?.changes, command.changes)
        ) {
          notice('command_not_sent', current?.reason ?? 'battle_changed');
          return Promise.resolve();
        }
      }
      const result = sendLiveSave(
        machine,
        {
          operationId: command.operationId,
          battleId: command.battleId,
          generation: command.generation,
          expectedRevision: command.expectedRevision,
          changes: command.changes,
          draftScope: command.scope,
          draft: command.draft,
          battleLabel: command.battleLabel,
        },
        tracker.current(),
        newKey,
      );
      report(result);
      return Promise.resolve();
    },
    resetLive: (command) => {
      if (!fenced(command)) return Promise.resolve();
      const editor = selectRunsPage(store.getSnapshot()).live.editor;
      const current =
        command.paths.length === 1
          ? editor?.resets.find((item) => item.path === command.paths[0])?.reset
          : editor?.resetAll?.reset;
      if (
        current?.enabled !== true ||
        !sameLiveTarget(current.command, command) ||
        !sameJson(current.command?.paths, command.paths)
      ) {
        notice('command_not_sent', current?.reason ?? 'battle_changed');
        return Promise.resolve();
      }
      report(
        sendLiveReset(
          machine,
          {
            operationId: command.operationId,
            battleId: command.battleId,
            generation: command.generation,
            expectedRevision: command.expectedRevision,
            paths: command.paths,
            battleLabel: command.battleLabel,
          },
          tracker.current(),
          newKey,
        ),
      );
      return Promise.resolve();
    },
    checkRun: () => {
      activePoller.read('runControls');
    },
    checkRuns: () => {
      activePoller.read('runs');
    },
    checkBattle: () => {
      activePoller.read('battlesActive');
    },
    openEmulators: (open) => {
      store.dispatch({ type: 'emulators_open', open });
    },
    openRuntime: (open) => {
      store.dispatch({ type: 'runtime_open', open });
    },
    openEquipment: (open) => {
      store.dispatch({ type: 'equipment_open', open });
    },
    openRecognition: (open) => {
      store.dispatch({ type: 'recognition_open', open });
    },
    selectRecognitionRun: (run) => {
      store.dispatch({ type: 'recognition_run', run });
    },
    checkRecognition: () => {
      activePoller.read('calibrate');
      activePoller.read('calibrateOperation');
    },
    checkRecognitionReadiness: async () => {
      const run = store.getSnapshot().recognitionRun;
      if (run === null) return;
      const epoch = tracker.epoch();
      const startedSeq = sequence.next();
      const readiness = { run, startedSeq };
      store.dispatch({
        type: 'recognition_readiness',
        epoch,
        readiness: { ...readiness, atMs: timers.now(), outcome: { kind: 'checking' } },
      });
      const controller = new AbortController();
      const timeout = timers.setTimeout(() => {
        controller.abort();
      }, GET_TIMEOUT_MS);
      const outcome = await readEndpoint(
        transport,
        (options.endpoints ?? ENDPOINTS).readiness,
        `/api/readiness?preset=${encodeURIComponent(run)}`,
        controller.signal,
      );
      timers.clearTimeout(timeout);
      // R4: the answer as sent (a refusal names its reason); a failure stays one, never "ready".
      store.dispatch({
        type: 'recognition_readiness',
        epoch,
        readiness: {
          ...readiness,
          atMs: timers.now(),
          outcome:
            outcome.kind === 'answered'
              ? { kind: 'answered', status: outcome.answer.status, body: outcome.answer.body }
              : { kind: 'failed', reason: outcome.reason },
        },
      });
    },
    recognitionLaunch: async (command) => {
      if (!fenced(command)) return;
      if (!tracker.confirmed() || !sameContext(tracker.current(), command.context)) {
        notice('command_not_sent', 'context_changed');
        return;
      }
      // One connection or recognition request at a time, click-time checks included.
      if (connectionWriteInFlight() || recognitionChecking) {
        notice('command_not_sent', 'launch_in_flight');
        return;
      }
      // R1, CLAUDE hard rules 1, 3, 8: the click reads the context, the device's runners, any
      // tracked battle and the calibrator now, and judges the launch against those reads.
      recognitionChecking = true;
      let named: LaunchContext | null;
      let safety: readonly (ReadArrival | null)[];
      try {
        const [context, status, calibrate, battle] = await Promise.all([
          readClickTimeLaunchContext(),
          activePoller.readNow('status'),
          activePoller.readNow('calibrate'),
          activePoller.readNow('battlesActive'),
        ]);
        named = context;
        // A capture never taps: it does not wait on the battle read.
        safety =
          command.launch.kind === 'start' ? [status, calibrate, battle] : [status, calibrate];
      } finally {
        recognitionChecking = false;
      }
      if (!fenced(command)) return;
      // F6R10-R1-01: every safety read of this click answered; an older answer never permits.
      if (
        safety.some(
          (arrival) =>
            arrival === null ||
            arrival.outcome.kind !== 'answered' ||
            arrival.outcome.status !== 200,
        )
      ) {
        notice('command_not_sent', 'safety_read_failed');
        return;
      }
      if (
        named === null ||
        !sameContext(named, command.context) ||
        !sameContext(tracker.current(), command.context)
      ) {
        notice('command_not_sent', 'context_changed');
        return;
      }
      const reason = recognitionLaunchReason(store.getSnapshot(), command.launch);
      if (reason !== null) {
        notice('command_not_sent', reason);
        return;
      }
      // The expected context (rendering included) is the one this click's config read named.
      const expected = named;
      const launch = command.launch;
      report(
        launch.kind === 'start'
          ? sendCalibrateStart(machine, launch.plan, expected, command.context, newKey)
          : sendCalibrateObserve(
              machine,
              { watch: launch.watch, run: launch.run },
              expected,
              command.context,
              newKey,
            ),
      );
    },
    recognitionApply: async (command) => {
      if (!fenced(command)) return;
      if (!sameContext(tracker.current(), command.context)) {
        notice('command_not_sent', 'context_changed');
        return;
      }
      if (recognitionChecking) {
        notice('command_not_sent', 'apply_checking');
        return;
      }
      // F6R10-R1-03: an apply writes the profile the server has active at that moment (the
      // saved-observations path names no operation): a config read made for this click must
      // still name the emulator, account and profile the page showed.
      recognitionChecking = true;
      let named: PageContext | null;
      let savedEvidence: Readonly<Record<string, unknown>> | undefined;
      try {
        const observations = ownPath(
          answeredBody(store.getSnapshot(), 'calibrate'),
          'state',
          'player',
        );
        if (
          command.apply.kind === 'saved' &&
          command.context.profile !== null &&
          observations !== undefined
        ) {
          savedEvidence = {
            before: await savedApplyReadback.readProfile(command.context.profile),
            observations,
          };
        }
        named = await readClickTimeContext();
      } finally {
        recognitionChecking = false;
      }
      if (!fenced(command)) return;
      if (
        named === null ||
        !sameContext(named, command.context) ||
        !sameContext(tracker.current(), command.context)
      ) {
        notice('command_not_sent', 'context_changed');
        return;
      }
      // The apply the page offers now, for the same operation and revision; else nothing.
      const page = selectRecognitionPage(store.getSnapshot());
      const offered =
        command.apply.kind === 'saved' ? page.savedApply : (page.operation?.apply ?? null);
      const now = offered?.command?.apply;
      const same =
        offered?.enabled === true &&
        now !== undefined &&
        (now.kind === 'saved'
          ? command.apply.kind === 'saved'
          : command.apply.kind === 'operation' &&
            now.operationId === command.apply.operationId &&
            now.expectedRevision === command.apply.expectedRevision);
      if (!same) {
        notice('command_not_sent', offered?.reason ?? 'not_awaiting_apply');
        return;
      }
      report(sendCalibrateApply(machine, command.apply, named, command.context, savedEvidence));
    },
    recognitionStop: (command) => {
      if (!fenced(command)) return Promise.resolve();
      if (!sameContext(tracker.current(), command.context)) {
        notice('command_not_sent', 'context_changed');
        return Promise.resolve();
      }
      report(
        sendCalibrateStop(
          machine,
          { operationId: command.operationId, expectedRevision: command.expectedRevision },
          command.context,
        ),
      );
      return Promise.resolve();
    },
    rescanEmulators: () => {
      activePoller.read('wizardEmulators');
    },
    checkSettings: () => {
      // C15-4: settings cannot verify a context transition. Recovery must read config
      // first so the subsequent settings read belongs to the verified current epoch.
      if (emulatorSwitchPending(store.getSnapshot())) {
        void activePoller.readNow('config').then(() => {
          activePoller.read('settings');
        });
        return;
      }
      activePoller.read('settings');
    },
    checkTools: () => {
      activePoller.read('wizardTools');
    },
    checkResolution: async (serial) => {
      const epoch = tracker.epoch();
      const startedSeq = sequence.next();
      const controller = new AbortController();
      const timeout = timers.setTimeout(() => {
        controller.abort();
      }, GET_TIMEOUT_MS);
      const outcome = await readEndpoint(
        transport,
        (options.endpoints ?? ENDPOINTS).resolution,
        `/api/wizard/resolution?serial=${encodeURIComponent(serial)}`,
        controller.signal,
      );
      timers.clearTimeout(timeout);
      // An answer of any status is shown as sent (a refusal names its reason); a failure
      // is kept as one (F6M-07), never as an empty result.
      const answer = outcome.answer;
      store.dispatch({
        type: 'resolution_checked',
        epoch,
        check: {
          serial,
          startedSeq,
          body: answer?.body ?? null,
          result:
            outcome.kind === 'answered'
              ? { kind: 'answered', status: outcome.answer.status }
              : { kind: 'failed', reason: outcome.reason, status: answer?.status ?? null },
          atMs: timers.now(),
        },
      });
    },
    emulatorWrite: async (command) => {
      if (!fenced(command)) return;
      // F6M-03: one connection request at a time, Reconnect included.
      if (connectionWriteInFlight()) {
        notice('command_not_sent', 'emulator_write_in_flight');
        return;
      }
      // F6M-C-02: the backend refuses these while a runner is alive; so does this boundary.
      if (runnerGate() !== 'ready') {
        notice('command_not_sent', runnerGate());
        return;
      }
      report(sendEmulatorWrite(machine, command.action, command.context));
      await Promise.resolve();
    },
    openCollection: (open) => {
      store.dispatch({ type: 'collection_open', open });
    },
    checkCollection: () => {
      activePoller.read('settings');
      activePoller.read('profileSource');
      // F6-14: the editing target's own source, when it is not the active profile (the read
      // plan names no path otherwise, and nothing is read).
      activePoller.read('targetSource');
      activePoller.read('runTemplates');
      activePoller.read('vocab');
    },
    saveSettings: async (command) => {
      if (!fenced(command)) return;
      // Rebuild the offer from the current read and the pinned raw draft at both
      // boundaries. A captured command cannot borrow a newer revision or value.
      const stillOffered = () => {
        const state = store.getSnapshot();
        const view = currentSettingsView(state);
        const profile =
          view?.modules.find((module) => module.id === command.module)?.scope === 'profile';
        const pending =
          settingsSavePending(state, profile ? ['settings', 'profileSource'] : ['settings']) ||
          emulatorSwitchPending(state);
        const offered = settingsModuleVM(state, view, command.module, { pending })?.save.command;
        return (
          offered !== undefined &&
          offered !== null &&
          offered.epoch === command.epoch &&
          offered.revision === command.revision &&
          sameContext(offered.context, command.context) &&
          JSON.stringify(offered.settingsContext) === JSON.stringify(command.settingsContext) &&
          offered.draft.scope === command.draft.scope &&
          JSON.stringify(offered.draft.values) === JSON.stringify(command.draft.values) &&
          JSON.stringify(offered.values) === JSON.stringify(command.values)
        );
      };
      if (!stillOffered()) {
        notice('command_not_sent', 'context_changed');
        return;
      }
      // The server fences this save itself (its body names the context the settings read
      // named, and the revision); the page sends it only for the context it shows.
      if (!sameContext(tracker.current(), command.context)) {
        notice('command_not_sent', 'context_changed');
        return;
      }
      if (
        collectionWriteInFlight(command.context) ||
        collectionChecks.has(contextKey(command.context)) ||
        emulatorSwitchPending(store.getSnapshot())
      ) {
        notice('command_not_sent', 'collection_write_in_flight');
        return;
      }
      {
        // Reserve this settings write while click-time reads revalidate the reviewed
        // context, revision and exact draft. The confirmation itself never sends.
        const reservation = contextKey(command.context);
        const clickedSeq = sequence.next();
        if (settingsSavePending(store.getSnapshot(), ['settings'])) {
          notice('command_not_sent', 'collection_write_in_flight');
          return;
        }
        collectionChecks.add(reservation);
        try {
          const named = await readClickTimeContext();
          if (named === null || !sameContext(named, command.context) || !fenced(command)) {
            notice('command_not_sent', 'context_changed');
            return;
          }
          let checked = await activePoller.readNow('settings');
          if (checked !== null && checked.startedSeq <= clickedSeq)
            checked = await activePoller.readNow('settings');
          if (
            checked === null ||
            checked.startedSeq <= clickedSeq ||
            checked.outcome.kind !== 'answered' ||
            checked.outcome.status !== 200 ||
            !fenced(command) ||
            !sameContext(tracker.current(), command.context) ||
            !stillOffered() ||
            collectionWriteInFlight(command.context)
          ) {
            notice('command_not_sent', 'context_changed');
            return;
          }
        } finally {
          collectionChecks.delete(reservation);
        }
      }
      report(
        sendSettingsSave(
          machine,
          {
            settingsContext: command.settingsContext,
            module: command.module,
            revision: command.revision,
            values: command.values,
            draft: command.draft.scope === '' ? null : command.draft,
          },
          command.context,
        ),
      );
      await Promise.resolve();
    },
    checkRunAdd: () => {
      const epoch = tracker.epoch();
      void activePoller.readNow('profileSource').then(() => {
        if (tracker.epoch() !== epoch) return;
        const vm = selectRunsAdd(store.getSnapshot());
        if (vm.baseRevision !== null)
          store.dispatch({
            type: 'draft_edit',
            scope: vm.scope,
            path: 'base_revision',
            value: vm.baseRevision,
          });
        activePoller.read('runTemplates');
      });
    },
    addRun: async (command) => {
      if (!fenced(command)) return;
      const clickedSeq = sequence.next();
      const reservation = contextKey(command.context);
      if (collectionChecks.has(reservation) || collectionWriteInFlight(command.context)) return;
      collectionChecks.add(reservation);
      try {
        const named = await readClickTimeContext();
        if (named === null || !fenced(command)) {
          notice('command_not_sent', 'context_unconfirmed');
          return;
        }
        if (
          !sameContext(named, command.context) ||
          !sameContext(tracker.current(), command.context)
        ) {
          await activePoller.readNow('config');
          notice('command_not_sent', 'context_changed');
          return;
        }
        // A read begun before the click may miss a taken id. Finish it, then read afresh.
        let checked = await activePoller.readNow('profileSource');
        if (checked !== null && checked.startedSeq <= clickedSeq)
          checked = await activePoller.readNow('profileSource');
        if (checked === null || checked.startedSeq <= clickedSeq) return;
        if (!fenced(command) || !sameContext(tracker.current(), command.context)) return;
        const read = store.getSnapshot().reads.profileSource.latest;
        const body =
          read?.outcome.kind === 'answered' && read.outcome.status === 200
            ? read.outcome.body
            : undefined;
        const profile = command.context.profile;
        if (
          profile === null ||
          !runAddSource(body, profile) ||
          runIdCheck(command.runId, body) !== null
        )
          return;
        const unresolved = machine
          .snapshot()
          .some(
            (record) =>
              record.kind === 'run_add' &&
              sameContext(record.context, command.context) &&
              (record.phase === 'sending' ||
                (record.phase === 'unconfirmed' &&
                  record.readSettlement === null &&
                  record.accepted !== true)),
          );
        if (unresolved || collectionWriteInFlight(command.context)) return;
        report(sendRunAdd(machine, command, named));
      } finally {
        collectionChecks.delete(reservation);
      }
    },
    checkRunEntry: () => {
      activePoller.read(editingSourceResource(selectEditingTarget(store.getSnapshot())));
      activePoller.read('profileFiles');
      activePoller.read('vocab');
    },
    patchRunEntry: async (command) => {
      if (!fenced(command)) return;
      // Reuse the form's current source authority without substituting newer
      // draft text for the submission already reserved by this command.
      const sourceReadable = () => selectRunEntry(store.getSnapshot()).readable;
      const currentTarget = () => {
        const target = selectEditingTarget(store.getSnapshot());
        return (
          target.name === command.profile &&
          target.generation === command.generation &&
          target.blocked === null
        );
      };
      if (!currentTarget()) {
        notice('command_not_sent', 'target_changed');
        return;
      }
      const reservation = contextKey(command.context);
      if (collectionChecks.has(reservation) || collectionWriteInFlight(command.context)) {
        notice('command_not_sent', 'collection_write_in_flight');
        return;
      }
      collectionChecks.add(reservation);
      try {
        if (command.inactive) {
          const endpoints = options.endpoints ?? ENDPOINTS;
          const checked = await createClickTimeTargetCheck(
            transport,
            {
              config: endpoints.config,
              profileFiles: endpoints.profileFiles,
            },
            timers,
          )(command.context, command.profile);
          if (checked !== 'same') {
            notice('command_not_sent', checked);
            return;
          }
        } else {
          // F6-14b: retain the click-time read as a second guard beside the server fence.
          const named = await readClickTimeContext();
          if (named === null) {
            notice('command_not_sent', 'context_unconfirmed');
            return;
          }
          if (!sameContext(named, command.context)) {
            await activePoller.readNow('config');
            notice('command_not_sent', 'context_changed');
            return;
          }
        }
        if (
          !fenced(command) ||
          !sameContext(tracker.current(), command.context) ||
          !currentTarget()
        ) {
          notice('command_not_sent', 'target_changed');
          return;
        }
        if (collectionWriteInFlight(command.context)) {
          notice('command_not_sent', 'collection_write_in_flight');
          return;
        }
        if (!sourceReadable()) {
          notice('command_not_sent', 'target_unconfirmed');
          return;
        }
        report(
          sendRunEntryPatch(
            machine,
            {
              target: command.target,
              profile: command.profile,
              path: command.path,
              omitDeleteValue: true,
              remove: command.remove,
              value: command.value,
              revision: command.revision,
              draft: command.draft,
            },
            command.context,
          ),
        );
      } finally {
        collectionChecks.delete(reservation);
      }
    },
    patchProfile: async (command) => {
      if (!fenced(command)) return;
      const profile = command.profileName ?? command.context.profile;
      const inactive = profile !== command.context.profile;
      const collectionTarget = command.targetGeneration !== undefined;
      const savePending = () =>
        collectionWriteInFlight(command.context) ||
        (collectionTarget && collectionSavePending(store.getSnapshot()));
      const targetStillCurrent = () => {
        const target = selectEditingTarget(store.getSnapshot());
        return (
          !collectionTarget ||
          (target.name === profile && target.generation === command.targetGeneration)
        );
      };
      if (!targetStillCurrent()) {
        notice('command_not_sent', 'target_changed');
        return;
      }
      if (profile === null || savePending()) {
        notice(
          'command_not_sent',
          profile === null ? 'context_unconfirmed' : 'collection_write_in_flight',
        );
        return;
      }
      // A profile patch names a profile, not the page context: a config read started after
      // the click must name exactly the context this command was drawn in (as Profiles does).
      // F6C-02: the one Collection write of this context is reserved before that read, so a
      // settings save clicked meanwhile is not sent either.
      const reservation = contextKey(command.context);
      if (collectionChecks.has(reservation)) {
        notice('command_not_sent', 'collection_write_in_flight');
        return;
      }
      collectionChecks.add(reservation);
      try {
        if (inactive) {
          const result = await createClickTimeTargetCheck(
            transport,
            { config: ENDPOINTS.config, profileFiles: ENDPOINTS.profileFiles },
            timers,
          )(command.context, profile);
          if (result !== 'same') {
            notice('command_not_sent', result);
            return;
          }
        }
        const named = inactive ? command.context : await readClickTimeContext();
        if (named === null) {
          notice('command_not_sent', 'context_unconfirmed');
          return;
        }
        if (!fenced(command)) {
          notice('command_not_sent', 'context_changed');
          return;
        }
        if (!targetStillCurrent()) {
          notice('command_not_sent', 'target_changed');
          return;
        }
        if (
          !sameContext(named, command.context) ||
          !sameContext(tracker.current(), command.context)
        ) {
          await activePoller.readNow('config');
          notice('command_not_sent', 'context_changed');
          return;
        }
        if (savePending()) {
          notice('command_not_sent', 'collection_write_in_flight');
          return;
        }
        report(
          sendProfilePatch(
            machine,
            {
              target: command.target,
              profile,
              path: command.path,
              ...(collectionTarget ? { omitDeleteValue: true } : {}),
              remove: command.remove,
              value: command.value,
              revision: command.revision,
              draft: command.draft,
            },
            command.context,
          ),
        );
      } finally {
        collectionChecks.delete(reservation);
      }
    },
    pickEditingTarget: (command) => {
      store.dispatch({ type: 'edit_target_pick', epoch: command.epoch, name: command.name });
    },
    checkEditingProfiles: () => {
      activePoller.read('profileFiles');
    },
    returnEditingToActive: (command) => {
      store.dispatch({ type: 'edit_target_return', epoch: command.epoch });
    },
    openProfiles: (open) => {
      store.dispatch({ type: 'profiles_open', open });
    },
    checkProfile: async (name) => {
      await checkProfileSource(name);
    },
    profileWrite: async (command) => {
      if (unbindBlocked(command)) {
        notice('command_not_sent', 'unbind_unresolved');
        return;
      }
      if (!fenced(command)) return;
      // One Profiles write in flight per emulator: the routes act on the server's binding.
      // A click whose click-time checks are still running counts as one in flight too.
      const emulator = JSON.stringify([command.context.instance, command.context.serial]);
      const inFlight = machine
        .snapshot()
        .some(
          (record) =>
            profileWriteKind(record.kind) &&
            record.phase === 'sending' &&
            record.context?.instance === command.context.instance &&
            record.context.serial === command.context.serial,
        );
      if (inFlight || profileChecks.has(emulator)) {
        notice('command_not_sent', 'profile_write_in_flight');
        return;
      }
      profileChecks.add(emulator);
      try {
        await checkThenSendProfileWrite(command);
      } finally {
        profileChecks.delete(emulator);
      }
    },
    checkEarlierBattle: async (intentId) => {
      const record = machine.snapshot().find((entry) => entry.id === intentId);
      const instance = tracker.current()?.instance;
      const { battleId, generation, operationId } = record?.meta ?? {};
      const key = opaqueToken(record?.key);
      if (
        record?.kind !== 'live_controls' ||
        instance === undefined ||
        battleId === undefined ||
        generation === undefined ||
        operationId === undefined ||
        key === null
      )
        return;
      const epoch = tracker.epoch();
      const outcome = await readEndpoint(
        transport,
        (options.endpoints ?? ENDPOINTS).battleDetail,
        `/api/battles/${encodeURIComponent(battleId)}?instance=${encodeURIComponent(instance)}`,
      );
      // S-014: an answer that arrives after the context changed is evidence for none.
      if (outcome.kind !== 'answered' || tracker.epoch() !== epoch) return;
      const settled = settleCommand(
        { kind: 'answered', body: outcome.answer.body, sameContext: true },
        { id: battleId, generation, operationId },
        key,
        'controls',
      );
      if (settled.kind === 'recorded') machine.settleByRead(intentId, 'recorded');
    },
  };

  const previewData = createPreviewData(store);
  return {
    store,
    preview: previewData.store,
    poller: activePoller,
    machine,
    tracker,
    sequence,
    diagnostics,
    commands,
    start: () => {
      if (unsubscribe.length > 0) return;
      previewData.start();
      previous = store.getSnapshot();
      unsubscribe = [
        store.subscribe(onStore),
        machine.subscribe(onMachine),
        store.subscribe(settleRecords),
        store.subscribe(clearSavedDrafts),
        store.subscribe(keepRecognitionOperation),
        store.subscribe(persistDrafts),
      ];
      onMachine();
      settleRecords();
      activePoller.start();
    },
    stop: () => {
      previewData.stop();
      savedApplyReadback.stop();
      activePoller.stop();
      machine.dispose();
      for (const undo of unsubscribe) undo();
      unsubscribe = [];
    },
  };
}
