import { entries, finite, id, list, own, ownPath, stringList, text } from './read.ts';
import { atPath } from './profilePatch.ts';
import { sameJson } from './json.ts';
import { runAddLoadouts } from './runAdd.ts';

/** The pin includes the raw edit so the shared per-key draft cleanup cannot discard its
 * revision when a newer edit survives an older save's receipt. Persisted as one string. */
export function entryDraftMarker(
  revision: string | null,
  raw: string | boolean,
  remove: boolean,
): string {
  return JSON.stringify([revision, raw, remove]);
}

export interface EntryDraftSnapshot {
  readonly revision: string | null;
  readonly raw: string | boolean | undefined;
  readonly remove: boolean | undefined;
}

/** Recover the whole edit after per-key cleanup. In a list+remove edit the unchanged
 * text may have been cleared while the newer removal choice and marker survived. */
export function entryDraftSnapshot(marker: unknown): EntryDraftSnapshot | null {
  if (typeof marker !== 'string') return null;
  const legacy = { revision: id(marker) ?? null, raw: undefined, remove: undefined };
  try {
    const parsed: unknown = JSON.parse(marker);
    const parts = list(parsed);
    if (parts === undefined) return legacy;
    const [revision, raw, remove] = parts;
    if (
      parts.length !== 3 ||
      (revision !== null && id(revision) === undefined) ||
      (typeof raw !== 'string' && typeof raw !== 'boolean') ||
      typeof remove !== 'boolean'
    )
      return null;
    return { revision: id(revision) ?? null, raw, remove };
  } catch {
    // Keep a draft recorded with the original plain revision format pinned too.
    return legacy;
  }
}

/** RL3 owns entry-field selection and parsing. The vocabulary owns kind legality and bounds;
 * profilePatch owns write classification and read settlement. No routine controls live here. */
const EDITED_FIELDS = [
  'label',
  'loadout',
  'tier',
  'dissonant_tab',
  'perk_bans',
  'gem_entry_max',
  'restart_via_home',
  'cancel_sprint',
  'max_wave',
  'count',
  'rides',
  'cycles',
  'policies.gather',
  'policies.uw',
  'policies.rescue',
  'shopping',
] as const;
export type EntryKey = (typeof EDITED_FIELDS)[number];

/** profile_target_more and profile_target_more_2 own the recorded inactive operations.
 * Field vocabulary and parsing still decide which operation a run can offer. */
export function inactiveEntryRecorded(key: EntryKey, remove: boolean): boolean {
  return remove
    ? key === 'label' ||
        key === 'policies.gather' ||
        key === 'policies.rescue' ||
        key === 'dissonant_tab' ||
        key === 'rides' ||
        key === 'perk_bans' ||
        key === 'policies.uw' ||
        key === 'shopping'
    : true;
}

/** Entries come from the edited file, independently of the active /api/runs list. */
export function runEntryRuns(source: unknown) {
  return entries(ownPath(source, 'profile', 'blueprints')).map(([key, blueprint]) => ({
    key,
    label: text(own(blueprint, 'label')) ?? key,
  }));
}
export interface EntryField {
  readonly key: EntryKey;
  readonly type: 'text' | 'number' | 'toggle' | 'select' | 'list';
  readonly saved: unknown;
  readonly choices: readonly { readonly value: string; readonly label: string }[];
  readonly min: number | null;
  readonly max: number | null;
  readonly removable: boolean;
}

export function runEntryFields(
  source: unknown,
  run: string,
  config: unknown,
  vocab: unknown,
): readonly EntryField[] {
  const blueprint = ownPath(source, 'profile', 'blueprints', run);
  const kind = id(own(blueprint, 'kind'));
  const specs = ownPath(vocab, 'vocab', 'blueprint_fields', 'fields', kind ?? '', 'fields');
  return EDITED_FIELDS.flatMap((key): EntryField[] => {
    const path = key.split('.');
    const spec =
      path.length === 1 ? own(specs, key) : ownPath(specs, 'policies', 'fields', path[1] ?? '');
    if (spec === undefined || (key === 'count' && kind !== 'shard')) return [];
    if (key === 'rides' && kind !== 'uw_grant_quest') return [];
    if (key === 'cycles' && kind !== 'cycle_quest') return [];
    const policy = key.startsWith('policies.') || key === 'shopping';
    const section =
      key === 'policies.gather'
        ? 'gather'
        : key === 'policies.uw'
          ? 'uw_policies'
          : key === 'policies.rescue'
            ? 'rescue_policies'
            : 'shopping_lists';
    const choices = policy
      ? entries(ownPath(source, 'profile', 'policies', section)).map(([value, body]) => ({
          value,
          label: text(own(body, 'label')) ?? value,
        }))
      : (key === 'loadout'
          ? [
              ...runAddLoadouts(config),
              ...(kind === 'coin'
                ? (stringList(ownPath(vocab, 'vocab', 'loadout_specials', 'values')) ?? [])
                : []),
            ]
          : (stringList(own(spec, 'values')) ?? [])
        ).map((value) => ({ value, label: value }));
    const type =
      policy || key === 'loadout' || key === 'dissonant_tab'
        ? 'select'
        : own(spec, 'type') === 'bool'
          ? 'toggle'
          : own(spec, 'type') === 'int'
            ? 'number'
            : key === 'perk_bans'
              ? 'list'
              : 'text';
    const range = list(own(spec, 'range'));
    return [
      {
        key,
        type,
        saved: atPath(blueprint, path),
        choices,
        min: finite(range?.[0]) ?? null,
        max: finite(range?.[1]) ?? null,
        removable:
          policy ||
          key === 'perk_bans' ||
          key === 'label' ||
          key === 'dissonant_tab' ||
          key === 'rides',
      },
    ];
  });
}

export function entryRaw(field: EntryField): string | boolean {
  if (field.type === 'toggle') return field.saved === true;
  if (field.type === 'list') return (stringList(field.saved) ?? []).join('\n');
  return typeof field.saved === 'number' ? String(field.saved) : (text(field.saved) ?? '');
}

/** RL11: match the recorded validator's non-null, non-off ownership requirement. */
export function entryNeedsChainLightning(source: unknown, reference: unknown): boolean {
  const mode = ownPath(
    source,
    'profile',
    'policies',
    'uw_policies',
    typeof reference === 'string' ? reference : '',
    'chain_lightning',
    'mode',
  );
  return (
    mode !== undefined &&
    mode !== null &&
    mode !== 'off' &&
    ownPath(source, 'profile', 'player', 'uws', 'chain_lightning') !== true
  );
}

export type EntryReason =
  'number' | 'minimum' | 'maximum' | 'choice' | 'list' | 'text' | 'unchanged';
export type EntryCheck =
  | { readonly kind: 'valid'; readonly remove: boolean; readonly value: unknown }
  | { readonly kind: 'invalid'; readonly reason: EntryReason };

/** Raw text stays in the draft. Empty max_wave is null; an empty list clears bans;
 * removing perk_bans leaves the game's existing bans untouched. Never clamp a value. */
export function checkEntry(field: EntryField, raw: string | boolean, remove = false): EntryCheck {
  if (remove && field.removable)
    return field.saved === undefined && field.key !== 'label'
      ? { kind: 'invalid', reason: 'unchanged' }
      : { kind: 'valid', remove: true, value: undefined };
  let value: unknown = raw;
  if (field.type === 'toggle') {
    if (typeof raw !== 'boolean') return { kind: 'invalid', reason: 'choice' };
  } else {
    if (typeof raw !== 'string') return { kind: 'invalid', reason: 'text' };
    if (field.type === 'number') {
      if (field.key === 'rides' || field.key === 'cycles') {
        // RL13: preserve scalar types; integer/range validation belongs to the server.
        try {
          const parsed: unknown = JSON.parse(raw);
          value = typeof parsed === 'number' || typeof parsed === 'string' ? parsed : raw;
        } catch {
          value = raw;
        }
      } else if (raw === '' && field.key === 'max_wave') value = null;
      else {
        if (!/^\d+$/u.test(raw) || !Number.isSafeInteger(Number(raw)))
          return { kind: 'invalid', reason: 'number' };
        value = Number(raw);
        if (field.min !== null && Number(raw) < field.min)
          return { kind: 'invalid', reason: 'minimum' };
        if (field.max !== null && Number(raw) > field.max)
          return { kind: 'invalid', reason: 'maximum' };
      }
    } else if (field.type === 'list') {
      const items = raw === '' ? [] : raw.split('\n');
      if (items.some((item) => item.trim() === '')) return { kind: 'invalid', reason: 'list' };
      value = items;
    } else if (field.type === 'select') {
      if (raw === '' && field.removable)
        return field.saved === undefined
          ? { kind: 'invalid', reason: 'unchanged' }
          : { kind: 'valid', remove: true, value: undefined };
      if (!field.choices.some((choice) => choice.value === raw))
        return { kind: 'invalid', reason: 'choice' };
    } else if (raw.trim() === '') return { kind: 'invalid', reason: 'text' };
  }
  return sameJson(value, field.saved)
    ? { kind: 'invalid', reason: 'unchanged' }
    : { kind: 'valid', remove: false, value };
}
