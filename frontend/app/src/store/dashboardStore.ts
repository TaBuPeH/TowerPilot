import { editTargetKey } from '../core/editingTarget.ts';
import { retainedStringListKeys, SETTINGS_BASE_REVISION } from '../core/settingsFields.ts';
import {
  currentSettingsView,
  settingsScope,
  settingsSavePending,
} from './selectors/settingsModule.ts';
import { pickableNow } from './selectors/editingTarget.ts';
import { onFromRead } from '../core/harvestChoice.ts';
import { harvestView } from '../core/readability.ts';
import { nextSettlements } from './fences.ts';
import { idleProvenBy, nextLastKnownStops } from './lastKnownStops.ts';
import { assessedRead } from './reads.ts';
import { controlFacts } from './selectors/controlFacts.ts';
import { createStore, type Store } from './store.ts';
import type {
  ContextView,
  DashboardAction,
  DashboardState,
  ReadRecord,
  ReadSlot,
  ResourceId,
} from './types.ts';

/**
 * The Dashboard's one immutable state (spec 7.1 store row): the reads with
 * their request sequence points and arrival times, the write records (a
 * mirror of api/intents/machine.ts, which owns them), the local notices of
 * clicks that sent nothing, a one-second clock and the facts that outlive a
 * single read in this context (Known On from reads, the last known Stops, the
 * last idle read). Every read is fenced by the context epoch: an arrival from
 * another epoch is dropped here as well as in the poller (S-014).
 */
const EMPTY_SLOT: ReadSlot = Object.freeze({ latest: null, lastAnswered: null, failures: 0 });

function emptyReads(): Readonly<Record<ResourceId, ReadSlot>> {
  return Object.freeze({
    status: EMPTY_SLOT,
    calibrate: EMPTY_SLOT,
    calibrateOperation: EMPTY_SLOT,
    battlesActive: EMPTY_SLOT,
    schedule: EMPTY_SLOT,
    battles: EMPTY_SLOT,
    config: EMPTY_SLOT,
    accounts: EMPTY_SLOT,
    runs: EMPTY_SLOT,
    wizardTools: EMPTY_SLOT,
    wizardState: EMPTY_SLOT,
    wizardEmulators: EMPTY_SLOT,
    runControls: EMPTY_SLOT,
    profileFiles: EMPTY_SLOT,
    profileSource: EMPTY_SLOT,
    targetSource: EMPTY_SLOT,
    settings: EMPTY_SLOT,
    vocab: EMPTY_SLOT,
    runTemplates: EMPTY_SLOT,
  });
}

export function initialDashboardState(
  options: {
    readonly nowMs?: number;
    readonly context?: ContextView | null;
    readonly hidden?: boolean;
  } = {},
): DashboardState {
  return Object.freeze({
    epoch: 0,
    context: options.context ?? null,
    contextConfirmed: false,
    sessionContext: options.context ?? null,
    reads: emptyReads(),
    runControls: Object.freeze({}),
    runControlsLastAnswered: Object.freeze({}),
    intents: Object.freeze([]),
    notices: Object.freeze([]),
    nowMs: options.nowMs ?? 0,
    hidden: options.hidden ?? false,
    knownOnFromReads: null,
    highestRevision: null,
    readSettlements: Object.freeze({}),
    lastKnownStops: Object.freeze([]),
    lastIdleAtMs: null,
    selectedRun: null,
    gemSourcePick: null,
    runsRun: null,
    runsOpen: false,
    profilesOpen: false,
    collectionOpen: false,
    emulatorsOpen: false,
    runtimeOpen: false,
    equipmentOpen: false,
    recognitionOpen: false,
    recognitionRun: null,
    recognitionReadiness: null,
    resolutionCheck: null,
    profileCheck: null,
    editTargets: Object.freeze({}),
    editTargetGeneration: 0,
    runDrafts: Object.freeze({}),
  });
}

/** A draft without `only`'s paths where they still hold exactly those values (a save of them confirmed). */
function withoutDraft(
  drafts: DashboardState['runDrafts'],
  scope: string,
  only: Readonly<Record<string, string | boolean>> | undefined,
): DashboardState['runDrafts'] {
  const current = drafts[scope];
  if (current === undefined) return drafts;
  const retained = only === undefined ? new Set<string>() : retainedStringListKeys(current, only);
  const kept =
    only === undefined
      ? {}
      : Object.fromEntries(
          Object.entries(current).filter(
            ([path, value]) => retained.has(path) || only[path] !== value,
          ),
        );
  const others = Object.entries(drafts).filter(([key]) => key !== scope);
  return Object.freeze(
    Object.fromEntries(
      Object.keys(kept).filter(
        (key) => !scope.startsWith('settings:') || key !== SETTINGS_BASE_REVISION,
      ).length === 0
        ? others
        : [...others, [scope, Object.freeze(kept)]],
    ),
  );
}

function applyRead(state: DashboardState, read: ReadRecord): DashboardState {
  // The context fence (S-014): a read from another epoch is dropped.
  if (read.epoch !== state.epoch) return state;
  const slot = state.reads[read.resource];
  const answered = read.outcome.kind === 'answered';
  const nextSlot: ReadSlot = Object.freeze({
    latest: read,
    lastAnswered: answered ? read : slot.lastAnswered,
    failures: answered ? 0 : slot.failures + 1,
  });
  let next: DashboardState = {
    ...state,
    reads: Object.freeze({ ...state.reads, [read.resource]: nextSlot }),
  };
  if (read.resource === 'runControls') {
    next = { ...next, runControls: Object.freeze({ ...state.runControls, [read.path]: read }) };
    if (read.outcome.kind === 'answered' && read.outcome.status === 200) {
      next = {
        ...next,
        runControlsLastAnswered: Object.freeze({
          ...state.runControlsLastAnswered,
          [read.path]: read,
        }),
      };
    }
  }
  if (read.resource === 'status') {
    const assessment = assessedRead(read);
    let knownOnFromReads = state.knownOnFromReads;
    let highestRevision = state.highestRevision;
    if (assessment.harvest.kind === 'known') {
      const learned = onFromRead(assessment.harvest.value, read.arrivedSeq);
      if (
        learned !== null &&
        (knownOnFromReads === null || learned.revision > knownOnFromReads.revision)
      ) {
        knownOnFromReads = Object.freeze({ ...learned });
      }
      const readRevision = harvestView(assessment.harvest.value).revision;
      if (highestRevision === null || readRevision > highestRevision)
        highestRevision = readRevision;
    }
    next = {
      ...next,
      knownOnFromReads,
      highestRevision,
      lastKnownStops: nextLastKnownStops(state, assessment),
      lastIdleAtMs: idleProvenBy(state, assessment) ? read.arrivedAtMs : state.lastIdleAtMs,
    };
  }
  return Object.freeze(next);
}

/** What later reads settled (spec 7.4 "Settled by"), judged again after every read and record change. */
function withSettlements(state: DashboardState): DashboardState {
  const readSettlements = nextSettlements(state, controlFacts(state));
  return readSettlements === state.readSettlements
    ? state
    : Object.freeze({ ...state, readSettlements });
}

/** The reducer: pure, every state frozen. */
export function reduceDashboard(state: DashboardState, action: DashboardAction): DashboardState {
  switch (action.type) {
    case 'read': {
      const next = applyRead(state, action.read);
      return next === state ? state : withSettlements(next);
    }
    case 'context': {
      if (action.epoch === state.epoch) {
        return Object.freeze({
          ...state,
          context: action.context,
          contextConfirmed: action.confirmed,
          sessionContext: action.sessionContext,
        });
      }
      // A new epoch: every fact learned in the old context is dropped (S-014); the write
      // records stay with the machine and are shown only in their own context.
      return Object.freeze({
        ...initialDashboardState({ nowMs: state.nowMs, hidden: state.hidden }),
        epoch: action.epoch,
        context: action.context,
        contextConfirmed: action.confirmed,
        sessionContext: action.sessionContext,
        intents: state.intents,
        selectedRun: state.selectedRun,
        gemSourcePick: state.gemSourcePick,
        runsRun: state.runsRun,
        runsOpen: state.runsOpen,
        profilesOpen: state.profilesOpen,
        collectionOpen: state.collectionOpen,
        emulatorsOpen: state.emulatorsOpen,
        runtimeOpen: state.runtimeOpen,
        equipmentOpen: state.equipmentOpen,
        recognitionOpen: state.recognitionOpen,
        recognitionRun: state.recognitionRun,
        // SR-2: a person's edits outlive a context change; their scope keeps them to their own.
        runDrafts: state.runDrafts,
        editTargets: state.editTargets,
        editTargetGeneration: state.editTargetGeneration,
      });
    }
    case 'intents':
      return withSettlements(
        Object.freeze({ ...state, intents: Object.freeze([...action.records]) }),
      );
    case 'notice':
      return Object.freeze({
        ...state,
        notices: Object.freeze([
          ...state.notices.filter((notice) => notice.kind !== action.notice.kind),
          action.notice,
        ]),
      });
    case 'clear_notice':
      return state.notices.some((notice) => notice.kind === action.kind)
        ? Object.freeze({
            ...state,
            notices: Object.freeze(state.notices.filter((notice) => notice.kind !== action.kind)),
          })
        : state;
    case 'clock':
      return action.nowMs === state.nowMs
        ? state
        : Object.freeze({ ...state, nowMs: action.nowMs });
    case 'visibility':
      return action.hidden === state.hidden
        ? state
        : Object.freeze({ ...state, hidden: action.hidden });
    case 'select_run':
      return action.run === state.selectedRun
        ? state
        : Object.freeze({ ...state, selectedRun: action.run });
    case 'select_gem_source':
      return action.run === state.gemSourcePick
        ? state
        : Object.freeze({ ...state, gemSourcePick: action.run });
    case 'runs_open':
      return action.run === state.runsRun && action.open === state.runsOpen
        ? state
        : Object.freeze({ ...state, runsRun: action.run, runsOpen: action.open });
    case 'edit_target_pick':
    case 'edit_target_return': {
      if (action.epoch !== state.epoch || state.context === null || !state.contextConfirmed)
        return state;
      if (
        action.type === 'edit_target_pick' &&
        !pickableNow(state).some((file) => file.name === action.name)
      )
        return state;
      const key = editTargetKey(state.context);
      const generation = state.editTargetGeneration + 1;
      const remaining = Object.fromEntries(
        Object.entries(state.editTargets).filter(([name]) => name !== key),
      );
      return Object.freeze({
        ...state,
        editTargetGeneration: generation,
        // A new selection must read its own source, even when it names the same file.
        // Later arrivals stay available to the original journal; editorSource fences display.
        reads: Object.freeze({ ...state.reads, targetSource: EMPTY_SLOT }),
        editTargets: Object.freeze(
          action.type === 'edit_target_pick'
            ? { ...remaining, [key]: { name: action.name, generation } }
            : remaining,
        ),
      });
    }
    case 'profiles_open':
      return action.open === state.profilesOpen
        ? state
        : Object.freeze({ ...state, profilesOpen: action.open });
    case 'emulators_open':
      return action.open === state.emulatorsOpen
        ? state
        : Object.freeze({ ...state, emulatorsOpen: action.open });
    case 'equipment_open':
      return action.open === state.equipmentOpen
        ? state
        : Object.freeze({ ...state, equipmentOpen: action.open });
    case 'runtime_open':
      return action.open === state.runtimeOpen
        ? state
        : Object.freeze({ ...state, runtimeOpen: action.open });
    case 'recognition_open':
      return action.open === state.recognitionOpen
        ? state
        : Object.freeze({ ...state, recognitionOpen: action.open });
    case 'recognition_run':
      return action.run === state.recognitionRun
        ? state
        : Object.freeze({ ...state, recognitionRun: action.run, recognitionReadiness: null });
    case 'recognition_readiness':
      // S-014: a read started in another context, or for another run, is evidence for none.
      return action.epoch !== state.epoch ||
        action.readiness.run !== state.recognitionRun ||
        (state.recognitionReadiness !== null &&
          action.readiness.startedSeq < state.recognitionReadiness.startedSeq)
        ? state
        : Object.freeze({ ...state, recognitionReadiness: Object.freeze({ ...action.readiness }) });
    case 'resolution_checked':
      // S-014: a check started in another context is evidence for none.
      return action.epoch !== state.epoch ||
        (state.resolutionCheck !== null &&
          action.check.startedSeq < state.resolutionCheck.startedSeq)
        ? state
        : Object.freeze({ ...state, resolutionCheck: Object.freeze({ ...action.check }) });
    case 'collection_open':
      return action.open === state.collectionOpen
        ? state
        : Object.freeze({ ...state, collectionOpen: action.open });
    case 'profile_checked':
      // S-014: a check started in another context is evidence for none.
      return action.epoch !== state.epoch ||
        (state.profileCheck !== null && action.check.startedSeq < state.profileCheck.startedSeq)
        ? state
        : Object.freeze({ ...state, profileCheck: Object.freeze({ ...action.check }) });
    case 'draft_edit': {
      const current = state.runDrafts[action.scope] ?? {};
      if (current[action.path] === action.value) return state;
      const settingsDraft = action.scope.startsWith('settings:');
      const view = settingsDraft ? currentSettingsView(state) : null;
      const ownsScope =
        view?.modules.some(
          (module) => module.editable && settingsScope(state.context, module.id) === action.scope,
        ) === true;
      // A legacy draft with no provenance must be reconciled explicitly. Never assign
      // a newer read's revision to raw input restored from an earlier session.
      const firstEdit = settingsDraft && Object.keys(current).length === 0;
      const revision = firstEdit && ownsScope ? (view.revision ?? '') : '';
      // Reconciliation is an explicit draft edit, accepted only against a current read.
      if (
        settingsDraft &&
        action.path === SETTINGS_BASE_REVISION &&
        (!ownsScope || action.value !== view.revision || settingsSavePending(state, ['settings']))
      )
        return state;
      return Object.freeze({
        ...state,
        runDrafts: Object.freeze({
          ...state.runDrafts,
          [action.scope]: Object.freeze({
            ...current,
            ...(firstEdit ? { [SETTINGS_BASE_REVISION]: revision } : {}),
            [action.path]: action.value,
          }),
        }),
      });
    }
    case 'draft_discard': {
      const runDrafts = withoutDraft(state.runDrafts, action.scope, action.only);
      return runDrafts === state.runDrafts ? state : Object.freeze({ ...state, runDrafts });
    }
  }
}

export interface DashboardStore extends Store<DashboardState> {
  readonly dispatch: (action: DashboardAction) => void;
}

export function createDashboardStore(
  initial: DashboardState = initialDashboardState(),
): DashboardStore {
  const store = createStore(initial);
  return {
    ...store,
    dispatch: (action) => {
      store.update((state) => reduceDashboard(state, action));
    },
  };
}
