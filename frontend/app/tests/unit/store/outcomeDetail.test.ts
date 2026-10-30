import { describe, expect, it } from 'vitest';
import { ownPath } from '../../../src/core/read.ts';
import { serverTextContent, type ServerText } from '../../../src/copy/server.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import type { IntentKindCode } from '../../../src/store/types.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sampleRecord } from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';

/**
 * R8-08: every outcome line keeps the distinctions its class carries (the
 * owners and their way out, the failed steps, what a Pause stopped, a
 * Start's handover) and its answer's age, with server strings as ServerText.
 */
const texts = (values: readonly ServerText[]) => values.map((value) => serverTextContent(value));

function lineOf(kind: IntentKindCode, path: string, meta = {}) {
  const h = storeHarness({ config: e0Config(false) });
  h.intent({ id: 'line', kind, meta });
  h.clock(4000);
  h.answer('line', answerOf(sampleRecord(path)));
  h.clock(9000);
  const line = selectOutcomes(h.state()).lines[0];
  if (line === undefined) throw new Error(`no line for ${path}`);
  return line;
}

describe('R8-08 outcome lines keep the classifier detail and the answer time', () => {
  it('S-041 a stopping Turn input off names each unfenced owner with its own way out and the paused revision', () => {
    const answer = sampleRecord('automation_access.disable_stopping');
    const line = lineOf('input_off', 'automation_access.disable_stopping');
    expect(line.answeredAgeSec).toBe(5);
    expect(line.detail).toMatchObject({
      kind: 'input',
      class: 'stopping',
      harvestPaused: { revision: ownPath(answer, 'body', 'revocation', 'harvest', 'revision') },
    });
    if (line.detail.kind !== 'input') throw new Error('an input detail');
    expect(texts(line.detail.stopping)).toEqual(ownPath(answer, 'body', 'revocation', 'stopping'));
    const owned = ownPath(answer, 'body', 'revocation', 'still_owned');
    const entry: unknown = Array.isArray(owned) ? owned[0] : undefined;
    expect(line.detail.unfenced).toHaveLength(1);
    expect(line.detail.unfenced[0]).toMatchObject({
      kind: 'operation',
      pid: ownPath(entry, 'pid'),
      input: 'unfenced',
      recovery: { kind: 'pause' },
    });
    expect(serverTextContent(line.detail.unfenced[0]?.operation ?? fail('operation'))).toBe(
      ownPath(entry, 'operation_id'),
    );
  });

  it('S-004 a Pause receipt keeps what it stopped, as a past claim', () => {
    const answer = sampleRecord('post_harvest.off_ok_stopped');
    const line = lineOf('pause', 'post_harvest.off_ok_stopped');
    expect(line.detail).toMatchObject({
      kind: 'harvest',
      class: 'paused',
      revision: ownPath(answer, 'body', 'desired', 'revision'),
      noCollectorWasRunning: false,
    });
    if (line.detail.kind !== 'harvest') throw new Error('a harvest detail');
    expect(texts(line.detail.stopped)).toEqual(ownPath(answer, 'body', 'stop', 'stopped'));
  });

  it('S-102 a Start that took over a collector keeps the handover lists and the wait', () => {
    const answer = sampleRecord('start_transfer.run_takes_over_collector');
    const line = lineOf('start', 'start_transfer.run_takes_over_collector');
    expect(line.detail).toMatchObject({
      kind: 'start',
      class: 'started',
      transfer: 'handover',
      waitedSec: ownPath(answer, 'body', 'transfer', 'waited_sec'),
    });
    if (line.detail.kind !== 'start') throw new Error('a start detail');
    expect(serverTextContent(line.detail.operation ?? fail('operation'))).toBe(
      ownPath(answer, 'body', 'operation_id'),
    );
    expect(texts(line.detail.stoppedAtHandover)).toEqual(
      ownPath(answer, 'body', 'transfer', 'stopped'),
    );
  });
});

function fail(what: string): never {
  throw new Error(`missing ${what}`);
}
