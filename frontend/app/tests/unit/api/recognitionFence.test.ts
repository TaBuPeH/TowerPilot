import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { contextFromConfig } from '../../../src/api/context.ts';
import { sendCalibrateApply } from '../../../src/api/intents/recognition.ts';
import { settledPhase } from '../../../src/api/intents/phases.ts';
import { ENDPOINTS, parseAnswer, type EndpointId } from '../../../src/api/wire.ts';
import { own } from '../../../src/core/read.ts';
import { applyAnswer, calibrateStopAnswer, launchAnswer } from '../../../src/core/recognition.ts';
import { machineHarness, postBodies } from '../../support/dataLayer.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';

const family = 'recognition_context_fence';
const found = (name: string) => dashboardPagesCase(`${family}.${name}`);
const endpoints: Readonly<Record<string, EndpointId>> = {
  '/api/calibrate/apply': 'calibrateApply',
  '/api/calibrate/start': 'calibrateStart',
  '/api/calibrate/observe': 'calibrateObserve',
  '/api/calibrate/stop': 'calibrateStop',
  '/api/screen-analysis/start': 'analysisStart',
  '/api/config': 'config',
};

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it.each(
  dashboardPagesCaseNames(family).filter(
    (name) =>
      endpoints[found(name).request.path] !== undefined ||
      found(name).request.path.startsWith('/api/calibrate/status'),
  ),
)('R-FENCE %s wire boundary', (name) => {
  const c = found(name);
  const endpoint = endpoints[c.request.path] ?? 'calibrate';
  const parsed = parseAnswer(ENDPOINTS[endpoint], c.status, JSON.stringify(c.body));
  expect(parsed.contract).toBe('exact');
  expect(parsed.body).toEqual(c.body);
  expect(parsed.issues).toEqual([]);
});

it.each(dashboardPagesCaseNames(family).filter((name) => found(name).status >= 400))(
  'R-FENCE %s classification',
  (name) => {
    const c = found(name);
    const apply = c.request.path === '/api/calibrate/apply';
    const result = apply ? applyAnswer(c) : launchAnswer(c);
    expect(result.kind).toBe('refused');
    if (result.kind !== 'refused') throw new Error('Expected a recorded refusal');
    expect(result.code).toBe(own(c.body, 'code'));
    expect(result.error).toBe(own(c.body, 'error'));
    expect(result.currentContext).toEqual(own(c.body, 'current_context') ?? null);
    const kind = apply
      ? 'calibrate_apply'
      : c.request.path.endsWith('/observe')
        ? 'calibrate_observe'
        : c.request.path.endsWith('/calibrate/start')
          ? 'calibrate_start'
          : 'analysis_start';
    expect(
      settledPhase(kind, { status: c.status, body: c.body, contract: 'exact', issues: [] }, {}),
    ).toBe('refused');
  },
);

it.each([
  'apply_saved_with_the_matching_context_is_applied',
  'apply_by_operation_with_the_matching_context_is_applied',
])('R-FENCE %s exact request body', async (name) => {
  const c = found(name);
  const context = contextFromConfig(found('read_config_the_page_context_comes_from').body);
  if (context === null) throw new Error('Fixture config must name a context');
  const { fake, machine } = machineHarness({ context });
  fake.route('POST', c.request.path, () => ({ status: c.status, body: c.body }));
  const operationId = own(c.request.body, 'operation_id');
  const expectedRevision = own(c.request.body, 'expected_revision');
  const apply =
    typeof operationId === 'string' && typeof expectedRevision === 'number'
      ? ({ kind: 'operation', operationId, expectedRevision } as const)
      : ({ kind: 'saved' } as const);
  // Structural LaunchContext input verifies the serializer strips rendering at runtime.
  const clickContext = { ...context, rendering: null };
  sendCalibrateApply(machine, apply, clickContext, context);
  expect(postBodies(fake, c.request.path)).toEqual([c.request.body]);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(postBodies(fake, c.request.path)).toHaveLength(1);
  expect(machine.snapshot()[0]?.phase).toBe('confirmed');
  machine.dispose();
});

it('R-FENCE stop_composed_for_alpha_after_the_switch_is_not_refused_a_stop_is_a_revocation', () => {
  const c = found('stop_composed_for_alpha_after_the_switch_is_not_refused_a_stop_is_a_revocation');
  expect(calibrateStopAnswer(c)).toMatchObject({ kind: 'answered', state: 'stopping' });
});
