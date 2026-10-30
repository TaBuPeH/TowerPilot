import { finite, id, list, own, record } from './read.ts';

/** Runtime owns read evidence and diagnostic queries. See features/runtime/runtime.rules.md. */
export const RUNTIME_FILTERS = [
  'instance',
  'operation_id',
  'battle_id',
  'command_id',
  'trace_id',
  'request_id',
  'attempt_id',
  'window_id',
  'offer_id',
  'kind',
] as const;
export type RuntimeFilter = (typeof RUNTIME_FILTERS)[number];
export const RUNTIME_FIELDS = {
  server: [
    'role',
    'pid',
    'session_id',
    'captured_at',
    'source_revision',
    'source_fingerprint',
    'disk_source_fingerprint',
    'source_changed',
    'error',
  ],
  runtime: ['port', 'api_contract', 'capabilities', 'persistence_operations'],
  process: ['state', 'age_sec', 'last_attempt_at', 'last_success_at', 'error'],
  worker: [
    'operation_id',
    'kind',
    'status',
    'instance',
    'pid',
    'creation_time',
    'source_revision',
    'source_fingerprint',
    'captured_at',
  ],
  coverage: [
    'files_scanned',
    'bytes_read',
    'matched_in_scan',
    'returned',
    'truncated',
    'malformed_records',
    'errors',
    'max_files',
    'max_bytes',
  ],
  writer: ['pid', 'process_id', 'write_errors', 'last_error'],
} as const;
export type DiagnosticQuery = Readonly<Record<string, string>>;
export interface FilterDraft {
  readonly exact: Readonly<Record<string, string>>;
  readonly since: string;
  readonly limit: string;
  readonly failures: boolean;
}
export type FilterProblem = 'limit' | 'since' | 'exact' | 'hash';
export type FilterValidation =
  | { readonly query: DiagnosticQuery; readonly problem: null }
  | { readonly query: null; readonly problem: FilterProblem };

export function localSince(epoch: string): string {
  const seconds = Number(epoch);
  if (epoch.trim() === '' || !Number.isFinite(seconds) || seconds < 0) return epoch;
  const date = new Date(seconds * 1000);
  if (!Number.isFinite(date.getTime())) return epoch;
  const pad = (n: number, width = 2) => String(n).padStart(width, '0');
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

/** Browser-local wall time becomes epoch seconds; impossible dates and DST gaps are refused. */
export function sinceEpoch(raw: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/.test(raw)) return null;
  const date = new Date(raw);
  const value = date.getTime() / 1000;
  if (!Number.isFinite(value) || value < 0) return null;
  return localSince(String(value)).slice(0, raw.length) === raw ? value : null;
}

export function draftFromQuery(query: DiagnosticQuery): FilterDraft {
  return {
    exact: Object.fromEntries(RUNTIME_FILTERS.map((key) => [key, query[key] ?? ''])),
    since: query['since'] === undefined ? '' : localSince(query['since']),
    limit: query['limit'] ?? '200',
    failures: query['failures'] === '1',
  };
}

export function validateFilters(draft: FilterDraft): FilterValidation {
  const limit = Number(draft.limit);
  if (!/^\d+$/.test(draft.limit) || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000)
    return { query: null, problem: 'limit' };
  const query: Record<string, string> = {};
  if (limit !== 200) query['limit'] = String(limit);
  if (draft.failures) query['failures'] = '1';
  for (const key of RUNTIME_FILTERS) {
    const value = draft.exact[key] ?? '';
    if (value.length > 200) return { query: null, problem: 'exact' };
    if (value !== '') query[key] = value;
  }
  if (draft.since !== '') {
    const since = sinceEpoch(draft.since);
    if (since === null) return { query: null, problem: 'since' };
    query['since'] = String(since);
  }
  return { query, problem: null };
}

export function runtimeHash(query: DiagnosticQuery): string {
  const encode = (value: string) => encodeURIComponent(value).replace(/%20/g, '+');
  const search = Object.entries(query)
    .map(([key, value]) => `${encode(key)}=${encode(value)}`)
    .join('&');
  return `#runtime${search === '' ? '' : `?${search}`}`;
}
export { queryFromHash } from './hashQuery.ts';
export function queryProblem(query: DiagnosticQuery): FilterProblem | null {
  const supported: readonly string[] = [...RUNTIME_FILTERS, 'since', 'limit', 'failures'];
  if (
    Object.keys(query).some((key) => !supported.includes(key)) ||
    (query['failures'] !== undefined && query['failures'] !== '0' && query['failures'] !== '1')
  )
    return 'hash';
  if (
    query['since'] !== undefined &&
    (query['since'].trim() === '' ||
      !Number.isFinite(Number(query['since'])) ||
      Number(query['since']) < 0)
  )
    return 'since';
  return validateFilters(draftFromQuery(query)).problem;
}

export function runtimeBody(body: unknown): boolean {
  return record(own(body, 'server')) !== undefined && list(own(body, 'workers')) !== undefined;
}
export function diagnosticsBody(body: unknown): boolean {
  return list(own(body, 'events')) !== undefined && record(own(body, 'coverage')) !== undefined;
}
export function diskState(server: unknown): 'matching' | 'changed' | 'unknown' | 'unreadable' {
  if (id(own(server, 'source_fingerprint')) === undefined) return 'unknown';
  if (own(server, 'source_changed') === true) return 'changed';
  if (own(server, 'error') !== null && own(server, 'error') !== undefined) return 'unreadable';
  if (
    own(server, 'source_changed') === false &&
    id(own(server, 'source_fingerprint')) !== undefined &&
    id(own(server, 'disk_source_fingerprint')) !== undefined
  )
    return 'matching';
  return 'unknown';
}
export function capturedAge(server: unknown, nowMs: number): number | null {
  const captured = finite(own(server, 'captured_at'));
  return captured === undefined ? null : Math.max(0, Math.floor(nowMs / 1000 - captured));
}
export function workerAlive(worker: unknown): 'alive' | 'gone' | 'unknown' {
  return own(worker, 'alive') === true
    ? 'alive'
    : own(worker, 'alive') === false
      ? 'gone'
      : 'unknown';
}
export function eventKey(event: unknown, index: number): string {
  return (
    id(own(event, 'event_id')) ?? JSON.stringify([own(event, '_log'), own(event, 'seq'), index])
  );
}
export function diagnosticExport(body: unknown, filters: DiagnosticQuery, nowMs: number): string {
  return JSON.stringify(
    {
      exported_at: new Date(nowMs).toISOString(),
      filters,
      coverage: own(body, 'coverage'),
      writer_health: own(body, 'writer_health') ?? null,
      events: own(body, 'events'),
    },
    null,
    2,
  );
}
