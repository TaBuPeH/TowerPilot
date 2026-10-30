import {
  HISTORY_PAGE_SIZE,
  historyCollection,
  historyHasNext,
  historyRows,
  historyStatsState,
} from '../../core/history.ts';
import { executionsView, type Coded, type ExecutionRow } from '../../core/historyActions.ts';
import { finite, id, list, own, ownPath } from '../../core/read.ts';
import { fromServer } from '../../copy/server.ts';
import type { HistoryRead, HistoryState, OlderRead } from '../history.ts';

function json(value: unknown) {
  return value === null || value === undefined
    ? null
    : fromServer('detail', JSON.stringify(value, null, 2));
}
function number(value: unknown): number | null {
  return finite(value) ?? null;
}

export function historyRow(raw: unknown) {
  return {
    id: id(own(raw, 'id')) ?? null,
    name: fromServer('name', own(raw, 'label')),
    blueprint: fromServer('id', own(raw, 'blueprint')),
    recordId: fromServer('id', own(raw, 'id')),
    status: fromServer('detail', own(raw, 'status')),
    tier: number(own(raw, 'tier')),
    observedWave: number(ownPath(raw, 'runtime', 'observed_wave')),
    finalWave: number(own(raw, 'final_wave')),
    startedAt: number(own(raw, 'started_at')),
  };
}

function codedVM<K extends string>(word: Coded<K>) {
  return { kind: word.kind, code: fromServer('detail', word.code) };
}
/** Text first, then `instance` only when it names something other than the action itself. */
function executionRowVM(row: ExecutionRow) {
  return {
    key: row.key,
    action: fromServer('id', row.action),
    instance: row.instance === row.action ? null : fromServer('id', row.instance),
    label: fromServer('name', row.label),
    trigger: codedVM(row.trigger),
    open: row.open,
    state: codedVM(row.state),
    result: row.result === null ? null : codedVM(row.result),
    effect: row.effect === null ? null : codedVM(row.effect),
    reason: fromServer('message', row.reason),
    startedAt: row.startedAt,
    finishedAt: row.finishedAt,
    durationMs: row.durationMs,
    showsDuration: row.showsDuration,
    coverage: codedVM(row.coverage),
    aggregate: row.aggregate,
  };
}

/** A2-7 AC4: the executions of the selected battle, as the block and the older pages read so far say. */
function historyExecutions(block: unknown, older: OlderRead, detail: HistoryRead) {
  const view = executionsView(block, older);
  const rows = view.kind === 'rows' ? view : null;
  return {
    state: view.kind,
    reason: view.kind === 'unreadable' ? fromServer('detail', view.reason) : null,
    rows: rows === null ? [] : rows.rows.map(executionRowVM),
    truncated: rows?.truncated ?? false,
    canLoadOlder: rows !== null && rows.cursor !== null,
    skipped: rows?.unusable ?? 0,
    loadedOlder: older.loaded,
    older: { busy: older.busy, error: older.error, reason: fromServer('detail', older.reason) },
    readAtMs: detail.loadedAt,
  };
}

function historyDetail(
  raw: unknown,
  events: unknown,
  executions: ReturnType<typeof historyExecutions>,
) {
  const setup = ownPath(raw, 'runtime', 'setup');
  const stats = ownPath(raw, 'runtime', 'stats_capture');
  return {
    ...historyRow(raw),
    origin: fromServer('detail', own(raw, 'origin')),
    certainty: fromServer('detail', own(raw, 'start_certainty')),
    partial: own(raw, 'start_certainty') === 'partial_observation',
    result: fromServer('detail', own(raw, 'result')),
    reason: fromServer('message', own(raw, 'end_reason')),
    duration: number(ownPath(raw, 'runtime', 'observed_duration_sec')),
    observedAt: number(own(raw, 'observed_started_at')),
    endedAt: number(own(raw, 'ended_at')),
    updatedAt: number(own(raw, 'updated_at')),
    provenance: fromServer('detail', own(raw, 'wave_provenance')),
    previousId: fromServer('id', own(raw, 'previous_battle_id')),
    observation: fromServer('detail', ownPath(raw, 'runtime', 'observation_status')),
    boundary: json(own(raw, 'boundary_evidence')),
    setup: {
      state: fromServer('detail', own(setup, 'state')),
      reason: fromServer('message', own(setup, 'reason')),
      detail: fromServer('detail', own(setup, 'detail')),
      partial: own(setup, 'continued_with_partial') === true,
      loadout: fromServer('name', own(setup, 'loadout')),
      modules: json(own(setup, 'modules')),
    },
    stats: {
      state: historyStatsState(raw),
      recordedState: fromServer('detail', own(stats, 'status')),
      reason: fromServer('message', own(stats, 'reason')),
      error: fromServer('message', own(stats, 'error')),
      raw: json(stats),
    },
    collection: historyCollection(raw),
    executions,
    collectionRaw: json(ownPath(raw, 'runtime', 'collection_evidence')),
    gaps: json(ownPath(raw, 'runtime', 'observation_gaps')),
    openGap: number(ownPath(raw, 'runtime', 'observation_gap_started_at')),
    snapshot: json(own(raw, 'envelope')),
    effective: json(own(raw, 'effective')),
    events: (list(events) ?? []).map((event, index) => ({
      key: `${index}:${String(own(event, 'sequence'))}`,
      at: number(own(event, 't')),
      kind: fromServer('detail', own(event, 'kind')),
      raw: json(event),
    })),
  };
}

export function selectHistory(state: HistoryState) {
  return {
    targetKnown: state.target !== null,
    filter: state.filter,
    selected: state.selected,
    pageNumber: state.offset / HISTORY_PAGE_SIZE + 1,
    hasPrevious: state.offset > 0,
    hasNext: historyHasNext(state.list.body),
    list: state.list,
    detailRead: state.detail,
    rows: historyRows(state.list.body).map(historyRow),
    detail:
      state.detail.loadedAt === null
        ? null
        : historyDetail(
            own(state.detail.body, 'battle'),
            own(state.detail.body, 'events'),
            historyExecutions(own(state.detail.body, 'executions'), state.older, state.detail),
          ),
  };
}
export type HistoryVM = ReturnType<typeof selectHistory>;
export type HistoryDetailVM = NonNullable<HistoryVM['detail']>;
export type HistoryRowVM = ReturnType<typeof historyRow>;
