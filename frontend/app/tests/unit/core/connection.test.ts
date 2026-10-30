import { expect, it } from 'vitest';
import {
  dashboardBody,
  dashboardCase,
  dashboardCaseNames,
} from '../../support/dashboardFixture.ts';

const fixtureDeviceCases = [
  'device_state_device',
  'device_state_missing',
  'device_state_no_server_connect_refused',
  'device_state_no_server_connect_timed_out',
  'device_state_offline',
  'device_state_unauthorized',
  'device_state_unconfigured',
  'device_state_unknown',
];
const fixtureReconnectCases = [
  'accepted_device_state_device',
  'adb_connect_raised',
  'answered_offline_after_the_retries',
  'answered_with_another_device_state',
  'refused_while_a_runner_is_listed',
];

it('C1 fixture status device states all expire after ten seconds', () => {
  expect(dashboardCaseNames('status').filter((name) => name.startsWith('device_state_'))).toEqual(
    fixtureDeviceCases,
  );
  for (const name of fixtureDeviceCases)
    expect(connectionView(answer(dashboardBody(`status.${name}`)), 10.001).row).toBe('C1');
});
it.each([
  ['device_state_device', 'C2', 'connected'],
  ['device_state_missing', 'C3', 'adb_missing'],
  ['device_state_no_server_connect_refused', 'C3', 'adb_unreachable'],
  ['device_state_no_server_connect_timed_out', 'C3', 'adb_unreachable'],
  ['device_state_offline', 'C3', 'adb_offline'],
  ['device_state_unauthorized', 'C3', 'adb_unauthorized'],
  ['device_state_unconfigured', 'C3', 'emulator_unconfigured'],
  ['device_state_unknown', 'C4', 'connection_unconfirmed'],
])('F-003 fixture %s maps to %s without a process-liveness claim', (name, row, line) => {
  expect(connectionView(answer(dashboardBody(`status.${name}`)), 0)).toMatchObject({ row, line });
});
it('C3 both fixture no_server causes use the same adb-unreachable line', () => {
  expect(dashboardBody('status.device_state_no_server_connect_refused')).toEqual(
    dashboardBody('status.device_state_no_server_connect_timed_out'),
  );
  for (const name of [
    'device_state_no_server_connect_refused',
    'device_state_no_server_connect_timed_out',
  ])
    expect(connectionView(answer(dashboardBody(`status.${name}`)), 0).line).toBe('adb_unreachable');
});
it('F-004 every fixture reconnect answer is handled without attributing later connection state to this request', () => {
  expect(dashboardCaseNames('reconnect')).toEqual(fixtureReconnectCases);
  const expected = ['C2', 'C1', 'C3', 'C3', 'C1'];
  fixtureReconnectCases.forEach((name, index) => {
    const c = dashboardCase(`reconnect.${name}`);
    expect(connectionView(c.status === 200 ? answer(c.body) : { kind: 'failed' }, 0).row).toBe(
      expected[index],
    );
    expect(
      reconnectGate('fake:1', answer(dashboardBody('status.processes_empty')), true).enabled,
    ).toBe(false);
  });
  expect(
    reconnectGate('fake:1', answer(dashboardBody('status.processes_linked_collector')), false)
      .reason,
  ).toBe('process_listed');
});
import { connectionView, reconnectGate, type ContextRead } from '../../../src/core/connection.ts';

// Constructed api_status bodies: backend/dashboard.py api_status/_device_state;
// backend/device/adbclient.py _ask_state (refusal and timeout both => no_server).
const answer = (body: unknown): ContextRead => ({ kind: 'answered', sameContext: true, body });

it('C1 requires a successful same-context read within 10 seconds', () => {
  for (const read of [
    null,
    { kind: 'failed' },
    { kind: 'answered', sameContext: false, body: { device_state: 'device' } },
  ] satisfies ContextRead[]) {
    expect(connectionView(read, 0).row).toBe('C1');
  }
  for (const age of [-1, 10.001, NaN, Infinity]) {
    expect(connectionView(answer({ device_state: 'device' }), age).row).toBe('C1');
  }
});

it('C2 Connected needs EV-11 including the inclusive 10-second boundary', () => {
  expect(connectionView(answer({ device_state: 'device' }), 10)).toEqual({
    row: 'C2',
    word: null,
    line: 'connected',
    evidence: 'EV-11',
    claim: null,
  });
});

it.each([
  ['offline', 'adb_offline'],
  ['unauthorized', 'adb_unauthorized'],
  ['missing', 'adb_missing'],
  ['no_server', 'adb_unreachable'],
  ['unconfigured', 'emulator_unconfigured'],
])(
  'C3 %s describes the adb connection without claiming a process is absent',
  (device_state, line) => {
    expect(connectionView(answer({ device_state }), 0)).toMatchObject({
      row: 'C3',
      word: 'blocked',
      line,
    });
  },
);

it('C4 a port-only connected value and inherited or coercible values are Unknown', () => {
  for (const body of [
    { connected: true, port_open: true },
    { device_state: 'unknown' },
    { device_state: 'constructor' },
    { device_state: { toString: () => 'device' } },
    Object.create({ device_state: 'device' }) as unknown,
  ]) {
    expect(connectionView(answer(body), 0).row).toBe('C4');
  }
});

it('F-004 reconnect waits for sending and every listed process, and missing serial offers navigation', () => {
  expect(reconnectGate(null, null, false)).toMatchObject({
    action: 'choose_emulator',
    enabled: true,
  });
  expect(reconnectGate('fake:1', answer({ processes: [] }), false).enabled).toBe(true);
  expect(reconnectGate('fake:1', answer({ processes: [{}] }), false).reason).toBe('process_listed');
  expect(reconnectGate('fake:1', answer({ processes: [] }), true).reason).toBe('sending');
  expect(reconnectGate('fake:1', null, false).reason).toBe('process_list_unavailable');
});
