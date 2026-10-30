import type { EditTargets } from '../core/editingTarget.ts';
import type { PageContext } from '../api/context.ts';
import type {
  IntentAnswer,
  IntentKind,
  IntentPhase,
  IntentRecord,
  ReadSettlement,
} from '../api/intents/types.ts';
import type { PolledResource, ReadArrival } from '../api/poller.ts';
import type { KnownOn } from '../core/harvestChoice.ts';
import type { LastKnownStop } from './lastKnownStops.ts';

/**
 * The Dashboard store's state and actions (spec 7.1 store row). Its inputs are
 * api/'s own types, imported as types only (store/ never imports an api/
 * value): one definition each, read here under the store's names.
 */
export type ResourceId = PolledResource;

/**
 * One finished read of the current epoch. targetSource also carries its start-time
 * editing selection: editorSource fences display, while fences.ts can settle the
 * original named patch from a read of an earlier selection.
 */
export type ReadRecord = ReadArrival;

export type ReadSlot = {
  /** The newest read, answered or failed. */
  readonly latest: ReadRecord | null;
  /** The newest answered read (kept when a later read failed: "last read {age}"). */
  readonly lastAnswered: ReadRecord | null;
  /** Consecutive failed reads. */
  readonly failures: number;
};

/** The page context. */
export type ContextView = PageContext;

export type IntentKindCode = IntentKind;
export type IntentPhaseCode = IntentPhase;
export type ReadSettlementCode = ReadSettlement;

export interface RetainedSettlement {
  readonly settlement: ReadSettlementCode;
  /** The proving read's start point. */
  readonly atSeq: number;
}

/**
 * One write record as the store reads it: api/'s record without the request it
 * froze (only the machine sends) and with only the answer's status and body.
 */
export type IntentView = Omit<IntentRecord, 'epoch' | 'request' | 'answer'> & {
  readonly answer: Pick<IntentAnswer, 'status' | 'body'> | null;
};

/** A click that sent nothing, and why (X2b "Pause not sent", a Start whose readiness did not permit it). */
export interface LocalNotice {
  readonly kind: 'pause_not_sent' | 'start_not_sent' | 'command_not_sent';
  readonly reason: string;
  readonly atSeq: number;
  readonly atMs: number;
}

export interface DashboardState {
  readonly epoch: number;
  /** The page context (null until a config read or the session record names one). */
  readonly context: ContextView | null;
  /** True once a config read of this page named the context. */
  readonly contextConfirmed: boolean;
  /** The context this browser session recorded (INV-1's third source). */
  readonly sessionContext: ContextView | null;
  readonly reads: Readonly<Record<ResourceId, ReadSlot>>;
  /** run-controls answers per path (7.2: cached per run and context). */
  readonly runControls: Readonly<Record<string, ReadRecord>>;
  /** Last successful read per run in this epoch, for retained editor display after a failed read. */
  readonly runControlsLastAnswered: Readonly<Record<string, ReadRecord>>;
  /** Every write record of every context (selectors keep only the current context's). */
  readonly intents: readonly IntentView[];
  readonly notices: readonly LocalNotice[];
  /** The one-second clock (F-112), browser ms. */
  readonly nowMs: number;
  readonly hidden: boolean;
  /** D-7 learned from readable reads in this context. */
  readonly knownOnFromReads: KnownOn | null;
  /**
   * The highest readable `desired.revision` of this context, whatever order reads and
   * answers arrived in (S-013, D-10: a late receipt never overrides a newer read).
   */
  readonly highestRevision: number | null;
  /**
   * What later reads settled per write record (spec 7.4 "Settled by"), kept once proven
   * (a later failed read never undoes it) with the start point of the proving read; dropped
   * when the record's own answer arrives after that read (B-01: the answer is newer evidence).
   */
  readonly readSettlements: Readonly<Record<string, RetainedSettlement>>;
  /** S-092: the last known Stop per operation (and kill per process) in this context. */
  readonly lastKnownStops: readonly LastKnownStop[];
  /** When the last fresh read proved idle (AS2), for AS4's "last known: idle". */
  readonly lastIdleAtMs: number | null;
  /** The Start picker's local value (F-037). */
  readonly selectedRun: string | null;
  /** The "Gem settings from" picker's local value (F-026); Turn on uses it. */
  readonly gemSourcePick: string | null;
  /** The run the Runs page shows (brief RUN-01); null: the page is not open or picks the default. */
  readonly runsRun: string | null;
  /** True while the Runs page is open: its runs' saved settings are read (brief RUN-02). */
  readonly runsOpen: boolean;
  /** True while the Profiles page is open: the profile files and the active profile are read. */
  readonly profilesOpen: boolean;
  /** The Collection page is open (collection brief): its settings, source and vocabulary are read. */
  readonly collectionOpen: boolean;
  /** The Emulators page is open (emulators brief): its settings are read, an install is followed. */
  readonly emulatorsOpen: boolean;
  /** The Runtime page is open (its settings are read). */
  readonly runtimeOpen: boolean;
  readonly equipmentOpen: boolean;
  /** The Recognition page is open (its reads follow it). */
  readonly recognitionOpen: boolean;
  /**
   * M6: the newest resolution check a person asked for, in this context (null: none).
   * `body` is the answered body (ok true or false), or null when the read failed.
   */
  readonly resolutionCheck: ResolutionCheck | null;
  /** The run chosen on the Recognition page (R4), or null. */
  readonly recognitionRun: string | null;
  /** The readiness read of that run (R4), made on a choice or a click; never polled. */
  readonly recognitionReadiness: RecognitionReadiness | null;
  /**
   * P5: the newest source read of a profile a person picked to activate, in this context
   * (null: none). `body` is the answered 200 body, or null when the read failed or refused.
   */
  readonly profileCheck: ProfileCheck | null;
  /**
   * A person's unsaved edits per editor scope (brief RUN-03, SR-2): `saved:<context>:<run>`
   * or `live:<battle>:<generation>`, path to what was typed or switched. Kept across polls,
   * reads and context changes (the scope names its context, so a draft is never shown or
   * sent in another); only a person's Discard, or a save of exactly those values, clears it.
   */
  readonly editTargets: EditTargets;
  readonly editTargetGeneration: number;
  readonly runDrafts: Readonly<Record<string, Readonly<Record<string, string | boolean>>>>;
}

export interface RecognitionReadiness {
  readonly run: string;
  readonly startedSeq: number;
  readonly atMs: number;
  readonly outcome:
    /** The click's read is in flight (a newer answer of the same read replaces it). */
    | { readonly kind: 'checking' }
    | { readonly kind: 'answered'; readonly status: number; readonly body: unknown }
    | { readonly kind: 'failed'; readonly reason: string };
}

export interface ResolutionCheck {
  readonly serial: string;
  readonly startedSeq: number;
  readonly body: unknown;
  /**
   * F6M-07: what came back, kept apart from the body: `answered` with its status, or
   * `failed` (no answer, a timeout, a body that is not JSON) with the transport's reason.
   */
  readonly result:
    | { readonly kind: 'answered'; readonly status: number }
    | { readonly kind: 'failed'; readonly reason: string; readonly status: number | null };
  /** The page clock when the answer arrived (its age is shown, never a device clock). */
  readonly atMs: number;
}

export interface ProfileCheck {
  readonly name: string;
  readonly startedSeq: number;
  readonly body: unknown;
  /** The server's `error` when it refused the read, as sent (an unknown profile, a bad file). */
  readonly error: string | null;
}

export type DashboardAction =
  | { readonly type: 'read'; readonly read: ReadRecord }
  | {
      readonly type: 'context';
      readonly context: ContextView | null;
      readonly epoch: number;
      readonly confirmed: boolean;
      readonly sessionContext: ContextView | null;
    }
  | { readonly type: 'intents'; readonly records: readonly IntentView[] }
  | { readonly type: 'notice'; readonly notice: LocalNotice }
  /** A later click of the same kind sent its request: the "not sent" line is history. */
  | { readonly type: 'clear_notice'; readonly kind: LocalNotice['kind'] }
  | { readonly type: 'clock'; readonly nowMs: number }
  | { readonly type: 'visibility'; readonly hidden: boolean }
  | { readonly type: 'select_run'; readonly run: string | null }
  | { readonly type: 'select_gem_source'; readonly run: string | null }
  /** The Runs page opened (or closed) on a run (null: its default run). */
  | { readonly type: 'runs_open'; readonly open: boolean; readonly run: string | null }
  | { readonly type: 'edit_target_pick'; readonly epoch: number; readonly name: string }
  | { readonly type: 'edit_target_return'; readonly epoch: number }
  | { readonly type: 'profiles_open'; readonly open: boolean }
  | { readonly type: 'collection_open'; readonly open: boolean }
  | { readonly type: 'profile_checked'; readonly epoch: number; readonly check: ProfileCheck }
  | { readonly type: 'emulators_open'; readonly open: boolean }
  | { readonly type: 'runtime_open'; readonly open: boolean }
  | { readonly type: 'equipment_open'; readonly open: boolean }
  | { readonly type: 'recognition_open'; readonly open: boolean }
  | { readonly type: 'recognition_run'; readonly run: string | null }
  | {
      readonly type: 'recognition_readiness';
      readonly epoch: number;
      readonly readiness: RecognitionReadiness;
    }
  | { readonly type: 'resolution_checked'; readonly epoch: number; readonly check: ResolutionCheck }
  | {
      readonly type: 'draft_edit';
      readonly scope: string;
      readonly path: string;
      readonly value: string | boolean;
    }
  /** Discard: every edit of the scope, or only the paths named (a save that sent exactly them). */
  | {
      readonly type: 'draft_discard';
      readonly scope: string;
      readonly only?: Readonly<Record<string, string | boolean>>;
    };
