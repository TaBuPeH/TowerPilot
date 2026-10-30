import { expect, it } from 'vitest';
import { Outcomes } from '../../../../../src/features/dashboard/outcomes/Outcomes.tsx';
import { selectOutcomes } from '../../../../../src/store/selectors/outcomes.ts';
import { e0Config } from '../../../../support/dataLayer.ts';
import { storeHarness } from '../../../../support/storeHarness.ts';
import { renderRegion } from '../harness.tsx';
import { answerState } from './helpers.tsx';

/**
 * Spec 3.4: an outcome line's word is decided by the store (never by a
 * component): Waiting only while sent, Unknown for no reply, Blocked for a
 * refusal, Stopping for a part still stopping, and no chip for a confirmed
 * result, which claims nothing current.
 */
it('F-015: a confirmed "stopped" receipt is drawn without a chip, never as Waiting', () => {
  const h = answerState('stop', 'stop_operation.cancel.stopped');
  const [line] = selectOutcomes(h.state()).lines;
  expect(line?.word).toBeNull();
  const { view } = renderRegion(h.state(), <Outcomes />);
  const notice = view.container.querySelector('.tp-notice');
  expect(notice).toHaveAttribute('data-state', 'settled');
  expect(notice?.querySelector('.tp-chip')).toBeNull();
});

it('F-015: a stopping receipt reads Stopping; a sent request reads Waiting; a lost one reads Unknown', () => {
  expect(
    selectOutcomes(answerState('stop', 'stop_operation.cancel.stopping').state()).lines[0]?.word,
  ).toBe('stopping');
  const sending = storeHarness({ config: e0Config(false) });
  sending.intent({ id: 'sent', kind: 'pause' });
  expect(selectOutcomes(sending.state()).lines[0]?.word).toBe('waiting');
  const lost = storeHarness({ config: e0Config(false) });
  lost.intent({ id: 'lost', kind: 'pause', phase: 'unconfirmed', lostReason: 'network' });
  expect(selectOutcomes(lost.state()).lines[0]?.word).toBe('unknown');
});

it('F-085: an owner refusal of a write reads Blocked', () => {
  const h = answerState('pause', 'refusals.device_owned_collector');
  expect(selectOutcomes(h.state()).lines[0]?.word).toBe('blocked');
});
