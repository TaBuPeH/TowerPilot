import type { ReadOutcome } from '../api/client.ts';
import type { RuntimeReader } from '../api/runtime.ts';
import { own, text } from '../core/read.ts';
import {
  diagnosticsBody,
  diagnosticExport,
  draftFromQuery,
  queryProblem,
  runtimeBody,
  validateFilters,
  type DiagnosticQuery,
  type FilterDraft,
  type FilterProblem,
} from '../core/runtime.ts';
import { createStore, type Store } from './store.ts';

export interface RuntimeRead {
  readonly body: unknown;
  readonly loadedAt: number | null;
  readonly busy: boolean;
  readonly error: 'network' | 'unavailable' | null;
  readonly serverError: string | null;
}
export interface RuntimeState {
  readonly identity: RuntimeRead;
  readonly events: RuntimeRead;
  readonly applied: DiagnosticQuery;
  readonly draft: FilterDraft;
  readonly problem: FilterProblem | null;
  readonly auto: boolean;
}
export interface RuntimeStore extends Store<RuntimeState> {
  readonly start: () => void;
  readonly refreshIdentity: () => void;
  readonly refreshEvents: () => void;
  readonly edit: (field: string, value: string | boolean) => void;
  readonly apply: () => DiagnosticQuery | null;
  readonly restore: (query: DiagnosticQuery) => void;
  readonly setAuto: (enabled: boolean) => void;
  readonly exportJSON: () => string | null;
  readonly dispose: () => void;
}
const empty = (): RuntimeRead => ({
  body: undefined,
  loadedAt: null,
  busy: false,
  error: null,
  serverError: null,
});

/** Independent, single-flight, sequence-fenced reads; retained evidence always belongs to applied. */
export function createRuntimeStore(
  reader: RuntimeReader,
  query: DiagnosticQuery = {},
  now: () => number = Date.now,
): RuntimeStore {
  const store = createStore<RuntimeState>({
    identity: empty(),
    events: empty(),
    applied: query,
    draft: draftFromQuery(query),
    problem: queryProblem(query),
    auto: false,
  });
  const requests: { identity: AbortController | null; events: AbortController | null } = {
    identity: null,
    events: null,
  };
  const invalidate = (slot: 'identity' | 'events') => {
    requests[slot]?.abort();
    requests[slot] = null;
  };
  const read = async (slot: 'identity' | 'events') => {
    if (
      requests[slot] !== null ||
      (slot === 'events' && queryProblem(store.getSnapshot().applied) !== null)
    )
      return;
    const controller = new AbortController();
    requests[slot] = controller;
    const applied = store.getSnapshot().applied;
    store.update((s) => ({ ...s, [slot]: { ...s[slot], busy: true } }));
    let outcome: ReadOutcome;
    try {
      outcome = await (slot === 'identity'
        ? reader.identity(controller.signal)
        : reader.events(applied, controller.signal));
    } catch {
      outcome = { kind: 'failed', reason: 'network', answer: null };
    }
    if (requests[slot] !== controller) return;
    requests[slot] = null;
    const body = outcome.answer?.body;
    const valid =
      outcome.kind === 'answered' &&
      (slot === 'identity' ? runtimeBody(body) : diagnosticsBody(body));
    store.update((s) => ({
      ...s,
      [slot]: valid
        ? { body, loadedAt: now(), busy: false, error: null, serverError: null }
        : {
            ...s[slot],
            busy: false,
            error:
              outcome.kind === 'failed' && outcome.reason === 'network' ? 'network' : 'unavailable',
            serverError: text(own(body, 'error')) ?? null,
          },
    }));
  };
  const refreshIdentity = () => {
    void read('identity');
  };
  const refreshEvents = () => {
    void read('events');
  };
  return {
    ...store,
    start: () => {
      refreshIdentity();
      refreshEvents();
    },
    refreshIdentity,
    refreshEvents,
    edit: (field, value) => {
      store.update((s) => {
        const draft =
          field === 'failures'
            ? { ...s.draft, failures: value === true }
            : typeof value !== 'string'
              ? s.draft
              : field === 'since' || field === 'limit'
                ? { ...s.draft, [field]: value }
                : { ...s.draft, exact: { ...s.draft.exact, [field]: value } };
        return { ...s, draft, problem: validateFilters(draft).problem };
      });
    },
    apply: () => {
      const checked = validateFilters(store.getSnapshot().draft);
      if (checked.query === null) {
        store.update((s) => ({ ...s, problem: checked.problem }));
        return null;
      }
      invalidate('events');
      store.update((s) => ({ ...s, applied: checked.query, problem: null, events: empty() }));
      refreshEvents();
      return checked.query;
    },
    restore: (applied) => {
      invalidate('events');
      store.update((s) => ({
        ...s,
        applied,
        draft: draftFromQuery(applied),
        problem: queryProblem(applied),
        events: empty(),
      }));
      refreshEvents();
    },
    setAuto: (auto) => {
      store.update((s) => ({ ...s, auto }));
    },
    exportJSON: () => {
      const s = store.getSnapshot();
      return s.events.loadedAt === null ? null : diagnosticExport(s.events.body, s.applied, now());
    },
    dispose: () => {
      invalidate('identity');
      invalidate('events');
      store.update((s) => ({
        ...s,
        auto: false,
        identity: { ...s.identity, busy: false },
        events: { ...s.events, busy: false },
      }));
    },
  };
}
