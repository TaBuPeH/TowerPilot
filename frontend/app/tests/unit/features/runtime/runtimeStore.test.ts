import { expect, it } from 'vitest';
import { createRuntimeReader } from '../../../../src/api/runtime.ts';
import { createRuntimeStore } from '../../../../src/store/runtime.ts';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { createFakeTransport } from '../../../support/fakeTransport.ts';

const tick = async () => {
  await Promise.resolve();
  await Promise.resolve();
};
const identity = dashboardPagesCase('runtime_state.idle_server_build_matches_the_source_on_disk');
const result = dashboardPagesCase(
  'runtime_diagnostics.every_event_oldest_first_with_log_coordinates',
);
function setup() {
  const fake = createFakeTransport();
  fake.route('GET', '/api/runtime', () => identity);
  const store = createRuntimeStore(createRuntimeReader(fake.transport));
  return { store, fake };
}
it('T4 SR-4 new applied query fences an older held result; duplicate refresh is single-flight', async () => {
  const { store, fake } = setup();
  store.start();
  const old = fake.next('/api/diagnostics/events');
  store.edit('operation_id', 'op-1');
  expect(store.apply()).toEqual({ operation_id: 'op-1' });
  store.refreshEvents();
  store.refreshEvents();
  expect(fake.requests.filter((r) => r.path.includes('/api/diagnostics/events'))).toHaveLength(2);
  old.respond(result.status, result.body);
  await tick();
  expect(store.getSnapshot().events.loadedAt).toBeNull();
  const filtered = dashboardPagesCase('runtime_diagnostics.filter_by_operation');
  fake.next('/api/diagnostics/events?operation_id=op-1').respond(filtered.status, filtered.body);
  await tick();
  expect(store.getSnapshot().events.body).toEqual(filtered.body);
  store.dispose();
  await tick();
});
it('T4 SR-5 invalid raw limit refuses Apply without replacing an applied query', async () => {
  const { store, fake } = setup();
  store.start();
  store.edit('limit', '1001');
  expect(store.apply()).toBeNull();
  expect(store.getSnapshot()).toMatchObject({
    applied: {},
    draft: { limit: '1001' },
    problem: 'limit',
  });
  expect(fake.requests.filter((r) => r.path.includes('/api/diagnostics/events'))).toHaveLength(1);
  store.dispose();
  await tick();
});
it('T6 failed refresh retains evidence; applying another query makes export unavailable until it answers', async () => {
  const { store, fake } = setup();
  store.start();
  fake.next('/api/diagnostics/events').respond(result.status, result.body);
  await tick();
  const exported = store.exportJSON();
  expect(exported).not.toBeNull();
  store.refreshEvents();
  fake.next('/api/diagnostics/events').lose();
  await tick();
  expect(store.getSnapshot().events.error).toBe('network');
  expect(store.exportJSON()).not.toBeNull();
  store.edit('kind', 'runner_error');
  store.apply();
  expect(store.exportJSON()).toBeNull();
  store.dispose();
  await tick();
  expect(store.getSnapshot().events.loadedAt).toBeNull();
});
