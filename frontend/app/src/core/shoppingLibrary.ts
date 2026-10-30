import { entries, id, list, own, ownPath, record, stringList, text } from './read.ts';
import { queuePlan, type QueueDraft } from './shoppingPlan.ts';
import { typedDecimal } from './typedNumber.ts';

/**
 * Shared shopping policies own their source shape and usage here. The v2 owner remains
 * shoppingPlan.ts. Legacy lists remain lists; mappings retain optional and unknown members.
 * Backend playerprofile._validate_shopping_list owns vocabulary, ranges and integer rules.
 * A draft pins its complete original value and revision before the first edit, so a poll
 * cannot silently rebase a whole-policy replacement. Each directive has a stable draft key.
 */
export interface LibraryPolicy {
  readonly id: string;
  readonly value: unknown;
  readonly usedBy: readonly string[];
}

export function librarySource(body: unknown) {
  const name = id(own(body, 'name'));
  const revision = id(own(body, 'revision'));
  const profile = own(body, 'profile');
  if (
    own(body, 'ok') !== true ||
    name === undefined ||
    revision === undefined ||
    record(profile) === undefined
  )
    return null;
  const blueprints = entries(own(profile, 'blueprints'));
  const policies: readonly LibraryPolicy[] = entries(
    ownPath(profile, 'policies', 'shopping_lists'),
  ).map(([key, value]) => ({
    id: key,
    value,
    usedBy: blueprints.filter(([, run]) => own(run, 'shopping') === key).map(([run]) => run),
  }));
  return { name, revision, policies };
}

export function shoppingVocabulary(body: unknown) {
  if (own(body, 'ok') !== true) return null;
  const tabs = stringList(ownPath(body, 'vocab', 'shop_tabs', 'values'));
  const modes = stringList(ownPath(body, 'vocab', 'shop_modes', 'values'));
  const stats = stringList(ownPath(body, 'vocab', 'shop_stats', 'values'));
  return tabs === undefined || modes === undefined || stats === undefined
    ? null
    : { tabs, modes, stats };
}

export function legacyPolicy(value: unknown) {
  if (queuePlan(value) !== null) return null;
  const bare = list(value);
  const directives = bare ?? list(own(value, 'directives'));
  if (directives === undefined || directives.some((line) => record(line) === undefined))
    return null;
  const kind: 'list' | 'mapping' = bare === undefined ? 'mapping' : 'list';
  return { kind, raw: value, directives };
}

export function draftJson(raw: string | boolean | undefined): unknown {
  if (typeof raw !== 'string') return undefined;
  try {
    const value: unknown = JSON.parse(raw);
    return value;
  } catch {
    return undefined;
  }
}

export function libraryBase(value: unknown, draft: QueueDraft): unknown {
  return Object.hasOwn(draft, 'library_base') ? draftJson(draft['library_base']) : value;
}

/** Pin without replacing an existing pin. Draft metadata shares the persisted raw map. */
export function pinLibrary(value: unknown, revision: string, draft: QueueDraft): QueueDraft {
  return Object.hasOwn(draft, 'library_base')
    ? draft
    : {
        ...draft,
        library_base: JSON.stringify(value),
        library_revision: revision,
      };
}

function directiveKeys(value: unknown, draft: QueueDraft): readonly string[] {
  const staged = stringList(draftJson(draft['legacy_order']));
  return staged ?? (legacyPolicy(value)?.directives ?? []).map((_, index) => `line:${index}`);
}

export interface DirectiveReview {
  readonly key: string;
  readonly enabled: boolean;
  readonly tab: string;
  readonly mode: string;
  readonly stats: readonly string[];
  readonly clicks: string;
  readonly clicksProblem: 'empty' | 'not_a_number' | null;
}

/** Full legacy value, preserving every unedited field, optional flag and source shape. */
export function reviewLegacy(value: unknown, draft: QueueDraft) {
  const model = legacyPolicy(value);
  let problems = model === null ? 1 : 0;
  const lines: DirectiveReview[] = [];
  const directives = directiveKeys(value, draft).map((key) => {
    const original = key.startsWith('line:') ? model?.directives[Number(key.slice(5))] : undefined;
    const built: Record<string, unknown> = Object.fromEntries(entries(original));
    const rawText = (field: string) =>
      text(draft[`${key}:${field}`]) ?? text(own(original, field)) ?? '';
    const tab = rawText('tab');
    const mode = rawText('mode');
    const enabled =
      typeof draft[`${key}:enabled`] === 'boolean'
        ? draft[`${key}:enabled`] === true
        : own(original, 'enabled') !== false;
    const stats =
      stringList(draftJson(draft[`${key}:stats`])) ?? stringList(own(original, 'stats')) ?? [];
    for (const field of ['enabled', 'tab', 'mode']) {
      if (draft[`${key}:${field}`] !== undefined) built[field] = draft[`${key}:${field}`];
    }
    if (draft[`${key}:stats`] !== undefined) built['stats'] = stats;
    const storedClicks = own(original, 'clicks');
    const clicks =
      text(draft[`${key}:clicks`]) ??
      (typeof storedClicks === 'number' ? String(storedClicks) : '');
    const parsed = typedDecimal(clicks);
    const clicksProblem = mode === 'clicks' && !('value' in parsed) ? parsed.reason : null;
    if (clicksProblem !== null) problems += 1;
    if (mode === 'clicks') {
      if (draft[`${key}:clicks`] !== undefined && 'value' in parsed) built['clicks'] = parsed.value;
    } else if (draft[`${key}:mode`] !== undefined) {
      // An explicit mode change removes a clicks-only value. Merely hiding it never does.
      delete built['clicks'];
    }
    lines.push({ key, enabled, tab, mode, stats, clicks, clicksProblem });
    return built;
  });
  const enabled =
    typeof draft['enabled'] === 'boolean' ? draft['enabled'] : own(value, 'enabled') !== false;
  const label = text(draft['label']) ?? text(own(value, 'label')) ?? '';
  const mapping = {
    ...Object.fromEntries(entries(value)),
    ...(draft['enabled'] === undefined ? {} : { enabled }),
    ...(draft['label'] === undefined ? {} : { label }),
    directives,
  };
  return {
    kind: model?.kind ?? null,
    enabled,
    label,
    lines,
    problems,
    value: problems > 0 ? null : model?.kind === 'list' ? directives : mapping,
  };
}

export function addDirective(value: unknown, draft: QueueDraft): QueueDraft {
  const keys = directiveKeys(value, draft);
  let index = 0;
  while (Object.keys(draft).some((key) => key.startsWith(`new:${index}:`))) index += 1;
  const key = `new:${index}`;
  return {
    ...draft,
    legacy_order: JSON.stringify([...keys, key]),
    [`${key}:enabled`]: true,
    [`${key}:tab`]: '',
    [`${key}:mode`]: '',
    [`${key}:stats`]: '[]',
  };
}

export function removeDirective(value: unknown, draft: QueueDraft, key: string): QueueDraft {
  return {
    ...draft,
    legacy_order: JSON.stringify(directiveKeys(value, draft).filter((item) => item !== key)),
  };
}

export function moveDirective(
  value: unknown,
  draft: QueueDraft,
  key: string,
  step: -1 | 1,
): QueueDraft | null {
  const keys = [...directiveKeys(value, draft)];
  const from = keys.indexOf(key);
  const to = from + step;
  if (from < 0 || to < 0 || to >= keys.length) return null;
  keys.splice(from, 1);
  keys.splice(to, 0, key);
  return { ...draft, legacy_order: JSON.stringify(keys) };
}
