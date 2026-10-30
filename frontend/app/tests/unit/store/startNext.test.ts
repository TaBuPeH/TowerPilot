import { expect, it } from 'vitest';
import { connectionView } from '../../../src/core/connection.ts';
import { gateOf } from '../../../src/core/gate.ts';
import { collectionPermit } from '../../../src/core/guards.ts';
import { own, ownPath, record } from '../../../src/core/read.ts';
import { collectGate, selectRun } from '../../../src/core/readiness.ts';
import { serverTextContent } from '../../../src/copy/server.ts';
import { reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { assessedRead, contextReadOf } from '../../../src/store/reads.ts';
import { selectStartNext } from '../../../src/store/selectors/startNext.ts';
import { dashboardBody, dashboardCaseNames } from '../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../support/fixture.ts';
import {
  addBody,
  addCase,
  addFailure,
  expectStateVM,
  intent,
  regionState,
} from './regionHelpers.ts';

function launchState() {
  let state = addCase(regionState(), 'runs', 'runs.ready');
  const guards = dashboardBody('status.guards_clear');
  state = addBody(state, 'status', {
    ...statusBody(sample('status.off')),
    setup_battle_guard: own(guards, 'setup_battle_guard'),
    collection_guard: own(guards, 'collection_guard'),
  });
  return state;
}

it.each(dashboardCaseNames('runs'))(
  'picker fixture %s keeps defined runs and readiness codes',
  (name) => {
    const state = addCase(launchState(), 'runs', `runs.${name}`);
    const body = dashboardBody(`runs.${name}`);
    const vm = selectStartNext(state);
    expect(vm.picker.selected).toBe(selectRun(body, null));
    expect(vm.start.enabled).toBe(true);
    expect(vm.start.command?.preset).toBe(vm.picker.selected);
    for (const row of vm.picker.runs)
      expect(ownPath(body, 'runs', serverTextContent(row.name), 'defined')).not.toBe(false);
    expectStateVM(vm, state);
  },
);

it.each(dashboardCaseNames('status'))('launch guards from status.%s stay separate', (name) => {
  // A real /api/status body always carries `harvest` (its own fixture); its server_time dates the permit.
  const recorded = dashboardBody(`status.${name}`);
  const state = addBody(launchState(), 'status', {
    ...(typeof recorded === 'object' && recorded !== null ? recorded : {}),
    harvest: sample('status.off'),
  });
  const vm = selectStartNext(state);
  const status = state.reads.status.latest;
  const body = status?.outcome.kind === 'answered' ? status.outcome.body : undefined;
  // F-038: the setup guard alone permits Start; an environment blocker (the emulator not
  // connected) still disables it, with its own reason.
  const connectionBlocked = connectionView(contextReadOf(status), 0).word === 'blocked';
  expect(vm.start.enabled).toBe(
    ownPath(body, 'setup_battle_guard', 'scope') === 'run' &&
      ownPath(body, 'setup_battle_guard', 'blocks_launch') === false &&
      !connectionBlocked,
  );
  if (connectionBlocked && vm.start.reason !== 'guard_blocks') {
    expect(vm.start.reason).toBe('connection_blocked');
  }
  expectStateVM(vm, state);
});

it('F-038 Start is disabled while game input is off, with its own reason, and the setup guard stays separate', () => {
  const state = addBody(launchState(), 'config', {
    ...record(dashboardBody('config.taps_false')),
  });
  const vm = selectStartNext(state);
  expect(vm.start).toMatchObject({ enabled: false, reason: 'input_off' });
  expect(vm.reasons.find((r) => r.action === 'start')?.code).toBe('input_off');
});

it.each(dashboardCaseNames('run_controls'))(
  'saved preview %s follows the core collection gate',
  (name) => {
    let state = addCase(launchState(), 'runControls', `run_controls.${name}`);
    if (name === 'combo_is_no_blueprint') {
      state = addCase(state, 'runs', 'runs.combo_entry');
      state = reduceDashboard(state, { type: 'select_run', run: 'combo' });
    }
    const status = state.reads.status.latest;
    if (status?.outcome.kind !== 'answered') throw new Error('Missing status');
    const permit = collectionPermit(own(status.outcome.body, 'collection_guard'), 10);
    const selected = selectStartNext(state).picker.selected ?? '';
    const path = `/api/run-controls?blueprint=${selected.startsWith('bp_') ? selected.slice(3) : selected}`;
    const gate = collectGate(
      permit.kind === 'known' ? permit.value : null,
      contextReadOf(state.runControls[path] ?? null),
      state.context,
      selected,
      selected === 'combo',
      gateOf(assessedRead(status), null),
    );
    const vm = selectStartNext(state);
    expect(vm.collect.enabled).toBe(gate.enabled);
    expect(vm.collect.reason).toBe(gate.reason === 'ready' ? null : gate.reason);
    expect(vm.collect.enabled).toBe(name === 'ad_gems_on_floating_off');
    expectStateVM(vm, state);
  },
);

it('Start waits for click-time readiness, keeps a chosen run, and never consumes a collection permit as a setup permit', () => {
  let state = addCase(launchState(), 'runs', 'runs.not_ready_image_row');
  expect(selectStartNext(state)).toMatchObject({
    picker: { selected: 'bp_other' },
    start: { enabled: true },
  });
  state = reduceDashboard(state, { type: 'select_run', run: 'bp_farm' });
  expect(selectStartNext(state).start.enabled).toBe(true);
  expect(
    selectStartNext(state).picker.runs.find((r) => serverTextContent(r.name) === 'bp_farm'),
  ).toMatchObject({ readiness: 'images_missing', imageCount: 1 });
  state = addCase(state, 'status', 'status.guards_setup_blocking_collection_released_by_handoff');
  expect(selectStartNext(state).start).toMatchObject({ enabled: false, reason: 'guard_blocks' });
});

it('Collect uses the per-run cache and newest D-4 read; preview failure and context changes close the gate', () => {
  let state = addCase(launchState(), 'runControls', 'run_controls.ad_gems_on_floating_off');
  expect(selectStartNext(state).collect.enabled).toBe(true);
  // A different cached path cannot replace the selected run's answer.
  state = addBody(state, 'runControls', {}, { path: '/api/run-controls?blueprint=other' });
  expect(selectStartNext(state).collect.enabled).toBe(true);
  expectStateVM(selectStartNext(state), state);
  const failure = addFailure(state, 'runControls', '/api/run-controls?blueprint=farm');
  expect(selectStartNext(failure).collect.reason).toBe('preview_unavailable');
  const failedStatus = addFailure(state, 'status');
  expect(selectStartNext(failedStatus).start.enabled).toBe(false);
  expect(selectStartNext(failedStatus).collect.enabled).toBe(false);
  const stale = addBody(state, 'status', {
    ...statusBody(sample('status.off'), { scan: { state: 'stale' } }),
    setup_battle_guard: { scope: 'run', blocks_launch: false },
    collection_guard: { scope: 'collection', blocks_launch: false },
  });
  expect(selectStartNext(stale).collect.reason).toBe('ownership_unconfirmed');
  const changed = reduceDashboard(state, {
    type: 'context',
    context: { instance: 'other', serial: 'fake:2', account: null, profile: 'mine' },
    epoch: 1,
    confirmed: true,
    sessionContext: null,
  });
  expect(selectStartNext(changed).collect.enabled).toBe(false);
  expect(selectStartNext(regionState()).start.enabled).toBe(false);
});

it('only current-context Start and Collect in-flight records set sending', () => {
  const state = addCase(launchState(), 'runControls', 'run_controls.ad_gems_on_floating_off');
  const start = intent(state, { kind: 'start', phase: 'sending' });
  const collect = intent(state, { kind: 'start_gems', phase: 'sending' });
  const vm = selectStartNext(
    reduceDashboard(state, { type: 'intents', records: [start, collect] }),
  );
  expect(vm.start).toMatchObject({ enabled: false, sending: true, reason: 'sending' });
  expect(vm.collect).toMatchObject({ enabled: false, sending: true, reason: 'sending' });
  const context = { instance: 'other', serial: 'fake:2', account: null, profile: 'mine' };
  const foreign = selectStartNext(
    reduceDashboard(state, {
      type: 'intents',
      records: [
        { ...start, context },
        { ...collect, context },
      ],
    }),
  );
  expect(foreign.start.sending).toBe(false);
  expect(foreign.collect.sending).toBe(false);
});
