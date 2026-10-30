/**
 * The page's session storage (ADR decision 5): unresolved write records and
 * the context this browser session recorded survive a reload in this tab and
 * nowhere else. Storage may be unavailable (a privacy mode) or full; every
 * access is guarded, and a failure degrades to "not recorded", never to a
 * crash or a resend.
 */
export interface KeyValueStorage {
  readonly length: number;
  readonly key: (index: number) => string | null;
  readonly getItem: (key: string) => string | null;
  readonly setItem: (key: string, value: string) => void;
  readonly removeItem: (key: string) => void;
}

/** The browser's sessionStorage, or null when this page may not use it. */
export function browserSessionStorage(): KeyValueStorage | null {
  try {
    const storage = sessionStorage;
    const probe = 'tp.probe';
    storage.setItem(probe, '1');
    storage.removeItem(probe);
    return storage;
  } catch {
    return null;
  }
}

/** Every key of `storage` that starts with `prefix`. */
export function keysWithPrefix(storage: KeyValueStorage, prefix: string): readonly string[] {
  const keys: string[] = [];
  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key?.startsWith(prefix) === true) keys.push(key);
    }
  } catch {
    return [];
  }
  return keys;
}

/** Independent editor journals share validation without removing each other's records. */
export function prefixedStorage(
  storage: KeyValueStorage | null,
  prefix: string,
): KeyValueStorage | null {
  if (storage === null) return null;
  const keys = () => keysWithPrefix(storage, prefix);
  return {
    get length() {
      return keys().length;
    },
    key: (index) => keys()[index]?.slice(prefix.length) ?? null,
    getItem: (key) => storage.getItem(prefix + key),
    setItem: (key, value) => {
      storage.setItem(prefix + key, value);
    },
    removeItem: (key) => {
      storage.removeItem(prefix + key);
    },
  };
}

/** The JSON value stored at `key`, or undefined when absent, unreadable or not JSON. */
export function readJsonItem(storage: KeyValueStorage | null, key: string): unknown {
  if (storage === null) return undefined;
  try {
    const text = storage.getItem(key);
    return text === null ? undefined : JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** Store `value` as JSON at `key` (or remove the key for undefined); false when that failed. */
export function writeJsonItem(
  storage: KeyValueStorage | null,
  key: string,
  value: unknown,
): boolean {
  if (storage === null) return false;
  try {
    if (value === undefined) {
      storage.removeItem(key);
    } else {
      storage.setItem(key, JSON.stringify(value));
    }
    return true;
  } catch {
    return false;
  }
}
