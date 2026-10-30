import { expect, it } from 'vitest';
import { connectionView } from '../../../src/core/connection.ts';
import { serverTextContent } from '../../../src/copy/server.ts';
import { reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { contextReadOf } from '../../../src/store/reads.ts';
import { selectTopBar, selectTopBarVM } from '../../../src/store/selectors/topBar.ts';
import { chipVM } from '../../../src/store/viewModels.ts';
import { dashboardBody, dashboardCaseNames } from '../../support/dashboardFixture.ts';
import {
  addBody,
  addCase,
  addFailure,
  expectStateVM,
  intent,
  regionState,
  tick,
} from './regionHelpers.ts';

for (const [family, resource] of [
  ['accounts', 'accounts'],
  ['config', 'config'],
  ['wizard_emulators', 'wizardEmulators'],
  ['status', 'status'],
] satisfies readonly (readonly [string, 'accounts' | 'config' | 'wizardEmulators' | 'status'])[]) {
  it.each(dashboardCaseNames(family))(`top bar projects ${family}.%s without raw data`, (name) => {
    const state = addCase(regionState(), resource, `${family}.${name}`);
    const vm = selectTopBarVM(state);
    expectStateVM(vm, state);
    if (resource === 'status')
      expect(vm.connection).toEqual(
        chipVM(connectionView(contextReadOf(state.reads.status.latest), 0)),
      );
  });
}

it('keeps the existing account API and retains its newest answered account after failure', () => {
  const body = dashboardBody('accounts.active_among_several');
  const state = addBody(regionState(), 'accounts', body);
  const vm = selectTopBarVM(addFailure(tick(state, 12), 'accounts'));
  expect(vm.account).toEqual(selectTopBar({ body, observedAt: 10 }).account);
  expect(vm.accountAgeSec).toBe(12);
  if (vm.account.kind !== 'known') throw new Error('Missing fixture account');
  expect(serverTextContent(vm.account.value)).toBe('Alpha');
});

it('matches the emulator by serial and adb port; a missing name keeps an Unknown fact and the instance id', () => {
  const state = addCase(
    regionState(),
    'wizardEmulators',
    'wizard_emulators.instances_with_names_and_ports',
  );
  const configured = addBody(state, 'config', {
    active_instance: 'main',
    instances: { main: { serial: '127.0.0.1:16384' } },
  });
  const vm = selectTopBarVM(configured);
  if (vm.emulator.kind !== 'known') throw new Error('Missing emulator name');
  expect(serverTextContent(vm.emulator.value)).toBe('Main Tower');
  const failed = selectTopBarVM(addFailure(configured, 'wizardEmulators'));
  expect(failed.emulator.kind).toBe('unknown');
  if (failed.emulatorInstance === null) throw new Error('Missing instance key');
  expect(serverTextContent(failed.emulatorInstance)).toBe('main');
  expectStateVM(vm, configured);
});

it('expires connection evidence at 11 seconds and fails closed on a later failed read', () => {
  const state = addCase(regionState(), 'status', 'status.device_state_device');
  expect(selectTopBarVM(tick(state, 10)).connection.row).toBe('C2');
  expect(selectTopBarVM(tick(state, 11)).connection.row).toBe('C1');
  expect(selectTopBarVM(addFailure(state, 'status')).connection.row).toBe('C1');
  expect(selectTopBarVM(regionState()).connection.word).toBe('unknown');
});

it('reconnect is gated by listed processes and by any connection request in flight (F6M-03)', () => {
  const state = addCase(
    addCase(regionState(), 'config', 'config.taps_true'),
    'status',
    'status.processes_empty',
  );
  expect(selectTopBarVM(state).reconnect.enabled).toBe(true);
  const sending = intent(state, { kind: 'reconnect', phase: 'sending' });
  expect(
    selectTopBarVM(reduceDashboard(state, { type: 'intents', records: [sending] })).reconnect
      .reason,
  ).toBe('sending');
  const foreign = {
    ...sending,
    context: { instance: 'other', serial: 'fake:2', account: null, profile: 'mine' },
  };
  // F6M-03: one connection request at a time, whatever context it was made in.
  expect(
    selectTopBarVM(reduceDashboard(state, { type: 'intents', records: [foreign] })).reconnect
      .reason,
  ).toBe('sending');
  for (const kind of ['adopt', 'launch', 'prepare', 'tools_install'] as const) {
    const other = intent(state, { kind, phase: 'sending' });
    expect(
      selectTopBarVM(reduceDashboard(state, { type: 'intents', records: [other] })).reconnect
        .enabled,
    ).toBe(false);
  }
  // F6M-02: an enabled Reconnect carries the context and the address it shows.
  expect(selectTopBarVM(state).reconnect.command).toMatchObject({
    context: state.context,
    serial: state.context?.serial,
  });
  expect(
    selectTopBarVM(addCase(state, 'status', 'status.processes_linked_collector')).reconnect.reason,
  ).toBe('process_listed');
  expect(selectTopBarVM(regionState()).reconnectAction).toBe('choose_emulator');
});
