import type { KeyValueStorage } from '../../src/api/storage.ts';
import type { Transport, TransportRequest, TransportResult } from '../../src/api/transport.ts';

/**
 * The fake transport of spec OD-8 for Vitest: every request is recorded with
 * its exact body text, and a test decides per request whether it is answered
 * at once (a route), held (answered, lost or dropped later, in any order) or
 * failed. No socket is opened (tests/unit/setup.ts refuses them).
 */
export interface Recorded {
  readonly method: 'GET' | 'POST';
  readonly path: string;
  readonly body: string | undefined;
  readonly at: number;
}

export interface Held {
  readonly request: Recorded;
  /** Answer with `status` and `body` (a JSON value, or a string sent as raw text). */
  readonly respond: (status: number, body: unknown) => void;
  /** The answer never arrives: a network failure at the client. */
  readonly lose: () => void;
  readonly settled: () => boolean;
}

export type Route = (
  request: Recorded,
) => { readonly status: number; readonly body: unknown } | 'hold';

export interface FakeTransport {
  readonly transport: Transport;
  readonly requests: Recorded[];
  readonly held: Held[];
  /** Every POST except the C-20 diagnostics route. */
  readonly writes: () => Recorded[];
  readonly route: (method: 'GET' | 'POST', path: string | RegExp, reply: Route) => void;
  /** The first unsettled held request matching `path`, or a thrown error. */
  readonly next: (path: string | RegExp, method?: 'GET' | 'POST') => Held;
  /** The newest unsettled held request matching `path`, or a thrown error. */
  readonly last: (path: string | RegExp, method?: 'GET' | 'POST') => Held;
}

function text(body: unknown): string {
  return typeof body === 'string' ? body : JSON.stringify(body);
}

export function createFakeTransport(now: () => number = () => Date.now()): FakeTransport {
  const requests: Recorded[] = [];
  const held: Held[] = [];
  const routes: { method: string; path: string | RegExp; reply: Route }[] = [];
  const matches = (pattern: string | RegExp, path: string) =>
    typeof pattern === 'string' ? pattern === path : pattern.test(path);

  const transport: Transport = (request: TransportRequest) => {
    const recorded: Recorded = {
      method: request.method,
      path: request.path,
      body: request.body,
      at: now(),
    };
    requests.push(recorded);
    const route = [...routes]
      .reverse()
      .find((entry) => entry.method === request.method && matches(entry.path, request.path));
    const reply = route?.reply(recorded) ?? 'hold';
    if (reply !== 'hold') {
      return Promise.resolve({ kind: 'response', status: reply.status, text: text(reply.body) });
    }
    return new Promise<TransportResult>((resolve) => {
      let done = false;
      const finish = (result: TransportResult) => {
        if (done) return;
        done = true;
        resolve(result);
      };
      request.signal?.addEventListener('abort', () => {
        finish({ kind: 'failed', reason: 'aborted', detail: 'AbortError: aborted' });
      });
      held.push({
        request: recorded,
        respond: (status, body) => {
          finish({ kind: 'response', status, text: text(body) });
        },
        lose: () => {
          finish({ kind: 'failed', reason: 'network', detail: 'TypeError: Failed to fetch' });
        },
        settled: () => done,
      });
    });
  };

  return {
    transport,
    requests,
    held,
    writes: () =>
      requests.filter(
        (entry) => entry.method === 'POST' && entry.path !== '/api/diagnostics/client',
      ),
    route: (method, path, reply) => {
      routes.push({ method, path, reply });
    },
    next: (path, method) => {
      const found = held.find(
        (entry) =>
          !entry.settled() &&
          matches(path, entry.request.path) &&
          (method === undefined || entry.request.method === method),
      );
      if (found === undefined) throw new Error(`no held request for ${String(path)}`);
      return found;
    },
    last: (path, method) => {
      const found = [...held]
        .reverse()
        .find(
          (entry) =>
            !entry.settled() &&
            matches(path, entry.request.path) &&
            (method === undefined || entry.request.method === method),
        );
      if (found === undefined) throw new Error(`no held request for ${String(path)}`);
      return found;
    },
  };
}

/** sessionStorage in memory (the Web Storage subset the app uses). */
export function createMemoryStorage(
  initial: Readonly<Record<string, string>> = {},
): KeyValueStorage & {
  readonly entries: () => Record<string, string>;
} {
  const items = new Map<string, string>(Object.entries(initial));
  return {
    get length() {
      return items.size;
    },
    key: (index) => [...items.keys()][index] ?? null,
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => {
      items.set(key, value);
    },
    removeItem: (key) => {
      items.delete(key);
    },
    entries: () => Object.fromEntries(items),
  };
}

/** A visibility source a test switches. */
export function createFakeVisibility(hidden = false) {
  let isHidden = hidden;
  const listeners = new Set<() => void>();
  return {
    hidden: () => isHidden,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    set: (next: boolean) => {
      isHidden = next;
      for (const listener of [...listeners]) listener();
    },
  };
}

/** Deterministic ids and keys: `<prefix>-0001`, ... (8 to 100 letters, digits, - or _). */
export function counter(prefix: string): () => string {
  let n = 0;
  return () => {
    n += 1;
    return `${prefix}-${n.toString().padStart(4, '0')}`;
  };
}
