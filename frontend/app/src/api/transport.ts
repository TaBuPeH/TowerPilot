/** Paths the app may use: the dashboard's JSON API. */
export type ApiPath = `/api/${string}`;

/**
 * The one way the app reaches the dashboard server (ADR-001 rule 1). Every
 * read and write goes through a `Transport`; `fetchTransport` is the only
 * caller of the browser's network API, and tests inject a fake transport that
 * holds, drops, reorders or answers requests on demand (spec OD-8).
 *
 * A transport never retries and never throws: a request either produced an
 * HTTP response (any status, the body as text) or failed (aborted, or a
 * network error), and the caller decides what that means.
 */
export interface TransportRequest {
  readonly method: 'GET' | 'POST';
  readonly path: ApiPath;
  /** The JSON text of a POST body, frozen by the caller. */
  readonly body?: string;
  readonly signal?: AbortSignal;
}

export type TransportResult =
  | { readonly kind: 'response'; readonly status: number; readonly text: string }
  | { readonly kind: 'failed'; readonly reason: 'aborted' | 'network'; readonly detail: string };

export type Transport = (request: TransportRequest) => Promise<TransportResult>;

function describe(error: unknown): string {
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error);
}

/** The browser transport: same-origin, never cached, JSON in and out, no retry. */
export const fetchTransport: Transport = async (request) => {
  try {
    const response = await fetch(request.path, {
      method: request.method,
      headers:
        request.body === undefined
          ? { Accept: 'application/json' }
          : { Accept: 'application/json', 'Content-Type': 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
      ...(request.body === undefined ? {} : { body: request.body }),
      ...(request.signal === undefined ? {} : { signal: request.signal }),
    });
    const text = await response.text();
    return { kind: 'response', status: response.status, text };
  } catch (error: unknown) {
    return {
      kind: 'failed',
      reason: request.signal?.aborted === true ? 'aborted' : 'network',
      detail: describe(error),
    };
  }
};

/** Binary GET: the transport owner returns bounded-reader inputs without reading the body. */
export async function fetchStreamTransport(path: ApiPath, signal: AbortSignal) {
  const response = await fetch(path, {
    method: 'GET',
    signal,
    cache: 'no-store',
    credentials: 'same-origin',
  });
  if (!response.ok || response.body === null) {
    await response.body?.cancel();
    throw new Error('Preview stream unavailable');
  }
  return { headers: response.headers, reader: response.body.getReader() };
}
