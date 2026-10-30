import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  createClickTimeContextRead,
  createClickTimeTargetCheck,
} from '../../../src/api/clickContext.ts';
import { contextFromConfig, expectedContext } from '../../../src/api/context.ts';
import { ENDPOINTS } from '../../../src/api/wire.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
import { createFakeTransport } from '../../support/fakeTransport.ts';

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

it('r10 Profiles and Equipment click-time reads are fresh independent GETs, never an earlier in-flight read', async () => {
  const fake = createFakeTransport();
  const read = createClickTimeContextRead(fake.transport, ENDPOINTS.config);
  const earlier = read();
  const later = read();
  expect(fake.requests).toHaveLength(2);
  const original = dashboardPagesCase('loadout_patch.read_before_the_first_save');
  const other = dashboardPagesCase('config_state.effective_view_of_the_other_account');
  fake.last('/api/config').respond(other.status, other.body);
  expect(await later).toEqual(contextFromConfig(other.body));
  fake.next('/api/config').respond(original.status, original.body);
  expect(await earlier).toEqual(contextFromConfig(original.body));
  expect(vi.getTimerCount()).toBe(0);
});

it('r10 a click-time read times out after the shared five-second limit without sending a write', async () => {
  const fake = createFakeTransport();
  const reading = createClickTimeContextRead(fake.transport, ENDPOINTS.config)();
  await vi.advanceTimersByTimeAsync(5000);
  expect(await reading).toBeNull();
  expect(fake.next.bind(fake, '/api/config')).toThrow('no held request');
  expect(fake.writes()).toHaveLength(0);
  expect(vi.getTimerCount()).toBe(0);
});

it('r10 a cancelled click-time read clears its timeout and cannot authorize a write', async () => {
  const fake = createFakeTransport();
  const controller = new AbortController();
  const reading = createClickTimeContextRead(fake.transport, ENDPOINTS.config)(controller.signal);
  controller.abort();
  expect(await reading).toBeNull();
  expect(fake.writes()).toHaveLength(0);
  expect(vi.getTimerCount()).toBe(0);
});

it('r10 an unreadable config answer does not provide a click-time context', async () => {
  const fake = createFakeTransport();
  fake.route('GET', '/api/config', () =>
    dashboardPagesCase('config_state.file_missing_and_no_example_500'),
  );
  expect(await createClickTimeContextRead(fake.transport, ENDPOINTS.config)()).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
});

it('r10 the shared mutation-context serializer emits exactly the four fields and preserves nulls', () => {
  const context = {
    instance: 'main',
    serial: 'fake:1',
    account: null,
    profile: null,
    extra: 'excluded',
  };
  expect(expectedContext(context)).toEqual({
    instance: 'main',
    account: null,
    profile: null,
    serial: 'fake:1',
  });
  expect(Object.keys(expectedContext(context)).sort()).toEqual([
    'account',
    'instance',
    'profile',
    'serial',
  ]);
});

it.each([
  ['inactive_editor_context_before_save', 'same_account_inactive_target_listed', 'same'],
  [
    'read_config_after_editor_active_profile_switch',
    'same_account_inactive_target_listed',
    'context_changed',
  ],
  [
    'read_config_after_editor_instance_switch',
    'same_account_inactive_target_listed',
    'context_changed',
  ],
  [
    'read_config_after_editor_serial_change',
    'same_account_inactive_target_listed',
    'context_changed',
  ],
  ['inactive_editor_context_before_save', 'inactive_target_list_after_deletion', 'target_missing'],
])('PE2 click-time %s + %s returns %s and never sends a POST', async (config, files, result) => {
  const fake = createFakeTransport();
  const target = (name: string) => dashboardPagesCase(`profile_target.${name}`);
  const drawn = contextFromConfig(target('inactive_editor_context_before_save').body);
  if (drawn === null) throw new Error('fixture context missing');
  fake.route('GET', '/api/config', () => target(config));
  fake.route('GET', '/api/profile-files', () => target(files));
  const check = createClickTimeTargetCheck(fake.transport, ENDPOINTS);
  expect(await check(drawn, 'second')).toBe(result);
  expect(fake.requests.map((request) => request.path)).toEqual([
    '/api/config',
    '/api/profile-files',
  ]);
  expect(fake.writes()).toEqual([]);
  expect(vi.getTimerCount()).toBe(0);
});

it.each(['config', 'profile-files'])(
  'PE2 click-time unreadable %s cannot authorize the target',
  async (resource) => {
    const fake = createFakeTransport();
    const target = (name: string) => dashboardPagesCase(`profile_target.${name}`);
    const drawn = contextFromConfig(target('inactive_editor_context_before_save').body);
    if (drawn === null) throw new Error('fixture context missing');
    fake.route('GET', '/api/config', () => target('inactive_editor_context_before_save'));
    fake.route('GET', '/api/profile-files', () => target('same_account_inactive_target_listed'));
    fake.route('GET', `/api/${resource}`, () => 'hold');
    const checking = createClickTimeTargetCheck(fake.transport, ENDPOINTS)(drawn, 'second');
    await vi.advanceTimersByTimeAsync(5000);
    expect(await checking).toBe(
      resource === 'config' ? 'context_unconfirmed' : 'target_unconfirmed',
    );
    expect(fake.writes()).toEqual([]);
  },
);
