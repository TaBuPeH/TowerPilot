import { expect, it } from 'vitest';
import { createHistoryStore } from '../../../src/store/history.ts';
import { own } from '../../../src/core/read.ts';
import { selectHistory } from '../../../src/store/selectors/history.ts';
import {
  historyCase,
  historyTarget,
  historyTransport,
  HISTORY_RECORDS,
} from '../../support/historyFixture.ts';

const flush = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};
const pageBody = (count: number) => ({
  ...historyCase('history_list', 'first_page').body,
  battles: HISTORY_RECORDS.slice(0, count).map((row) => row.battle),
});

it('H1: 21/20/final/empty pages, single-flight refresh, retained failure and filter offset reset', async () => {
  const api = historyTransport();
  const store = createHistoryStore(api.reader, () => 1234);
  store.setTarget(historyTarget);
  store.refreshList();
  store.refreshList();
  expect(api.pending).toHaveLength(1);
  expect(api.pending[0]?.request.path).toContain('limit=21&offset=0');
  api.body(0, pageBody(21));
  await flush();
  expect(selectHistory(store.getSnapshot()).rows).toHaveLength(20);
  store.page('next');
  expect(api.pending[1]?.request.path).toContain('offset=20');
  api.body(1, pageBody(20));
  await flush();
  expect(selectHistory(store.getSnapshot()).hasNext).toBe(false);
  store.refreshList();
  api.fail(2);
  await flush();
  expect(selectHistory(store.getSnapshot()).rows).toHaveLength(20);
  expect(store.getSnapshot().list).toMatchObject({ error: 'network', loadedAt: 1234 });
  store.filter('ended');
  expect(api.pending[3]?.request.path).toContain('offset=0&status=ended');
  api.answer(3, 'history_list', 'final_page_one_row');
  await flush();
  expect(selectHistory(store.getSnapshot()).rows).toHaveLength(1);
  store.filter('interrupted');
  api.answer(4, 'history_list', 'past_the_end_empty');
  await flush();
  expect(selectHistory(store.getSnapshot()).rows).toHaveLength(0);
  expect(api.pending.every((row) => row.request.method === 'GET')).toBe(true);
});

it('H2: selecting B drops delayed A, wrong context is rejected, and account switch clears all records', async () => {
  const api = historyTransport();
  const store = createHistoryStore(api.reader);
  store.setTarget(historyTarget);
  api.body(0, pageBody(2));
  await flush();
  const a = String(HISTORY_RECORDS[0]?.battle['id']);
  const b = String(HISTORY_RECORDS[1]?.battle['id']);
  store.select(a);
  store.select(b);
  expect(api.pending[1]?.request.signal?.aborted).toBe(true);
  api.answer(2, 'history_detail', HISTORY_RECORDS[1]?.name ?? 'setup_verified');
  await flush();
  api.answer(
    1,
    'history_detail',
    HISTORY_RECORDS[0]?.name ?? 'collection_sampled_confirmations_ended',
  );
  await flush();
  expect(own(store.getSnapshot().detail.body, 'battle')).toEqual(
    historyCase('history_detail', 'setup_verified').body['battle'],
  );
  store.refreshDetail();
  api.answer(3, 'history_detail', 'detail_of_this_accounts_record');
  await flush();
  expect(store.getSnapshot().detail.error).toBe('wrong_context');
  store.setTarget({ ...historyTarget, account: 'alpha', epoch: 2 });
  expect(selectHistory(store.getSnapshot())).toMatchObject({
    rows: [],
    detail: null,
    selected: null,
  });
  store.dispose();
});

it('H4: independent selected-ID retry preserves detail and last-loaded time through network/404 failures', async () => {
  const api = historyTransport();
  const store = createHistoryStore(api.reader, () => 5678);
  store.setTarget(historyTarget);
  api.body(0, pageBody(1));
  await flush();
  store.select(String(HISTORY_RECORDS[0]?.battle['id']));
  api.answer(1, 'history_detail', 'collection_sampled_confirmations_ended');
  await flush();
  const before = store.getSnapshot().detail.body;
  store.refreshDetail();
  store.refreshDetail();
  expect(api.pending).toHaveLength(3);
  api.fail(2);
  await flush();
  expect(store.getSnapshot().detail.body).toBe(before);
  store.refreshDetail();
  expect(api.pending[3]?.request.path).toBe(api.pending[1]?.request.path);
  api.answer(3, 'history_detail', 'detail_of_the_other_accounts_record_is_a_404');
  await flush();
  expect(store.getSnapshot().detail).toMatchObject({
    body: before,
    loadedAt: 5678,
    error: 'not_found',
  });
  store.dispose();
});

it('H4: missing or malformed activity rejects the refresh and retains the previously loaded event evidence', async () => {
  const api = historyTransport();
  const store = createHistoryStore(api.reader);
  store.setTarget(historyTarget);
  api.body(0, pageBody(1));
  await flush();
  store.select(String(HISTORY_RECORDS[0]?.battle['id']));
  const detail = historyCase('history_detail', 'collection_sampled_confirmations_ended');
  api.answer(1, 'history_detail', 'collection_sampled_confirmations_ended');
  await flush();
  const kept = store.getSnapshot().detail.body;
  for (const events of [undefined, null, 'unreadable', {}]) {
    store.refreshDetail();
    api.body(api.pending.length - 1, { ...detail.body, events });
    await flush();
    expect(store.getSnapshot().detail).toMatchObject({
      body: kept,
      busy: false,
      error: 'unavailable',
    });
  }
  store.dispose();
});
