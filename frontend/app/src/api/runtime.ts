import { runtimeStateAnswerSchema, runtimeDiagnosticsAnswerSchema } from '../contract/runtime.ts';
import type { DiagnosticQuery } from '../core/runtime.ts';
import { readEndpoint, type ReadOutcome } from './client.ts';
import { fetchTransport, type Transport } from './transport.ts';
import { bodyMembers, type Endpoint } from './wire.ts';

export interface RuntimeReader {
  readonly identity: (signal: AbortSignal) => Promise<ReadOutcome>;
  readonly events: (query: DiagnosticQuery, signal: AbortSignal) => Promise<ReadOutcome>;
}
const identity: Endpoint = {
  id: 'status',
  method: 'GET',
  strict: [runtimeStateAnswerSchema],
  members: bodyMembers([runtimeStateAnswerSchema]),
};
const events: Endpoint = {
  id: 'status',
  method: 'GET',
  strict: [runtimeDiagnosticsAnswerSchema],
  members: bodyMembers([runtimeDiagnosticsAnswerSchema]),
};

/** Page-local GETs; endpoint ids describe the existing read parser, never the request path. */
export function createRuntimeReader(transport: Transport = fetchTransport): RuntimeReader {
  return {
    identity: (signal) => readEndpoint(transport, identity, '/api/runtime', signal),
    events: (query, signal) => {
      const search = new URLSearchParams(query).toString();
      return readEndpoint(
        transport,
        events,
        `/api/diagnostics/events${search === '' ? '' : `?${search}`}`,
        signal,
      );
    },
  };
}
