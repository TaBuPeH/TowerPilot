import { expect, it } from 'vitest';
import { createHistoryStore } from '../../../src/store/history.ts';
import { selectHistory } from '../../../src/store/selectors/history.ts';
import { historyCase, historyTarget, historyTransport } from '../../support/historyFixture.ts';
import { objectOf, r17bBody, rowsOf } from '../../support/historyExecutions.ts';

/**
 * A2-7 AC4: the older-executions read of a selected battle. Bodies are the landed r17b
 * recordings (backend/tests/fixtures/dashboard_contract_pages_v1.json); a modified body says why beside the change.
 */
const TRUNCATED = 'action_executions_more.battle_detail_truncated_at_fifty';
const CONTINUED =
  'action_executions_more.battle_detail_truncated_continued_through_the_generic_route';
const BATTLE = '0000019e-0000-4000-8000-000000000000';
const CURSOR = '1800000110.0:00000000000000000000000000000065';
const flush = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};

async function opened(name: string = TRUNCATED) {
  const api = historyTransport();
  const store = createHistoryStore(api.reader, () => 5000);
  const detail = r17bBody(name);
  store.setTarget(historyTarget);
  api.body(0, { ...historyCase('history_list', 'first_page').body, battles: [detail['battle']] });
  await flush();
  store.select(String(objectOf(detail['battle'])['id']));
  api.body(1, detail);
  await flush();
  return { api, store, detail };
}
const executions = (store: ReturnType<typeof createHistoryStore>) =>
  selectHistory(store.getSnapshot()).detail?.executions;

it('AC4 a truncated battle offers older executions and reads the recorded generic route once', async () => {
  const { api, store } = await opened();
  expect(executions(store)).toMatchObject({ state: 'rows', truncated: true, canLoadOlder: true });
  store.loadOlder();
  store.loadOlder();
  expect(api.pending).toHaveLength(3);
  expect(api.pending[2]?.request).toMatchObject({ method: 'GET' });
  // The recorded request is battle_id and before; instance names the emulator the detail came from.
  expect(api.pending[2]?.request.path).toBe(
    `/api/actions/executions?battle_id=${BATTLE}&before=${encodeURIComponent(CURSOR)}&instance=main`,
  );
  expect(executions(store)?.older.busy).toBe(true);
  api.body(2, r17bBody(CONTINUED));
  await flush();
  const view = executions(store);
  expect(view).toMatchObject({ state: 'rows', truncated: false, canLoadOlder: false });
  expect(view?.rows).toHaveLength(51);
  store.loadOlder();
  expect(api.pending).toHaveLength(3);
});

it('AC4 a failed older read keeps every row, says why and retries the same cursor', async () => {
  const { api, store } = await opened();
  store.loadOlder();
  api.fail(2);
  await flush();
  expect(executions(store)).toMatchObject({
    canLoadOlder: true,
    older: { busy: false, error: 'network' },
  });
  expect(executions(store)?.rows).toHaveLength(50);
  store.loadOlder();
  expect(api.pending[3]?.request.path).toBe(api.pending[2]?.request.path);
  api.body(3, r17bBody(CONTINUED));
  await flush();
  expect(executions(store)).toMatchObject({ older: { error: null }, canLoadOlder: false });
});

it('AC4 an older page for another battle or emulator is discarded and named', async () => {
  const { api, store } = await opened();
  store.loadOlder();
  // Modified recorded case: the continuation with the context of another emulator.
  api.body(2, {
    ...r17bBody(CONTINUED),
    context: { ...objectOf(r17bBody(CONTINUED)['context']), instance: 'other' },
  });
  await flush();
  expect(executions(store)).toMatchObject({ older: { error: 'wrong_context' } });
  expect(executions(store)?.rows).toHaveLength(50);
  store.loadOlder();
  // Modified recorded case: the continuation of another battle (every row's host names it).
  const other = r17bBody(CONTINUED);
  api.body(3, {
    ...other,
    executions: rowsOf(other['executions']).map((row) => ({
      ...row,
      host: { ...objectOf(row['host']), battle_id: 'another-battle' },
    })),
  });
  await flush();
  expect(executions(store)).toMatchObject({ older: { error: 'wrong_context' } });
  expect(executions(store)?.rows).toHaveLength(50);
});

it('AC4 a refreshed, re-selected or retargeted battle drops the older rows and the read in flight', async () => {
  const { api, store, detail } = await opened();
  store.loadOlder();
  api.body(2, r17bBody(CONTINUED));
  await flush();
  expect(executions(store)?.rows).toHaveLength(51);
  store.refreshDetail();
  api.body(3, detail);
  await flush();
  expect(executions(store)).toMatchObject({ truncated: true, canLoadOlder: true });
  expect(executions(store)?.rows).toHaveLength(50);
  store.loadOlder();
  const read = api.pending[4];
  store.setTarget({ ...historyTarget, serial: 'fake:2', epoch: 2 });
  expect(read?.request.signal?.aborted).toBe(true);
  expect(selectHistory(store.getSnapshot()).detail).toBeNull();
  store.loadOlder();
  expect(api.pending).toHaveLength(6);
});

it('AC4 a battle with no cursor, or none selected, sends no older read', async () => {
  const none = historyTransport();
  const empty = createHistoryStore(none.reader);
  empty.setTarget(historyTarget);
  empty.loadOlder();
  expect(none.pending).toHaveLength(1);
  const { api, store } = await opened(
    'action_executions.battle_detail_of_a_started_battle_lists_its_executions',
  );
  store.loadOlder();
  expect(api.pending).toHaveLength(2);
  expect(executions(store)).toMatchObject({ truncated: false, canLoadOlder: false });
});

/** Treat the recorded page's rows as rows of BATTLE, keeping the page's own bookkeeping. */
function pageOfBattle(name: string): Record<string, unknown> {
  const page = r17bBody(name);
  return {
    ...page,
    executions: rowsOf(page['executions']).map((row) => ({
      ...row,
      host: { ...objectOf(row['host']), battle_id: BATTLE },
    })),
  };
}

it('AC4 two older pages chain through each next_before until the last page has none', async () => {
  // No recording holds a two-page chain of one battle: the recorded battle_detail_truncated_continued
  // page is the only battle-scoped page, and it is the last. So the chain is composed from the two
  // recorded consecutive pages of the generic route (read_second_page_follows_the_cursor, whose
  // next_before is the before of read_last_page_has_no_cursor) with every row's host.battle_id set
  // to this battle; ids, order, cursors and counts are the recorded ones.
  const first = pageOfBattle('action_executions.read_second_page_follows_the_cursor');
  const last = pageOfBattle('action_executions.read_last_page_has_no_cursor');
  const { api, store } = await opened();
  store.loadOlder();
  api.body(2, first);
  await flush();
  expect(executions(store)).toMatchObject({ truncated: true, canLoadOlder: true });
  expect(executions(store)?.rows).toHaveLength(53);
  store.loadOlder();
  expect(api.pending[3]?.request.path).toBe(
    `/api/actions/executions?battle_id=${BATTLE}&before=${encodeURIComponent(String(first['next_before']))}&instance=main`,
  );
  api.body(3, last);
  await flush();
  expect(executions(store)).toMatchObject({ truncated: false, canLoadOlder: false });
  const rows = executions(store)?.rows ?? [];
  expect(rows).toHaveLength(55);
  expect(new Set(rows.map((row) => row.key)).size).toBe(55);
  store.loadOlder();
  expect(api.pending).toHaveLength(4);
});

it('AC4 a refused older page keeps its reason code in the view', async () => {
  const { api, store } = await opened();
  store.loadOlder();
  // Modified recorded case: the recorded continuation answered as a newer ledger answers it.
  api.body(2, { ...r17bBody(CONTINUED), executions: [], reason: 'newer_schema' });
  await flush();
  expect(executions(store)).toMatchObject({
    canLoadOlder: true,
    older: { busy: false, error: 'unavailable', reason: 'newer_schema' },
  });
  expect(executions(store)?.rows).toHaveLength(50);
  store.loadOlder();
  api.body(3, r17bBody(CONTINUED));
  await flush();
  expect(executions(store)).toMatchObject({ older: { error: null, reason: null } });
});
