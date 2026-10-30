import { describe, expect, it } from 'vitest';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';
import {
  runtimeStateAnswerSchema,
  runtimeDiagnosticsAnswerSchema,
} from '../../../src/contract/runtime.ts';
import { createRuntimeReader } from '../../../src/api/runtime.ts';
import {
  capturedAge,
  diagnosticExport,
  diagnosticsBody,
  diskState,
  draftFromQuery,
  localSince,
  queryFromHash,
  queryProblem,
  RUNTIME_FILTERS,
  runtimeBody,
  runtimeHash,
  sinceEpoch,
  validateFilters,
  workerAlive,
} from '../../../src/core/runtime.ts';
import { own } from '../../../src/core/read.ts';

for (const family of ['runtime_state', 'runtime_diagnostics']) {
  describe(`${family === 'runtime_state' ? 'T3' : 'T4 T5 T6'} recorded ${family} contract`, () => {
    it.each(dashboardPagesCaseNames(family))(
      'reads the recorded case %s through the generated boundary',
      async (name) => {
        const found = dashboardPagesCase(`${family}.${name}`);
        const schema =
          family === 'runtime_state' ? runtimeStateAnswerSchema : runtimeDiagnosticsAnswerSchema;
        expect(schema.safeParse({ status: found.status, body: found.body }).success).toBe(true);
        const reader = createRuntimeReader(() =>
          Promise.resolve({
            kind: 'response',
            status: found.status,
            text: JSON.stringify(found.body),
          }),
        );
        const signal = new AbortController().signal;
        const result =
          family === 'runtime_state'
            ? await reader.identity(signal)
            : await reader.events(
                Object.fromEntries(new URL(found.request.path, 'http://fixture').searchParams),
                signal,
              );
        expect(result.kind).toBe(found.status === 200 ? 'answered' : 'failed');
        expect(result.answer?.body).toEqual(found.body);
        expect(
          family === 'runtime_state' ? runtimeBody(found.body) : diagnosticsBody(found.body),
        ).toBe(found.status === 200);
      },
    );
  });
}
it.each(['', '0', '1001', '1.5', 'NaN', 'Infinity', '-1', ' 2 '])(
  'T4 SR-5 invalid limit %j stays raw and is never clamped',
  (limit) => {
    const draft = { ...draftFromQuery({}), limit };
    expect(validateFilters(draft)).toEqual({ query: null, problem: 'limit' });
    expect(draft.limit).toBe(limit);
  },
);
it.each(['1', '1000'])('T4 inclusive limit %s is accepted', (limit) => {
  expect(validateFilters({ ...draftFromQuery({}), limit }).query?.['limit']).toBe(limit);
});
it('T4 every exact filter round trips the hash and local Since becomes epoch seconds', () => {
  const exact = Object.fromEntries(RUNTIME_FILTERS.map((key) => [key, `${key} / +& value`]));
  const since = '2027-01-15T12:00:00.500';
  const query = validateFilters({ exact, since, limit: '1', failures: true }).query;
  expect(query).toMatchObject({
    ...exact,
    since: String(new Date(since).getTime() / 1000),
    limit: '1',
    failures: '1',
  });
  if (query === null) throw new Error('query missing');
  expect(queryFromHash(runtimeHash(query))).toEqual(query);
  expect(draftFromQuery(query).since).toBe(since);
  expect(localSince('1800000002.5')).toContain('.500');
  expect(sinceEpoch('2027-02-30T12:00')).toBeNull();
  expect(queryProblem({ since: 'inf' })).toBe('since');
  expect(queryProblem({ colour: 'red' })).toBe('hash');
  expect(queryProblem(queryFromHash('#runtime?kind=%invalid'))).toBe('hash');
  expect(queryFromHash('#runtime?kind=a+b%2Bc%3Fd')).toEqual({ kind: 'a b+c?d' });
});
it('T3 source evidence and liveness distinguish missing, unreadable, changed and unverified', () => {
  const server = (name: string) => own(dashboardPagesCase(`runtime_state.${name}`).body, 'server');
  expect(diskState(server('idle_server_build_matches_the_source_on_disk'))).toBe('matching');
  expect(diskState(server('server_started_before_the_source_changed_restart_needed'))).toBe(
    'changed',
  );
  expect(diskState(server('disk_source_cannot_be_read_so_freshness_is_unknown'))).toBe(
    'unreadable',
  );
  expect(diskState(null)).toBe('unknown');
  expect(diskState(server('no_snapshot_could_be_taken_at_start_nothing_to_compare'))).toBe(
    'unknown',
  );
  expect(capturedAge(null, 0)).toBeNull();
  expect(workerAlive(null)).toBe('unknown');
});
it('T6 exports the retained raw result and its applied filters without narrowing extra data', () => {
  const found = dashboardPagesCase(
    'runtime_diagnostics.every_event_oldest_first_with_log_coordinates',
  );
  const filters = { operation_id: 'op-1' };
  expect(JSON.parse(diagnosticExport(found.body, filters, 1800000000000))).toEqual({
    exported_at: new Date(1800000000000).toISOString(),
    filters,
    coverage: own(found.body, 'coverage'),
    writer_health: own(found.body, 'writer_health'),
    events: own(found.body, 'events'),
  });
});
