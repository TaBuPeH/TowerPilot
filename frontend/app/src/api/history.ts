import {
  actionsBattleDetailAnswerSchema,
  actionsMoreBattleDetailAnswerSchema,
} from '../contract/actions.ts';
import { historyDetailAnswerSchema, historyListAnswerSchema } from '../contract/history.ts';
import { HISTORY_PAGE_SIZE } from '../core/history.ts';
import { readEndpoint, type ReadOutcome } from './client.ts';
import { fetchTransport, type Transport } from './transport.ts';
import { bodyMembers, ENDPOINTS, type Endpoint } from './wire.ts';

export interface HistoryTarget {
  readonly instance: string;
  readonly account: string | null;
  readonly serial: string;
  readonly epoch: number;
}

export type HistoryFilter = 'all' | 'active' | 'prepared' | 'ended' | 'interrupted';

export interface HistoryReader {
  readonly list: (
    target: HistoryTarget,
    filter: HistoryFilter,
    offset: number,
    signal: AbortSignal,
  ) => Promise<ReadOutcome>;
  readonly detail: (target: HistoryTarget, id: string, signal: AbortSignal) => Promise<ReadOutcome>;
  /** A2-7 AC4: the executions of one battle older than the cursor a read named as `next_before`. */
  readonly executionsBefore: (
    target: HistoryTarget,
    battleId: string,
    before: string,
    signal: AbortSignal,
  ) => Promise<ReadOutcome>;
}

const listEndpoint: Endpoint = {
  id: 'battles',
  method: 'GET',
  strict: [historyListAnswerSchema],
  members: bodyMembers([historyListAnswerSchema]),
};
// A2-7 R0: a battle detail whose executions block lists recorded action executions.
const DETAIL_SCHEMAS = [
  historyDetailAnswerSchema,
  actionsBattleDetailAnswerSchema,
  actionsMoreBattleDetailAnswerSchema,
];
const detailEndpoint: Endpoint = {
  id: 'battleDetail',
  method: 'GET',
  strict: DETAIL_SCHEMAS,
  members: bodyMembers(DETAIL_SCHEMAS),
};

/** Read-only adapter over the existing transport and generated wire boundary. */
export function createHistoryReader(transport: Transport = fetchTransport): HistoryReader {
  return {
    list: (target, filter, offset, signal) =>
      readEndpoint(
        transport,
        listEndpoint,
        `/api/battles?instance=${encodeURIComponent(target.instance)}&limit=${HISTORY_PAGE_SIZE + 1}&offset=${offset}${filter === 'all' ? '' : `&status=${filter}`}`,
        signal,
      ),
    detail: (target, id, signal) =>
      readEndpoint(
        transport,
        detailEndpoint,
        `/api/battles/${encodeURIComponent(id)}?instance=${encodeURIComponent(target.instance)}`,
        signal,
      ),
    executionsBefore: (target, battleId, before, signal) =>
      readEndpoint(
        transport,
        ENDPOINTS.actionExecutions,
        `/api/actions/executions?battle_id=${encodeURIComponent(battleId)}&before=${encodeURIComponent(before)}&instance=${encodeURIComponent(target.instance)}`,
        signal,
      ),
  };
}
