import { sameJson } from './json.ts';
import { entries, finite, id, list, own, record, text } from './read.ts';
import { typedDecimal } from './typedNumber.ts';

/**
 * F6-12 shopping queues (brief backend/logs/reviews/ui-pages/f6-12-shopping-brief.md): the
 * version 2 shopping plan as the server sends it, a person's raw draft over it, and the full
 * plan that draft would send. One owner for the saved queue (POST /api/run-controls
 * `changes.shopping_plan`), a battle's live queue (POST .../live-controls) and a shared
 * library policy (POST /api/profile-patch).
 *
 * The backend owns every rule (backend/towerpilot_workflows/shopping_plan.py normalize_plan):
 * ranges, integer-ness, unique ids, defaults. This module never copies them: it only refuses
 * text that is not a number at all (it cannot be sent as one) and an unset cash reserve (the
 * brief requires an explicit reserve, 0 included); everything else is sent and the server's
 * refusal is shown. A blank field means: tap limit none (unlimited), last wave none (no end),
 * weight and first wave the server's default (the field is left out of the plan).
 *
 * The draft is a flat map of raw values (the store's draft shape, persisted and restored by the
 * existing draft boundary):
 * - `base`: the plan the draft started from when it is not the read's (a new queue, a reviewed
 *   legacy conversion), as JSON text;
 * - `reserve`, `mode`, `cadence`: the plan's cash reserve, queue mode and cadence;
 * - `order`: the queue's upgrade ids in priority order, as JSON text;
 * - `entry:<id>:enabled` (boolean) and `entry:<id>:<field>` (text) for tap_limit, weight,
 *   from_wave and until_wave;
 * - `excluded:<id>` (boolean): the upgrade is on, or off, the plan's exclusion list.
 */

export type QueueDraft = Readonly<Record<string, string | boolean>>;

/**
 * The queue modes and cadences the backend accepts (shopping_plan.py normalize_plan): the
 * choices an editor offers. A plan holding another value keeps it, shown as the server sent it,
 * and the server judges it.
 */
export const QUEUE_MODES = ['highest_affordable', 'round_robin', 'weighted_split'] as const;
export const CADENCES = ['single_attempt', 'while_affordable'] as const;

/** The version of the plan this editor reads and writes (shopping_plan.py, version 2). */
const VERSION = 2;

/** One upgrade of the shop catalogue a read publishes (shopping_plan.catalog()). */
export interface CatalogItem {
  readonly id: string;
  readonly label: string | null;
  /** The shop page: attack, defense or utility as the server names it. */
  readonly section: string | null;
  /** False while the game order is provisional (needs the account's UI to verify it). */
  readonly orderVerified: boolean;
  readonly excludedByDefault: boolean;
  readonly exclusionReason: string | null;
}

export function shopCatalog(value: unknown): readonly CatalogItem[] {
  return (list(value) ?? []).flatMap((item) => {
    const itemId = id(own(item, 'id'));
    if (itemId === undefined) return [];
    return [
      {
        id: itemId,
        label: text(own(item, 'label')) ?? null,
        section: text(own(item, 'section')) ?? null,
        orderVerified: own(item, 'order_verified') === true,
        excludedByDefault: own(item, 'excluded_by_default') === true,
        exclusionReason: text(own(item, 'exclusion_reason')) ?? null,
      },
    ];
  });
}

/** A version 2 plan: its members as sent, its queue entries in priority order. */
export interface QueuePlan {
  readonly raw: Readonly<Record<string, unknown>>;
  readonly queue: readonly Readonly<Record<string, unknown>>[];
}

function members(value: unknown): Readonly<Record<string, unknown>> | undefined {
  return record(value) === undefined || list(value) !== undefined
    ? undefined
    : Object.fromEntries(entries(value));
}

/** The plan a value holds, or null when it is not a version 2 plan with a queue. */
export function queuePlan(value: unknown): QueuePlan | null {
  const raw = members(value);
  const queue = list(own(value, 'queue'));
  if (raw === undefined || own(value, 'version') !== VERSION || queue === undefined) return null;
  const items = queue.flatMap((item) => {
    const entry = members(item);
    return entry !== undefined && id(own(item, 'id')) !== undefined ? [entry] : [];
  });
  return items.length === queue.length ? { raw, queue: items } : null;
}

/**
 * A saved run's legacy conversion (GET /api/run-controls `legacy_conversion`, never written by
 * the read): the plan it proposes and every warning, which a person must review.
 */
export interface LegacyConversion {
  readonly plan: QueuePlan;
  readonly warnings: readonly string[];
  readonly requiresReview: boolean;
}

export function legacyConversion(value: unknown): LegacyConversion | null {
  const plan = queuePlan(own(value, 'plan'));
  if (plan === null) return null;
  return {
    plan,
    warnings: (list(own(value, 'warnings')) ?? []).flatMap((line) => {
      const shown = text(line);
      return shown === undefined ? [] : [shown];
    }),
    requiresReview: own(value, 'requires_review') !== false,
  };
}

const ENTRY_FIELDS = ['tap_limit', 'weight', 'from_wave', 'until_wave'] as const;
export type EntryField = (typeof ENTRY_FIELDS)[number];
/** Blank means null for these; for weight and from_wave it leaves the field to the server. */
const BLANK_IS_NULL: ReadonlySet<EntryField> = new Set(['tap_limit', 'until_wave']);
const EDITED: ReadonlySet<string> = new Set([...ENTRY_FIELDS, 'id', 'enabled']);

export const entryKey = (upgrade: string, field: EntryField | 'enabled') =>
  `entry:${upgrade}:${field}`;
export const excludedKey = (upgrade: string) => `excluded:${upgrade}`;

function shown(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
}

function parsedJson(value: string | boolean | undefined): unknown {
  if (typeof value !== 'string') return undefined;
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed;
  } catch {
    return undefined;
  }
}

/** What the draft edits: its own staged base if any, else the plan the read holds. */
export interface QueueBase {
  /** The plan the read holds (null: the run or battle has none). */
  readonly plan: QueuePlan | null;
  readonly catalog: readonly CatalogItem[];
}

function baseOf(base: QueueBase, draft: QueueDraft): QueuePlan | null {
  return queuePlan(parsedJson(draft['base'])) ?? base.plan;
}

function orderOf(plan: QueuePlan | null, draft: QueueDraft): readonly string[] {
  const staged = list(parsedJson(draft['order']));
  const ids = staged?.flatMap((item) => (typeof item === 'string' ? [item] : []));
  if (ids !== undefined && ids.length === staged?.length && new Set(ids).size === ids.length)
    return ids;
  return (plan?.queue ?? []).flatMap((entry) => {
    const entryId = id(own(entry, 'id'));
    return entryId === undefined ? [] : [entryId];
  });
}

function excludedOf(plan: QueuePlan | null, draft: QueueDraft, catalog: readonly CatalogItem[]) {
  const listed = (list(own(plan?.raw, 'excluded_upgrades')) ?? []).flatMap((item) =>
    typeof item === 'string' ? [item] : [],
  );
  const known = new Set([...listed, ...catalog.map((item) => item.id)]);
  return [...known].filter((upgrade) => {
    const staged = draft[excludedKey(upgrade)];
    return typeof staged === 'boolean' ? staged : listed.includes(upgrade);
  });
}

export type FieldProblem = 'empty' | 'not_a_number' | 'not_a_choice';

export interface FieldReview {
  readonly raw: string;
  readonly problem: FieldProblem | null;
}

export interface EntryReview {
  readonly id: string;
  /** 1-based priority. */
  readonly position: number;
  readonly enabled: boolean;
  /** Not in the plan the draft started from (added from the catalogue). */
  readonly added: boolean;
  readonly fields: Readonly<Record<EntryField, FieldReview>>;
}

export interface QueueReview {
  readonly reserve: FieldReview;
  /** The queue mode and cadence to send ('' when neither draft nor plan names one). */
  readonly mode: string;
  readonly modeProblem: FieldProblem | null;
  readonly cadence: string;
  readonly cadenceProblem: FieldProblem | null;
  readonly entries: readonly EntryReview[];
  readonly excluded: readonly string[];
  /** The full plan to send, or null while a field is not a number or the reserve is unset. */
  readonly plan: Readonly<Record<string, unknown>> | null;
  readonly problems: number;
  /** The draft would send something the plan it started from does not hold. */
  readonly changed: boolean;
}

function numberField(raw: string, blank: 'null' | 'omit' | 'required') {
  const typed = typedDecimal(raw);
  if ('value' in typed) return { review: { raw, problem: null }, value: typed.value };
  if (typed.reason === 'empty' && blank !== 'required')
    return { review: { raw, problem: null }, value: blank === 'null' ? null : undefined };
  return { review: { raw, problem: typed.reason }, value: undefined };
}

/**
 * The queue a draft describes over its base: every field as typed, its problem, and the full
 * plan to send (base members kept, edited ones replaced, queue in the draft's order).
 */
export function reviewQueue(base: QueueBase, draft: QueueDraft): QueueReview {
  const plan = baseOf(base, draft);
  const byId = new Map(
    (plan?.queue ?? []).map((entry) => [id(own(entry, 'id')) ?? '', entry] as const),
  );
  let problems = 0;
  const queue: Record<string, unknown>[] = [];
  const reviewed = orderOf(plan, draft).map((upgrade, index): EntryReview => {
    const from = byId.get(upgrade);
    const staged = draft[entryKey(upgrade, 'enabled')];
    const enabled = typeof staged === 'boolean' ? staged : own(from, 'enabled') !== false;
    // Members this editor does not edit are kept as the plan holds them.
    const entry: Record<string, unknown> = {
      ...Object.fromEntries(entries(from).filter(([key]) => !EDITED.has(key))),
      id: upgrade,
      enabled,
    };
    const field = (name: EntryField): FieldReview => {
      const typed = draft[entryKey(upgrade, name)];
      const raw = typeof typed === 'string' ? typed : shown(own(from, name));
      const parsed = numberField(raw, BLANK_IS_NULL.has(name) ? 'null' : 'omit');
      if (parsed.review.problem !== null) problems += 1;
      if (parsed.value !== undefined) entry[name] = parsed.value;
      return parsed.review;
    };
    const fields = {
      tap_limit: field('tap_limit'),
      weight: field('weight'),
      from_wave: field('from_wave'),
      until_wave: field('until_wave'),
    };
    queue.push(entry);
    return { id: upgrade, position: index + 1, enabled, added: from === undefined, fields };
  });
  const reserveRaw =
    typeof draft['reserve'] === 'string' ? draft['reserve'] : shown(own(plan?.raw, 'cash_reserve'));
  const reserve = numberField(reserveRaw, 'required');
  if (reserve.review.problem !== null) problems += 1;
  const mode =
    typeof draft['mode'] === 'string' ? draft['mode'] : (text(own(plan?.raw, 'queue_mode')) ?? '');
  const cadence =
    typeof draft['cadence'] === 'string'
      ? draft['cadence']
      : (text(own(plan?.raw, 'cadence')) ?? '');
  // A new queue names no mode or cadence: the person chooses each (brief SQ1, reviewed defaults).
  // A value the editor does not know (a restored or server value) stays shown and is not sent
  // until the person chooses a known one (F6-12-C-03).
  const choiceProblem = (value: string, known: readonly string[]): FieldProblem | null =>
    value === '' ? 'empty' : known.includes(value) ? null : 'not_a_choice';
  const modeProblem = choiceProblem(mode, QUEUE_MODES);
  const cadenceProblem = choiceProblem(cadence, CADENCES);
  if (modeProblem !== null) problems += 1;
  if (cadenceProblem !== null) problems += 1;
  const excluded = excludedOf(plan, draft, base.catalog);
  const built =
    problems > 0
      ? null
      : {
          ...(plan?.raw ?? { version: VERSION }),
          cash_reserve: reserve.value,
          queue_mode: mode,
          cadence,
          excluded_upgrades: excluded,
          queue,
        };
  return {
    reserve: reserve.review,
    mode,
    modeProblem,
    cadence,
    cadenceProblem,
    entries: reviewed,
    excluded,
    plan: built,
    problems,
    changed:
      Object.keys(draft).length > 0 &&
      (built === null || base.plan === null || !planHolds(base.plan.raw, built)),
  };
}

/**
 * A read holds a sent plan when every member the plan sent has the sent value there (queue
 * entries in the same order, each sent field equal); members the server filled in with its
 * defaults do not matter. This is positive evidence of the current state, not of who wrote it.
 */
export function planHolds(read: unknown, sent: unknown): boolean {
  const readPlan = queuePlan(read);
  const sentPlan = queuePlan(sent);
  if (readPlan === null || sentPlan === null) return false;
  if (readPlan.queue.length !== sentPlan.queue.length) return false;
  const queueHeld = sentPlan.queue.every((entry, index) =>
    entries(entry).every(([key, value]) => sameJson(own(readPlan.queue[index], key), value)),
  );
  return (
    queueHeld &&
    entries(sentPlan.raw).every(
      ([key, value]) => key === 'queue' || sameJson(own(readPlan.raw, key), value),
    )
  );
}

function withOrder(draft: QueueDraft, order: readonly string[]): QueueDraft {
  return { ...draft, order: JSON.stringify(order) };
}

/**
 * Include an upgrade (catalogue "Included in queue"): one already queued is switched on where it
 * stands, keeping its limits and place; a new one is appended. Including an excluded upgrade
 * removes its exclusion explicitly (brief SQ4).
 */
export function includeUpgrade(base: QueueBase, draft: QueueDraft, upgrade: string): QueueDraft {
  const plan = baseOf(base, draft);
  const order = orderOf(plan, draft);
  const next = order.includes(upgrade) ? draft : withOrder(draft, [...order, upgrade]);
  const included = { ...next, [entryKey(upgrade, 'enabled')]: true };
  return excludedOf(plan, draft, base.catalog).includes(upgrade)
    ? { ...included, [excludedKey(upgrade)]: false }
    : included;
}

/** Leave an upgrade out: it stays in the queue, switched off, with its limits and place. */
export function leaveOutUpgrade(draft: QueueDraft, upgrade: string): QueueDraft {
  return { ...draft, [entryKey(upgrade, 'enabled')]: false };
}

/** Move an upgrade one place up (-1) or down (1); null at either end. */
export function moveUpgrade(
  base: QueueBase,
  draft: QueueDraft,
  upgrade: string,
  step: -1 | 1,
): QueueDraft | null {
  const order = [...orderOf(baseOf(base, draft), draft)];
  const from = order.indexOf(upgrade);
  const to = from + step;
  if (from < 0 || to < 0 || to >= order.length) return null;
  order.splice(from, 1);
  order.splice(to, 0, upgrade);
  return withOrder(draft, order);
}

/**
 * The draft values `next` sets that `draft` does not hold (keys never disappear from a queue
 * draft), for an owner that stores a draft one value at a time.
 */
export function draftChanges(
  draft: QueueDraft,
  next: QueueDraft,
): readonly (readonly [string, string | boolean])[] {
  return Object.entries(next).filter(([key, value]) => draft[key] !== value);
}

/** Put an upgrade on, or take it off, the plan's exclusion list. */
export function setExcluded(draft: QueueDraft, upgrade: string, excluded: boolean): QueueDraft {
  return { ...draft, [excludedKey(upgrade)]: excluded };
}

/**
 * Stage a plan as the draft's own base: a reviewed legacy conversion (the person chose "Use
 * reviewed queue"; Save still sends it) or a new queue for a run without one (brief SQ1). Its
 * cash reserve is never taken over when unset: the person types one, 0 included.
 */
export function stagePlan(plan: Readonly<Record<string, unknown>>): QueueDraft {
  const reserve = finite(own(plan, 'cash_reserve'));
  return { base: JSON.stringify(plan), reserve: reserve === undefined ? '' : String(reserve) };
}

/**
 * A new queue for a saved run without one: switched on (the run's shopping switch still decides
 * whether it runs), no upgrades yet, the catalogue's default exclusions listed (each with its
 * reason), and the run's shopping interval (as the backend's own legacy conversion takes it;
 * the routine's interval control stays its editor). Mode, cadence and reserve are the person's
 * explicit choices.
 */
export function newQueue(
  catalog: readonly CatalogItem[],
  intervalSec: number | null,
): Readonly<Record<string, unknown>> {
  return {
    version: VERSION,
    enabled: true,
    ...(intervalSec === null ? {} : { interval_sec: intervalSec }),
    cash_reserve: null,
    excluded_upgrades: catalog.filter((item) => item.excludedByDefault).map((item) => item.id),
    queue: [],
  };
}
