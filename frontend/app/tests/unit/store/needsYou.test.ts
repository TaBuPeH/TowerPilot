import { expect, it } from 'vitest';
import {
  finishSetupNeed,
  guardImpact,
  handoffTarget,
  setupOutcome,
  statusNeed,
} from '../../../src/core/needs.ts';
import { own } from '../../../src/core/read.ts';
import { otherRunsMissingImages, selectRun } from '../../../src/core/readiness.ts';
import { serverTextContent } from '../../../src/copy/server.ts';
import { reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { answeredBody, contextReadOf } from '../../../src/store/reads.ts';
import { selectNeedsYou } from '../../../src/store/selectors/needsYou.ts';
import type { ResourceId } from '../../../src/store/types.ts';
import { chipVM } from '../../../src/store/viewModels.ts';
import { dashboardCase, dashboardCaseNames } from '../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../support/fixture.ts';
import { addBody, addCase, addFailure, expectStateVM, regionState, tick } from './regionHelpers.ts';

const families: readonly (readonly [string, ResourceId])[] = [
  ['status', 'status'],
  ['calibrate_status', 'calibrate'],
  ['runs', 'runs'],
  ['wizard_tools', 'wizardTools'],
  ['wizard_state', 'wizardState'],
  ['config', 'config'],
  ['accounts', 'accounts'],
];

for (const [family, resource] of families) {
  it.each(dashboardCaseNames(family))(
    `Needs you projects ${family}.%s with source ages`,
    (name) => {
      const state = addCase(regionState(), resource, `${family}.${name}`);
      const vm = selectNeedsYou(state);
      const status = state.reads.status.latest;
      const body = status?.outcome.kind === 'answered' ? status.outcome.body : undefined;
      const impact = guardImpact(
        own(body, 'setup_battle_guard'),
        own(body, 'collection_guard'),
        status === null ? NaN : 10,
      );
      const guard = vm.items.find((i) => i.code === 'launch_guard');
      expect(guard !== undefined).toBe(impact.start === 'off' || impact.collection === 'on_hold');
      if (guard !== undefined) {
        expect(guard.impact).toMatchObject({
          start: impact.start,
          collection: impact.collection,
          commonBlock: impact.commonBlock,
        });
        const target = handoffTarget(body, 10);
        expect(guard.handoffTarget).toEqual(
          target === null ? null : { ...target, epoch: state.epoch },
        );
      }
      const setup = setupOutcome(
        contextReadOf(state.reads.calibrate.latest),
        body,
        status === null ? NaN : 10,
      );
      expect(vm.items.find((i) => i.code === 'setup_outcome')?.chip ?? null).toEqual(
        setup === null ? null : chipVM(setup.decision),
      );
      const need = statusNeed(contextReadOf(status));
      expect(vm.items.find((i) => i.code === 'status_need')?.chip ?? null).toEqual(
        need === null ? null : chipVM(need),
      );
      expect(vm.items.find((i) => i.code === 'finish_setup')?.setupStep ?? null).toBe(
        finishSetupNeed(
          answeredBody(state, 'wizardTools'),
          answeredBody(state, 'accounts'),
          answeredBody(state, 'config'),
          answeredBody(state, 'runs'),
        ),
      );
      const runs = answeredBody(state, 'runs');
      expect(vm.items.find((i) => i.code === 'other_runs_images')?.imageCount ?? 0).toBe(
        otherRunsMissingImages(runs, selectRun(runs, state.selectedRun)),
      );
      expect(vm.count).toBe(vm.items.length);
      expect(vm.visible).toEqual(vm.items.slice(0, 3));
      for (const item of vm.items) {
        expect(item.sources.some((read) => read.source === item.source)).toBe(true);
        const basis =
          item.code === 'finish_setup' || item.code === 'other_runs_images'
            ? 'lastAnswered'
            : 'latest';
        expect(item.ageSec).toBe(state.reads[item.source][basis] === null ? null : 0);
      }
      expectStateVM(vm, state);
    },
  );
}

it('hands off only the common unresolved guards; collection release does not release Start', () => {
  // As /api/status sends it: the guards with the harvest record (the dashboard fixture omits it).
  const state = addBody(regionState(), 'status', {
    ...(dashboardCase('status.guards_setup_and_collection_blocking_the_same_operation')
      .body as Record<string, unknown>),
    harvest: sample('status.off'),
  });
  const item = selectNeedsYou(state).items.find((i) => i.code === 'launch_guard');
  expect(item?.handoffTarget).not.toBeNull();
  expect(item?.handoffTarget).not.toHaveProperty('attestation');
  const released = selectNeedsYou(
    addCase(state, 'status', 'status.guards_setup_blocking_collection_released_by_handoff'),
  );
  const guard = released.items.find((i) => i.code === 'launch_guard');
  expect(guard?.impact).toMatchObject({
    start: 'off',
    collection: 'confirmed_manual_battle',
    commonBlock: false,
  });
  expect(guard?.handoffTarget).toBeNull();
});

it('retained missing-image facts cite their supporting answer instead of a later failed refresh', () => {
  let state = addCase(regionState(), 'runs', 'runs.combo_entry');
  state = reduceDashboard(state, { type: 'select_run', run: 'combo' });
  state = addCase(state, 'wizardTools', 'wizard_tools.installed');
  state = addCase(state, 'accounts', 'accounts.active_among_several');
  state = addCase(state, 'config', 'config.taps_true');
  state = addFailure(tick(state, 23), 'runs');
  const vm = selectNeedsYou(state);
  expect(vm.items.find((item) => item.code === 'other_runs_images')).toMatchObject({
    ageSec: 23,
    sources: [{ source: 'runs', ageSec: 23, outcome: 'answered' }],
  });
  expect(vm.items.find((item) => item.code === 'finish_setup')).toMatchObject({
    source: 'runs',
    ageSec: 23,
  });
  expect(vm.checked.find((read) => read.source === 'runs')).toEqual({
    source: 'runs',
    ageSec: 0,
    outcome: 'failed',
  });
  expectStateVM(vm, state);
});

it('failures cite the failed read and never reuse old guards as a new handoff target', () => {
  let state = addCase(
    regionState(),
    'status',
    'status.guards_setup_and_collection_blocking_the_same_operation',
  );
  state = addCase(state, 'calibrate', 'calibrate_status.failed_setup_unverified_final_screen');
  state = addFailure(tick(state, 17), 'status');
  state = addFailure(state, 'calibrate');
  const vm = selectNeedsYou(state);
  expect(vm.items.some((i) => i.handoffTarget !== null)).toBe(false);
  expect(vm.items.find((i) => i.code === 'status_need')?.chip?.line).toBe('no_live_status');
  expect(vm.items.find((i) => i.code === 'setup_outcome')?.chip?.line).toBe(
    'setup_status_unavailable',
  );
  expect(vm.checked.find((r) => r.source === 'status')).toEqual({
    source: 'status',
    ageSec: 0,
    outcome: 'failed',
  });
  expectStateVM(vm, state);
});

it('the honest empty state lists exactly the reads checked and their ages', () => {
  let state = addBody(regionState(), 'status', statusBody(sample('status.off')));
  for (const [resource, name] of [
    ['calibrate', 'calibrate_status.idle'],
    ['wizardTools', 'wizard_tools.installed'],
    ['accounts', 'accounts.active_among_several'],
    ['config', 'config.taps_true'],
    ['runs', 'runs.ready'],
    ['wizardState', 'wizard_state.all_sections'],
  ] satisfies readonly (readonly [ResourceId, string])[])
    state = addCase(state, resource, name);
  const vm = selectNeedsYou(tick(state, 9));
  expect(vm).toMatchObject({
    count: 0,
    items: [],
    visible: [],
    showAll: false,
    line: 'no_unresolved_issues_reported',
  });
  expect(vm.checked.map((r) => r.source)).toEqual([
    'status',
    'calibrate',
    'runs',
    'wizardTools',
    'wizardState',
    'config',
    'accounts',
  ]);
  expect(vm.checked.every((r) => r.ageSec === 9 && r.outcome === 'answered')).toBe(true);
  expectStateVM(vm, state);
});

it('a stale scan produces an Unknown item and unknown reads do not produce health claims', () => {
  const state = addCase(regionState(), 'status', 'status.process_refresh_stale');
  expect(selectNeedsYou(state).items.find((i) => i.code === 'status_need')?.chip).toMatchObject({
    word: 'unknown',
    line: 'process_discovery_unconfirmed',
  });
  const cold = selectNeedsYou(regionState());
  expect(cold.checked.every((read) => read.outcome === 'not_read' && read.ageSec === null)).toBe(
    true,
  );
  expect(
    cold.items.every((item) => item.chip?.word !== 'idle' && item.chip?.word !== 'running'),
  ).toBe(true);
});

it('retains structured setup errors as ServerText without exporting the raw error object', () => {
  const state = addBody(regionState(), 'calibrate', {
    operation: {
      id: 'setup-1',
      status: 'failed',
      error: { code: 'worker_lost', message: 'Worker exited without a confirmed outcome' },
    },
  });
  const vm = selectNeedsYou(state);
  const error = vm.items.find((item) => item.code === 'setup_outcome')?.error;
  if (error === undefined || error === null) throw new Error('Missing structured setup error');
  expect(serverTextContent(error)).toBe('Worker exited without a confirmed outcome');
  expectStateVM(vm, state);
});
