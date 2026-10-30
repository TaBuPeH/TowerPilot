import { settledPhase } from '../../src/api/intents/phases.ts';
import { parseAnswer, type WireAnswer } from '../../src/api/wire.ts';
import { ownPath } from '../../src/core/read.ts';
import { selectOutcomes, type OutcomeLineVM } from '../../src/store/selectors/outcomes.ts';
import { e0Config } from './dataLayer.ts';
import { sampleRecord } from './fixture.ts';
import { LOADED_ENDPOINTS } from './loadedContract.ts';
import { storeHarness } from './storeHarness.ts';

/**
 * One real Stop answer of the loaded fixture through the whole data path the
 * page uses (B-03): the API boundary (`parseAnswer` with the loaded contract's
 * cancel endpoint), the machine's phase (`settledPhase`, core's classifier),
 * and the store's outcome line (`selectOutcomes`).
 */
export function stopAnswerLine(path: string): {
  readonly wire: WireAnswer;
  readonly operationId: string;
  readonly line: OutcomeLineVM;
} {
  const raw = sampleRecord(`stop_operation.${path}`);
  const body = raw['body'];
  const named = ownPath(body, 'operation', 'id');
  const operationId = typeof named === 'string' ? named : '00000001-0000-4000-8000-000000000000';
  const wire = parseAnswer(LOADED_ENDPOINTS.cancel, Number(raw['status']), JSON.stringify(body));
  const h = storeHarness({ config: e0Config(false) });
  h.intent({
    id: 'stop',
    kind: 'stop',
    target: `operation:${operationId}`,
    meta: { operationId, expectedRevision: 1 },
  });
  h.answer(
    'stop',
    { status: wire.status, body: wire.body },
    settledPhase('stop', wire, { operationId }),
  );
  const line = selectOutcomes(h.state()).lines[0];
  if (line === undefined) throw new Error(`no outcome line for ${path}`);
  return { wire, operationId, line };
}
