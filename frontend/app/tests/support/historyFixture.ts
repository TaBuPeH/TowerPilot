import { historyCase, HISTORY_RECORDS } from './history-fixtures.mjs';
import { createHistoryReader, type HistoryTarget } from '../../src/api/history.ts';
import type { Transport, TransportRequest } from '../../src/api/transport.ts';

export const historyTarget: HistoryTarget = {
  instance: 'main',
  serial: 'fake:1',
  account: null,
  epoch: 1,
};
export { historyCase, HISTORY_RECORDS };

/** Controlled transport ordering over real generated backend answers. */
export function historyTransport() {
  const pending: {
    readonly request: TransportRequest;
    readonly resolve: (result: Awaited<ReturnType<Transport>>) => void;
  }[] = [];
  const transport: Transport = (request) =>
    new Promise((resolve) => {
      pending.push({ request, resolve });
    });
  return {
    pending,
    reader: createHistoryReader(transport),
    answer: (index: number, family: 'history_list' | 'history_detail', name: string) => {
      const row = pending[index];
      if (row === undefined) throw new Error('No pending read');
      const answer = historyCase(family, name);
      row.resolve({ kind: 'response', status: answer.status, text: JSON.stringify(answer.body) });
    },
    body: (index: number, body: unknown) => {
      const row = pending[index];
      if (row === undefined) throw new Error('No pending read');
      row.resolve({ kind: 'response', status: 200, text: JSON.stringify(body) });
    },
    fail: (index: number) => {
      pending[index]?.resolve({ kind: 'failed', reason: 'network', detail: 'offline' });
    },
  };
}
