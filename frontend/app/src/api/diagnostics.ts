import { postEndpoint } from './client.ts';
import type { Transport } from './transport.ts';
import { ENDPOINTS, type Endpoint, type EndpointId } from './wire.ts';

/**
 * Client diagnostics (C-20, F-107, M-20): the ONE automatic POST of the app.
 * `POST /api/diagnostics/client` is sent at most once per distinct fault per
 * page session, never retried, never counted as a person's write, and never
 * through the write-intent machine; its answer is not tracked. A page session
 * also sends at most `MAX_REPORTS` of them in total. A diagnostic changes no
 * game, harvest or input state.
 */
export type DiagnosticKind = 'api_error' | 'javascript_error' | 'unhandled_rejection';

export interface DiagnosticEvent {
  readonly kind: DiagnosticKind;
  readonly page: string;
  readonly message: string;
  readonly path?: string;
  readonly method?: string;
  readonly stack?: string;
}

export const MAX_REPORTS = 20;

const LIMITS = { message: 2000, stack: 8000, path: 500, page: 100, method: 20 } as const;

export interface Diagnostics {
  /** Send `event` unless `fault` was already reported in this page session; true when it was sent. */
  readonly report: (fault: string, event: DiagnosticEvent) => boolean;
  /** How many diagnostics this page session sent. */
  readonly sent: () => number;
}

export function createDiagnostics(
  transport: Transport,
  endpoints: Readonly<Record<EndpointId, Endpoint>> = ENDPOINTS,
): Diagnostics {
  const reported = new Set<string>();
  let sent = 0;
  return {
    report: (fault, event) => {
      if (reported.has(fault) || sent >= MAX_REPORTS) return false;
      reported.add(fault);
      sent += 1;
      const body = {
        kind: event.kind,
        page: event.page.slice(0, LIMITS.page),
        message: event.message.slice(0, LIMITS.message),
        ...(event.path === undefined ? {} : { path: event.path.slice(0, LIMITS.path) }),
        ...(event.method === undefined ? {} : { method: event.method.slice(0, LIMITS.method) }),
        ...(event.stack === undefined ? {} : { stack: event.stack.slice(0, LIMITS.stack) }),
      };
      // Fire and forget: the answer is not tracked and nothing is ever resent (C-20).
      void postEndpoint(
        transport,
        endpoints.diagnostics,
        '/api/diagnostics/client',
        JSON.stringify(body),
      );
      return true;
    },
    sent: () => sent,
  };
}
