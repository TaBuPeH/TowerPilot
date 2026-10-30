import { useCallback, useRef, useSyncExternalStore } from 'react';

/**
 * A minimal external store: one immutable snapshot, replaced on update.
 * Components subscribe through `useStoreSelector` (useSyncExternalStore).
 */
export interface Store<S> {
  readonly getSnapshot: () => S;
  readonly subscribe: (listener: () => void) => () => void;
  readonly update: (next: (current: S) => S) => void;
}

export function createStore<S>(initial: S): Store<S> {
  let snapshot = initial;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    update: (next) => {
      const value = next(snapshot);
      if (Object.is(value, snapshot)) {
        return;
      }
      snapshot = value;
      for (const listener of [...listeners]) {
        listener();
      }
    },
  };
}

function plain(value: object): boolean {
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/**
 * Structural equality of two view models: primitives (server text included)
 * by `Object.is`, arrays and plain objects (confirmed claims included) member
 * by member; any other object by identity. Used to skip a
 * re-render when a new snapshot yields an equal view model (a clock tick).
 */
export function viewEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item: unknown, index) => viewEqual(item, b[index]))
    );
  }
  if (plain(a) && plain(b)) {
    const keys = Object.keys(a);
    const other = new Map(Object.entries(b));
    return (
      keys.length === other.size &&
      Object.entries(a).every(([key, value]) => other.has(key) && viewEqual(value, other.get(key)))
    );
  }
  return false;
}

/**
 * Read `select(snapshot)`. The selection is recomputed only when the snapshot
 * (or `select`) changes, and when `isEqual` says the new view model equals the
 * previous one, the previous one is returned, so the component does not
 * re-render (pass `viewEqual` for view models rebuilt on every snapshot).
 * Pass a stable `select` and `isEqual` (module-level or memoized).
 */
export function useStoreSelector<S, V>(
  store: Store<S>,
  select: (state: S) => V,
  isEqual: (a: V, b: V) => boolean = Object.is,
): V {
  const cache = useRef<{
    readonly snapshot: S;
    readonly select: (state: S) => V;
    readonly value: V;
  } | null>(null);
  const selection = useCallback((): V => {
    const snapshot = store.getSnapshot();
    const cached = cache.current;
    if (cached?.snapshot === snapshot && cached.select === select) return cached.value;
    const value = select(snapshot);
    const kept = cached !== null && cached.select === select && isEqual(cached.value, value);
    const result = kept ? cached.value : value;
    cache.current = { snapshot, select, value: result };
    return result;
  }, [store, select, isEqual]);
  return useSyncExternalStore(store.subscribe, selection, selection);
}
