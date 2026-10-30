import { entries, finite, id, list, own, text } from './read.ts';
import { typedDecimal } from './typedNumber.ts';
import { configContext, configRevision } from './config.ts';

/**
 * The typed settings of `GET /api/settings` (collection brief C1 to C4, abilities brief A1):
 * modules of fields the server describes (type, bounds, unit, label, value). This module
 * reads them and judges a person's raw draft against the server's own metadata: never
 * clamped, never rounded, an empty or reversed range stays a visible error. It is the one
 * owner of these rules for every page that edits a settings module.
 */
export type SettingsFieldType =
  'boolean' | 'integer' | 'number' | 'range' | 'point' | 'enum' | 'string' | 'string-list';

const FIELD_TYPES: readonly SettingsFieldType[] = [
  'boolean',
  'integer',
  'number',
  'range',
  'point',
  'enum',
  'string',
  'string-list',
];

export interface SettingsOption {
  readonly value: string;
  readonly label: string;
}

export interface SettingsField {
  /** The value's dotted path inside the module (`ad_gem_options.poll_sec`): the key it is sent under. */
  readonly path: string;
  /** A type this page does not know is shown read-only, never edited. */
  readonly type: SettingsFieldType | null;
  readonly label: string;
  readonly description: string | null;
  readonly unit: string | null;
  readonly min: number | null;
  readonly max: number | null;
  readonly maxItems: number | null;
  /** A range whose bounds must be whole numbers. */
  readonly wholeNumbers: boolean;
  readonly options: readonly SettingsOption[];
  /** The saved value as the server read it (any JSON). */
  readonly value: unknown;
}

export interface SettingsModule {
  readonly id: string;
  readonly scope: string | null;
  readonly group: string | null;
  readonly title: string;
  readonly description: string | null;
  readonly editable: boolean;
  readonly applyNotice: string | null;
  readonly fields: readonly SettingsField[];
}

export interface SettingsContext {
  readonly instance: string;
  readonly account: string | null;
  readonly profile: string | null;
}

export interface SettingsView {
  readonly context: SettingsContext | null;
  /** The settings revision (config and profile together): the save's expected revision. */
  readonly revision: string | null;
  readonly modules: readonly SettingsModule[];
}

function fieldType(value: unknown): SettingsFieldType | null {
  return FIELD_TYPES.find((type) => type === value) ?? null;
}

function field(raw: unknown): SettingsField | null {
  const path = text(own(raw, 'path')) ?? text(own(raw, 'key'));
  if (path === undefined || path === '') return null;
  const options = (list(own(raw, 'options')) ?? []).flatMap((option): SettingsOption[] => {
    const value = text(own(option, 'value'));
    return value === undefined ? [] : [{ value, label: text(own(option, 'label')) ?? value }];
  });
  const description = text(own(raw, 'description'));
  const unit = text(own(raw, 'unit'));
  return {
    path,
    type: fieldType(own(raw, 'type')),
    label: text(own(raw, 'label')) ?? path,
    description: description === undefined || description === '' ? null : description,
    unit: unit === undefined || unit === '' ? null : unit,
    min: finite(own(raw, 'min')) ?? null,
    max: finite(own(raw, 'max')) ?? null,
    maxItems: finite(own(raw, 'maxItems')) ?? null,
    wholeNumbers: own(raw, 'wholeNumbers') === true,
    options,
    value: own(raw, 'value'),
  };
}

/** The `{instance, account, profile}` a settings read or a save body names, else null. */
export function settingsContext(raw: unknown): SettingsContext | null {
  const instance = id(own(raw, 'instance'));
  if (instance === undefined) return null;
  return {
    instance,
    account: id(own(raw, 'account')) ?? null,
    profile: id(own(raw, 'profile')) ?? null,
  };
}

/** A readable settings answer (`GET /api/settings`, or a save's 200), else null. */
export function settingsView(body: unknown): SettingsView | null {
  const modules = list(own(body, 'modules'));
  if (modules === undefined) return null;
  return {
    context: settingsContext(own(body, 'context')),
    revision: id(own(body, 'revision')) ?? null,
    modules: modules.flatMap((raw): SettingsModule[] => {
      const moduleId = id(own(raw, 'id'));
      if (moduleId === undefined) return [];
      const description = text(own(raw, 'description'));
      return [
        {
          id: moduleId,
          scope: id(own(raw, 'scope')) ?? null,
          group: text(own(raw, 'group')) ?? null,
          title: text(own(raw, 'title')) ?? moduleId,
          description: description === undefined || description === '' ? null : description,
          editable: own(raw, 'editable') === true,
          applyNotice: text(own(raw, 'apply_notice')) ?? null,
          fields: (list(own(raw, 'fields')) ?? []).flatMap((entry) => {
            const found = field(entry);
            return found === null ? [] : [found];
          }),
        },
      ];
    }),
  };
}

/** One module of a settings answer by id, or null. */
export function settingsModule(view: SettingsView | null, moduleId: string): SettingsModule | null {
  return view?.modules.find((module) => module.id === moduleId) ?? null;
}

// ------------------------------------------------------------------ drafts

/** A draft holds raw text per part: `<path>` for one value, `<path>#0`/`<path>#1` for a pair. */
export type SettingsDraft = Readonly<Record<string, string | boolean>>;

/** Draft provenance, persisted beside raw parts and never sent as a settings value. */
export const SETTINGS_BASE_REVISION = '#settings-base-revision';

export function settingsDraftRevision(draft: SettingsDraft): string | null {
  const revision = draft[SETTINGS_BASE_REVISION];
  return typeof revision === 'string' && revision !== '' ? revision : null;
}

export function partKey(path: string, part: 0 | 1 | null): string {
  return part === null ? path : `${path}#${String(part)}`;
}

/** The keys of a field's raw parts in a draft. */
export function fieldParts(field: SettingsField, draft: SettingsDraft = {}): readonly string[] {
  if (field.type === 'string-list')
    return [
      field.path,
      stringListSourceKey(field),
      ...Object.keys(draft).filter((key) => key.startsWith(`${field.path}#list:`)),
    ];
  return field.type === 'range' || field.type === 'point'
    ? [partKey(field.path, 0), partKey(field.path, 1)]
    : [partKey(field.path, null)];
}

/** Stable row keys and removal tombstones keep raw lists in the shared scalar draft store.
 * The path marker means edited; an absent marker keeps the server list. Names are never
 * normalized or checked for ownership. Tombstones retain identity on explicit removal.
 */
function stringListSourceKey(field: Pick<SettingsField, 'path'>): string {
  return `${field.path}#list-source`;
}

/** Internal provenance for the scalar draft journal, never an editable JSON field or
 * the value sent to the server. It preserves malformed saved members across later reads.
 */
export function stringListSource(field: SettingsField, draft: SettingsDraft) {
  const key = stringListSourceKey(field);
  const existing = draft[key];
  return {
    key,
    raw:
      typeof existing === 'string'
        ? existing
        : field.value === undefined
          ? 'null'
          : JSON.stringify(field.value),
  };
}

function stringListOriginal(
  field: SettingsField,
  draft: SettingsDraft,
): readonly unknown[] | undefined {
  if (draft[field.path] !== true) return list(field.value);
  const source = draft[stringListSourceKey(field)];
  if (source === undefined) return [];
  if (typeof source !== 'string') return undefined;
  try {
    const parsed: unknown = JSON.parse(source);
    return list(parsed);
  } catch {
    return undefined;
  }
}

export function stringListRows(field: SettingsField, draft: SettingsDraft) {
  const prefix = `${field.path}#list:`;
  const saved = stringListOriginal(field, draft) ?? [];
  const keys = new Set([
    ...saved.map((_, index) => `${prefix}${String(index)}`),
    ...Object.keys(draft).filter((key) => key.startsWith(prefix)),
  ]);
  return [...keys].flatMap((key) => {
    const index = Number(key.slice(prefix.length));
    const raw = draft[key] ?? saved[index];
    return draft[key] === false ? [] : [{ key, raw }];
  });
}

/** A list is one draft value. A confirmed older save cannot clear its marker or
 * tombstones separately from newer rows: that would reindex the server's readback
 * into the person's draft. Scalar fields continue to settle part by part.
 */
export function retainedStringListKeys(
  current: SettingsDraft,
  sent: SettingsDraft,
): ReadonlySet<string> {
  const paths = new Set(
    Object.keys(current).flatMap((key) => {
      const marker = key.indexOf('#list:');
      return marker < 0 ? [] : [key.slice(0, marker)];
    }),
  );
  const kept = new Set<string>();
  for (const path of paths) {
    const keys = Object.keys(current).filter(
      (key) =>
        key === path || key === stringListSourceKey({ path }) || key.startsWith(`${path}#list:`),
    );
    if (keys.some((key) => current[key] !== sent[key])) keys.forEach((key) => kept.add(key));
  }
  return kept;
}

function stringListProblem(
  field: SettingsField,
  values: readonly unknown[] | undefined,
): FieldReason | null {
  if (values === undefined) return 'unknown_type';
  if (field.maxItems !== null && values.length > field.maxItems) return 'above_max';
  return values.some((value) => typeof value !== 'string' || value.trim() === '') ? 'empty' : null;
}

export function nextStringListKey(field: SettingsField, draft: SettingsDraft): string {
  const prefix = `${field.path}#list:`;
  const indices = Object.keys(draft)
    .filter((key) => key.startsWith(prefix))
    .map((key) => Number(key.slice(prefix.length)))
    .filter(Number.isInteger);
  return `${prefix}${String(Math.max((stringListOriginal(field, draft)?.length ?? 0) - 1, ...indices) + 1)}`;
}

/** The text a part shows before any edit: the saved value as the server sent it. */
export function savedPart(field: SettingsField, part: 0 | 1 | null): string | boolean {
  if (field.type === 'boolean') return field.value === true;
  const value = part === null ? field.value : list(field.value)?.[part];
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'string') return value;
  return '';
}

export type FieldReason =
  | 'empty'
  | 'not_a_number'
  | 'not_whole'
  | 'below_min'
  | 'above_max'
  | 'reversed'
  | 'unknown_option'
  | 'unknown_type';

export type FieldCheck =
  | { readonly kind: 'unchanged' }
  | { readonly kind: 'valid'; readonly value: unknown }
  | { readonly kind: 'invalid'; readonly reason: FieldReason; readonly part: 0 | 1 | null };

function number(
  raw: string | boolean | undefined,
  field: SettingsField,
  whole: boolean,
): { readonly value: number } | { readonly reason: FieldReason } {
  if (typeof raw !== 'string') return { reason: 'not_a_number' };
  const typed = typedDecimal(raw);
  if ('reason' in typed) return typed;
  const { value } = typed;
  if (whole && !Number.isInteger(value)) return { reason: 'not_whole' };
  if (field.min !== null && value < field.min) return { reason: 'below_min' };
  if (field.max !== null && value > field.max) return { reason: 'above_max' };
  return { value };
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Judge a field's draft parts (`undefined`: not edited). Unedited fields are `unchanged`;
 * an edited field whose parsed value equals the saved one is `unchanged` too, so only
 * changed fields are sent, as the recorded saves do.
 */
export function checkField(field: SettingsField, draft: SettingsDraft): FieldCheck {
  const parts = fieldParts(field, draft).map((key) => draft[key]);
  if (parts.every((part) => part === undefined)) return { kind: 'unchanged' };
  const valid = (value: unknown): FieldCheck =>
    sameJson(value, field.value) ? { kind: 'unchanged' } : { kind: 'valid', value };
  switch (field.type) {
    case 'string-list': {
      if (draft[field.path] !== true)
        return { kind: 'invalid', reason: 'unknown_type', part: null };
      const rows = stringListRows(field, draft);
      if (
        draft[stringListSourceKey(field)] !== undefined &&
        stringListOriginal(field, draft) === undefined
      )
        return { kind: 'invalid', reason: 'unknown_type', part: null };
      const values = rows.map((row) => row.raw);
      const reason = stringListProblem(field, values);
      return reason === null ? valid(values) : { kind: 'invalid', reason, part: null };
    }
    case null:
      return { kind: 'invalid', reason: 'unknown_type', part: null };
    case 'boolean': {
      const raw = parts[0];
      return typeof raw === 'boolean'
        ? valid(raw)
        : { kind: 'invalid', reason: 'not_a_number', part: null };
    }
    case 'string': {
      const raw = parts[0];
      return typeof raw === 'string'
        ? valid(raw)
        : { kind: 'invalid', reason: 'unknown_type', part: null };
    }
    case 'enum': {
      const raw = parts[0];
      return typeof raw === 'string' && field.options.some((option) => option.value === raw)
        ? valid(raw)
        : { kind: 'invalid', reason: 'unknown_option', part: null };
    }
    case 'integer':
    case 'number': {
      const parsed = number(
        parts[0] ?? savedPart(field, null),
        field,
        field.type === 'integer' || field.wholeNumbers,
      );
      return 'reason' in parsed
        ? { kind: 'invalid', reason: parsed.reason, part: null }
        : valid(parsed.value);
    }
    case 'range':
    case 'point': {
      const whole = field.type === 'range' && field.wholeNumbers;
      const low = number(parts[0] ?? savedPart(field, 0), field, whole);
      if ('reason' in low) return { kind: 'invalid', reason: low.reason, part: 0 };
      const high = number(parts[1] ?? savedPart(field, 1), field, whole);
      if ('reason' in high) return { kind: 'invalid', reason: high.reason, part: 1 };
      if (field.type === 'range' && low.value > high.value)
        return { kind: 'invalid', reason: 'reversed', part: null };
      return valid([low.value, high.value]);
    }
  }
}

/**
 * A saved value the field's own rules refuse (review F6M-10): shown with its reason so the
 * person sees it, never added to a save by itself (an unedited field stays unchanged).
 */
export function savedFieldProblem(
  field: SettingsField,
): { readonly reason: FieldReason; readonly part: 0 | 1 | null } | null {
  if (field.type === 'boolean') return null;
  if (field.type === 'string-list') {
    const reason = stringListProblem(field, list(field.value));
    return reason === null ? null : { reason, part: null };
  }
  const keys = fieldParts(field);
  const saved = Object.fromEntries(
    keys.map((key, index) => [
      key,
      savedPart(field, keys.length === 1 ? null : index === 0 ? 0 : 1),
    ]),
  );
  // Empty enum values are explicit choices: only the server's options can permit them.
  // Other absent saved values read as '' (savedPart) and are not judged.
  if (field.type !== 'enum' && Object.values(saved).some((raw) => raw === '')) return null;
  const checked = checkField(field, saved);
  return checked.kind === 'invalid' ? { reason: checked.reason, part: checked.part } : null;
}

export interface ModuleReview {
  /** The changed values, keyed by field path: the `values` of a save. */
  readonly values: Readonly<Record<string, unknown>>;
  readonly invalid: Readonly<
    Record<string, { readonly reason: FieldReason; readonly part: 0 | 1 | null }>
  >;
}

/** A module's draft judged field by field. */
export function reviewModule(module: SettingsModule, draft: SettingsDraft): ModuleReview {
  const values: Record<string, unknown> = {};
  const invalid: Record<string, { readonly reason: FieldReason; readonly part: 0 | 1 | null }> = {};
  for (const entry of module.fields) {
    const checked = checkField(entry, draft);
    if (checked.kind === 'valid') values[entry.path] = checked.value;
    else if (checked.kind === 'invalid')
      invalid[entry.path] = { reason: checked.reason, part: checked.part };
  }
  return { values, invalid };
}

// ------------------------------------------------------------------ saves

/** What a settings save sent, as its record keeps it. */
export interface SentSettings {
  readonly module?: string | undefined;
  readonly revision?: string | undefined;
  readonly values?: Readonly<Record<string, unknown>> | undefined;
}

export type SettingsSaveAnswer =
  /** A 200 whose module holds every sent value: the saved snapshot. */
  | { readonly kind: 'saved'; readonly revision: string | null; readonly backup: string | null }
  /**
   * 400 or 409 with `ok: false`. A refused stale revision may follow a save that
   * committed (an identical resend is refused as stale), so a 409 is judged again by
   * a later read before it is called not saved.
   */
  | {
      readonly kind: 'refused';
      readonly status: number;
      readonly code: string | null;
      readonly error: string | null;
    }
  /** No receipt: a 5xx (a failure after the write is a text 500), a text page, a 200 without the values. */
  | { readonly kind: 'unknown' };

function holdsValues(view: SettingsView | null, sent: SentSettings): boolean {
  if (sent.module === undefined || sent.values === undefined) return false;
  const found = settingsModule(view, sent.module);
  if (found === null) return false;
  return entries(sent.values).every(([path, value]) =>
    found.fields.some((entry) => entry.path === path && sameJson(entry.value, value)),
  );
}

export function settingsSaveAnswer(
  answer: { readonly status: number; readonly body: unknown },
  sent: SentSettings,
): SettingsSaveAnswer {
  if (answer.status === 200 && own(answer.body, 'ok') === true) {
    // C15-4: the receipt cannot prove which context every page should now use.
    // Retain its original journal until a later config read verifies the transition.
    if (sent.module === 'active-instance') return { kind: 'unknown' };
    const view = settingsView(answer.body);
    if (holdsValues(view, sent))
      return {
        kind: 'saved',
        revision: view?.revision ?? null,
        backup: text(own(answer.body, 'backup')) ?? null,
      };
    return { kind: 'unknown' };
  }
  if ((answer.status === 400 || answer.status === 409) && own(answer.body, 'ok') === false)
    return {
      kind: 'refused',
      status: answer.status,
      code: id(own(answer.body, 'code')) ?? null,
      error: text(own(answer.body, 'error')) ?? null,
    };
  return { kind: 'unknown' };
}

/**
 * What a later settings read says about a save whose outcome is not known: `applied` when
 * the read is of the context the save named and its module holds every sent value; null
 * otherwise. An unchanged revision or absent values never prove the save failed: the
 * original request may still be running and commit later (review F6C-R1-02), so the save
 * stays unknown. A read of another emulator, account or profile says nothing (F6C-R1-05).
 */
export function settleSettingsSave(
  readBody: unknown,
  sent: SentSettings,
  context: SettingsContext,
): 'applied' | null {
  // C15-4: selection intentionally changes context. Only config readback with a
  // readable target address proves it; a settings receipt never adopts a context.
  if (sent.module === 'active-instance') {
    const intended = id(own(sent.values, 'active_instance'));
    const named = configContext(readBody);
    return intended !== undefined &&
      intended !== context.instance &&
      named?.instance === intended &&
      configRevision(readBody) !== null
      ? 'applied'
      : null;
  }
  const view = settingsView(readBody);
  if (view === null || sent.module === undefined || !sameSettingsContext(view.context, context))
    return null;
  return holdsValues(view, sent) ? 'applied' : null;
}

/** The emulator, account and profile two settings contexts name are the same. */
export function sameSettingsContext(a: SettingsContext | null, b: SettingsContext | null): boolean {
  return (
    a !== null &&
    b !== null &&
    a.instance === b.instance &&
    a.account === b.account &&
    a.profile === b.profile
  );
}
