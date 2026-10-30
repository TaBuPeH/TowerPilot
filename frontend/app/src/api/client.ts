import type { z } from 'zod';
import { fetchTransport, type ApiPath, type Transport } from './transport.ts';
import { parseAnswer, type Endpoint, type WireAnswer } from './wire.ts';

/**
 * The typed client: the one place the app talks to the dashboard server (lint
 * allows network names only inside `api/`, and `fetchTransport` is the only
 * caller of the browser's network API). Every answer is parsed at the boundary
 * by `api/wire.ts` against the generated contract schemas.
 *
 * Reads are GETs; writes are POSTs sent only by the write-intent machine
 * (`api/intents/machine.ts`, never retried) and the client diagnostics of C-20
 * (`api/diagnostics.ts`).
 */

export type { ApiPath };

/** A read: answered (HTTP 2xx with a JSON object body), or failed with its reason. */
export type ReadOutcome =
  | { readonly kind: 'answered'; readonly answer: WireAnswer }
  | {
      readonly kind: 'failed';
      /** `aborted`, `network`, `http_<status>`, `body_not_json` or `body_not_object`. */
      readonly reason: string;
      readonly answer: WireAnswer | null;
    };

/** GET `path` of `endpoint` through `transport`; never throws, never retries. */
export async function readEndpoint(
  transport: Transport,
  endpoint: Endpoint,
  path: ApiPath,
  signal?: AbortSignal,
): Promise<ReadOutcome> {
  const result = await transport({
    method: 'GET',
    path,
    ...(signal === undefined ? {} : { signal }),
  });
  if (result.kind === 'failed') {
    return { kind: 'failed', reason: result.reason, answer: null };
  }
  const answer = parseAnswer(endpoint, result.status, result.text);
  if (result.status < 200 || result.status > 299) {
    return { kind: 'failed', reason: `http_${result.status.toString()}`, answer };
  }
  if (!answer.json) {
    return { kind: 'failed', reason: 'body_not_json', answer };
  }
  if (answer.body === undefined) {
    return { kind: 'failed', reason: 'body_not_object', answer };
  }
  return { kind: 'answered', answer };
}

/** A write's answer, or why there is none (the outcome is then unknown). */
export type WriteOutcome =
  | { readonly kind: 'answered'; readonly answer: WireAnswer }
  | { readonly kind: 'lost'; readonly reason: 'aborted' | 'network' };

/** POST the frozen `body` text to `path`; never throws, never retries. */
export async function postEndpoint(
  transport: Transport,
  endpoint: Endpoint,
  path: ApiPath,
  body: string,
): Promise<WriteOutcome> {
  const result = await transport({ method: 'POST', path, body });
  if (result.kind === 'failed') {
    return { kind: 'lost', reason: result.reason };
  }
  return { kind: 'answered', answer: parseAnswer(endpoint, result.status, result.text) };
}

export type ReadError =
  | { readonly kind: 'aborted' }
  | { readonly kind: 'network'; readonly detail: string }
  | { readonly kind: 'http'; readonly status: number }
  | { readonly kind: 'contract'; readonly issues: readonly string[] };

export type ReadResult<T> =
  { readonly ok: true; readonly data: T } | { readonly ok: false; readonly error: ReadError };

export interface ReadOptions {
  readonly signal?: AbortSignal;
  readonly transport?: Transport;
}

/**
 * GET `path` and validate the JSON body against `schema` (a one-off read
 * outside the Dashboard endpoints). Never throws: every failure is a typed
 * `ReadError`, and a body that does not match the schema is a `contract`
 * error, never data.
 */
export async function readJson<T>(
  path: ApiPath,
  schema: z.ZodType<T>,
  options: ReadOptions = {},
): Promise<ReadResult<T>> {
  const transport = options.transport ?? fetchTransport;
  const result = await transport({
    method: 'GET',
    path,
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
  if (result.kind === 'failed') {
    return result.reason === 'aborted'
      ? { ok: false, error: { kind: 'aborted' } }
      : { ok: false, error: { kind: 'network', detail: result.detail } };
  }
  if (result.status < 200 || result.status > 299) {
    return { ok: false, error: { kind: 'http', status: result.status } };
  }
  let body: unknown;
  try {
    body = JSON.parse(result.text);
  } catch (error: unknown) {
    const detail = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
    return { ok: false, error: { kind: 'contract', issues: [`body is not JSON: ${detail}`] } };
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    return {
      ok: false,
      error: {
        kind: 'contract',
        issues: parsed.error.issues.map(
          (issue) => `${issue.path.map(String).join('.')}: ${issue.message}`,
        ),
      },
    };
  }
  return { ok: true, data: parsed.data };
}
