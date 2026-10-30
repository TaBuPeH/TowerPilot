import { entries, list, own, ownPath, record, text } from '../core/read.ts';

export const EQUIPMENT_FIELDS = [
  'global_preset',
  'cards',
  'module_preset',
  'guardian_preset',
  'workshop_preset',
  'bot_preset',
] as const;
export type EquipmentField = (typeof EQUIPMENT_FIELDS)[number];
export type EquipmentMode = 'manual' | 'global' | 'category';
export interface RestoreModuleRow {
  readonly key: string;
  readonly module: string;
  readonly slot: string;
  readonly originalRaw?: unknown;
  readonly moduleEdited?: boolean;
  readonly slotEdited?: boolean;
}
export interface EquipmentDraft {
  /** null means untouched: preserve the original server value byte-for-value in JSON. */
  readonly restoreCards: string | null;
  readonly restoreModules: readonly RestoreModuleRow[] | null;
  readonly mode: EquipmentMode;
  readonly values: Readonly<Record<EquipmentField, string>>;
}
export const loadoutOf = (body: unknown, name: string): unknown => ownPath(body, 'loadouts', name);
export { configRevision } from '../core/config.ts';

export function equipmentMode(body: unknown): EquipmentMode {
  const fields = entries(body).filter(
    ([key]) => key !== 'cards_restore' && key !== 'modules_restore',
  );
  if (
    fields.some(([key]) => key !== 'defined' && !EQUIPMENT_FIELDS.some((field) => key === field)) ||
    fields.some(([key, value]) => key !== 'defined' && typeof value !== 'string') ||
    (own(body, 'global_preset') !== undefined && fields.some(([key]) => key !== 'global_preset'))
  )
    return 'manual';
  return typeof own(body, 'global_preset') === 'string' ? 'global' : 'category';
}
export function equipmentDraft(body: unknown): EquipmentDraft {
  return {
    restoreCards: null,
    restoreModules: null,
    mode: equipmentMode(body),
    values: {
      global_preset: text(own(body, 'global_preset')) ?? '',
      cards: text(own(body, 'cards')) ?? '',
      module_preset: text(own(body, 'module_preset')) ?? '',
      guardian_preset: text(own(body, 'guardian_preset')) ?? '',
      workshop_preset: text(own(body, 'workshop_preset')) ?? '',
      bot_preset: text(own(body, 'bot_preset')) ?? '',
    },
  };
}
export function restoreEquipmentDraft(value: unknown): EquipmentDraft | null {
  const mode = own(value, 'mode');
  const values = own(value, 'values');
  if (
    !['manual', 'global', 'category'].some((candidate) => candidate === mode) ||
    EQUIPMENT_FIELDS.some((field) => typeof own(values, field) !== 'string')
  )
    return null;
  const restored = equipmentDraft(values);
  return {
    ...restored,
    restoreCards: text(own(value, 'restoreCards')) ?? null,
    restoreModules:
      list(own(value, 'restoreModules'))?.flatMap((row): RestoreModuleRow[] => {
        const key = text(own(row, 'key'));
        const module = text(own(row, 'module'));
        const slot = text(own(row, 'slot'));
        return key === undefined || module === undefined || slot === undefined
          ? []
          : [
              {
                key,
                module,
                slot,
                originalRaw: own(row, 'originalRaw'),
                moduleEdited: own(row, 'moduleEdited') === true,
                slotEdited: own(row, 'slotEdited') === true,
              },
            ];
      }) ?? null,
    mode: mode === 'manual' ? 'manual' : mode === 'global' ? 'global' : 'category',
  };
}
/** Selection structure only; the backend owns entry behavior and ownership validation. */
export function equipmentBody(
  draft: EquipmentDraft,
  original: unknown,
): Readonly<Record<string, unknown>> {
  const converting = draft.mode !== equipmentMode(original);
  const saved = equipmentDraft(original);
  const sameSelection =
    !converting && EQUIPMENT_FIELDS.every((field) => draft.values[field] === saved.values[field]);
  const body: Record<string, unknown> =
    draft.mode === 'manual' || sameSelection
      ? Object.fromEntries(entries(original))
      : Object.fromEntries([
          ...entries(original).filter(
            ([key]) =>
              !EQUIPMENT_FIELDS.some((field) => field === key) &&
              (key === 'cards_restore' ||
                key === 'modules_restore' ||
                (!converting && key !== 'defined')),
          ),
          ...EQUIPMENT_FIELDS.flatMap((field): [string, unknown][] => {
            const active =
              draft.mode === 'global' ? field === 'global_preset' : field !== 'global_preset';
            const value = draft.values[field];
            return active && value !== '' ? [[field, value]] : [];
          }),
        ]);
  if (draft.restoreCards !== null) {
    if (draft.restoreCards === '') delete body['cards_restore'];
    else body['cards_restore'] = draft.restoreCards;
  }
  if (draft.restoreModules !== null) {
    if (draft.restoreModules.length === 0) delete body['modules_restore'];
    else body['modules_restore'] = draft.restoreModules.map(restoreModuleValue);
  }
  return body;
}

export function restoreModuleRows(
  draft: EquipmentDraft,
  original: unknown,
): readonly RestoreModuleRow[] {
  return (
    draft.restoreModules ??
    (list(own(original, 'modules_restore')) ?? []).map((row, index) => ({
      key: `saved:${String(index)}`,
      module: restorePartText(list(row)?.[0]),
      slot: restorePartText(list(row)?.[1]),
      originalRaw: row,
    }))
  );
}

function restorePartText(value: unknown): string {
  return typeof value === 'string' ? value : value === undefined ? '' : JSON.stringify(value);
}

/** Only explicitly edited cells replace their original values; extra/raw members survive. */
export function restoreModuleValue(row: RestoreModuleRow): unknown {
  if (row.originalRaw === undefined) return [row.module, row.slot];
  const original = list(row.originalRaw);
  if (original === undefined) return row.originalRaw;
  const value = [...original];
  if (row.moduleEdited === true) value[0] = row.module;
  if (row.slotEdited === true) value[1] = row.slot;
  return value;
}

export function restoreRowInvalid(row: RestoreModuleRow): boolean {
  const value = list(restoreModuleValue(row));
  return (
    value === undefined ||
    value.length !== 2 ||
    value.some((part) => typeof part !== 'string' || part.trim() === '')
  );
}

export function restoreInvalid(draft: EquipmentDraft): boolean {
  return draft.restoreModules?.some(restoreRowInvalid) === true;
}

export function restoreChanged(draft: EquipmentDraft, original: unknown): boolean {
  const body = equipmentBody(draft, original);
  return ['cards_restore', 'modules_restore'].some(
    (key) => JSON.stringify(own(body, key)) !== JSON.stringify(own(original, key)),
  );
}

/** Recorded restore_loadout_unused also refuses clearing. Only the active profile's
 * blueprint references establish eligibility; unavailable references defer to the server.
 * Existing unchanged restore values do not block an ordinary selection save.
 */
export function restoreEligibility(
  draft: EquipmentDraft,
  original: unknown,
  source: unknown,
  name: string,
  activeProfile: string | null,
): 'unused' | 'noProfile' | null {
  if (!restoreChanged(draft, original)) return null;
  return restoreUsage(source, name, activeProfile);
}

export function restoreUsage(
  source: unknown,
  name: string,
  activeProfile: string | null,
): 'unused' | 'noProfile' | null {
  if (activeProfile === null) return 'noProfile';
  const blueprints = ownPath(source, 'profile', 'blueprints');
  if (record(blueprints) === undefined) return null;
  return entries(blueprints).some(([, bp]) => own(bp, 'loadout') === name) ? null : 'unused';
}
/* Selection choices are read from the same profile as the ordinary cards picker. */
export function discoveredOptions(source: unknown, field: EquipmentField): readonly string[] {
  const player = ownPath(source, 'profile', 'player');
  const raw =
    field === 'global_preset'
      ? own(player, 'global_presets')
      : field === 'cards'
        ? own(player, 'card_presets')
        : ownPath(
            player,
            'category_presets',
            field === 'module_preset'
              ? 'modules'
              : field === 'guardian_preset'
                ? 'guardians'
                : field === 'workshop_preset'
                  ? 'workshop'
                  : 'bots',
          );
  return [...new Set((list(raw) ?? []).filter((item): item is string => typeof item === 'string'))];
}
export function invalidEquipment(
  draft: EquipmentDraft,
  source: unknown,
): readonly EquipmentField[] {
  return EQUIPMENT_FIELDS.filter((field) => {
    const active =
      draft.mode === 'global'
        ? field === 'global_preset'
        : draft.mode === 'category' && field !== 'global_preset';
    return (
      active &&
      draft.values[field] !== '' &&
      !discoveredOptions(source, field).includes(draft.values[field])
    );
  });
}
