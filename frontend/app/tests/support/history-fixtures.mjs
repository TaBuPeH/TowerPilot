// @ts-check
import { loadHistoryFixture } from '../../scripts/history-fixture.mjs';
import { record } from '../../scripts/generate-contract.mjs';

const cases = loadHistoryFixture();

/** A recorded landed r17b case `<family>.<case>` (backend/tests/fixtures/dashboard_contract_pages_v1.json), a fresh copy.
 * @param {string} path */
export function actionsCase(path) {
  const [family = '', name = ''] = path.split('.');
  const row = record(record(cases[family])[name]);
  if (typeof row['status'] !== 'number') throw new Error(`Unknown landed r17b case ${path}`);
  return { status: row['status'], body: structuredClone(record(row['body'])) };
}

/** The executions rows of a recorded case, in order.
 * @param {string} path @returns {Record<string, unknown>[]} */
function rowsOfCase(path) {
  const executions = actionsCase(path).body['executions'];
  const rows = Array.isArray(executions) ? executions : record(executions)['items'];
  return Array.isArray(rows) ? rows.map((row) => record(row)) : [];
}

/**
 * MODIFIED recorded case, for the mixed-state screens only: the started battle's detail with rows
 * of other recordings joined into its `executions.items` (a Run now row, a failed row with an
 * unconfirmed effect, an open row, a refresher-settled row, a harvest_launch row, and the second
 * started row with its label null). No recorded battle detail holds these states together, and no
 * recording has a null label. Each row is a recorded row; only the joining (sorted newest first by
 * started_at, as the server orders them), the null label and the open row's id are ours.
 */
function mixedDetail() {
  const detail = actionsCase(
    'action_executions.battle_detail_of_a_started_battle_lists_its_executions',
  );
  const failed = rowsOfCase(
    'action_executions.read_a_bare_start_time_returns_strictly_older_rows',
  ).find((row) => row['state'] === 'failed');
  const started = rowsOfCase(
    'action_executions.battle_detail_of_a_started_battle_lists_its_executions',
  );
  /** @type {Record<string, unknown>[]} */
  const items = [
    ...rowsOfCase('action_executions_more.battle_detail_lists_the_run_now_execution'),
    ...(failed === undefined ? [] : [failed]),
    // The open row and the settled row are one execution recorded twice, so the open copy gets its own id.
    ...rowsOfCase('action_executions_more.read_an_open_row_before_the_refresher_settles_it').map(
      (row) => ({
        ...row,
        id: 'open-copy-of-the-settled-row',
      }),
    ),
    ...rowsOfCase('action_executions_more.battle_detail_after_the_refresher_settled_an_open_row'),
    ...rowsOfCase('action_executions_more.read_harvest_launch_accepted'),
    ...started.slice(0, 1),
    ...started.slice(1).map((row) => ({ ...row, label: null })),
  ];
  // Newest first, as the server orders a battle's executions (a later start before an earlier one).
  items.sort((a, b) => Number(b['started_at']) - Number(a['started_at']));
  detail.body['executions'] = { items, reason: null, truncated: false };
  return detail;
}

/** The detail answer behind `/__fake/history?case=actions:<name>`: a recording, or the mixed screen.
 * @param {string} name */
function actionsDetail(name) {
  return name === 'mixed' ? mixedDetail() : actionsCase(name);
}

/** The recorded continuation of the truncated battle, for exactly the request that recorded it.
 * @param {URL} url */
function actionsOlder(url) {
  const recorded = actionsCase(
    'action_executions_more.battle_detail_truncated_continued_through_the_generic_route',
  );
  const recordedRequest = record(
    record(
      record(cases['action_executions_more'])[
        'battle_detail_truncated_continued_through_the_generic_route'
      ],
    )['request'],
  );
  const request = new URL(`http://recorded.invalid${String(recordedRequest['path'])}`);
  for (const key of ['battle_id', 'before'])
    if (url.searchParams.get(key) !== request.searchParams.get(key))
      return { status: 404, body: { ok: false, error: 'No recording for this executions query' } };
  return recorded;
}

/** Every returned answer originates in the backend-generated pages fixture.
 * @param {'history_list' | 'history_detail'} family @param {string} name
 */
export function historyCase(family, name) {
  const row = record(record(cases[family])[name]);
  if (typeof row['status'] !== 'number')
    throw new Error(`Unknown history fixture ${family}.${name}`);
  return { status: row['status'], body: structuredClone(record(row['body'])) };
}

/** Unique real-route records, with evidence variants first, for the 20+1 paging browser flow. */
export const HISTORY_DETAIL_NAMES = [
  'collection_sampled_confirmations_ended',
  'setup_verified',
  'setup_degraded_module_not_equipped',
  'stats_capture_unverified_wrong_tier',
  'stats_capture_verified',
  'active_adopted_partial_observation',
  'interrupted_before_any_observation_everything_unknown',
  'ended_after_an_observation_gap',
  'prepared_nothing_observed',
  ...Object.keys(record(cases['history_detail'])),
].filter((name, index, all) => all.indexOf(name) === index);

const seen = new Set();
export const HISTORY_RECORDS = HISTORY_DETAIL_NAMES.flatMap((name) => {
  const answer = historyCase('history_detail', name);
  const battle = record(answer.body['battle'] ?? {});
  const id = battle['id'];
  if (
    answer.status !== 200 ||
    typeof id !== 'string' ||
    seen.has(id) ||
    record(answer.body['context'])['account'] !== null
  )
    return [];
  seen.add(id);
  return [{ name, battle }];
});

/** Paging/filtering slices actual generated records; no invented battle or evidence fields.
 * @param {URL} url @param {string | undefined} selected
 */
export function historyAnswer(url, selected) {
  if (selected?.startsWith('actions:')) {
    const detail = actionsDetail(selected.slice('actions:'.length));
    if (url.pathname === '/api/actions/executions') return actionsOlder(url);
    if (url.pathname !== '/api/battles') return detail;
    const template = historyCase('history_list', 'first_page');
    template.body['battles'] = [detail.body['battle']];
    return template;
  }
  if (url.pathname === '/api/battles') {
    if (selected?.startsWith('list:')) return historyCase('history_list', selected.slice(5));
    const template = historyCase('history_list', 'first_page');
    const status = url.searchParams.get('status');
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? 21);
    template.body['battles'] = HISTORY_RECORDS.map((row) => row.battle)
      .filter((battle) => !status || battle['status'] === status)
      .slice(offset, offset + limit);
    return template;
  }
  if (selected !== undefined && !selected.startsWith('list:'))
    return historyCase('history_detail', selected);
  const id = decodeURIComponent(url.pathname.slice('/api/battles/'.length));
  const row = HISTORY_RECORDS.find((row) => row.battle['id'] === id);
  return historyCase('history_detail', row?.name ?? 'unknown_record_404');
}
