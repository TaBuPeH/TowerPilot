/**
 * Own-property readers for raw server data (safety rule S-095).
 *
 * Every core module reads wire values through these helpers: a key counts only
 * when it is an OWN property of a plain object, so inherited names such as
 * `constructor`, `toString` and `__proto__` never match; values are compared by
 * `typeof` and `===` only, never converted to strings or numbers implicitly.
 * Nothing here throws for a malformed value: it reads as `undefined` ("absent
 * or not that kind").
 */

/** `value` when it is a plain object as JSON makes it (not null, not an array), else `undefined`. */
export function record(value: unknown): object | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return undefined;
  }
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null ? value : undefined;
}

/** True when `value` is a plain object. */
export function isRecord(value: unknown): boolean {
  return record(value) !== undefined;
}

/** True when `value` is a plain object with `key` as an own property, whatever its value. */
export function hasOwn(value: unknown, key: string): boolean {
  const object = record(value);
  return object !== undefined && Object.hasOwn(object, key);
}

/** The own property `key` of a plain object; `undefined` when absent or not a plain object. */
export function own(value: unknown, key: string): unknown {
  const object = record(value);
  if (object === undefined || !Object.hasOwn(object, key)) {
    return undefined;
  }
  const read: unknown = Reflect.get(object, key);
  return read;
}

/** The own property at `keys`, read one own property at a time. */
export function ownPath(value: unknown, ...keys: readonly string[]): unknown {
  let current: unknown = value;
  for (const key of keys) {
    current = own(current, key);
  }
  return current;
}

/** `value` when it is a finite number (never a numeric string, NaN or an infinity). */
export function finite(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/** `value` when it is a revision: a safe integer >= 0. */
export function revision(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

/** `value` when it is a PID: a safe integer > 0. */
export function pid(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : undefined;
}

/** `value` when it is a string with at least one character (an id). */
export function id(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** `value` when it is a string. */
export function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

/** `value` when it is an array. */
export function list(value: unknown): readonly unknown[] | undefined {
  return Array.isArray(value) ? value : undefined;
}

/** `value` when it is an array of strings only. */
export function stringList(value: unknown): readonly string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const out: string[] = [];
  for (const item of value) {
    if (typeof item !== 'string') {
      return undefined;
    }
    out.push(item);
  }
  return out;
}

/**
 * The entry a code-keyed table holds for `code` as its OWN key, else `fallback` (an inherited
 * name such as `constructor` is never an entry). The one lookup of a code in a word table.
 */
export function ownOr<T>(table: Readonly<Record<string, T>>, code: string, fallback: T): T {
  return Object.hasOwn(table, code) ? (table[code] ?? fallback) : fallback;
}

/** The own entries of a plain object (string keys, own enumerable properties only). */
export function entries(value: unknown): readonly (readonly [string, unknown])[] {
  const object = record(value);
  return object === undefined ? [] : Object.entries(object);
}
