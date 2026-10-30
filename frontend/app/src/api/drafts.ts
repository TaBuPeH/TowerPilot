import { entries, record } from '../core/read.ts';
import { readJsonItem, writeJsonItem, type KeyValueStorage } from './storage.ts';

/**
 * The Runs page's drafts in this tab's session storage (brief SR-2; review
 * F6R-02): what a person typed or switched, raw (`030` stays `030`), per draft
 * scope (`saved:<context>:<run>`, `live:<battle>:<generation>`), so a reload
 * keeps it beside the unresolved save it may belong to. The scopes keep their
 * isolation: a draft is restored only into its own scope. Malformed entries
 * are skipped, never repaired. The page drafts (Profiles, Collection, Abilities and every
 * settings module, review F6C-R1-01) are scoped to one context: their scope
 * ends with the context key (store contextScopeKey), checked here.
 */
export const DRAFTS_KEY = 'tp.runDrafts.v1';

/** At most this many scopes are kept (the newest written last). */
const MAX_SCOPES = 40;

export type StoredDrafts = Readonly<Record<string, Readonly<Record<string, string | boolean>>>>;

/**
 * Page draft scopes `<page>:<form>:<context key>`; the key is `[instance, serial, account,
 * profile]`, or `[instance, serial]` for a Profiles form that belongs to the emulator.
 */
const CONTEXT_SCOPED = [
  'profiles:',
  'collection:',
  'runs:',
  'settings:',
  'abilities:',
  // R1, R5, R6: the followed operation, the scan choices and the watch length of a context.
  'recognition:',
] as const;

function contextKeyPart(value: string): boolean {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return false;
  }
  return (
    Array.isArray(parsed) &&
    (parsed.length === 4 || parsed.length === 2) &&
    typeof parsed[0] === 'string' &&
    parsed.every((part) => part === null || typeof part === 'string')
  );
}

function scopeName(value: string): boolean {
  if (value.startsWith('saved:') || value.startsWith('live:')) return true;
  const prefix = CONTEXT_SCOPED.find((name) => value.startsWith(name));
  if (prefix === undefined) return false;
  const rest = value.slice(prefix.length);
  const at = rest.indexOf(':["');
  return at > 0 && contextKeyPart(rest.slice(at + 1));
}

/** Every stored draft, checked value by value. */
export function restoreDrafts(storage: KeyValueStorage | null): StoredDrafts {
  const raw = readJsonItem(storage, DRAFTS_KEY);
  if (record(raw) === undefined) return Object.freeze({});
  const out: Record<string, Readonly<Record<string, string | boolean>>> = {};
  for (const [scope, draft] of entries(raw)) {
    if (!scopeName(scope) || record(draft) === undefined) continue;
    const values: Record<string, string | boolean> = {};
    for (const [path, value] of entries(draft)) {
      if (typeof value === 'string' || typeof value === 'boolean') values[path] = value;
    }
    if (Object.keys(values).length > 0) out[scope] = Object.freeze(values);
  }
  return Object.freeze(out);
}

/** Write the drafts (the newest scopes, empty ones dropped); remove the key when none is left. */
export function saveDrafts(storage: KeyValueStorage | null, drafts: StoredDrafts): void {
  const kept = Object.entries(drafts)
    .filter(([, draft]) => Object.keys(draft).length > 0)
    .slice(-MAX_SCOPES);
  writeJsonItem(storage, DRAFTS_KEY, kept.length === 0 ? undefined : Object.fromEntries(kept));
}
