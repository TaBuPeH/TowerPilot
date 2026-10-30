import { sameJson } from './json.ts';
import { queryFromHash } from './hashQuery.ts';
import {
  entries,
  finite,
  hasOwn,
  id,
  list,
  own,
  ownPath,
  record,
  revision,
  stringList,
  text,
} from './read.ts';

/**
 * The Recognition page's rules (screens brief R1 to R8, roadmap item 9). What is saved,
 * what is verified on screen, what blocks a run, and what an operation did are different
 * facts: a captured file, a self-match, a written report entry or a finished worker is never
 * shown as verified recognition, a restored setup or a recognized Home. Every answer is read
 * here; the store and the page only show what these readers return.
 */

// ------------------------------------------------------------------ choices and bodies

/** Recognition's optional run query; the destination validates membership in its current runs. */
export function recognitionHref(run: string | null): string {
  return run === null ? '#recognition' : `#recognition?run=${encodeURIComponent(run)}`;
}

/** Empty, malformed or unsupported queries leave the normal picker open. */
export function recognitionRunFromHash(hash: string): string | null {
  if (!/^#\/?recognition(?:\?|$)/u.test(hash)) return null;
  const query = queryFromHash(hash);
  if (Object.keys(query).some((key) => key !== 'run')) return null;
  const run = query['run'];
  return run === undefined || run === '' ? null : run;
}

/** The basic-scan features (legacy calibration phases): cards, modules, global presets, guardians, bots, workshop. */
export const SCAN_FEATURES = ['c', 'm', 'g', 'u', 'b', 'w'] as const;
export type ScanFeature = (typeof SCAN_FEATURES)[number];

/** A person's remembered scan choices for one emulator and account (R6). */
export interface ScanChoices {
  readonly features: readonly ScanFeature[];
  /** Start the unfinished steps over (fresh) or resume them. */
  readonly fresh: boolean;
  /** Replace images that already exist (never by default). */
  readonly overwrite: boolean;
  /** Let the scan tap this emulator's menus. */
  readonly navigation: boolean;
}

const DEFAULT_FEATURES: readonly ScanFeature[] = ['c', 'm'];
export const DEFAULT_CHOICES: ScanChoices = {
  features: DEFAULT_FEATURES,
  fresh: true,
  overwrite: false,
  navigation: false,
};

/** Choices read back from their raw draft values (`feature:c` booleans, `fresh` ...). */
export function scanChoices(raw: Readonly<Record<string, string | boolean>>): ScanChoices {
  const flag = (key: string, fallback: boolean) => {
    const value = raw[key];
    return typeof value === 'boolean' ? value : fallback;
  };
  return {
    features: SCAN_FEATURES.filter((feature) =>
      flag(`feature:${feature}`, DEFAULT_CHOICES.features.includes(feature)),
    ),
    fresh: flag('fresh', DEFAULT_CHOICES.fresh),
    overwrite: flag('overwrite', DEFAULT_CHOICES.overwrite),
    navigation: flag('navigation', DEFAULT_CHOICES.navigation),
  };
}

/** A launch of the calibrator (POST /api/calibrate/start), without its context and key. */
export type StartPlan =
  /** R1, R7: learn screens: extract the installed artwork and verify Home and the menus; with `battle`, also capture controls in a consented test battle. */
  | { readonly kind: 'learn'; readonly battle: boolean; readonly overwrite: boolean }
  /** R6: the basic scan with the chosen features. */
  | { readonly kind: 'basic'; readonly choices: ScanChoices }
  /** R8: phase 2, the equipped modules (records the slots, unequips, scans, restores). */
  | { readonly kind: 'equipped'; readonly overwrite: boolean }
  /** R4: prepare one run's missing battle controls in a consented test battle. */
  | { readonly kind: 'prepare'; readonly run: string };

/** The action fields of a start body (R1 learn: bootstrap+flows+fresh, phases c,m, no overwrite, navigation consent). */
export function startFields(plan: StartPlan): Readonly<Record<string, unknown>> {
  switch (plan.kind) {
    case 'learn':
      return {
        bootstrap: true,
        flows: plan.battle,
        phases: 'c,m',
        fresh: true,
        overwrite: plan.overwrite,
        allow_navigation: true,
      };
    case 'basic':
      return {
        phases: plan.choices.features.join(','),
        fresh: plan.choices.fresh,
        overwrite: plan.choices.overwrite,
        allow_navigation: plan.choices.navigation,
      };
    case 'equipped':
      return { phases: 'e', fresh: true, overwrite: plan.overwrite, allow_navigation: true };
    case 'prepare':
      return {
        bootstrap: true,
        flows: true,
        battle_only: true,
        phases: 'c,m',
        overwrite: false,
        allow_navigation: true,
        selected_run: plan.run,
      };
  }
}

/** Whether a start taps the emulator (navigation, a test battle): it needs explicit consent. */
export function startTaps(plan: StartPlan): boolean {
  return plan.kind !== 'basic' || plan.choices.navigation;
}

/**
 * Whether the server refuses this start while `allow_taps` is off for the instance: every
 * calibrator start walks the game's menus, and only the navigation consent stands in for
 * the setting (start_taps_off_409, start_taps_off_with_the_navigation_consent_is_accepted).
 */
export function refusedWhileTapsOff(plan: StartPlan): boolean {
  return startFields(plan)['allow_navigation'] !== true;
}

/**
 * Whether `GET /api/battles/active` names a battle (prepared or active): true, false for
 * none (`battle: null`), null when the answer does not say. A calibrator start taps this
 * emulator and may start its own test battle: never over a battle it did not start.
 */
export function battleTracked(body: unknown): boolean | null {
  if (record(body) === undefined || !hasOwn(body, 'battle')) return null;
  const battle = own(body, 'battle');
  if (battle === null) return false;
  return record(battle) === undefined ? null : true;
}

/** Why a launch of this page may not be sent now (null: it may). */
export type LaunchBlock =
  | 'context_unknown'
  | 'launch_in_flight'
  | 'status_not_read'
  | 'restore_pending'
  | 'operation_active'
  | 'launch_unresolved'
  | 'process_listed'
  | 'process_list_unavailable'
  | 'battle_not_read'
  | 'battle_tracked'
  | 'taps_off'
  | 'no_features';

export interface LaunchFacts {
  /** A config read of this page named the context the page shows. */
  readonly contextConfirmed: boolean;
  /** A launch of this page is in flight. */
  readonly launchSending: boolean;
  /** An earlier launch's outcome is unknown and nobody chose to go on without it. */
  readonly launchUnresolved: boolean;
  /** The newest calibrator status read (null: none answered). */
  readonly status: CalibrateStatusView | null;
  /** The followed or latest operation is still working. */
  readonly operationWorking: boolean;
  /** core/connection idleForConnectionChange over the newest status read. */
  readonly runner: 'ready' | 'process_listed' | 'process_list_unavailable';
  /** battleTracked over the newest battle read. */
  readonly battle: boolean | null;
}

export type LaunchKind =
  { readonly kind: 'start'; readonly plan: StartPlan } | { readonly kind: 'observe' };

/**
 * R1, R5 to R8 and CLAUDE hard rules 1, 3 and 8: a launch waits for a known context, one
 * request at a time, a readable calibrator status, no restoration pending, no operation
 * working, no launch whose outcome is unknown (a replacement is never started on its own)
 * and no runner on the device. A start taps: it also needs a battle read naming none and,
 * without the navigation consent, `allow_taps`. Observing never taps.
 */
export function launchBlock(facts: LaunchFacts, launch: LaunchKind): LaunchBlock | null {
  if (!facts.contextConfirmed) return 'context_unknown';
  if (facts.launchSending) return 'launch_in_flight';
  if (facts.status === null) return 'status_not_read';
  if (facts.status.restorePending) return 'restore_pending';
  if (facts.operationWorking || facts.status.running) return 'operation_active';
  if (facts.launchUnresolved) return 'launch_unresolved';
  if (facts.runner !== 'ready') return facts.runner;
  if (launch.kind === 'observe') return null;
  if (launch.plan.kind === 'basic' && launch.plan.choices.features.length === 0)
    return 'no_features';
  if (facts.battle === null) return 'battle_not_read';
  if (facts.battle) return 'battle_tracked';
  if (facts.status.tapsAllowed === false && refusedWhileTapsOff(launch.plan)) return 'taps_off';
  return null;
}

export type WatchCheck =
  | { readonly kind: 'valid'; readonly seconds: number }
  | {
      readonly kind: 'invalid';
      readonly reason: 'not_whole' | 'below_min' | 'above_max' | 'empty';
    };

/** R5: a watch length as typed: a whole number of seconds from 1 to 600, never clamped. */
export function checkWatch(raw: string): WatchCheck {
  const value = raw.trim();
  if (value === '') return { kind: 'invalid', reason: 'empty' };
  if (!/^\d+$/u.test(value)) return { kind: 'invalid', reason: 'not_whole' };
  const seconds = Number(value);
  if (seconds < 1) return { kind: 'invalid', reason: 'below_min' };
  if (seconds > 600) return { kind: 'invalid', reason: 'above_max' };
  return { kind: 'valid', seconds };
}

/** The action fields of an observe body: `{}` captures now (two frames), `{watch}` watches. */
export function observeFields(
  watch: number | null,
  run: string | null,
): Readonly<Record<string, unknown>> {
  return {
    ...(watch === null ? {} : { watch }),
    ...(run === null ? {} : { selected_run: run }),
  };
}

// ------------------------------------------------------------------ operations

export interface OperationView {
  readonly id: string;
  readonly kind: string | null;
  readonly status: string | null;
  readonly step: string | null;
  readonly revision: number | null;
  readonly cancellable: boolean;
  readonly error: { readonly code: string | null; readonly message: string | null } | null;
  readonly recoveryAction: string | null;
  readonly idempotencyKey: string | null;
  readonly context: {
    readonly instance: string | null;
    readonly account: string | null;
    readonly profile: string | null;
    readonly serial: string | null;
  };
  /** What the request asked for (phases, bootstrap, flows, battle_only, watch, selected_run ...). */
  readonly request: {
    readonly phases: string | null;
    readonly bootstrap: boolean;
    readonly flows: boolean;
    readonly battleOnly: boolean;
    readonly overwrite: boolean;
    readonly navigation: boolean;
    readonly watch: number | null;
    readonly run: string | null;
  };
  readonly createdAt: number | null;
  readonly updatedAt: number | null;
  readonly workerPid: number | null;
  readonly apply: {
    readonly status: string | null;
    readonly requestRevision: number | null;
    readonly error: string | null;
  };
}

export function operationView(raw: unknown): OperationView | null {
  const operationId = id(own(raw, 'id'));
  if (operationId === undefined) return null;
  const error = own(raw, 'error');
  const request = own(raw, 'request');
  const refs = own(raw, 'result_refs');
  const applyError = own(refs, 'apply_error');
  return {
    id: operationId,
    kind: id(own(raw, 'kind')) ?? null,
    status: id(own(raw, 'status')) ?? null,
    step: id(own(raw, 'step')) ?? null,
    revision: revision(own(raw, 'revision')) ?? null,
    cancellable: own(raw, 'cancellable') === true,
    error:
      record(error) === undefined
        ? null
        : {
            code: id(own(error, 'code')) ?? null,
            message: text(own(error, 'message')) ?? null,
          },
    recoveryAction: text(own(raw, 'recovery_action')) ?? null,
    idempotencyKey: id(own(raw, 'idempotency_key')) ?? null,
    context: {
      instance: id(ownPath(raw, 'context', 'instance')) ?? null,
      account: id(ownPath(raw, 'context', 'account')) ?? null,
      profile: id(ownPath(raw, 'context', 'profile')) ?? null,
      serial: id(ownPath(raw, 'context', 'serial')) ?? null,
    },
    request: {
      phases: text(own(request, 'phases')) ?? null,
      bootstrap: own(request, 'bootstrap') === true,
      flows: own(request, 'flows') === true,
      battleOnly: own(request, 'battle_only') === true,
      overwrite: own(request, 'overwrite') === true,
      navigation: own(request, 'allow_navigation') === true,
      watch: finite(own(request, 'watch')) ?? null,
      run: id(own(request, 'selected_run')) ?? id(ownPath(raw, 'context', 'selected_run')) ?? null,
    },
    createdAt: finite(own(raw, 'created_at')) ?? null,
    updatedAt: finite(own(raw, 'updated_at')) ?? null,
    workerPid: finite(ownPath(raw, 'worker', 'pid')) ?? null,
    apply: {
      status: id(own(refs, 'apply_status')) ?? null,
      requestRevision: revision(own(refs, 'apply_request_revision')) ?? null,
      error:
        text(applyError) ??
        text(own(applyError, 'message')) ??
        text(own(applyError, 'error')) ??
        null,
    },
  };
}

/**
 * R2: where an operation stands, from its own status and step only. `applied` needs the
 * step the apply wrote; a worker exit is never success, restored or Home.
 */
export type Stage =
  | 'accepted'
  | 'running'
  | 'stopping'
  | 'awaiting_apply'
  | 'applied'
  | 'succeeded'
  | 'failed'
  | 'interrupted'
  | 'cancelled'
  | 'restoration_required'
  | 'unknown';

const STAGES: readonly Stage[] = [
  'accepted',
  'running',
  'awaiting_apply',
  'succeeded',
  'failed',
  'interrupted',
  'cancelled',
  'restoration_required',
];

export function stageOf(operation: OperationView): Stage {
  if (operation.status === 'cancelling') return 'stopping';
  if (operation.status === 'succeeded' && operation.step === 'applied') return 'applied';
  return STAGES.find((stage) => stage === operation.status) ?? 'unknown';
}

/** The operation is still working (its worker may tap or change equipment). */
export function operationActive(operation: OperationView): boolean {
  const stage = stageOf(operation);
  return stage === 'accepted' || stage === 'running' || stage === 'stopping';
}

export type ApplyBlock =
  | 'context_unknown'
  | 'not_awaiting_apply'
  | 'revision_unknown'
  | 'restore_pending'
  | 'apply_in_flight'
  | 'apply_unresolved'
  | 'drafts_open';

/**
 * R1, R4: an apply of an operation's discoveries writes the profile: only an operation the
 * server accepts an apply for (awaiting apply, or a failed or interrupted capture whose
 * completed discoveries are kept: backend/dashboard.py api_calibrate_apply require_revision)
 * that has not been applied yet, at a known revision, with no restoration pending, one apply
 * at a time, never over an apply whose outcome is unknown, and never while a saved-settings
 * editor of this context holds unsaved values (the apply would change what it was based on).
 */
const APPLICABLE: readonly Stage[] = ['awaiting_apply', 'failed', 'interrupted'];

/** The operation's discoveries may be applied (its capture outcome is kept either way). */
export function applicable(operation: OperationView): boolean {
  return APPLICABLE.includes(stageOf(operation)) && operation.apply.status !== 'succeeded';
}
export function applyBlock(facts: {
  readonly contextConfirmed: boolean;
  readonly operation: OperationView | null;
  readonly restorePending: boolean;
  readonly applySending: boolean;
  readonly applyUnresolved: boolean;
  readonly draftsOpen: boolean;
}): ApplyBlock | null {
  if (!facts.contextConfirmed) return 'context_unknown';
  if (facts.applySending) return 'apply_in_flight';
  if (facts.operation === null || !applicable(facts.operation)) return 'not_awaiting_apply';
  if (facts.operation.revision === null) return 'revision_unknown';
  if (facts.restorePending) return 'restore_pending';
  if (facts.applyUnresolved) return 'apply_unresolved';
  if (facts.draftsOpen) return 'drafts_open';
  return null;
}

export type StopBlock =
  | 'context_unknown'
  | 'not_running'
  | 'not_cancellable'
  | 'revision_unknown'
  | 'already_stopping'
  | 'stop_in_flight';

/** R2, R8 (C6): Stop is offered for a working operation the server says can be cancelled. */
export function stopBlock(facts: {
  readonly contextConfirmed: boolean;
  readonly operation: OperationView | null;
  readonly stopSending: boolean;
}): StopBlock | null {
  if (!facts.contextConfirmed) return 'context_unknown';
  if (facts.stopSending) return 'stop_in_flight';
  const operation = facts.operation;
  if (operation === null || !operationActive(operation)) return 'not_running';
  if (stageOf(operation) === 'stopping') return 'already_stopping';
  if (!operation.cancellable) return 'not_cancellable';
  if (operation.revision === null) return 'revision_unknown';
  return null;
}

/**
 * Observations saved by no tracked operation (a legacy run): the plain status read names
 * no operation but carries a report or phase state. Only these may be applied as
 * `{saved_observations: true}`; the server refuses it while any operation is tracked.
 */
export function untrackedObservations(status: CalibrateStatusView): boolean {
  return (
    status.operation === null &&
    !status.running &&
    (status.report.length > 0 || status.phases.length > 0 || status.player.length > 0)
  );
}

// ------------------------------------------------------------------ status

export interface ReportEntry {
  readonly rel: string;
  readonly status: string | null;
  /** The calibrator's own verdict; a self score alone never makes an entry verified. */
  readonly verified: boolean;
  readonly phase: string | null;
  readonly screen: string | null;
  readonly self: number | null;
  readonly next: number | null;
  readonly reason: string | null;
}

export interface CalibrateStatusView {
  readonly running: boolean;
  readonly restorePending: boolean;
  readonly tapsAllowed: boolean | null;
  readonly selection: {
    readonly mode: string | null;
    readonly phases: readonly string[];
    readonly flows: boolean;
  } | null;
  readonly activity: { readonly phase: string | null; readonly at: number | null } | null;
  readonly operation: OperationView | null;
  readonly artifactErrors: readonly (readonly [string, string])[];
  readonly report: readonly ReportEntry[];
  readonly phases: readonly {
    readonly name: string;
    readonly status: string | null;
    readonly error: string | null;
  }[];
  readonly finalScreen: {
    readonly name: string | null;
    readonly verified: boolean;
    readonly at: number | null;
    readonly error: string | null;
  } | null;
  readonly setupBattle: {
    readonly status: string | null;
    readonly startedAt: number | null;
    readonly finishedAt: number | null;
    readonly reason: string | null;
  } | null;
  /** What the scan discovered about the player (card presets, weapons, abilities ...), as written. */
  readonly player: readonly (readonly [string, unknown])[];
  readonly modules: readonly {
    readonly slug: string;
    readonly rarity: string | null;
    readonly slot: number | null;
  }[];
  readonly cards: readonly { readonly name: string; readonly slot: number | null }[];
}

function reportEntries(report: unknown): readonly ReportEntry[] {
  return (list(own(report, 'entries')) ?? []).flatMap((entry): ReportEntry[] => {
    const rel = id(own(entry, 'rel'));
    if (rel === undefined) return [];
    return [
      {
        rel,
        status: id(own(entry, 'status')) ?? null,
        verified: own(entry, 'verified') === true,
        phase: id(own(entry, 'phase')) ?? null,
        screen: id(own(entry, 'screen')) ?? null,
        self: finite(own(entry, 'self')) ?? null,
        next: finite(own(entry, 'next')) ?? null,
        reason: text(own(entry, 'reason')) ?? null,
      },
    ];
  });
}

/** `GET /api/calibrate/status[?operation_id=]` (a 200 body), else null. */
export function calibrateStatusView(body: unknown): CalibrateStatusView | null {
  if (record(body) === undefined || typeof own(body, 'running') !== 'boolean') return null;
  const state = own(body, 'state');
  const selection = own(body, 'selection');
  const activity = own(body, 'activity') ?? own(state, 'activity');
  const final = own(state, 'final_screen');
  const battle = own(state, 'setup_battle');
  const taps = own(body, 'taps_allowed');
  return {
    running: own(body, 'running') === true,
    restorePending: own(body, 'restore_pending') === true,
    tapsAllowed: typeof taps === 'boolean' ? taps : null,
    selection:
      record(selection) === undefined
        ? null
        : {
            mode: id(own(selection, 'mode')) ?? null,
            phases: stringList(own(selection, 'phases')) ?? [],
            flows: own(selection, 'flows') === true,
          },
    activity:
      record(activity) === undefined
        ? null
        : { phase: id(own(activity, 'phase')) ?? null, at: finite(own(activity, 't')) ?? null },
    operation: operationView(own(body, 'operation')),
    artifactErrors: entries(own(body, 'artifact_errors')).flatMap(([name, message]) =>
      typeof message === 'string' ? [[name, message] as const] : [],
    ),
    report: reportEntries(own(body, 'report')),
    phases: entries(own(state, 'phases')).map(([name, value]) => ({
      name,
      status: id(own(value, 'status')) ?? null,
      error: text(own(value, 'error')) ?? null,
    })),
    finalScreen:
      record(final) === undefined
        ? null
        : {
            name: id(own(final, 'name')) ?? null,
            verified: own(final, 'verified') === true,
            at: finite(own(final, 'observed_at')) ?? null,
            error: text(own(final, 'error')) ?? null,
          },
    setupBattle:
      record(battle) === undefined
        ? null
        : {
            status: id(own(battle, 'status')) ?? null,
            startedAt: finite(own(battle, 'started_at')) ?? null,
            finishedAt: finite(own(battle, 'finished_at')) ?? null,
            reason: text(own(battle, 'reason')) ?? null,
          },
    player: entries(own(state, 'player')),
    modules: entries(ownPath(body, 'manifest', 'modules')).map(([slug, value]) => ({
      slug,
      rarity: id(own(value, 'rarity')) ?? null,
      slot: finite(own(value, 'slot')) ?? null,
    })),
    cards: entries(ownPath(body, 'card_manifest', 'cards')).map(([name, value]) => ({
      name,
      slot: finite(own(value, 'slot')) ?? null,
    })),
  };
}

/**
 * Roadmap item 9: the targeted evidence the report names, grouped by kind. Only entries
 * the calibrator wrote are listed; an absent group is "not in this report", never a gap
 * the page computed. Gap summaries and scan metrics are not published (R-06).
 */
export const EVIDENCE_GROUPS = ['range', 'first_perk', 'module', 'rare_dialog'] as const;
export type EvidenceGroup = (typeof EVIDENCE_GROUPS)[number];

export function evidenceGroup(rel: string): EvidenceGroup | null {
  if (rel.startsWith('stats/') && rel.includes('range')) return 'range';
  if (rel.startsWith('buttons/perk') || rel.startsWith('perks/')) return 'first_perk';
  if (rel.startsWith('modules/')) return 'module';
  if (rel.startsWith('dialogs/')) return 'rare_dialog';
  return null;
}

// ------------------------------------------------------------------ answers

type Answer = { readonly status: number; readonly body: unknown };

export interface Refusal {
  readonly kind: 'refused';
  readonly status: number;
  readonly code: string | null;
  /** The mutation fence names the current target; the operation's frozen fence does not. */
  readonly currentContext: Readonly<
    Record<'instance' | 'account' | 'profile' | 'serial', string | null>
  > | null;
  readonly error: string | null;
  /** navigation_images_missing: the images still to capture, as the server listed them. */
  readonly missing: readonly string[];
  /** The operation as it is now, when the refusal names one (the Stop contract). */
  readonly operation: OperationView | null;
}

/** A JSON 4xx `{ok:false, code, error[, operation][, missing]}`: this request did nothing. */
function refusal(answer: Answer): Refusal | null {
  if (answer.status < 400 || answer.status > 499 || record(answer.body) === undefined) return null;
  if (own(answer.body, 'ok') === true) return null;
  const current = record(own(answer.body, 'current_context'));
  return {
    kind: 'refused',
    status: answer.status,
    code: id(own(answer.body, 'code')) ?? null,
    currentContext:
      own(answer.body, 'code') === 'context_changed' && current !== undefined
        ? {
            instance: text(own(current, 'instance')) ?? null,
            account: text(own(current, 'account')) ?? null,
            profile: text(own(current, 'profile')) ?? null,
            serial: text(own(current, 'serial')) ?? null,
          }
        : null,
    error: text(own(answer.body, 'error')) ?? null,
    missing: stringList(own(answer.body, 'missing')) ?? [],
    operation: operationView(own(answer.body, 'operation')),
  };
}

/** The server's own words on an answer that proves nothing (a 5xx JSON body). */
export function unknownWords(answer: Answer): string | null {
  if (record(answer.body) === undefined) return null;
  return text(own(answer.body, 'error')) ?? text(own(answer.body, 'message')) ?? null;
}

export type LaunchAnswer =
  /** The operation exists (its worker may not have started yet): its id and revision. */
  | { readonly kind: 'accepted'; readonly operation: OperationView }
  | Refusal
  | { readonly kind: 'unknown' };

/** POST /api/calibrate/start | observe (and /api/screen-analysis/start): a C5 receipt. */
export function launchAnswer(answer: Answer): LaunchAnswer {
  if (answer.status === 200 && own(answer.body, 'ok') === true) {
    const operation = operationView(own(answer.body, 'operation'));
    const named = id(own(answer.body, 'operation_id'));
    return operation !== null && named === operation.id
      ? { kind: 'accepted', operation }
      : { kind: 'unknown' };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}

export type ApplyAnswer =
  | {
      readonly kind: 'applied';
      readonly operation: OperationView | null;
      readonly profile: string | null;
      /** The run checks still open after the apply, as the server listed them. */
      readonly remaining: readonly string[];
      readonly readinessReady: boolean | null;
    }
  | Refusal
  | { readonly kind: 'unknown' };

/**
 * POST /api/calibrate/apply. A 400 `apply_failed` is a write that failed (the operation is
 * back to awaiting_apply: refused); a 500 is unknown, settled only by a later read.
 */
export function applyAnswer(answer: Answer): ApplyAnswer {
  if (
    answer.status === 200 &&
    own(answer.body, 'ok') === true &&
    own(answer.body, 'applied') === true
  ) {
    const remaining = (list(own(answer.body, 'remaining_run_checks')) ?? []).flatMap((entry) => {
      const named = text(entry) ?? text(own(entry, 'message')) ?? id(own(entry, 'preset'));
      return named === undefined ? [] : [named];
    });
    const ready = ownPath(answer.body, 'readiness', 'ready');
    return {
      kind: 'applied',
      operation: operationView(own(answer.body, 'operation')),
      profile: id(own(answer.body, 'profile')) ?? null,
      remaining,
      readinessReady: typeof ready === 'boolean' ? ready : null,
    };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}

export type CalibrateStopAnswer =
  /** Answered: the stop's own state (`stopping` asked the worker; `released` let go of an unverifiable one). */
  | {
      readonly kind: 'answered';
      readonly state: string | null;
      readonly operation: OperationView | null;
    }
  | Refusal
  | { readonly kind: 'unknown' };

/** POST /api/calibrate/stop (C6): stopping, released and stopped are never the same claim. */
export function calibrateStopAnswer(answer: Answer): CalibrateStopAnswer {
  if (answer.status === 200 && own(answer.body, 'ok') === true) {
    return {
      kind: 'answered',
      state: id(ownPath(answer.body, 'stop', 'state')) ?? null,
      operation: operationView(own(answer.body, 'operation')),
    };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}

export type TemplateWriteAnswer =
  /** The file was written. Its provenance and its recognition are not proven by this. */
  | {
      readonly kind: 'written';
      readonly rel: string;
      readonly width: number | null;
      readonly height: number | null;
      readonly secondBest: number | null;
    }
  | Refusal
  | { readonly kind: 'unknown' };

/** POST /api/template/<rel> `{ts, x, y, w, h, overwrite?}`. */
export function templateWriteAnswer(answer: Answer): TemplateWriteAnswer {
  if (answer.status === 200 && own(answer.body, 'ok') === true) {
    const rel = id(own(answer.body, 'rel'));
    return rel === undefined
      ? { kind: 'unknown' }
      : {
          kind: 'written',
          rel,
          width: finite(own(answer.body, 'width')) ?? null,
          height: finite(own(answer.body, 'height')) ?? null,
          secondBest: finite(own(answer.body, 'second_best')) ?? null,
        };
  }
  return refusal(answer) ?? { kind: 'unknown' };
}

// ------------------------------------------------------------------ settlement by a later read

/**
 * Saved observations have no operation receipt. Only newly present discoveries in the
 * original profile can settle their lost answer; an unchanged read never proves failure
 * or success. wall_bar belongs to machine config, so it supplies no profile evidence.
 * This compares observed values, without predicting the server's profile merge.
 */
export function savedApplyRecordedIn(
  before: unknown,
  observations: unknown,
  after: unknown,
): boolean {
  const discoveries = Object.fromEntries(
    entries(observations).filter(([key]) => key !== 'wall_bar'),
  );
  if (
    record(before) === undefined ||
    record(after) === undefined ||
    entries(discoveries).length === 0 ||
    sameJson(before, after)
  )
    return false;
  const contains = (found: unknown, expected: unknown): boolean =>
    record(expected) === undefined
      ? sameJson(found, expected)
      : record(found) !== undefined &&
        entries(expected).every(([key, value]) => contains(own(found, key), value));
  return (
    !contains(own(before, 'player'), discoveries) && contains(own(after, 'player'), discoveries)
  );
}

/**
 * A start or observe whose answer was lost: a later status read naming an operation that
 * carries the request's idempotency key proves the launch was recorded (and which operation
 * it is). Anything else proves nothing.
 */
export function launchRecordedIn(statusBody: unknown, key: string): OperationView | null {
  const operation = operationView(own(statusBody, 'operation'));
  return operation !== null && operation.idempotencyKey === key ? operation : null;
}

/** An apply whose answer was lost: the operation's own record of an apply of that revision. */
export function applyRecordedIn(
  statusBody: unknown,
  operationId: string,
  sentRevision: number,
): boolean {
  const operation = operationView(own(statusBody, 'operation'));
  return (
    operation !== null &&
    operation.id === operationId &&
    operation.apply.status === 'succeeded' &&
    operation.apply.requestRevision === sentRevision
  );
}

/** A stop whose answer was lost: the operation moved past the sent revision into stopping or stopped. */
export function stopRecordedIn(
  statusBody: unknown,
  operationId: string,
  sentRevision: number,
): boolean {
  const operation = operationView(own(statusBody, 'operation'));
  return (
    operation !== null &&
    operation.id === operationId &&
    operation.revision !== null &&
    operation.revision > sentRevision &&
    (operation.status === 'cancelling' ||
      operation.status === 'cancelled' ||
      operation.step === 'stop_requested')
  );
}
