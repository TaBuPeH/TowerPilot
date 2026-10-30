import type { GemOutcomes } from '../../core/collection.ts';
import {
  gemPolicy,
  gemSourceChoices,
  gemSourceDefault,
  blueprintOf,
  policyCollects,
  previewPolicy,
  type GemPolicy,
  type GemSourceChoice,
} from '../../core/gemSource.ts';
import type { ChoiceView } from '../../core/harvestChoice.ts';
import { connectionView } from '../../core/connection.ts';
import { gemSlot, turnOnView, type GemCause, type TurnOnFacts } from '../../core/harvestView.ts';
import { overlayView, type OverlayRoutine } from '../../core/overlay.ts';
import { id, own, pid } from '../../core/read.ts';
import { harvestView, type ReadableHarvest } from '../../core/readability.ts';
import { drawnRead } from '../../core/statusContext.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { TurnOnCommand } from '../commands.ts';
import { selectRun } from '../../core/readiness.ts';
import { settledByRead } from '../fences.ts';
import { unresolvedIntent } from '../intents.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { chipVM, commandVM, type ChipVM, type CommandVM } from '../viewModels.ts';
import { controlFacts, latestOnTarget } from './controlFacts.ts';

/**
 * Control / Gem harvesting (spec 7.3, 3.6.2; S-001, S-030, S-032 to S-034,
 * S-037, S-100, S-104). Your choice and Seen now are separate facts (S-034):
 * the choice from D-1 alone, the chip from the gem table alone. Pause follows
 * INV-1 (hidden only when D-8 holds; enabled whenever shown); Turn on follows
 * core's F-027 gate (core/harvestView.ts `turnOnView`), fed here with the
 * config read, the connection line and the newest harvest request.
 */
export interface GemCauseVM {
  readonly kind: GemCause['kind'];
  readonly code: string | null;
  /** The server's message (blocked, reconciler failing, an invalid source's `source.message`). */
  readonly message: ServerText<'message'> | null;
  readonly retryInSec: number | null;
  /** The source's Edit link target (`source.edit`), as codes. */
  readonly editPage: string | null;
  readonly editBlueprint: string | null;
}

export interface OverlayVM {
  readonly kind: 'none' | 'reported' | 'unknown';
  readonly state: string | null;
  readonly pending: boolean;
  readonly applied: boolean;
  readonly cadence: 'current_battle' | 'unknown';
  readonly routines: readonly OverlayRoutine[];
  readonly waitedSec: number | null;
}

/** F-025, F-026, S-080: the "Gem settings from" picker and the description of its run. */
export interface GemSourceVM {
  /** Shown only while the choice is confirmed Off or Not set (F-026). */
  readonly pickerShown: boolean;
  readonly choices: readonly {
    readonly key: string;
    readonly label: ServerText<'name'> | null;
  }[];
  /** The run the picker shows (and Turn on uses), or null when no run is offered. */
  readonly value: string | null;
  readonly label: ServerText<'name'> | null;
  /** The shown run's gem routines: from `harvest.source.policy` for the server's source, else its saved preview. */
  readonly policy: GemPolicy | null;
  /** True while the shown run's saved settings have not been read in this context. */
  readonly policyPending: boolean;
  /** The shown run is the server's resolved source. */
  readonly isServerSource: boolean;
}

/** The collector this dashboard found running at its start: the server's code and the collector's PID, as sent. */
export interface CollectorAtStartVM {
  readonly code: string;
  readonly pid: number | null;
}

export interface GemSlotVM {
  readonly choice: ChoiceView;
  readonly seen: ChipVM;
  readonly pause: CommandVM<null>;
  readonly turnOn: CommandVM<TurnOnCommand>;
  /** F-029: after repeated collector failures (`circuit_open`, choice On), Turn on again with the current revision. */
  readonly turnOnAgain: CommandVM<TurnOnCommand>;
  readonly source: GemSourceVM;
  readonly sourceChanged: boolean;
  readonly cause: GemCauseVM | null;
  /** This battle's confirmations seen, missed windows and the latest miss (lower bounds, from the backend). */
  readonly outcomes: GemOutcomes | null;
  readonly collectorAtStart: CollectorAtStartVM | null;
  readonly overlay: OverlayVM | null;
}

function causeVM(cause: GemCause | null): GemCauseVM | null {
  if (cause === null) return null;
  const base = {
    kind: cause.kind,
    code: null,
    message: null,
    retryInSec: null,
    editPage: null,
    editBlueprint: null,
  };
  switch (cause.kind) {
    case 'reconciler_failing':
      return { ...base, message: fromServer('message', own(cause.error, 'message')) };
    case 'blocked':
      return { ...base, code: cause.code, message: fromServer('message', cause.message) };
    case 'backing_off':
      return { ...base, retryInSec: cause.retryInSec };
    case 'source_invalid':
      return {
        ...base,
        code: cause.code,
        message: fromServer('message', cause.message),
        editPage: id(own(cause.edit, 'page')) ?? null,
        editBlueprint: id(own(cause.edit, 'blueprint')) ?? null,
      };
    default:
      return base;
  }
}

/**
 * The run whose saved settings the gem picker needs read (F-111): the shown
 * run while the picker is shown, unless it is the server's own source (whose
 * policy the status read already carries). Null otherwise.
 */
export function gemPreviewRun(state: DashboardState): string | null {
  const facts = controlFacts(state);
  const read = drawnRead(facts.status);
  const harvest = read !== null && read.harvest.kind === 'known' ? read.harvest.value : null;
  if (harvest === null) return null;
  const choice = harvestView(harvest).choice;
  if (choice === 'on') return null;
  const run = gemSourcePickOf(state, harvest);
  return run === null || run === id(own(harvestView(harvest).source, 'preset')) ? null : run;
}

/** The run-controls path of a run (F-111: one cached read per run and context). */
export function runControlsPath(run: string): `/api/${string}` {
  return `/api/run-controls?blueprint=${encodeURIComponent(blueprintOf(run))}`;
}

/**
 * F-026's default order: the person's own pick, the desired source, the
 * resolved source, the Start picker's run, the first run.
 */
/** The server's own source is always a choice, also before /api/runs answers (F-025). */
function withServerSource(
  choices: readonly GemSourceChoice[],
  view: ReturnType<typeof harvestView> | null,
): readonly GemSourceChoice[] {
  const preset = id(own(view?.source, 'preset'));
  if (preset === undefined || choices.some((choice) => choice.key === preset)) return choices;
  return [...choices, { key: preset, label: own(view?.source, 'label') }];
}

export function gemSourcePickOf(
  state: DashboardState,
  harvest: ReadableHarvest | null,
): string | null {
  const runsBody = answeredBody(state, 'runs');
  const view = harvest === null ? null : harvestView(harvest);
  return gemSourceDefault(withServerSource(gemSourceChoices(runsBody), view), [
    state.gemSourcePick,
    id(own(view?.desired, 'preset')),
    id(own(view?.source, 'preset')),
    selectRun(runsBody, state.selectedRun),
  ]);
}

function gemSourceVM(
  state: DashboardState,
  harvest: ReadableHarvest | null,
  choice: ChoiceView,
): GemSourceVM {
  const view = harvest === null ? null : harvestView(harvest);
  const serverPreset = id(own(view?.source, 'preset')) ?? null;
  const choices = withServerSource(gemSourceChoices(answeredBody(state, 'runs')), view);
  const value = gemSourcePickOf(state, harvest);
  const isServerSource = value !== null && value === serverPreset;
  let policy: GemPolicy | null = null;
  let policyPending = false;
  if (isServerSource) {
    policy = gemPolicy(own(view?.source, 'policy'));
  } else if (value !== null) {
    const preview = state.runControls[runControlsPath(value)];
    if (preview === undefined) policyPending = true;
    else policy = previewPolicy(contextReadOf(preview), value, state.context);
  }
  const listed = choices.find((entry) => entry.key === value);
  return {
    pickerShown: choice.word === 'off' || choice.word === 'not_set',
    choices: choices.map((entry) => ({ key: entry.key, label: fromServer('name', entry.label) })),
    value,
    label:
      fromServer('name', listed?.label) ??
      (isServerSource ? fromServer('name', own(view?.source, 'label')) : null),
    policy,
    policyPending,
    isServerSource,
  };
}

function collectorAtStartVM(
  view: ReturnType<typeof harvestView> | null,
): CollectorAtStartVM | null {
  const found = own(view?.host, 'collector_at_start');
  const code = id(own(found, 'code'));
  return code === undefined ? null : { code, pid: pid(own(found, 'pid')) ?? null };
}

export function selectGemSlot(state: DashboardState): GemSlotVM {
  const facts = controlFacts(state);
  const slot = gemSlot(facts.status, facts.knownOn, facts.unsettled);
  const read = drawnRead(facts.status);
  const harvest = read !== null && read.harvest.kind === 'known' ? read.harvest.value : null;
  const latest = latestOnTarget(facts, 'harvest');
  const pauseSending = latest?.kind === 'pause' && latest.phase === 'sending';
  const harvestSending = latest?.phase === 'sending';
  // The busy mark of the Turn on button while its own request is in flight.
  const turnOnSending = latest?.kind === 'turn_on' && harvestSending;
  const choiceIsOn = slot.choice.word === 'on';
  const source = gemSourceVM(state, harvest, slot.choice);
  const status = latestRead(state, 'status');
  const turnOnFacts: TurnOnFacts = {
    config: facts.input.config,
    connection: connectionView(
      contextReadOf(status),
      status === null ? 0 : readAgeSec(state, status),
    ).line,
    // F-027: no harvest request of this context in flight, or with an outcome no read settled.
    harvestRequest:
      latest === null || !unresolvedIntent(latest) || settledByRead(state, latest) !== null
        ? 'none'
        : latest.phase === 'sending'
          ? 'sending'
          : 'unresolved',
    pickedSourceCollects: !source.isServerSource && policyCollects(source.policy),
  };
  const turnOn = turnOnView(facts.status, own(read?.body, 'collection_guard'), turnOnFacts);
  let turnOnVM: CommandVM<TurnOnCommand>;
  if (choiceIsOn || slot.choice.word === 'unknown') {
    turnOnVM = commandVM<TurnOnCommand>(
      false,
      false,
      choiceIsOn ? 'already_on' : 'choice_unknown',
      false,
      null,
    );
  } else if (
    !turnOn.offered &&
    !(turnOn.reason === 'source_missing' && source.value !== null && !source.isServerSource)
  ) {
    turnOnVM = commandVM<TurnOnCommand>(true, false, turnOn.reason, turnOnSending, null);
  } else if (source.value === null) {
    turnOnVM = commandVM<TurnOnCommand>(true, false, 'source_missing', false, null);
  } else if (source.policyPending) {
    turnOnVM = commandVM<TurnOnCommand>(true, false, 'source_pending', false, null);
  } else if (source.policy === null) {
    turnOnVM = commandVM<TurnOnCommand>(true, false, 'source_unreadable', false, null);
  } else if (!policyCollects(source.policy)) {
    // S-104: a source counts only with at least one gem routine on.
    turnOnVM = commandVM<TurnOnCommand>(true, false, 'gem_routines_off', false, null);
  } else if (!turnOn.offered) {
    turnOnVM = commandVM<TurnOnCommand>(true, false, turnOn.reason, false, null);
  } else {
    turnOnVM = commandVM(true, true, null, false, {
      preset: source.value,
      expectedRevision: turnOn.expectedRevision,
      epoch: state.epoch,
    });
  }
  const overlay = harvest === null ? null : overlayView(harvest);
  const view = harvest === null ? null : harvestView(harvest);
  const circuitOpen =
    choiceIsOn && view !== null && own(own(read?.body, 'harvest'), 'circuit_open') === true;
  const againPreset = id(own(view?.desired, 'preset')) ?? id(own(view?.source, 'preset'));
  const turnOnAgain: CommandVM<TurnOnCommand> = !circuitOpen
    ? commandVM<TurnOnCommand>(false, false, 'not_offered', false, null)
    : !turnOn.offered
      ? commandVM<TurnOnCommand>(true, false, turnOn.reason, turnOnSending, null)
      : againPreset === undefined
        ? commandVM<TurnOnCommand>(true, false, 'source_missing', false, null)
        : commandVM(true, true, null, false, {
            preset: againPreset,
            expectedRevision: turnOn.expectedRevision,
            epoch: state.epoch,
          });
  return {
    choice: slot.choice,
    seen: chipVM(slot.seen),
    // INV-1: shown unless D-8 holds; never disabled; a click while one is in flight sends nothing new.
    pause: commandVM(
      slot.pause,
      slot.pause,
      slot.pause ? null : 'off_confirmed',
      pauseSending,
      null,
    ),
    turnOn: turnOnVM,
    turnOnAgain,
    source,
    sourceChanged: turnOn.offered && turnOn.sourceChanged,
    cause: causeVM(slot.cause),
    outcomes: slot.outcomes,
    collectorAtStart: collectorAtStartVM(view),
    overlay:
      overlay === null
        ? null
        : {
            kind: overlay.kind,
            state: overlay.state,
            pending: overlay.pending,
            applied: overlay.applied,
            cadence: overlay.cadence,
            routines: overlay.routines,
            waitedSec: overlay.waitedSec,
          },
  };
}
