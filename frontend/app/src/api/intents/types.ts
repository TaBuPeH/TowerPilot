import type { PageContext } from '../context.ts';
import type { ApiPath } from '../transport.ts';
import type { WriteEndpointId } from '../wire.ts';

/**
 * Write intents (ADR rule 3, spec 7.4): every write is one record per click,
 * its body and idempotency key frozen at the click. A record is `sending`
 * while its request is in flight and inside the 15 s window (D-10), then
 * settled by its answer as `confirmed` (the server applied it), `refused`
 * (the server answered that nothing was applied) or `unconfirmed` (the
 * outcome is unknown: lost, timed out, a 5xx, an answer outside the
 * contract). No record means `idle`. Nothing here ever sends a request on its
 * own: a resend is a person's explicit action with the identical body.
 */
export type IntentKind =
  | 'pause'
  | 'turn_on'
  | 'input_off'
  | 'input_on'
  | 'stop'
  | 'kill'
  | 'handoff'
  | 'start'
  | 'start_gems'
  | 'clear_flag'
  | 'reconnect'
  | 'run_now'
  | 'run_save'
  | 'run_add'
  | 'schedule_save'
  | 'live_controls'
  | 'account_select'
  | 'account_create'
  | 'account_tier'
  | 'profile_activate'
  | 'profile_copy'
  | 'profile_promote'
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
  | 'template_write';

export const INTENT_KINDS: readonly IntentKind[] = [
  'pause',
  'turn_on',
  'input_off',
  'input_on',
  'stop',
  'kill',
  'handoff',
  'start',
  'start_gems',
  'clear_flag',
  'reconnect',
  'run_now',
  'run_save',
  'run_add',
  'schedule_save',
  'live_controls',
  'account_select',
  'account_create',
  'account_tier',
  'profile_activate',
  'profile_copy',
  'profile_promote',
  'settings_save',
  'profile_patch',
  'adopt',
  'launch',
  'prepare',
  'tools_install',
  'calibrate_start',
  'calibrate_observe',
  'calibrate_apply',
  'calibrate_stop',
  'analysis_start',
  'template_write',
];

export type IntentPhase = 'sending' | 'confirmed' | 'refused' | 'unconfirmed';

/** How an answered record settled. */
export type SettledPhase = 'confirmed' | 'refused' | 'unconfirmed';

/** Why a record has no answer: the window passed, the network failed, or the page was reloaded. */
export type LostReason = 'window_expired' | 'network' | 'aborted' | 'page_reloaded';

/**
 * What a later read settled for a record whose answer did not (spec 7.4 "Settled by"):
 * a Pause by D-8, a Turn on by its revision and key, a Stop by D-4/D-5, a handoff by the
 * released guard, a Run now or a live change by a command carrying its key, a saved-run
 * save by a later read of that run. Decided in store/fences.ts.
 */
export type ReadSettlement = 'off_confirmed' | 'applied' | 'not_applied' | 'stopped' | 'recorded';

/** The request frozen at the click: a resend sends exactly these bytes. */
export interface IntentRequest {
  readonly endpoint: WriteEndpointId;
  readonly path: ApiPath;
  /** The JSON text of the body. */
  readonly body: string;
}

/** The answer as the boundary parsed it (api/wire.ts). */
export interface IntentAnswer {
  readonly status: number;
  readonly body: unknown;
  readonly contract: 'exact' | 'drift';
  readonly issues: readonly string[];
}

/** Kind-specific facts of the request, copied from the body the person's click froze. */
export interface IntentMeta {
  readonly operationId?: string;
  readonly expectedRevision?: number;
  readonly pid?: number;
  readonly created?: number;
  readonly preset?: string;
  readonly setupBattleId?: string;
  readonly evidenceRevision?: string;
  readonly battleId?: string;
  readonly generation?: string;
  readonly action?: string;
  /** A saved-run save: the run and the profile revision it was made against (compare-and-set). */
  readonly blueprint?: string;
  readonly profileRevision?: string;
  /** The dotted-path changes a saved-run or live save sent, to judge a later read against. */
  readonly changes?: Readonly<Record<string, unknown>>;
  /**
   * A Runs save (F6R-02): the draft scope it was made from and the raw values typed for the
   * sent paths, so a confirmation clears exactly those, also after a reload.
   */
  readonly draftScope?: string;
  readonly draft?: Readonly<Record<string, string | boolean>>;
  /** A live save: the battle's label as the read named it, to identify an earlier battle's line. */
  readonly battleLabel?: string;
  /** F6-11 RL1, RL2: a live reset: the paths sent as `null` (the battle's starting value). */
  readonly resetPaths?: readonly string[];
  /**
   * A Profiles write (profiles brief P1 to P5): the account id, profile name or destination
   * it names, the tier it sets, and whether the destination was already listed at the click.
   */
  readonly name?: string;
  readonly maxTier?: number;
  readonly existed?: boolean;
  /** F6-14 PE1: a profile activation with `name: null` (use no profile). */
  readonly unbind?: boolean;
  /**
   * A settings save (collection brief C1 to C4): the module it saved and the values it
   * sent, to know the answer's receipt (the module holds them) and to judge a later read.
   */
  readonly module?: string;
  readonly values?: Readonly<Record<string, unknown>>;
  /** A settings save or profile patch: the settings or profile revision it was made from. */
  readonly baseRevision?: string;
  /** A profile patch: the path it writes, whether it deletes it, and the value it writes. */
  readonly patchPath?: readonly string[];
  readonly patchRemove?: boolean;
  readonly patchValue?: unknown;
  /**
   * Emulators page (M2, M3, M4): the address an adopt sets and the instance it sets it on,
   * and the emulator family and its instance index or key a launch or prepare names.
   */
  readonly serial?: string;
  readonly instance?: string;
  readonly emulator?: string;
  readonly index?: string;
}

export interface IntentRecord {
  readonly id: string;
  readonly kind: IntentKind;
  /** Requests on the same target replace each other's records (S-011). */
  readonly target: string;
  /** The page context the request was made in (null: before the page knew one). */
  readonly context: PageContext | null;
  /** The context epoch of the first send; -1 for a record restored after a reload. */
  readonly epoch: number;
  readonly request: IntentRequest;
  /** The idempotency key the body carries, when it carries one. */
  readonly key: string | null;
  readonly meta: IntentMeta;
  /** Sequence point of the first send (0 for a record restored after a reload: before this page). */
  readonly firstSentSeq: number;
  /** Sequence point of the latest send (a person's resend takes a new one). */
  readonly sentSeq: number;
  /** How many times a person sent it: 1, plus each explicit resend. */
  readonly sends: number;
  readonly phase: IntentPhase;
  readonly answer: IntentAnswer | null;
  readonly answerSeq: number | null;
  /** Browser clock (ms) when the answer arrived: the dated past result of 7.3. */
  readonly answeredAtMs: number | null;
  readonly lostReason: LostReason | null;
  /**
   * The sequence point at which this page learned the answer was lost (the window ended,
   * the connection failed). A read must start after it to say anything about the outcome:
   * one started while the request was still in flight may predate the server's commit.
   * Absent for a record restored after a reload (every read of the new page is later).
   */
  readonly lostSeq?: number | null;
  /** A later read settled this record's unknown outcome (monotonic; never undone). */
  readonly readSettlement: ReadSettlement | null;
  /** The sequence point of the newer request that replaced this record (S-003, S-005, S-011). */
  readonly supersededBySeq: number | null;
  /** The person dismissed its line (OD-10): the line goes, every fact it carries stays. */
  readonly dismissed: boolean;
  /** Read back from session storage after a reload (spec 7.6). */
  readonly restored: boolean;
  /**
   * F6R-01: the newest refusal of one attempt of a request that was sent more than once (a
   * person's explicit resend). It proves only that this attempt applied nothing, never that
   * an earlier attempt did not: the record keeps its unknown outcome, body, key and resend.
   */
  readonly attemptRefusal?: IntentAnswer | null;
  /**
   * The person chose to continue from the newest read while this record's outcome is still
   * unknown (an unchanged read never proves a lost write failed: it may still commit). The
   * outcome stays unknown and nothing is resent; only the gate that waited for it opens.
   */
  readonly accepted?: boolean;
}

/** A new request: what the machine sends and records. */
export interface IntentDraft {
  readonly kind: IntentKind;
  readonly target: string;
  readonly endpoint: WriteEndpointId;
  readonly path: ApiPath;
  readonly body: Readonly<Record<string, unknown>>;
  readonly key: string | null;
  readonly meta: IntentMeta;
  readonly context: PageContext | null;
  /** Kinds on other targets this request supersedes (Turn input off supersedes a Turn on). */
  readonly supersedes: readonly IntentKind[];
}

/** True while the outcome is not known: in flight, or unconfirmed. */
export function unresolved(record: IntentRecord): boolean {
  return record.phase === 'sending' || record.phase === 'unconfirmed';
}

/** This page can still receive an authoritative answer, even after positive readback. */
export function lateAnswerPossible(
  record: Pick<IntentRecord, 'phase' | 'answer' | 'lostReason' | 'restored'>,
): boolean {
  return (
    record.answer === null &&
    !record.restored &&
    (record.phase === 'sending' ||
      (record.phase === 'unconfirmed' && record.lostReason === 'window_expired'))
  );
}
