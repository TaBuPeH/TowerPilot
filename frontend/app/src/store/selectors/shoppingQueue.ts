import {
  CADENCES,
  QUEUE_MODES,
  reviewQueue,
  type CatalogItem,
  type EntryField,
  type FieldReview,
  type QueueBase,
  type QueueDraft,
  type QueueReview,
} from '../../core/shoppingPlan.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import { contextIntents } from '../intents.ts';
import type { DashboardState } from '../types.ts';
import type { RunOutcomeVM } from './runs.ts';

/** SQ5: show only this queue action's refused answer beside its Save control. */
export function queueRefusal(
  state: DashboardState,
  scope: string | null,
  outcome: RunOutcomeVM | null,
): ServerText<'message'> | null {
  if (
    outcome === null ||
    !['not_saved', 'not_saved_changed', 'live_not_recorded'].includes(outcome.code)
  )
    return null;
  const record = contextIntents(state).find((item) => item.id === outcome.line.id);
  return record?.meta.draftScope === scope ? outcome.line.error : null;
}

/**
 * F6-12: the view of one shopping queue draft (saved run, live battle or shared policy) for the
 * shared editor features/shopping/QueueEditor.tsx. Upgrade names, sections and exclusion reasons
 * are the server's words; every rule is core/shoppingPlan.ts's.
 */
export type KnownMode = (typeof QUEUE_MODES)[number];
export type KnownCadence = (typeof CADENCES)[number];
export type KnownSection = 'attack' | 'defense' | 'utility';

export interface ChoiceVM<K extends string> {
  readonly value: string;
  /** The catalogue word for a known value; null shows `server` instead. */
  readonly known: K | null;
  readonly server: ServerText<'name'> | null;
}

export interface EntryVM {
  readonly id: string;
  readonly label: ServerText<'name'> | null;
  readonly position: number;
  readonly enabled: boolean;
  readonly added: boolean;
  readonly fields: Readonly<Record<EntryField, FieldReview>>;
  readonly first: boolean;
  readonly last: boolean;
}

export interface PickerItemVM {
  readonly id: string;
  readonly label: ServerText<'name'> | null;
  readonly included: boolean;
  readonly excluded: boolean;
  readonly exclusionReason: ServerText<'message'> | null;
}

export interface PickerSectionVM {
  readonly key: string;
  readonly known: KnownSection | null;
  readonly server: ServerText<'name'> | null;
  readonly items: readonly PickerItemVM[];
}

export interface QueueEditorVM {
  readonly review: QueueReview;
  readonly mode: { readonly value: string; readonly choices: readonly ChoiceVM<KnownMode>[] };
  readonly cadence: { readonly value: string; readonly choices: readonly ChoiceVM<KnownCadence>[] };
  readonly entries: readonly EntryVM[];
  readonly picker: readonly PickerSectionVM[];
  /** True while any catalogue item's order is provisional (not verified on the account). */
  readonly orderProvisional: boolean;
}

function choices<K extends string>(known: readonly K[], current: string): ChoiceVM<K>[] {
  const listed: ChoiceVM<K>[] = known.map((value) => ({ value, known: value, server: null }));
  return current === '' || known.some((value) => value === current)
    ? listed
    : [...listed, { value: current, known: null, server: fromServer('name', current) }];
}

const SECTIONS: readonly KnownSection[] = ['attack', 'defense', 'utility'];
const knownSection = (section: string): KnownSection | null =>
  SECTIONS.find((known) => known === section) ?? null;

function pickerOf(catalog: readonly CatalogItem[], review: QueueReview): PickerSectionVM[] {
  const sections = new Map<string, CatalogItem[]>();
  for (const item of catalog) {
    const key = item.section ?? '';
    sections.set(key, [...(sections.get(key) ?? []), item]);
  }
  return [...sections].map(([key, items]) => ({
    key,
    known: knownSection(key),
    server: key === '' ? null : fromServer('name', key),
    items: items.map((item) => ({
      id: item.id,
      label: fromServer('name', item.label ?? item.id),
      included: review.entries.some((entry) => entry.id === item.id && entry.enabled),
      excluded: review.excluded.includes(item.id),
      exclusionReason: fromServer('message', item.exclusionReason),
    })),
  }));
}

export function selectQueueEditor(base: QueueBase, draft: QueueDraft): QueueEditorVM {
  const review = reviewQueue(base, draft);
  const labels = new Map(base.catalog.map((item) => [item.id, item.label ?? item.id]));
  return {
    review,
    mode: { value: review.mode, choices: choices(QUEUE_MODES, review.mode) },
    cadence: { value: review.cadence, choices: choices(CADENCES, review.cadence) },
    entries: review.entries.map((entry, index) => ({
      id: entry.id,
      label: fromServer('name', labels.get(entry.id) ?? entry.id),
      position: entry.position,
      enabled: entry.enabled,
      added: entry.added,
      fields: entry.fields,
      first: index === 0,
      last: index === review.entries.length - 1,
    })),
    picker: pickerOf(base.catalog, review),
    orderProvisional: base.catalog.some((item) => !item.orderVerified),
  };
}
