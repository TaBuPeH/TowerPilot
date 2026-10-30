import type { ApplyTarget } from '../../api/intents/recognition.ts';
import { idleForConnectionChange } from '../../core/connection.ts';
import {
  contextsEqual,
  readinessCheck,
  runDefined,
  type ReadinessCard,
} from '../../core/readiness.ts';
import { entries, own } from '../../core/read.ts';
import {
  EVIDENCE_GROUPS,
  SCAN_FEATURES,
  applyAnswer,
  applyBlock,
  battleTracked,
  calibrateStatusView,
  calibrateStopAnswer,
  checkWatch,
  evidenceGroup,
  launchAnswer,
  launchBlock,
  operationActive,
  scanChoices,
  stageOf,
  stopBlock,
  untrackedObservations,
  type CalibrateStatusView,
  type EvidenceGroup,
  type LaunchFacts,
  type OperationView,
  type ReportEntry,
  type ScanChoices,
  type ScanFeature,
  type Stage,
  type StartPlan,
  type WatchCheck,
} from '../../core/recognition.ts';
import type { StateWord } from '../../core/vocabulary.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { Fenced } from '../commands.ts';
import { settledByRead } from '../fences.ts';
import { contextIntents, contextScopeKey } from '../intents.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { ContextView, DashboardState, IntentView, ReadRecord } from '../types.ts';
import { commandVM, type CommandVM } from '../viewModels.ts';
import { selectOutcomes, type OutcomeLineVM } from './outcomes.ts';
import {
  RECOGNITION_LAUNCH_KINDS,
  trackedOperation,
  trackedOperationPath,
  trackedOperationWorking,
} from './recognitionOperation.ts';

/**
 * The Recognition page (screens brief R1 to R8, roadmap item 9). Four facts stay apart: what
 * a request did (its answer, or a later read naming it), where the calibrator operation
 * stands (its own status and step), what its report wrote (verified, written, stale or an
 * unverified copy: a self score or a written file is never shown as verified) and what a
 * run still needs (the readiness read made for one run on a click). Nothing here starts,
 * applies or stops anything; the commands carry what one click sends.
 */

/** The page context a launch, apply or stop was drawn in (the server fences it again). */
export interface RecognitionBase extends Fenced {
  readonly context: ContextView;
}

/** One calibrator launch as the person chose it at the click (consents included). */
export interface RecognitionLaunchCommand extends RecognitionBase {
  readonly launch:
    | { readonly kind: 'start'; readonly plan: StartPlan }
    | { readonly kind: 'observe'; readonly watch: number | null; readonly run: string | null };
}

export interface RecognitionApplyCommand extends RecognitionBase {
  readonly apply: ApplyTarget;
}

export interface RecognitionStopCommand extends RecognitionBase {
  readonly operationId: string;
  readonly expectedRevision: number;
}

export type ReadState = 'not_read' | 'unavailable' | 'ready';

/** The draft scopes of this page, one per context (api/drafts.ts keeps them across a reload). */
export function choicesScope(context: ContextView | null): string | null {
  return context === null ? null : `recognition:choices:${contextScopeKey(context)}`;
}
export function watchScope(context: ContextView | null): string | null {
  return context === null ? null : `recognition:watch:${contextScopeKey(context)}`;
}

export interface EvidenceEntryVM {
  readonly rel: ServerText<'id'>;
  /** The calibrator's status: `verified` only with its own verified verdict. */
  readonly status: 'verified' | 'written' | 'stale' | 'unverified_copy' | 'other';
  readonly statusText: ServerText<'id'> | null;
  readonly screen: ServerText<'id'> | null;
  readonly phase: ServerText<'id'> | null;
  readonly self: number | null;
  readonly next: number | null;
  readonly reason: ServerText<'message'> | null;
}

export interface StatusVM {
  readonly read: ReadState;
  readonly ageSec: number | null;
  /** The newest read did not answer: what is shown is the last answered read. */
  readonly stale: boolean;
  /** The followed operation's evidence could not be read: none is shown (never another's). */
  readonly evidenceUnavailable: boolean;
  readonly tapsAllowed: boolean | null;
  readonly restorePending: boolean;
  /** A calibrator worker is running (possibly one no operation tracks). */
  readonly running: boolean;
  readonly selection: {
    readonly mode: ServerText<'id'> | null;
    readonly phases: readonly ServerText<'id'>[];
    readonly flows: boolean;
  } | null;
  readonly activity: { readonly phase: ServerText<'id'> | null; readonly at: number | null } | null;
  readonly phases: readonly {
    readonly name: ServerText<'id'>;
    readonly status: ServerText<'id'> | null;
    readonly error: ServerText<'message'> | null;
  }[];
  readonly finalScreen: {
    readonly name: ServerText<'id'> | null;
    readonly verified: boolean;
    readonly at: number | null;
    readonly error: ServerText<'message'> | null;
  } | null;
  readonly setupBattle: {
    readonly status: ServerText<'id'> | null;
    readonly startedAt: number | null;
    readonly finishedAt: number | null;
    readonly reason: ServerText<'message'> | null;
  } | null;
  readonly artifactErrors: readonly {
    readonly name: ServerText<'id'>;
    readonly message: ServerText<'message'>;
  }[];
  readonly entries: readonly EvidenceEntryVM[];
  /** Roadmap item 9: the report's entries per targeted kind; an empty group is not in this report. */
  readonly groups: readonly {
    readonly group: EvidenceGroup;
    readonly entries: readonly EvidenceEntryVM[];
  }[];
  readonly modules: readonly {
    readonly slug: ServerText<'id'>;
    readonly rarity: ServerText<'id'> | null;
    readonly slot: number | null;
  }[];
  readonly cards: readonly { readonly name: ServerText<'id'>; readonly slot: number | null }[];
  readonly player: readonly {
    readonly key: ServerText<'id'>;
    readonly value: ServerText<'message'> | null;
  }[];
}

export interface OperationVM {
  readonly id: ServerText<'id'> | null;
  /** `launch`: started from this page; `kept`: followed across a reload; `latest`: the newest of the context. */
  readonly source: 'launch' | 'kept' | 'latest';
  readonly kind: ServerText<'id'> | null;
  readonly stage: Stage;
  readonly word: StateWord | null;
  readonly status: ServerText<'id'> | null;
  readonly step: ServerText<'id'> | null;
  readonly revision: number | null;
  readonly request: {
    readonly phases: ServerText<'id'> | null;
    readonly battle: boolean;
    readonly bootstrap: boolean;
    readonly battleOnly: boolean;
    readonly overwrite: boolean;
    readonly navigation: boolean;
    readonly watch: number | null;
    readonly run: ServerText<'id'> | null;
  };
  readonly createdAt: number | null;
  readonly updatedAt: number | null;
  readonly workerPid: number | null;
  readonly error: {
    readonly code: ServerText<'id'> | null;
    readonly message: ServerText<'message'> | null;
  } | null;
  readonly recovery: ServerText<'message'> | null;
  readonly applyStatus: ServerText<'id'> | null;
  readonly applyError: ServerText<'message'> | null;
  readonly stop: CommandVM<RecognitionStopCommand>;
  readonly apply: CommandVM<RecognitionApplyCommand>;
}

export interface ReadinessCardVM {
  readonly blocking: boolean;
  readonly setting: boolean;
  readonly alternatives: readonly ServerText<'id'>[];
  readonly reasons: readonly ServerText<'message'>[];
  readonly docs: readonly {
    readonly label: ServerText<'name'> | null;
    readonly what: ServerText<'message'> | null;
    readonly where: ServerText<'message'> | null;
    readonly how: ServerText<'message'> | null;
    readonly step: ServerText<'message'> | null;
  }[];
}

export interface ReadinessVM {
  readonly state: 'failed' | 'answered';
  readonly httpStatus: number | null;
  readonly line:
    | 'readiness_unknown'
    | 'readiness_check_failed'
    | 'needs_setup'
    | 'ready'
    | 'images_missing'
    | 'not_ready';
  /** The answer was checked for the emulator, account and profile this page shows. */
  readonly forThisPage: boolean | null;
  readonly ageSec: number;
  readonly missing: readonly ReadinessCardVM[];
  readonly advisory: readonly ReadinessCardVM[];
  readonly message: ServerText<'message'> | null;
  readonly actionLabel: ServerText<'message'> | null;
  readonly error: ServerText<'message'> | null;
}

export type RecognitionOutcomeCode =
  | 'sending'
  | 'accepted'
  | 'applied'
  | 'stopping'
  | 'released'
  | 'stop_answered'
  | 'refused'
  | 'apply_context_changed'
  | 'apply_invalid_request'
  | 'not_started'
  | 'unknown'
  | 'recorded'
  | 'applied_by_read';

export interface RecognitionOutcomeVM {
  readonly id: string;
  readonly kind: 'calibrate_start' | 'calibrate_observe' | 'calibrate_apply' | 'calibrate_stop';
  /** The start's plan kind, or capture/watch for an observe (meta.action). */
  readonly action: string | null;
  readonly code: RecognitionOutcomeCode;
  readonly word: StateWord | null;
  readonly operation: ServerText<'id'> | null;
  readonly refusalCode: ServerText<'id'> | null;
  readonly currentContext: Readonly<
    Record<'instance' | 'account' | 'profile' | 'serial', ServerText<'name'> | null>
  > | null;
  readonly error: ServerText<'message'> | null;
  /** navigation_images_missing: the images still to capture, as the server listed them. */
  readonly missing: readonly ServerText<'id'>[];
  /** An apply's open run checks, as the server listed them. */
  readonly remaining: readonly ServerText<'message'>[];
  readonly readinessReady: boolean | null;
  readonly answeredAgeSec: number | null;
  readonly lost: string | null;
  readonly sends: number;
  readonly restored: boolean;
  readonly resendOffered: boolean;
  /** The person chose to go on without this launch's answer (its outcome stays unknown). */
  readonly accepted: boolean;
  readonly acceptOffered: boolean;
  readonly attemptRefused: OutcomeLineVM['attemptRefused'];
  readonly dismissOffered: boolean;
}

export interface RecognitionPageVM {
  readonly epoch: number;
  readonly context: {
    readonly instance: ServerText<'id'> | null;
    readonly serial: ServerText<'id'> | null;
    readonly account: ServerText<'id'> | null;
    readonly profile: ServerText<'id'> | null;
  } | null;
  readonly contextConfirmed: boolean;
  readonly status: StatusVM;
  /** The operation shown: the followed one, else the newest of the context; null: none. */
  readonly operation: OperationVM | null;
  /** The followed operation's own read could not be used (409, unreadable), with the server's words. */
  readonly operationUnread: { readonly error: ServerText<'message'> | null } | null;
  /** Why a start may not be sent now (null: it may). */
  readonly launchReason: string | null;
  readonly battle: boolean | null;
  readonly learn: CommandVM<RecognitionBase>;
  readonly equipped: CommandVM<RecognitionBase>;
  readonly basic: {
    readonly scope: string | null;
    readonly choices: ScanChoices;
    readonly features: readonly { readonly feature: ScanFeature; readonly on: boolean }[];
    readonly start: CommandVM<RecognitionLaunchCommand>;
  };
  readonly observe: {
    readonly scope: string | null;
    readonly watchText: string;
    readonly watch: WatchCheck;
    readonly capture: CommandVM<RecognitionLaunchCommand>;
    readonly watchStart: CommandVM<RecognitionLaunchCommand>;
  };
  readonly run: {
    readonly read: ReadState;
    readonly runs: readonly {
      readonly name: ServerText<'id'>;
      readonly label: ServerText<'name'>;
    }[];
    readonly selected: ServerText<'id'> | null;
    readonly checking: boolean;
    readonly readiness: ReadinessVM | null;
    readonly check: CommandVM<{ readonly run: string }>;
    readonly prepare: CommandVM<RecognitionLaunchCommand>;
  };
  /** Observations saved by no tracked operation: their explicit apply. */
  readonly savedApply: CommandVM<RecognitionApplyCommand> | null;
  readonly outcomes: readonly RecognitionOutcomeVM[];
}

const LAUNCH_KINDS = new Set(RECOGNITION_LAUNCH_KINDS);
const PAGE_KINDS = new Set([
  'calibrate_start',
  'calibrate_observe',
  'calibrate_apply',
  'calibrate_stop',
]);

function readState(read: ReadRecord | null, viewable: boolean): ReadState {
  if (viewable) return 'ready';
  // A read that arrived (answered with another body, or failed) left nothing to show.
  return read === null ? 'not_read' : 'unavailable';
}

function ids(values: readonly string[]): readonly ServerText<'id'>[] {
  return values.flatMap((value) => {
    const shown = fromServer('id', value);
    return shown === null ? [] : [shown];
  });
}

function messages(values: readonly string[]): readonly ServerText<'message'>[] {
  return values.flatMap((value) => {
    const shown = fromServer('message', value);
    return shown === null ? [] : [shown];
  });
}

function entryVM(entry: ReportEntry): EvidenceEntryVM | null {
  const rel = fromServer('id', entry.rel);
  if (rel === null) return null;
  const status: EvidenceEntryVM['status'] =
    entry.status === 'verified'
      ? entry.verified
        ? 'verified'
        : 'other'
      : entry.status === 'written' || entry.status === 'stale' || entry.status === 'unverified_copy'
        ? entry.status
        : 'other';
  return {
    rel,
    status,
    statusText: fromServer('id', entry.status),
    screen: fromServer('id', entry.screen),
    phase: fromServer('id', entry.phase),
    self: entry.self,
    next: entry.next,
    reason: fromServer('message', entry.reason),
  };
}

function playerValue(value: unknown): ServerText<'message'> | null {
  if (typeof value === 'string') return fromServer('message', value);
  return fromServer('message', JSON.stringify(value));
}

function statusVM(
  facts: CalibrateStatusView | null,
  view: CalibrateStatusView | null,
  read: ReadRecord | null,
  ageSec: number | null,
  flags: { readonly stale: boolean; readonly unavailable: boolean },
): StatusVM {
  const report = view?.report ?? [];
  const shown = report.map(entryVM);
  const entriesVM = shown.flatMap((entry) => (entry === null ? [] : [entry]));
  return {
    read: readState(read, facts !== null),
    ageSec,
    stale: flags.stale,
    evidenceUnavailable: flags.unavailable,
    tapsAllowed: facts?.tapsAllowed ?? null,
    restorePending: facts?.restorePending ?? false,
    running: facts?.running ?? false,
    selection:
      facts?.selection == null
        ? null
        : {
            mode: fromServer('id', facts.selection.mode),
            phases: ids(facts.selection.phases),
            flows: facts.selection.flows,
          },
    activity:
      view?.activity == null
        ? null
        : { phase: fromServer('id', view.activity.phase), at: view.activity.at },
    phases: (view?.phases ?? []).flatMap((phase) => {
      const name = fromServer('id', phase.name);
      return name === null
        ? []
        : [
            {
              name,
              status: fromServer('id', phase.status),
              error: fromServer('message', phase.error),
            },
          ];
    }),
    finalScreen:
      view?.finalScreen == null
        ? null
        : {
            name: fromServer('id', view.finalScreen.name),
            verified: view.finalScreen.verified,
            at: view.finalScreen.at,
            error: fromServer('message', view.finalScreen.error),
          },
    setupBattle:
      view?.setupBattle == null
        ? null
        : {
            status: fromServer('id', view.setupBattle.status),
            startedAt: view.setupBattle.startedAt,
            finishedAt: view.setupBattle.finishedAt,
            reason: fromServer('message', view.setupBattle.reason),
          },
    artifactErrors: (view?.artifactErrors ?? []).flatMap(([name, message]) => {
      const shownName = fromServer('id', name);
      const shownMessage = fromServer('message', message);
      return shownName === null || shownMessage === null
        ? []
        : [{ name: shownName, message: shownMessage }];
    }),
    entries: entriesVM,
    groups: EVIDENCE_GROUPS.map((group) => ({
      group,
      entries: report.flatMap((entry, index) => {
        const vm = shown[index];
        return vm != null && evidenceGroup(entry.rel) === group ? [vm] : [];
      }),
    })),
    modules: (view?.modules ?? []).flatMap((module) => {
      const slug = fromServer('id', module.slug);
      return slug === null
        ? []
        : [{ slug, rarity: fromServer('id', module.rarity), slot: module.slot }];
    }),
    cards: (view?.cards ?? []).flatMap((card) => {
      const name = fromServer('id', card.name);
      return name === null ? [] : [{ name, slot: card.slot }];
    }),
    player: (view?.player ?? []).flatMap(([key, value]) => {
      const name = fromServer('id', key);
      return name === null ? [] : [{ key: name, value: playerValue(value) }];
    }),
  };
}

function stageWord(stage: Stage): StateWord | null {
  switch (stage) {
    case 'accepted':
    case 'running':
      return 'running';
    case 'stopping':
      return 'stopping';
    case 'awaiting_apply':
      return 'waiting';
    case 'failed':
      return 'failed';
    case 'restoration_required':
      return 'blocked';
    case 'unknown':
      return 'unknown';
    case 'applied':
    case 'succeeded':
    case 'interrupted':
    case 'cancelled':
      return null;
  }
}

function cardVM(card: ReadinessCard): ReadinessCardVM {
  return {
    blocking: card.blocking,
    setting: card.setting,
    alternatives: ids(card.alternatives),
    reasons: messages(card.reasons),
    docs: card.docs.map((doc) => ({
      label: fromServer('name', doc.label),
      what: fromServer('message', doc.what),
      where: fromServer('message', doc.where),
      how: fromServer('message', doc.how),
      step: fromServer('message', doc.step),
    })),
  };
}

/**
 * A saved-run, profile, collection or settings editor of this context holds unsaved values:
 * an apply writes the profile they were based on, so it waits until they are saved or discarded.
 */
function profileDraftsOpen(state: DashboardState): boolean {
  if (state.context === null) return false;
  const key = contextScopeKey(state.context);
  return Object.entries(state.runDrafts).some(
    ([scope, values]) =>
      !scope.startsWith('recognition:') &&
      !scope.startsWith('live:') &&
      scope.includes(key) &&
      Object.keys(values).length > 0,
  );
}

function newestOn(records: readonly IntentView[], target: string): IntentView | undefined {
  return records.filter((record) => record.target === target).at(-1);
}

/** A request whose outcome is unknown, not settled by a read, and not set aside by the person. */
function waitingOn(state: DashboardState, record: IntentView | undefined): boolean {
  return (
    record !== undefined &&
    record.phase === 'unconfirmed' &&
    settledByRead(state, record) === null &&
    record.accepted !== true
  );
}

function outcomeVM(
  state: DashboardState,
  record: IntentView,
  line: OutcomeLineVM | undefined,
): RecognitionOutcomeVM | null {
  const kind = record.kind;
  if (
    kind !== 'calibrate_start' &&
    kind !== 'calibrate_observe' &&
    kind !== 'calibrate_apply' &&
    kind !== 'calibrate_stop'
  )
    return null;
  const settled = settledByRead(state, record);
  const raw = record.answer;
  let code: RecognitionOutcomeCode = record.phase === 'sending' ? 'sending' : 'unknown';
  let operation: unknown = record.meta.operationId ?? null;
  let refusalCode: unknown = null;
  let currentContext: RecognitionOutcomeVM['currentContext'] = null;
  let error: unknown = null;
  let missing: readonly string[] = [];
  let remaining: readonly string[] = [];
  let readinessReady: boolean | null = null;
  if (raw !== null && record.phase !== 'sending') {
    if (kind === 'calibrate_apply') {
      const answer = applyAnswer(raw);
      if (answer.kind === 'applied') {
        code = 'applied';
        remaining = answer.remaining;
        readinessReady = answer.readinessReady;
      } else if (answer.kind === 'refused') {
        code =
          answer.code === 'context_changed'
            ? 'apply_context_changed'
            : answer.code === 'invalid_request'
              ? 'apply_invalid_request'
              : 'refused';
        const named = answer.currentContext;
        if (named !== null)
          currentContext = {
            instance: fromServer('name', named.instance),
            account: fromServer('name', named.account),
            profile: fromServer('name', named.profile),
            serial: fromServer('name', named.serial),
          };
        refusalCode = answer.code;
        error = answer.error;
      }
    } else if (kind === 'calibrate_stop') {
      const answer = calibrateStopAnswer(raw);
      if (answer.kind === 'answered') {
        code =
          answer.state === 'stopping'
            ? 'stopping'
            : answer.state === 'released'
              ? 'released'
              : 'stop_answered';
        error = answer.operation?.error?.message ?? null;
      } else if (answer.kind === 'refused') {
        code = 'refused';
        refusalCode = answer.code;
        error = answer.error;
      }
    } else {
      const answer = launchAnswer(raw);
      if (answer.kind === 'accepted') {
        code = 'accepted';
        operation = answer.operation.id;
      } else if (answer.kind === 'refused') {
        code = answer.code === 'context_changed' ? 'not_started' : 'refused';
        refusalCode = answer.code;
        error = answer.error;
        missing = answer.missing;
      }
    }
    // An answer that proves nothing keeps the server's own words beside Unknown.
    if (code === 'unknown') error = own(raw.body, 'error') ?? own(raw.body, 'message') ?? null;
  }
  if (code === 'unknown' && settled !== null)
    code = kind === 'calibrate_apply' ? 'applied_by_read' : 'recorded';
  const unknown = code === 'unknown';
  return {
    id: record.id,
    kind,
    action: record.meta.action ?? null,
    code,
    word:
      code === 'sending'
        ? 'waiting'
        : unknown
          ? 'unknown'
          : code === 'refused' ||
              code === 'apply_context_changed' ||
              code === 'apply_invalid_request' ||
              code === 'not_started'
            ? 'blocked'
            : code === 'stopping'
              ? 'stopping'
              : null,
    operation: fromServer('id', operation),
    refusalCode: fromServer('id', refusalCode),
    currentContext,
    error: fromServer('message', error),
    missing: ids(missing),
    remaining: messages(remaining),
    readinessReady,
    answeredAgeSec:
      record.answeredAtMs === null
        ? null
        : Math.max(0, Math.floor((state.nowMs - record.answeredAtMs) / 1000)),
    lost: record.lostReason,
    sends: record.sends,
    restored: record.restored,
    resendOffered: unknown && line?.resendOffered === true,
    accepted: record.accepted === true,
    acceptOffered: unknown && LAUNCH_KINDS.has(kind) && record.accepted !== true,
    attemptRefused: line?.attemptRefused ?? null,
    dismissOffered: code !== 'sending' && !unknown,
  };
}

function readinessVM(state: DashboardState): ReadinessVM | null {
  const check = state.recognitionReadiness;
  if (check === null || check.run !== state.recognitionRun || check.outcome.kind === 'checking')
    return null;
  const ageSec = Math.max(0, Math.floor((state.nowMs - check.atMs) / 1000));
  if (check.outcome.kind === 'failed') {
    return {
      state: 'failed',
      httpStatus: null,
      line: 'readiness_unknown',
      forThisPage: null,
      ageSec,
      missing: [],
      advisory: [],
      message: null,
      actionLabel: null,
      error: null,
    };
  }
  const read = readinessCheck(check.outcome.body);
  const now = state.context;
  const named = read.context;
  // A readiness answer names the context it checked; it describes this page only for that one.
  const forThisPage =
    named === undefined || now === null
      ? null
      : contextsEqual(named, now, ['instance', 'serial', 'account', 'profile']);
  return {
    state: 'answered',
    httpStatus: check.outcome.status,
    line: check.outcome.status === 200 ? read.view.line : 'readiness_unknown',
    forThisPage,
    ageSec,
    missing: read.missing.map(cardVM),
    advisory: read.advisory.map(cardVM),
    message: fromServer('message', read.message),
    actionLabel: fromServer('message', read.actionLabel),
    error: fromServer('message', read.error),
  };
}

/** The body of a slot's newest read when it answered 200; null when it failed or refused. */
function newest200(read: ReadRecord | null): unknown {
  return read?.outcome.kind === 'answered' && read.outcome.status === 200
    ? read.outcome.body
    : undefined;
}

/**
 * F6R10-R1-01: the battle fact a launch is judged by comes from the newest battle read; one
 * that failed leaves it unknown (an older answer is history, never permission).
 */
function battleFact(state: DashboardState): boolean | null {
  const read = state.reads.battlesActive.latest;
  return read?.outcome.kind === 'answered' && read.outcome.status === 200
    ? battleTracked(read.outcome.body)
    : null;
}

/** The facts every launch of this page is judged by (core launchBlock), from the newest reads. */
export function launchFacts(state: DashboardState): LaunchFacts {
  const launches = contextIntents(state).filter((record) => LAUNCH_KINDS.has(record.kind));
  // F6R10-R1-01: only the newest calibrator read, when it answered, permits a launch.
  const latestView = calibrateStatusView(newest200(state.reads.calibrate.latest));
  const latestOperation = latestView?.operation ?? null;
  return {
    contextConfirmed: state.contextConfirmed && state.context !== null,
    launchSending: launches.some((record) => record.phase === 'sending'),
    launchUnresolved: waitingOn(state, launches.at(-1)),
    status: latestView,
    operationWorking:
      trackedOperationWorking(state) ||
      (latestOperation !== null && operationActive(latestOperation)),
    runner: idleForConnectionChange(contextReadOf(latestRead(state, 'status'))),
    battle: battleFact(state),
  };
}

/** Why `launch` may not be sent now (null: it may): the click's final check (app/wiring.ts). */
export function recognitionLaunchReason(
  state: DashboardState,
  launch: RecognitionLaunchCommand['launch'],
): string | null {
  return launchBlock(
    launchFacts(state),
    launch.kind === 'start' ? { kind: 'start', plan: launch.plan } : { kind: 'observe' },
  );
}

export function selectRecognitionPage(state: DashboardState): RecognitionPageVM {
  const context = state.context;
  const confirmed = state.contextConfirmed && context !== null;
  const records = contextIntents(state).filter((record) => PAGE_KINDS.has(record.kind));
  const lines = selectOutcomes(state).lines;

  // The plain status read: the newest calibrator state of the context.
  const statusAnswered = state.reads.calibrate.lastAnswered;
  const latestView = calibrateStatusView(answeredBody(state, 'calibrate'));

  // The followed operation, read by its id.
  const tracked = trackedOperation(state);
  const opAnswered = state.reads.calibrateOperation.lastAnswered;
  const opBody = answeredBody(state, 'calibrateOperation');
  const opView = calibrateStatusView(opBody);
  const followed =
    tracked !== null && opView?.operation?.id === tracked.id ? opView.operation : null;
  // The newest read of the followed operation's own path arrived without naming it (a 409
  // unknown operation, another workflow, an unreadable body): its state stays unknown.
  const opLatest = state.reads.calibrateOperation.latest;
  const operationUnread =
    tracked !== null &&
    followed === null &&
    opLatest !== null &&
    opLatest.path === trackedOperationPath(tracked)
      ? {
          error:
            opLatest.outcome.kind === 'answered'
              ? fromServer('message', own(opLatest.outcome.body, 'error'))
              : null,
        }
      : null;
  const shown: {
    readonly operation: OperationView;
    readonly source: OperationVM['source'];
  } | null =
    followed !== null && tracked !== null
      ? { operation: followed, source: tracked.source }
      : tracked === null && latestView?.operation != null
        ? { operation: latestView.operation, source: 'latest' }
        : null;
  // F6R10-R1-02: the evidence shown is the shown operation's own (its read by id when
  // followed); a followed operation that cannot be read shows none, never the newest
  // operation's report. Taps, restoration and the running worker come from the plain read.
  // A newest read that failed after an answer marks what is shown as the last answered read.
  const statusStale =
    state.reads.calibrate.latest !== null && state.reads.calibrate.latest !== statusAnswered;
  const opStale = followed !== null && opLatest !== null && opLatest !== opAnswered;
  const status =
    followed !== null && opView !== null
      ? statusVM(
          latestView,
          opView,
          state.reads.calibrate.latest,
          opAnswered === null ? null : readAgeSec(state, opAnswered),
          { stale: opStale || statusStale, unavailable: false },
        )
      : statusVM(
          latestView,
          tracked === null ? latestView : null,
          state.reads.calibrate.latest,
          statusAnswered === null ? null : readAgeSec(state, statusAnswered),
          { stale: statusStale, unavailable: tracked !== null },
        );

  const facts = launchFacts(state);
  const battle = facts.battle;
  const launchSending = facts.launchSending;
  const base: RecognitionBase | null = confirmed ? { epoch: state.epoch, context } : null;
  const gate = <C>(reason: string | null, command: C | null): CommandVM<C> =>
    reason === null && command !== null
      ? commandVM(true, true, null, false, command)
      : commandVM<C>(true, false, reason ?? 'context_unknown', launchSending, null);
  const launchCommand = (launch: RecognitionLaunchCommand['launch']) =>
    base === null ? null : { ...base, launch };
  // Learn and equipped carry the person's consents, chosen at the click; the gate is the same.
  const learnReason = launchBlock(facts, {
    kind: 'start',
    plan: { kind: 'learn', battle: false, overwrite: false },
  });
  const equippedReason = launchBlock(facts, {
    kind: 'start',
    plan: { kind: 'equipped', overwrite: false },
  });

  // R6: the remembered scan choices of this context.
  const cScope = choicesScope(context);
  const choices = scanChoices(cScope === null ? {} : (state.runDrafts[cScope] ?? {}));
  const basicPlan: StartPlan = { kind: 'basic', choices };

  // R5: capture now, or watch for a length validated as typed (never clamped).
  const wScope = watchScope(context);
  const typed = wScope === null ? undefined : state.runDrafts[wScope]?.['seconds'];
  const watchText = typeof typed === 'string' ? typed : '120';
  const watch = checkWatch(watchText);
  const observeReason = launchBlock(facts, { kind: 'observe' });
  const observe = (seconds: number | null) =>
    gate<RecognitionLaunchCommand>(
      observeReason,
      launchCommand({ kind: 'observe', watch: seconds, run: state.recognitionRun }),
    );

  // R4: the run picker and the readiness read made for one run.
  const runsBody = answeredBody(state, 'runs');
  const runs = entries(own(runsBody, 'runs')).flatMap(([key, run]) => {
    const name = fromServer('id', key);
    const label = fromServer('name', own(run, 'label')) ?? fromServer('name', key);
    if (!runDefined(run) || name === null || label === null) return [];
    return [{ name, label }];
  });
  const selectedRun = state.recognitionRun;
  const readiness = readinessVM(state);
  const checking =
    selectedRun !== null &&
    state.recognitionReadiness?.run === selectedRun &&
    state.recognitionReadiness.outcome.kind === 'checking';
  const prepareReason =
    selectedRun === null
      ? 'pick_run'
      : readiness?.state !== 'answered'
        ? 'readiness_not_checked'
        : readiness.forThisPage !== true
          ? 'readiness_other_context'
          : readiness.line !== 'images_missing'
            ? 'nothing_to_prepare'
            : launchBlock(facts, { kind: 'start', plan: { kind: 'prepare', run: selectedRun } });

  // The shown operation's own Stop and apply.
  let operation: OperationVM | null = null;
  if (shown !== null) {
    const op = shown.operation;
    const stopTarget = `recognition:stop:${op.id}`;
    const applyTarget = `recognition:apply:${op.id}`;
    const stopSending = newestOn(records, stopTarget)?.phase === 'sending';
    const applyRecord = newestOn(records, applyTarget);
    const stopReason = stopBlock({ contextConfirmed: confirmed, operation: op, stopSending });
    const applyReason = applyBlock({
      contextConfirmed: confirmed,
      operation: op,
      restorePending: status.restorePending,
      applySending: applyRecord?.phase === 'sending',
      applyUnresolved: waitingOn(state, applyRecord),
      draftsOpen: profileDraftsOpen(state),
    });
    const stage = stageOf(op);
    operation = {
      id: fromServer('id', op.id),
      source: shown.source,
      kind: fromServer('id', op.kind),
      stage,
      word: stageWord(stage),
      status: fromServer('id', op.status),
      step: fromServer('id', op.step),
      revision: op.revision,
      request: {
        phases: fromServer('id', op.request.phases),
        battle: op.request.flows,
        bootstrap: op.request.bootstrap,
        battleOnly: op.request.battleOnly,
        overwrite: op.request.overwrite,
        navigation: op.request.navigation,
        watch: op.request.watch,
        run: fromServer('id', op.request.run),
      },
      createdAt: op.createdAt,
      updatedAt: op.updatedAt,
      workerPid: op.workerPid,
      error:
        op.error === null
          ? null
          : {
              code: fromServer('id', op.error.code),
              message: fromServer('message', op.error.message),
            },
      recovery: fromServer('message', op.recoveryAction),
      applyStatus: fromServer('id', op.apply.status),
      applyError: fromServer('message', op.apply.error),
      stop:
        stopReason === null && base !== null && op.revision !== null
          ? commandVM(true, true, null, false, {
              ...base,
              operationId: op.id,
              expectedRevision: op.revision,
            })
          : commandVM<RecognitionStopCommand>(
              stopReason !== 'not_running',
              false,
              stopReason ?? 'context_unknown',
              stopSending,
              null,
            ),
      apply:
        applyReason === null && base !== null && op.revision !== null
          ? commandVM(true, true, null, false, {
              ...base,
              apply: { kind: 'operation', operationId: op.id, expectedRevision: op.revision },
            })
          : commandVM<RecognitionApplyCommand>(
              applyReason !== 'not_awaiting_apply',
              false,
              applyReason ?? 'context_unknown',
              applyRecord?.phase === 'sending',
              null,
            ),
    };
  }

  // Observations saved by no tracked operation: their apply is offered only for those.
  let savedApply: CommandVM<RecognitionApplyCommand> | null = null;
  if (latestView !== null && tracked === null && untrackedObservations(latestView)) {
    const savedRecord = newestOn(records, 'recognition:apply:saved');
    const reason = !confirmed
      ? 'context_unknown'
      : savedRecord?.phase === 'sending'
        ? 'apply_in_flight'
        : latestView.restorePending
          ? 'restore_pending'
          : waitingOn(state, savedRecord)
            ? 'apply_unresolved'
            : profileDraftsOpen(state)
              ? 'drafts_open'
              : null;
    savedApply =
      reason === null && base !== null
        ? commandVM<RecognitionApplyCommand>(true, true, null, false, {
            ...base,
            apply: { kind: 'saved' },
          })
        : commandVM<RecognitionApplyCommand>(
            true,
            false,
            reason ?? 'context_unknown',
            savedRecord?.phase === 'sending',
            null,
          );
  }

  // The newest line per request target (a newer request on a target replaces the older line).
  const outcomes = records
    .filter(
      (record, index) =>
        !record.dismissed &&
        !records.some((other, at) => at > index && other.target === record.target),
    )
    .flatMap((record) => {
      const vm = outcomeVM(
        state,
        record,
        lines.find((entry) => entry.id === record.id),
      );
      return vm === null ? [] : [vm];
    });

  return {
    epoch: state.epoch,
    context:
      context === null
        ? null
        : {
            instance: fromServer('id', context.instance),
            serial: fromServer('id', context.serial),
            account: fromServer('id', context.account),
            profile: fromServer('id', context.profile),
          },
    contextConfirmed: confirmed,
    status,
    operation,
    operationUnread,
    launchReason: learnReason,
    battle,
    learn: gate<RecognitionBase>(learnReason, base),
    equipped: gate<RecognitionBase>(equippedReason, base),
    basic: {
      scope: cScope,
      choices,
      features: SCAN_FEATURES.map((feature) => ({
        feature,
        on: choices.features.includes(feature),
      })),
      start: gate<RecognitionLaunchCommand>(
        launchBlock(facts, { kind: 'start', plan: basicPlan }),
        launchCommand({ kind: 'start', plan: basicPlan }),
      ),
    },
    observe: {
      scope: wScope,
      watchText,
      watch,
      capture: observe(null),
      watchStart:
        watch.kind === 'valid'
          ? observe(watch.seconds)
          : commandVM<RecognitionLaunchCommand>(
              true,
              false,
              `watch_${watch.reason}`,
              launchSending,
              null,
            ),
    },
    run: {
      read: readState(state.reads.runs.latest, runsBody !== undefined && runs.length > 0),
      runs,
      selected: fromServer('id', selectedRun),
      checking,
      readiness,
      check:
        confirmed && selectedRun !== null && !checking
          ? commandVM(true, true, null, false, { run: selectedRun })
          : commandVM<{ readonly run: string }>(
              true,
              false,
              !confirmed
                ? 'context_unknown'
                : selectedRun === null
                  ? 'pick_run'
                  : 'readiness_checking',
              checking,
              null,
            ),
      prepare: gate<RecognitionLaunchCommand>(
        prepareReason,
        selectedRun === null
          ? null
          : launchCommand({ kind: 'start', plan: { kind: 'prepare', run: selectedRun } }),
      ),
    },
    savedApply,
    outcomes,
  };
}
