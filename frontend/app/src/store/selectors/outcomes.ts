import type { Confirmed } from '../../core/evidence.ts';
import type { Fact } from '../../core/facts.ts';
import { gameInputSeen, turnOffStoppingSettled } from '../../core/input.ts';
import { enableUnresolvedBefore } from '../../core/inputFacts.ts';
import { classifyHarvestAnswer, type HarvestAnswerClass } from '../../core/outcomes/harvest.ts';
import {
  classifyInputAnswer,
  type InputAnswerClass,
  type StillOwned,
} from '../../core/outcomes/input.ts';
import { classifyKillAnswer } from '../../core/outcomes/kill.ts';
import { refusalRecovery, type Recovery } from '../../core/outcomes/refusal.ts';
import {
  classifyClearFlagAnswer,
  classifyHandoffAnswer,
  classifyStartAnswer,
  type StartAnswerClass,
} from '../../core/outcomes/start.ts';
import {
  classifyStopAnswer,
  stopAgainAfterRead,
  type StopAnswerClass,
  type StopEffects,
  type StopOperation,
} from '../../core/outcomes/stop.ts';
import { own } from '../../core/read.ts';
import { commandAnswer, runNowAnswer } from '../../core/routines.ts';
import { profileWriteAnswer, type ProfileWriteKind } from '../../core/profiles.ts';
import { savedSaveAnswer } from '../../core/runControls.ts';
import type { StateWord } from '../../core/vocabulary.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type {
  StopCommand,
  TurnOnCommand,
  LaunchCommand,
  ClearFlagCommand,
  HandoffCommand,
  RunNowCommand,
} from '../commands.ts';
import { inCurrentContext, unresolvedIntent } from '../intents.ts';
import {
  harvestPausedCurrent,
  killListing,
  lineCurrent,
  settledByRead,
  type KillListing,
} from '../fences.ts';
import { ownerNotice, type NoticeClass } from '../ownerNotice.ts';
import { statusReadOf } from '../reads.ts';
import type {
  DashboardState,
  IntentKindCode,
  IntentPhaseCode,
  IntentView,
  LocalNotice,
  ReadSettlementCode,
} from '../types.ts';
import { controlFacts, highestStopRevision, type ControlFacts } from './controlFacts.ts';
import type { ReconnectCommand } from './topBar.ts';

/**
 * Control / Outcomes (spec 7.3, 7.4, 7.5; S-003, S-005, S-010 to S-014,
 * S-046, S-047, S-049, S-051 to S-057, S-070 to S-074). One line per write
 * record of the current context that the person did not dismiss: its phase,
 * the class core's classifier gives its answer with every distinction that
 * class carries (`detail`: per-request effects, attribution, matched
 * operation, reasons, holders, conflicts, owners, failed steps; server strings
 * as ServerText, codes as codes), the answer's age, what a later read settled,
 * whether it is still the current result of its target, and whether a resend
 * of the identical request is offered (only a person's click sends it).
 * Records of another context are never shown here (S-014); a record made
 * before the page knew any context is shown, marked `bound: false`.
 */
export type OutcomeSlot =
  | 'gem'
  | 'input'
  | 'automation'
  | 'start'
  | 'battle'
  | 'connection'
  | 'needs'
  | 'runs'
  | 'profiles'
  | 'settings'
  | 'emulators'
  | 'recognition';

/** Kinds whose identical request a person may send again (spec 7.4); api/intents/machine.ts RESENDABLE. */
export const RESEND_OFFERED: readonly IntentKindCode[] = [
  'pause',
  'turn_on',
  'start',
  'start_gems',
  'run_now',
  'handoff',
  'live_controls',
  // Recognition (R1, R4, R5): the same key answers the same operation, a lost apply is
  // answered from the operation's record (calibrate_writes *_retry_*, apply_resend_*).
  'calibrate_start',
  'calibrate_observe',
  'calibrate_apply',
];

/** The operation a Stop answer names, matched to the request's id and validated by core. */
export interface StopOperationVM {
  readonly id: ServerText<'id'> | null;
  /** The operation status code core validated (e.g. cancelling, cancelled, interrupted, running). */
  readonly status: string;
  readonly revision: number;
  readonly purpose: string | null;
  readonly label: ServerText<'name'> | null;
  /** `operation.error.code`, or null. */
  readonly errorCode: ServerText<'id'> | null;
  /** `operation.error.message` (or a string `error`), exactly as sent, or null. */
  readonly errorMessage: ServerText<'message'> | null;
}

/** `stop.by_this_request` as core validated it (S-052): what THIS request did. */
export interface StopEffectsVM {
  readonly paused: boolean;
  readonly requested: boolean;
  readonly signalled: readonly number[];
  readonly family: 'stopped' | 'stopping' | 'unverified' | 'gone' | null;
  readonly released: boolean;
}

export interface LeaseHolderVM {
  readonly pid: number;
  readonly role: ServerText<'id'> | null;
}

interface StopDetailBase {
  readonly kind: 'stop';
  readonly operation: StopOperationVM | null;
  readonly effects: StopEffectsVM | null;
  /** The unverified reason code (S-053), else null. */
  readonly unverifiedReason: string | null;
  /** The lease holders the answer names (S-053), each with its pid and role. */
  readonly holders: readonly LeaseHolderVM[];
  /** `stop.holder.foreign` when the answer names a holder record, else null. */
  readonly holderForeign: boolean | null;
  /** S-053: Turn input off is offered beside Stop again (a held or unreadable lease). */
  readonly turnInputOff: boolean;
}

/** A Stop answer (S-051 to S-057), discriminated by core's class. */
export type StopDetailVM =
  | (StopDetailBase & {
      readonly class: 'stopped' | 'released' | 'stopping' | 'unverified' | 'ended' | 'active';
      /** S-052: an ended operation is attributed to this Stop only when it signalled processes. */
      readonly endedAttribution: 'this_stop_signalled' | 'unattributed';
    })
  | (StopDetailBase & { readonly class: 'not_applied'; readonly code: string })
  | (StopDetailBase & {
      readonly class: 'partial';
      /** S-055: the conflict that interrupted the Stop, exactly as sent. */
      readonly conflict: ServerText<'message'> | null;
    })
  | (StopDetailBase & { readonly class: 'action_lock' })
  | (StopDetailBase & { readonly class: 'unknown' });

export interface InputOwnerVM {
  readonly kind: 'operation' | 'process';
  readonly operation: ServerText<'id'> | null;
  readonly pid: number | null;
  /** The owner's input fence: `refused` (fenced), `unfenced`, or `unknown` for any other value. */
  readonly input: 'refused' | 'unfenced' | 'unknown';
  /** The owner's own way out (S-041): Pause, its Stop with the highest revision known, or none. */
  readonly recovery: RecoveryVM;
}

export interface FailedStepVM {
  readonly step: string | null;
  readonly message: ServerText<'message'> | null;
}

/** A Turn input off or Allow game input answer (S-040 to S-045). */
export interface InputDetailVM {
  readonly kind: 'input';
  readonly class: InputAnswerClass['kind'];
  readonly stopped: readonly ServerText<'id'>[];
  readonly stopping: readonly ServerText<'id'>[];
  readonly owners: readonly InputOwnerVM[];
  readonly unfenced: readonly InputOwnerVM[];
  readonly failedSteps: readonly FailedStepVM[];
  /** S-046: the answer's "harvesting paused" claim and its revision (null: no claim). */
  readonly harvestPaused: { readonly revision: number | null } | null;
  readonly settingSaved: boolean | null;
  /** The refusal or unknown-outcome code, else null. */
  readonly code: string | null;
}

/** A Pause or Turn on answer (S-004, S-010, S-015, S-029). */
export interface HarvestDetailVM {
  readonly kind: 'harvest';
  readonly class: HarvestAnswerClass['kind'];
  readonly revision: number | null;
  readonly replayed: boolean;
  /** Pause receipts: what the Pause stopped or found stopping, as sent (a past claim). */
  readonly stopped: readonly ServerText<'id'>[];
  readonly stopping: readonly ServerText<'id'>[];
  readonly stopErrors: readonly ServerText<'message'>[];
  readonly noCollectorWasRunning: boolean | null;
  readonly code: string | null;
  readonly recovery: 'read' | 'try_again' | null;
}

/** A Start or Collect answer (S-102, 7.4). */
export interface StartDetailVM {
  readonly kind: 'start';
  readonly class: StartAnswerClass['kind'];
  readonly operation: ServerText<'id'> | null;
  readonly transfer: 'none' | 'unconfirmed' | 'handover' | null;
  readonly stoppedAtHandover: readonly ServerText<'id'>[];
  readonly wasStoppingAtHandover: readonly ServerText<'id'>[];
  readonly waitedSec: number | null;
  readonly code: string | null;
}

/** A kill (S-060): never "killed"; whether a later D-4 read still lists that exact process. */
export interface KillDetailVM {
  readonly kind: 'kill';
  readonly class: 'no_longer_running' | 'identity_unverified' | 'refused' | 'unknown';
  readonly code: string | null;
  readonly listing: KillListing | null;
}

export interface HandoffDetailVM {
  readonly kind: 'handoff';
  readonly class: 'recorded' | 'owner_refused' | 'refused' | 'unknown';
  readonly handoff: ServerText<'id'> | null;
  readonly created: boolean | null;
  readonly code: string | null;
}

export interface CodeDetailVM {
  readonly kind:
    | 'clear_flag'
    | 'run_now'
    | 'reconnect'
    | 'run_save'
    | 'run_add'
    | 'schedule_save'
    | 'settings_save'
    | 'profile_patch'
    | 'adopt'
    | 'launch'
    | 'prepare'
    | 'tools_install'
    | 'calibrate_start'
    | 'calibrate_observe'
    | 'calibrate_apply'
    | 'calibrate_stop'
    | 'analysis_start'
    | 'template_write'
    | 'live_controls'
    | ProfileWriteKind;
  readonly class: string;
  readonly code: string | null;
}

export type OutcomeDetailVM =
  | StopDetailVM
  | InputDetailVM
  | HarvestDetailVM
  | StartDetailVM
  | KillDetailVM
  | HandoffDetailVM
  | CodeDetailVM
  /** No answer (in flight, lost, reloaded). */
  | { readonly kind: 'none' };

export interface OutcomeLineVM {
  readonly id: string;
  readonly target: ServerText<'id'> | null;
  readonly inputRecoverySettled: boolean;
  readonly earlierEnableUnresolved: boolean;
  readonly kind: IntentKindCode;
  readonly slot: OutcomeSlot;
  readonly phase: IntentPhaseCode;
  /** The answer's class from core (e.g. paused, not_applied, unknown, input_stopped, stopping); `sending` or `no_answer` without one. */
  readonly outcome: string;
  /**
   * The line's status word (spec 3.4): Waiting while sent, Unknown for no reply or an
   * unproven receipt, Stopping for a part still stopping, Blocked for a refusal, Failed
   * for a definite failure; null (no chip) for a settled or confirmed result.
   */
  readonly word: StateWord | null;
  /** Every distinction core's class carries for this answer. */
  readonly detail: OutcomeDetailVM;
  /** Whole seconds since the answer arrived (store clock), or null: a dated past result (7.3). */
  readonly answeredAgeSec: number | null;
  /** False for a record made before the page knew a context: its answer is evidence for none. */
  readonly bound: boolean;
  /** What a later read settled for a record whose answer did not (spec 7.4 last column). */
  readonly settledByRead: ReadSettlementCode | null;
  /** False once a newer request, choice or input fact made the line history. */
  readonly current: boolean;
  readonly resendOffered: boolean;
  /** Why there is no answer (window_expired, network, aborted, page_reloaded). */
  readonly lost: string | null;
  readonly sends: number;
  readonly restored: boolean;
  /** The server's `error`, as sent. */
  readonly error: ServerText<'message'> | null;
  readonly operationError?: ServerText<'message'> | null;
  /** S-046: whether an input answer's "harvesting paused" claim is still current (null: none). */
  readonly harvestPausedCurrent: boolean | null;
  /** S-056, S-051: "Stop again" with the newest revision, only while a later read shows the operation active. */
  readonly stopAgain: StopCommand | null;
  /**
   * F6R-01: a person's resend of this unknown request was refused (the request lock, a
   * changed revision): what that attempt proves, never a verdict on the first attempt.
   * The server's `error` and `code` of the refusal, or null.
   */
  readonly attemptRefused: {
    readonly error: ServerText<'message'> | null;
    readonly code: ServerText<'id'> | null;
  } | null;
}

export type RecoveryVM =
  | { readonly kind: 'pause' }
  | {
      readonly kind: 'stop';
      readonly operation: ServerText<'id'> | null;
      readonly command: StopCommand | null;
    }
  | { readonly kind: 'retry' }
  | { readonly kind: 'none' };

export interface OwnerNoticeVM {
  readonly intentId: string;
  readonly action: IntentKindCode;
  readonly retry: RetryVM;
  readonly purpose: ServerText<'name'> | null;
  readonly purposeCode: string | null;
  readonly status: ServerText<'id'> | null;
  readonly currentOperation: ServerText<'id'> | null;
  readonly currentPid: number | null;
  readonly noticeClass: NoticeClass;
  readonly operation: ServerText<'id'> | null;
  readonly pid: number | null;
  /** `device_owned_pre_spawn`: an operation is starting (accepted). */
  readonly starting: boolean;
  readonly foreign: boolean;
  readonly script: ServerText<'name'> | null;
  readonly holders: readonly LeaseHolderVM[];
  readonly error: ServerText<'message'> | null;
  readonly recovery: RecoveryVM;
  /** Null until a status read started after the notice; then core's D-4 judgement. */
  readonly stopped: Fact<Confirmed<'stopped'>> | null;
  readonly free: Fact<Confirmed<'free'>> | null;
  readonly canRetry: boolean;
  /** S-026: the owner a later read names now (another owner may have taken over), or null. */
  readonly currentOwner: NoticeClass | 'busy' | 'not_owner_notice' | null;
}

/** A fresh request offered by an unreadable-owner notice, already fenced by its selector. */
export type RetryVM =
  | { readonly kind: 'pause' | 'input_off' | 'input_on' | 'none' }
  | { readonly kind: 'reconnect'; readonly command: ReconnectCommand }
  | { readonly kind: 'start' | 'start_gems'; readonly command: LaunchCommand }
  | { readonly kind: 'turn_on'; readonly command: TurnOnCommand }
  | { readonly kind: 'stop' | 'kill'; readonly command: StopCommand }
  | { readonly kind: 'clear_flag'; readonly command: ClearFlagCommand }
  | { readonly kind: 'handoff'; readonly command: HandoffCommand }
  | { readonly kind: 'run_now'; readonly command: RunNowCommand };

export interface LocalNoticeVM {
  readonly kind: LocalNotice['kind'];
  readonly reason: string;
  readonly ageSec: number;
}

export interface OutcomesVM {
  readonly lines: readonly OutcomeLineVM[];
  readonly ownerNotice: OwnerNoticeVM | null;
  readonly notices: readonly LocalNoticeVM[];
  readonly otherContextUnresolved: boolean;
}

const SLOTS: Readonly<Record<IntentKindCode, OutcomeSlot>> = {
  pause: 'gem',
  turn_on: 'gem',
  input_off: 'input',
  input_on: 'input',
  stop: 'automation',
  kill: 'automation',
  clear_flag: 'automation',
  handoff: 'needs',
  start: 'start',
  start_gems: 'start',
  reconnect: 'connection',
  run_now: 'battle',
  run_save: 'runs',
  run_add: 'runs',
  schedule_save: 'runs',
  // Collection and Abilities pages word their own saves (slot `settings`).
  settings_save: 'settings',
  profile_patch: 'settings',
  // The Emulators page words its own requests (slot `emulators`); owner notices stay shared.
  adopt: 'emulators',
  launch: 'emulators',
  prepare: 'emulators',
  tools_install: 'emulators',
  // The Recognition page words its own requests (slot `recognition`); owner notices stay shared.
  calibrate_start: 'recognition',
  calibrate_observe: 'recognition',
  calibrate_apply: 'recognition',
  calibrate_stop: 'recognition',
  analysis_start: 'recognition',
  template_write: 'recognition',
  live_controls: 'runs',
  account_select: 'profiles',
  account_create: 'profiles',
  account_tier: 'profiles',
  profile_activate: 'profiles',
  profile_copy: 'profiles',
  profile_promote: 'profiles',
};

const NONE: OutcomeDetailVM = Object.freeze({ kind: 'none' });

function ids(values: readonly string[]): readonly ServerText<'id'>[] {
  return values.flatMap((value) => {
    const shown = fromServer('id', value);
    return shown === null ? [] : [shown];
  });
}

function codeOf(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/** The S-050 rule for every Stop surface: the highest revision any source names for exactly this id. */
function highest(facts: ControlFacts, operationId: string, revision: number): number {
  return Math.max(revision, highestStopRevision(facts, operationId) ?? revision);
}

function operationVM(operation: StopOperation | null): StopOperationVM | null {
  if (operation === null) return null;
  const error = operation.error;
  return {
    id: fromServer('id', operation.id),
    status: operation.status,
    revision: operation.revision,
    purpose: codeOf(own(operation.raw, 'kind')),
    label: fromServer('name', own(operation.raw, 'label')),
    errorCode: fromServer('id', own(error, 'code')),
    errorMessage: errorMessage(error),
  };
}

function errorMessage(error: unknown): ServerText<'message'> | null {
  return fromServer('message', typeof error === 'string' ? error : own(error, 'message'));
}

function effectsVM(effects: StopEffects | null): StopEffectsVM | null {
  return effects === null ? null : { ...effects, signalled: [...effects.signalled] };
}

function stopDetail(answer: StopAnswerClass): StopDetailVM {
  const holder = answer.holder;
  const foreign = own(holder, 'foreign');
  const base: StopDetailBase = {
    kind: 'stop',
    operation: operationVM(answer.operation),
    effects: effectsVM(answer.effects),
    unverifiedReason: answer.kind === 'unverified' ? answer.reason : null,
    holders: answer.holders.map((entry) => ({
      pid: entry.pid,
      role: fromServer('id', entry.role),
    })),
    holderForeign: typeof foreign === 'boolean' ? foreign : null,
    turnInputOff: answer.turnInputOff,
  };
  switch (answer.kind) {
    case 'not_applied':
      return { ...base, class: 'not_applied', code: answer.code };
    case 'partial':
      return {
        ...base,
        class: 'partial',
        conflict: fromServer('message', answer.conflict),
      };
    case 'action_lock':
      return { ...base, class: 'action_lock' };
    case 'unknown':
      return { ...base, class: 'unknown' };
    default:
      return { ...base, class: answer.kind, endedAttribution: answer.endedAttribution };
  }
}

function inputOwnerVM(state: DashboardState, facts: ControlFacts, entry: StillOwned): InputOwnerVM {
  const owner = entry.owner;
  return {
    kind: owner.kind,
    operation: owner.kind === 'operation' ? fromServer('id', owner.operationId) : null,
    pid: owner.pid,
    input: entry.input === 'refused' || entry.input === 'unfenced' ? entry.input : 'unknown',
    // Core reads the entry's own `recovery` record, as it reads an owner notice's (S-070).
    recovery: recoveryVM(state, facts, refusalRecovery({ recovery: entry.recovery }, facts.claims)),
  };
}

function inputDetail(
  state: DashboardState,
  facts: ControlFacts,
  answer: InputAnswerClass,
): InputDetailVM {
  const ownerVM = (entry: StillOwned) => inputOwnerVM(state, facts, entry);
  const detail: InputDetailVM = {
    kind: 'input',
    class: answer.kind,
    stopped: [],
    stopping: [],
    owners: [],
    unfenced: [],
    failedSteps: [],
    harvestPaused: null,
    settingSaved: null,
    code: null,
  };
  const steps = (errors: readonly { step: string | null; message: unknown }[]) =>
    errors.map((error) => ({ step: error.step, message: fromServer('message', error.message) }));
  switch (answer.kind) {
    case 'input_stopped':
      return { ...detail, stopped: ids(answer.stopped), settingSaved: answer.settingSaved };
    case 'input_stopped_not_saved':
      return { ...detail, stopped: ids(answer.stopped), settingSaved: false };
    case 'nothing_running':
      return { ...detail, settingSaved: answer.settingSaved };
    case 'not_enforced':
      return {
        ...detail,
        owners: answer.owners.map(ownerVM),
        unfenced: answer.unfenced.map(ownerVM),
      };
    case 'stopping':
    case 'stopping_with_errors':
      return {
        ...detail,
        stopping: ids(answer.stopping),
        owners: answer.owners.map(ownerVM),
        unfenced: answer.unfenced.map(ownerVM),
        failedSteps: steps(answer.errors),
        harvestPaused:
          answer.harvestPaused === null ? null : { revision: answer.harvestPaused.revision },
      };
    case 'revocation_failed':
      return {
        ...detail,
        stopped: ids(answer.stopped),
        failedSteps: steps(answer.errors),
        harvestPaused:
          answer.harvestPaused === null ? null : { revision: answer.harvestPaused.revision },
        settingSaved: answer.settingSaved,
      };
    case 'enable_refused':
      return { ...detail, code: answer.code };
    case 'unknown':
      return { ...detail, code: answer.reason };
    default:
      return detail;
  }
}

function harvestDetail(answer: HarvestAnswerClass): HarvestDetailVM {
  const detail: HarvestDetailVM = {
    kind: 'harvest',
    class: answer.kind,
    revision: null,
    replayed: false,
    stopped: [],
    stopping: [],
    stopErrors: [],
    noCollectorWasRunning: null,
    code: null,
    recovery: null,
  };
  switch (answer.kind) {
    case 'turned_on':
      return { ...detail, revision: answer.revision, replayed: answer.replayed };
    case 'paused':
      return {
        ...detail,
        revision: answer.revision,
        replayed: answer.replayed,
        stopped: ids(answer.stop.stopped),
        stopping: ids(answer.stop.stopping),
        stopErrors: answer.stop.errors.flatMap((error) => {
          const shown = fromServer('message', error);
          return shown === null ? [] : [shown];
        }),
        noCollectorWasRunning: answer.stop.noCollectorWasRunning,
      };
    case 'pause_unconfirmed':
      return { ...detail, revision: answer.revision, code: answer.reason };
    case 'not_applied':
      return { ...detail, code: answer.code, recovery: answer.recovery };
    case 'unknown':
      return { ...detail, code: answer.code };
  }
}

function startDetail(answer: StartAnswerClass): StartDetailVM {
  const detail: StartDetailVM = {
    kind: 'start',
    class: answer.kind,
    operation: null,
    transfer: null,
    stoppedAtHandover: [],
    wasStoppingAtHandover: [],
    waitedSec: null,
    code: null,
  };
  switch (answer.kind) {
    case 'started': {
      const transfer = answer.transfer;
      return {
        ...detail,
        operation: fromServer('id', answer.operationId),
        transfer: transfer.kind,
        ...(transfer.kind === 'handover'
          ? {
              stoppedAtHandover: ids(transfer.stoppedAtHandover),
              wasStoppingAtHandover: ids(transfer.wasStoppingAtHandover),
              waitedSec: transfer.waitedSec,
            }
          : {}),
      };
    }
    case 'failed':
    case 'cancelled':
    case 'interrupted':
    case 'succeeded':
      return { ...detail, operation: fromServer('id', answer.operationId) };
    case 'context_changed':
    case 'refused':
      return { ...detail, code: codeOf(answer.code) };
    case 'unknown':
      return { ...detail, code: codeOf(answer.code) };
    default:
      return detail;
  }
}

function answerDetail(
  state: DashboardState,
  facts: ControlFacts,
  record: IntentView,
): OutcomeDetailVM {
  const raw = record.answer;
  if (raw === null) return NONE;
  switch (record.kind) {
    case 'pause':
      return harvestDetail(classifyHarvestAnswer(raw, 'pause'));
    case 'turn_on':
      return harvestDetail(classifyHarvestAnswer(raw, 'turn_on'));
    case 'input_off':
      return inputDetail(state, facts, classifyInputAnswer(raw, 'disable'));
    case 'input_on':
      return inputDetail(state, facts, classifyInputAnswer(raw, 'enable'));
    case 'stop': {
      const operationId = record.meta.operationId;
      return operationId === undefined
        ? NONE
        : stopDetail(classifyStopAnswer(raw, operationId, facts.claims));
    }
    case 'kill': {
      const answer = classifyKillAnswer(raw);
      return {
        kind: 'kill',
        class: answer.kind,
        code: codeOf(answer.code),
        listing: killListing(state, record),
      };
    }
    case 'handoff': {
      const answer = classifyHandoffAnswer(raw);
      return {
        kind: 'handoff',
        class: answer.kind,
        handoff: answer.kind === 'recorded' ? fromServer('id', answer.handoffId) : null,
        created: answer.kind === 'recorded' ? answer.created : null,
        code: answer.kind === 'refused' ? codeOf(answer.code) : null,
      };
    }
    case 'start':
    case 'start_gems':
      return startDetail(classifyStartAnswer(raw));
    case 'clear_flag': {
      const answer = classifyClearFlagAnswer(raw);
      return { kind: 'clear_flag', class: answer.kind, code: codeOf(answer.code) };
    }
    case 'reconnect':
      return { kind: 'reconnect', class: record.phase, code: codeOf(own(raw.body, 'code')) };
    case 'run_add':
    case 'schedule_save':
    case 'settings_save':
    case 'profile_patch':
    case 'adopt':
    case 'launch':
    case 'prepare':
    case 'tools_install':
    case 'calibrate_start':
    case 'calibrate_observe':
    case 'calibrate_apply':
    case 'calibrate_stop':
    case 'analysis_start':
    case 'template_write':
      return { kind: record.kind, class: record.phase, code: codeOf(own(raw.body, 'code')) };
    case 'account_select':
    case 'account_create':
    case 'account_tier':
    case 'profile_activate':
    case 'profile_copy':
    case 'profile_promote': {
      const answer = profileWriteAnswer(record.kind, raw, record.meta);
      return {
        kind: record.kind,
        class: answer.kind,
        code: answer.kind === 'refused' ? codeOf(answer.code) : null,
      };
    }
    case 'run_now':
      return {
        kind: 'run_now',
        class: runNowAnswer({ kind: 'answered', status: raw.status, body: raw.body }).kind,
        code: codeOf(own(raw.body, 'code')),
      };
    case 'live_controls':
      return {
        kind: 'live_controls',
        class: commandAnswer({ kind: 'answered', status: raw.status, body: raw.body }, 'controls')
          .kind,
        code: codeOf(own(raw.body, 'code')),
      };
    case 'run_save': {
      const answer = savedSaveAnswer(raw, record.meta);
      return {
        kind: 'run_save',
        class: answer.kind,
        code: answer.kind === 'refused' ? codeOf(answer.code) : null,
      };
    }
  }
}

function outcomeClass(record: IntentView, detail: OutcomeDetailVM): string {
  if (detail.kind === 'none') {
    if (record.answer !== null) return 'unknown';
    return record.phase === 'sending' ? 'sending' : 'no_answer';
  }
  return detail.class;
}

function stopAgain(
  state: DashboardState,
  facts: ControlFacts,
  record: IntentView,
): StopCommand | null {
  const operationId = record.meta.operationId;
  if (record.kind !== 'stop' || operationId === undefined || record.supersededBySeq !== null)
    return null;
  const command = (revision: number): StopCommand => ({
    kind: 'cancel',
    operationId,
    expectedRevision: highest(facts, operationId, revision),
    epoch: state.epoch,
  });
  if (record.answer !== null) {
    const answer = classifyStopAnswer(record.answer, operationId, facts.claims);
    if (answer.kind !== 'unknown' && answer.stopAgainRevision !== null) {
      return command(answer.stopAgainRevision);
    }
    if (answer.kind !== 'unknown') return null;
  } else if (record.phase === 'sending') {
    return null;
  }
  const latest = state.reads.status.latest;
  if (latest === null) return null;
  const revision = stopAgainAfterRead(
    operationId,
    statusReadOf(latest),
    record.answerSeq ?? record.sentSeq,
    facts.claims,
  );
  return revision === null ? null : command(revision);
}

function recoveryVM(state: DashboardState, facts: ControlFacts, recovery: Recovery): RecoveryVM {
  switch (recovery.kind) {
    case 'stop': {
      const known = highestStopRevision(facts, recovery.operationId);
      const revision =
        recovery.revision === null
          ? known
          : highest(facts, recovery.operationId, recovery.revision);
      return {
        kind: 'stop',
        operation: fromServer('id', recovery.operationId),
        command:
          revision === null
            ? null
            : {
                kind: 'cancel',
                operationId: recovery.operationId,
                expectedRevision: revision,
                epoch: state.epoch,
              },
      };
    }
    case 'pause':
      return { kind: 'pause' };
    case 'retry':
      return { kind: 'retry' };
    case 'none':
      return { kind: 'none' };
  }
}

function retryVM(state: DashboardState, facts: ControlFacts, record: IntentView): RetryVM {
  const m = record.meta;
  const epoch = state.epoch;
  switch (record.kind) {
    case 'pause':
    case 'input_off':
    case 'input_on':
      return { kind: record.kind };
    case 'reconnect':
      // F6M-02: a retry reconnects the address the refused request named, in its context.
      if (record.context !== null && m.serial !== undefined)
        return {
          kind: 'reconnect',
          command: { epoch, context: record.context, serial: m.serial },
        };
      break;
    case 'start':
    case 'start_gems':
      if (m.preset !== undefined)
        return { kind: record.kind, command: { epoch, preset: m.preset } };
      break;
    case 'turn_on':
      if (m.preset !== undefined && m.expectedRevision !== undefined)
        return {
          kind: record.kind,
          command: { epoch, preset: m.preset, expectedRevision: m.expectedRevision },
        };
      break;
    case 'stop':
    case 'clear_flag':
      if (m.operationId !== undefined && m.expectedRevision !== undefined) {
        const command = {
          epoch,
          operationId: m.operationId,
          expectedRevision: highest(facts, m.operationId, m.expectedRevision),
        };
        return record.kind === 'stop'
          ? { kind: 'stop', command: { ...command, kind: 'cancel' } }
          : { kind: 'clear_flag', command };
      }
      break;
    case 'kill':
      if (m.pid !== undefined && m.created !== undefined)
        return { kind: 'kill', command: { kind: 'kill', epoch, pid: m.pid, created: m.created } };
      break;
    case 'handoff':
      if (
        m.operationId !== undefined &&
        m.setupBattleId !== undefined &&
        m.evidenceRevision !== undefined
      )
        return {
          kind: 'handoff',
          command: {
            epoch,
            operationId: m.operationId,
            setupBattleId: m.setupBattleId,
            evidenceRevision: m.evidenceRevision,
            attestation: 'new_manual_battle',
          },
        };
      break;
    case 'run_now':
      if (
        m.operationId !== undefined &&
        m.battleId !== undefined &&
        m.generation !== undefined &&
        m.expectedRevision !== undefined &&
        m.action !== undefined
      )
        return {
          kind: 'run_now',
          command: {
            epoch,
            operationId: m.operationId,
            battleId: m.battleId,
            generation: m.generation,
            expectedRevision: m.expectedRevision,
            action: m.action,
          },
        };
      break;
  }
  return { kind: 'none' };
}

function ownerNoticeVM(state: DashboardState, facts: ControlFacts): OwnerNoticeVM | null {
  const found = ownerNotice(state, facts);
  if (found === null) return null;
  const { notice, assessment } = found;
  const current = assessment?.currentOwner;
  const base = {
    intentId: found.record.id,
    action: found.record.kind,
    retry: retryVM(state, facts, found.record),
    purpose:
      notice.kind === 'operation_owner' ? fromServer('name', own(notice.owner, 'label')) : null,
    purposeCode: notice.kind === 'operation_owner' ? codeOf(notice.operationKind) : null,
    status: notice.kind === 'operation_owner' ? fromServer('id', notice.status) : null,
    currentOperation:
      current?.kind === 'operation_owner' ? fromServer('id', current.operationId) : null,
    currentPid:
      current?.kind === 'operation_owner' || current?.kind === 'process_owner' ? current.pid : null,
    noticeClass: found.noticeClass,
    operation: null,
    pid: null,
    starting: false,
    foreign: false,
    script: null,
    holders: [],
    error: fromServer('message', notice.error),
    recovery: recoveryVM(state, facts, assessment?.recovery ?? notice.recovery),
    stopped: assessment?.stopped ?? null,
    free: assessment?.free ?? null,
    canRetry: assessment?.canRetry ?? false,
    currentOwner: assessment?.currentOwner?.kind ?? null,
  };
  switch (notice.kind) {
    case 'operation_owner':
      return {
        ...base,
        operation: fromServer('id', notice.operationId),
        pid: notice.pid,
        starting: notice.starting,
        foreign: own(notice.owner, 'foreign') === true,
      };
    case 'process_owner':
      return {
        ...base,
        pid: notice.pid,
        foreign: notice.foreign,
        script: fromServer('name', notice.script),
      };
    case 'lease_holder':
      return {
        ...base,
        foreign: own(notice.owner, 'foreign') === true,
        holders: notice.holders.map((holder) => ({
          pid: holder.pid,
          role: fromServer('id', holder.role),
        })),
      };
    default:
      return base;
  }
}

const UNKNOWN_OUTCOMES: ReadonlySet<string> = new Set([
  'unknown',
  'no_answer',
  'unverified',
  'active',
  'pause_unconfirmed',
  'not_enforced',
  'input_stopped_not_saved',
]);
const REFUSED_OUTCOMES: ReadonlySet<string> = new Set([
  'not_applied',
  'action_lock',
  'enable_refused',
]);

/** Spec 3.4: the word of an outcome line, decided here so no component classifies outcomes. */
export function outcomeWord(line: {
  readonly phase: IntentView['phase'];
  readonly settledByRead: ReadSettlementCode | null;
  readonly inputRecoverySettled: boolean;
  readonly outcome: string;
  readonly detail: OutcomeDetailVM;
}): StateWord | null {
  if (line.phase === 'sending') return 'waiting';
  if (line.settledByRead === 'not_applied') return 'blocked';
  // A later read settled it (stopped, off confirmed, applied, recorded): a quiet result.
  if (line.settledByRead !== null || line.inputRecoverySettled) return null;
  if (line.outcome === 'failed' || line.outcome === 'revocation_failed') return 'failed';
  if (line.detail.kind === 'start' && line.detail.class === 'refused') return 'failed';
  if ('code' in line.detail && line.detail.code === 'invalid_request') return 'failed';
  // A part still stopping (a partial Stop conflict included) reads Stopping, refused or not.
  if (['stopping', 'stopping_with_errors', 'partial'].includes(line.outcome)) return 'stopping';
  // A refused request (an owner or guard said no, nothing applied) reads Blocked.
  if (line.phase === 'refused' || REFUSED_OUTCOMES.has(line.outcome)) return 'blocked';
  if (UNKNOWN_OUTCOMES.has(line.outcome)) return 'unknown';
  // Every other class is a confirmed result of this request (stopped, released, ended,
  // input stopped, paused, cleared, recorded): no chip, it claims nothing current.
  return null;
}

export function selectOutcomes(state: DashboardState): OutcomesVM {
  const facts = controlFacts(state);
  const inputSeen = gameInputSeen(facts.input);
  const lines: OutcomeLineVM[] = [];
  for (const record of facts.records) {
    if (record.dismissed) continue;
    const settled = settledByRead(state, record);
    const detail = answerDetail(state, facts, record);
    const line: Omit<OutcomeLineVM, 'word'> = {
      id: record.id,
      target: fromServer('id', record.meta.operationId ?? record.meta.preset ?? record.meta.action),
      inputRecoverySettled:
        record.kind === 'input_off' &&
        facts.input.turnOff?.answerSeq === record.answerSeq &&
        lineCurrent(state, facts, record) &&
        (inputSeen.word === 'idle' || turnOffStoppingSettled(facts.input)),
      earlierEnableUnresolved:
        record.kind === 'input_off' &&
        record.answerSeq !== null &&
        enableUnresolvedBefore(facts.input, record.answerSeq),
      kind: record.kind,
      slot: SLOTS[record.kind],
      phase: record.phase,
      outcome: outcomeClass(record, detail),
      detail,
      answeredAgeSec:
        record.answeredAtMs === null
          ? null
          : Math.max(0, Math.floor((state.nowMs - record.answeredAtMs) / 1000)),
      bound: record.context !== null,
      settledByRead: settled,
      current: lineCurrent(state, facts, record),
      resendOffered:
        RESEND_OFFERED.includes(record.kind) &&
        record.phase === 'unconfirmed' &&
        record.supersededBySeq === null &&
        settled === null,
      lost: record.lostReason,
      sends: record.sends,
      restored: record.restored,
      error:
        record.answer === null ? null : fromServer('message', own(record.answer.body, 'error')),
      operationError:
        record.answer === null
          ? null
          : errorMessage(own(own(record.answer.body, 'operation'), 'error')),
      harvestPausedCurrent: harvestPausedCurrent(state, record),
      stopAgain: stopAgain(state, facts, record),
      attemptRefused:
        record.attemptRefusal === null ||
        record.attemptRefusal === undefined ||
        !unresolvedIntent(record) ||
        settled !== null
          ? null
          : {
              error: fromServer('message', own(record.attemptRefusal.body, 'error')),
              code: fromServer('id', own(record.attemptRefusal.body, 'code')),
            },
    };
    lines.push({ ...line, word: outcomeWord(line) });
  }
  return {
    lines,
    otherContextUnresolved: state.intents.some(
      (record) => !record.dismissed && !inCurrentContext(state, record) && unresolvedIntent(record),
    ),
    ownerNotice: ownerNoticeVM(state, facts),
    notices: state.notices.map((notice) => ({
      kind: notice.kind,
      reason: notice.reason,
      ageSec: Math.max(0, Math.floor((state.nowMs - notice.atMs) / 1000)),
    })),
  };
}
