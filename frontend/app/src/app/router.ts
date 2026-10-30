import { useMemo, useSyncExternalStore } from 'react';
import { PAGE_IDS, pageHref, type PageId, type PageTarget } from '../ui/routes.ts';

/**
 * A tiny hash router: `#dashboard`-style routes like the legacy page's, so
 * Flask keeps serving static files only and no server route is needed. A page
 * may take one target after a slash: the Runs page opens `#runs/saved/<run>`
 * (one run's saved settings) or `#runs/live` (this battle's live settings).
 */
export const ROUTE_IDS = PAGE_IDS;

export type RouteId = PageId;

export const DEFAULT_ROUTE: RouteId = 'dashboard';

/** Where a page opens: the Runs page's live editor, or one run's saved settings. */
export type RouteTarget = PageTarget;

export type RouteMatch =
  | { readonly kind: 'page'; readonly id: RouteId; readonly target: RouteTarget | null }
  | { readonly kind: 'not_found'; readonly hash: string };

function toRouteId(value: string): RouteId | null {
  return ROUTE_IDS.find((id) => id === value) ?? null;
}

function decoded(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

function targetOf(id: RouteId, rest: readonly string[]): RouteTarget | null | undefined {
  if (rest.length === 0) return null;
  if (id === 'collection') {
    // The Collection page opens on one gathering policy: `#collection/policy/<id>`.
    const policy = rest.length === 2 && rest[0] === 'policy' ? decoded(rest[1] ?? '') : null;
    return policy === null || policy === '' ? undefined : { kind: 'policy', policy };
  }
  if (id === 'abilities' && rest.length === 2) {
    const family = rest[0];
    const policy = decoded(rest[1] ?? '');
    return (family === 'uw_policies' || family === 'rescue_policies') &&
      policy !== null &&
      policy !== ''
      ? { kind: 'ability', family, policy }
      : undefined;
  }
  if (id !== 'runs') return undefined;
  if (rest.length === 1 && rest[0] === 'live') return { kind: 'live' };
  if (rest.length === 2 && rest[0] === 'saved') {
    const run = decoded(rest[1] ?? '');
    return run === null || run === '' ? undefined : { kind: 'saved', run };
  }
  return undefined;
}

/** Match `location.hash`. An empty hash opens the default page; `#/name` and `#name` are the same route. */
export function matchHash(hash: string): RouteMatch {
  // A page's own state may follow a `?` (the Runtime page keeps its applied diagnostic
  // filters there): it never decides the route.
  const name = hash.replace(/^#\/?/, '').replace(/\?.*$/su, '');
  if (name === '') {
    return { kind: 'page', id: DEFAULT_ROUTE, target: null };
  }
  const [head = '', ...rest] = name.split('/');
  const id = toRouteId(head);
  const target = id === null ? undefined : targetOf(id, rest);
  return id === null || target === undefined
    ? { kind: 'not_found', hash }
    : { kind: 'page', id, target };
}

/** The hash of a page, and of a target on it (`#runs/saved/bp_farm`, `#runs/live`). */
export const hrefFor = pageHref;

function subscribe(onChange: () => void): () => void {
  window.addEventListener('hashchange', onChange);
  return () => {
    window.removeEventListener('hashchange', onChange);
  };
}

function currentHash(): string {
  return window.location.hash;
}

function serverHash(): string {
  return '';
}

/** The current route; re-renders on `hashchange`. */
export function useRouteHash(): string {
  return useSyncExternalStore(subscribe, currentHash, serverHash);
}

export function useRoute(): RouteMatch {
  const hash = useRouteHash();
  return useMemo(() => matchHash(hash), [hash]);
}
