import type { ReadOutcome } from '../api/client.ts';
import type { HistoryFilter, HistoryReader, HistoryTarget } from '../api/history.ts';
import { id, list, own, ownPath } from '../core/read.ts';
import { HISTORY_PAGE_SIZE, historyHasNext, historyRows } from '../core/history.ts';
import {
  NO_OLDER,
  joinOlder,
  nextCursor,
  olderPage,
  type OlderExecutions,
} from '../core/historyActions.ts';
import { createStore, type Store } from './store.ts';

export interface HistoryRead {
  readonly body: unknown;
  readonly loadedAt: number | null;
  readonly busy: boolean;
  readonly error: 'network' | 'unavailable' | 'not_found' | 'wrong_context' | null;
}
/** The older executions of the selected battle (A2-7 AC4), read through the generic route. */
export interface OlderRead extends OlderExecutions {
  readonly busy: boolean;
  readonly error: HistoryRead['error'];
  /** The reason code a refused page carried (a newer ledger answers with one), else null. */
  readonly reason: string | null;
}
export interface HistoryState {
  readonly target: HistoryTarget | null;
  readonly filter: HistoryFilter;
  readonly offset: number;
  readonly selected: string | null;
  readonly list: HistoryRead;
  readonly detail: HistoryRead;
  readonly older: OlderRead;
}
export interface HistoryStore extends Store<HistoryState> {
  readonly setTarget: (target: HistoryTarget | null) => void;
  readonly filter: (filter: HistoryFilter) => void;
  readonly page: (direction: 'previous' | 'next') => void;
  readonly select: (id: string) => void;
  readonly refreshList: () => void;
  readonly refreshDetail: () => void;
  /** Read the next older page of the selected battle's executions, when its last read named a cursor. */
  readonly loadOlder: () => void;
  readonly dispose: () => void;
}
const empty = (): HistoryRead => ({ body: undefined, loadedAt: null, busy: false, error: null });
const noOlder = (): OlderRead => ({ ...NO_OLDER, busy: false, error: null, reason: null });

function sameTarget(a: HistoryTarget | null, b: HistoryTarget | null): boolean {
  return a === null || b === null
    ? a === b
    : a.epoch === b.epoch &&
        a.instance === b.instance &&
        a.account === b.account &&
        a.serial === b.serial;
}

function matches(body: unknown, target: HistoryTarget): boolean {
  const context = own(body, 'context');
  return (
    own(context, 'instance') === target.instance &&
    own(context, 'account') === target.account &&
    own(context, 'serial') === target.serial
  );
}

/** H1/H2/H4: independent single-flight reads, sequence and target fences, same-query last-loaded data. */
export function createHistoryStore(
  reader: HistoryReader,
  now: () => number = Date.now,
): HistoryStore {
  const store = createStore<HistoryState>({
    target: null,
    filter: 'all',
    offset: 0,
    selected: null,
    list: empty(),
    detail: empty(),
    older: noOlder(),
  });
  let listRequest: AbortController | null = null;
  let detailRequest: AbortController | null = null;
  let olderRequest: AbortController | null = null;
  const invalidate = (slot: 'list' | 'detail' | 'older') => {
    if (slot === 'list') {
      listRequest?.abort();
      listRequest = null;
    } else if (slot === 'detail') {
      detailRequest?.abort();
      detailRequest = null;
    } else {
      olderRequest?.abort();
      olderRequest = null;
    }
  };
  const read = async (slot: 'list' | 'detail') => {
    const state = store.getSnapshot();
    const target = state.target;
    if (
      target === null ||
      (slot === 'detail' && state.selected === null) ||
      (slot === 'list' ? listRequest : detailRequest) !== null
    )
      return;
    const controller = new AbortController();
    if (slot === 'list') listRequest = controller;
    else detailRequest = controller;
    store.update((s) => ({ ...s, [slot]: { ...s[slot], busy: true, error: null } }));
    let outcome: ReadOutcome;
    try {
      outcome = await (slot === 'list'
        ? reader.list(target, state.filter, state.offset, controller.signal)
        : reader.detail(target, state.selected ?? '', controller.signal));
    } catch {
      outcome = { kind: 'failed', reason: 'network', answer: null };
    }
    if (
      (slot === 'list' ? listRequest : detailRequest) !== controller ||
      !sameTarget(store.getSnapshot().target, target)
    )
      return;
    if (slot === 'list') listRequest = null;
    else detailRequest = null;
    const body = outcome.kind === 'answered' ? outcome.answer.body : undefined;
    const correctContext = outcome.kind === 'answered' && matches(body, target);
    const valid =
      correctContext &&
      (slot === 'list'
        ? list(own(body, 'battles')) !== undefined
        : ownPath(body, 'battle', 'id') === state.selected &&
          list(own(body, 'events')) !== undefined);
    if (!valid) {
      const error: HistoryRead['error'] =
        outcome.kind === 'answered'
          ? correctContext
            ? 'unavailable'
            : 'wrong_context'
          : outcome.reason === 'network'
            ? 'network'
            : outcome.answer?.status === 404
              ? 'not_found'
              : 'unavailable';
      store.update((s) => ({ ...s, [slot]: { ...s[slot], busy: false, error } }));
      return;
    }
    // A new detail read replaces the executions block, so the pages read before it no longer follow it.
    if (slot === 'detail') invalidate('older');
    store.update((s) => ({
      ...s,
      [slot]: { body, loadedAt: now(), busy: false, error: null },
      ...(slot === 'detail' ? { older: noOlder() } : {}),
    }));
  };
  const loadOlder = async () => {
    const state = store.getSnapshot();
    const target = state.target;
    const battle = state.selected;
    const cursor = nextCursor(own(state.detail.body, 'executions'), state.older);
    if (
      target === null ||
      battle === null ||
      state.detail.loadedAt === null ||
      cursor === null ||
      olderRequest !== null
    )
      return;
    const controller = new AbortController();
    olderRequest = controller;
    store.update((s) => ({ ...s, older: { ...s.older, busy: true, error: null, reason: null } }));
    let outcome: ReadOutcome;
    try {
      outcome = await reader.executionsBefore(target, battle, cursor, controller.signal);
    } catch {
      outcome = { kind: 'failed', reason: 'network', answer: null };
    }
    if (olderRequest !== controller || !sameTarget(store.getSnapshot().target, target)) return;
    olderRequest = null;
    const body = outcome.kind === 'answered' ? outcome.answer.body : undefined;
    const correctContext = outcome.kind === 'answered' && matches(body, target);
    const result = correctContext ? olderPage(body, battle) : null;
    if (result === null || result.kind === 'refused') {
      const error: HistoryRead['error'] =
        outcome.kind === 'answered'
          ? correctContext && result?.why !== 'other_battle'
            ? 'unavailable'
            : 'wrong_context'
          : outcome.reason === 'network'
            ? 'network'
            : 'unavailable';
      const reason = result?.kind === 'refused' && result.why === 'reason' ? result.reason : null;
      store.update((s) => ({ ...s, older: { ...s.older, busy: false, error, reason } }));
      return;
    }
    store.update((s) => ({
      ...s,
      older: { ...joinOlder(s.older, result.page), busy: false, error: null, reason: null },
    }));
  };
  const refreshList = () => {
    void read('list');
  };
  const refreshDetail = () => {
    void read('detail');
  };
  return {
    ...store,
    setTarget: (target) => {
      if (sameTarget(store.getSnapshot().target, target)) return;
      invalidate('list');
      invalidate('detail');
      invalidate('older');
      store.update(() => ({
        target,
        filter: 'all',
        offset: 0,
        selected: null,
        list: empty(),
        detail: empty(),
        older: noOlder(),
      }));
      if (target !== null) refreshList();
    },
    filter: (filter) => {
      if (filter === store.getSnapshot().filter) return;
      invalidate('list');
      invalidate('detail');
      invalidate('older');
      store.update((s) => ({
        ...s,
        filter,
        offset: 0,
        selected: null,
        list: empty(),
        detail: empty(),
        older: noOlder(),
      }));
      refreshList();
    },
    page: (direction) => {
      const s = store.getSnapshot();
      if (s.list.busy || (direction === 'previous' ? s.offset === 0 : !historyHasNext(s.list.body)))
        return;
      invalidate('detail');
      invalidate('older');
      store.update((s) => ({
        ...s,
        offset: s.offset + (direction === 'next' ? HISTORY_PAGE_SIZE : -HISTORY_PAGE_SIZE),
        selected: null,
        list: empty(),
        detail: empty(),
        older: noOlder(),
      }));
      refreshList();
    },
    select: (selected) => {
      if (store.getSnapshot().selected === selected) return;
      // Selection is limited to records on the visible page.
      if (
        !historyRows(store.getSnapshot().list.body).some((row) => id(own(row, 'id')) === selected)
      )
        return;
      invalidate('detail');
      invalidate('older');
      store.update((s) => ({ ...s, selected, detail: empty(), older: noOlder() }));
      refreshDetail();
    },
    refreshList,
    refreshDetail,
    loadOlder: () => {
      void loadOlder();
    },
    dispose: () => {
      invalidate('list');
      invalidate('detail');
      invalidate('older');
    },
  };
}
