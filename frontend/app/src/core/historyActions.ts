import { ACTION_EFFECTS, ACTION_RESULTS, effectWord, type ActionEffectWord } from './actions.ts';
import { finite, id, list, own, ownPath, text } from './read.ts';

/**
 * A2-7 AC4: History's reading of recorded action executions. The `executions` block of a battle
 * detail and a page of the generic route (`GET /api/actions/executions`) become plain rows. Rules
 * are documented in history.rules.md. A history is a read, not a status: nothing here says an
 * action is running now, and a missing list never reads as "nothing ran".
 */
const STATES = ['running', ...ACTION_RESULTS] as const;
export type ExecutionState = (typeof STATES)[number];
export type ExecutionEffect = ActionEffectWord;
export type ExecutionTrigger = 'automatic' | 'manual';
export type ExecutionCoverage = 'full' | 'since_adoption' | 'unknown';

/** A server word: its kind when this build knows it, `other` when it does not, `unreported` when absent. */
export interface Coded<K extends string> {
  readonly kind: K | 'other' | 'unreported';
  /** The word as sent, or null when none was sent. */
  readonly code: string | null;
}

export interface ExecutionAggregate {
  readonly count: number | null;
  readonly firstAt: number | null;
  readonly lastAt: number | null;
}

export interface ExecutionRow {
  readonly key: string;
  readonly action: string | null;
  readonly instance: string | null;
  /** The action's label, or null: the page then shows the action code. */
  readonly label: string | null;
  readonly trigger: Coded<ExecutionTrigger>;
  readonly state: Coded<ExecutionState>;
  /** The outcome's result, or null when it repeats the state (shown once). */
  readonly result: Coded<ExecutionState> | null;
  /** The row was read while the action had not ended: no effect, finish time or duration yet. */
  readonly open: boolean;
  /** Null while the row is open: an effect is not known before the action ends. */
  readonly effect: Coded<ExecutionEffect> | null;
  readonly reason: string | null;
  readonly startedAt: number | null;
  readonly finishedAt: number | null;
  /** Monotonic duration only, never for an aggregate or an open row. */
  readonly durationMs: number | null;
  /** Whether the row has a duration line at all: not an open row, not an aggregate entry. */
  readonly showsDuration: boolean;
  readonly coverage: Coded<ExecutionCoverage>;
  readonly aggregate: ExecutionAggregate | null;
}

export type ExecutionsView =
  | { readonly kind: 'not_reported' }
  | { readonly kind: 'before_history' }
  | { readonly kind: 'newer_schema' }
  | { readonly kind: 'unreadable'; readonly reason: string | null }
  | { readonly kind: 'empty' }
  | {
      readonly kind: 'rows';
      readonly rows: readonly ExecutionRow[];
      /** Older executions exist than the rows shown. */
      readonly truncated: boolean;
      /** The `next_before` cursor of the next older page, or null when none is named. */
      readonly cursor: string | null;
      /** Damaged rows the server skipped on the older pages read so far. */
      readonly unusable: number;
    };

/** The older pages read through the generic route, joined in order. */
export interface OlderExecutions {
  readonly loaded: boolean;
  readonly rows: readonly unknown[];
  readonly cursor: string | null;
  readonly truncated: boolean;
  readonly unusable: number;
}

export const NO_OLDER: OlderExecutions = {
  loaded: false,
  rows: [],
  cursor: null,
  truncated: false,
  unusable: 0,
};

function coded<K extends string>(value: unknown, known: readonly K[]): Coded<K> {
  const word = text(value);
  if (word === undefined) return { kind: 'unreported', code: null };
  const found = known.find((candidate) => candidate === word);
  return { kind: found ?? 'other', code: word };
}

const TRIGGERS = ['automatic', 'manual'] as const;
const COVERAGES = ['full', 'since_adoption', 'unknown'] as const;

function aggregate(raw: unknown): ExecutionAggregate | null {
  if (raw === null || raw === undefined) return null;
  return {
    count: finite(own(raw, 'count')) ?? null,
    firstAt: finite(own(raw, 'first_at')) ?? null,
    lastAt: finite(own(raw, 'last_at')) ?? null,
  };
}

/** One execution row of the `executions` block or of the generic route. */
export function executionRow(raw: unknown, index: number): ExecutionRow {
  const state = coded(own(raw, 'state'), STATES);
  // Preserve History's accepted result words, including running on an unusual recorded row.
  const result = coded(ownPath(raw, 'outcome', 'result'), STATES);
  const open = state.kind === 'running';
  const effect = open ? null : coded(ownPath(raw, 'outcome', 'effect'), ACTION_EFFECTS);
  const reason = id(ownPath(raw, 'outcome', 'reason')) ?? id(own(raw, 'settled_reason')) ?? null;
  const merged = aggregate(own(raw, 'aggregate'));
  const duration = finite(own(raw, 'duration_ms'));
  const showsDuration = !open && merged === null;
  return {
    key: id(own(raw, 'id')) ?? `row-${String(index)}`,
    action: id(own(raw, 'action')) ?? null,
    instance: id(own(raw, 'instance')) ?? null,
    label: id(own(raw, 'label')) ?? null,
    trigger: coded(own(raw, 'trigger'), TRIGGERS),
    state,
    result: result.kind === 'unreported' || result.code === state.code ? null : result,
    open,
    effect: effect === null ? null : { kind: effectWord(effect.kind, reason), code: effect.code },
    reason,
    startedAt: finite(own(raw, 'started_at')) ?? null,
    finishedAt: finite(own(raw, 'finished_at')) ?? null,
    durationMs:
      showsDuration &&
      own(raw, 'duration_basis') === 'monotonic' &&
      duration !== undefined &&
      duration >= 0
        ? duration
        : null,
    showsDuration,
    coverage: coded(own(raw, 'coverage'), COVERAGES),
    aggregate: merged,
  };
}

function cursorOf(value: unknown): string | null {
  return id(value) ?? null;
}

/**
 * The executions of one battle: the block of the battle detail, then the older pages read so far.
 * `block` is `body.executions` of the detail answer.
 */
export function executionsView(block: unknown, older: OlderExecutions): ExecutionsView {
  if (own(block, 'items') === undefined && own(block, 'reason') === undefined)
    return { kind: 'not_reported' };
  const items = list(own(block, 'items'));
  if (items === undefined) {
    const reason = own(block, 'reason');
    if (reason === 'recorded_before_action_history') return { kind: 'before_history' };
    if (reason === 'newer_schema') return { kind: 'newer_schema' };
    return { kind: 'unreadable', reason: id(reason) ?? null };
  }
  const seen = new Set<string>();
  const rows = [...items, ...(older.loaded ? older.rows : [])].map(executionRow).filter((row) => {
    if (seen.has(row.key)) return false;
    seen.add(row.key);
    return true;
  });
  if (rows.length === 0) return { kind: 'empty' };
  return {
    kind: 'rows',
    rows,
    truncated: older.loaded ? older.truncated : own(block, 'truncated') === true,
    cursor: older.loaded ? older.cursor : cursorOf(own(block, 'next_before')),
    unusable: older.unusable,
  };
}

/** A page of the generic route, or why it cannot be used. */
export type OlderPageResult =
  | { readonly kind: 'page'; readonly page: Omit<OlderExecutions, 'loaded'> }
  | { readonly kind: 'refused'; readonly why: 'other_battle' | 'no_list' }
  | { readonly kind: 'refused'; readonly why: 'reason'; readonly reason: string | null };

/**
 * One page of `GET /api/actions/executions?battle_id=<id>&before=<cursor>`. A page is refused when
 * it has no list, names another battle, or carries a `reason` (a ledger a newer build wrote answers
 * an empty list with its reason, which is not a complete history).
 */
export function olderPage(body: unknown, battleId: string): OlderPageResult {
  const rows = list(own(body, 'executions'));
  if (rows === undefined) return { kind: 'refused', why: 'no_list' };
  if (rows.some((row) => ownPath(row, 'host', 'battle_id') !== battleId))
    return { kind: 'refused', why: 'other_battle' };
  const reason = own(body, 'reason');
  if (reason !== undefined && reason !== null)
    return { kind: 'refused', why: 'reason', reason: id(reason) ?? null };
  return {
    kind: 'page',
    page: {
      rows,
      cursor: cursorOf(own(body, 'next_before')),
      truncated: own(body, 'truncated') === true,
      unusable: finite(own(body, 'unusable')) ?? 0,
    },
  };
}

/** The older pages joined: the rows of both, the newest page's cursor, the damaged-row counts added. */
export function joinOlder(
  before: OlderExecutions,
  page: Omit<OlderExecutions, 'loaded'>,
): OlderExecutions {
  return {
    loaded: true,
    rows: [...before.rows, ...page.rows],
    cursor: page.cursor,
    truncated: page.truncated,
    unusable: before.unusable + page.unusable,
  };
}

/** The cursor the next older read sends: the newest page's, else the block's, else none. */
export function nextCursor(block: unknown, older: OlderExecutions): string | null {
  return older.loaded ? older.cursor : cursorOf(own(block, 'next_before'));
}
