import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { contextFromConfig, createContextTracker } from '../../../src/api/context.ts';
import { sendCalibrateApply } from '../../../src/api/intents/recognition.ts';
import { createRecognitionApplyReadback } from '../../../src/api/recognitionApplyReadback.ts';
import { browserTimers } from '../../../src/api/timers.ts';
import { ownPath } from '../../../src/core/read.ts';
import { flush, machineHarness, postBodies } from '../../support/dataLayer.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

const c = (name: string) => dashboardPagesCase(`recognition_context_fence.${name}`);
const config = c('read_config_the_page_context_comes_from').body;
const other = c('read_config_after_another_client_switched_the_account').body;
const before = c('read_the_profile_after_the_malformed_requests_nothing_was_applied').body;
const after = c('read_the_profile_after_the_matching_apply').body;
const observations = ownPath(
  c('read_the_calibration_whose_apply_the_page_must_settle_by_reading').body,
  'state',
  'player',
);

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

async function lostApply() {
  const context = contextFromConfig(config);
  if (context === null) throw new Error('Recorded config context required');
  const harness = machineHarness({ context });
  const tracker = createContextTracker(harness.storage);
  tracker.observeConfig(config);
  harness.fake.route('POST', '/api/calibrate/apply', () => 'hold');
  sendCalibrateApply(harness.machine, { kind: 'saved' }, context, context, {
    before,
    observations,
  });
  harness.fake.next('/api/calibrate/apply', 'POST').lose();
  await flush();
  return {
    ...harness,
    tracker,
    context,
    api: createRecognitionApplyReadback(
      harness.machine,
      harness.fake.transport,
      tracker,
      browserTimers,
    ),
  };
}

it.each([false, true])(
  'R-FENCE a saved-apply profile read cannot settle after a context epoch change (return=%s)',
  async (returnToOriginal) => {
    const { machine, fake, tracker, api } = await lostApply();
    fake.route('GET', '/api/profile/mine.yaml', () => 'hold');
    api.check(1);
    tracker.observeConfig(other);
    if (returnToOriginal) tracker.observeConfig(config);
    fake.next('/api/profile/mine.yaml').respond(200, after);
    await flush();
    expect(machine.snapshot()[0]).toMatchObject({ phase: 'unconfirmed', readSettlement: null });
    expect(postBodies(fake, '/api/calibrate/apply')).toHaveLength(1);
    api.stop();
    machine.dispose();
  },
);

it('R-FENCE saved-apply evidence survives reload; unchanged read stays Unknown and matching read settles without a resend', async () => {
  const original = await lostApply();
  original.api.stop();
  original.machine.dispose();
  const { fake, machine } = machineHarness({
    storage: original.storage,
    context: original.context,
  });
  const tracker = createContextTracker(original.storage);
  tracker.observeConfig(config);
  const api = createRecognitionApplyReadback(machine, fake.transport, tracker, browserTimers);
  let profile = before;
  fake.route('GET', '/api/profile/mine.yaml', () => ({ status: 200, body: profile }));
  api.check(1);
  await flush();
  expect(machine.snapshot()[0]).toMatchObject({
    phase: 'unconfirmed',
    readSettlement: null,
    restored: true,
  });
  profile = after;
  api.check(2);
  await flush();
  expect(machine.snapshot()[0]).toMatchObject({
    phase: 'confirmed',
    readSettlement: 'applied',
    restored: true,
  });
  expect(fake.writes()).toEqual([]);
  api.stop();
  machine.dispose();
});

it('R-FENCE saved-apply settling read times out; polling the same trigger never repeats it or the apply', async () => {
  const { machine, fake, api } = await lostApply();
  fake.route('GET', '/api/profile/mine.yaml', () => 'hold');
  api.check(1);
  await vi.advanceTimersByTimeAsync(5_001);
  api.check(1);
  await flush();
  expect(machine.snapshot()[0]).toMatchObject({ phase: 'unconfirmed', readSettlement: null });
  expect(
    fake.requests.filter((read) => read.method === 'GET' && read.path === '/api/profile/mine.yaml'),
  ).toHaveLength(1);
  expect(postBodies(fake, '/api/calibrate/apply')).toHaveLength(1);
  api.stop();
  machine.dispose();
});

it('R-FENCE an extra saved run cannot hide positive profile readback after a lost apply', async () => {
  const { machine, fake, api } = await lostApply();
  // Add a run copied from this recorded profile: people can create runs after the fixture
  // was recorded, and this unrelated key must not invalidate discovery readback.
  const profile = structuredClone(after) as Record<string, unknown>;
  const blueprints = profile['blueprints'] as Record<string, unknown>;
  blueprints['my_run'] = structuredClone(blueprints['farm']);
  fake.route('GET', '/api/profile/mine.yaml', () => ({ status: 200, body: profile }));
  api.check(1);
  await flush();
  expect(machine.snapshot()[0]).toMatchObject({ phase: 'confirmed', readSettlement: 'applied' });
  expect(postBodies(fake, '/api/calibrate/apply')).toHaveLength(1);
  api.stop();
  machine.dispose();
});
