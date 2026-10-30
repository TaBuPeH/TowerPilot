import type { ContextRead } from './connection.ts';
import type { Fact } from './facts.ts';
import type { GateRead } from './gate.ts';
import {
  collectionPermitObservedAt,
  setupPermitObservedAt,
  type CollectionGuardPermit,
  type SetupBattleGuardPermit,
} from './guards.ts';
import { entries, hasOwn, id, list, own, ownPath, record, stringList, text } from './read.ts';

export interface ReadinessView {
  readonly line:
    | 'readiness_unknown'
    | 'readiness_check_failed'
    | 'needs_setup'
    | 'ready'
    | 'images_missing'
    | 'not_ready';
  readonly imageCount: number;
  readonly imageRows: readonly unknown[];
  readonly otherRows: readonly unknown[];
  readonly missing: unknown;
  readonly message: unknown;
  readonly nextAction: unknown;
  readonly actionLabel: unknown;
  readonly advisory: unknown;
  readonly diagnostics: unknown;
  readonly error: unknown;
}

/** F-039: count requirement rows, not filenames or missing[] strings. */
export function readiness(entry: unknown, source: 'runs' | 'check' = 'runs'): ReadinessView {
  // backend/dashboard.py _runs_readiness; backend/player/readiness.py check.
  const raw = {
    missing: own(entry, 'missing'),
    message: own(entry, 'message'),
    nextAction: own(entry, 'next_action'),
    actionLabel: own(entry, 'action_label'),
    advisory: own(entry, 'advisory'),
    diagnostics: own(entry, 'diagnostics'),
    error: own(entry, 'error'),
  };
  const empty = { imageCount: 0, imageRows: [], otherRows: [], ...raw };
  const missing = stringList(raw.missing);
  if (
    !hasOwn(entry, 'details') &&
    missing?.some((line) => line.startsWith('readiness check failed')) === true
  )
    return { line: 'readiness_check_failed', ...empty };
  if (
    own(entry, 'setup_required') === true ||
    (source === 'check' &&
      own(entry, 'ready') === false &&
      (list(own(entry, 'diagnostics'))?.length ?? 0) > 0)
  )
    return { line: 'needs_setup', ...empty };
  if (own(entry, 'ready') === true) return { line: 'ready', ...empty };
  // api_readiness returns check().missing rows; _runs_readiness projects those into details.
  const details = list(own(entry, source === 'check' ? 'missing' : 'details'));
  if (source === 'check' && hasOwn(entry, 'error')) return { line: 'readiness_unknown', ...empty };
  if (own(entry, 'ready') !== false || details === undefined)
    return { line: 'readiness_unknown', ...empty };
  const imageRows: unknown[] = [];
  const otherRows: unknown[] = [];
  for (const row of details) {
    const alternatives = stringList(own(row, 'alternatives'));
    if (record(row) === undefined || alternatives === undefined)
      return { line: 'readiness_unknown', ...empty };
    if (alternatives.some((alternative) => !alternative.startsWith('config:'))) imageRows.push(row);
    else otherRows.push(row);
  }
  return {
    line: imageRows.length > 0 ? 'images_missing' : 'not_ready',
    imageCount: imageRows.length,
    imageRows,
    otherRows,
    ...raw,
  };
}

/** S-014: no absent key, prototype property or object coercion matches. */
export function contextsEqual(
  actual: unknown,
  expected: unknown,
  keys: readonly string[],
): boolean {
  return keys.every((key) => {
    const value = own(actual, key);
    return (
      hasOwn(actual, key) &&
      hasOwn(expected, key) &&
      (value === null || typeof value === 'string') &&
      value === own(expected, key)
    );
  });
}

export interface LaunchGate {
  readonly enabled: boolean;
  readonly reason:
    | 'ready'
    | 'guard_unconfirmed'
    | 'readiness_unconfirmed'
    | 'context_changed'
    | 'pick_single_run'
    | 'preview_unavailable'
    | 'gem_routines_off'
    | 'ownership_unconfirmed'
    | 'automation_listed';
}

/** F-040: the setup permit cannot be replaced by a collection permit. */
export function startGate(
  permit: SetupBattleGuardPermit | null,
  read: ContextRead,
  pageContext: unknown,
  selectedRun: string,
): LaunchGate {
  if (permit === null || !Number.isFinite(setupPermitObservedAt(permit)))
    return { enabled: false, reason: 'guard_unconfirmed' };
  if (
    read?.kind !== 'answered' ||
    own(read.body, 'ready') !== true ||
    own(read.body, 'preset') !== selectedRun
  )
    return { enabled: false, reason: 'readiness_unconfirmed' };
  if (
    !read.sameContext ||
    !contextsEqual(own(read.body, 'context'), pageContext, [
      'instance',
      'account',
      'serial',
      'rendering',
      'profile',
    ])
  )
    return { enabled: false, reason: 'context_changed' };
  return { enabled: true, reason: 'ready' };
}

/** F-042/S-104: a saved preview must still name this run and this context. */
export function collectGate(
  permit: CollectionGuardPermit | null,
  preview: ContextRead,
  pageContext: unknown,
  selectedRun: string,
  combo: boolean,
  ownership: Fact<GateRead>,
): LaunchGate {
  if (combo) return { enabled: false, reason: 'pick_single_run' };
  if (permit === null || !Number.isFinite(collectionPermitObservedAt(permit)))
    return { enabled: false, reason: 'guard_unconfirmed' };
  // backend/dashboard.py api_run_controls strips bp_ before run_workspace.read.
  const blueprint = selectedRun.startsWith('bp_') ? selectedRun.slice(3) : selectedRun;
  if (
    preview?.kind !== 'answered' ||
    !preview.sameContext ||
    own(preview.body, 'blueprint') !== blueprint
  )
    return { enabled: false, reason: 'preview_unavailable' };
  // api_run_controls reports instance/account/profile; the request fence covers serial/rendering.
  if (!contextsEqual(own(preview.body, 'context'), pageContext, ['instance', 'account', 'profile']))
    return { enabled: false, reason: 'context_changed' };
  const routines = ownPath(preview.body, 'settings', 'routines');
  if (
    ownPath(routines, 'ad_gems', 'enabled') !== true &&
    ownPath(routines, 'floating_gems', 'enabled') !== true
  )
    return { enabled: false, reason: 'gem_routines_off' };
  if (ownership.kind !== 'known') return { enabled: false, reason: 'ownership_unconfirmed' };
  if (
    ownership.value.ownership.processes.length > 0 ||
    ownership.value.ownership.operation !== null ||
    ownership.value.ownership.owner !== null
  )
    return { enabled: false, reason: 'automation_listed' };
  return { enabled: true, reason: 'ready' };
}

/** compile_preset deliberately omits the default `defined`; false denotes a placeholder. */
export function runDefined(run: unknown): boolean {
  return record(run) !== undefined && (!hasOwn(run, 'defined') || own(run, 'defined') === true);
}

/** F-037: stable selections survive polls; defaults prefer ready bp_coin_default. */
export function selectRun(body: unknown, selected: string | null): string | null {
  const runs = entries(own(body, 'runs')).filter(([, run]) => runDefined(run));
  if (selected !== null && runs.some(([name]) => name === selected)) return selected;
  const ready = runs.filter(([name]) => ownPath(body, 'readiness', name, 'ready') === true);
  return (
    ready.find(([name]) => name === 'bp_coin_default')?.[0] ?? ready[0]?.[0] ?? runs[0]?.[0] ?? null
  );
}

/** F-039: other unchecked/combo entries never inflate the Needs-you count. */
export function otherRunsMissingImages(body: unknown, selected: string | null): number {
  return entries(own(body, 'readiness')).reduce(
    (count, [name, entry]) =>
      name === selected || name === 'combo' || !runDefined(ownPath(body, 'runs', name))
        ? count
        : count + readiness(entry).imageCount,
    0,
  );
}

/** F-041: listed processes are evidence; a retained collector record is qualified. */
export function transferHint(body: unknown): 'collector_stops_first' | 'collector_recorded' | null {
  const isCollector = (row: unknown): boolean =>
    own(row, 'runner') === 'gem_collector' ||
    own(row, 'runner') === 'gems' ||
    ownPath(row, 'operation', 'kind') === 'gems';
  if ((list(own(body, 'processes')) ?? []).some(isCollector)) return 'collector_stops_first';
  const operation = ownPath(body, 'harvest', 'operation');
  const owner = ownPath(body, 'harvest', 'owner');
  return (id(own(operation, 'id')) !== undefined && own(operation, 'kind') === 'gems') ||
    (id(own(owner, 'operation_id')) !== undefined && own(owner, 'operation_kind') === 'gems')
    ? 'collector_recorded'
    : null;
}

/** One requirement row of a readiness answer (R4): what to capture or set, where, and how. */
export interface ReadinessCard {
  /** The run cannot start without it. */
  readonly blocking: boolean;
  /** Every alternative is a configuration value, not an image. */
  readonly setting: boolean;
  /** Any one of these satisfies the row (image paths, or `config: <key>`). */
  readonly alternatives: readonly string[];
  /** Why the run needs it, as the server words it. */
  readonly reasons: readonly string[];
  readonly docs: readonly {
    readonly label: string | null;
    readonly what: string | null;
    readonly where: string | null;
    readonly how: string | null;
    readonly step: string | null;
  }[];
}

function cardOf(row: unknown): ReadinessCard | null {
  const alternatives = stringList(own(row, 'alternatives'));
  if (record(row) === undefined || alternatives === undefined) return null;
  return {
    blocking: own(row, 'blocking') === true,
    setting: alternatives.length > 0 && alternatives.every((entry) => entry.startsWith('config:')),
    alternatives,
    reasons: stringList(own(row, 'reasons')) ?? [],
    docs: (list(own(row, 'docs')) ?? []).map((doc) => ({
      label: text(own(doc, 'label')) ?? null,
      what: text(own(doc, 'what')) ?? null,
      where: text(own(doc, 'where')) ?? null,
      how: text(own(doc, 'how')) ?? null,
      step: text(ownPath(doc, 'step', 'title')) ?? null,
    })),
  };
}

/**
 * `GET /api/readiness?preset=` read for one run (R4): its line (core `readiness`, the
 * same classification Start next uses), the missing and advisory rows as the server
 * listed them, its message and next action, and the context it was checked for.
 */
export function readinessCheck(body: unknown): {
  readonly view: ReadinessView;
  readonly missing: readonly ReadinessCard[];
  readonly advisory: readonly ReadinessCard[];
  readonly message: string | null;
  readonly actionLabel: string | null;
  readonly error: string | null;
  readonly context: unknown;
} {
  const cards = (rows: unknown) =>
    (list(rows) ?? []).flatMap((row) => {
      const card = cardOf(row);
      return card === null ? [] : [card];
    });
  return {
    view: readiness(body, 'check'),
    missing: cards(own(body, 'missing')),
    advisory: cards(own(body, 'advisory')),
    message: text(own(body, 'message')) ?? null,
    actionLabel: text(own(body, 'action_label')) ?? null,
    error: text(own(body, 'error')) ?? null,
    context: own(body, 'context'),
  };
}
